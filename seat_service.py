"""Server-owned reservation worker. No browser tab is required to keep it running."""
from __future__ import annotations

import copy
import json
import math
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from library_config import WATCH_LIST

BASE = 'https://library.konkuk.ac.kr'
ROOMS = {102: '1열람실 A', 101: '1열람실 B', 232: '2열람실',
         233: '3열람실 A', 234: '3열람실 B', 107: '5열람실'}
SINGLE_SEATS = {(room, str(number)) for room, number in WATCH_LIST}
MAX_TARGETS = 50
ACTIVE_STATES = {'TEMP_CHARGE', 'CHARGE', 'IN_USE'}
TEMP_REPEAT_SECONDS = 9 * 60
TEMP_DURATION_SECONDS = 10 * 60
AUTO_RENEW_REMAINING = 119 * 60
KST = timezone(timedelta(hours=9))
UNAVAILABLE_3B_SEATS = {149, 150, 207, 279, 325, 326, 347, 348}


def configured_nfc_tags():
    """Room identifiers stay server-side; malformed configuration fails closed."""
    try:
        tags = json.loads(os.getenv('LIBRARY_NFC_TAGS', '{}'))
        if not isinstance(tags, dict) or any(
            not isinstance(room, str) or not room.isdigit() or int(room) not in ROOMS
            or not isinstance(tag, str) or not re.fullmatch(r'[0-9a-fA-F]{16}', tag)
            for room, tag in tags.items()
        ):
            return {}
        # The captured NFC tag is shared across the library's reading rooms.
        if len(set(tags.values())) == 1:
            shared = next(iter(tags.values()))
            return {room: shared for room in ROOMS}
        return {int(room): tag for room, tag in tags.items()}
    except (ValueError, TypeError):
        return {}


