import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from library_login import login_to_library
from seat_service import DemoClient, LibraryClient, LibraryError, SeatService, SettingsStore
from webapp import create_app


class LibraryPauseTests(unittest.TestCase):
    def test_pause_prevents_login_and_all_provider_requests_before_transport(self):
        client = LibraryClient('synthetic-token')
        self.addCleanup(client.close)
        with patch.dict(os.environ, {'LIBRARY_ACCESS_PAUSED': '1'}), \
                patch('library_login.requests.Session') as login_transport, \
                patch.object(client.session, 'request') as api_transport:
            with self.assertRaises(LibraryError):
                login_to_library('synthetic-id', 'synthetic-password')
            for method in ('GET', 'POST', 'DELETE'):
                with self.subTest(method=method), self.assertRaises(LibraryError):
                    client._request(method, 'seat-charges')
            login_transport.assert_not_called()
            api_transport.assert_not_called()

    def test_paused_api_skips_cron_and_blocks_actions_but_keeps_saved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            service = SeatService(SettingsStore(Path(directory) / 'state.db'), client=DemoClient())
            service.tick()
            app = create_app(service, 'a-test-password-long-enough', secret='test', secure_cookie=False)
            app.testing = True
            browser = app.test_client()
            csrf = browser.get('/api/session').json['csrf']
            response = browser.post('/api/login', json={'password': 'a-test-password-long-enough'},
                                    headers={'X-CSRF-Token': csrf})
            headers = {'X-CSRF-Token': response.json['csrf']}
            before = service.snapshot()
            with patch.dict(os.environ, {'LIBRARY_ACCESS_PAUSED': '1', 'CRON_SECRET': 'x' * 32}):
                self.assertTrue(browser.get('/api/session').json['libraryAccessPaused'])
                state = browser.get('/api/state')
                self.assertEqual(state.status_code, 200)
                self.assertFalse(state.json['reservationFresh'])
                self.assertIn('일시 중지', state.json['error'])
                for route in ('library-login', 'connect', 'refresh', 'reserve', 'confirm', 'renew',
                              'release', 'reassign', 'auto-renew', 'wait', 'wait/seat', 'schedule'):
                    with self.subTest(route=route):
                        response = browser.post('/api/' + route, json={}, headers=headers)
                        self.assertEqual(response.status_code, 503)
                        self.assertEqual(response.json['kind'], 'paused')
                service.for_account = Mock()
                response = browser.post('/api/cron', json={}, headers={'Authorization': 'Bearer ' + 'x' * 32})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['skipped'], 'library_access_paused')
                service.for_account.assert_not_called()
                self.assertEqual(browser.post('/api/cron', json={}).status_code, 401)
                self.assertEqual(browser.post('/api/logout', json={}, headers=headers).status_code, 200)
            self.assertEqual(service.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
