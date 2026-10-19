import copy
import time
import unittest
from email.utils import formatdate
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

from cloud_service import CloudService
from library_access import access_context, retry_after, work_due
from library_login import LoginError, login_to_library
from seat_service import LibraryClient, LibraryError, SeatService
from test_service import DemoClient, FakeCloudStore, ServiceFixture


class TransportPolicyTests(unittest.TestCase):
    def test_only_recognized_json_auth_expiry_allows_reauthentication(self):
        client = LibraryClient('synthetic')
        self.addCleanup(client.close)
        response = Mock(status_code=401)
        client.session.request = Mock(return_value=response)
        response.json.return_value = {'success': False, 'code': 'error.token.expired'}
        with self.assertRaises(LibraryError) as caught:
            client.reservation()
        self.assertTrue(caught.exception.expired)
        response.json.side_effect = ValueError()
        with self.assertRaises(LibraryError) as caught:
            client.reservation()
        self.assertTrue(caught.exception.blocked)
        self.assertFalse(caught.exception.expired)

    def test_forbidden_stops_other_accounts_and_survives_new_service_instances(self):
        store = FakeCloudStore()
        first, second = LibraryClient('synthetic-a'), LibraryClient('synthetic-b')
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.session.request = Mock(return_value=Mock(status_code=403))
        second.session.request = Mock()
        with access_context(store.for_account('a')):
            with self.assertRaises(LibraryError) as caught:
                first.reservation()
        self.assertTrue(caught.exception.blocked)
        self.assertFalse(caught.exception.expired)
        first.session.request.assert_called_once()
        with access_context(store.for_account('b')):
            with self.assertRaises(LibraryError):
                second.reservation()
        second.session.request.assert_not_called()
        root = CloudService(store, Fernet.generate_key())
        with patch('cloud_service.login_to_library') as authenticate:
            with self.assertRaises(LibraryError):
                root.login('synthetic-user', 'synthetic-password')
        authenticate.assert_not_called()

    def test_rate_limit_saves_retry_after_without_automatically_reopening(self):
        store = FakeCloudStore()
        client = LibraryClient('synthetic')
        self.addCleanup(client.close)
        client.session.request = Mock(return_value=Mock(status_code=429, headers={'Retry-After': '3600'}))
        with patch('time.time', return_value=1000), access_context(store):
            with self.assertRaises(LibraryError) as caught:
                client.reservation()
        self.assertEqual(caught.exception.retry_at, 4600)
        with patch('time.time', return_value=5000), access_context(store):
            with self.assertRaises(LibraryError):
                client.reservation()
        client.session.request.assert_called_once()

    def test_retry_after_http_date_and_malformed_value(self):
        with patch('time.time', return_value=1000):
            self.assertEqual(retry_after(Mock(headers={'Retry-After': formatdate(3000, usegmt=True)})), 3000)
            self.assertIsNone(retry_after(Mock(headers={'Retry-After': 'bad'})))

    def test_login_html_is_a_shared_hold_and_never_wrong_password(self):
        store = FakeCloudStore()
        response = Mock(status_code=200)
        response.json.side_effect = ValueError()
        with access_context(store), patch('library_login.requests.Session') as factory:
            factory.return_value.__enter__.return_value.post.return_value = response
            with self.assertRaises(LoginError) as caught:
                login_to_library('synthetic-id', 'synthetic-password')
        self.assertTrue(caught.exception.blocked)
        self.assertEqual(caught.exception.kind, 'unexpected_response')
        self.assertTrue(store.access_status()['blocked'])

    def test_read_server_error_is_retryable_but_write_result_remains_uncertain(self):
        client = LibraryClient('synthetic')
        self.addCleanup(client.close)
        client.session.request = Mock(return_value=Mock(status_code=503, headers={}))
        for method in ('GET', 'POST'):
            with self.subTest(method=method), self.assertRaises(LibraryError) as caught:
                client._request(method, 'seat-charges')
            self.assertTrue(caught.exception.retryable)
            self.assertEqual(caught.exception.uncertain, method == 'POST')


class CloudPolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeCloudStore()
        self.key = Fernet.generate_key()
        self.root = CloudService(self.store, self.key)
        self.client = DemoClient()
        self.clients = patch('cloud_service.LibraryClient', return_value=self.client)
        self.clients.start()
        self.addCleanup(self.clients.stop)
        self.login_patch = patch('cloud_service.login_to_library', return_value={
            'token': 'synthetic', 'cookies': {}, 'identity': 'synthetic'})
        self.authenticate = self.login_patch.start()
        self.addCleanup(self.login_patch.stop)
        self.cloud = self.root.for_account(self.root.login('synthetic', 'synthetic-password', remember=True))

    def test_idle_account_and_disconnected_saved_login_never_poll(self):
        self.client.reservation = Mock(wraps=self.client.reservation)
        self.client.seats = Mock(wraps=self.client.seats)
        document = self.store.accounts[self.cloud.store.account]
        document['state']['lastChecked'] = 0
        for _ in range(3):
            CloudService(self.cloud.store, self.key).tick()
        self.client.reservation.assert_not_called()
        self.client.seats.assert_not_called()
        self.assertIsNone(self.cloud.store.read()['nextPollAt'])
        self.store.accounts[self.cloud.store.account].pop('credential')
        self.authenticate.reset_mock()
        self.cloud.tick()
        self.authenticate.assert_not_called()
        self.assertIsNone(self.cloud.store.read()['nextPollAt'])

    def test_future_booking_does_not_refresh_catalog_or_reauthenticate_early(self):
        due = time.time() + 3600
        self.store.accounts[self.cloud.store.account]['state']['scheduledBooking'] = {
            'status': 'pending', 'dueAt': due, 'retryAt': None}
        self.client.reservation = Mock(wraps=self.client.reservation)
        self.client.seats = Mock(wraps=self.client.seats)
        self.authenticate.reset_mock()
        self.cloud.tick()
        self.client.reservation.assert_not_called()
        self.client.seats.assert_not_called()
        self.authenticate.assert_not_called()
        self.assertEqual(self.cloud.store.read()['nextPollAt'], due)

    def test_sleeping_until_a_future_renewal_does_not_report_missing_heartbeat(self):
        document = self.store.accounts[self.cloud.store.account]
        document['schedulerLastSeen'] = time.time() - 3600
        document['state']['autoRenew'] = {'status': 'scheduled', 'dueAt': time.time() + 3600}
        self.assertIsNone(self.cloud.snapshot()['error'])

    def test_transient_read_retry_is_bounded_and_persists_between_instances(self):
        self.store.accounts[self.cloud.store.account]['state']['autoRenew'] = {
            'status': 'scheduled', 'dueAt': time.time() - 1, 'retryAt': None}
        self.client.reservation = Mock(side_effect=LibraryError('temporary', retryable=True))
        now = time.time() + 1
        for attempt, delay in enumerate((300, 900, 3600, None), start=1):
            with patch('time.time', return_value=now):
                CloudService(self.cloud.store, self.key).tick()
                retry = self.cloud.store.read()['state']['connectionRetry']
                self.assertEqual(retry['attempts'], attempt)
                self.cloud.tick()  # Deliver/skip the queued failure notice without provider access.
                if delay is not None:
                    self.assertEqual(retry['retryAt'], now + delay)
                    self.assertEqual(self.cloud.store.read()['nextPollAt'], now + delay)
                    self.assertEqual(self.client.reservation.call_count, attempt)
                else:
                    self.assertEqual(retry['status'], 'paused')
                    self.assertIsNone(self.cloud.store.read()['nextPollAt'])
            now += delay or 10000
        self.cloud.tick()
        self.assertEqual(self.client.reservation.call_count, 4)

    def test_successful_complete_read_clears_transient_failure_count(self):
        self.cloud.set_wait(['232:1'], True)
        original = self.client.reservation
        self.client.reservation = Mock(side_effect=LibraryError('temporary', retryable=True))
        now = time.time() + 1
        with patch('time.time', return_value=now):
            self.cloud.tick()
        self.client.reservation = original
        with patch('time.time', return_value=now + 300):
            self.cloud.tick()
        self.assertIsNone(self.cloud.store.read()['state']['connectionRetry'])

    def test_failed_auto_login_is_not_retried_even_after_a_day_or_restart(self):
        self.cloud.set_wait(['232:1'], True)
        self.client.reservation = Mock(side_effect=LibraryError('expired', expired=True))
        self.cloud.tick()
        self.authenticate.side_effect = LoginError('temporary', kind='unavailable', retryable=True)
        self.cloud.tick()
        calls = self.authenticate.call_count
        with patch('time.time', return_value=time.time() + 86400):
            CloudService(self.cloud.store, self.key).tick()
        self.assertEqual(self.authenticate.call_count, calls)
        self.assertTrue(self.cloud.store.read()['autoLoginStopped'])
        self.assertIn('login', self.cloud.store.read())

    def test_manual_refresh_is_read_only_and_scopes_to_selected_room(self):
        self.cloud.set_wait(['102:3'], True)
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.client.seats = Mock(wraps=self.client.seats)
        other = next(seat for seat in self.cloud.snapshot()['seats'] if seat['key'] == '107:1')
        self.cloud.refresh(102)
        self.client.reserve.assert_not_called()
        self.client.seats.assert_called_once_with(102)
        self.assertEqual(next(seat for seat in self.cloud.snapshot()['seats'] if seat['key'] == '107:1'), other)


class WaitBudgetTests(ServiceFixture):
    def test_expired_wait_stops_without_a_provider_request_after_restart(self):
        self.service.set_wait(['232:1'], True)
        deadline = self.service.snapshot()['waitControl']['deadlineAt']
        self.client.reservation = Mock(wraps=self.client.reservation)
        with patch('time.time', return_value=deadline):
            restored = SeatService(self.store, client=self.client)
            restored.tick()
        self.assertFalse(restored.snapshot()['running'])
        self.client.reservation.assert_not_called()

    def test_wait_edit_does_not_extend_the_deadline_or_reset_request_budget(self):
        self.service.set_wait(['232:1'], True)
        before = copy.deepcopy(self.service.snapshot()['waitControl'])
        self.service.update_wait('232:2', True)
        self.assertEqual(self.service.snapshot()['waitControl'], before)
        self.service._update(waitControl={**before, 'checks': 60})
        self.client.reservation = Mock(wraps=self.client.reservation)
        self.service.tick()
        self.assertFalse(self.service.snapshot()['running'])
        self.client.reservation.assert_not_called()


if __name__ == '__main__':
    unittest.main()
