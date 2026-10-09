import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import App from './App';
import { pollDelay, useLibrary } from './useLibrary';

const rooms = [
  { id: 102, name: '1열람실 A' },
  { id: 232, name: '2열람실' },
  { id: 107, name: '5열람실' },
];
const seats = rooms.flatMap((room) =>
  [1, 3, 30, 31].map((number) => ({
    key: `${room.id}:${number}`,
    roomId: room.id,
    roomName: room.name,
    number: String(number),
    occupied: number !== 3,
    remainingTime: 1,
    single: room.id === 102,
    checkedAt: Date.now() / 1000,
  })),
);
const initialData = () => ({
  rooms,
  seats,
  connected: true,
  connecting: false,
  running: false,
  targets: [],
  reservation: null,
  reservationFresh: true,
  repeat: null,
  confirmationRooms: [102],
  events: [],
  interval: 30,
  cloud: true,
  demo: false,
  autoLogin: true,
  error: null,
});
const response = (data, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  json: async () => structuredClone(data),
});
let data, signedIn, requests, fakeFetch;

beforeEach(() => {
  window.history.replaceState(null, '');
  data = initialData();
  signedIn = true;
  requests = [];
  HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new Event('close'));
  };
  Element.prototype.scrollIntoView = vi.fn();
  window.scrollTo = vi.fn();
  fakeFetch = vi.fn(async (url, options = {}) => {
    const path = url.replace('/api/', ''),
      body = options.body ? JSON.parse(options.body) : undefined;
    requests.push({ path, body, options });
    if (path === 'session')
      return response({
        authorized: signedIn,
        csrf: 'test-csrf',
        directLogin: true,
        demo: false,
      });
    if (path === 'state')
      return signedIn
        ? response(data)
        : response({ error: '로그인 필요' }, 401);
    if (path === 'library-login') {
      if (body.password !== 'correct-password')
        return response({ error: '비밀번호를 다시 입력해 주세요.' }, 422);
      signedIn = true;
      return response({ csrf: 'new-csrf' });
    }
    if (path === 'wait') {
      data.running = body.running;
      data.targets = body.targets;
    }
    if (path === 'wait/seat') {
      if (!data.running) return response({ error: '대기가 이미 종료되었습니다. 내 좌석을 확인해 주세요.' }, 409);
      data.targets = body.enabled ? [...new Set([...data.targets, body.key])] : data.targets.filter(key => key !== body.key);
      data.running = data.targets.length > 0;
    }
    if (path === 'reserve') {
      data.reservation = {
        id: 'reservation-1',
        seatNo: '3',
        roomName: '2열람실',
        state: 'TEMP_CHARGE',
        remainingTime: 10,
      };
      data.running = false;
      data.targets = [];
      data.repeat = { reservationId: 'reservation-1', dueAt: Date.now() / 1000 + 540 };
    }
    if (path === 'repeat')
      data.repeat = body.enabled ? { dueAt: Date.now() / 1000 + 540 } : null;
    if (path === 'confirm') {
      data.reservation = { ...data.reservation, state: 'CHARGE' };
      data.repeat = null;
      data.running = false;
      data.targets = [];
    }
    if (path === 'reassign') {
      data.reservation = { ...data.reservation, id: '124', state: 'CHARGE' };
      data.repeat = null;
      data.running = false;
      data.targets = [];
    }
    if (path === 'release') {
      data.reservation = null;
      data.repeat = null;
    }
    if (path === 'logout') signedIn = false;
    return response({ ok: true });
  });
  vi.stubGlobal('fetch', fakeFetch);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});
const openApp = async () => {
  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
  await screen.findByRole('heading', { name: '좌석 찾기' });
};
const allSeats = () => {
  fireEvent.click(
    screen.getByRole('button', { name: '전체 좌석', exact: true }),
  );
  // Grid interactions below use global search; room-directory flows are tested separately.
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '열람실' } });
};
const seat = (name) => screen.getByRole('button', { name });
const writes = (path) =>
  requests.filter(
    (item) => item.path === path && item.options.method === 'POST',
  );

