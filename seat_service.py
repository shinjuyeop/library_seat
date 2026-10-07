"""Server-owned reservation worker. No browser tab is required to keep it running."""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

import requests

from library_config import WATCH_LIST

BASE = 'https://library.konkuk.ac.kr'
ROOMS = {102: '제1열람실 A', 101: '제1열람실 B'}
ACTIVE_STATES = {'TEMP_CHARGE', 'CHARGE', 'IN_USE'}


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
        return {
            'id': str(data['id']), 'state': str(code).upper(),
            'seatNo': str(data.get('seatNo') or seat.get('code') or seat.get('name') or '확인 중'),
            'roomName': str(data.get('roomName') or room.get('name') or '열람실'),
            'endTime': data.get('endTime') or data.get('chargeEndDateTime') or data.get('endDateTime'),
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

    def save(self, targets, running):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (1, ?)',
                       (json.dumps({'targets': targets, 'running': running}),))


class SeatService:
    def __init__(self, store, *, interval=30, client=None, demo=False):
        self.store = store
        self.interval = max(10, interval)
        self.client = client
        self.demo = demo
        self.operation = threading.RLock()
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.login_thread = None
        saved = store.load()
        allowed = {f'{room}:{seat}' for room, seat in WATCH_LIST}
        self.state = {
            'connected': client is not None, 'connecting': False,
            'running': bool(saved['running']),
            'targets': [key for key in saved['targets'] if key in allowed],
            'seats': [], 'reservation': None, 'reservationFresh': False,
            'lastChecked': None, 'nextCheck': None, 'error': None,
            'message': '도서관 계정을 연결해 주세요.' if not client else '좌석 현황을 확인하고 있습니다.',
            'events': [], 'demo': demo, 'interval': self.interval,
        }

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def _update(self, **kwargs):
        with self.lock:
            self.state.update(kwargs)

    def _event(self, text):
        with self.lock:
            self.state['events'].insert(0, {'time': time.time(), 'text': text})
            del self.state['events'][20:]
            self.state['message'] = text

    def _save(self):
        with self.lock:
            self.store.save(self.state['targets'], self.state['running'])

    def _failure(self, error):
        self._update(error=str(error), reservationFresh=False)
        if error.expired:
            self._disconnect_client()
            self._update(connected=False, nextCheck=None)
            self._event('도서관 연결이 만료되어 대기를 일시 중지했습니다. 다시 로그인해 주세요.')
        if error.uncertain:
            self._update(running=False)
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
                         seats=[], lastChecked=None, nextCheck=None, error=None)
            self._save()
            self._event('도서관 연결과 자동 예약을 종료했습니다.')

    def set_wait(self, targets, running):
        allowed = {f'{room}:{seat}' for room, seat in WATCH_LIST}
        if not isinstance(targets, list) or any(not isinstance(key, str) or key not in allowed for key in targets):
            raise LibraryError('관심 좌석 목록에서 좌석을 선택해 주세요.')
        if type(running) is not bool:
            raise LibraryError('올바른 대기 상태를 지정해 주세요.')
        if running and not targets:
            raise LibraryError('대기할 좌석을 먼저 선택해 주세요.')
        with self.operation:
            if running:
                if not self.client:
                    raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
                try:
                    reservation = self.client.reservation()
                except LibraryError as error:
                    self._failure(error)
                    raise
                self._update(reservation=reservation, reservationFresh=True)
                if reservation:
                    raise LibraryError('이미 이용 중이거나 임시배정된 좌석이 있습니다. 내 좌석을 확인해 주세요.')
            self._update(targets=list(dict.fromkeys(targets)), running=running, error=None)
            self._save()
            self._event('서버에서 자동 예약을 시작했습니다.' if running else '자동 예약을 중지했습니다.')
            self.wake.set()

    def tick(self):
        with self.operation:
            if not self.client:
                return
            try:
                reservation = self.client.reservation()
                self._update(reservation=reservation, reservationFresh=True)
                if reservation and self.snapshot()['running']:
                    self._update(running=False, targets=[])
                    self._save()
                    self._event('내 좌석이 확인되어 모든 예약 대기를 종료했습니다.')
                seats = []
                for room_id, room_name in ROOMS.items():
                    watch = {seat for room, seat in WATCH_LIST if room == room_id}
                    for item in self.client.seats(room_id):
                        code = str(item.get('code'))
                        if code not in watch:
                            continue
                        occupied = item.get('isOccupied')
                        seats.append({
                            'key': f'{room_id}:{code}', 'roomId': room_id, 'roomName': room_name,
                            'number': code, 'id': item.get('seatId') or item.get('id'),
                            'occupied': occupied if type(occupied) is bool else None,
                            'remainingTime': item.get('remainingTime'),
                        })
                self._update(seats=seats, lastChecked=time.time(), error=None)
                state = self.snapshot()
                if state['running'] and not reservation:
                    for key in state['targets']:
                        seat = next((item for item in seats if item['key'] == key), None)
                        if seat and seat['occupied'] is False and seat['id']:
                            try:
                                # Recheck immediately before a write; never release an existing seat implicitly.
                                current = self.client.reservation()
                                if current:
                                    self._update(reservation=current, running=False, targets=[])
                                    self._save()
                                    self._event('내 좌석이 확인되어 예약 대기를 종료했습니다.')
                                    break
                                self._reserve(seat)
                                break
                            except LibraryError as error:
                                if error.expired or error.uncertain or not self.snapshot()['running']:
                                    raise
                                self._update(error=str(error))
                                break  # One reservation attempt per tick bounds each cloud invocation.
            except LibraryError as error:
                self._failure(error)
            finally:
                self._update(nextCheck=time.time() + self.interval if self.client else None)

    def _reserve(self, seat):
        previous = self.snapshot()
        # Persist a disarmed state before a write, including when the process dies mid-request.
        self._update(running=False)
        self._event(f"{seat['roomName']} {seat['number']}번 예약을 요청합니다. 결과가 표시되지 않으면 내 좌석을 새로고침해 주세요.")
        self._save()
        try:
            self.client.reserve(seat['id'])
        except LibraryError as error:
            if not error.uncertain and not error.expired:
                self._update(running=previous['running'])
                self._save()
            raise
        # Stop and persist BEFORE re-querying. A failed read must never lead to another write.
        self._update(running=False, targets=[])
        self._save()
        self._event(f"{seat['roomName']} {seat['number']}번 예약 요청이 접수되었습니다. 배정 상태를 확인합니다.")
        reservation = self.client.reservation()
        self._update(reservation=reservation, reservationFresh=True)
        if not reservation:
            raise LibraryError('예약 요청은 접수되었지만 내 좌석에 아직 반영되지 않았습니다. 새로고침해 주세요.')
        self._event('좌석을 확보했습니다. 임시배정이면 도서관에서 NFC 인증을 완료해 주세요.')

    def reserve(self, key):
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            if key not in {f'{room}:{seat}' for room, seat in WATCH_LIST}:
                raise LibraryError('올바른 좌석을 선택해 주세요.')
            try:
                current = self.client.reservation()
                self._update(reservation=current, reservationFresh=True)
                if current:
                    raise LibraryError('이미 배정된 좌석이 있습니다. 내 좌석을 확인해 주세요.')
                room, code = key.split(':')
                raw = next((item for item in self.client.seats(int(room)) if str(item.get('code')) == code), None)
                if not raw or raw.get('isOccupied') is not False:
                    raise LibraryError('현재 예약 가능한 빈 좌석이 아닙니다.')
                seat_id = raw.get('seatId') or raw.get('id')
                if not seat_id:
                    raise LibraryError('좌석 식별 정보를 확인할 수 없습니다.')
                self._reserve({'id': seat_id, 'roomName': ROOMS[int(room)], 'number': code})
                self.wake.set()
            except LibraryError as error:
                self._failure(error)
                raise

    def release(self, expected_id, expected_state):
        with self.operation:
            if not self.client:
                raise LibraryError('도서관 계정을 먼저 연결해 주세요.')
            try:
                reservation = self.client.reservation()
                if not reservation or reservation['id'] != expected_id or reservation['state'] != expected_state:
                    self._update(reservation=reservation, reservationFresh=True)
                    raise LibraryError('좌석 상태가 변경되었습니다. 새로고침 후 다시 확인해 주세요.')
                self._update(running=False, targets=[])
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
                    self._update(error='서버 처리 중 오류가 발생해 자동 예약을 중지했습니다.', running=False)
                    self._save()
            self.wake.wait(self.interval)

    def stop(self):
        self.stopping.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=30)


class DemoClient:
    """Local preview only. Never sends requests to the library."""
    def __init__(self):
        self.current = None

    def close(self):
        pass

    def reservation(self):
        return copy.deepcopy(self.current)

    def seats(self, room_id):
        return [{'id': room_id * 1000 + int(number), 'code': number,
                 'isOccupied': int(number) % 3 != 0, 'remainingTime': (int(number) * 7) % 180 + 1}
                for room, number in WATCH_LIST if room == room_id]

    def reserve(self, seat_id):
        room, number = divmod(int(seat_id), 1000)
        self.current = {'id': '1', 'state': 'TEMP_CHARGE', 'seatNo': str(number),
                        'roomName': ROOMS[room], 'remainingTime': 10, 'endTime': None}

    def release(self, reservation):
        self.current = None
