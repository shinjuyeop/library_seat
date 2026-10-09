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


def sanitize_seat_catalog(state):
    """Also filter persisted catalogs before their next provider refresh."""
    state['seats'] = [seat for seat in state.get('seats', []) if valid_seat_key(seat.get('key'))]
    state['targets'] = [key for key in state.get('targets', []) if valid_seat_key(key)]
    state['running'] = bool(state.get('running') and state['targets'])
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

    def save(self, targets, running, repeat=None, repeat_control=None):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (1, ?)',
                       (json.dumps({'targets': targets, 'running': running, 'repeat': repeat,
                                    'repeatControl': repeat_control}),))


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
            'lastChecked': None, 'nextCheck': None, 'error': None,
            'message': '도서관 계정을 연결해 주세요.' if not client else '좌석 현황을 확인하고 있습니다.',
            'events': [], 'demo': demo, 'interval': self.interval,
        }
        sanitize_seat_catalog(self.state)

    def snapshot(self):
        with self.lock:
            return {**sanitize_seat_catalog(copy.deepcopy(self.state)), 'confirmationRooms': sorted(self.nfc_tags)}

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
                            self.state['repeatControl'])

    def _failure(self, error):
        self._update(error=str(error), reservationFresh=False)
        if error.expired:
            self._disconnect_client()
            self._update(connected=False, nextCheck=None)
            self._event('도서관 연결이 만료되어 대기를 일시 중지했습니다. 다시 로그인해 주세요.')
        if error.uncertain:
            self._update(running=False, repeat=None, repeatControl={'observedId': None, 'paused': True})
            self._save()
            self._event('요청 결과가 불명확해 자동 예약을 중지했습니다. 내 좌석을 확인한 뒤 다시 시작해 주세요.')

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
            self._update(connected=False, running=False, reservation=None, reservationFresh=False,
                         seats=[], lastChecked=None, nextCheck=None, error=None, repeat=None)
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

    def tick(self, *, targets_only=False, allow_repeat=True):
        with self.operation:
            if not self.client:
                return
            started = time.time()
            try:
                reservation = self._read_reservation()
                self._update(reservation=reservation, reservationFresh=True)
                self._check_repeat(reservation, allow_repeat=allow_repeat)
                reservation = self.snapshot()['reservation']
                self._auto_repeat(reservation)
                current = self.snapshot()
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
                                break  # Try one target per tick, including its recovery if needed.
            except LibraryError as error:
                self._failure(error)
            finally:
                interval = self.poll_interval()
                next_check = max(started + interval, time.time())
                repeat = self.snapshot()['repeat']
                if repeat and not self.snapshot()['error']:
                    next_check = min(next_check, max(time.time() + 1, repeat['dueAt']))
                self._update(interval=interval, nextCheck=next_check if self.client else None)

    @staticmethod
    def _repeat_plan(reservation):
        if not reservation or reservation['state'] != 'TEMP_CHARGE':
            raise LibraryError('임시배정된 좌석에서만 자동 재예약을 켤 수 있습니다.')
        started = reservation.get('startedAt')
        seat_id, room_id = reservation.get('seatId'), reservation.get('roomId')
        if (not str(seat_id).isdigit() or not str(room_id).isdigit() or int(room_id) not in ROOMS
                or not valid_seat_key(f"{room_id}:{reservation.get('seatNo')}")
                or not isinstance(started, (int, float)) or not math.isfinite(started)
                or started > time.time() + 5 or time.time() >= started + TEMP_DURATION_SECONDS):
            raise LibraryError('임시배정 시간이나 좌석 정보를 확인할 수 없습니다. 새로고침 후 다시 시도해 주세요.')
        return {'reservationId': reservation['id'], 'seatId': seat_id, 'roomId': int(room_id),
                'dueAt': started + TEMP_REPEAT_SECONDS, 'expiresAt': started + TEMP_DURATION_SECONDS}

    @staticmethod
    def _repeat_matches(reservation, repeat):
        return bool(reservation and reservation['state'] == 'TEMP_CHARGE'
                    and reservation['id'] == repeat['reservationId']
                    and str(reservation.get('seatId')) == str(repeat['seatId'])
                    and str(reservation.get('roomId')) == str(repeat['roomId']))

    def _auto_repeat(self, reservation, *, force=False):
        if not reservation or reservation['state'] != 'TEMP_CHARGE':
            return
        state = self.snapshot()
        control = state['repeatControl']
        if state['repeat'] or (not force and (control['paused'] or control['observedId'] == reservation['id'])):
            return
        self._update(repeatControl={'observedId': reservation['id'], 'paused': False})
        try:
            self._update(repeat=self._repeat_plan(reservation))
            self._event('임시배정 자동 재예약을 시작했습니다. 배정 9분 후 같은 좌석을 다시 예약합니다.')
        except LibraryError:
            self._event('임시배정 시간이나 좌석 정보를 확인할 수 없어 자동 재예약을 켜지 못했습니다.')
        self._save()

    def set_repeat(self, enabled, expected_id):
        if type(enabled) is not bool or not isinstance(expected_id, str):
            raise LibraryError('올바른 자동 재예약 설정을 지정해 주세요.')
        with self.operation:
            if not enabled:
                current = self.snapshot()['reservation']
                self._update(repeat=None, error=None,
                             repeatControl={'observedId': current['id'] if current else expected_id, 'paused': False})
                self._save()
                self._event('자동 재예약을 껐습니다. 현재 좌석은 유지됩니다.')
                return
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            try:
                reservation = self._read_reservation()
                self._update(reservation=reservation, reservationFresh=True)
                if not reservation or reservation['id'] != expected_id:
                    raise LibraryError('내 좌석이 변경되었습니다. 다시 확인해 주세요.')
                plan = self._repeat_plan(reservation)
                self._update(repeat=plan, error=None,
                             repeatControl={'observedId': reservation['id'], 'paused': False},
                             nextCheck=min(self.snapshot()['nextCheck'] or time.time() + 30,
                                           time.time() + 30, max(time.time() + 1, plan['dueAt'])))
                self._save()
                self._event('자동 재예약을 켰습니다. 임시배정 9분 후 같은 좌석을 다시 예약합니다.')
                self.wake.set()
            except LibraryError as error:
                self._failure(error)
                raise

    def _check_repeat(self, reservation, *, allow_repeat):
        repeat = self.snapshot()['repeat']
        if not repeat:
            return
        if (not self._repeat_matches(reservation, repeat) or time.time() >= repeat['expiresAt']
                or not valid_seat_key(f"{reservation.get('roomId')}:{reservation.get('seatNo')}")):
            self._update(repeat=None, repeatControl={'observedId': reservation['id'] if reservation else None, 'paused': False})
            self._save()
            self._event('배정 상태가 바뀌었거나 시간이 지나 자동 재예약을 종료했습니다.')
            return
        if not allow_repeat or time.time() < repeat['dueAt']:
            return
        # Recheck at the write boundary: never cancel a confirmed or replacement reservation.
        current = self._read_reservation()
        self._update(reservation=current, reservationFresh=True)
        if (not self._repeat_matches(current, repeat) or time.time() >= repeat['expiresAt']
                or not valid_seat_key(f"{current.get('roomId')}:{current.get('seatNo')}")):
            self._update(repeat=None, repeatControl={'observedId': current['id'] if current else None, 'paused': False})
            self._save()
            self._event('내 좌석 상태가 변경되어 자동 재예약을 종료했습니다.')
            return
        # Durable disarm BEFORE cancel: crashes and ambiguous writes must never replay.
        previous = self.snapshot()
        self._update(repeat=None, running=False, targets=[],
                     repeatControl={'observedId': current['id'], 'paused': True})
        self._event('임시배정을 취소하고 같은 좌석을 다시 예약합니다.')
        self._save()
        try:
            self.client.release(current)
            after_cancel = self.client.reservation()
            self._update(reservation=after_cancel, reservationFresh=True)
            if after_cancel:
                raise LibraryError('취소 후 좌석 상태를 확인할 수 없어 재예약을 중지했습니다.')
            booked_at = time.time()
            self.client.reserve(repeat['seatId'])
            booked = self.client.reservation()
            self._update(reservation=booked, reservationFresh=True)
            if (not booked or booked['id'] == repeat['reservationId']
                    or str(booked.get('seatId')) != str(repeat['seatId'])
                    or str(booked.get('roomId')) != str(repeat['roomId'])):
                raise LibraryError('재예약 결과를 확인할 수 없어 자동 재예약을 중지했습니다. 내 좌석을 확인해 주세요.')
            if booked['state'] == 'TEMP_CHARGE':
                if booked.get('startedAt') is None:
                    booked['startedAt'] = booked_at
                    self._update(reservation=booked)
                plan = self._repeat_plan(booked)
                if plan['dueAt'] <= time.time():
                    raise LibraryError('새 배정 시간을 확인할 수 없어 자동 재예약을 중지했습니다.')
                self._update(repeat=plan)
            self._update(running=previous['running'], targets=previous['targets'],
                         repeatControl={'observedId': booked['id'], 'paused': False})
            self._save()
            self._event('같은 좌석을 다시 예약했습니다. 현장에서 NFC 인증을 완료해 주세요.')
        except LibraryError:
            self._update(repeat=None)
            self._event('자동 재예약을 중지했습니다. 내 좌석을 확인해 주세요.')
            raise

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
        self._confirm_booking(seat, booked_at)
        self._event('좌석을 확보했습니다. 임시배정 자동 재예약은 9분마다 실행되며 NFC 인증 시 종료됩니다.'
                    if self.snapshot()['repeat'] else '좌석을 확보했습니다. 내 좌석에서 배정 상태를 확인해 주세요.')

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

    def _switch_reserve(self, seat, current, *, auto_confirm=False):
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
            return
        confirm_target = auto_confirm and int(seat['roomId']) in self.nfc_tags
        self._confirm_booking(seat, booked_at, start_repeat=not confirm_target, previous_id=current['id'])
        if confirm_target:
            self._confirm_switched_seat()
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
                self._event('배정이 확정되었습니다. 자동 재예약과 갈아타기 대기를 종료했습니다.')
                self._save()
                self.wake.set()
            except LibraryError as error:
                self._failure(error)
                raise

    def reassign(self, expected_id):
        """One explicit transaction: return, reserve the same seat, then confirm it."""
        if not isinstance(expected_id, str) or not re.fullmatch(r'[0-9]{1,20}', expected_id):
            raise LibraryError('배정 정보를 다시 확인해 주세요.')
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            stage = None
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

                # The cloud lease and this lock span all three steps. Never arm the
                # normal temporary repeat between reserve and confirm, including crashes.
                self._update(running=False, targets=[], repeat=None, error=None,
                             repeatControl={'observedId': expected_id, 'paused': True})
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
                             repeatControl={'observedId': reservation['id'], 'paused': True})
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
        return copy.deepcopy(self.current)

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
                        'roomName': ROOMS[room], 'remainingTime': 10, 'endTime': None}

    def release(self, reservation):
        self.current = None

    def check_arrival(self, room_id, serial_no):
        if not self.current or self.current['roomId'] != room_id:
            raise LibraryError('데모 좌석이 변경되었습니다.')

    def confirm_reservation(self, reservation_id):
        if not self.current or self.current['id'] != reservation_id:
            raise LibraryError('데모 좌석이 변경되었습니다.')
        self.current.update(state='CHARGE', remainingTime=180,
                            endTime=(datetime.now(timezone(timedelta(hours=9))) + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S'))
