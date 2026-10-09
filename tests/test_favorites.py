import copy
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

from cloud_service import CloudService
from seat_service import DemoClient, LibraryError, SeatService
from test_service import FakeCloudStore, ServiceFixture
from webapp import create_app


class FavoriteTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service.tick()

    def test_order_idempotency_and_restart(self):
        self.service.update_favorite('232:3', True)
        self.service.update_favorite('102:1', True)
        self.service.update_favorite('232:3', True)
        restored = SeatService(self.store, client=self.client)
        self.assertEqual(restored.snapshot()['favorites'], ['232:3', '102:1'])
        restored.update_favorite('232:3', False)
        restored.update_favorite('232:3', False)
        self.assertEqual(self.store.load()['favorites'], ['102:1'])

    def test_preferences_do_not_touch_seat_or_waiting_or_schedule(self):
        self.service.set_wait(['102:1'], True)
        self.service._update(scheduledBooking={'id': 'synthetic-job', 'status': 'pending'})
        original = copy.deepcopy(self.service.snapshot())
        self.client.reserve = Mock()
        self.client.release = Mock()
        self.client.seats = Mock()
        self.service.update_favorite('102:3', True)
        current = self.service.snapshot()
        for field in ('running', 'targets', 'reservation', 'scheduledBooking', 'autoRenew'):
            self.assertEqual(current[field], original[field], field)
        self.client.reserve.assert_not_called()
        self.client.release.assert_not_called()
        self.client.seats.assert_not_called()
        self.service.client = None
        self.service.update_favorite('102:3', False)
        self.assertEqual(self.service.snapshot()['favorites'], [])

    def test_invalid_unknown_and_excluded_seats_are_rejected_and_saved_data_sanitized(self):
        for key, enabled in ((None, True), ('999:3', True), ('101:409', True),
                             ('234:149', True), ('102:99999', True), ('102:3', 'true')):
            with self.assertRaises(LibraryError):
                self.service.update_favorite(key, enabled)
        self.service.state['favorites'] = ['101:409', '234:149', '102:3', '102:3', None]
        self.assertEqual(self.service.snapshot()['favorites'], ['102:3'])
        self.service.update_favorite('102:3', False)
        self.assertEqual(self.service.snapshot()['favorites'], [])

    def test_bounded_list_does_not_block_idempotent_add_or_remove(self):
        self.service._update(favorites=[f'102:{n}' for n in range(1, 101)])
        with self.assertRaises(LibraryError):
            self.service.update_favorite('232:3', True)
        self.service.update_favorite('102:3', True)
        self.service.update_favorite('102:3', False)
        self.assertEqual(len(self.service.snapshot()['favorites']), 99)

    def test_api_requires_login_and_csrf(self):
        browser = create_app(self.service, 'synthetic-long-password', secure_cookie=False).test_client()
        csrf = browser.get('/api/session').json['csrf']
        body = {'key': '102:3', 'enabled': True}
        self.assertEqual(browser.post('/api/favorites/seat', json=body, headers={'X-CSRF-Token': csrf}).status_code, 401)
        csrf = browser.post('/api/login', json={'password': 'synthetic-long-password'}, headers={'X-CSRF-Token': csrf}).json['csrf']
        self.assertEqual(browser.post('/api/favorites/seat', json=body).status_code, 403)
        self.assertEqual(browser.post('/api/favorites/seat', json=[], headers={'X-CSRF-Token': csrf}).status_code, 400)
        self.assertEqual(browser.post('/api/favorites/seat', json=body, headers={'X-CSRF-Token': csrf}).status_code, 200)
        self.assertEqual(browser.get('/api/state').json['favorites'], ['102:3'])


class FavoriteCloudTests(unittest.TestCase):
    def test_account_isolation_multiple_devices_relogin_and_lease(self):
        root = CloudService(FakeCloudStore(), Fernet.generate_key())
        clients = {'alice': DemoClient(), 'bob': DemoClient()}
        with patch('cloud_service.LibraryClient', side_effect=lambda token, cookies=None: clients[token]), \
                patch('cloud_service.login_to_library', side_effect=lambda user, password: {'token': user, 'cookies': {}, 'identity': user}):
            alice_id = root.login('alice', 'synthetic-password', remember=True)
            bob_id = root.login('bob', 'synthetic-password', remember=True)
            alice, bob = root.for_account(alice_id), root.for_account(bob_id)
            alice.update_favorite('102:3', True)
            bob.update_favorite('232:3', True)
            root.login('alice', 'synthetic-password', remember=True)
            second = root.for_account(alice_id)
            self.assertEqual(second.snapshot()['favorites'], ['102:3'])
            second.update_favorite('102:3', False)
            self.assertEqual(alice.snapshot()['favorites'], [])
            self.assertEqual(bob.snapshot()['favorites'], ['232:3'])
            self.assertTrue(alice.store.claim('other-device'))
            with self.assertRaises(LibraryError):
                second.update_favorite('102:1', True)
            alice.store.release('other-device')
            self.assertEqual(alice.snapshot()['favorites'], [])
