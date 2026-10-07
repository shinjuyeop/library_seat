import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from cryptography.fernet import Fernet

from cloud_service import CloudService
from seat_service import DemoClient, LibraryClient, LibraryError, SeatService, SettingsStore, normalize_reservation
from webapp import create_app


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SettingsStore(Path(self.temp.name) / 'state.db')
        self.client = DemoClient()
        self.service = SeatService(self.store, client=self.client)


class WorkerTests(ServiceFixture):
    def test_success_stops_all_targets_and_survives_restart(self):
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.service.set_wait(['102:3', '102:393'], True)
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.client.reserve.call_count, 1)
        self.assertFalse(self.store.load()['running'])
        self.assertEqual(self.store.load()['targets'], [])
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'TEMP_CHARGE')
        restored = SeatService(self.store)
        self.assertFalse(restored.snapshot()['running'])

    def test_worker_reserves_without_any_web_request(self):
        self.service.set_wait(['102:3'], True)
        self.service.start()
        self.addCleanup(self.service.stop)
        deadline = time.monotonic() + 3
        while not self.service.snapshot()['reservation'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertIsNotNone(self.service.snapshot()['reservation'])

    def test_existing_reservation_is_never_released(self):
        self.service.set_wait(['102:3'], True)
        self.client.reserve(101021)
        self.client.release = Mock()
        self.service.tick()
        self.client.release.assert_not_called()
        self.assertFalse(self.service.snapshot()['running'])
        self.assertEqual(self.service.snapshot()['reservation']['seatNo'], '21')

    def test_unknown_occupancy_is_not_treated_as_empty(self):
        self.service.set_wait(['102:3'], True)
        self.client.seats = Mock(return_value=[{'id': 102003, 'code': '3'}])
        self.client.reserve = Mock()
        self.service.tick()
        self.client.reserve.assert_not_called()

    def test_query_failure_prevents_any_write(self):
        self.service.set_wait(['102:3'], True)
        self.client.reservation = Mock(side_effect=LibraryError('read failed'))
        self.client.reserve = Mock()
        self.service.tick()
        self.client.reserve.assert_not_called()
        self.assertFalse(self.service.snapshot()['reservationFresh'])

    def test_ambiguous_write_is_not_retried(self):
        self.service.set_wait(['102:3', '102:393'], True)
        self.client.reserve = Mock(side_effect=LibraryError('timeout', uncertain=True))
        self.service.tick()
        self.service.tick()
        self.client.reserve.assert_called_once()
        self.assertFalse(self.store.load()['running'])

    def test_process_crash_disarms_before_mutation(self):
        self.service.set_wait(['102:3'], True)
        def crash(_):
            self.assertFalse(self.store.load()['running'])
            raise SystemExit('simulated process termination')
        self.client.reserve = crash
        with self.assertRaises(SystemExit):
            self.service.tick()
        self.assertFalse(SeatService(self.store).snapshot()['running'])

    def test_auth_expiration_pauses_without_losing_targets(self):
        self.service.set_wait(['102:3'], True)
        self.client.reservation = Mock(side_effect=LibraryError('expired', expired=True))
        self.service.tick()
        self.assertIsNone(self.service.client)
        self.assertEqual(self.service.snapshot()['targets'], ['102:3'])
        self.assertFalse(self.service.snapshot()['connected'])

    def test_known_competition_failure_can_retry_next_tick(self):
        self.service.set_wait(['102:3'], True)
        self.client.reserve = Mock(side_effect=LibraryError('seat occupied'))
        self.service.tick()
        self.assertTrue(self.store.load()['running'])
        self.service.tick()
        self.assertEqual(self.client.reserve.call_count, 2)

    def test_unknown_state_never_becomes_confirmed(self):
        item = normalize_reservation({'id': 5, 'seat': {'code': '3'}})
        self.assertEqual(item['state'], 'UNKNOWN')
        with self.assertRaises(LibraryError):
            LibraryClient('dummy').release(item)

    def test_error_payload_is_not_empty_reservation(self):
        for payload in ({'message':'error'}, {}, None, {'data':None}):
            with self.subTest(payload=payload), self.assertRaises(LibraryError):
                normalize_reservation(payload)
        self.assertIsNone(normalize_reservation({'list': []}))

    def test_stale_cancel_cannot_return_newly_confirmed_seat(self):
        self.client.reserve(102003)
        self.client.current['state'] = 'CHARGE'
        self.client.release = Mock()
        with self.assertRaises(LibraryError):
            self.service.release('1', 'TEMP_CHARGE')
        self.client.release.assert_not_called()

    def test_http_timeout_is_ambiguous_for_write_only(self):
        client = LibraryClient('secret-test-token')
        client.session.request = Mock(side_effect=requests.Timeout())
        for method in ('GET','POST'):
            with self.assertRaises(LibraryError) as result:
                client._request(method, 'seat-charges')
            self.assertEqual(result.exception.uncertain, method == 'POST')


class ApiTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.app = create_app(self.service, 'a-test-password-long-enough', secret='test-only', secure_cookie=False)
        self.app.testing = True
        self.browser = self.app.test_client()
        self.csrf = self.browser.get('/api/session').json['csrf']

    def login(self):
        response = self.browser.post('/api/login', json={'password':'a-test-password-long-enough'}, headers={'X-CSRF-Token':self.csrf})
        self.assertEqual(response.status_code, 200)
        self.csrf = response.json['csrf']

    def test_authentication_and_csrf(self):
        self.assertEqual(self.browser.get('/api/state').status_code, 401)
        self.assertEqual(self.browser.post('/api/login', json={'password':'a-test-password-long-enough'}).status_code, 403)
        self.login()
        self.assertEqual(self.browser.get('/api/state').status_code, 200)
        self.assertEqual(self.browser.post('/api/wait', json={'targets':['102:3'],'running':True}).status_code, 403)

    def test_wait_survives_browser_logout(self):
        self.login()
        headers = {'X-CSRF-Token':self.csrf}
        self.assertEqual(self.browser.post('/api/wait', json={'targets':['102:3'],'running':True}, headers=headers).status_code, 200)
        self.browser.post('/api/logout', json={}, headers=headers)
        self.service.tick()
        self.assertIsNotNone(self.service.snapshot()['reservation'])
        self.assertEqual(self.browser.get('/api/state').status_code, 401)

    def test_bad_input_and_no_credential_leak(self):
        self.login()
        for body in ([], {'targets':['999:1'], 'running':True}, {'targets':['102:3'], 'running':'true'}):
            result = self.browser.post('/api/wait', json=body, headers={'X-CSRF-Token':self.csrf})
            self.assertIn(result.status_code, (400,409))
        response = self.browser.get('/api/state')
        self.assertNotIn('token', response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_static_shell_and_cloud_cron_protection(self):
        for path in ('/', '/assets/app.js', '/assets/style.css', '/assets/manifest.webmanifest', '/sw.js'):
            with self.browser.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
        self.assertEqual(self.browser.post('/api/cron', json={}).status_code, 401)


class FakeCloudStore:
    def __init__(self):
        self.document = {}
        self.owner = None

    def read(self):
        return copy.deepcopy(self.document)

    def claim(self, owner):
        if self.owner:
            return False
        self.owner = owner
        return True

    def save(self, owner, document):
        if owner != self.owner:
            raise LibraryError('lease lost')
        self.document = copy.deepcopy(document)

    def release(self, owner):
        if self.owner == owner:
            self.owner = None


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeCloudStore()
        self.key = Fernet.generate_key()
        self.cloud = CloudService(self.store, self.key)
        self.client = DemoClient()
        self.patch = patch('cloud_service.LibraryClient', return_value=self.client)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_encrypted_login_and_state_survive_new_instance(self):
        self.cloud.connect_token('test-library-token', {'session':'test-cookie'})
        self.assertNotIn('test-library-token', json.dumps(self.store.document))
        self.assertNotIn('test-cookie', json.dumps(self.store.document))
        self.cloud.set_wait(['102:3'], True)
        other = CloudService(self.store, self.key)
        other.tick()
        self.assertFalse(other.snapshot()['running'])
        self.assertEqual(other.snapshot()['reservation']['seatNo'], '3')
        self.assertNotIn('credential', other.snapshot())

    def test_overlapping_ticks_are_rejected(self):
        self.store.owner = 'different-instance'
        with self.assertRaises(LibraryError):
            self.cloud.tick()
        self.assertEqual(self.store.owner, 'different-instance')

    def test_reconnect_and_disconnect_recover_from_changed_encryption_key(self):
        self.cloud.connect_token('test-library-token', {})
        other = CloudService(self.store, Fernet.generate_key())
        other.connect_token('replacement-token', {})
        other.disconnect()
        self.assertNotIn('credential', self.store.document)
        self.assertFalse(other.snapshot()['connected'])

    def test_disarmed_state_is_durable_before_cloud_write(self):
        self.cloud.connect_token('test-library-token', {})
        self.cloud.set_wait(['102:3'], True)
        original = self.client.reserve
        def reserve(seat):
            self.assertFalse(self.store.document['state']['running'])
            original(seat)
        self.client.reserve = reserve
        self.cloud.tick()

    def test_missing_scheduler_heartbeat_is_visible(self):
        self.cloud.connect_token('test-library-token', {})
        self.cloud.set_wait(['102:3'], True)
        self.assertIsNotNone(self.cloud.snapshot()['error'])


if __name__ == '__main__':
    unittest.main()