def reservation_time(value):
    """The library sends ISO dates, sometimes without its Korean time zone."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        date = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        return date.replace(tzinfo=timezone(timedelta(hours=9))).timestamp() if date.tzinfo is None else date.timestamp()
    except ValueError:
        return None


def valid_seat_key(key):
    if not isinstance(key, str) or not re.fullmatch(r'\d{1,6}:\d{1,6}', key):
        return False
    room, number = key.split(':')
    room, number = int(room), int(number)
    return (room in ROOMS and number > 0
            and not (room == 101 and number > 408)
            and not (room == 234 and number in UNAVAILABLE_3B_SEATS))


def renewal_count(value):
    # Missing or malformed counts must never be mistaken for exhausted quota.
    if type(value) is int and value >= 0:
        return value
    return int(value) if isinstance(value, str) and re.fullmatch(r'[0-9]{1,4}', value) else None


def closed_until(now=None):
    date = datetime.fromtimestamp(time.time() if now is None else now, KST)
    if 5 <= date.hour < 23:
        return None
    morning = date.replace(hour=5, minute=0, second=0, microsecond=0)
    return (morning + (timedelta(days=1) if date.hour >= 23 else timedelta())).timestamp()


def schedule_window(now=None):
    date = datetime.fromtimestamp(time.time() if now is None else now, KST)
    morning = date.replace(hour=5, minute=0, second=0, microsecond=0)
    if date.hour >= 5:
        morning += timedelta(days=1)
    return {'date': morning.strftime('%Y-%m-%d'), 'open': date.hour < 5 or date.hour >= 12,
            'opensAt': (morning - timedelta(hours=17)).timestamp(), 'closesAt': morning.timestamp()}


def sanitize_seat_catalog(state):
    """Also filter persisted catalogs before their next provider refresh."""
    state['seats'] = [seat for seat in state.get('seats', []) if valid_seat_key(seat.get('key'))]
    state['targets'] = [key for key in state.get('targets', []) if valid_seat_key(key)]
    state['running'] = bool(state.get('running') and state['targets'])
    # Retire persisted temporary-seat loops, including cloud documents from older versions.
    state['repeat'] = None
    state['repeatControl'] = {'observedId': None, 'paused': True}
    return state


class LibraryError(Exception):
    def __init__(self, message, *, expired=False, uncertain=False):
        super().__init__(message)
        self.expired = expired
        self.uncertain = uncertain


def normalize_reservation(data):
    """Only a recognized empty collection means no reservation; fail closed otherwise."""
    if isinstance(data, dict):
        for key in ('list', 'items', 'seatCharges', 'reservations'):
            if key in data:
                return normalize_reservation(data[key])
        for key in ('reservation', 'data'):
            if key in data:
                return normalize_reservation(data[key])
        if 'id' not in data or not any(k in data for k in ('seat', 'seatId', 'seatNo')):
            raise LibraryError('내 예약 응답 형식을 확인할 수 없어 자동 예약을 보류합니다.')
        state = data.get('state', {})
        code = state.get('code') if isinstance(state, dict) else state
        code = code or data.get('stateCode') or data.get('seatChargeStateCode') or 'UNKNOWN'
        seat = data.get('seat') if isinstance(data.get('seat'), dict) else {}
        room = data.get('room') if isinstance(data.get('room'), dict) else seat.get('room', {})
        room = room if isinstance(room, dict) else {}
        end = data.get('endTime') or data.get('chargeEndDateTime') or data.get('endDateTime')
        started = next((reservation_time(data.get(key)) for key in (
            'startTime', 'startDateTime', 'beginTime', 'beginDateTime', 'startDate',
            'chargeStartDateTime', 'chargeStartTime', 'seatChargeStartDateTime',
            'seatChargeStartTime', 'useStartDateTime', 'useStartTime', 'fromDateTime'
        ) if reservation_time(data.get(key)) is not None), None)
        return {
            'id': str(data['id']), 'state': str(code).upper(),
            'seatNo': str(data.get('seatNo') or seat.get('code') or seat.get('name') or '확인 중'),
            'roomName': str(data.get('roomName') or room.get('name') or '열람실'),
            'seatId': data.get('seatId') or data.get('smufSeatId') or seat.get('seatId') or seat.get('smufSeatId') or seat.get('id'),
            'roomId': data.get('roomId') or data.get('smufRoomId') or room.get('id') or room.get('roomId'),
            'startedAt': started, 'endTime': end,
            'remainingTime': data.get('remainingTime'),
            'renewalLimit': renewal_count(data.get('renewalLimit')),
            'renewableCnt': renewal_count(data.get('renewableCnt')),
            'renewableAt': reservation_time(data.get('renewableDate')),
            'isRenewable': data.get('isRenewable') if type(data.get('isRenewable')) is bool else None,
            'isRenewalImpossible': data.get('isRenewalImpossible') if type(data.get('isRenewalImpossible')) is bool else None,
        }
    if isinstance(data, list):
        if not data:
            return None
        reservations = [normalize_reservation(item) for item in data]
        active = [item for item in reservations if item and item['state'] in ACTIVE_STATES]
        if len(active) == 1:
            return active[0]
        if len(reservations) == 1:
            return reservations[0]
    raise LibraryError('내 예약 상태가 불명확해 자동 예약을 보류합니다.')


class LibraryClient:
    def __init__(self, token, cookies=None):
        self.session = requests.Session()
        self.session.cookies.update(cookies or {})
        self.session.headers.update({
            'Pyxis-Auth-Token': token, 'Accept': 'application/json, text/plain, */*',
            'User-Agent': 'Mozilla/5.0', 'Origin': BASE,
            'Referer': BASE + '/mylibrary/seat/reservations',
            'X-Requested-With': 'XMLHttpRequest',
        })

    def close(self):
        self.session.close()

    def _request(self, method, path, **kwargs):
        try:
            response = self.session.request(method, BASE + '/pyxis-api/1/api/' + path,
                                            timeout=(5, 10), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise LibraryError('도서관 서버에 연결하지 못했습니다.', uncertain=method != 'GET') from None
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            raise LibraryError('도서관 로그인이 만료되었거나 접근이 거부되었습니다. 다시 연결해 주세요.', expired=True)
        try:
            body = response.json()
        except ValueError:
            raise LibraryError('도서관에서 정상적인 응답을 받지 못했습니다.', uncertain=method != 'GET') from None
        if not isinstance(body, dict):
            raise LibraryError('도서관 응답 형식을 확인할 수 없습니다.', uncertain=method != 'GET')
        if response.status_code != 200 or body.get('success') is not True:
            code = str(body.get('code', response.status_code))
            expired = any(word in code.upper() for word in ('AUTH', 'TOKEN', 'LOGIN', 'SESSION'))
            # Do not return arbitrary upstream payloads, which may contain account data.
            message = '도서관 요청이 거절되었습니다. 공식 앱에서 이용 상태를 확인해 주세요.'
            raise LibraryError(message, expired=expired, uncertain=method != 'GET' and response.status_code >= 500)
        if method == 'GET' and path == 'seat-charges' and body.get('code') == 'success.noRecord':
            return []  # Explicit empty result used by the official reservation page.
        return body.get('data')

    def seats(self, room_id):
        data = self._request('GET', f'rooms/{room_id}/seats')
        items = data.get('list') if isinstance(data, dict) else data
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise LibraryError('좌석 현황 응답 형식을 확인할 수 없습니다.')
        return items

    def reservation(self):
        return normalize_reservation(self._request('GET', 'seat-charges'))

    def reserve(self, seat_id):
        self._request('POST', 'seat-charges', json={'seatId': seat_id, 'smufMethodCode': 'PC'})

    def check_arrival(self, room_id, serial_no):
        result = self._request('POST', f'rooms/{room_id}/check-arrival',
                               json={'methodCode': 'RF_TAG', 'serialNo': serial_no})
        if result is not True:
            raise LibraryError('열람실 태그 확인에 실패했습니다. 공식 앱에서 NFC 인증을 진행해 주세요.')

    def confirm_reservation(self, reservation_id):
        self._request('POST', 'seat-charges/' + reservation_id,
                      params={'smufMethodCode': 'MOBILE', '_method': 'put'})

    def renew_reservation(self, reservation_id):
        self._request('POST', 'seat-renewed-charges',
                      json={'seatCharge': int(reservation_id), 'smufMethodCode': 'MOBILE'})

    def release(self, reservation):
        if reservation['state'] == 'TEMP_CHARGE':
            self._request('POST', 'seat-charges/' + reservation['id'],
                          params={'smufMethodCode': 'PC', '_method': 'delete'})
        elif reservation['state'] in {'CHARGE', 'IN_USE'}:
            self._request('POST', 'seat-discharges',
                          json={'seatCharge': int(reservation['id']), 'smufMethodCode': 'PC'})
        else:
            raise LibraryError('배정 상태를 확인할 수 없습니다. 공식 앱에서 처리해 주세요.')


class SettingsStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')

    def load(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT value FROM settings WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {'targets': [], 'running': False}

    def save(self, targets, running, repeat=None, repeat_control=None, auto_renew=None, renew_night_until=None, extras=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (1, ?)',
                       (json.dumps({'targets': targets, 'running': running, 'repeat': repeat,
                                    'repeatControl': repeat_control, 'autoRenew': auto_renew,
                                    'renewNightUntil': renew_night_until, **(extras or {})}),))


class SeatService:
    def __init__(self, store, *, interval=30, client=None, demo=False, nfc_tags=None):
        self.store = store
        self.interval = max(10, interval)
        self.client = client
        self.demo = demo
        self.nfc_tags = (nfc_tags if nfc_tags is not None else
                         {room: '0000000000000000' for room in ROOMS} if demo else configured_nfc_tags())
        self.operation = threading.RLock()
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.login_thread = None
        self.quiet_notifications = False
        saved = store.load()
        self.state = {
            'connected': client is not None, 'connecting': False,
            'running': bool(saved['running']),
            'targets': [key for key in saved['targets'] if valid_seat_key(key)][:MAX_TARGETS],
            'rooms': [{'id': room, 'name': name} for room, name in ROOMS.items()],
            'seats': [], 'reservation': None, 'reservationFresh': False,
            'catalogVersion': 0,
            'repeat': saved.get('repeat'),
            'repeatControl': saved.get('repeatControl') or {'observedId': None, 'paused': False},
            'autoRenew': saved.get('autoRenew'), 'renewNightUntil': saved.get('renewNightUntil'),
            'autoRenewDisabledId': saved.get('autoRenewDisabledId'),
            'scheduledBooking': saved.get('scheduledBooking'), 'notifications': saved.get('notifications', []),
            'lastChecked': None, 'nextCheck': None, 'error': None,
            'message': '도서관 계정을 연결해 주세요.' if not client else '좌석 현황을 확인하고 있습니다.',
            'events': [], 'demo': demo, 'interval': self.interval,
        }
        sanitize_seat_catalog(self.state)

    def snapshot(self):
        with self.lock:
            return {**sanitize_seat_catalog(copy.deepcopy(self.state)), 'confirmationRooms': sorted(self.nfc_tags),
                    'scheduleWindow': schedule_window()}

    def _update(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)

    def _event(self, text):
        with self.lock:
            self.state['events'].insert(0, {'time': time.time(), 'text': text})
            del self.state['events'][20:]
            self.state['message'] = text

    def _read_reservation(self):
        return self._with_booking_time(self.client.reservation())

    def _notify(self, kind, title, body, *, url='/?tab=my', key=None):
        if self.quiet_notifications:
            return
        with self.lock:
            recent = self.state['notifications']
            if any((key and item['id'] == key) or (kind == 'failure' and item['title'] == title
                   and item['body'] == body[:180] and time.time() - item['time'] < 600) for item in recent):
                return
            recent.insert(0, {'id': key or uuid.uuid4().hex, 'time': time.time(), 'kind': kind,
                             'title': title, 'body': body[:180], 'url': url, 'delivery': 'pending'})
            del recent[30:]

    def _seat_notification(self, title, kind='assignment'):
        current = self.snapshot()['reservation']
        if current:
            self._notify(kind, title, f"{current['roomName']} {current['seatNo']}번 · {title}")

    def _with_booking_time(self, reservation):
        previous = self.snapshot()['reservation']
        # Keep our verified booking timestamp if the provider omits it on later reads.
        if (reservation and previous and reservation['id'] == previous['id']
                and reservation.get('seatId') is not None
                and str(reservation['seatId']) == str(previous.get('seatId'))
                and reservation.get('startedAt') is None):
            reservation['startedAt'] = previous.get('startedAt')
        return reservation

    def _save(self):
        with self.lock:
            self.store.save(self.state['targets'], self.state['running'], self.state['repeat'],
                            self.state['repeatControl'], self.state['autoRenew'], self.state['renewNightUntil'],
                            {'scheduledBooking': self.state['scheduledBooking'], 'notifications': self.state['notifications'],
                             'autoRenewDisabledId': self.state['autoRenewDisabledId']})

    def _failure(self, error):
        self._notify('failure', getattr(error, 'notification_title', '좌석 작업에 실패했습니다'), str(error))
        self._update(error=str(error), reservationFresh=False)
        if error.expired:
            self._disconnect_client()
            self._update(connected=False, nextCheck=None)
            self._event('도서관 연결이 만료되어 대기를 일시 중지했습니다. 다시 로그인해 주세요.')
        if error.uncertain:
            self._update(running=False, repeat=None, repeatControl={'observedId': None, 'paused': True})
            self._save()
            self._event('요청 결과가 불명확해 자동 예약을 중지했습니다. 내 좌석을 확인한 뒤 다시 시작해 주세요.')
        self._save()

    def _disconnect_client(self):
        if self.client:
            self.client.close()
        self.client = None

    def connect(self, username, password):
        with self.lock:
            if self.state['connecting']:
                raise LibraryError('이미 로그인 중입니다. 잠시 기다려 주세요.')
            self.state['connecting'] = True
            self.state['error'] = None
        self.login_thread = threading.Thread(target=self._login, args=(username, password), daemon=True)
        self.login_thread.start()

    def _login(self, username, password):
        client = None
        try:
            from library_auth import get_token_automatically
            token, _, cookies = get_token_automatically(username, password, headless=True)
            password = None
            if not token:
                raise LibraryError('로그인하지 못했습니다. 계정 정보 또는 서버의 도서관 접속 상태를 확인해 주세요.')
            client = LibraryClient(token, cookies)
            reservation = client.reservation()
            with self.operation:
                self._disconnect_client()
                self.client, client = client, None
                self._update(connected=True, reservation=reservation, reservationFresh=True, error=None)
                self._event('도서관 계정을 연결했습니다.')
                self.wake.set()
        except LibraryError as error:
            self._update(error=str(error))
        except Exception:
            self._update(error='로그인에 실패했습니다. 서버의 Chrome 설치 상태를 확인해 주세요.')
        finally:
            if client:
                client.close()
            self._update(connecting=False)

    def disconnect(self):
        with self.operation:
            if self.snapshot()['connecting']:
                raise LibraryError('로그인이 끝난 뒤 연결을 해제해 주세요.')
            self._disconnect_client()
            job = self.snapshot()['scheduledBooking']
            if job and job['status'] in {'pending', 'working'}:
                self._update(scheduledBooking={**job, 'status': 'cancelled', 'result': '연결을 해제해 시간 예약을 취소했습니다.'})
            self._update(connected=False, running=False, reservation=None, reservationFresh=False,
                         seats=[], lastChecked=None, nextCheck=None, error=None, repeat=None, autoRenew=None)
            self._save()
            self._event('도서관 연결과 자동 예약을 종료했습니다.')

    def set_wait(self, targets, running):
        if not isinstance(targets, list) or len(targets) > MAX_TARGETS or any(not valid_seat_key(key) for key in targets):
            raise LibraryError('열람실 좌석을 최대 50개까지 선택해 주세요.')
        if type(running) is not bool:
            raise LibraryError('올바른 대기 상태를 지정해 주세요.')
        if running and not targets:
            raise LibraryError('대기할 좌석을 먼저 선택해 주세요.')
        with self.operation:
            was_running = self.snapshot()['running']
            if running:
                if not self.client:
                    raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
                try:
                    reservation = self._read_reservation()
                except LibraryError as error:
                    self._failure(error)
                    raise
                self._update(reservation=reservation, reservationFresh=True)
                if reservation:
                    self._validate_switch_source(reservation)
                try:
                    available = self._read_seats({int(key.split(':')[0]) for key in targets})
                except LibraryError as error:
                    self._failure(error)
                    raise
                known = {seat['key'] for seat in available if seat['id']}
                if any(key not in known for key in targets):
                    raise LibraryError('좌석 목록이 변경되었습니다. 새로고침 후 다시 선택해 주세요.')
                self._merge_seats(available)
            self._update(targets=list(dict.fromkeys(targets)), running=running, error=None)
            self._update(interval=self.poll_interval(), nextCheck=time.time())
            self._save()
            self._event('대기 좌석을 변경했습니다.' if running and was_running else
                        '서버에서 자동 예약을 시작했습니다.' if running else '자동 예약을 중지했습니다.')
            self.wake.set()

    def update_wait(self, key, enabled):
        """Edit the current job atomically without restarting a completed job."""
        if not valid_seat_key(key) or type(enabled) is not bool:
            raise LibraryError('변경할 좌석과 대기 상태를 확인해 주세요.')
        with self.operation:
            state = self.snapshot()
            if not state['running']:
                raise LibraryError('대기가 이미 종료되었습니다. 내 좌석을 확인해 주세요.')
            targets = state['targets']
            if enabled:
                if key not in targets:
                    self.set_wait([*targets, key], True)
                return
            if key not in targets:
                return
            remaining = [target for target in targets if target != key]
            # Removing a target needs no provider request, even during a read failure.
            self._update(targets=remaining, running=bool(remaining))
            self._update(interval=self.poll_interval(), nextCheck=time.time())
            self._save()
            self._event('대기에서 좌석을 제외했습니다.' if remaining else '마지막 좌석을 제외해 대기를 종료했습니다.')
            self.wake.set()

    def _read_seats(self, room_ids):
        seats = []
        for room_id in ROOMS:
            if room_id not in room_ids:
                continue
            items = self.client.seats(room_id)
            checked = time.time()
            for item in items:
                code = str(item.get('code', ''))
                if not valid_seat_key(f'{room_id}:{code}'):
                    continue
                occupied = item.get('isOccupied')
                seats.append({
                    'key': f'{room_id}:{code}', 'roomId': room_id, 'roomName': ROOMS[room_id],
                    'number': code, 'id': item.get('seatId') or item.get('id'),
                    'occupied': occupied if type(occupied) is bool else None,
                    'remainingTime': item.get('remainingTime'),
                    'single': (room_id, code) in SINGLE_SEATS, 'checkedAt': checked,
                })
        return seats

    def _merge_seats(self, seats, room_ids=None):
        replaced = set(room_ids) if room_ids is not None else {item['roomId'] for item in seats}
        others = [item for item in self.snapshot()['seats'] if item['roomId'] not in replaced]
        order = {room: i for i, room in enumerate(ROOMS)}
        self._update(seats=sorted(others + seats, key=lambda item: (order[item['roomId']], int(item['number']))))

    def poll_interval(self):
        state = self.snapshot()
        if not state['running'] or state['error']:
            return self.interval
        for seat in state['seats']:
            if seat['key'] not in state['targets']:
                continue
            if seat['occupied'] is False:
                return 1
            remaining = seat.get('remainingTime')
            if remaining is None or isinstance(remaining, bool) or (isinstance(remaining, str) and not remaining.strip()):
                continue
            try:
                minutes = float(remaining)
            except (TypeError, ValueError):
                continue
            if seat['occupied'] is True and math.isfinite(minutes) and 0 <= minutes <= 1:
                return 1
        return self.interval

    def tick(self, *, targets_only=False, allow_repeat=True, reservation_only=False):
        with self.operation:
            if allow_repeat and self._check_scheduled():
                return
            if not self.client:
                return
            started = time.time()
            try:
                reservation = self._read_reservation()
                self._update(reservation=reservation, reservationFresh=True)
                self._check_repeat(reservation, allow_repeat=allow_repeat)
                reservation = self.snapshot()['reservation']
                self._auto_repeat(reservation)
                self._default_auto_renew(reservation)
                self._check_auto_renew(reservation, allow_run=allow_repeat)
                current = self.snapshot()
                if reservation_only and not current['running']:
                    self._update(error=None)
                    return
                room_ids = {int(key.split(':')[0]) for key in current['targets']} if targets_only and current['running'] else set(ROOMS)
                seats = self._read_seats(room_ids)
                self._merge_seats(seats, room_ids)
                if room_ids == set(ROOMS):
                    self._update(catalogVersion=1)
                self._update(lastChecked=time.time(), error=None)
                state = self.snapshot()
                if state['running']:
                    for key in state['targets']:
                        seat = next((item for item in seats if item['key'] == key), None)
                        if seat and seat['occupied'] is False and seat['id']:
                            try:
                                # Recheck the held seat immediately before booking or switching.
                                current = self._read_reservation()
                                self._update(reservation=current, reservationFresh=True)
                                if current and str(current.get('seatId')) == str(seat['id']):
                                    continue
                                if current:
                                    self._switch_reserve(seat, current, auto_confirm=True)
                                else:
                                    self._reserve(seat)
                                break
                            except LibraryError as error:
                                if error.expired or error.uncertain or not self.snapshot()['running']:
                                    raise
                                self._update(error=str(error))
                                self._notify('failure', '대기 좌석 예약에 실패했습니다', str(error))
                                self._save()
                                break  # Try one target per tick, including its recovery if needed.
            except LibraryError as error:
                self._failure(error)
            finally:
                interval = self.poll_interval()
                next_check = max(started + interval, time.time())
                repeat = self.snapshot()['repeat']
                if repeat and not self.snapshot()['error']:
                    next_check = min(next_check, max(time.time() + 1, repeat['dueAt']))
                renewal = self.snapshot()['autoRenew']
                if renewal and renewal['status'] in {'scheduled', 'retry'}:
                    due = max(renewal['dueAt'], renewal.get('retryAt') or 0, self.snapshot()['renewNightUntil'] or 0)
                    next_check = min(next_check, max(time.time() + 1, due))
                job = self.snapshot()['scheduledBooking']
                if job and job['status'] == 'pending':
                    next_check = min(next_check, max(time.time() + 1, job['dueAt']))
                self._update(interval=interval, nextCheck=next_check if self.client else None)

    def set_schedule(self, key, due_at):
        with self.operation:
            window = schedule_window()
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            if not valid_seat_key(key) or type(due_at) not in (int, float) or not math.isfinite(due_at):
                raise LibraryError('시간과 좌석을 확인해 주세요.')
            if not window['open'] or not window['closesAt'] <= due_at <= window['closesAt'] + 410 * 60 or due_at % 600:
                raise LibraryError('전날 낮 12시부터 당일 오전 5시 전까지, 오전 5시~11시 50분을 10분 단위로 선택해 주세요.')
            room = int(key.split(':')[0])
            if room not in self.nfc_tags:
                raise LibraryError('자동 배정확정을 지원하는 열람실만 시간 예약할 수 있습니다.')
            seat = next((seat for seat in self._read_seats({room}) if seat['key'] == key and seat['id']), None)
            if not seat:
                raise LibraryError('사용할 수 있는 좌석을 선택해 주세요.')
            self._update(scheduledBooking={'id': uuid.uuid4().hex, 'key': key, 'roomId': room,
                         'seatId': seat['id'], 'roomName': seat['roomName'], 'number': seat['number'],
                         'dueAt': due_at, 'status': 'pending', 'stage': None, 'result': None}, error=None)
            self._event(f"{seat['roomName']} {seat['number']}번 시간 예약을 등록했습니다.")
            self._save()
            self.wake.set()

    def cancel_schedule(self, expected_id):
        with self.operation:
            job = self.snapshot()['scheduledBooking']
            if not job or job['id'] != expected_id or job['status'] != 'pending':
                raise LibraryError('시간 예약 상태가 바뀌었습니다. 다시 확인해 주세요.')
            self._update(scheduledBooking={**job, 'status': 'cancelled', 'result': '시간 예약을 취소했습니다.'})
            self._event('시간 예약을 취소했습니다. 현재 좌석은 유지됩니다.')
            self._save()

    def _check_scheduled(self):
        job = self.snapshot()['scheduledBooking']
        if not job or job['status'] not in {'pending', 'working'} or (job['status'] == 'pending' and time.time() < job['dueAt']):
            return False
        stage = job.get('stage') or '실행 준비'
        self.quiet_notifications = True
        try:
            if job['status'] == 'working':
                raise LibraryError('이전 실행 결과를 확인하지 못해 종료했습니다. 내 좌석을 확인해 주세요.')
            if time.time() - job['dueAt'] > 120:
                raise LibraryError('실행 시간을 지나 종료했습니다.')
            self._update(scheduledBooking={**job, 'status': 'working', 'stage': stage})
            self._save()  # Claim once durably, before even the first external request.
            if not self.client:
                raise LibraryError('도서관 연결이 만료되었습니다.')
            current = self._read_reservation()
            self._update(reservation=current, reservationFresh=True)
            if current:
                raise LibraryError('이미 이용 중인 좌석이 있어 현재 좌석을 유지했습니다.')
            if job['roomId'] not in self.nfc_tags:
                raise LibraryError('이 열람실의 배정확정 설정을 확인할 수 없습니다.')
            seat = next((seat for seat in self._read_seats({job['roomId']}) if seat['key'] == job['key']), None)
            if not seat or seat['occupied'] is not False or str(seat['id']) != str(job['seatId']):
                raise LibraryError('선택한 좌석이 비어 있지 않거나 좌석 정보가 바뀌었습니다.')
            self._update(running=False, targets=[], repeat=None, autoRenew=None,
                         repeatControl={'observedId': None, 'paused': True})
            stage = '좌석 예약'
            self._update(scheduledBooking={**job, 'status': 'working', 'stage': stage})
            self._save()
            booked_at = time.time()
            if booked_at - job['dueAt'] > 120:
                raise LibraryError('실행 시간을 지나 종료했습니다.')
            self.client.reserve(seat['id'])
            self._confirm_booking(seat, booked_at, start_repeat=False)
            stage = '배정확정'
            self._update(scheduledBooking={**job, 'status': 'working', 'stage': stage})
            self._save()
            self.confirm(self.snapshot()['reservation']['id'])
            stage = '자동 연장 설정'
            self._update(scheduledBooking={**job, 'status': 'working', 'stage': stage})
            self._save()
            self.set_auto_renew(True, self.snapshot()['reservation']['id'])
        except LibraryError as error:
            self._update(scheduledBooking={**job, 'status': 'failed', 'stage': stage,
                         'finishedAt': time.time(), 'result': str(error)})
            self._failure(error)
            title, body, kind = f'시간 예약 · {stage} 실패', str(error), 'failure'
        else:
            self._update(scheduledBooking={**job, 'status': 'succeeded', 'stage': '완료',
                         'finishedAt': time.time(), 'result': '배정확정 · 자동 연장 켜짐'}, error=None)
            title, body, kind = '시간 예약에 성공했습니다', f"{job['roomName']} {job['number']}번 · 배정확정 · 자동 연장 켜짐", 'assignment'
        finally:
            self.quiet_notifications = False
        self._notify(kind, title, body, url='/?tab=schedule', key=job['id'])
        self._event(f'{title} · {body}')
        self._save()
        self._update(nextCheck=time.time() + self.interval)
        return True

    def _auto_repeat(self, reservation, *, force=False):
        # Compatibility for old workers: never arm or execute a temporary rebooking.
        self._update(repeat=None, repeatControl={'observedId': None, 'paused': True})

    def _check_repeat(self, reservation, *, allow_repeat):
        self._auto_repeat(reservation)

    def set_repeat(self, enabled, expected_id):
        if type(enabled) is not bool or not isinstance(expected_id, str):
            raise LibraryError('올바른 자동 재예약 설정을 지정해 주세요.')
        if enabled:
            raise LibraryError('임시배정 자동 재예약은 비활성화되었습니다.')
        with self.operation:
            self._auto_repeat(None)
            self._save()

    def _reserve(self, seat):
        previous = self.snapshot()
        # Persist a disarmed state before a write, including when the process dies mid-request.
        self._update(running=False, repeat=None,
                     repeatControl={'observedId': None, 'paused': True})
        self._event(f"{seat['roomName']} {seat['number']}번 예약을 요청합니다. 결과가 표시되지 않으면 내 좌석을 새로고침해 주세요.")
        self._save()
        try:
            booked_at = time.time()
            self.client.reserve(seat['id'])
        except LibraryError as error:
            if not error.uncertain and not error.expired:
                self._update(running=previous['running'], repeat=previous['repeat'],
                             repeatControl=previous['repeatControl'])
                self._save()
            raise
        # Stop and persist BEFORE re-querying. A failed read must never lead to another write.
        self._update(running=False, targets=[])
        self._save()
        self._event(f"{seat['roomName']} {seat['number']}번 예약 요청이 접수되었습니다. 배정 상태를 확인합니다.")
        self._confirm_booking(seat, booked_at, start_repeat=False)
        if int(seat['roomId']) in self.nfc_tags:
            self.confirm(self.snapshot()['reservation']['id'])
        else:
            self._seat_notification('좌석 예약에 성공했습니다')
        self._save()
        self._event('좌석 예약과 배정확정을 완료했습니다.' if int(seat['roomId']) in self.nfc_tags else
                    '좌석을 예약했습니다. 공식 앱에서 NFC 인증을 진행해 주세요.')

    def _confirm_booking(self, seat, booked_at, *, start_repeat=True, previous_id=None):
        reservation = self.client.reservation()
        if (reservation and reservation['state'] == 'TEMP_CHARGE'
                and str(reservation.get('seatId')) == str(seat['id'])
                and reservation.get('startedAt') is None):
            reservation['startedAt'] = booked_at
        self._update(reservation=reservation, reservationFresh=True)
        if (not reservation or reservation['state'] not in ACTIVE_STATES
                or reservation['id'] == previous_id
                or str(reservation.get('seatId')) != str(seat['id'])
                or str(reservation.get('roomId')) != str(seat['roomId'])):
            raise LibraryError('예약 결과를 확인할 수 없습니다. 내 좌석을 새로고침해 주세요.')
        if start_repeat:
            self._auto_repeat(reservation, force=True)
        else:
            # Automatic confirmation must never briefly arm the nine-minute repeat.
            self._update(repeat=None, repeatControl={'observedId': reservation['id'], 'paused': True})
        self._save()

    def _confirm_switched_seat(self, *, recovered=False):
        reservation = self.snapshot()['reservation']
        try:
            self.confirm(reservation['id'])
        except LibraryError as error:
            label = '원래 좌석 복구' if recovered else '새 좌석 예약'
            message = f'{label} 후 배정확정을 확인하지 못해 대기를 중지했습니다. 내 좌석에서 배정 상태를 확인해 주세요. {error}'
            self._event(message)
            raise LibraryError(message, expired=error.expired, uncertain=error.uncertain) from error

    @staticmethod
    def _validate_switch_source(reservation):
        if (reservation['state'] not in ACTIVE_STATES
                or not valid_seat_key(f"{reservation.get('roomId')}:{reservation.get('seatNo')}")
                or not str(reservation.get('seatId')).isdigit()
                or not str(reservation.get('roomId')).isdigit()
                or int(reservation['roomId']) not in ROOMS):
            raise LibraryError('기존 좌석 상태나 식별 정보를 확인할 수 없어 갈아타기를 중지했습니다.')

    def _switch_reserve(self, seat, current, *, auto_confirm=True):
        self._validate_switch_source(current)
        previous = self.snapshot()
        # Persist before releasing: neither a crash nor an unknown result may replay the switch.
        self._update(running=False, targets=[], repeat=None,
                     repeatControl={'observedId': current['id'], 'paused': True})
        self._event('기존 좌석을 해제하고 선택한 빈 좌석으로 갈아타기를 시도합니다.')
        self._save()
        self.client.release(current)
        after_cancel = self.client.reservation()
        self._update(reservation=after_cancel, reservationFresh=True)
        if after_cancel:
            raise LibraryError('기존 좌석 해제를 확인할 수 없어 갈아타기를 중지했습니다.')
        booked_at = time.time()
        try:
            self.client.reserve(seat['id'])
        except LibraryError as error:
            if error.expired or error.uncertain:
                raise
            # A definite rejection permits recovery only after verifying no seat was assigned.
            assigned = self.client.reservation()
            self._update(reservation=assigned, reservationFresh=True)
            if assigned:
                raise LibraryError('새 좌석 요청 후 배정 상태가 변경되었습니다. 내 좌석을 확인해 주세요.') from error
            original = {'id': current['seatId'], 'roomId': int(current['roomId']),
                        'roomName': current['roomName'], 'number': current['seatNo']}
            confirm_original = auto_confirm and original['roomId'] in self.nfc_tags
            recovered_at = time.time()
            try:
                self.client.reserve(original['id'])
                self._confirm_booking(original, recovered_at, start_repeat=not confirm_original,
                                      previous_id=current['id'])
            except LibraryError as recovery_error:
                self._event('새 좌석 예약과 원래 좌석 재예약에 실패했습니다. 내 좌석을 확인해 주세요.')
                raise LibraryError('새 좌석 예약과 원래 좌석 재예약에 실패했습니다. 내 좌석을 확인해 주세요.',
                                   expired=recovery_error.expired, uncertain=recovery_error.uncertain) from recovery_error
            # A failed confirmation is not a failed recovery: preserve the acquired
            # temporary seat and stop, without cancelling it or trying other targets.
            if confirm_original:
                self._confirm_switched_seat(recovered=True)
            self._update(running=previous['running'], targets=previous['targets'])
            self._save()
            message = ('새 좌석 예약에 실패해 원래 좌석을 다시 예약하고 배정확정까지 완료했습니다.'
                       if confirm_original else '새 좌석 예약에 실패해 원래 좌석을 다시 예약했습니다.')
            if auto_confirm and not confirm_original:
                message += ' 이 열람실은 공식 앱에서 NFC 인증이 필요합니다.'
            self._event(message + (' 예약 대기는 계속됩니다.' if previous['running'] else ''))
            self._notify('failure', '갈아타기 실패 · 원래 좌석 복구', message)
            self._save()
            return
        confirm_target = auto_confirm and int(seat['roomId']) in self.nfc_tags
        self._confirm_booking(seat, booked_at, start_repeat=not confirm_target, previous_id=current['id'])
        if confirm_target:
            self._confirm_switched_seat()
        else:
            self._seat_notification('갈아타기에 성공했습니다')
        self._save()
        self._event('선택한 좌석으로 갈아타기와 배정확정을 완료했습니다. 나머지 예약 대기는 종료했습니다.'
                    if confirm_target else '선택한 좌석으로 갈아타기를 완료했습니다. 나머지 예약 대기는 종료했습니다.'
                    + (' 이 열람실은 공식 앱에서 NFC 인증이 필요합니다.' if auto_confirm else ''))

    def reserve(self, key):
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            if not valid_seat_key(key):
                raise LibraryError('올바른 좌석을 선택해 주세요.')
            try:
                current = self._read_reservation()
                self._update(reservation=current, reservationFresh=True)
                room, code = key.split(':')
                raw = next((item for item in self.client.seats(int(room)) if str(item.get('code')) == code), None)
                if not raw or raw.get('isOccupied') is not False:
                    raise LibraryError('현재 예약 가능한 빈 좌석이 아닙니다.')
                seat_id = raw.get('seatId') or raw.get('id')
                if not seat_id:
                    raise LibraryError('좌석 식별 정보를 확인할 수 없습니다.')
                seat = {'id': seat_id, 'roomId': int(room), 'roomName': ROOMS[int(room)], 'number': code}
                if current:
                    if str(current.get('seatId')) == str(seat_id):
                        raise LibraryError('이미 배정된 좌석입니다.')
                    # Recheck before returning an existing seat, including NFC state changes.
                    current = self._read_reservation()
                    self._update(reservation=current, reservationFresh=True)
                if current:
                    self._switch_reserve(seat, current)
                else:
                    self._reserve(seat)
                self.wake.set()
            except LibraryError as error:
                self._failure(error)
                raise

    @staticmethod
    def _same_renewal_seat(current, original):
        return bool(current and original and current['id'] == original['id']
                    and current['state'] in {'CHARGE', 'IN_USE'}
                    and str(current.get('seatId')) == str(original.get('seatId'))
                    and str(current.get('roomId')) == str(original.get('roomId')))

    def _renewal_source(self, current, expected_id):
        if not current or current['id'] != expected_id or current['state'] not in {'CHARGE', 'IN_USE'}:
            raise LibraryError('확정된 내 좌석이 변경되었습니다. 다시 확인해 주세요.')
        self._validate_switch_source(current)
        if int(current['roomId']) not in self.nfc_tags:
            raise LibraryError('이 열람실은 공식 앱에서 NFC로 연장해 주세요.')
        end = reservation_time(current.get('endTime'))
        if end is None or end <= time.time():
            raise LibraryError('좌석 종료 시간을 확인할 수 없거나 이용 시간이 끝났습니다.')
        return end

    def _renewal_plan(self, current):
        end = self._renewal_source(current, current['id'])
        plan = {'reservationId': current['id'], 'seatId': current['seatId'], 'roomId': current['roomId'],
                'endTime': current['endTime'], 'dueAt': max(end - AUTO_RENEW_REMAINING, current.get('renewableAt') or 0),
                'status': 'scheduled', 'retryAt': None, 'message': ''}
        if (self.snapshot()['renewNightUntil'] or 0) > time.time():
            plan.update(status='night', retryAt=self.snapshot()['renewNightUntil'],
                        message='야간 연장 중지 · 오전 5시 이후 현재 좌석을 다시 확인합니다.')
        return plan

    def set_auto_renew(self, enabled, expected_id):
        if type(enabled) is not bool or not isinstance(expected_id, str):
            raise LibraryError('자동 연장 설정과 내 좌석을 확인해 주세요.')
        with self.operation:
            if not enabled:
                state = self.snapshot()
                if ((state['reservation'] and state['reservation']['id'] != expected_id)
                        or (state['autoRenew'] and state['autoRenew']['reservationId'] != expected_id)):
                    raise LibraryError('내 좌석이 변경되었습니다. 다시 확인해 주세요.')
                self._update(autoRenew=None, autoRenewDisabledId=expected_id)
            else:
                if not self.client:
                    raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
                current = self._read_reservation()
                self._update(reservation=current, reservationFresh=True)
                self._renewal_source(current, expected_id)
                plan = self._renewal_plan(current)
                self._update(autoRenew=plan, autoRenewDisabledId=None, error=None, nextCheck=time.time())
            self._save()
            self._event('자동 연장을 켰습니다.' if enabled else '자동 연장을 껐습니다. 현재 좌석은 유지됩니다.')
            self.wake.set()

    def _default_auto_renew(self, current):
        """Arm verified confirmed bookings, preserving opt-outs and unfinished writes."""
        if not current or current['state'] not in {'CHARGE', 'IN_USE'}:
            return
        state = self.snapshot()
        plan = state['autoRenew']
        if plan and (plan['reservationId'] == current['id'] or plan['status'] == 'working'):
            return
        if state['autoRenewDisabledId'] == current['id']:
            return
        try:
            plan = self._renewal_plan(current)
        except LibraryError:
            return
        self._update(autoRenew=plan)
        self._save()

    def _check_auto_renew(self, current, *, allow_run):
        plan = self.snapshot()['autoRenew']
        if not plan:
            return
        original = {'id': plan['reservationId'], 'seatId': plan['seatId'], 'roomId': plan['roomId']}
        if not self._same_renewal_seat(current, original):
            self._update(autoRenew=None)
            self._save()
            self._event('좌석이 해제되거나 배정이 변경되어 자동 연장을 종료했습니다.')
            return
        end = reservation_time(current.get('endTime'))
        if end is not None and end <= time.time():
            self._update(autoRenew=None)
            self._save()
            self._event('좌석 이용 시간이 끝나 자동 연장을 종료했습니다.')
            return
        if plan['status'] == 'working':
            self._update(autoRenew={**plan, 'status': 'paused', 'message': '이전 요청 결과를 확인해 주세요. 자동 연장은 일시 중지되었습니다.'})
            self._save()
            return
        if plan['status'] == 'paused' or not allow_run:
            return
        if (self.snapshot()['renewNightUntil'] or 0) > time.time():
            return
        if current['endTime'] != plan['endTime'] or plan['status'] == 'night':
            try:
                plan = self._renewal_plan(current)
            except LibraryError:
                self._update(autoRenew={**plan, 'status': 'paused', 'message': '연장할 좌석 정보를 다시 확인해 주세요.'})
                self._save()
                return
            self._update(autoRenew=plan)
            self._save()
        if time.time() >= max(plan['dueAt'], plan.get('retryAt') or 0):
            self._renew_current(plan['reservationId'], automatic=True)

    def _renewal_succeeded(self, original, verified):
        before, after = reservation_time(original.get('endTime')), reservation_time((verified or {}).get('endTime'))
        return self._same_renewal_seat(verified, original) and before is not None and after is not None and after > before

    def _finish_renewal(self, original, verified):
        if not self._renewal_succeeded(original, verified):
            raise LibraryError('연장 후 종료 시간 증가를 확인하지 못했습니다. 공식 앱에서 확인해 주세요.', uncertain=True)
        self._update(reservation=verified, reservationFresh=True, error=None)
        if self.snapshot()['autoRenew']:
            plan = self._renewal_plan(verified)
            if plan['dueAt'] <= time.time():
                plan.update(status='paused', message='연장 후 이용 시간이 짧아 자동 연장을 멈췄습니다. 운영시간을 확인해 주세요.')
            self._update(autoRenew=plan)
        self._event('좌석 연장을 완료했습니다.')
        self._seat_notification('좌석 연장에 성공했습니다', 'renewal')
        self._save()

    def _reassign_for_renewal(self, current):
        if closed_until():
            raise LibraryError('23시~05시에는 연장 횟수 소진으로 좌석을 반납·재배정하지 않습니다.')
        previous = self.snapshot()
        self.reassign(current['id'], for_renewal=True)
        if self.snapshot()['autoRenew'] and self.snapshot()['autoRenew']['status'] == 'scheduled':
            self._update(running=previous['running'], targets=previous['targets'])
            self._event('연장 횟수를 소진해 같은 좌석을 재배정·확정했습니다. 자동 연장을 계속합니다.')
            self._save()

    def _renew_current(self, expected_id, *, automatic=False):
        plan = self.snapshot()['autoRenew']
        stage = None
        try:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            if automatic and (self.snapshot()['renewNightUntil'] or 0) > time.time():
                return
            current = self._read_reservation()
            self._update(reservation=current, reservationFresh=True)
            end = self._renewal_source(current, expected_id)
            threshold = AUTO_RENEW_REMAINING if automatic else 120 * 60
            if end - time.time() > threshold or (current.get('renewableAt') or 0) > time.time():
                if automatic:
                    self._update(autoRenew=self._renewal_plan(current))
                    self._save()
                    return
                raise LibraryError('연장은 좌석 종료 2시간 전부터 가능합니다.')
            if renewal_count(current.get('renewableCnt')) == 0:
                if automatic:
                    stage = 'reassign'
                    self._reassign_for_renewal(current)
                    return
                raise LibraryError('연장 가능 횟수를 모두 사용했습니다.')
            if current.get('isRenewable') is False or current.get('isRenewalImpossible') is True:
                raise LibraryError('현재 도서관에서 연장을 허용하지 않습니다. 운영시간을 확인해 주세요.')
            if plan:
                self._update(autoRenew={**plan, 'status': 'working', 'message': '좌석 연장 확인 중'})
                self._save()
            stage = 'arrival'
            self.client.check_arrival(int(current['roomId']), self.nfc_tags[int(current['roomId'])])
            verified = self._read_reservation()
            self._update(reservation=verified, reservationFresh=True)
            if self._renewal_succeeded(current, verified):
                self._finish_renewal(current, verified)
                return
            if not self._same_renewal_seat(verified, current) or verified['endTime'] != current['endTime']:
                raise LibraryError('태그 확인 중 내 좌석이나 종료 시간이 변경되었습니다.', uncertain=True)
            if renewal_count(verified.get('renewableCnt')) == 0:
                if automatic:
                    stage = 'reassign'
                    self._reassign_for_renewal(verified)
                    return
                raise LibraryError('연장 가능 횟수를 모두 사용했습니다.')
            if verified.get('isRenewable') is False or verified.get('isRenewalImpossible') is True:
                raise LibraryError('현재 도서관에서 연장을 허용하지 않습니다.')
            stage = 'renew'
            try:
                self.client.renew_reservation(expected_id)
            except LibraryError as error:
                if error.expired:
                    raise
                try:
                    verified = self._read_reservation()
                    self._update(reservation=verified, reservationFresh=True)
                except LibraryError:
                    raise LibraryError('연장 요청 결과를 확인할 수 없습니다. 공식 앱에서 확인해 주세요.', uncertain=True) from None
                if self._renewal_succeeded(current, verified):
                    self._finish_renewal(current, verified)
                    return
                if (automatic and not error.uncertain and self._same_renewal_seat(verified, current)
                        and renewal_count(verified.get('renewableCnt')) == 0):
                    stage = 'reassign'
                    self._reassign_for_renewal(verified)
                    return
                raise error
            try:
                verified = self._read_reservation()
                self._update(reservation=verified, reservationFresh=True)
            except LibraryError:
                raise LibraryError('연장 후 배정 조회에 실패했습니다. 공식 앱에서 결과를 확인해 주세요.', uncertain=True) from None
            self._finish_renewal(current, verified)
        except LibraryError as error:
            night = closed_until() if automatic or stage is not None else None
            error.notification_title = '좌석 연장에 실패했습니다'
            if night:
                self._update(renewNightUntil=night)
            if plan and plan['reservationId'] == expected_id:
                status = 'paused' if error.uncertain or error.expired or stage == 'reassign' else 'retry'
                if night:
                    resetting = stage == 'reassign' and (self.snapshot()['autoRenew'] or {}).get('status') == 'working'
                    status = 'paused' if error.uncertain or resetting else 'night'
                message = ('야간 연장 결과 확인 필요 · 자동 연장 일시 중지' if night and status == 'paused' else
                           '야간 연장 실패 · 오전 5시까지 자동 연장을 다시 시도하지 않습니다.' if night else
                           '자동 연장 일시 중지 · 내 좌석을 확인해 주세요.' if status == 'paused' else
                           '연장 보류 · 5분 후 다시 확인합니다.')
                self._update(autoRenew={**(self.snapshot()['autoRenew'] or plan), 'status': status,
                                       'retryAt': night or time.time() + 300, 'message': message})
                self._event(message)
            self._failure(error)
            self._save()
            raise

    def renew(self, expected_id):
        if not isinstance(expected_id, str) or not re.fullmatch(r'[0-9]{1,20}', expected_id):
            raise LibraryError('연장할 배정 정보를 확인해 주세요.')
        with self.operation:
            self._renew_current(expected_id)

    def confirm(self, expected_id):
        """Confirm only the caller's current booking, under the same lock as rebooking."""
        if not isinstance(expected_id, str) or not re.fullmatch(r'[0-9]{1,20}', expected_id):
            raise LibraryError('배정 정보를 다시 확인해 주세요.')
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            try:
                current = self._read_reservation()
                self._update(reservation=current, reservationFresh=True)
                if not current or current['id'] != expected_id:
                    raise LibraryError('내 좌석이 변경되었습니다. 다시 확인해 주세요.')
                self._validate_switch_source(current)
                room = int(current['roomId'])
                if current['state'] == 'TEMP_CHARGE' and room not in self.nfc_tags:
                    raise LibraryError('이 열람실의 태그 정보가 아직 없습니다. 공식 앱에서 NFC 인증을 진행해 주세요.')

                # Persist before any write. Crashes or uncertain responses must not revive
                # rebooking or a switching job and release this seat during confirmation.
                self._update(repeat=None, running=False, targets=[], error=None,
                             repeatControl={'observedId': expected_id, 'paused': True})
                self._save()

                def matches(reservation):
                    return bool(reservation and reservation['id'] == expected_id
                                and str(reservation.get('seatId')) == str(current['seatId'])
                                and str(reservation.get('roomId')) == str(current['roomId']))

                def is_confirmed(reservation):
                    return matches(reservation) and reservation['state'] in {'CHARGE', 'IN_USE'}

                verified = current
                if not is_confirmed(current):
                    self.client.check_arrival(room, self.nfc_tags[room])
                    # The official app may have changed the booking during arrival checking.
                    verified = self._read_reservation()
                    self._update(reservation=verified, reservationFresh=True)
                    if not matches(verified) or verified['state'] not in ACTIVE_STATES:
                        raise LibraryError('태그 확인 중 내 좌석이 변경되었습니다. 다시 확인해 주세요.')
                    if not is_confirmed(verified):
                        try:
                            self.client.confirm_reservation(expected_id)
                        except LibraryError as error:
                            if not error.uncertain or error.expired:
                                raise
                            # Reconcile an ambiguous write once, without replaying it.
                            verified = self._read_reservation()
                            self._update(reservation=verified, reservationFresh=True)
                            if not is_confirmed(verified):
                                raise LibraryError('배정확정 결과를 확인하지 못했습니다. 자동 재예약과 대기는 중지되었습니다. 새로고침 후 공식 앱에서도 확인해 주세요.', uncertain=True) from None
                        else:
                            verified = self._read_reservation()
                            self._update(reservation=verified, reservationFresh=True)
                if not is_confirmed(verified):
                    raise LibraryError('아직 배정확정이 확인되지 않았습니다. 자동 재예약과 대기는 중지되었습니다. 새로고침 후 공식 앱에서도 확인해 주세요.')
                self._update(reservation=verified, reservationFresh=True, error=None)
                self._default_auto_renew(verified)
                self._event('배정이 확정되었습니다. 자동 재예약과 갈아타기 대기를 종료했습니다.')
                if current['state'] == 'TEMP_CHARGE':
                    self._seat_notification('배정확정에 성공했습니다')
                self._save()
                self.wake.set()
            except LibraryError as error:
                self._failure(error)
                raise

    def reassign(self, expected_id, *, for_renewal=False):
        """One explicit transaction: return, reserve the same seat, then confirm it."""
        if not isinstance(expected_id, str) or not re.fullmatch(r'[0-9]{1,20}', expected_id):
            raise LibraryError('배정 정보를 다시 확인해 주세요.')
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            stage = None
            previous_renewal = self.snapshot()['autoRenew']
            try:
                current = self._read_reservation()
                self._update(reservation=current, reservationFresh=True)
                if not current or current['id'] != expected_id or current['state'] not in {'CHARGE', 'IN_USE'}:
                    raise LibraryError('확정된 내 좌석이 변경되었습니다. 다시 확인해 주세요.')
                if (not str(current.get('seatId')).isdigit() or not str(current.get('roomId')).isdigit()
                        or not valid_seat_key(f"{current.get('roomId')}:{current.get('seatNo')}")):
                    raise LibraryError('현재 좌석 정보를 확인할 수 없어 반납하지 않았습니다.')
                if int(current['roomId']) not in self.nfc_tags:
                    raise LibraryError('이 열람실은 자동 배정확정을 지원하지 않아 반납하지 않았습니다.')
                if for_renewal:
                    end = self._renewal_source(current, expected_id)
                    if (closed_until() or renewal_count(current.get('renewableCnt')) != 0
                            or end - time.time() > AUTO_RENEW_REMAINING):
                        raise LibraryError('연장 횟수 소진이나 재배정 가능 시간을 확인할 수 없어 반납하지 않았습니다.')

                # The cloud lease and this lock span all three steps. Never arm the
                # normal temporary repeat between reserve and confirm, including crashes.
                self._update(running=False, targets=[], repeat=None, error=None,
                             repeatControl={'observedId': expected_id, 'paused': True})
                if previous_renewal:
                    self._update(autoRenew={**previous_renewal, 'status': 'working', 'message': '좌석 재배정 확인 중'})
                self._save()
                stage = '좌석 반납'
                self._event('같은 좌석의 재배정·확정을 위해 좌석을 반납합니다.')
                self.client.release(current)
                after_return = self._read_reservation()
                self._update(reservation=after_return, reservationFresh=True)
                if after_return:
                    raise LibraryError('좌석 반납을 확인하지 못해 재예약하지 않았습니다.')

                stage = '같은 좌석 재예약'
                booked_at = time.time()
                self.client.reserve(current['seatId'])
                booked = self._read_reservation()
                if (booked and booked['state'] == 'TEMP_CHARGE' and booked.get('startedAt') is None):
                    booked['startedAt'] = booked_at
                self._update(reservation=booked, reservationFresh=True)
                if (not booked or booked['id'] == expected_id or booked['state'] not in ACTIVE_STATES
                        or str(booked.get('seatId')) != str(current['seatId'])
                        or str(booked.get('roomId')) != str(current['roomId'])):
                    raise LibraryError('같은 좌석의 새 배정을 확인하지 못했습니다. 내 좌석을 확인해 주세요.')
                self._update(repeatControl={'observedId': booked['id'], 'paused': True})
                self._save()

                stage = '배정 확정'
                self.confirm(booked['id'])
                if previous_renewal:
                    renewed = self.snapshot()['reservation']
                    plan = self._renewal_plan(renewed)
                    if (renewal_count(renewed.get('renewableCnt')) or 0) <= 0 or plan['dueAt'] <= time.time():
                        plan.update(status='paused', message='재배정 후 연장 횟수 또는 이용 시간을 확인해 주세요.')
                    self._update(autoRenew=plan)
                self._event('같은 좌석의 재배정과 배정확정을 완료했습니다.')
                self._save()
            except LibraryError as error:
                if stage is None:
                    self._failure(error)
                    raise
                failure = LibraryError(f'{stage} 단계에서 중지했습니다. {error}',
                                       expired=error.expired, uncertain=error.uncertain)
                self._failure(failure)
                # Show what is actually held after a failure. This read never resumes
                # later write steps, even if an ambiguous request happened to succeed.
                if self.client:
                    try:
                        self._update(reservation=self._read_reservation(), reservationFresh=True)
                    except LibraryError as read_error:
                        if read_error.expired:
                            self._failure(read_error)
                self._update(error=str(failure))
                self._event(str(failure))
                self._save()
                raise failure from None

    def release(self, expected_id, expected_state):
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            try:
                reservation = self._read_reservation()
                if not reservation or reservation['id'] != expected_id or reservation['state'] != expected_state:
                    self._update(reservation=reservation, reservationFresh=True)
                    raise LibraryError('좌석 상태가 변경되었습니다. 새로고침 후 다시 확인해 주세요.')
                self._update(running=False, targets=[], repeat=None,
                             repeatControl={'observedId': reservation['id'], 'paused': True}, autoRenew=None)
                self._save()
                self.client.release(reservation)
                self._update(reservation=self.client.reservation(), reservationFresh=True)
                self._event('예약 취소·반납 요청을 처리했습니다.')
                self.wake.set()
            except LibraryError as error:
                self._failure(error)
                raise

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name='seat-worker', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            self.wake.clear()
            try:
                self.tick()
            except Exception:
                # Never silently lose the background worker or auto-retry an unknown write outcome.
                with self.operation:
                    self._update(error='서버 처리 중 오류가 발생해 자동 예약을 중지했습니다.', running=False, repeat=None,
                                 repeatControl={'observedId': None, 'paused': True})
                    self._save()
            self.wake.wait(max(0, (self.snapshot()['nextCheck'] or time.time() + self.interval) - time.time()))

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=30)


