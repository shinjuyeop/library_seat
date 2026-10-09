"""Synthetic renewal fixtures; captured credentials and tags never enter tests."""
import copy
import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet

from cloud_service import CloudService
from seat_service import (DemoClient, KST, LibraryClient, LibraryError, SeatService,
                          closed_until, normalize_reservation, renewal_count, reservation_time)
from test_service import FakeCloudStore, ServiceFixture
from webapp import create_app

TAG = '0123456789ABCDEF'
NOON = datetime(2026, 10, 9, 12, tzinfo=KST).timestamp()


def end_at(timestamp):
    return datetime.fromtimestamp(timestamp, KST).strftime('%Y-%m-%d %H:%M:%S')


class RenewalTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.clock = patch('seat_service.time.time', return_value=NOON).start()
        self.addCleanup(patch.stopall)
        self.service.nfc_tags = {102: TAG}
        self.client.reserve(102003)
        self.client.confirm_reservation('1')
        self.service.tick()
        self.calls = Mock()
        for method in ('release', 'reserve', 'check_arrival', 'renew_reservation', 'confirm_reservation'):
            wrapper = Mock(wraps=getattr(self.client, method))
            setattr(self.client, method, wrapper)
            self.calls.attach_mock(wrapper, method)

    def due(self, minutes=119, count=3, *, enable=True):
        self.client.current.update(endTime=end_at(self.clock.return_value + minutes * 60),
                                   renewableAt=self.clock.return_value + (minutes - 120) * 60,
                                   renewableCnt=count, isRenewalImpossible=False)
        if enable:
            self.service.set_auto_renew(True, self.client.current['id'])

    def restart(self):
        self.service = SeatService(self.store, client=self.client, nfc_tags={102: TAG})
        self.service.tick()

    def test_provider_renewal_fields_and_strict_quota_parsing(self):
        current = normalize_reservation({'id': 1, 'state': {'code': 'CHARGE'},
            'seat': {'id': 102003, 'code': '3'}, 'room': {'id': 102},
            'renewableCnt': 0, 'renewalLimit': 3, 'isRenewable': False,
            'isRenewalImpossible': True, 'renewableDate': '2026-10-09 12:00:00'})
        self.assertEqual(current['renewableCnt'], 0)
        self.assertEqual(current['renewalLimit'], 3)
        self.assertEqual(current['renewableAt'], NOON)
        self.assertFalse(current['isRenewable'])
        for value in (None, False, True, '', '-1', -1, 0.0, 'bad'):
            self.assertIsNone(renewal_count(value))
        self.assertEqual(renewal_count('0'), 0)

    def test_wire_request_matches_har_without_exposing_payload(self):
        client = LibraryClient('synthetic-token')
        self.addCleanup(client.close)
        client._request = Mock()
        client.renew_reservation('123')
        client._request.assert_called_once_with('POST', 'seat-renewed-charges',
                                               json={'seatCharge': 123, 'smufMethodCode': 'MOBILE'})

    def test_auto_119_minute_boundary_and_verified_success(self):
        self.due(minutes=120)
        self.service.tick()
        self.client.renew_reservation.assert_not_called()
        self.clock.return_value += 59
        self.service.tick()
        self.client.renew_reservation.assert_not_called()
        self.clock.return_value += 1
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['renewableCnt'], 2)
        self.assertEqual(state['autoRenew']['status'], 'scheduled')
        self.assertEqual(state['autoRenew']['dueAt'], self.clock.return_value + 3660)
        self.assertEqual([call[0] for call in self.calls.mock_calls], ['check_arrival', 'renew_reservation'])
        self.service.tick()
        self.client.renew_reservation.assert_called_once()
        self.assertNotIn(TAG, json.dumps(state))

    def test_manual_renewal_at_120_minutes_does_not_enable_automation(self):
        self.service.set_auto_renew(False, '1')
        self.due(minutes=121, enable=False)
        with self.assertRaises(LibraryError):
            self.service.renew('1')
        self.client.check_arrival.assert_not_called()
        self.due(minutes=120, enable=False)
        self.service.renew('1')
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.assertEqual(self.service.snapshot()['reservation']['renewableCnt'], 2)

    def test_enabling_and_disabling_only_changes_setting(self):
        self.due()
        self.assertEqual(self.calls.mock_calls, [])
        self.service.set_auto_renew(False, '1')
        self.service.tick()
        self.assertEqual(self.calls.mock_calls, [])
        self.assertEqual(self.client.current['id'], '1')

    def test_existing_confirmed_booking_defaults_on_without_a_provider_write(self):
        state = self.service.snapshot()
        self.assertEqual(state['autoRenew']['reservationId'], '1')
        self.assertEqual(state['autoRenew']['dueAt'], NOON + 3660)
        self.service.tick(allow_repeat=False)
        self.assertEqual(self.calls.mock_calls, [])

    def test_opt_out_survives_due_reads_and_restart_but_new_booking_defaults_on(self):
        self.service.set_auto_renew(False, '1')
        self.due(enable=False)
        self.service.tick()
        self.restart()
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.assertEqual(self.store.load()['autoRenewDisabledId'], '1')
        self.assertEqual(self.calls.mock_calls, [])
        self.service.reserve('102:6')
        self.assertEqual(self.service.snapshot()['autoRenew']['reservationId'], '2')
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'scheduled')
        with self.assertRaises(LibraryError):
            self.service.set_auto_renew(False, '1')
        self.assertEqual(self.service.snapshot()['autoRenew']['reservationId'], '2')

    def test_default_requires_supported_confirmed_unexpired_booking(self):
        self.service._update(autoRenew=None)
        self.service.nfc_tags = {}
        self.service.tick()
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.service.nfc_tags = {102: TAG}
        self.client.current['state'] = 'TEMP_CHARGE'
        self.service.tick()
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.client.current.update(state='CHARGE', endTime=end_at(NOON - 1))
        self.service.tick()
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.assertEqual(self.calls.mock_calls, [])

    def test_zero_quota_reassigns_and_keeps_automation_on_the_new_booking(self):
        self.due(count=0)
        self.service.set_wait(['232:1'], True)
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual([call[0] for call in self.calls.mock_calls],
                         ['release', 'reserve', 'check_arrival', 'confirm_reservation'])
        self.assertEqual(state['reservation']['id'], '2')
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertEqual(state['reservation']['renewableCnt'], 3)
        self.assertEqual(state['autoRenew']['reservationId'], '2')
        self.assertEqual(state['autoRenew']['status'], 'scheduled')
        self.assertTrue(state['running'])
        self.assertEqual(state['targets'], ['232:1'])
        self.assertIsNone(state['repeat'])
        self.clock.return_value = state['autoRenew']['dueAt']
        self.service.tick()
        self.client.renew_reservation.assert_called_once_with('2')
        self.client.release.assert_called_once()

    def test_manual_reassignment_also_preserves_enabled_auto_renewal(self):
        self.due(minutes=180)
        self.service.reassign('1')
        self.assertEqual(self.service.snapshot()['autoRenew']['reservationId'], '2')
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'scheduled')

    def test_zero_quota_is_not_reset_early_or_after_fresh_quota_changes(self):
        self.due(minutes=120, count=0)
        self.service.tick()
        self.client.release.assert_not_called()
        self.due(count=0)
        original = self.client.reservation()
        changed = {**original, 'renewableCnt': 2}
        self.client.reservation = Mock(side_effect=[original, original, changed])
        self.service.tick()
        self.client.release.assert_not_called()

    def test_generic_failure_with_remaining_or_unknown_quota_never_reassigns(self):
        for count in (2, None, False):
            with self.subTest(count=count):
                self.due(count=count)
                # Unknown quota is supported only by the synthetic response, not DemoClient arithmetic.
                current = copy.deepcopy(self.client.current)
                current['isRenewable'] = True
                self.client.reservation = Mock(return_value=current)
                self.client.renew_reservation.side_effect = LibraryError('rejected')
                self.service.tick()
                self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'retry')
                self.assertEqual(self.service.snapshot()['autoRenew']['retryAt'], self.clock.return_value + 300)
                attempts = self.client.renew_reservation.call_count
                self.service.tick()
                self.assertEqual(self.client.renew_reservation.call_count, attempts)
        self.client.release.assert_not_called()
        self.client.reserve.assert_not_called()

    def test_definite_failure_rechecks_quota_before_reset(self):
        self.due()
        def reject(_):
            self.client.current['renewableCnt'] = 0
            raise LibraryError('quota exhausted')
        self.client.renew_reservation.side_effect = reject
        self.service.tick()
        self.client.release.assert_called_once()
        self.assertEqual(self.service.snapshot()['autoRenew']['reservationId'], '2')

    def test_provider_impossible_flag_with_remaining_quota_does_not_return_seat(self):
        self.due(count=2)
        self.client.current['isRenewalImpossible'] = True
        self.service.tick()
        self.assertEqual(self.calls.mock_calls, [])
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'retry')

    def test_night_failure_survives_restart_toggle_and_midnight_without_retry(self):
        self.clock.return_value = datetime(2026, 10, 9, 23, 10, tzinfo=KST).timestamp()
        self.due()
        self.client.renew_reservation.side_effect = LibraryError('closed')
        self.service.tick()
        morning = datetime(2026, 10, 10, 5, tzinfo=KST).timestamp()
        self.assertEqual(self.store.load()['renewNightUntil'], morning)
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'night')
        self.clock.return_value += 3600
        self.restart()
        self.service.set_auto_renew(False, '1')
        self.service.set_auto_renew(True, '1')
        self.service.tick()
        self.client.renew_reservation.assert_called_once()
        self.client.release.assert_not_called()
        # Closure removes the booking; never attempt to reserve it again.
        self.client.current = None
        self.service.tick()
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.clock.return_value = morning
        self.service.tick()
        self.client.reserve.assert_not_called()

    def test_night_zero_quota_never_returns_seat(self):
        self.clock.return_value = datetime(2026, 10, 9, 23, tzinfo=KST).timestamp()
        self.due(count=0)
        self.service.tick()
        self.assertEqual(self.calls.mock_calls, [])
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'night')
        self.restart()
        self.assertEqual(self.calls.mock_calls, [])

    def test_korean_closure_boundaries(self):
        for hour, minute, expected in ((22, 59, False), (23, 0, True), (0, 0, True), (4, 59, True), (5, 0, False)):
            self.assertEqual(bool(closed_until(datetime(2026, 10, 9, hour, minute, tzinfo=KST).timestamp())), expected)

    def test_five_am_does_not_restart_an_expired_booking_or_spin_polling(self):
        self.clock.return_value = datetime(2026, 10, 9, 23, tzinfo=KST).timestamp()
        self.due()
        self.client.renew_reservation.side_effect = LibraryError('closed')
        self.service.tick()
        self.clock.return_value = datetime(2026, 10, 10, 5, tzinfo=KST).timestamp()
        self.service.tick()
        self.assertIsNone(self.service.snapshot()['autoRenew'])
        self.client.renew_reservation.assert_called_once()

    def test_read_failure_after_post_never_replays_the_write(self):
        self.due()
        current = self.client.reservation()
        original_read = self.client.reservation
        self.client.reservation = Mock(side_effect=[current, current, current, LibraryError('failed read')])
        self.service.tick()
        self.client.reservation = original_read
        self.restart()
        self.client.renew_reservation.assert_called_once()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'paused')

    def test_stale_manual_request_does_not_change_new_job(self):
        self.due()
        plan = self.service.snapshot()['autoRenew']
        with self.assertRaises(LibraryError):
            self.service.renew('99')
        self.assertEqual(self.service.snapshot()['autoRenew'], plan)
        self.assertEqual(self.calls.mock_calls, [])

    def test_ambiguous_success_is_verified_without_repeating_write(self):
        self.due()
        renew = self.client.renew_reservation._mock_wraps
        def timeout(reservation_id):
            renew(reservation_id)
            raise LibraryError('timeout', uncertain=True)
        self.client.renew_reservation.side_effect = timeout
        self.service.tick()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'scheduled')
        self.restart()
        self.client.renew_reservation.assert_called_once()

    def test_unresolved_write_does_not_reset_even_if_quota_is_now_zero(self):
        self.due()
        def timeout(_):
            self.client.current['renewableCnt'] = 0
            raise LibraryError('timeout', uncertain=True)
        self.client.renew_reservation.side_effect = timeout
        self.service.tick()
        self.restart()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'paused')
        self.client.renew_reservation.assert_called_once()
        self.client.release.assert_not_called()

    def test_success_response_without_increased_end_time_pauses(self):
        self.due()
        self.client.renew_reservation = Mock()
        self.service.tick()
        self.restart()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'paused')
        self.client.renew_reservation.assert_called_once()

    def test_short_closing_time_extension_does_not_spend_quota_in_a_loop(self):
        self.due(minutes=90)
        def partial(_):
            self.client.current.update(endTime=end_at(NOON + 100 * 60), renewableCnt=2)
        self.client.renew_reservation.side_effect = partial
        self.service.tick()
        self.restart()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'paused')
        self.client.renew_reservation.assert_called_once()
        self.client.release.assert_not_called()

    def test_booking_change_after_arrival_prevents_renewal(self):
        self.due()
        self.client.check_arrival.side_effect = lambda *_: self.client.current.update(id='99')
        self.service.tick()
        self.client.renew_reservation.assert_not_called()
        self.client.release.assert_not_called()

    def test_crash_after_checkpoint_never_replays_renewal(self):
        self.due()
        def crash(*_):
            self.assertEqual(self.store.load()['autoRenew']['status'], 'working')
            raise SystemExit('crash')
        self.client.check_arrival.side_effect = crash
        with self.assertRaises(SystemExit):
            self.service.tick()
        self.restart()
        self.client.check_arrival.assert_called_once()
        self.client.renew_reservation.assert_not_called()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'paused')

    def test_crash_in_reassignment_cannot_cancel_twice(self):
        self.due(count=0)
        def crash(*_):
            self.assertEqual(self.store.load()['autoRenew']['status'], 'working')
            raise SystemExit('crash')
        self.client.release.side_effect = crash
        with self.assertRaises(SystemExit):
            self.service.tick()
        self.restart()
        self.client.release.assert_called_once()
        self.client.reserve.assert_not_called()

    def test_quota_not_reset_after_reassignment_stops_instead_of_looping(self):
        self.due(count=0)
        confirm = self.client.confirm_reservation._mock_wraps
        def no_reset(reservation_id):
            confirm(reservation_id)
            self.client.current['renewableCnt'] = 0
        self.client.confirm_reservation.side_effect = no_reset
        self.service.tick()
        self.restart()
        self.assertEqual(self.service.snapshot()['autoRenew']['status'], 'paused')
        self.client.release.assert_called_once()

    def test_release_and_disconnection_disable_auto_renewal(self):
        self.due()
        self.service.release('1', 'CHARGE')
        self.assertIsNone(self.store.load()['autoRenew'])
        self.client.reserve(102003)
        self.client.confirm_reservation('2')
        self.service.set_auto_renew(True, '2')
        self.service.disconnect()
        self.assertIsNone(self.store.load()['autoRenew'])

    def test_api_requires_auth_csrf_and_valid_input(self):
        app = create_app(self.service, 'a-test-password-long-enough', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        headers = {'X-CSRF-Token': csrf}
        for route in ('renew', 'auto-renew'):
            self.assertEqual(browser.post('/api/' + route, json={'id': '1', 'enabled': True}, headers=headers).status_code, 401)
        headers['X-CSRF-Token'] = browser.post('/api/login', json={'password': 'a-test-password-long-enough'}, headers=headers).json['csrf']
        for route in ('renew', 'auto-renew'):
            self.assertEqual(browser.post('/api/' + route, json={'id': '1', 'enabled': True}).status_code, 403)
            for invalid in ([], {}, {'id': 1, 'enabled': True}):
                self.assertEqual(browser.post('/api/' + route, json=invalid, headers=headers).status_code, 400)
        self.due(enable=False)
        self.assertEqual(browser.post('/api/auto-renew', json={'id': '1', 'enabled': True}, headers=headers).status_code, 200)
        browser.post('/api/logout', json={}, headers=headers)
        self.service.tick()
        self.client.renew_reservation.assert_called_once()


class RenewalCloudTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch('seat_service.time.time', return_value=NOON).start()
        self.addCleanup(patch.stopall)
        self.root = CloudService(FakeCloudStore(), Fernet.generate_key())
        self.clients = {'alice': DemoClient(), 'bob': DemoClient()}
        patch('cloud_service.LibraryClient', side_effect=lambda token, cookies=None: self.clients[token]).start()
        patch('cloud_service.login_to_library', side_effect=lambda user, password: {'token': user, 'cookies': {}, 'identity': user}).start()
        patch.dict('os.environ', {'LIBRARY_NFC_TAGS': json.dumps({'102': TAG})}).start()
        self.clouds = {}
        for name in self.clients:
            client = self.clients[name]
            client.reserve(102003)
            client.confirm_reservation('1')
            client.current.update(endTime=end_at(NOON + 7140), renewableAt=NOON - 60)
            self.clouds[name] = self.root.for_account(self.root.login(name, 'test-password', remember=True))
            client.renew_reservation = Mock(wraps=client.renew_reservation)

    def test_account_isolation_scheduler_and_server_jobs_after_logout(self):
        alice, bob = self.clouds['alice'], self.clouds['bob']
        bob.set_auto_renew(False, '1')
        alice.set_auto_renew(True, '1')
        self.assertLessEqual(alice.store.read()['nextPollAt'], NOON + 1)
        self.assertIsNone(bob.snapshot()['autoRenew'])
        seats = self.clients['alice'].seats = Mock(wraps=self.clients['alice'].seats)
        alice.tick()
        self.clients['alice'].renew_reservation.assert_called_once()
        self.clients['bob'].renew_reservation.assert_not_called()
        seats.assert_not_called()  # A renewal poll need not reload all six room catalogs.
        self.assertEqual(alice.snapshot()['reservation']['renewableCnt'], 2)
        self.assertEqual(alice.store.read()['nextPollAt'], NOON + 1)  # Deliver the queued renewal result first.
        alice.tick()
        self.assertEqual(alice.store.read()['nextPollAt'], NOON + 30)
        self.assertNotIn(TAG, json.dumps(alice.store.read()))

    def test_default_and_opt_out_persist_across_devices_and_login(self):
        alice, bob = self.clouds['alice'], self.clouds['bob']
        self.assertEqual(alice.snapshot()['autoRenew']['reservationId'], '1')
        alice.set_auto_renew(False, '1')
        self.root.login('alice', 'test-password', remember=True)
        second_device = self.root.for_account(alice.store.account)
        second_device.tick()
        self.assertIsNone(second_device.snapshot()['autoRenew'])
        self.assertEqual(second_device.snapshot()['autoRenewDisabledId'], '1')
        self.assertEqual(bob.snapshot()['autoRenew']['reservationId'], '1')
        self.clients['alice'].renew_reservation.assert_not_called()
        self.clients['bob'].renew_reservation.assert_not_called()

    def test_login_never_performs_due_renewal_and_cloud_lock_serializes_actions(self):
        alice = self.clouds['alice']
        alice.set_auto_renew(True, '1')
        self.root.login('alice', 'test-password', remember=True)
        self.clients['alice'].renew_reservation.assert_not_called()
        self.assertTrue(alice.store.claim('held'))
        with self.assertRaises(LibraryError):
            alice.renew('1')
        self.clients['alice'].renew_reservation.assert_not_called()
        alice.store.release('held')
        alice.tick()
        self.clients['alice'].renew_reservation.assert_called_once()
