import copy
import json
import re
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
    def test_all_six_rooms_and_non_single_seats_are_available(self):
        from seat_service import ROOMS, SINGLE_SEATS
        self.service.tick()
        seats = self.service.snapshot()['seats']
        self.assertEqual({seat['roomId'] for seat in seats}, set(ROOMS))
        self.assertEqual({(s['roomId'], s['number']) for s in seats if s['single']}, SINGLE_SEATS)
        self.assertTrue(any(s['key'] == '232:12' and not s['single'] for s in seats))
        for room in ROOMS:
            with self.subTest(room=room):
                self.client.current = None
                self.service.reserve(f'{room}:12')
                self.assertEqual(self.service.snapshot()['reservation']['roomName'], ROOMS[room])

    def test_missing_seat_cannot_be_registered_for_wait(self):
        with self.assertRaises(LibraryError):
            self.service.set_wait(['232:99999'], True)
        self.assertFalse(self.service.snapshot()['running'])

    def test_fast_polling_exact_boundary_and_unknown_remaining_time(self):
        self.service.set_wait(['232:1'], True)
        for remaining, expected in [(0,1), (0.5,1), (1,1), ('1',1), (1.01,30), (2,30),
                                    (-1,30), (None,30), ('',30), (' ',30), ('bad',30), (True,30), (float('nan'),30)]:
            with self.subTest(remaining=remaining):
                self.client.seats = Mock(return_value=[{'id':232001,'code':'1','isOccupied':True,'remainingTime':remaining}])
                before = time.time()
                self.service.tick(targets_only=True)
                state = self.service.snapshot()
                self.assertEqual(state['interval'], expected)
                self.assertGreaterEqual(state['nextCheck'], before + expected)
        self.client.reservation = Mock(side_effect=LibraryError('rate limited'))
        self.service.tick(targets_only=True)
        self.assertEqual(self.service.snapshot()['interval'], 30)

    def test_fast_polling_only_selected_rooms_preserves_other_room_cache(self):
        self.service.tick()
        other = next(s for s in self.service.snapshot()['seats'] if s['key'] == '107:1')
        self.service.set_wait(['232:1'], True)
        self.client.seats = Mock(return_value=[{'id':232001,'code':'1','isOccupied':True,'remainingTime':0}])
        self.service.tick(targets_only=True)
        self.client.seats.assert_called_once_with(232)
        self.assertEqual(next(s for s in self.service.snapshot()['seats'] if s['key'] == '107:1'), other)
        self.assertEqual(self.service.snapshot()['interval'], 1)
        self.service.set_wait(['232:1'], False)
        self.assertEqual(self.service.snapshot()['interval'], 30)

    def test_unselected_urgent_seat_does_not_enable_fast_polling(self):
        self.service.set_wait(['232:2'], True)
        self.client.seats = Mock(return_value=[{'id':232001,'code':'1','isOccupied':True,'remainingTime':0},
                                               {'id':232002,'code':'2','isOccupied':True,'remainingTime':10}])
        self.service.tick(targets_only=True)
        self.assertEqual(self.service.snapshot()['interval'], 30)

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

    def test_existing_reservation_is_kept_while_target_is_occupied(self):
        self.client.reserve(101021)
        self.service.set_wait(['102:1'], True)
        self.client.release = Mock()
        self.service.tick()
        self.client.release.assert_not_called()
        self.assertTrue(self.service.snapshot()['running'])
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

    def test_explicit_provider_no_record_is_an_empty_reservation(self):
        client = LibraryClient('test-token')
        response = Mock(status_code=200)
        response.json.return_value = {'success': True, 'code': 'success.noRecord', 'data': None}
        client.session.request = Mock(return_value=response)
        self.assertIsNone(client.reservation())

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