describe('React app with the existing account API', () => {
  it('shows room availability first and supports room back and browser forward', async () => {
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '전체 좌석', exact: true }));
    expect(document.querySelectorAll('.room-card')).toHaveLength(3);
    expect(document.querySelector('.seat-cell')).toBeNull();
    const card = seat('2열람실 좌석 보기');
    expect(card.textContent).toContain('사용 3 / 전체 4');
    expect(card.querySelector('.ring-label strong').textContent).toBe('1');
    fireEvent.click(card);
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(4);
    expect(document.querySelectorAll('.room-group')).toHaveLength(1);
    expect(document.activeElement.id).toBe('room-detail-heading');
    expect(screen.getByRole('heading', { name: '2열람실' })).toBeTruthy();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '3' } });
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: '내 좌석', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: '좌석 찾기', exact: true }));
    expect(screen.getByRole('searchbox').value).toBe('3');
    expect(document.querySelectorAll('.room-group')).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: '열람실 목록' }));
    await screen.findByRole('button', { name: '2열람실 좌석 보기' });
    expect(document.activeElement.id).toBe('room-card-232');
    act(() => window.history.forward());
    await screen.findByRole('heading', { name: '2열람실' });
    expect(screen.getByRole('searchbox').value).toBe('3');
    fireEvent.click(screen.getByRole('button', { name: '설정', exact: true }));
    act(() => window.history.back());
    await screen.findByRole('button', { name: '2열람실 좌석 보기' });
    expect(screen.getByRole('heading', { name: '좌석 찾기', level: 1 })).toBeTruthy();
    expect(document.activeElement.id).toBe('room-card-232');
    expect(writes('reserve')).toHaveLength(0);
  });

  it('keeps ordered selections while moving between room cards', async () => {
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '전체 좌석', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: '여러 좌석 선택' }));
    fireEvent.click(seat('5열람실 좌석 보기'));
    fireEvent.click(seat('5열람실 31번 1분 대기 선택'));
    fireEvent.click(screen.getByRole('button', { name: '열람실 목록' }));
    fireEvent.click(await screen.findByRole('button', { name: '2열람실 좌석 보기' }));
    fireEvent.click(seat('2열람실 1번 1분 대기 선택'));
    fireEvent.click(screen.getByRole('button', { name: '선택한 좌석 2' }));
    const selected = [...document.querySelectorAll('.seat-list-button')];
    expect(selected[0].textContent).toContain('5열람실 · 31번');
    expect(selected[1].textContent).toContain('2열람실 · 1번');
    expect(writes('wait')).toHaveLength(0);
  });

  it('searches across all rooms from the directory and returns when cleared', async () => {
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '전체 좌석', exact: true }));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '3' } });
    expect(document.querySelectorAll('.room-group')).toHaveLength(3);
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(9);
    fireEvent.click(screen.getByRole('button', { name: '검색어 지우기' }));
    expect(document.querySelectorAll('.room-card')).toHaveLength(3);
    fireEvent.click(seat('1열람실 A 좌석 보기'));
    fireEvent.click(screen.getByRole('checkbox', { name: '빈자리만' }));
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(1);
    expect(seat('1열람실 A 3번 빈자리 상세 보기')).toBeTruthy();
  });

  it('does not count unknown states as occupied and distinguishes missing room data', async () => {
    data.seats = data.seats.filter(item => item.roomId !== 107).map(item =>
      item.key === '102:1' ? { ...item, occupied: null } : item);
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '전체 좌석', exact: true }));
    const known = seat('1열람실 A 좌석 보기'), missing = seat('5열람실 좌석 보기');
    expect(known.textContent).toContain('사용 2 / 전체 4');
    expect(known.textContent).toContain('상태 미확인 1석');
    expect(missing.querySelector('.ring-label strong').textContent).toBe('—');
    expect(missing.textContent).toContain('좌석 정보 확인 중');
  });

  it('restores the room and search after reloading the app', async () => {
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '전체 좌석', exact: true }));
    fireEvent.click(seat('2열람실 좌석 보기'));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '31' } });
    cleanup();
    await openApp();
    expect(screen.getByRole('searchbox').value).toBe('31');
    expect(screen.getByRole('heading', { name: '2열람실' })).toBeTruthy();
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(1);
  });

  it('keeps failed login on the password form and clears the password', async () => {
    signedIn = false;
    render(<App />);
    const username = await screen.findByLabelText('아이디 / 학번'),
      password = screen.getByLabelText('비밀번호');
    fireEvent.change(username, { target: { value: 'demo' } });
    fireEvent.change(password, { target: { value: 'wrong-password' } });
    fireEvent.submit(
      screen
        .getByRole('button', { name: '로그인', exact: true })
        .closest('form'),
    );
    expect(await screen.findByRole('alert')).toHaveProperty(
      'textContent',
      '비밀번호를 다시 입력해 주세요.',
    );
    await waitFor(() => expect(password.value).toBe(''));
    expect(document.activeElement).toBe(password);
    expect(screen.queryByRole('heading', { name: '좌석 찾기' })).toBeNull();
    fireEvent.change(password, { target: { value: 'correct-password' } });
    fireEvent.submit(password.closest('form'));
    await screen.findByRole('heading', { name: '좌석 찾기' });
    expect(writes('library-login')[1].body.remember).toBe(true);
    expect(writes('library-login')[1].options.headers['X-CSRF-Token']).toBe(
      'test-csrf',
    );
  });

  it('preserves filters and ordered selection across navigation, then starts waiting', async () => {
    await openApp();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '3' } });
    expect(document.querySelectorAll('.room-group')).toHaveLength(3);
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(9);
    fireEvent.click(screen.getByRole('button', { name: '여러 좌석 선택' }));
    fireEvent.click(seat('5열람실 3번 빈자리 대기 선택'));
    fireEvent.click(seat('2열람실 31번 1분 대기 선택'));
    fireEvent.click(screen.getByRole('button', { name: '설정', exact: true }));
    expect(screen.queryByRole('searchbox')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '좌석 찾기', exact: true }));
    expect(screen.getByRole('searchbox').value).toBe('3');
    fireEvent.click(screen.getByRole('button', { name: '선택한 좌석 2' }));
    const chosen = [...document.querySelectorAll('.seat-list-button')].map(item => item.textContent);
    expect(chosen[0]).toContain('5열람실 · 3번');
    expect(chosen[1]).toContain('2열람실 · 31번');
    fireEvent.click(screen.getByRole('button', { name: '자동 예약 시작' }));
    await screen.findByRole('button', { name: '자동 예약 중지' });
    expect(writes('wait')[0].body).toEqual({ targets: ['107:3', '232:31'], running: true });
    expect(screen.getByRole('heading', { name: '내 좌석', level: 1 })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '좌석 찾기', exact: true }));
    expect(screen.queryByRole('button', { name: '여러 좌석 선택' })).toBeNull();
    expect(screen.getByRole('button', { name: '대기 중 2' })).toBeTruthy();
  });

  it('opens seat details without selecting or booking and requires explicit confirmation', async () => {
    await openApp(); allSeats();
    fireEvent.click(seat('2열람실 3번 빈자리 상세 보기'));
    expect(writes('reserve')).toHaveLength(0);
    expect(screen.getByRole('dialog').id).toBe('seat-sheet');
    fireEvent.click(screen.getByRole('button', { name: '이 자리 바로 예약' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '취소', exact: true }));
    expect(writes('reserve')).toHaveLength(0);
    fireEvent.click(seat('2열람실 3번 빈자리 상세 보기'));
    fireEvent.click(screen.getByRole('button', { name: '이 자리 바로 예약' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '예약하기' }));
    await screen.findByText('임시배정 · 확정 필요');
    expect(writes('reserve')).toHaveLength(1);
    expect(writes('reserve')[0].body).toEqual({ key: '232:3' });
    expect(writes('reserve')[0].options.headers['X-CSRF-Token']).toBe('test-csrf');
    expect(document.getElementById('toast').textContent).toBe('배정 완료 · 2열람실 3번');
    expect(document.activeElement.id).toBe('reservation');
    expect(screen.getByRole('switch', { name: '임시배정 자동 재예약' }).getAttribute('aria-checked')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: '좌석 찾기', exact: true }));
    expect(screen.getByRole('button', { name: '내 좌석 보기' }).textContent).toContain('임시배정');
    expect(screen.queryByRole('button', { name: '자동 예약 시작' })).toBeNull();
  });

  it('keeps held seat and switch targets across refresh and confirms switching wait', async () => {
    data.reservation = { id: 'held', seatNo: '21', roomName: '1열람실 B', state: 'CHARGE' };
    await openApp(); allSeats();
    fireEvent.click(screen.getByRole('button', { name: '여러 좌석 선택' }));
    fireEvent.click(seat('2열람실 31번 1분 대기 선택'));
    fireEvent.click(screen.getByRole('button', { name: '새로고침' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '갈아타기 대기', exact: true }).disabled).toBe(false));
    expect(seat('2열람실 31번 1분 대기 선택').getAttribute('aria-pressed')).toBe('true');
    fireEvent.click(screen.getByRole('button', { name: '갈아타기 대기', exact: true }));
    expect(writes('wait')).toHaveLength(0);
    expect(within(screen.getByRole('dialog')).getByText(/새 좌석과 복구 좌석 모두 자동으로 배정확정/)).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '갈아타기 대기 시작' }));
    await screen.findByRole('button', { name: '자동 예약 중지' });
    expect(writes('wait')[0].body).toEqual({ targets: ['232:31'], running: true });
    expect(data.reservation.id).toBe('held');
    expect(writes('release')).toHaveLength(0);
    expect(screen.getByText(/현재 좌석을 유지하며 대기/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '자동 예약 중지' }));
    await waitFor(() => expect(writes('wait')).toHaveLength(2));
    expect(writes('wait')[1].body.running).toBe(false);
  });

  it('offers immediate switching and explains restoration before writing', async () => {
    data.reservation = { id: 'held', seatNo: '21', roomName: '1열람실 B', state: 'CHARGE' };
    await openApp(); allSeats();
    fireEvent.click(seat('2열람실 3번 빈자리 상세 보기'));
    fireEvent.click(screen.getByRole('button', { name: '이 자리로 갈아타기' }));
    expect(within(screen.getByRole('dialog')).getByText(/원래 좌석 재예약을 시도/)).toBeTruthy();
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '갈아타기', exact: true }));
    await waitFor(() => expect(data.reservation.id).toBe('reservation-1'));
    expect(writes('reserve')[0].body).toEqual({ key: '232:3' });
  });

  it('starts a one-seat wait from an occupied seat and closes the sheet', async () => {
    await openApp(); allSeats();
    fireEvent.click(seat('2열람실 31번 1분 상세 보기'));
    fireEvent.click(screen.getByRole('button', { name: '이 좌석 대기 시작' }));
    await screen.findByRole('button', { name: '자동 예약 중지' });
    expect(writes('wait')[0].body).toEqual({ targets: ['232:31'], running: true });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('adds and removes live targets after a one-seat wait without restarting it', async () => {
    await openApp(); allSeats();
    fireEvent.click(seat('2열람실 31번 1분 상세 보기'));
    fireEvent.click(screen.getByRole('button', { name: '이 좌석 대기 시작' }));
    await screen.findByRole('button', { name: '좌석 추가', exact: true });
    fireEvent.click(screen.getByRole('button', { name: '좌석 추가', exact: true }));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '열람실' } });
    fireEvent.click(seat('5열람실 1번 1분 상세 보기'));
    fireEvent.click(screen.getByRole('button', { name: '대기에 추가', exact: true }));
    await screen.findByRole('button', { name: '대기 중 2' });
    expect(writes('wait')).toHaveLength(1);
    expect(writes('wait/seat')[0].body).toEqual({ key: '107:1', enabled: true });
    expect(writes('wait/seat')[0].options.headers['X-CSRF-Token']).toBe('test-csrf');
    expect(data.targets).toEqual(['232:31', '107:1']);
    expect(screen.getByRole('heading', { name: '좌석 찾기', level: 1 })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '대기 중 2' }));
    fireEvent.click(screen.getByRole('button', { name: '2열람실 31번 대기에서 제외' }));
    await screen.findByRole('button', { name: '대기 중 1' });
    expect(data.running).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: /5열람실 · 1번/ }));
    expect(screen.getByText(/제외하면 대기가 종료/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '대기에서 제외', exact: true }));
    await screen.findByRole('button', { name: '선택한 좌석 0' });
    expect(data.running).toBe(false);
    expect(writes('reserve')).toHaveLength(0);
    expect(writes('release')).toHaveLength(0);
  });

  it('reports a completed job instead of restarting it from a stale sheet', async () => {
    data.running = true; data.targets = ['232:31'];
    await openApp(); allSeats();
    fireEvent.click(seat('5열람실 1번 1분 상세 보기'));
    data.running = false; data.targets = [];
    fireEvent.click(screen.getByRole('button', { name: '대기에 추가', exact: true }));
    await screen.findByText('대기가 이미 종료되었습니다. 내 좌석을 확인해 주세요.');
    expect(writes('wait')).toHaveLength(0);
    expect(data.running).toBe(false);
  });

  it('blocks live additions when offline and preserves existing wait targets', async () => {
    data.running = true; data.targets = ['232:31'];
    await openApp(); allSeats();
    fireEvent.click(seat('5열람실 1번 1분 상세 보기'));
    fireEvent(window, new Event('offline'));
    expect(screen.getByRole('button', { name: '대기에 추가', exact: true }).disabled).toBe(true);
    expect(writes('wait/seat')).toHaveLength(0);
    expect(data.targets).toEqual(['232:31']);
  });

  it('adds seats from details to the multiselection draft without starting a job', async () => {
    await openApp();
    fireEvent.click(seat('1열람실 A 1번 1분 상세 보기'));
    fireEvent.click(screen.getByRole('button', { name: '여러 좌석 선택에 추가' }));
    expect(screen.getByRole('button', { name: '선택 마치기' })).toBeTruthy();
    expect(seat('1열람실 A 1번 1분 대기 선택').getAttribute('aria-pressed')).toBe('true');
    expect(writes('wait')).toHaveLength(0);
  });

  it('enables repeat explicitly and disabling never releases the seat', async () => {
    data.reservation = { id: 'temp-id', seatNo: '3', roomName: '2열람실', state: 'TEMP_CHARGE' };
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '내 좌석', exact: true }));
    fireEvent.click(screen.getByRole('switch'));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '자동 재예약 켜기' }));
    await waitFor(() => expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('true'));
    expect(writes('repeat')[0].body).toEqual({ enabled: true, id: 'temp-id' });
    expect(screen.getByText(/후 재예약/)).toBeTruthy();
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() => expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false'));
    expect(writes('release')).toHaveLength(0);
    expect(within(screen.getByRole('region', { name: '현재 배정' })).getByText('2열람실 · 3번')).toBeTruthy();
  });

  it('disables mutations offline while details remain readable', async () => {
    await openApp(); allSeats();
    fireEvent.click(seat('2열람실 3번 빈자리 상세 보기'));
    fireEvent(window, new Event('offline'));
    expect(screen.getByRole('button', { name: '이 자리 바로 예약' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: '여러 좌석 선택에 추가' }).disabled).toBe(true);
    expect(screen.getByRole('heading', { name: '3번 좌석' })).toBeTruthy();
    expect(writes('reserve')).toHaveLength(0);
  });

  it('passes original reservation identity if a release confirmation becomes stale', async () => {
    data.reservation = { id: 'temp-id', seatNo: '3', roomName: '2열람실', state: 'TEMP_CHARGE' };
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '내 좌석', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: '임시배정 취소' }));
    data.reservation = { ...data.reservation, state: 'CHARGE' };
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '임시배정 취소' }));
    await waitFor(() => expect(writes('release')).toHaveLength(1));
    expect(writes('release')[0].body).toEqual({ id: 'temp-id', state: 'TEMP_CHARGE' });
  });

  it('logs out from settings without stopping server jobs or leaking account state', async () => {
    data.running = true; data.targets = ['102:1'];
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '설정', exact: true }));
    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '로그아웃' }));
    await screen.findByRole('heading', { name: '로그인' });
    expect(writes('logout')).toHaveLength(1);
    expect(writes('wait')).toHaveLength(0);
    expect(screen.queryByRole('navigation')).toBeNull();
    expect(screen.queryByRole('heading', { name: '좌석 찾기' })).toBeNull();
  });
});

