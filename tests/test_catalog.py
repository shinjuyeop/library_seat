import time
from unittest.mock import Mock

from seat_service import LibraryError, SeatService, valid_seat_key
from test_service import ServiceFixture


class CatalogExclusionTests(ServiceFixture):
    def test_room_specific_boundaries_and_excluded_seats(self):
        for key in ('101:409', '101:410', '101:9999', *[f'234:{n}' for n in (149, 150, 207, 279, 325, 326, 347, 348)]):
            self.assertFalse(valid_seat_key(key), key)
        for key in ('101:408', '102:409', '233:149', '234:148', '234:151', '234:208', '234:349'):
            self.assertTrue(valid_seat_key(key), key)

    def test_provider_catalog_filters_unavailable_seats_before_public_counts(self):
        self.client.seats = Mock(side_effect=lambda room: [
            {'id': room * 1000 + n, 'code': str(n), 'isOccupied': False}
            for n in ([408, 409, 410] if room == 101 else [148, 149, 150, 151, 207, 279, 325, 326, 347, 348, 349])
        ])
        seats = self.service._read_seats({101, 234})
        self.assertEqual([seat['key'] for seat in seats], ['101:408', '234:148', '234:151', '234:349'])

    def test_legacy_saved_targets_and_cached_rows_are_removed(self):
        self.store.save(['101:409', '234:149', '102:3'], True)
        service = SeatService(self.store, client=self.client)
        service.state['seats'] = [{'key': '101:409'}, {'key': '234:149'}, {'key': '102:3'}]
        self.assertEqual(service.snapshot()['targets'], ['102:3'])
        self.assertEqual(service.snapshot()['seats'], [{'key': '102:3'}])
        self.assertTrue(service.snapshot()['running'])
        self.store.save(['101:409', '234:149'], True)
        self.assertFalse(SeatService(self.store).snapshot()['running'])

    def test_direct_booking_and_wait_cannot_target_excluded_seats(self):
        self.client.reserve = Mock()
        self.client.release = Mock()
        for key in ('101:409', '234:149'):
            with self.assertRaises(LibraryError):
                self.service.reserve(key)
            with self.assertRaises(LibraryError):
                self.service.set_wait([key], True)
        self.client.reserve.assert_not_called()
        self.client.release.assert_not_called()

    def test_saved_repeat_for_removed_seat_stops_without_returning_it(self):
        self.client.reserve(101409)
        current = self.client.reservation()
        repeat = {'reservationId': current['id'], 'seatId': 101409, 'roomId': 101,
                  'dueAt': time.time() - 1, 'expiresAt': time.time() + 30}
        self.store.save([], False, repeat, {'observedId': current['id'], 'paused': False})
        service = SeatService(self.store, client=self.client)
        self.client.release = Mock()
        self.client.reserve = Mock()
        service.tick()
        self.assertIsNone(service.snapshot()['repeat'])
        self.assertEqual(service.snapshot()['reservation']['id'], current['id'])
        self.client.release.assert_not_called()
        self.client.reserve.assert_not_called()

    def test_reassign_removed_seat_does_not_return_it(self):
        self.client.reserve(101409)
        self.client.current['state'] = 'CHARGE'
        self.client.release = Mock()
        with self.assertRaises(LibraryError):
            self.service.reassign(self.client.current['id'])
        self.client.release.assert_not_called()
