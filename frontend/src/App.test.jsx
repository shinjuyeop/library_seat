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
import { useLibrary } from './useLibrary';

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
    }
    if (path === 'repeat')
      data.repeat = body.enabled ? { dueAt: Date.now() / 1000 + 540 } : null;
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
const allSeats = () =>
  fireEvent.click(
    screen.getByRole('button', { name: '전체 좌석', exact: true }),
  );
const seat = (name) => screen.getByRole('button', { name });
const writes = (path) =>
  requests.filter(
    (item) => item.path === path && item.options.method === 'POST',
  );

describe('React app with the existing account API', () => {
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

  it('groups numeric search results once per room and preserves selected order', async () => {
    await openApp();
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '3' } });
    expect(document.querySelectorAll('.room-group')).toHaveLength(3);
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(9);
    fireEvent.click(seat('5열람실 3번 빈자리 대기 선택'));
    fireEvent.click(seat('2열람실 31번 1분 대기 선택'));
    fireEvent.click(screen.getByRole('button', { name: '선택한 좌석 2' }));
    const chosen = [...document.querySelectorAll('.seat-select')].map((item) =>
      item.getAttribute('aria-label'),
    );
    expect(chosen[0]).toContain('5열람실 3번');
    expect(chosen[1]).toContain('2열람실 31번');
    fireEvent.click(screen.getByRole('button', { name: '자동 예약 시작' }));
    await waitFor(() => expect(writes('wait')).toHaveLength(1));
    expect(writes('wait')[0].body).toEqual({
      targets: ['107:3', '232:31'],
      running: true,
    });
    await screen.findByRole('button', { name: '자동 예약 중지' });
    expect(seat('5열람실 3번 빈자리 대기 선택').disabled).toBe(true);
  });

  it('requires confirmation for immediate booking and uses server-reported TEMP state', async () => {
    await openApp();
    allSeats();
    fireEvent.click(seat('2열람실 3번 빈자리 대기 선택'));
    fireEvent.click(screen.getByRole('button', { name: '이 자리 바로 예약' }));
    let dialog = screen.getByRole('dialog');
    fireEvent.click(
      within(dialog).getByRole('button', { name: '취소', exact: true }),
    );
    expect(writes('reserve')).toHaveLength(0);
    fireEvent.click(screen.getByRole('button', { name: '이 자리 바로 예약' }));
    dialog = screen.getByRole('dialog');
    fireEvent.click(
      within(dialog).getByRole('button', { name: '확인', exact: true }),
    );
    await screen.findByText('임시배정 · NFC 필요');
    expect(writes('reserve')).toHaveLength(1);
    expect(writes('reserve')[0].body).toEqual({ key: '232:3' });
    expect(writes('reserve')[0].options.headers['X-CSRF-Token']).toBe(
      'test-csrf',
    );
    expect(
      screen
        .getByRole('switch', { name: '임시배정 자동 재예약' })
        .getAttribute('aria-checked'),
    ).toBe('false');
  });

  it('enables repeat explicitly and disabling it never releases the seat', async () => {
    data.reservation = {
      id: 'temp-id',
      seatNo: '3',
      roomName: '2열람실',
      state: 'TEMP_CHARGE',
    };
    await openApp();
    fireEvent.click(screen.getByRole('switch'));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: '확인',
        exact: true,
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe(
        'true',
      ),
    );
    expect(writes('repeat')[0].body).toEqual({ enabled: true, id: 'temp-id' });
    expect(screen.getByText(/후 재예약/)).toBeTruthy();
    fireEvent.click(screen.getByRole('switch'));
    await waitFor(() =>
      expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe(
        'false',
      ),
    );
    expect(writes('release')).toHaveLength(0);
    expect(screen.getByText('2열람실 · 3번')).toBeTruthy();
  });

  it('disables mutations when offline while keeping seat information readable', async () => {
    await openApp();
    allSeats();
    fireEvent.click(seat('2열람실 3번 빈자리 대기 선택'));
    fireEvent(window, new Event('offline'));
    expect(
      screen.getByRole('button', { name: '자동 예약 시작' }).disabled,
    ).toBe(true);
    expect(
      screen.getByRole('button', { name: '이 자리 바로 예약' }).disabled,
    ).toBe(true);
    expect(document.querySelectorAll('.seat-cell')).toHaveLength(12);
    expect(writes('reserve')).toHaveLength(0);
  });

  it('passes the original reservation identity when a confirmation becomes stale', async () => {
    data.reservation = {
      id: 'temp-id',
      seatNo: '3',
      roomName: '2열람실',
      state: 'TEMP_CHARGE',
    };
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '임시배정 취소' }));
    data.reservation = { ...data.reservation, state: 'CHARGE' };
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: '확인',
        exact: true,
      }),
    );
    await waitFor(() => expect(writes('release')).toHaveLength(1));
    expect(writes('release')[0].body).toEqual({
      id: 'temp-id',
      state: 'TEMP_CHARGE',
    });
  });

  it('logs out without stopping server jobs or showing another account state', async () => {
    data.running = true;
    data.targets = ['102:1'];
    await openApp();
    fireEvent.click(screen.getByRole('button', { name: '로그아웃' }));
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: '확인',
        exact: true,
      }),
    );
    await screen.findByRole('heading', { name: '로그인' });
    expect(writes('logout')).toHaveLength(1);
    expect(writes('wait')).toHaveLength(0);
    expect(screen.queryByRole('heading', { name: '좌석 찾기' })).toBeNull();
  });
});

describe('polling and concurrent user actions', () => {
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
