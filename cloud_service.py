"""Account-scoped Supabase state and independent leases for Vercel invocations."""
import hashlib
import hmac
import json
import os
import re
import threading
import time
import uuid

import requests
from cryptography.fernet import Fernet, InvalidToken

from library_login import LoginError, login_to_library
from seat_service import LibraryClient, LibraryError, SeatService, configured_nfc_tags, sanitize_seat_catalog, schedule_window, scheduled_check_at, require_library_access
from library_access import guarded_access, bind_retry_state, record_access_failure, work_due
import push_notifications as push


class SupabaseStore:
    def __init__(self, url, key, account=None, *, transport=None):
        self.base, self.key, self.account = url.rstrip('/'), key, account
        self.url = self.base + '/rest/v1'
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        # Account wrappers share connections, but concurrent Flask threads do not
        # share mutable requests.Session state. Never retry a write implicitly.
        self._transport = transport if transport is not None else threading.local()

    def for_account(self, account):
        if not isinstance(account, str) or not re.fullmatch(r'[a-f0-9]{64}', account):
            raise LibraryError('로그인이 필요합니다.')
        return SupabaseStore(self.base, self.key, account, transport=self._transport)

    def _request(self, method, path, **kwargs):
        try:
            if not hasattr(self._transport, 'session'):
                self._transport.session = requests.Session()
            with self._transport.session.request(method, self.url + path, headers=self.headers, timeout=10, **kwargs) as response:
                response.raise_for_status()
                return response.json()
        except (requests.RequestException, ValueError):
            raise LibraryError('저장소에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.') from None

    def _account(self):
        if not self.account:
            raise LibraryError('로그인이 필요합니다.')
        return self.account

    def ensure(self):
        self._request('POST', '/rpc/library_account_ensure', json={'p_account': self._account()})

    def read(self):
        document = self._request('POST', '/rpc/library_account_read', json={'p_account': self._account()})
        if document is None:
            raise LibraryError('저장된 계정이 없습니다. 다시 로그인해 주세요.')
        return document

    def acquire(self, owner, *, include_catalog=True):
        return self._request('POST', '/rpc/library_account_acquire', json={
            'p_account': self._account(), 'p_owner': owner, 'p_catalog': include_catalog})

    def claim(self, owner):
        return self._request('POST', '/rpc/library_account_claim', json={'p_account': self._account(), 'p_owner': owner})

    def save(self, owner, document, *, catalog=None, release=False):
        if not self._request('POST', '/rpc/library_account_commit', json={
                'p_account': self._account(), 'p_owner': owner, 'p_document': document,
                'p_catalog': catalog, 'p_release': release}):
            raise LibraryError('다른 작업이 진행 중이거나 처리 시간이 초과되었습니다. 다시 확인해 주세요.')

    def release(self, owner):
        self._request('POST', '/rpc/library_account_unlock', json={'p_account': self._account(), 'p_owner': owner})

    def push_devices(self):
        return self._request('GET', '/library_push_devices', params={'account_key': 'eq.' + self._account(),
                             'select': 'id,subscription,preferences', 'limit': 3})

    def put_push(self, subscription, preferences):
        return self._request('POST', '/rpc/library_push_put', json={'p_account': self._account(),
            'p_id': subscription['id'], 'p_subscription': subscription['encrypted'], 'p_preferences': preferences})

    def delete_push(self, identifier):
        self._request('POST', '/rpc/library_push_delete', json={'p_account': self._account(), 'p_id': identifier})

    def access_status(self):
        status = self._request('POST', '/rpc/library_access_status', json={})
        if not isinstance(status, dict):
            raise LibraryError('도서관 연결 중지 상태를 확인하지 못했습니다.')
        return status

    def stop_access(self, reason, retry_at=None):
        self._request('POST', '/rpc/library_access_stop', json={'p_reason': reason, 'p_retry_at': retry_at})


