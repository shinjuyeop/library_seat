"""Synthetic scheduled jobs and encrypted push; never send real seat or push requests."""
import base64
import copy
import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException

from cloud_service import CloudService, SupabaseStore
import push_notifications as push
from seat_service import DemoClient, KST, LibraryClient, LibraryError, SeatService, schedule_window
from test_service import FakeCloudStore, ServiceFixture
from webapp import create_app

EVENING = datetime(2026, 10, 9, 20, tzinfo=KST).timestamp()
MORNING = datetime(2026, 10, 10, 5, tzinfo=KST).timestamp()


def subscription(suffix='test'):
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    encode = lambda value: base64.urlsafe_b64encode(value).decode().rstrip('=')
    return {'endpoint': 'https://fcm.googleapis.com/fcm/send/' + suffix,
            'keys': {'auth': encode(b'a' * 16), 'p256dh': encode(point)}}


class ScheduleTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.clock = patch('seat_service.time.time', return_value=EVENING).start()
        self.addCleanup(patch.stopall)
        self.service.nfc_tags = {102: '0123456789ABCDEF'}
        self.reserve = self.client.reserve = Mock(wraps=self.client.reserve)
        self.release = self.client.release = Mock(wraps=self.client.release)

    def register(self, key='102:3', due=MORNING):
        self.service.set_schedule(key, due)
        return self.service.snapshot()['scheduledBooking']

    def test_registration_window_midnight_and_boundaries(self):
        for hour, minute, opened, day in [(0, 0, True, 9), (4, 59, True, 9), (5, 0, False, 10),
                                          (11, 59, False, 10), (12, 0, True, 10), (23, 59, True, 10)]:
            window = schedule_window(datetime(2026, 10, 9, hour, minute, tzinfo=KST).timestamp())
            self.assertEqual(window['open'], opened)
            self.assertEqual(window['date'], f'2026-10-{day:02}')

    def test_validation_and_occupied_future_seat_no_booking(self):
        for key, due in [('999:3', MORNING), ('101:409', MORNING), ('232:3', MORNING),
                         ('102:3', True), ('102:3', float('inf')), ('102:3', MORNING - 600),
                         ('102:3', MORNING + 30), ('102:3', MORNING + 420 * 60), ('102:99999', MORNING)]:
            with self.subTest(key=key, due=due), self.assertRaises(LibraryError):
                self.register(key, due)
        self.register('102:1', MORNING + 410 * 60)  # Currently occupied is still selectable.
        self.reserve.assert_not_called()
        self.release.assert_not_called()
        self.clock.return_value = MORNING
        with self.assertRaises(LibraryError):
            self.register()

    def test_success_survives_restart_and_enables_renewal_once(self):
        self.register()
        self.service = SeatService(self.store, client=self.client, nfc_tags=self.service.nfc_tags)
        self.service.tick()
        self.reserve.assert_not_called()
        self.clock.return_value = MORNING
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['scheduledBooking']['status'], 'succeeded')
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertEqual(state['autoRenew']['status'], 'scheduled')
        self.assertIsNone(state['repeat'])
        self.assertEqual(len(state['notifications']), 1)
        self.service.tick()
        self.reserve.assert_called_once_with(102003)

    def start_opening_retry(self):
        self.register()
        self.clock.return_value = MORNING
        self.reserve.side_effect = LibraryError('not open yet', rejected=True)
        self.service.tick()
        return self.service.snapshot()['scheduledBooking']

    def test_opening_retries_only_after_three_seconds_across_restart_then_confirms_once(self):
        job = self.start_opening_retry()
        self.assertEqual((job['status'], job['attempts'], job['retryAt']), ('pending', 1, MORNING + 3))
        self.assertEqual(self.service.snapshot()['notifications'], [])
        self.assertFalse(self.service.quiet_notifications)
        self.clock.return_value = MORNING + 2
        self.service = SeatService(self.store, client=self.client, nfc_tags=self.service.nfc_tags)
        self.service.tick()
        self.reserve.assert_called_once()
        self.clock.return_value = MORNING + 3
        self.service.tick()
        self.assertEqual(self.reserve.call_count, 2)
        self.assertEqual(self.store.load()['scheduledBooking']['retryAt'], MORNING + 6)
        self.clock.return_value = MORNING + 5
        self.service.tick()
        self.assertEqual(self.reserve.call_count, 2)
        self.reserve.side_effect = None
        self.client.confirm_reservation = Mock(wraps=self.client.confirm_reservation)
        self.clock.return_value = MORNING + 6
        self.service.tick()
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['scheduledBooking']['status'], 'succeeded')
        self.assertEqual(state['scheduledBooking']['attempts'], 3)
        self.assertEqual(state['autoRenew']['status'], 'scheduled')
        self.assertEqual(len(state['notifications']), 1)
        self.assertEqual(self.reserve.call_count, 3)
        self.client.confirm_reservation.assert_called_once()
        self.release.assert_not_called()

    def test_opening_retries_stop_at_exactly_five_oh_one_with_one_final_notification(self):
        self.start_opening_retry()
        for elapsed in range(3, 60, 3):
            self.clock.return_value = MORNING + elapsed
            self.service.tick()
        self.assertEqual(self.reserve.call_count, 20)
        self.assertEqual(self.service.snapshot()['notifications'], [])
        self.clock.return_value = MORNING + 60
        self.service.tick()
        self.clock.return_value += 60
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['scheduledBooking']['status'], 'failed')
        self.assertIn('05:01', state['scheduledBooking']['result'])
        self.assertEqual(self.reserve.call_count, 20)
        self.assertEqual(len(state['notifications']), 1)

    def test_opening_job_never_starts_a_new_request_after_the_window(self):
        self.register()
        self.clock.return_value = MORNING + 60
        self.service.tick()
        self.reserve.assert_not_called()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_existing_saved_five_am_job_without_new_fields_gets_the_retry_behavior(self):
        job = self.register()
        job.pop('retryAt')
        job.pop('attempts')
        self.service._update(scheduledBooking=job)
        self.service._save()
        self.service = SeatService(self.store, client=self.client, nfc_tags=self.service.nfc_tags)
        self.reserve.side_effect = LibraryError('not open yet', rejected=True)
        self.clock.return_value = MORNING
        self.service.tick()
        saved = self.store.load()['scheduledBooking']
        self.assertEqual((saved['status'], saved['retryAt'], saved['attempts']), ('pending', MORNING + 3, 1))

    def test_last_rejection_waits_only_until_the_window_end(self):
        self.register()
        self.clock.return_value = MORNING + 59
        self.reserve.side_effect = LibraryError('not open yet', rejected=True)
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['retryAt'], MORNING + 60)
        self.clock.return_value += 1
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_slow_preflight_cannot_send_a_late_reservation_request(self):
        self.register()
        seats = self.client.seats
        def slow(room):
            self.clock.return_value = MORNING + 60
            return seats(room)
        self.client.seats = slow
        self.clock.return_value = MORNING
        self.service.tick()
        self.reserve.assert_not_called()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_success_before_deadline_can_finish_confirmation_after_deadline(self):
        self.register()
        reserve = self.reserve._mock_wraps
        def slow(seat):
            reserve(seat)
            self.clock.return_value = MORNING + 62
        self.reserve.side_effect = slow
        self.clock.return_value = MORNING + 59
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'succeeded')
        self.reserve.assert_called_once()

    def test_seat_taken_between_retries_stops_without_a_second_reservation(self):
        self.start_opening_retry()
        seats = self.client.seats
        self.client.seats = lambda room: [{**seat, 'isOccupied': True} if str(seat['code']) == '3' else seat
                                         for seat in seats(room)]
        self.clock.return_value = MORNING + 3
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_seat_already_taken_after_rejection_is_not_queued_for_retry(self):
        self.register()
        seats = self.client.seats
        def reject(_):
            self.client.seats = lambda room: [{**seat, 'isOccupied': True} if str(seat['code']) == '3' else seat
                                             for seat in seats(room)]
            raise LibraryError('occupied', rejected=True)
        self.reserve.side_effect = reject
        self.clock.return_value = MORNING
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_changed_seat_identifier_stops_a_pending_retry(self):
        self.start_opening_retry()
        seats = self.client.seats
        self.client.seats = lambda room: [{**seat, 'seatId': 999999, 'id': 999999} if str(seat['code']) == '3' else seat
                                         for seat in seats(room)]
        self.clock.return_value = MORNING + 3
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_another_booking_during_retry_is_preserved_without_return_or_rebooking(self):
        self.start_opening_retry()
        self.reserve._mock_wraps(102030)  # Simulate a booking made in the official app.
        current = copy.deepcopy(self.client.current)
        self.clock.return_value = MORNING + 3
        self.service.tick()
        self.assertEqual(self.client.current, current)
        self.reserve.assert_called_once()
        self.release.assert_not_called()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_expired_login_and_unclassified_error_never_retry(self):
        for error in (LibraryError('login expired', expired=True, rejected=True), LibraryError('unknown response')):
            with self.subTest(error=str(error)):
                self.service.client = self.client
                self.clock.return_value = EVENING
                self.register()
                self.reserve.reset_mock()
                self.reserve.side_effect = error
                self.clock.return_value = MORNING
                self.service.tick()
                self.clock.return_value += 3
                self.service.tick()
                self.reserve.assert_called_once()
                self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_timeout_with_verified_booking_confirms_without_repeating_reserve(self):
        self.register()
        reserve = self.reserve._mock_wraps
        def timeout(seat):
            reserve(seat)
            raise LibraryError('timeout', uncertain=True)
        self.reserve.side_effect = timeout
        self.client.confirm_reservation = Mock(wraps=self.client.confirm_reservation)
        self.clock.return_value = MORNING
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'succeeded')
        self.reserve.assert_called_once()
        self.client.confirm_reservation.assert_called_once()

    def test_reconciliation_read_failure_never_repeats_unknown_reservation(self):
        self.register()
        read = self.client.reservation
        self.client.reservation = Mock(side_effect=[None, LibraryError('read failed')])
        self.reserve.side_effect = LibraryError('timeout', uncertain=True, rejected=True)
        self.clock.return_value = MORNING
        self.service.tick()
        self.client.reservation = read
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_retry_can_be_cancelled_after_registration_window_closes(self):
        job = self.start_opening_retry()
        self.service.cancel_schedule(job['id'])
        self.clock.return_value += 3
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'cancelled')

    def test_other_morning_slots_keep_single_attempt_behavior(self):
        self.register(due=MORNING + 600)
        self.clock.return_value = MORNING + 600
        self.reserve.side_effect = LibraryError('denied', rejected=True)
        self.service.tick()
        self.clock.return_value += 3
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')

    def test_provider_rejection_classification_requires_a_clear_negative_response(self):
        client = LibraryClient('synthetic-token')
        self.addCleanup(client.close)
        for status, success, code, rejected in ((200, False, 'closed', True), (400, False, 'closed', True),
                (409, False, 'occupied', True), (422, False, 'denied', True),
                (200, 'false', 'closed', False), (200, None, 'closed', False),
                (200, False, 'session.expired', False), (429, False, 'rate.limited', False),
                (500, False, 'server.error', False), (403, False, 'forbidden', False)):
            with self.subTest(status=status, success=success, code=code):
                response = Mock(status_code=status)
                response.json.return_value = {'success': success, 'code': code, 'private': 'never-expose-me'}
                client.session.request = Mock(return_value=response)
                with self.assertRaises(LibraryError) as raised:
                    client.reserve(102003)
                self.assertEqual(raised.exception.rejected, rejected)
                self.assertNotIn('never-expose-me', str(raised.exception))

    def test_no_early_booking_login_read_only_and_cancel(self):
        job = self.register()
        self.clock.return_value = MORNING
        self.service.tick(allow_actions=False)
        self.reserve.assert_not_called()
        with self.assertRaises(LibraryError):
            self.service.cancel_schedule('stale')
        self.service.cancel_schedule(job['id'])
        self.service.tick()
        self.reserve.assert_not_called()

    def test_existing_seat_retained_without_release(self):
        self.register()
        self.client.reserve(102030)
        self.reserve.reset_mock()
        original = copy.deepcopy(self.client.current)
        self.clock.return_value = MORNING
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')
        self.assertEqual(self.client.current, original)
        self.release.assert_not_called()
        self.reserve.assert_not_called()

    def test_occupied_failure_stops_job(self):
        self.register('102:1')
        self.clock.return_value = MORNING
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')
        self.reserve.assert_not_called()

    def test_ambiguous_reserve_never_replays(self):
        self.register()
        self.clock.return_value = MORNING
        self.reserve.side_effect = LibraryError('unknown write', uncertain=True)
        self.service.tick()
        self.service = SeatService(self.store, client=self.client, nfc_tags=self.service.nfc_tags)
        self.service.tick()
        self.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['stage'], '좌석 예약')

    def test_confirmation_failure_keeps_temporary_seat_without_repeat(self):
        self.register()
        self.clock.return_value = MORNING
        self.client.confirm_reservation = Mock(side_effect=LibraryError('rejected'))
        self.service.tick()
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['scheduledBooking']['stage'], '배정확정')
        self.assertEqual(state['scheduledBooking']['status'], 'failed')
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertIsNone(state['repeat'])
        self.assertIsNone(state['autoRenew'])
        self.assertEqual(len(state['notifications']), 1)
        self.reserve.assert_called_once()
        self.release.assert_not_called()

    def test_crash_after_checkpoint_and_missed_deadline(self):
        job = self.register()
        self.service._update(scheduledBooking={**job, 'status': 'working', 'stage': '좌석 예약'})
        self.service._save()
        self.service = SeatService(self.store, client=self.client, nfc_tags=self.service.nfc_tags)
        self.clock.return_value = MORNING
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')
        self.reserve.assert_not_called()
        self.clock.return_value = EVENING
        self.register()
        self.clock.return_value = MORNING + 121
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'failed')
        self.reserve.assert_not_called()

    def test_disconnected_job_finishes_and_dispatch_remains_for_result(self):
        self.register()
        self.clock.return_value = MORNING
        self.service.client = None
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['scheduledBooking']['status'], 'failed')
        document = {}
        CloudService._schedule_document(document, self.service)
        self.assertTrue(document['pendingWork'])
        self.assertEqual(document['nextPollAt'], MORNING + 1)

    def test_api_auth_csrf_and_schedule_preserved_after_logout(self):
        app = create_app(self.service, 'long-demo-password', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        body = {'key': '102:3', 'dueAt': MORNING}
        self.assertEqual(browser.post('/api/schedule', json=body, headers={'X-CSRF-Token': csrf}).status_code, 401)
        csrf = browser.post('/api/login', json={'password': 'long-demo-password'}, headers={'X-CSRF-Token': csrf}).json['csrf']
        self.assertEqual(browser.post('/api/schedule', json=body).status_code, 403)
        headers = {'X-CSRF-Token': csrf}
        self.assertEqual(browser.post('/api/schedule', json=body, headers=headers).status_code, 200)
        browser.post('/api/logout', json={}, headers=headers)
        self.clock.return_value = MORNING
        self.service.tick()
        self.assertEqual(self.service.snapshot()['scheduledBooking']['status'], 'succeeded')


class PushTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeCloudStore()
        self.root = CloudService(self.store, Fernet.generate_key())
        self.alice, self.bob = [self.root.for_account(name * 64) for name in ('a', 'b')]
        for cloud in (self.alice, self.bob):
            cloud.store.ensure()
        self.sub = subscription()

    def test_ssrf_and_invalid_crypto_keys_rejected(self):
        for endpoint in ('https://127.0.0.1/secret', 'http://fcm.googleapis.com/send/a',
                         'https://fcm.googleapis.com.evil.test/a', 'https://a.push.apple.com:444/a',
                         'https://evil@fcm.googleapis.com/a', 'https://fcm.googleapis.com/a#b',
                         'https://fcm.googleapis.com\\@127.0.0.1/a'):
            with self.subTest(endpoint=endpoint), self.assertRaises(LibraryError):
                push.device_id(endpoint)
        self.assertTrue(push.device_id('https://web.push.apple.com/test'))
        self.assertTrue(push.device_id('https://updates.push.services.mozilla.com/wpush/v2/test'))
        for value in ({}, {'endpoint': self.sub['endpoint'], 'keys': {'auth': 'a' * 22, 'p256dh': 'b' * 87}}):
            with self.assertRaises(LibraryError):
                push.subscription_info(value)

    @patch('push_notifications.configuration', return_value={'configured': True, 'publicKey': 'synthetic'})
    def test_encrypted_subscription_and_single_account_ownership(self, _):
        self.alice.subscribe_push(self.sub, push.DEFAULT_PREFERENCES)
        self.assertNotIn(self.sub['endpoint'], json.dumps(self.store.devices))
        self.assertTrue(self.alice.push_status(self.sub['endpoint'])['enabled'])
        self.assertFalse(self.bob.push_status(self.sub['endpoint'])['enabled'])
        self.bob.unsubscribe_push(self.sub['endpoint'])
        self.assertTrue(self.alice.push_status(self.sub['endpoint'])['enabled'])
        self.bob.subscribe_push(self.sub, push.DEFAULT_PREFERENCES)
        self.assertFalse(self.alice.push_status(self.sub['endpoint'])['enabled'])
        self.assertTrue(self.bob.push_status(self.sub['endpoint'])['enabled'])

    def worker(self):
        class Store:
            def load(self): return {'targets': [], 'running': False}
            def save(self, *args): pass
        worker = SeatService(Store(), client=DemoClient())
        worker._notify('renewal', 'renewed', 'seat 3')
        return worker

    @patch('push_notifications.send_notification', return_value='sent')
    @patch('push_notifications.configuration', return_value={'configured': True})
    def test_delivery_preferences_isolation_expiry_and_no_replay(self, _, send):
        self.alice.subscribe_push(self.sub, push.DEFAULT_PREFERENCES)
        worker = self.worker()
        self.assertFalse(self.bob._deliver_push(worker))
        send.assert_not_called()
        worker = self.worker()
        self.assertTrue(self.alice._deliver_push(worker))
        self.assertFalse(self.alice._deliver_push(worker))
        send.assert_called_once()
        self.alice.subscribe_push(self.sub, {**push.DEFAULT_PREFERENCES, 'renewal': False})
        self.alice._deliver_push(self.worker())
        send.assert_called_once()
        self.alice.subscribe_push(self.sub, push.DEFAULT_PREFERENCES)
        send.return_value = 'expired'
        self.alice._deliver_push(self.worker())
        self.assertFalse(self.alice.push_status(self.sub['endpoint'])['enabled'])

    @patch('push_notifications.send_notification')
    def test_imminent_booking_prioritized_over_push(self, send):
        worker = self.worker()
        worker._update(scheduledBooking={'status': 'pending', 'dueAt': 0})
        self.assertFalse(self.alice._deliver_push(worker))
        send.assert_not_called()

    @patch.dict('os.environ', {'VAPID_PRIVATE_KEY': 'test-private', 'VAPID_PUBLIC_KEY': 'test-public'})
    @patch('push_notifications.webpush')
    def test_provider_delivery_payload_and_expired_status(self, webpush):
        webpush.return_value.status_code = 201
        item = self.worker().snapshot()['notifications'][0]
        self.assertEqual(push.send_notification(self.sub, item), 'sent')
        args = webpush.call_args.kwargs
        self.assertEqual(args['timeout'], 5)
        self.assertEqual(json.loads(args['data'])['title'], 'renewed')
        self.assertNotIn('test-private', args['data'])
        webpush.side_effect = WebPushException('secret response', response=Mock(status_code=410))
        self.assertEqual(push.send_notification(self.sub, item), 'expired')

    def test_redirects_disabled(self):
        with patch('requests.Session.request') as request:
            push.NoRedirectSession().post(self.sub['endpoint'])
        self.assertIs(request.call_args.kwargs['allow_redirects'], False)

    def test_store_queries_always_account_scoped(self):
        store = SupabaseStore('https://db.example', 'synthetic', 'a' * 64)
        store._request = Mock(return_value=[])
        store.push_devices()
        self.assertEqual(store._request.call_args.kwargs['params']['account_key'], 'eq.' + 'a' * 64)
        store.delete_push('b' * 64)
        self.assertEqual(store._request.call_args.kwargs['json']['p_account'], 'a' * 64)

    def test_real_vapid_signing_and_payload_encryption_without_network(self):
        key = ec.generate_private_key(ec.SECP256R1())
        encoded = base64.urlsafe_b64encode(key.private_bytes(serialization.Encoding.DER,
            serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())).decode().rstrip('=')
        item = self.worker().snapshot()['notifications'][0]
        with patch.dict('os.environ', {'VAPID_PRIVATE_KEY': encoded, 'VAPID_PUBLIC_KEY': 'test-public'}), \
                patch('requests.Session.request', return_value=Mock(status_code=201)) as request:
            self.assertEqual(push.send_notification(self.sub, item), 'sent')
        args = request.call_args.kwargs
        self.assertIn('vapid ', args['headers']['Authorization'])
        self.assertNotIn(b'renewed', args['data'])
        self.assertEqual(args['headers']['content-encoding'], 'aes128gcm')
        self.assertFalse(args['allow_redirects'])

    def test_failure_notification_dedup_and_no_public_endpoints(self):
        worker = self.worker()
        worker._failure(LibraryError('same error'))
        worker._failure(LibraryError('same error'))
        failures = [item for item in worker.snapshot()['notifications'] if item['kind'] == 'failure']
        self.assertEqual(len(failures), 1)

    def test_push_routes_require_session_csrf_and_validate_provider(self):
        app = create_app(self.root, 'long-demo-password', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        self.assertEqual(browser.get('/api/push/config').status_code, 401)
        self.assertEqual(browser.post('/api/push/status', json={'endpoint': self.sub['endpoint']},
                                     headers={'X-CSRF-Token': csrf}).status_code, 401)
        with browser.session_transaction() as session:
            session.update(authorized=True, account='a' * 64)
        self.assertEqual(browser.post('/api/push/subscribe', json={}).status_code, 403)
        with patch('push_notifications.configuration', return_value={'configured': True, 'publicKey': 'public-only'}):
            body = {'subscription': self.sub, 'preferences': push.DEFAULT_PREFERENCES}
            headers = {'X-CSRF-Token': csrf}
            self.assertEqual(browser.post('/api/push/subscribe', json=body, headers=headers).status_code, 200)
            self.assertEqual(browser.get('/api/push/config').json, {'configured': True, 'publicKey': 'public-only'})
            body['subscription']['endpoint'] = 'https://127.0.0.1/private'
            self.assertEqual(browser.post('/api/push/subscribe', json=body, headers=headers).status_code, 409)


class ScheduledCloudTests(unittest.TestCase):
    def test_retry_deadline_and_attempts_persist_between_leased_cloud_invocations(self):
        store = FakeCloudStore()
        root = CloudService(store, Fernet.generate_key())
        alice = root.for_account('a' * 64)
        alice.store.ensure()
        store.accounts[alice.store.account]['credential'] = alice._encrypt({'token': 'synthetic', 'cookies': {}})
        client = DemoClient()
        client.reserve = Mock(wraps=client.reserve)
        with patch('cloud_service.LibraryClient', return_value=client), \
                patch.dict('os.environ', {'LIBRARY_NFC_TAGS': '{"102":"0123456789ABCDEF"}'}), \
                patch('seat_service.time.time', return_value=EVENING) as clock:
            alice.set_schedule('102:3', MORNING)
            client.reserve.side_effect = LibraryError('not open yet', rejected=True)
            clock.return_value = MORNING
            alice.tick()
            self.assertEqual(alice.store.read()['nextPollAt'], MORNING + 3)
            self.assertTrue(alice.store.read()['pendingWork'])
            self.assertEqual(alice.snapshot()['notifications'], [])
            second_device = root.for_account(alice.store.account)
            clock.return_value = MORNING + 2
            second_device.tick()
            client.reserve.assert_called_once()
            self.assertEqual(alice.store.read()['nextPollAt'], MORNING + 3)
            second_device._execute(lambda worker: worker.tick(allow_actions=False))
            self.assertEqual(alice.snapshot()['scheduledBooking']['retryAt'], MORNING + 3)
            client.reserve.assert_called_once()
            alice.store.claim('another invocation')
            clock.return_value = MORNING + 3
            with self.assertRaises(LibraryError):
                second_device.tick()
            client.reserve.assert_called_once()
            alice.store.release('another invocation')
            client.reserve.side_effect = None
            second_device.tick()
            self.assertEqual(alice.snapshot()['scheduledBooking']['status'], 'succeeded')
            self.assertEqual(alice.snapshot()['scheduledBooking']['attempts'], 2)
            self.assertEqual(client.reserve.call_count, 2)

    def test_cloud_hydration_lease_deadline_and_account_isolation(self):
        store = FakeCloudStore()
        root = CloudService(store, Fernet.generate_key())
        alice, bob = root.for_account('a' * 64), root.for_account('b' * 64)
        client = DemoClient()
        client.reserve = Mock(wraps=client.reserve)
        for cloud in (alice, bob):
            cloud.store.ensure()
            store.accounts[cloud.store.account]['credential'] = cloud._encrypt({'token': 'synthetic', 'cookies': {}})
        with patch('cloud_service.LibraryClient', return_value=client), \
                patch.dict('os.environ', {'LIBRARY_NFC_TAGS': '{"102":"0123456789ABCDEF"}'}), \
                patch('seat_service.time.time', return_value=EVENING) as clock:
            alice.set_schedule('102:3', MORNING)
            self.assertIsNone(bob.snapshot()['scheduledBooking'])
            alice.tick()
            self.assertLessEqual(alice.store.read()['nextPollAt'], MORNING)
            client.reserve.assert_not_called()
            alice.store.claim('another invocation')
            clock.return_value = MORNING
            with self.assertRaises(LibraryError):
                alice.tick()
            client.reserve.assert_not_called()
            alice.store.release('another invocation')
            alice.tick()
            self.assertEqual(alice.snapshot()['scheduledBooking']['status'], 'succeeded')
            self.assertIsNotNone(alice.snapshot()['autoRenew'])
            self.assertIsNone(bob.snapshot()['scheduledBooking'])
            self.assertTrue(alice.store.read()['pendingWork'])
            alice.tick()
            client.reserve.assert_called_once_with(102003)


if __name__ == '__main__':
    unittest.main()
