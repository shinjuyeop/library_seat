import threading
import unittest
from unittest.mock import Mock, patch

import requests

from cloud_service import SupabaseStore
from seat_service import LibraryError


class StoreConnectionTests(unittest.TestCase):
    @patch('cloud_service.requests.Session')
    def test_reuses_transport_across_account_wrappers_with_scoped_queries(self, session):
        response = session.return_value.request.return_value.__enter__.return_value
        response.json.return_value = [{'document': {}}]
        root = SupabaseStore('https://db.example', 'synthetic')
        root.for_account('a' * 64).read()
        root.for_account('b' * 64).read()
        session.assert_called_once()
        calls = session.return_value.request.call_args_list
        self.assertEqual([call.kwargs['params']['account_key'] for call in calls], ['eq.' + 'a' * 64, 'eq.' + 'b' * 64])
        self.assertTrue(all(call.kwargs['timeout'] == 10 for call in calls))

    @patch('cloud_service.requests.Session')
    def test_concurrent_threads_have_separate_sessions(self, session):
        session.side_effect = lambda: Mock(request=Mock(return_value=Mock(
            __enter__=Mock(return_value=Mock(json=Mock(return_value=[]))),
            __exit__=Mock(return_value=False))))
        store = SupabaseStore('https://db.example', 'synthetic', 'a' * 64)
        threads = [threading.Thread(target=store.push_devices) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(session.call_count, 2)

    @patch('cloud_service.requests.Session')
    def test_failed_write_is_not_retried(self, session):
        session.return_value.request.side_effect = requests.ConnectionError('offline')
        store = SupabaseStore('https://db.example', 'synthetic', 'a' * 64)
        with self.assertRaises(LibraryError):
            store.release('synthetic-owner')
        session.return_value.request.assert_called_once()


if __name__ == '__main__':
    unittest.main()
