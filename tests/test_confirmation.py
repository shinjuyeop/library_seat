"""Confirmation uses mock provider data only; no captured credentials or tag values."""
import copy
import json
import unittest
from unittest.mock import Mock, patch

from seat_service import LibraryClient, LibraryError, SeatService, configured_nfc_tags
from test_service import ServiceFixture
from webapp import create_app

TAG = '0123456789ABCDEF'


class ConfirmationTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service.nfc_tags = {102: TAG}
        self.service.reserve('102:3')
        self.service.set_wait(['232:1'], True)
        self.original = copy.deepcopy(self.client.current)
        self.client.release = Mock(wraps=self.client.release)
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.client.check_arrival = Mock(wraps=self.client.check_arrival)
        self.client.confirm_reservation = Mock(wraps=self.client.confirm_reservation)

    def confirm(self):
        self.service.confirm(self.original['id'])

    def assert_stopped(self):
        saved = self.store.load()
        self.assertIsNone(saved['repeat'])
        self.assertFalse(saved['running'])
        self.assertEqual(saved['targets'], [])
        self.assertTrue(saved['repeatControl']['paused'])
        self.client.release.assert_not_called()
        self.client.reserve.assert_not_called()

    def test_success_disarms_before_writes_and_survives_restart(self):
        self.client.check_arrival.side_effect = lambda *_: self.assert_stopped()
        self.confirm()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['id'], self.original['id'])
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertTrue(state['reservationFresh'])
        self.assert_stopped()
        self.client.check_arrival.assert_called_once_with(102, TAG)
        self.client.confirm_reservation.assert_called_once_with(self.original['id'])
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.assertIsNone(restarted.snapshot()['repeat'])
        self.assertNotIn(TAG, json.dumps(state))
        self.assertNotIn(TAG, json.dumps(self.store.load()))

    def test_already_confirmed_is_idempotent_without_provider_writes(self):
        self.client.current['state'] = 'CHARGE'
        self.confirm()
        self.assert_stopped()
        self.client.check_arrival.assert_not_called()
        self.client.confirm_reservation.assert_not_called()

    def test_stale_id_unknown_state_missing_seat_and_unsupported_room_do_not_write(self):
        variants = [None, {**self.original, 'id': '999'}, {**self.original, 'state': 'UNKNOWN'},
                    {**self.original, 'seatId': None}, {**self.original, 'roomId': 232}]
        for current in variants:
            with self.subTest(current=current):
                self.client.current = current
                with self.assertRaises(LibraryError):
                    self.confirm()
                self.client.check_arrival.assert_not_called()
                self.client.confirm_reservation.assert_not_called()
                self.client.release.assert_not_called()

    def test_arrival_rejection_prevents_confirmation_and_stays_paused(self):
        self.client.check_arrival.side_effect = LibraryError('tag rejected')
        with self.assertRaisesRegex(LibraryError, 'tag rejected'):
            self.confirm()
        self.client.confirm_reservation.assert_not_called()
        self.assert_stopped()
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.assertIsNone(restarted.snapshot()['repeat'])

    def test_booking_changes_after_arrival_prevent_confirmation(self):
        for changes in ({'id': '999'}, {'seatId': 102004}, {'roomId': 232}, {'state': 'UNKNOWN'}):
            with self.subTest(changes=changes):
                self.client.current = copy.deepcopy(self.original)
                self.client.check_arrival.side_effect = lambda *_, changes=changes: self.client.current.update(changes)
                with self.assertRaisesRegex(LibraryError, '변경'):
                    self.confirm()
                self.client.confirm_reservation.assert_not_called()
        self.assert_stopped()

    def test_http_success_without_confirmed_state_is_not_success(self):
        self.client.confirm_reservation = Mock()
        with self.assertRaisesRegex(LibraryError, '아직 배정확정'):
            self.confirm()
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'TEMP_CHARGE')
        self.assertFalse(self.service.snapshot()['reservationFresh'])
        self.assert_stopped()

    def test_timeout_reconciles_confirmed_state_once_without_retry(self):
        def timeout(_):
            self.client.current['state'] = 'CHARGE'
            raise LibraryError('timeout', uncertain=True)
        self.client.confirm_reservation.side_effect = timeout
        self.confirm()
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'CHARGE')
        self.client.confirm_reservation.assert_called_once()
        self.assert_stopped()

    def test_unresolved_timeout_is_not_replayed_by_worker(self):
        self.client.confirm_reservation.side_effect = LibraryError('timeout', uncertain=True)
        with self.assertRaisesRegex(LibraryError, '결과를 확인'):
            self.confirm()
        self.service.tick()
        self.client.confirm_reservation.assert_called_once()
        self.assert_stopped()

    def test_failed_read_after_write_never_reports_success_or_repeats(self):
        self.client.reservation = Mock(side_effect=[self.original, self.original, LibraryError('read failed')])
        with self.assertRaisesRegex(LibraryError, 'read failed'):
            self.confirm()
        self.assertFalse(self.service.snapshot()['reservationFresh'])
        self.assert_stopped()

    def test_process_crash_after_disarm_cannot_revive_rebooking(self):
        self.client.check_arrival.side_effect = SystemExit('crash')
        with self.assertRaises(SystemExit):
            self.confirm()
        self.assert_stopped()
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.assertIsNone(restarted.snapshot()['repeat'])

    def test_api_auth_csrf_input_and_tag_privacy(self):
        app = create_app(self.service, 'a-test-password-long-enough', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        payload = {'id': self.original['id']}
        self.assertEqual(browser.post('/api/confirm', json=payload, headers={'X-CSRF-Token': csrf}).status_code, 401)
        csrf = browser.post('/api/login', json={'password': 'a-test-password-long-enough'}, headers={'X-CSRF-Token': csrf}).json['csrf']
        self.assertEqual(browser.post('/api/confirm', json=payload).status_code, 403)
        headers = {'X-CSRF-Token': csrf}
        for body in ([], {}, {'id': 1}, {'id': '../2'}, {'id': '1' * 21}):
            self.assertEqual(browser.post('/api/confirm', json=body, headers=headers).status_code, 400)
        self.assertEqual(browser.post('/api/confirm', json=payload, headers=headers).status_code, 200)
        response = browser.get('/api/state')
        self.assertEqual(response.json['confirmationRooms'], [102])
        self.assertNotIn(TAG, response.text)


class ReassignmentTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service.nfc_tags = {102: TAG}
        self.client.reserve(102003)
        self.client.confirm_reservation(self.client.current['id'])
        self.service.tick()
        self.service.set_wait(['232:1'], True)
        self.original = copy.deepcopy(self.client.current)
        self.calls = Mock()
        for method in ('release', 'reserve', 'check_arrival', 'confirm_reservation'):
            wrapped = Mock(wraps=getattr(self.client, method))
            setattr(self.client, method, wrapped)
            self.calls.attach_mock(wrapped, method)

    def reassign(self):
        self.service.reassign(self.original['id'])

    def assert_disarmed(self):
        saved = self.store.load()
        self.assertFalse(saved['running'])
        self.assertEqual(saved['targets'], [])
        self.assertIsNone(saved['repeat'])
        self.assertTrue(saved['repeatControl']['paused'])

    def test_returns_reserves_and_confirms_same_seat_in_order(self):
        original_release = self.client.release._mock_wraps
        def release(reservation):
            self.assert_disarmed()
            original_release(reservation)
        self.client.release.side_effect = release
        self.reassign()
        self.assertEqual([call[0] for call in self.calls.mock_calls],
                         ['release', 'reserve', 'check_arrival', 'confirm_reservation'])
        self.client.reserve.assert_called_once_with(self.original['seatId'])
        current = self.service.snapshot()['reservation']
        self.assertNotEqual(current['id'], self.original['id'])
        self.assertEqual((current['seatId'], current['roomId'], current['state']), (102003, 102, 'CHARGE'))
        self.client.confirm_reservation.assert_called_once_with(current['id'])
        self.assertTrue(self.service.snapshot()['reservationFresh'])
        self.assertIsNone(self.service.snapshot()['error'])
        self.assert_disarmed()
        # Replaying the old browser request must never return the replacement seat.
        with self.assertRaises(LibraryError):
            self.reassign()
        self.client.release.assert_called_once()

    def test_invalid_or_changed_source_is_never_returned(self):
        for current in (None, {**self.original, 'id': '999'}, {**self.original, 'state': 'TEMP_CHARGE'},
                        {**self.original, 'state': 'UNKNOWN'}, {**self.original, 'seatId': None},
                        {**self.original, 'roomId': 232}):
            with self.subTest(current=current):
                self.client.current = current
                with self.assertRaises(LibraryError):
                    self.reassign()
                self.assertEqual(self.calls.mock_calls, [])

    def test_return_failure_prevents_rebooking(self):
        self.client.release.side_effect = LibraryError('return rejected')
        with self.assertRaisesRegex(LibraryError, '좌석 반납 단계'):
            self.reassign()
        self.client.reserve.assert_not_called()
        self.assertEqual(self.service.snapshot()['reservation']['id'], self.original['id'])
        self.assert_disarmed()

    def test_ambiguous_return_is_not_followed_by_reserve_even_if_empty(self):
        def timeout(_):
            self.client.current = None
            raise LibraryError('timeout', uncertain=True)
        self.client.release.side_effect = timeout
        with self.assertRaisesRegex(LibraryError, '좌석 반납 단계'):
            self.reassign()
        self.client.reserve.assert_not_called()
        self.assertIsNone(self.service.snapshot()['reservation'])
        self.assert_disarmed()

    def test_return_must_be_verified_before_reserve(self):
        self.client.release = Mock()
        with self.assertRaisesRegex(LibraryError, '좌석 반납 단계'):
            self.reassign()
        self.client.reserve.assert_not_called()
        self.assert_disarmed()

    def test_contested_seat_stops_without_retry_or_confirmation(self):
        self.client.reserve.side_effect = LibraryError('already taken')
        with self.assertRaisesRegex(LibraryError, '같은 좌석 재예약 단계'):
            self.reassign()
        self.assertIsNone(self.service.snapshot()['reservation'])
        self.assertTrue(self.service.snapshot()['reservationFresh'])
        self.client.check_arrival.assert_not_called()
        self.service.tick()
        self.client.reserve.assert_called_once()
        self.assert_disarmed()

    def test_ambiguous_reserve_keeps_detected_temporary_seat_without_more_writes(self):
        reserve = self.client.reserve._mock_wraps
        def timeout(seat_id):
            reserve(seat_id)
            raise LibraryError('timeout', uncertain=True)
        self.client.reserve.side_effect = timeout
        with self.assertRaisesRegex(LibraryError, '같은 좌석 재예약 단계'):
            self.reassign()
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'TEMP_CHARGE')
        self.client.check_arrival.assert_not_called()
        self.assert_disarmed()

    def test_wrong_replacement_seat_or_reused_id_is_never_confirmed(self):
        reserve = self.client.reserve._mock_wraps
        for override in ({'id': self.original['id']}, {'seatId': 102004}, {'roomId': 232}, {'state': 'UNKNOWN'}):
            with self.subTest(override=override):
                self.client.current = copy.deepcopy(self.original)
                def replacement(seat_id):
                    reserve(seat_id)
                    self.client.current.update(override)
                self.client.reserve.side_effect = replacement
                with self.assertRaisesRegex(LibraryError, '같은 좌석 재예약 단계'):
                    self.reassign()
                self.client.check_arrival.assert_not_called()
        self.assert_disarmed()

    def test_failed_confirmation_keeps_new_temporary_seat_and_shows_failed_stage(self):
        self.client.check_arrival.side_effect = LibraryError('tag rejected')
        with self.assertRaisesRegex(LibraryError, '배정 확정 단계'):
            self.reassign()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertNotEqual(state['reservation']['id'], self.original['id'])
        self.assertTrue(state['reservationFresh'])
        self.assertIn('배정 확정 단계', state['error'])
        self.client.release.assert_called_once()
        self.client.confirm_reservation.assert_not_called()
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.assertIsNone(restarted.snapshot()['repeat'])
        self.assertFalse(restarted.snapshot()['running'])

    def test_confirmation_timeout_can_reconcile_success_without_repeating(self):
        confirm = self.client.confirm_reservation._mock_wraps
        def timeout(identifier):
            confirm(identifier)
            raise LibraryError('timeout', uncertain=True)
        self.client.confirm_reservation.side_effect = timeout
        self.reassign()
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'CHARGE')
        self.client.confirm_reservation.assert_called_once()
        self.assert_disarmed()

    def test_crash_after_new_booking_cannot_arm_repeat_on_restart(self):
        self.client.check_arrival.side_effect = SystemExit('crash')
        with self.assertRaises(SystemExit):
            self.reassign()
        self.assert_disarmed()
        restarted = SeatService(self.store, client=self.client)
        restarted.tick()
        self.assertEqual(restarted.snapshot()['reservation']['state'], 'TEMP_CHARGE')
        self.assertIsNone(restarted.snapshot()['repeat'])
        self.client.release.assert_called_once()
        self.client.reserve.assert_called_once()

    def test_api_requires_login_csrf_and_original_booking_id(self):
        app = create_app(self.service, 'a-test-password-long-enough', secure_cookie=False)
        browser = app.test_client()
        csrf = browser.get('/api/session').json['csrf']
        payload = {'id': self.original['id']}
        self.assertEqual(browser.post('/api/reassign', json=payload, headers={'X-CSRF-Token': csrf}).status_code, 401)
        csrf = browser.post('/api/login', json={'password': 'a-test-password-long-enough'}, headers={'X-CSRF-Token': csrf}).json['csrf']
        self.assertEqual(browser.post('/api/reassign', json=payload).status_code, 403)
        headers = {'X-CSRF-Token': csrf}
        for body in ([], {}, {'id': 1}, {'id': '../2'}, {'id': '1' * 21}):
            self.assertEqual(browser.post('/api/reassign', json=body, headers=headers).status_code, 400)
        self.assertEqual(browser.post('/api/reassign', json=payload, headers=headers).status_code, 200)
        self.assertEqual(browser.post('/api/reassign', json=payload, headers=headers).status_code, 409)
        self.client.release.assert_called_once()


class AutomaticSwitchConfirmationTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service.nfc_tags = {102: TAG}
        self.client.reserve(102006)
        self.client.confirm_reservation(self.client.current['id'])
        self.original = copy.deepcopy(self.client.current)
        self.service.set_wait(['102:3', '232:3'], True)
        self.calls = Mock()
        for method in ('release', 'reserve', 'check_arrival', 'confirm_reservation'):
            wrapped = Mock(wraps=getattr(self.client, method))
            setattr(self.client, method, wrapped)
            self.calls.attach_mock(wrapped, method)

    def reject_target(self, target=102003):
        reserve = self.client.reserve._mock_wraps
        def reserve_or_reject(seat_id):
            if seat_id == target:
                raise LibraryError('occupied')
            reserve(seat_id)
        self.client.reserve.side_effect = reserve_or_reject

    def assert_disarmed(self):
        saved = self.store.load()
        self.assertFalse(saved['running'])
        self.assertEqual(saved['targets'], [])
        self.assertIsNone(saved['repeat'])
        self.assertTrue(saved['repeatControl']['paused'])

    def test_wait_switch_confirms_new_seat_and_never_arms_repeat(self):
        self.client.check_arrival.side_effect = lambda *_: self.assert_disarmed()
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 102003)
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertTrue(state['reservationFresh'])
        self.assertIsNone(state['error'])
        self.assertIn('갈아타기와 배정확정', state['message'])
        self.assert_disarmed()
        self.assertEqual([call[0] for call in self.calls.mock_calls],
                         ['release', 'reserve', 'check_arrival', 'confirm_reservation'])
        self.client.confirm_reservation.assert_called_once_with(state['reservation']['id'])
        self.service.tick()
        self.client.release.assert_called_once()

    def test_recovered_original_is_confirmed_before_wait_resumes(self):
        self.reject_target()
        self.client.check_arrival.side_effect = lambda *_: self.assert_disarmed()
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 102006)
        self.assertNotEqual(state['reservation']['id'], self.original['id'])
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertTrue(state['running'])
        self.assertEqual(state['targets'], ['102:3', '232:3'])
        self.assertIsNone(state['repeat'])
        self.assertIsNone(state['error'])
        self.assertIn('배정확정까지 완료', state['message'])
        self.assertEqual([call[0] for call in self.calls.mock_calls],
                         ['release', 'reserve', 'reserve', 'check_arrival', 'confirm_reservation'])
        self.client.confirm_reservation.assert_called_once_with(state['reservation']['id'])
        self.client.seats = Mock(return_value=[])
        restarted = SeatService(self.store, client=self.client, nfc_tags={102: TAG})
        restarted.tick()
        self.assertTrue(restarted.snapshot()['running'])
        self.assertIsNone(restarted.snapshot()['repeat'])
        self.client.release.assert_called_once()

    def test_new_seat_confirmation_failure_never_cancels_or_restores(self):
        self.client.check_arrival.side_effect = LibraryError('tag rejected')
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 102003)
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertIn('새 좌석 예약 후 배정확정', state['error'])
        self.client.reserve.assert_called_once_with(102003)
        self.client.release.assert_called_once()
        self.assert_disarmed()
        restarted = SeatService(self.store, client=self.client, nfc_tags={102: TAG})
        restarted.tick()
        self.client.reserve.assert_called_once()
        self.client.check_arrival.assert_called_once()
        self.assertIsNone(restarted.snapshot()['repeat'])

    def test_recovery_confirmation_failure_preserves_temporary_seat_and_stops_wait(self):
        self.reject_target()
        self.client.confirm_reservation.side_effect = LibraryError('confirmation rejected')
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 102006)
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertIn('원래 좌석 복구 후 배정확정', state['error'])
        self.assertNotIn('원래 좌석 재예약에 실패', state['error'])
        self.assert_disarmed()
        self.service.tick()
        self.assertEqual(self.client.reserve.call_count, 2)
        self.client.release.assert_called_once()
        self.client.confirm_reservation.assert_called_once()

    def test_confirmation_success_without_state_change_does_not_complete_switch(self):
        self.client.confirm_reservation = Mock()
        self.service.tick()
        self.assertEqual(self.service.snapshot()['reservation']['state'], 'TEMP_CHARGE')
        self.assertIn('배정확정을 확인하지 못해', self.service.snapshot()['error'])
        self.assert_disarmed()

    def test_crash_during_recovery_confirmation_does_not_resume_wait_or_repeat(self):
        self.reject_target()
        self.client.check_arrival.side_effect = SystemExit('crash')
        with self.assertRaises(SystemExit):
            self.service.tick()
        self.assert_disarmed()
        restarted = SeatService(self.store, client=self.client, nfc_tags={102: TAG})
        restarted.tick()
        self.assertFalse(restarted.snapshot()['running'])
        self.assertIsNone(restarted.snapshot()['repeat'])
        self.client.release.assert_called_once()
        self.assertEqual(self.client.reserve.call_count, 2)

    def test_ambiguous_booking_never_attempts_recovery_or_confirmation(self):
        self.client.reserve.side_effect = LibraryError('timeout', uncertain=True)
        self.service.tick()
        self.client.reserve.assert_called_once_with(102003)
        self.client.check_arrival.assert_not_called()
        self.assert_disarmed()

    def test_unregistered_target_stays_temporary_with_official_app_guidance(self):
        self.service.set_wait(['232:3'], True)
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 232003)
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertFalse(state['running'])
        self.assertIsNotNone(state['repeat'])
        self.assertIn('공식 앱에서 NFC 인증', state['message'])
        self.client.check_arrival.assert_not_called()

    def test_recovery_checks_original_room_support_not_failed_target_room(self):
        self.service.set_wait(['232:3'], True)
        self.reject_target(232003)
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 102006)
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertTrue(state['running'])
        self.client.check_arrival.assert_called_once_with(102, TAG)

    def test_unregistered_original_is_recovered_without_using_target_tag(self):
        self.client.current.update(roomId=232, seatId=232006, roomName='2열람실')
        self.reject_target()
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['seatId'], 232006)
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertTrue(state['running'])
        self.assertIsNotNone(state['repeat'])
        self.assertIn('공식 앱에서 NFC 인증', state['message'])
        self.client.check_arrival.assert_not_called()


