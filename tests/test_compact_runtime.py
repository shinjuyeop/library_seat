"""Transport budgets and crash boundaries for the compact cloud runtime."""
import copy
import time
import unittest
from unittest.mock import Mock

from cryptography.fernet import Fernet

from cloud_service import CloudService, SupabaseStore
from seat_service import LibraryError


class CompactRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.store = SupabaseStore('https://db.example', 'synthetic', 'a' * 64)
        self.cloud = CloudService(self.store, Fernet.generate_key())
        self.document = {'state': {'catalogVersion': 1, 'lastChecked': time.time(), 'running': False}}
        self.catalog = [{'key': '102:3', 'roomId': 102, 'number': '3', 'id': 102003}]

    def test_idle_worker_uses_two_requests_without_fetching_or_sending_the_catalog(self):
        self.store._request = Mock(side_effect=[copy.deepcopy(self.document), True])
        self.cloud.tick()
        calls = self.store._request.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[1], '/rpc/library_account_acquire')
        self.assertIs(calls[0].kwargs['json']['p_catalog'], False)
        self.assertEqual(calls[1].args[1], '/rpc/library_account_commit')
        saved = calls[1].kwargs['json']
        self.assertIs(saved['p_release'], True)
        self.assertIsNone(saved['p_catalog'])
        self.assertNotIn('seats', saved['p_document']['state'])
        self.assertEqual(saved['p_owner'], calls[0].kwargs['json']['p_owner'])

    def test_loaded_unchanged_catalog_is_not_uploaded_again(self):
        document = copy.deepcopy(self.document)
        document['state']['seats'] = self.catalog
        self.store._request = Mock(side_effect=[document, True])
        self.cloud._execute(lambda worker: worker._event('synthetic activity'))
        saved = self.store._request.call_args_list[1].kwargs['json']
        self.assertIsNone(saved['p_catalog'])
        self.assertNotIn('seats', saved['p_document']['state'])

    def test_changed_catalog_is_saved_once_and_intermediate_saves_keep_the_lease(self):
        self.store._request = Mock(side_effect=[copy.deepcopy(self.document), True, True])
        def action(worker):
            worker._update(seats=copy.deepcopy(self.catalog), running=False)
            worker._save()
        self.cloud._execute(action)
        calls = self.store._request.call_args_list
        intermediate, final = [call.kwargs['json'] for call in calls[1:]]
        self.assertEqual(intermediate['p_catalog'], self.catalog)
        self.assertFalse(intermediate['p_release'])
        self.assertIsNone(final['p_catalog'])
        self.assertTrue(final['p_release'])
        self.assertTrue(all('seats' not in call.kwargs['json']['p_document']['state'] for call in calls[1:]))

    def test_busy_acquire_never_runs_the_action_or_releases_someone_elses_lease(self):
        self.store._request = Mock(return_value=None)
        action = Mock()
        with self.assertRaises(LibraryError):
            self.cloud._execute(action)
        action.assert_not_called()
        self.store._request.assert_called_once()

    def test_failed_final_commit_only_unlocks_and_never_retries_the_save(self):
        self.store._request = Mock(side_effect=[copy.deepcopy(self.document), False, True])
        with self.assertRaises(LibraryError):
            self.cloud.tick()
        self.assertEqual([call.args[1] for call in self.store._request.call_args_list], [
            '/rpc/library_account_acquire', '/rpc/library_account_commit', '/rpc/library_account_unlock'])

    def test_setup_failure_releases_the_lease_without_overwriting_the_document(self):
        self.store._request = Mock(side_effect=[{'credential': 'invalid-encrypted-data'}, True])
        with self.assertRaises(LibraryError):
            self.cloud.tick()
        self.assertEqual([call.args[1] for call in self.store._request.call_args_list], [
            '/rpc/library_account_acquire', '/rpc/library_account_unlock'])

    def test_missing_account_read_is_reported_instead_of_returning_an_empty_snapshot(self):
        self.store._request = Mock(return_value=None)
        with self.assertRaises(LibraryError):
            self.cloud.snapshot()


if __name__ == '__main__':
    unittest.main()
