import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { runInNewContext } from 'node:vm';
import swTemplate from '../sw.template.js?raw';
import usePushNotifications from './usePushNotifications';

let registration, sub, requestPermission, fetcher, calls;
const session = { authorized: true, csrf: 'csrf-a' };
beforeEach(() => {
  calls = [];
  sub = { endpoint: 'https://fcm.googleapis.com/test', toJSON: () => ({ endpoint: 'https://fcm.googleapis.com/test', keys: {} }), unsubscribe: vi.fn(async () => true) };
  registration = { pushManager: { getSubscription: vi.fn(async () => null), subscribe: vi.fn(async () => sub) } };
  Object.defineProperty(navigator, 'serviceWorker', { configurable: true, value: { getRegistration: vi.fn(async () => registration), ready: Promise.resolve(registration) } });
  vi.stubGlobal('PushManager', function () {});
  requestPermission = vi.fn(async () => { calls.push('permission'); return 'granted'; });
  vi.stubGlobal('Notification', { requestPermission });
  fetcher = vi.fn(async (url, options = {}) => {
    calls.push(url);
    const result = url.endsWith('/config') ? { configured: true, publicKey: 'BAAA' } : url.endsWith('/status') ? { enabled: false } : { ok: true };
    return { ok: true, json: async () => result };
  });
  vi.stubGlobal('fetch', fetcher);
});
afterEach(() => { cleanup(); delete navigator.serviceWorker; vi.unstubAllGlobals(); });

it('only requests permission on a gesture and subscribes with the session CSRF', async () => {
  const { result } = renderHook(() => usePushNotifications(session));
  await waitFor(() => expect(result.current.configured).toBe(true));
  expect(requestPermission).not.toHaveBeenCalled();
  await act(() => result.current.enable());
  expect(calls.indexOf('permission')).toBeLessThan(calls.indexOf('/api/push/subscribe'));
  expect(registration.pushManager.subscribe.mock.calls[0][0].userVisibleOnly).toBe(true);
  const options = fetcher.mock.calls.find(call => call[0].endsWith('/subscribe'))[1];
  expect(options.headers['X-CSRF-Token']).toBe('csrf-a');
  expect(result.current.enabled).toBe(true);
  await act(() => result.current.disable());
  expect(sub.unsubscribe).toHaveBeenCalledOnce();
  expect(result.current.enabled).toBe(false);
});

it('a denied permission sends no subscription and gives an actionable message', async () => {
  requestPermission.mockResolvedValue('denied');
  const { result } = renderHook(() => usePushNotifications(session));
  await waitFor(() => expect(result.current.configured).toBe(true));
  await act(() => result.current.enable());
  expect(registration.pushManager.subscribe).not.toHaveBeenCalled();
  expect(result.current.error).toContain('기기 설정');
});

it('unsubscribes a previous account on a shared browser', async () => {
  registration.pushManager.getSubscription.mockResolvedValue(sub);
  const { result } = renderHook(() => usePushNotifications(session));
  await waitFor(() => expect(result.current.configured).toBe(true));
  expect(sub.unsubscribe).toHaveBeenCalledOnce();
  expect(result.current.enabled).toBe(false);
});

it('rolls back browser subscription when server registration fails', async () => {
  const base = fetcher.getMockImplementation();
  fetcher.mockImplementation((url, options) => url.endsWith('/subscribe') ? Promise.resolve({ ok: false, status: 409, json: async () => ({ error: 'save failed' }) }) : base(url, options));
  const { result } = renderHook(() => usePushNotifications(session));
  await waitFor(() => expect(result.current.configured).toBe(true));
  await act(() => result.current.enable());
  expect(sub.unsubscribe).toHaveBeenCalledOnce();
  expect(result.current.enabled).toBe(false);
});

it('service worker shows every push, sanitizes destinations, and opens the correct tab', async () => {
  const listeners = {};
  const client = { url: 'https://app.example/', focus: vi.fn(async () => {}), postMessage: vi.fn() };
  const self = { location: { origin: 'https://app.example' }, addEventListener: (name, fn) => { listeners[name] = fn; },
    registration: { showNotification: vi.fn(async () => {}) }, clients: { matchAll: vi.fn(async () => [client]), openWindow: vi.fn() } };
  const source = swTemplate.replace('"__BUILD_ASSETS__"', '[]');
  runInNewContext(source, { self, URL });
  let promise;
  const waitUntil = value => { promise = value; };
  listeners.push({ data: { json: () => ({ id: 'job-1', title: '완료', body: '좌석 확보', url: '/?tab=schedule' }) }, waitUntil });
  await promise;
  expect(self.registration.showNotification.mock.calls[0][1].data.tab).toBe('schedule');
  listeners.notificationclick({ notification: { close: vi.fn(), data: { tab: 'schedule' } }, waitUntil });
  await promise;
  expect(client.postMessage).toHaveBeenCalledWith({ type: 'open-tab', tab: 'schedule' });
  listeners.push({ data: { json: () => ({ url: 'https://evil.example/?tab=settings' }) }, waitUntil });
  await promise;
  expect(self.registration.showNotification.mock.calls[1][1].data.tab).toBe('my');
  listeners.push({ data: { json: () => { throw new Error(); } }, waitUntil });
  await promise;
  expect(self.registration.showNotification).toHaveBeenCalledTimes(3);
});