class SwitchTests(ServiceFixture):
    def wait_with_seat(self, state='TEMP_CHARGE'):
        self.client.reserve(101021)
        self.client.current['state'] = state
        self.service.set_wait(['102:3', '232:3'], True)

    def test_switches_temporary_and_confirmed_seats_and_stops_other_targets(self):
        for state in ('TEMP_CHARGE', 'CHARGE', 'IN_USE'):
            with self.subTest(state=state):
                self.wait_with_seat(state)
                self.client.release = Mock(wraps=self.client.release)
                self.service.tick()
                self.client.release.assert_called_once()
                result = self.service.snapshot()
                self.assertEqual(result['reservation']['seatId'], 102003)
                self.assertFalse(result['running'])
                self.assertEqual(result['targets'], [])
                self.assertEqual(result['repeat']['reservationId'], result['reservation']['id'])
                self.client.release = self.client.release._mock_wraps

    def test_definite_rejection_recovers_original_and_resumes_wait(self):
        self.wait_with_seat('CHARGE')
        reserve = self.client.reserve
        def reject_target(seat):
            if seat == 102003:
                raise LibraryError('occupied')
            reserve(seat)
        self.client.reserve = Mock(side_effect=reject_target)
        self.service.tick()
        self.assertEqual([call.args[0] for call in self.client.reserve.call_args_list], [102003, 101021])
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 101021)
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertTrue(state['running'])
        self.assertEqual(state['targets'], ['102:3', '232:3'])
        self.assertIsNotNone(state['repeat'])
        self.assertIn('원래 좌석을 다시 예약', state['message'])

    def test_uncertain_or_expired_target_request_never_attempts_recovery(self):
        for options in ({'uncertain': True}, {'expired': True}):
            with self.subTest(options=options):
                self.service.client = self.client
                self.wait_with_seat()
                self.client.reserve = Mock(side_effect=LibraryError('failed', **options))
                self.service.tick()
                self.client.reserve.assert_called_once_with(102003)
                self.assertFalse(self.store.load()['running'])
                self.assertIsNone(self.store.load()['repeat'])
                self.client = DemoClient()

    def test_release_failure_or_unverified_release_stops_without_booking(self):
        for fails in (False, True):
            with self.subTest(fails=fails):
                self.wait_with_seat()
                self.client.release = Mock(side_effect=LibraryError('release failed') if fails else None)
                self.client.reserve = Mock()
                self.service.tick()
                self.service.tick()
                self.client.release.assert_called_once()
                self.client.reserve.assert_not_called()
                self.assertIsNone(self.store.load()['repeat'])
                self.client = DemoClient()
                self.service.client = self.client

    def test_successful_write_with_failed_verification_never_recovers_or_replays(self):
        self.wait_with_seat()
        original = self.client.reservation()
        self.client.reservation = Mock(side_effect=[original, original, None, LibraryError('read failed')])
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.service.tick()
        self.client.reservation = Mock(side_effect=lambda: copy.deepcopy(self.client.current))
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.client.reserve.assert_called_once_with(102003)
        self.assertFalse(restarted.snapshot()['running'])
        self.assertIsNone(restarted.snapshot()['repeat'])

    def test_recovery_failure_stops_wait_and_reports_lost_seat(self):
        self.wait_with_seat()
        self.client.reserve = Mock(side_effect=LibraryError('occupied'))
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.client.reserve.call_count, 2)
        self.assertIsNone(self.service.snapshot()['reservation'])
        self.assertFalse(self.service.snapshot()['running'])
        self.assertIn('원래 좌석 재예약에 실패', self.service.snapshot()['message'])

    def test_same_seat_is_not_cancelled_and_unknown_source_is_rejected(self):
        self.client.reserve(102003)
        self.service.set_wait(['102:3'], True)
        self.client.release = Mock()
        self.service.tick()
        self.client.release.assert_not_called()
        self.client.current['state'] = 'UNKNOWN'
        with self.assertRaises(LibraryError):
            self.service.set_wait(['232:3'], True)
        self.client.release.assert_not_called()

    def test_immediate_booking_switches_and_can_restore_original(self):
        self.client.reserve(101021)
        self.service.reserve('232:3')
        self.assertEqual(self.service.snapshot()['reservation']['seatId'], 232003)
        reserve = self.client.reserve
        def reject_target(seat):
            if seat == 102003:
                raise LibraryError('occupied')
            reserve(seat)
        self.client.reserve = Mock(side_effect=reject_target)
        self.service.reserve('102:3')
        self.assertEqual(self.service.snapshot()['reservation']['seatId'], 232003)
        self.assertFalse(self.service.snapshot()['running'])

    def test_live_wait_additions_keep_order_and_held_seat_without_duplicate_targets(self):
        self.client.reserve(101021)
        self.service.tick()
        repeat = self.service.snapshot()['repeat']
        self.service.set_wait(['232:1'], True)
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.client.release = Mock(wraps=self.client.release)
        self.service.update_wait('107:2', True)
        self.service.update_wait('107:2', True)
        state = self.service.snapshot()
        self.assertEqual(state['targets'], ['232:1', '107:2'])
        self.assertTrue(state['running'])
        self.assertEqual(state['reservation']['seatId'], 101021)
        self.assertEqual(state['repeat'], repeat)
        self.assertEqual(self.store.load()['targets'], state['targets'])
        self.client.reserve.assert_not_called()
        self.client.release.assert_not_called()

    def test_live_wait_removal_works_without_provider_and_last_removal_stops(self):
        self.service.set_wait(['232:1', '107:2'], True)
        self.client.reservation = Mock(side_effect=AssertionError('unexpected provider read'))
        self.client.seats = Mock(side_effect=AssertionError('unexpected provider read'))
        self.service.update_wait('232:1', False)
        self.assertEqual(self.service.snapshot()['targets'], ['107:2'])
        self.assertTrue(self.service.snapshot()['running'])
        self.service.update_wait('107:2', False)
        self.assertEqual(self.store.load()['targets'], [])
        self.assertFalse(self.store.load()['running'])

    def test_live_edit_after_booking_never_restarts_wait_or_releases_assignment(self):
        self.service.set_wait(['102:3'], True)
        self.service.tick()
        held = self.service.snapshot()['reservation']
        self.client.reserve = Mock()
        self.client.release = Mock()
        for enabled in (True, False):
            with self.assertRaisesRegex(LibraryError, '이미 종료'):
                self.service.update_wait('232:1', enabled)
        self.assertFalse(self.service.snapshot()['running'])
        self.assertEqual(self.service.snapshot()['reservation'], held)
        self.client.reserve.assert_not_called()
        self.client.release.assert_not_called()

    def test_live_addition_validates_seat_and_limit_without_losing_targets(self):
        targets = [f'232:{number}' for number in range(1, 51)]
        self.service.set_wait(targets, True)
        with self.assertRaisesRegex(LibraryError, '50개'):
            self.service.update_wait('232:51', True)
        self.assertEqual(self.service.snapshot()['targets'], targets)
        self.service.update_wait('232:1', False)
        with self.assertRaisesRegex(LibraryError, '좌석 목록'):
            self.service.update_wait('232:99999', True)
        self.assertEqual(self.service.snapshot()['targets'], targets[1:])

    def test_crash_after_disarm_does_not_repeat_release(self):
        self.wait_with_seat()
        def crash(reservation):
            self.assertFalse(self.store.load()['running'])
            self.assertIsNone(self.store.load()['repeat'])
            raise RuntimeError('crashed')
        self.client.release = Mock(side_effect=crash)
        with self.assertRaises(RuntimeError):
            self.service.tick()
        SeatService(self.store, client=self.client).tick()
        self.client.release.assert_called_once()


