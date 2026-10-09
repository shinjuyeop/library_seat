"""Immediate confirmed bookings with a synthetic shared tag, without provider traffic."""
import copy
import json
import time
from datetime import datetime
from unittest.mock import Mock, patch

from seat_service import KST, LibraryError, ROOMS, SeatService, configured_nfc_tags
from test_service import ServiceFixture

TAG = '0123456789ABCDEF'


class ImmediateBookingTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service.nfc_tags = {room: TAG for room in ROOMS}

    def test_shared_tag_covers_every_room_and_each_booking_confirms_immediately(self):
        with patch.dict('os.environ', {'LIBRARY_NFC_TAGS': json.dumps({'102': TAG})}):
            self.assertEqual(configured_nfc_tags(), self.service.nfc_tags)
        for room in ROOMS:
            with self.subTest(room=room):
                self.client.current = None
                self.client.check_arrival = Mock(wraps=self.client.check_arrival)
                self.service.reserve(f'{room}:3')
                state = self.service.snapshot()
                self.assertEqual(state['reservation']['state'], 'CHARGE')
                self.assertEqual(int(state['reservation']['roomId']), room)
                self.assertIsNone(state['repeat'])
                self.assertFalse(state['running'])
                self.client.check_arrival.assert_called_once_with(room, TAG)
                self.client.check_arrival.reset_mock()
                self.client.current.update(renewableAt=time.time() - 1,
                    endTime=datetime.fromtimestamp(time.time() + 7100, KST).strftime('%Y-%m-%d %H:%M:%S'))
                self.service.renew(state['reservation']['id'])
                self.client.check_arrival.assert_called_once_with(room, TAG)
                self.assertEqual(self.service.snapshot()['reservation']['renewableCnt'], 2)
                self.client.check_arrival = self.client.check_arrival._mock_wraps

    def test_confirmation_failure_retains_temporary_seat_and_never_rebooks(self):
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.client.release = Mock(wraps=self.client.release)
        self.client.confirm_reservation = Mock(side_effect=LibraryError('synthetic rejection'))
        with self.assertRaises(LibraryError):
            self.service.reserve('232:3')
        self.service.tick()
        self.service.tick()
        self.client.reserve.assert_called_once_with(232003)
        self.client.release.assert_not_called()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['state'], 'TEMP_CHARGE')
        self.assertIsNone(state['repeat'])
        self.assertFalse(state['running'])

    def test_previously_saved_due_repeat_cannot_cancel_or_rebook_on_restart(self):
        self.client.reserve(102003)
        original = copy.deepcopy(self.client.current)
        plan = {'reservationId': original['id'], 'seatId': 102003, 'roomId': 102,
                'dueAt': time.time() - 1, 'expiresAt': time.time() + 60}
        self.store.save([], False, plan, {'observedId': original['id'], 'paused': False})
        self.client.reserve = Mock(wraps=self.client.reserve)
        self.client.release = Mock(wraps=self.client.release)
        restarted = SeatService(self.store, client=self.client, nfc_tags=self.service.nfc_tags)
        restarted.tick()
        restarted.tick()
        self.assertEqual(self.client.current, original)
        self.assertIsNone(restarted.snapshot()['repeat'])
        with self.assertRaises(LibraryError):
            restarted.set_repeat(True, original['id'])
        restarted.set_repeat(False, original['id'])
        self.client.reserve.assert_not_called()
        self.client.release.assert_not_called()

    def test_first_available_waiting_seat_is_confirmed_and_stops_waiting(self):
        self.service.set_wait(['233:3'], True)
        self.service.tick()
        state = self.service.snapshot()
        self.assertEqual(state['reservation']['state'], 'CHARGE')
        self.assertFalse(state['running'])
        self.assertEqual(state['targets'], [])
        self.assertIsNone(state['repeat'])