describe('allocation confirmation', () => {
  beforeEach(() => {
    data.reservation = { id: '123', roomId: 102, seatId: 102003, seatNo: '3', roomName: '1열람실 A', state: 'TEMP_CHARGE' };
    data.repeat = { reservationId: '123', dueAt: Date.now() / 1000 + 540 };
  });

  const mySeat = async () => {
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '내 좌석', exact: true }));
  };

  it('requires explicit confirmation and only announces the returned confirmed state', async () => {
    await mySeat();
    expect(screen.queryByText(/배정 확정 완료/)).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '배정 확정', exact: true }));
    expect(screen.getByRole('dialog').textContent).toContain('자동 재예약과 갈아타기 대기는 중지');
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '취소' }));
    expect(writes('confirm')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '배정 확정', exact: true }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '배정 확정' }));
    await waitFor(() => expect(document.getElementById('reservation-badge').textContent).toBe('배정 확정'));
    expect(document.getElementById('toast').textContent).toBe('배정 확정 완료 · 1열람실 A 3번');
    expect(writes('confirm')).toHaveLength(1);
    expect(writes('confirm')[0].body).toEqual({ id: '123' });
    expect(writes('confirm')[0].options.headers['X-CSRF-Token']).toBe('test-csrf');
    expect(screen.queryByRole('switch')).toBeNull();
    expect(screen.getByRole('button', { name: '좌석 반납' })).toBeTruthy();
    expect(writes('release')).toHaveLength(0);
  });

  it.each([false, true])('never pretends a still-temporary response is confirmed (rejected=%s)', async rejected => {
    await mySeat();
    const original = fakeFetch.getMockImplementation();
    fakeFetch.mockImplementation((url, options) => url === '/api/confirm'
      ? response(rejected ? { error: '태그 확인에 실패했습니다.' } : { ok: true }, rejected ? 409 : 200)
      : original(url, options));
    fireEvent.click(screen.getByRole('button', { name: '배정 확정', exact: true }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '배정 확정' }));
    await waitFor(() => expect(screen.getByRole('button', { name: '배정 확정', exact: true }).disabled).toBe(false));
    expect(document.getElementById('reservation-badge').textContent).toBe('임시배정 · 확정 필요');
    expect(screen.queryByText(/배정 확정 완료/)).toBeNull();
    if (rejected) expect(document.getElementById('toast').textContent).toBe('태그 확인에 실패했습니다.');
  });

  it('disables confirmation for stale and offline state', async () => {
    data.reservationFresh = false;
    await mySeat();
    expect(screen.getByRole('button', { name: '배정 확정' }).disabled).toBe(true);
    data.reservationFresh = true;
    fireEvent(window, new Event('focus'));
    await waitFor(() => expect(screen.getByRole('button', { name: '배정 확정' }).disabled).toBe(false));
    fireEvent(window, new Event('offline'));
    expect(screen.getByRole('button', { name: '배정 확정' }).disabled).toBe(true);
    expect(writes('confirm')).toHaveLength(0);
  });

  it('offers official app guidance for rooms without a configured tag', async () => {
    data.confirmationRooms = [];
    await mySeat();
    expect(screen.queryByRole('button', { name: '배정 확정' })).toBeNull();
    expect(screen.getByText('이 열람실은 현장에서 공식 앱으로 NFC 인증을 진행해 주세요.')).toBeTruthy();
  });
});