class RepeatTests(ServiceFixture):
    def enable(self, elapsed=0):
        self.client.reserve(102003)
        self.client.current['startedAt'] = time.time() - elapsed
        self.service.set_repeat(True, self.client.current['id'])

    def test_repeat_runs_at_nine_minutes(self):
        self.enable(539)
        self.client.release = Mock(wraps=self.client.release)
        self.service.tick()
        self.client.release.assert_not_called()
        self.service.state['repeat']['dueAt'] = time.time() - 1
        self.service.tick()
        self.client.release.assert_called_once()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['id'], '2')
        self.assertEqual(state['reservation']['seatId'], 102003)
        self.assertEqual(state['repeat']['reservationId'], '2')
        self.assertAlmostEqual(state['repeat']['dueAt'], time.time() + 540, delta=1)
        self.service.tick()
        self.assertEqual(self.client.release.call_count, 1)

    def test_booking_and_existing_temporary_seat_auto_enable_repeat(self):
        self.service.reserve('102:3')
        self.assertEqual(self.service.snapshot()['repeat']['reservationId'], '1')
        self.assertAlmostEqual(self.store.load()['repeat']['dueAt'], time.time() + 540, delta=1)
        self.client.reserve(232003)
        # A new externally observed seat without an old repeat plan also auto starts.
        self.service.state['repeat'] = None
        self.service.tick()
        self.assertEqual(self.service.snapshot()['repeat']['reservationId'], '2')

    def test_manual_disable_survives_poll_and_restart_until_new_booking(self):
        self.service.reserve('102:3')
        self.service.set_repeat(False, '1')
        self.service.tick()
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.assertIsNone(restarted.snapshot()['repeat'])
        restarted.reserve('232:3')
        self.assertIsNotNone(restarted.snapshot()['repeat'])

    def test_repeat_cycle_keeps_switch_wait_targets(self):
        self.enable(541)
        self.service.set_wait(['232:1'], True)
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['id'], '2')
        self.assertTrue(state['running'])
        self.assertEqual(state['targets'], ['232:1'])

    def test_repeat_stops_on_confirmed_changed_or_expired_reservation(self):
        for change in ({'state':'CHARGE'}, {'state':'IN_USE'}, {'id':'another'}, {'seatId':102006}, {'roomId':101}):
            with self.subTest(change=change):
                self.enable(541)
                self.client.current.update(change)
                self.client.release = Mock()
                self.service.tick()
                self.client.release.assert_not_called()
                self.assertIsNone(self.store.load()['repeat'])
        self.enable(541)
        self.service.state['repeat']['expiresAt'] = time.time() - 1
        self.service.tick()
        self.client.release.assert_not_called()
        self.assertIsNone(self.service.snapshot()['repeat'])

    def test_nfc_confirmation_at_write_boundary_is_not_cancelled(self):
        self.enable(541)
        temporary = self.client.reservation()
        confirmed = dict(temporary, state='CHARGE')
        self.client.reservation = Mock(side_effect=[temporary, confirmed])
        self.client.release = Mock()
        self.service.tick()
        self.client.release.assert_not_called()
        self.assertIsNone(self.service.snapshot()['repeat'])
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'CHARGE')

    def test_cancel_failures_never_reserve_or_replay(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain):
                self.client = DemoClient()
                self.service.client = self.client
                self.enable(541)
                self.client.release = Mock(side_effect=LibraryError('cancel failed', uncertain=uncertain))
                self.client.reserve = Mock()
                self.service.tick()
                self.service.tick()
                self.client.release.assert_called_once()
                self.client.reserve.assert_not_called()
                self.assertIsNone(self.store.load()['repeat'])

    def test_competition_after_cancel_stops_repeat(self):
        self.enable(541)
        self.client.reserve = Mock(side_effect=LibraryError('seat occupied'))
        self.service.tick()
        self.service.tick()
        self.client.reserve.assert_called_once_with(102003)
        self.assertIsNone(self.service.snapshot()['reservation'])
        self.assertIsNone(self.store.load()['repeat'])

    def test_cancel_not_reflected_or_verification_read_failure_never_rebooks(self):
        for failed_read in (False, True):
            with self.subTest(failed_read=failed_read):
                self.client = DemoClient()
                self.service.client = self.client
                self.enable(541)
                old = self.client.reservation()
                self.client.release = Mock()
                self.client.reserve = Mock()
                self.client.reservation = Mock(side_effect=[old, old, LibraryError('read failed') if failed_read else old])
                self.service.tick()
                self.client.reserve.assert_not_called()
                self.assertIsNone(self.store.load()['repeat'])

    def test_final_verification_failure_never_repeats_write(self):
        self.enable(541)
        old = self.client.reservation()
        self.client.release = Mock(wraps=self.client.release)
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.client.reservation = Mock(side_effect=[old, old, None, LibraryError('read failed')])
        self.service.tick()
        self.assertIsNone(self.store.load()['repeat'])
        self.client.reservation = Mock(return_value=self.client.current)
        self.service.tick()
        self.client.release.assert_called_once()
        self.client.reserve.assert_called_once()

    def test_disarm_survives_crash_before_cancel_completes(self):
        self.enable(541)
        def crash(reservation):
            self.assertIsNone(self.store.load()['repeat'])
            raise RuntimeError('process crashed')
        self.client.release = Mock(side_effect=crash)
        with self.assertRaises(RuntimeError):
            self.service.tick()
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.client.release.assert_called_once()

    def test_ambiguous_rebook_stops_even_if_write_succeeded(self):
        self.enable(541)
        reserve = self.client.reserve
        def ambiguous(seat):
            reserve(seat)
            raise LibraryError('timeout', uncertain=True)
        self.client.reserve = Mock(side_effect=ambiguous)
        self.service.tick()
        self.service.tick()
        self.client.reserve.assert_called_once()
        self.assertEqual(self.service.snapshot()['reservation']['id'], '2')
        self.assertIsNone(self.store.load()['repeat'])

    def test_disable_keeps_current_seat_and_invalid_metadata_is_rejected(self):
        self.enable()
        self.client.release = Mock()
        self.service.set_repeat(False, '1')
        self.client.release.assert_not_called()
        self.assertIsNotNone(self.client.reservation())
        self.client.current['startedAt'] = None
        self.client.current['id'] = 'external'
        with self.assertRaises(LibraryError):
            self.service.set_repeat(True, 'external')
        self.assertIsNone(self.store.load()['repeat'])

    def test_provider_metadata_and_korean_naive_time(self):
        base = {'id':7, 'state':{'code':'TEMP_CHARGE'}, 'seat':{'id':102003,'code':'3','room':{'id':102,'name':'Room'}}, 'startTime':'2026-10-07T10:00:00'}
        item = normalize_reservation(base)
        self.assertEqual(item['seatId'], 102003)
        self.assertEqual(item['roomId'], 102)
        self.assertEqual(item['startedAt'], normalize_reservation(dict(base, startTime='2026-10-07T01:00:00Z'))['startedAt'])
        base.pop('startTime')
        # A normal seat end time is not necessarily the temporary NFC deadline.
        self.assertIsNone(normalize_reservation(dict(base, endTime='2026-10-07T14:00:00'))['startedAt'])

    def test_app_booking_time_is_retained_when_provider_omits_start_time(self):
        reserve = self.client.reserve
        def no_timestamp(seat):
            reserve(seat)
            self.client.current['startedAt'] = None
        self.client.reserve = no_timestamp
        self.service.reserve('102:3')
        self.service.tick()
        self.service.set_repeat(True, '1')
        self.assertAlmostEqual(self.service.snapshot()['repeat']['dueAt'], time.time() + 540, delta=1)


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

    def test_live_wait_endpoint_requires_login_csrf_and_valid_input(self):
        route = '/api/wait/seat'
        body = {'key': '232:2', 'enabled': True}
        self.assertEqual(self.browser.post(route, json=body, headers={'X-CSRF-Token': self.csrf}).status_code, 401)
        self.login()
        self.service.set_wait(['232:1'], True)
        self.assertEqual(self.browser.post(route, json=body).status_code, 403)
        headers = {'X-CSRF-Token': self.csrf}
        for invalid in ([], {}, {'key': '232:2', 'enabled': 'true'}, {'key': '999:1', 'enabled': True}):
            self.assertIn(self.browser.post(route, json=invalid, headers=headers).status_code, (400, 409))
        self.assertEqual(self.browser.post(route, json=body, headers=headers).status_code, 200)
        self.assertEqual(self.service.snapshot()['targets'], ['232:1', '232:2'])

    def test_bad_input_and_no_credential_leak(self):
        self.login()
        for body in ([], {'targets':['999:1'], 'running':True}, {'targets':['102:3'], 'running':'true'}):
            result = self.browser.post('/api/wait', json=body, headers={'X-CSRF-Token':self.csrf})
            self.assertIn(result.status_code, (400,409))
        response = self.browser.get('/api/state')
        self.assertNotIn('token', response.text)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_static_shell_and_cloud_cron_protection(self):
        with self.browser.get('/') as response:
            shell = response.text
        self.assertIn('id="root"', shell)
        self.assertIn('type="module"', shell)
        assets = re.findall(r'(?:src|href)="(/assets/[^"?]+\.(?:js|css))"', shell)
        self.assertEqual(len(assets), 2)
        for path in ('/', *assets, '/assets/manifest.webmanifest', '/sw.js'):
            with self.browser.get(path) as response:
                self.assertEqual(response.status_code, 200, path)
        self.assertEqual(self.browser.post('/api/cron', json={}).status_code, 401)

    def test_repeat_requires_login_csrf_and_explicit_boolean(self):
        self.assertEqual(self.browser.post('/api/repeat', json={'enabled':True,'id':'1'}, headers={'X-CSRF-Token':self.csrf}).status_code, 401)
        self.login()
        self.assertEqual(self.browser.post('/api/repeat', json={'enabled':True,'id':'1'}).status_code, 403)
        headers = {'X-CSRF-Token':self.csrf}
        self.client.reserve(102003)
        self.assertEqual(self.browser.post('/api/repeat', json={'enabled':'true','id':'1'}, headers=headers).status_code, 400)
        self.assertEqual(self.browser.post('/api/repeat', json={'enabled':True,'id':'1'}, headers=headers).status_code, 200)
        self.assertTrue(self.browser.get('/api/state').json['repeat'])