class CloudService:
    cloud = True
    demo = False

    def __init__(self, store, encryption_key):
        self.store = store
        self.encryption_key = encryption_key.encode() if isinstance(encryption_key, str) else encryption_key
        self.cipher = Fernet(self.encryption_key)

    def for_account(self, account):
        return CloudService(self.store.for_account(account), self.encryption_key)

    def _encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def _decrypt(self, value):
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError, KeyError, AttributeError):
            raise LibraryError('저장된 로그인을 복원하지 못했습니다. 다시 로그인해 주세요.') from None

    def _account_key(self, identity):
        return hmac.new(self.encryption_key, ('patron:' + identity).encode(), hashlib.sha256).hexdigest()

    def throttle(self, address, label, limit, seconds):
        bucket = hmac.new(self.encryption_key, (str(address) + ':' + label).encode(), hashlib.sha256).hexdigest()
        return not self.store._request('POST', '/rpc/library_rate_limit', json={'p_bucket': bucket, 'p_limit': limit, 'p_seconds': seconds})

    @staticmethod
    def _empty_state():
        class EmptyStore:
            def load(self):
                return {'targets': [], 'running': False}
        return SeatService(EmptyStore()).snapshot()

    def snapshot(self):
        document = self.store.read()
        state = self._empty_state()
        state.update(document.get('state', {}))
        sanitize_seat_catalog(state)
        state['confirmationRooms'] = sorted(configured_nfc_tags())
        state['scheduleWindow'] = schedule_window()
        state.update(cloud=True, connected=bool(document.get('credential')), autoLogin=bool(document.get('login')), schedulerLastSeen=document.get('schedulerLastSeen'))
        access = self.store.access_status()
        state['accessControl'] = access
        heartbeat = document.get('schedulerLastSeen')
        expected = work_due(state)
        if expected is not None and time.time() - expected > 120 and (not heartbeat or time.time() - heartbeat > 120):
            state['error'] = '자동 실행 연결을 확인할 수 없습니다. 잠시 후 다시 확인해 주세요.'
            state['reservationFresh'] = False
        if access.get('blocked'):
            state.update(error='도서관 연결이 중지되어 있습니다. 접속 제한을 확인한 뒤 운영자가 재개해야 합니다.',
                         connected=False, reservationFresh=False, running=False)
        return state

    @staticmethod
    def _schedule_document(document, worker):
        state = worker.snapshot()
        now = time.time()
        due = work_due(state, now)
        if due is not None:
            due = max(now + 1, due)
        if not worker.client and (not document.get('login') or document.get('autoLoginStopped')):
            due = None
        job = state.get('scheduledBooking')
        if any(item['delivery'] == 'pending' for item in state.get('notifications', [])):
            due = min(due, now + 1) if due is not None else now + 1
        document['nextPollAt'] = due
        document['pendingWork'] = bool((job and job['status'] in {'pending', 'working'}) or
                                     any(item['delivery'] == 'pending' for item in state.get('notifications', [])))

    @guarded_access
    def login(self, username, password, *, remember=False):
        require_library_access()
        credentials = login_to_library(username, password)
        # No account row or saved password is created before the provider validates login.
        client = LibraryClient(credentials['token'], credentials['cookies'])
        try:
            reservation = client.reservation()
        finally:
            client.close()
        account = self._account_key(credentials['identity'])
        scoped = self.for_account(account)
        scoped.store.ensure()
        scoped._execute(None, credentials=credentials, reservation=reservation, saved_login={'username': username, 'password': password} if remember else None)
        return account

    @guarded_access
    def _execute(self, action, *, credentials=None, reservation=None, saved_login=None, cron=False, discard_credentials=False):
        owner = str(uuid.uuid4())
        document = self.store.acquire(owner, include_catalog=not cron)
        if document is None:
            raise LibraryError('좌석을 확인 중입니다. 잠시 후 다시 시도해 주세요.')
        worker, released = None, False
        try:
            state = self._empty_state()
            state.update(document.get('state', {}))
            sanitize_seat_catalog(state)
            client = None
            if document.get('credential') and not credentials and not discard_credentials:
                credential = self._decrypt(document['credential'])
                client = LibraryClient(credential['token'], credential['cookies'])
            store = self.store

            def persist(*, final=False):
                nonlocal saved_catalog, released
                snapshot = worker.snapshot()
                catalog = snapshot.pop('seats')
                document['state'] = snapshot
                CloudService._schedule_document(document, worker)
                # Omitted catalogs stay untouched. Intermediate saves still hold
                # the lease and durably disarm retries before any external write.
                changed_catalog = catalog if catalog != saved_catalog else None
                store.save(owner, document, catalog=changed_catalog, release=final)
                saved_catalog = catalog
                released = final

            class RuntimeStore:
                def load(self):
                    return {'targets': state['targets'], 'running': state['running']}
                def save(self, targets, running, repeat=None, repeat_control=None, auto_renew=None, renew_night_until=None, extras=None):
                    persist()
            worker = SeatService(RuntimeStore(), client=client)
            worker.state.update(state)
            bind_retry_state(worker.state)
            worker._update(connected=client is not None, connecting=False)
            saved_catalog = worker.snapshot()['seats']
            if cron:
                document['schedulerLastSeen'] = time.time()
            try:
                if credentials:
                    worker.client = LibraryClient(credentials['token'], credentials['cookies'])
                    document['credential'] = self._encrypt({'token': credentials['token'], 'cookies': credentials['cookies']})
                    if saved_login:
                        document['login'] = self._encrypt(saved_login)
                    else:
                        document.pop('login', None)
                    document.pop('loginRetryAt', None)
                    document.pop('autoLoginStopped', None)
                    worker._update(connected=True, running=False, error=None, connectionRetry=None,
                                   reservation=worker._with_booking_time(reservation), reservationFresh=True)
                    worker._event('로그인했습니다.')
                    worker.tick(allow_actions=False)  # Login never reserves, renews, or switches seats.
                    worker._update(running=bool(state['running'] and worker.client))
                    worker._update(interval=worker.poll_interval())
                    if worker.snapshot()['running']:
                        worker._update(nextCheck=time.time())
                elif discard_credentials:
                    document.pop('login', None)
                    document.pop('loginRetryAt', None)
                    action(worker)
                elif cron and self._deliver_push(worker):
                    pass  # Delivery has its own invocation budget; never delays a seat transaction.
                elif cron and not worker.client and document.get('login'):
                    due = work_due(worker.snapshot())
                    if due is not None and due <= time.time() and not document.get('autoLoginStopped'):
                        self._auto_login(worker, document)
                    if not worker.client:
                        worker._check_scheduled()
                    # Never retry a reservation in the same invocation as reauthentication.
                else:
                    action(worker)
            finally:
                if not worker.client:
                    document.pop('credential', None)
                persist(final=True)
        finally:
            try:
                if worker and worker.client:
                    worker.client.close()
            finally:
                # A failed/ambiguous save is never retried. Release only our own
                # lease; an already-committed final save makes this a no-op.
                if not released:
                    self.store.release(owner)

    def _auto_login(self, worker, document):
        new_client = None
        # One attempt per expired connection; a crash also leaves retries stopped.
        document['autoLoginStopped'] = True
        document.pop('loginRetryAt', None)
        worker._save()
        try:
            saved = self._decrypt(document['login'])
            credentials = login_to_library(saved['username'], saved['password'])
            if not hmac.compare_digest(self._account_key(credentials['identity']), self.store.account):
                raise LoginError('계정 정보가 변경되었습니다. 다시 로그인해 주세요.', kind='action_required')
            new_client = LibraryClient(credentials['token'], credentials['cookies'])
            reservation = new_client.reservation()
            worker.client, new_client = new_client, None
            document['credential'] = self._encrypt({'token': credentials['token'], 'cookies': credentials['cookies']})
            document.pop('loginRetryAt', None)
            document.pop('autoLoginStopped', None)
            worker._update(connected=True, error=None, connectionRetry=None,
                           reservation=worker._with_booking_time(reservation), reservationFresh=True)
            worker._event('자동로그인했습니다.')
        except LibraryError as error:
            record_access_failure(error)
            worker._update(error=str(error), connected=False, reservationFresh=False)
            worker._update(running=False, connectionRetry={'status': 'paused', 'attempts': 1,
                           'retryAt': error.retry_at, 'reason': error.kind or 'login_failed'})
            if isinstance(error, LoginError) and error.kind in {'credentials', 'action_required'}:
                document.pop('login', None)
            worker._event('자동로그인에 실패해 자동 접속을 중지했습니다. 연결 상태를 직접 확인해 주세요.')
        finally:
            if new_client:
                new_client.close()

    def disconnect(self):
        self._execute(lambda worker: worker.disconnect(), discard_credentials=True)

    def set_wait(self, targets, running):
        self._execute(lambda worker: worker.set_wait(targets, running))

    def update_wait(self, key, enabled):
        self._execute(lambda worker: worker.update_wait(key, enabled))

    def update_favorite(self, key, enabled):
        self._execute(lambda worker: worker.update_favorite(key, enabled))

    def reserve(self, key):
        self._execute(lambda worker: worker.reserve(key))

    def confirm(self, expected_id):
        self._execute(lambda worker: worker.confirm(expected_id))

    def reassign(self, expected_id):
        self._execute(lambda worker: worker.reassign(expected_id))

    def release(self, expected_id, expected_state):
        self._execute(lambda worker: worker.release(expected_id, expected_state))

    def set_repeat(self, enabled, expected_id):
        self._execute(lambda worker: worker.set_repeat(enabled, expected_id))

    def renew(self, expected_id):
        self._execute(lambda worker: worker.renew(expected_id))

    def set_auto_renew(self, enabled, expected_id):
        self._execute(lambda worker: worker.set_auto_renew(enabled, expected_id))

    def set_schedule(self, key, due_at):
        self._execute(lambda worker: worker.set_schedule(key, due_at))

    def cancel_schedule(self, expected_id):
        self._execute(lambda worker: worker.cancel_schedule(expected_id))

    def push_status(self, endpoint):
        identifier = push.device_id(endpoint)
        device = next((item for item in self.store.push_devices() if item['id'] == identifier), None)
        return {'enabled': bool(device), 'preferences': device['preferences'] if device else push.DEFAULT_PREFERENCES}

    def subscribe_push(self, subscription, preferences):
        if not push.configuration()['configured']:
            raise LibraryError('서버 알림 설정을 준비 중입니다.')
        subscription, preferences = push.subscription_info(subscription), push.preferences(preferences)
        if not self.store.put_push({'id': push.device_id(subscription['endpoint']), 'encrypted': self._encrypt(subscription)}, preferences):
            raise LibraryError('알림은 계정당 기기 3개까지 켤 수 있습니다. 다른 기기에서 먼저 꺼 주세요.')

    def unsubscribe_push(self, endpoint):
        self.store.delete_push(push.device_id(endpoint))

    def test_push(self, endpoint):
        identifier = push.device_id(endpoint)
        device = next((item for item in self.store.push_devices() if item['id'] == identifier), None)
        if not device:
            raise LibraryError('이 기기의 알림을 먼저 켜 주세요.')
        result = push.send_notification(self._decrypt(device['subscription']), {'id': uuid.uuid4().hex,
            'title': '테스트 알림', 'body': '도서관 좌석 알림이 연결되었습니다.', 'url': '/?tab=settings'})
        if result == 'expired':
            self.store.delete_push(identifier)
        if result != 'sent':
            raise LibraryError('알림 발송에 실패했습니다. 알림을 껐다 켠 뒤 다시 시도해 주세요.')

    def _deliver_push(self, worker):
        state = worker.snapshot()
        due = work_due(state)
        if due is not None and due <= time.time() + 20:
            return False
        pending = [item for item in state.get('notifications', []) if item['delivery'] == 'pending']
        if not pending:
            return False
        item = pending[-1]
        # Persist before external delivery: a worker crash must not cause duplicate pushes.
        def mark(status):
            worker._update(notifications=[{**n, 'delivery': status} if n['id'] == item['id'] else n
                                          for n in worker.snapshot()['notifications']])
            worker._save()
        mark('sending')
        sent, failed = False, False
        try:
            for device in self.store.push_devices():
                if not device['preferences'].get(item['kind']) or time.time() - item['time'] > 3600:
                    continue
                result = push.send_notification(self._decrypt(device['subscription']), item)
                sent |= result == 'sent'
                failed |= result == 'failed'
                if result == 'expired':
                    self.store.delete_push(device['id'])
        except LibraryError:
            failed = True
        mark('failed' if failed else 'sent' if sent else 'skipped')
        return sent or failed

    def refresh(self, room=None):
        self._execute(lambda worker: worker.tick(allow_actions=False, rooms={room} if room is not None else None))

    def tick(self):
        def poll(worker):
            state = worker.snapshot()
            due = work_due(state)
            if due is not None and due <= time.time():
                worker.tick(targets_only=bool(state['running']),
                            reservation_only=bool(state['autoRenew'] and not state['running']))
        self._execute(poll, cron=True)


def service_from_env():
    return CloudService(SupabaseStore(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']), os.environ['LIBRARY_ENCRYPTION_KEY'])