class ConfirmationTransportTests(unittest.TestCase):
    def test_exact_provider_requests_and_strict_arrival_result(self):
        client = LibraryClient('fake-token')
        self.addCleanup(client.close)
        client.session.request = Mock(return_value=Mock(status_code=200, json=Mock(return_value={'success': True, 'data': True})))
        client.check_arrival(102, TAG)
        args, kwargs = client.session.request.call_args
        self.assertEqual(args, ('POST', 'https://library.konkuk.ac.kr/pyxis-api/1/api/rooms/102/check-arrival'))
        self.assertEqual(kwargs['json'], {'methodCode': 'RF_TAG', 'serialNo': TAG})
        client.confirm_reservation('123')
        args, kwargs = client.session.request.call_args
        self.assertTrue(args[1].endswith('/seat-charges/123'))
        self.assertEqual(kwargs['params'], {'smufMethodCode': 'MOBILE', '_method': 'put'})
        self.assertNotIn('json', kwargs)
        for result in (False, None, {}, 'true', 1):
            client.session.request.return_value.json.return_value = {'success': True, 'data': result}
            with self.assertRaises(LibraryError):
                client.check_arrival(102, TAG)

    def test_configuration_rejects_invalid_values_without_exposure(self):
        for value in ('bad-json', 'null', '[]', '{"999":"0123456789ABCDEF"}', '{"102":"bad"}'):
            with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': value}):
                self.assertEqual(configured_nfc_tags(), {})
        with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': json.dumps({'102': TAG})}):
            self.assertEqual(configured_nfc_tags(), {102: TAG})


if __name__ == '__main__':
    unittest.main()