class FakeCloudStore:
    def __init__(self, accounts=None, account=None, locks=None, devices=None):
        self.accounts = {} if accounts is None else accounts
        self.account = account
        self.locks = {} if locks is None else locks
        self.devices = {} if devices is None else devices

    def for_account(self, account):
        return FakeCloudStore(self.accounts, account, self.locks, self.devices)

    def push_devices(self):
        return [copy.deepcopy(device) for device in self.devices.values() if device['account'] == self.account]

    def put_push(self, subscription, preferences):
        self.devices[subscription['id']] = {'id': subscription['id'], 'subscription': subscription['encrypted'],
                                          'preferences': preferences, 'account': self.account}
        return True

    def delete_push(self, identifier):
        if self.devices.get(identifier, {}).get('account') == self.account:
            self.devices.pop(identifier)

    def ensure(self):
        self.accounts.setdefault(self.account, {})

    def read(self):
        return copy.deepcopy(self.accounts[self.account])

    def claim(self, owner):
        if self.locks.get(self.account):
            return False
        self.locks[self.account] = owner
        return True

    def save(self, owner, document):
        if self.locks.get(self.account) != owner:
            raise LibraryError('lease lost')
        self.accounts[self.account] = copy.deepcopy(document)

    def release(self, owner):
        if self.locks.get(self.account) == owner:
            self.locks.pop(self.account)

    def _request(self, *args, **kwargs):
        return True  # Rate limiter permits these test calls.


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeCloudStore()
        self.key = Fernet.generate_key()
        self.root = CloudService(self.store, self.key)
        self.clients = {'alice': DemoClient(), 'bob': DemoClient()}
        client_patch = patch('cloud_service.LibraryClient', side_effect=lambda token, cookies=None: self.clients[token])
        client_patch.start()
        self.addCleanup(client_patch.stop)
        login_patch = patch('cloud_service.login_to_library', side_effect=lambda username, password: {'token': username, 'cookies': {'session': 'test-cookie'}, 'identity': username})
        self.authenticate = login_patch.start()
        self.addCleanup(login_patch.stop)

    def login(self, username='alice', remember=True):
        account = self.root.login(username, 'valid-password', remember=remember)
        return self.root.for_account(account)

    def test_encrypted_login_and_state_survive_new_instance(self):
        cloud = self.login()
        raw = json.dumps(cloud.store.read())
        self.assertNotIn('valid-password', raw)
        self.assertNotIn('test-cookie', raw)
        cloud.set_wait(['102:3'], True)
        other = CloudService(cloud.store, self.key)
        other.tick()
        self.assertFalse(other.snapshot()['running'])
        self.assertEqual(other.snapshot()['reservation']['seatNo'], '3')
        self.assertNotIn('credential', other.snapshot())
        self.assertNotIn('login', other.snapshot())

    def test_cloud_filters_old_catalogs_and_targets_before_next_refresh(self):
        cloud = self.login()
        document = cloud.store.accounts[cloud.store.account]
        document['state'].update(seats=[{'key': '101:409'}, {'key': '234:149'}, {'key': '101:408'}],
                                 targets=['101:409', '234:149'], running=True)
        state = cloud.snapshot()
        self.assertEqual(state['seats'], [{'key': '101:408'}])
        self.assertEqual(state['targets'], [])
        self.assertFalse(state['running'])
        client = self.clients['alice']
        client.reserve = Mock()
        cloud.tick()
        self.assertFalse(cloud.store.read()['state']['running'])
        self.assertEqual(cloud.store.read()['state']['targets'], [])
        client.reserve.assert_not_called()

    def test_confirmation_is_leased_account_scoped_and_keeps_tags_private(self):
        tag = '0123456789ABCDEF'
        with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': json.dumps({'102': tag})}):
            alice, bob = self.login(), self.login('bob')
            alice.reserve('102:3')
            bob.reserve('102:3')
            identifier = alice.snapshot()['reservation']['id']
            self.assertTrue(alice.store.claim('other-operation'))
            with self.assertRaises(LibraryError):
                alice.confirm(identifier)
            self.assertEqual(self.clients['alice'].current['state'], 'TEMP_CHARGE')
            alice.store.release('other-operation')
            alice.confirm(identifier)
            state = CloudService(alice.store, self.key).snapshot()
            self.assertEqual(state['reservation']['state'], 'CHARGE')
            self.assertIsNone(state['repeat'])
            self.assertEqual(self.clients['bob'].current['state'], 'TEMP_CHARGE')
            self.assertEqual(state['confirmationRooms'], [102])
            self.assertNotIn(tag, json.dumps(alice.store.read()))
        with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': '{}'}):
            self.assertEqual(alice.snapshot()['confirmationRooms'], [])

    def test_live_wait_edits_merge_latest_cloud_state_and_stay_account_scoped(self):
        alice, bob = self.login(), self.login('bob')
        alice.set_wait(['232:1'], True)
        bob.set_wait(['107:1'], True)
        other = CloudService(alice.store, self.key)
        other.update_wait('102:2', True)
        alice.update_wait('232:2', True)
        self.assertEqual(other.snapshot()['targets'], ['232:1', '102:2', '232:2'])
        self.assertEqual(bob.snapshot()['targets'], ['107:1'])
        other.update_wait('232:1', False)
        self.assertEqual(alice.snapshot()['targets'], ['102:2', '232:2'])
        self.assertTrue(alice.snapshot()['running'])

    def test_reassignment_uses_account_lease_for_entire_flow(self):
        with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': json.dumps({'102': '0123456789ABCDEF'})}):
            alice, bob = self.login(), self.login('bob')
            for cloud in (alice, bob):
                cloud.reserve('102:3')
                cloud.confirm(cloud.snapshot()['reservation']['id'])
            identifier = alice.snapshot()['reservation']['id']
            bob_before = copy.deepcopy(self.clients['bob'].current)
            self.assertTrue(alice.store.claim('other-operation'))
            with self.assertRaises(LibraryError):
                alice.reassign(identifier)
            alice.store.release('other-operation')
            release = self.clients['alice'].release
            def guarded_release(reservation):
                with self.assertRaises(LibraryError):
                    alice.reassign(identifier)
                self.assertFalse(alice.store.read()['state']['running'])
                self.assertTrue(alice.store.read()['state']['repeatControl']['paused'])
                release(reservation)
            self.clients['alice'].release = guarded_release
            alice.reassign(identifier)
            state = CloudService(alice.store, self.key).snapshot()
            self.assertEqual(state['reservation']['state'], 'CHARGE')
            self.assertNotEqual(state['reservation']['id'], identifier)
            self.assertIsNone(state['repeat'])
            self.assertEqual(self.clients['bob'].current, bob_before)

    def test_cloud_live_edit_cannot_restart_a_completed_job(self):
        cloud = self.login()
        cloud.set_wait(['102:3'], True)
        cloud.tick()
        other = CloudService(cloud.store, self.key)
        with self.assertRaisesRegex(LibraryError, '이미 종료'):
            other.update_wait('232:1', True)
        self.assertFalse(cloud.snapshot()['running'])
        self.assertEqual(cloud.snapshot()['reservation']['seatId'], 102003)

    def test_switch_wait_and_manual_repeat_off_survive_second_device_login(self):
        alice = self.login()
        client = self.clients['alice']
        client.reserve(101021)
        alice.refresh()
        self.assertEqual(alice.snapshot()['repeat']['reservationId'], '1')
        alice.set_repeat(False, '1')
        alice.set_wait(['102:1'], True)
        client.release = Mock(wraps=client.release)
        client.reserve = Mock(wraps=client.reserve)
        other = self.login()
        self.assertTrue(other.snapshot()['running'])
        self.assertEqual(other.snapshot()['targets'], ['102:1'])
        self.assertIsNone(other.snapshot()['repeat'])
        other.tick()
        client.release.assert_not_called()
        client.reserve.assert_not_called()
        self.assertEqual(other.snapshot()['reservation']['seatId'], 101021)

    def test_switch_disarms_durably_and_isolated_accounts_keep_their_seats(self):
        alice, bob = self.login(), self.login('bob')
        client = self.clients['alice']
        client.reserve(101021)
        self.clients['bob'].reserve(107003)
        bob.refresh()
        alice.set_wait(['102:3'], True)
        release = client.release
        def cancel(reservation):
            saved = alice.store.read()['state']
            self.assertFalse(saved['running'])
            self.assertIsNone(saved['repeat'])
            self.assertTrue(saved['repeatControl']['paused'])
            release(reservation)
        client.release = Mock(side_effect=cancel)
        alice.tick()
        state = CloudService(alice.store, self.key).snapshot()
        self.assertEqual(state['reservation']['seatId'], 102003)
        self.assertFalse(state['running'])
        self.assertIsNotNone(state['repeat'])
        self.assertEqual(bob.snapshot()['reservation']['seatId'], 107003)

    def test_cloud_switch_recovery_persists_original_seat_and_wait(self):
        alice = self.login()
        client = self.clients['alice']
        client.reserve(101021)
        alice.set_wait(['102:3'], True)
        reserve = client.reserve
        def reject(seat):
            if seat == 102003:
                raise LibraryError('occupied')
            reserve(seat)
        client.reserve = Mock(side_effect=reject)
        alice.tick()
        other = self.login()
        self.assertTrue(other.snapshot()['running'])
        self.assertEqual(other.snapshot()['reservation']['seatId'], 101021)
        self.assertIsNotNone(other.snapshot()['repeat'])
        self.assertEqual(client.reserve.call_count, 2)

    def test_cloud_switch_and_recovery_confirmation_persist_under_account_lease(self):
        with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': json.dumps({'102': '0123456789ABCDEF'})}):
            alice, bob = self.login(), self.login('bob')
            client = self.clients['alice']
            client.reserve(102006)
            client.confirm_reservation(client.current['id'])
            alice.set_wait(['102:3', '232:3'], True)
            reserve = client.reserve
            def reject_target(seat_id):
                if seat_id == 102003:
                    raise LibraryError('occupied')
                reserve(seat_id)
            client.reserve = Mock(side_effect=reject_target)
            arrival = client.check_arrival
            def guarded_arrival(room_id, tag):
                saved = alice.store.read()['state']
                self.assertFalse(saved['running'])
                self.assertIsNone(saved['repeat'])
                self.assertTrue(saved['repeatControl']['paused'])
                with self.assertRaises(LibraryError):
                    alice.tick()
                arrival(room_id, tag)
            client.check_arrival = Mock(side_effect=guarded_arrival)
            alice.tick()
            persisted = CloudService(alice.store, self.key).snapshot()
            self.assertTrue(persisted['running'])
            self.assertEqual(persisted['reservation']['seatId'], 102006)
            self.assertEqual(persisted['reservation']['state'], 'CHARGE')
            self.assertIsNone(persisted['repeat'])
            self.assertIsNone(bob.snapshot()['reservation'])
            client.reserve = Mock(wraps=reserve)
            alice.tick()
            persisted = CloudService(alice.store, self.key).snapshot()
            self.assertFalse(persisted['running'])
            self.assertEqual(persisted['reservation']['seatId'], 102003)
            self.assertEqual(persisted['reservation']['state'], 'CHARGE')
            self.assertIsNone(persisted['repeat'])
            self.assertEqual(client.check_arrival.call_count, 2)

    def test_existing_temporary_seat_login_only_arms_repeat_without_cancelling(self):
        client = self.clients['alice']
        client.reserve(101021)
        client.current['startedAt'] = time.time() - 541
        client.release = Mock(wraps=client.release)
        alice = self.login()
        self.assertIsNotNone(alice.snapshot()['repeat'])
        client.release.assert_not_called()
        alice.tick()
        client.release.assert_called_once()

    def test_repeat_persists_isolated_and_disarms_before_cancel(self):
        alice, bob = self.login(), self.login('bob')
        client = self.clients['alice']
        client.reserve(102003)
        client.current['startedAt'] = time.time() - 541
        alice.set_repeat(True, '1')
        self.assertIsNone(bob.snapshot()['repeat'])
        self.assertLess(alice.store.read()['nextPollAt'], time.time() + 2)
        release = client.release
        def cancel(reservation):
            self.assertIsNone(alice.store.read()['state']['repeat'])
            self.assertFalse(alice.store.read()['state']['running'])
            release(reservation)
        client.release = Mock(side_effect=cancel)
        # A login on another device must not execute a due cancellation.
        self.login()
        client.release.assert_not_called()
        other = CloudService(alice.store, self.key)
        other.tick()
        client.release.assert_called_once()
        self.assertEqual(other.snapshot()['repeat']['reservationId'], '2')
        self.assertIsNone(bob.snapshot()['reservation'])

    def test_repeat_api_cannot_target_another_account(self):
        alice, bob = self.login(), self.login('bob')
        self.clients['alice'].reserve(102003)
        self.clients['bob'].reserve(102006)
        app = create_app(self.root, 'test-admin-password-long-enough', secret='test-only', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        response = browser.post('/api/library-login', json={'username':'alice','password':'valid-password','remember':True}, headers={'X-CSRF-Token':csrf})
        csrf = response.json['csrf']
        response = browser.post('/api/repeat', json={'enabled':True,'id':'1','account':bob.store.account}, headers={'X-CSRF-Token':csrf})
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(alice.snapshot()['repeat'])
        self.assertIsNone(bob.snapshot()['repeat'])
        browser.post('/api/logout', json={}, headers={'X-CSRF-Token':csrf})
        self.assertIsNotNone(alice.snapshot()['repeat'])

    def test_known_booking_time_survives_login_and_cloud_reconstruction(self):
        cloud = self.login()
        client = self.clients['alice']
        original = client.reserve
        def reserve_without_time(seat):
            original(seat)
            client.current['startedAt'] = None
        client.reserve = reserve_without_time
        cloud.reserve('102:3')
        cloud = self.login()
        cloud.set_repeat(True, '1')
        self.assertAlmostEqual(cloud.snapshot()['repeat']['dueAt'], time.time() + 540, delta=1)

    def test_cloud_dispatch_deadline_tracks_fast_wait_stop_and_idle(self):
        cloud = self.login()
        self.assertGreaterEqual(cloud.store.read()['nextPollAt'], time.time() + 290)
        cloud.set_wait(['232:1'], True)
        self.assertLess(cloud.store.read()['nextPollAt'], time.time() + 2)
        self.clients['alice'].seats = Mock(return_value=[{'id':232001,'code':'1','isOccupied':True,'remainingTime':1}])
        started = time.time()
        cloud.tick()
        self.assertEqual(cloud.snapshot()['interval'], 1)
        self.assertAlmostEqual(cloud.store.read()['nextPollAt'], started + 1, delta=.2)
        cloud.set_wait(['232:1'], False)
        self.assertGreater(cloud.store.read()['nextPollAt'], time.time() + 290)

    def test_invalid_login_does_not_register_account(self):
        from library_login import LoginError
        self.authenticate.side_effect = LoginError('wrong password')
        with self.assertRaises(LoginError):
            self.login()
        self.assertEqual(self.store.accounts, {})

    def test_failed_token_validation_does_not_register_account(self):
        self.clients['alice'].reservation = Mock(side_effect=LibraryError('token rejected', expired=True))
        with self.assertRaises(LibraryError):
            self.login()
        self.assertEqual(self.store.accounts, {})

    def test_failed_relogin_preserves_existing_saved_credentials(self):
        from library_login import LoginError
        cloud = self.login()
        before = cloud.store.read()
        self.authenticate.side_effect = LoginError('wrong password')
        with self.assertRaises(LoginError):
            self.login()
        self.assertEqual(cloud.store.read(), before)

    def test_no_password_stored_without_remember(self):
        cloud = self.login(remember=False)
        self.assertNotIn('login', cloud.store.read())
        self.assertFalse(cloud.snapshot()['autoLogin'])

    def test_second_device_login_preserves_wait_without_booking(self):
        cloud = self.login()
        cloud.set_wait(['102:3'], True)
        self.clients['alice'].reserve = Mock()
        other = self.login()
        self.assertTrue(other.snapshot()['running'])
        self.assertEqual(other.snapshot()['targets'], ['102:3'])
        self.clients['alice'].reserve.assert_not_called()

    def test_account_state_reservations_and_locks_are_isolated(self):
        alice, bob = self.login(), self.login('bob')
        alice.set_wait(['102:3'], True)
        self.assertEqual(bob.snapshot()['targets'], [])
        self.store.locks[alice.store.account] = 'another-worker'
        with self.assertRaises(LibraryError):
            alice.tick()
        bob.tick()
        self.store.locks.pop(alice.store.account)
        alice.tick()
        self.assertIsNone(bob.snapshot()['reservation'])
        self.assertEqual(alice.snapshot()['reservation']['seatNo'], '3')
        alice.disconnect()
        self.assertTrue(bob.snapshot()['connected'])
        self.assertTrue(bob.snapshot()['autoLogin'])
        self.assertNotIn('login', alice.store.read())

    def test_auto_login_after_expiry_defers_reservation_until_next_tick(self):
        cloud = self.login()
        cloud.set_wait(['102:3'], True)
        self.clients['alice'].reservation = Mock(side_effect=LibraryError('expired', expired=True))
        cloud.tick()
        self.assertFalse(cloud.snapshot()['connected'])
        self.clients['alice'] = DemoClient()
        self.clients['alice'].reserve = Mock(wraps=self.clients['alice'].reserve)
        cloud.tick()
        self.assertTrue(cloud.snapshot()['connected'])
        self.clients['alice'].reserve.assert_not_called()
        cloud.tick()
        self.clients['alice'].reserve.assert_called_once()

    def test_failed_auto_login_removes_bad_password_and_stops_retries(self):
        from library_login import LoginError
        cloud = self.login()
        cloud.set_wait(['102:3'], True)
        self.clients['alice'].reservation = Mock(side_effect=LibraryError('expired', expired=True))
        cloud.tick()
        self.authenticate.side_effect = LoginError('wrong password')
        cloud.tick()
        calls = self.authenticate.call_count
        cloud.tick()
        self.assertEqual(self.authenticate.call_count, calls)
        self.assertNotIn('login', cloud.store.read())
        self.assertFalse(cloud.snapshot()['running'])

    def test_network_failure_backs_off_without_discarding_valid_saved_login(self):
        from library_login import LoginError
        cloud = self.login()
        self.clients['alice'].reservation = Mock(side_effect=LibraryError('expired', expired=True))
        cloud.store.accounts[cloud.store.account]['state']['lastChecked'] = 0
        cloud.tick()
        self.authenticate.side_effect = LoginError('offline', kind='unavailable')
        cloud.tick()
        calls = self.authenticate.call_count
        cloud.tick()
        self.assertEqual(self.authenticate.call_count, calls)
        self.assertIn('login', cloud.store.read())

    def test_disarmed_state_is_durable_before_cloud_write(self):
        cloud = self.login()
        cloud.set_wait(['102:3'], True)
        client = self.clients['alice']
        original = client.reserve
        def reserve(seat):
            self.assertFalse(cloud.store.read()['state']['running'])
            original(seat)
        client.reserve = reserve
        cloud.tick()

    def test_browser_sessions_cannot_read_or_modify_other_accounts(self):
        app = create_app(self.root, 'test-admin-password-long-enough', secret='test-only', secure_cookie=False)
        app.testing = True
        alice, bob = app.test_client(), app.test_client()
        def signin(browser, username):
            csrf = browser.get('/api/session').json['csrf']
            response = browser.post('/api/library-login', json={'username': username, 'password': 'valid-password', 'remember': True}, headers={'X-CSRF-Token': csrf})
            self.assertEqual(response.status_code, 200)
            return response.json['csrf']
        alice_csrf = signin(alice, 'alice')
        signin(bob, 'bob')
        bob_account = self.root._account_key('bob')
        response = alice.post('/api/wait', json={'targets': ['102:3'], 'running': True, 'account': bob_account}, headers={'X-CSRF-Token': alice_csrf})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(bob.get('/api/state').json['targets'], [])
        self.assertEqual(alice.get('/api/state?account=' + bob_account).json['targets'], ['102:3'])
        self.assertEqual(alice.post('/api/cron', json={'account': bob_account}).status_code, 401)
        self.assertEqual(app.test_client().get('/api/state').status_code, 401)

    def test_failed_login_creates_no_browser_session_or_account(self):
        from library_login import LoginError
        self.authenticate.side_effect = LoginError('wrong password')
        app = create_app(self.root, 'test-admin-password-long-enough', secret='test-only', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        response = browser.post('/api/library-login', json={'username': 'alice', 'password': 'bad', 'remember': True}, headers={'X-CSRF-Token': csrf})
        self.assertEqual(response.status_code, 422)
        self.assertFalse(browser.get('/api/session').json['authorized'])
        self.assertEqual(browser.get('/api/state').status_code, 401)
        self.assertEqual(self.store.accounts, {})


class ProviderLoginTests(unittest.TestCase):
    def response(self, body=None, status=200):
        response = Mock(status_code=status, is_redirect=False)
        response.json.return_value = body
        return response

    def test_only_explicit_success_with_token_and_identity_is_accepted(self):
        from library_login import LoginError, login_to_library
        with patch('library_login.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.cookies.get_dict.return_value = {}
            for body in ({'success': False, 'code': 'error.login'}, {'success': True, 'code': 'success.loggedIn', 'data': {}}, {'success': 'true', 'code': 'success.loggedIn'}, {'success': True, 'code': 'warning.secondary.need.authentication'}):
                session.post.return_value = self.response(body)
                with self.assertRaises(LoginError):
                    login_to_library('alice', 'bad')
            session.post.return_value = self.response({'success': True, 'code': 'success.loggedIn', 'data': {'id': 25, 'accessToken': 'valid-token', 'isPrivacyPolicyAgree': False}})
            self.assertEqual(login_to_library('alice', 'valid')['identity'], '25')

    def test_html_and_timeout_are_not_reported_as_wrong_password(self):
        from library_login import LoginError, login_to_library
        with patch('library_login.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = self.response()
            response.json.side_effect = ValueError()
            session.post.return_value = response
            with self.assertRaises(LoginError) as error:
                login_to_library('alice', 'password')
            self.assertEqual(error.exception.kind, 'unavailable')
            session.post.side_effect = requests.Timeout()
            with self.assertRaises(LoginError) as error:
                login_to_library('alice', 'password')
            self.assertEqual(error.exception.kind, 'unavailable')


if __name__ == '__main__':
    unittest.main()