class DemoClient:
    """Local preview only. Never sends requests to the library."""
    def __init__(self):
        self.current = None
        self.sequence = 0

    def close(self):
        pass

    def reservation(self):
        current = copy.deepcopy(self.current)
        if current and current['state'] == 'CHARGE':
            current['isRenewable'] = bool(current.get('renewableAt') is not None
                and current['renewableAt'] <= time.time() and current.get('renewableCnt', 0) > 0
                and not current.get('isRenewalImpossible'))
        return current

    def seats(self, room_id):
        numbers = {str(number) for number in range(1, 121)} | {number for room, number in WATCH_LIST if room == room_id}
        return [{'id': room_id * 1000 + int(number), 'code': number,
                 'isOccupied': int(number) % 3 != 0, 'remainingTime': (int(number) * 7) % 180 + 1}
                for number in sorted(numbers, key=int)]

    def reserve(self, seat_id):
        room, number = divmod(int(seat_id), 1000)
        self.sequence += 1
        self.current = {'id': str(self.sequence), 'state': 'TEMP_CHARGE', 'seatNo': str(number),
                        'seatId': seat_id, 'roomId': room, 'startedAt': time.time(),
                        'roomName': ROOMS[room], 'remainingTime': 10, 'endTime': None,
                        'renewableCnt': 3, 'renewalLimit': 3, 'renewableAt': None,
                        'isRenewable': False, 'isRenewalImpossible': False}

    def release(self, reservation):
        self.current = None

    def check_arrival(self, room_id, serial_no):
        if not self.current or self.current['roomId'] != room_id:
            raise LibraryError('데모 좌석이 변경되었습니다.')

    def confirm_reservation(self, reservation_id):
        if not self.current or self.current['id'] != reservation_id:
            raise LibraryError('데모 좌석이 변경되었습니다.')
        self.current.update(state='CHARGE', remainingTime=180,
                            renewableAt=time.time() + 3600,
                            endTime=datetime.fromtimestamp(time.time() + 10800, KST).strftime('%Y-%m-%d %H:%M:%S'))

    def renew_reservation(self, reservation_id):
        if not self.current or self.current['id'] != reservation_id or self.current['state'] != 'CHARGE':
            raise LibraryError('데모 좌석이 변경되었습니다.')
        if self.current['renewableCnt'] <= 0:
            raise LibraryError('데모 연장 횟수 소진')
        self.current.update(renewableCnt=self.current['renewableCnt'] - 1, remainingTime=180,
                            renewableAt=time.time() + 3600, isRenewable=False,
                            endTime=datetime.fromtimestamp(time.time() + 10800, KST).strftime('%Y-%m-%d %H:%M:%S'))