describe('return and reassign a confirmed seat', () => {
  beforeEach(() => {
    data.reservation = { id: '123', roomId: 102, seatId: 102003, seatNo: '3', roomName: '1열람실 A', state: 'CHARGE' };
  });
  const mySeat = async () => {
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '내 좌석', exact: true }));
  };
  const openConfirmation = () => fireEvent.click(screen.getByRole('button', { name: '좌석 반납 후 다시 배정' }));
  const accept = () => fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '반납 후 다시 배정' }));

  it('puts the button above return and requires an explicit warning confirmation', async () => {
    await mySeat();
    expect(document.getElementById('reassign').nextElementSibling.id).toBe('release');
    openConfirmation();
    expect(screen.getByRole('dialog').textContent).toContain('자리를 잃을 수 있습니다');
    expect(writes('reassign')).toHaveLength(0);
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '취소' }));
    expect(writes('reassign')).toHaveLength(0);
    expect(writes('release')).toHaveLength(0);
  });

  it('uses one server action and shows success only with the replacement confirmed seat', async () => {
    await mySeat();
    openConfirmation();
    accept();
    await waitFor(() => expect(document.getElementById('toast')?.textContent).toBe('재배정·확정 완료 · 1열람실 A 3번'));
    expect(writes('reassign')).toHaveLength(1);
    expect(writes('reassign')[0].body).toEqual({ id: '123' });
    expect(writes('reassign')[0].options.headers['X-CSRF-Token']).toBe('test-csrf');
    expect(writes('release')).toHaveLength(0);
    expect(writes('reserve')).toHaveLength(0);
    expect(writes('confirm')).toHaveLength(0);
    expect(document.getElementById('reservation-badge').textContent).toBe('배정 확정');
  });

  it('keeps a failed confirmation at temporary state with its error visible', async () => {
    await mySeat();
    const original = fakeFetch.getMockImplementation();
    fakeFetch.mockImplementation((url, options) => {
      if (url === '/api/reassign') {
        data.reservation = { ...data.reservation, id: '124', state: 'TEMP_CHARGE' };
        data.error = '배정 확정 단계에서 중지했습니다. 태그 확인에 실패했습니다.';
        return response({ error: data.error }, 409);
      }
      return original(url, options);
    });
    openConfirmation();
    accept();
    await screen.findByRole('button', { name: '배정 확정', exact: true });
    expect(document.getElementById('reservation-badge').textContent).toBe('임시배정 · 확정 필요');
    expect(document.getElementById('service-error').textContent).toBe(data.error);
    expect(document.getElementById('toast').textContent).toBe(data.error);
    expect(screen.queryByText(/재배정·확정 완료/)).toBeNull();
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false');
  });

  it('disables actions during the request and prevents double submission', async () => {
    await mySeat();
    const original = fakeFetch.getMockImplementation();
    let finish;
    fakeFetch.mockImplementation((url, options) => url === '/api/reassign'
      ? new Promise(resolve => { finish = () => resolve(original(url, options)); })
      : original(url, options));
    openConfirmation();
    accept();
    await screen.findByText('좌석 반납 → 같은 좌석 예약 → 배정 확정');
    expect(screen.getByRole('button', { name: '재배정 진행 중…' }).disabled).toBe(true);
    expect(screen.getByRole('button', { name: '좌석 반납', exact: true }).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button', { name: '재배정 진행 중…' }));
    await act(async () => { finish(); });
    expect(writes('reassign')).toHaveLength(1);
    await waitFor(() => expect(screen.getByRole('button', { name: '좌석 반납 후 다시 배정' }).disabled).toBe(false));
    expect(document.getElementById('reassign-progress')).toBeNull();
  });

  it('does not offer reassign for a room without confirmation support', async () => {
    data.confirmationRooms = [];
    await mySeat();
    expect(screen.queryByRole('button', { name: '좌석 반납 후 다시 배정' })).toBeNull();
    expect(screen.getByRole('button', { name: '좌석 반납', exact: true })).toBeTruthy();
  });

  it('disables reassign for stale or offline state', async () => {
    data.reservationFresh = false;
    await mySeat();
    expect(screen.getByRole('button', { name: '좌석 반납 후 다시 배정' }).disabled).toBe(true);
    data.reservationFresh = true;
    fireEvent(window, new Event('focus'));
    await waitFor(() => expect(screen.getByRole('button', { name: '좌석 반납 후 다시 배정' }).disabled).toBe(false));
    fireEvent(window, new Event('offline'));
    expect(screen.getByRole('button', { name: '좌석 반납 후 다시 배정' }).disabled).toBe(true);
  });
});

