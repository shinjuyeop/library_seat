"""Vercel adapter: persist state in Supabase and serialize work with a database lease."""
import hashlib
import json
import os
import time
import uuid

import requests
from cryptography.fernet import Fernet, InvalidToken

from seat_service import LibraryClient, LibraryError, SeatService


class SupabaseStore:
    def __init__(self, url, key):
        self.url = url.rstrip('/') + '/rest/v1'
        self.headers = {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}

    def _request(self, method, path, **kwargs):
        try:
            response = requests.request(method, self.url + path, headers=self.headers, timeout=10, **kwargs)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError):
            raise LibraryError('클라우드 저장소에 연결하지 못했습니다. Supabase 설정을 확인해 주세요.') from None

    def read(self):
        rows = self._request('GET', '/library_runtime?id=eq.1&select=document')
        if not rows:
            raise LibraryError('Supabase 초기 설정이 필요합니다. 데이터베이스 마이그레이션을 실행해 주세요.')
        return rows[0]['document']

    def claim(self, owner):
        return self._request('POST', '/rpc/library_claim', json={'p_owner': owner})

    def save(self, owner, document):
        if not self._request('POST', '/rpc/library_save', json={'p_owner': owner, 'p_document': document}):
            raise LibraryError('다른 작업이 진행 중이거나 처리 시간이 초과되었습니다. 다시 확인해 주세요.')

    def release(self, owner):
        self._request('POST', '/rpc/library_unlock', json={'p_owner': owner})


class CloudService:
    cloud = True
    demo = False

    def __init__(self, store, encryption_key):
        self.store = store
        self.cipher = Fernet(encryption_key)

    def throttle(self, address, label, limit, seconds):
        bucket = hashlib.sha256((str(address) + ':' + label).encode()).hexdigest()
        return not self.store._request('POST', '/rpc/library_rate_limit',
                                       json={'p_bucket': bucket, 'p_limit': limit, 'p_seconds': seconds})

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
        state['cloud'] = True
        state['connected'] = bool(document.get('credential'))
        state['schedulerLastSeen'] = document.get('schedulerLastSeen')
        heartbeat = document.get('schedulerLastSeen')
        if state['running'] and (not heartbeat or time.time() - heartbeat > 120):
            state['error'] = '예약 스케줄러의 실행을 확인하지 못했습니다. Supabase Cron 설정을 확인해 주세요.'
            state['reservationFresh'] = False
        return state

    def _execute(self, action, *, credentials=None, cron=False, discard_credentials=False):
        owner = str(uuid.uuid4())
        if not self.store.claim(owner):
            raise LibraryError('서버가 좌석을 확인 중입니다. 잠시 후 다시 시도해 주세요.')
        worker = None
        try:
            document = self.store.read()
            state = self._empty_state()
            state.update(document.get('state', {}))
            client = None
            encrypted = document.get('credential')
            if encrypted and not credentials and not discard_credentials:
                try:
                    credential = json.loads(self.cipher.decrypt(encrypted.encode()))
                    client = LibraryClient(credential['token'], credential['cookies'])
                except (InvalidToken, ValueError, KeyError):
                    raise LibraryError('저장된 로그인을 복원하지 못했습니다. 암호화 키를 확인하거나 다시 연결해 주세요.') from None

            store = self.store
            class RuntimeStore:
                def load(self):
                    return {'targets': state['targets'], 'running': state['running']}

                def save(self, targets, running):
                    document['state'] = worker.snapshot()
                    # Before external writes, this durable save disarms retries after a crash.
                    store.save(owner, document)

            worker = SeatService(RuntimeStore(), client=client)
            worker.state.update(state)
            worker._update(connected=client is not None, connecting=False)
            if cron:
                document['schedulerLastSeen'] = time.time()
            try:
                if credentials:
                    new_client = LibraryClient(credentials['token'], credentials['cookies'])
                    try:
                        reservation = new_client.reservation()
                    except Exception:
                        new_client.close()
                        raise
                    worker._disconnect_client()
                    worker.client = new_client
                    document['credential'] = self.cipher.encrypt(json.dumps(credentials).encode()).decode()
                    worker._update(connected=True, error=None, reservation=reservation, reservationFresh=True)
                    worker._event('도서관 계정을 클라우드에 연결했습니다.')
                else:
                    action(worker)
            finally:
                if not worker.client:
                    document.pop('credential', None)
                document['state'] = worker.snapshot()
                store.save(owner, document)
        finally:
            if worker and worker.client:
                worker.client.close()
            self.store.release(owner)

    def connect(self, username, password):
        raise LibraryError('클라우드는 PC의 connect_cloud.py로 도서관 로그인을 연결해 주세요.')

    def connect_token(self, token, cookies):
        self._execute(None, credentials={'token': token, 'cookies': cookies})

    def disconnect(self):
        self._execute(lambda worker: worker.disconnect(), discard_credentials=True)

    def set_wait(self, targets, running):
        self._execute(lambda worker: worker.set_wait(targets, running))

    def reserve(self, key):
        self._execute(lambda worker: worker.reserve(key))

    def release(self, expected_id, expected_state):
        self._execute(lambda worker: worker.release(expected_id, expected_state))

    def refresh(self):
        self._execute(lambda worker: worker.tick())

    def tick(self):
        def poll(worker):
            state = worker.snapshot()
            # Idle accounts refresh at most once every five minutes; active waiting polls every tick.
            if state['running'] or (state['reservation'] and state['reservation']['state'] == 'TEMP_CHARGE') or time.time() - (state['lastChecked'] or 0) >= 300:
                worker.tick()
        self._execute(poll, cron=True)


def service_from_env():
    return CloudService(SupabaseStore(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY']),
                        os.environ['LIBRARY_ENCRYPTION_KEY'])