describe('polling and concurrent user actions', () => {
  it.each([false, true])('shows verified automatic switch or recovery completion (recovered=%s)', async recovered => {
    data.running = true;
    data.targets = ['102:3'];
    data.reservation = { id: '1', state: 'CHARGE', roomName: '1열람실 A', seatNo: '6' };
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data?.reservation?.id).toBe('1'));
    data.reservation = { ...data.reservation, id: '2', seatNo: recovered ? '6' : '3' };
    data.running = recovered;
    data.targets = recovered ? ['102:3'] : [];
    await act(() => result.current.refresh());
    expect(result.current.toast).toBe(recovered ? '재배정·확정 완료 · 1열람실 A 6번' : '배정 확정 완료 · 1열람실 A 3번');
    expect(result.current.data.running).toBe(recovered);
    expect(result.current.data.repeat).toBeNull();
    expect(writes('confirm')).toHaveLength(0);
  });

  it('does not announce completion when a switched temporary seat fails confirmation', async () => {
    data.running = true;
    data.targets = ['102:3'];
    data.reservation = { id: '1', state: 'CHARGE', roomName: '1열람실 A', seatNo: '6' };
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data?.reservation?.id).toBe('1'));
    data.reservation = { ...data.reservation, id: '2', seatNo: '3', state: 'TEMP_CHARGE' };
    data.running = false;
    data.targets = [];
    data.error = '새 좌석 예약 후 배정확정을 확인하지 못해 대기를 중지했습니다.';
    data.reservationFresh = false;
    await act(() => result.current.refresh());
    expect(result.current.toast).toBe('');
    expect(result.current.data.error).toBe(data.error);
    expect(result.current.data.reservation.state).toBe('TEMP_CHARGE');
    expect(result.current.data.running).toBe(false);
  });

  it('keeps checking when a wait job disarms before its first response is shown', async () => {
    vi.useFakeTimers();
    const { result } = renderHook(useLibrary);
    await act(async () => {});
    fakeFetch.mockImplementationOnce(async () => response({ ok: true }));
    await act(() => result.current.mutate('wait', {
      targets: ['232:3'], running: true,
    }));
    expect(result.current.data.running).toBe(false);
    data.reservation = { id: 'fast-1', state: 'TEMP_CHARGE', roomName: '2열람실', seatNo: '3' };
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(result.current.data.reservation.id).toBe('fast-1');
    expect(result.current.toast).toBe('배정 완료 · 2열람실 3번');
  });

  it('shows server auto-assignment within the next poll and does not announce it twice', async () => {
    vi.useFakeTimers();
    data.running = true;
    data.interval = 1;
    data.targets = ['232:3'];
    render(<App />);
    await act(async () => {});
    expect(document.getElementById('reservation')).toBeNull();
    data.running = false;
    data.targets = [];
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(document.getElementById('reservation')).toBeNull();
    data.reservation = {
      id: 'auto-1', state: 'TEMP_CHARGE', roomName: '2열람실', seatNo: '3',
    };
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(document.getElementById('toast').textContent).toBe('배정 완료 · 2열람실 3번');
    expect(document.activeElement.id).toBe('reservation');
    const scrolls = Element.prototype.scrollIntoView.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(15000); });
    expect(document.getElementById('toast')).toBeNull();
    expect(Element.prototype.scrollIntoView.mock.calls).toHaveLength(scrolls);
    expect(document.getElementById('reservation-result')).not.toBeNull();
    expect(writes('reserve')).toHaveLength(0);
    expect(writes('refresh')).toHaveLength(0);
  });

  it('detects a new repeat reservation for the same seat even across a cancellation snapshot', async () => {
    vi.useFakeTimers();
    data.reservation = {
      id: 'repeat-1', state: 'TEMP_CHARGE', roomName: '2열람실', seatNo: '3', startedAt: 100,
    };
    data.repeat = { reservationId: 'repeat-1', dueAt: Date.now() / 1000 + 2 };
    render(<App />);
    await act(async () => {});
    expect(document.getElementById('toast')).toBeNull();
    data.reservation = null;
    data.repeat = null;
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    data.reservation = {
      id: 'repeat-2', state: 'TEMP_CHARGE', roomName: '2열람실', seatNo: '3', startedAt: 200,
    };
    data.repeat = { reservationId: 'repeat-2', dueAt: Date.now() / 1000 + 540 };
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(document.getElementById('toast').textContent).toBe('자동 재예약 완료 · 2열람실 3번');
    expect(document.getElementById('reservation-result').textContent).toMatch(/자동 재예약 완료 · .* 확인/);
    expect(document.activeElement.id).not.toBe('reservation');
    expect(screen.getByRole('heading', { name: '좌석 찾기', level: 1 })).toBeTruthy();
    expect(screen.getByRole('button', { name: '내 좌석 보기' })).toBeTruthy();
    expect(writes('repeat')).toHaveLength(0);
    expect(writes('reserve')).toHaveLength(0);
  });

  it('does not announce an unverified or unknown reservation as a success', async () => {
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data).not.toBeNull());
    data.reservation = { id: 'unknown', state: 'TEMP_CHARGE', roomName: '2열람실', seatNo: '3' };
    data.reservationFresh = false;
    await act(() => result.current.refresh());
    expect(result.current.reservationNotice).toBeNull();
    expect(result.current.toast).toBe('');
    data.reservationFresh = true;
    data.reservation.state = 'UNKNOWN';
    await act(() => result.current.refresh());
    expect(result.current.reservationNotice).toBeNull();
  });

  it('refreshes immediately when returning to the browser', async () => {
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data).not.toBeNull());
    data.reservation = { id: 'away-1', state: 'TEMP_CHARGE', roomName: '2열람실', seatNo: '3' };
    await act(async () => { fireEvent(window, new Event('focus')); });
    expect(result.current.data.reservation.id).toBe('away-1');
    expect(result.current.toast).toBe('배정 완료 · 2열람실 3번');
  });

  it('checks more often while waiting or near a repeat deadline', () => {
    expect(pollDelay({ running: true, interval: 1 }, 100)).toBe(1000);
    expect(pollDelay({ running: true, interval: 30 }, 100)).toBe(2000);
    expect(pollDelay({ repeat: { dueAt: 640 } }, 100)).toBe(5000);
    expect(pollDelay({ repeat: { dueAt: 112 } }, 100)).toBe(2000);
    expect(pollDelay({ repeat: { dueAt: 110 } }, 100)).toBe(1000);
    expect(pollDelay({ repeat: { dueAt: 99 } }, 100)).toBe(1000);
    expect(pollDelay({ running: false, repeat: null }, 100)).toBe(15000);
  });

  it('keeps unsaved seat selection when background data is refreshed', async () => {
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data).not.toBeNull());
    act(() => result.current.dispatch({ type: 'toggle', key: '232:3' }));
    data.seats = data.seats.map((item) => ({ ...item, remainingTime: 0 }));
    await act(() => result.current.refresh());
    expect(result.current.selected).toEqual(['232:3']);
    expect(result.current.data.seats[0].remainingTime).toBe(0);
  });

  it('ignores a delayed read from before an automatic-wait mutation', async () => {
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data).not.toBeNull());
    let finish;
    const old = structuredClone(data);
    fakeFetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    let pending;
    act(() => {
      pending = result.current.refresh();
    });
    await act(() =>
      result.current.mutate(
        'wait',
        { targets: ['232:1'], running: true },
        { resetSelection: true },
      ),
    );
    expect(result.current.data.running).toBe(true);
    await act(async () => {
      finish(response(old));
      await pending;
    });
    expect(result.current.data.running).toBe(true);
    expect(result.current.selected).toEqual(['232:1']);
  });

  it('deduplicates double clicks while a write is in flight', async () => {
    const { result } = renderHook(useLibrary);
    await waitFor(() => expect(result.current.data).not.toBeNull());
    let finish;
    fakeFetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    let first;
    act(() => {
      first = result.current.mutate('wait', {
        targets: ['232:1'],
        running: true,
      });
    });
    let second;
    await act(async () => {
      second = await result.current.mutate('wait', {
        targets: ['232:1'],
        running: true,
      });
    });
    expect(second).toBe(false);
    await act(async () => {
      finish(response({ ok: true }));
      await first;
    });
  });
});
