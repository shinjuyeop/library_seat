// @vitest-environment node
import { runInNewContext } from 'node:vm';
import { describe, expect, it, vi } from 'vitest';
import source from '../sw.template.js?raw';

const asset = '/assets/index-12345678.js';
const template = source.replaceAll('"__BUILD_ASSETS__"', JSON.stringify([asset]));
const response = name => ({ name, ok: true, clone() { return response(name); } });

function worker(cached) {
  const handlers = {};
  const cache = { match: vi.fn(async () => cached), put: vi.fn(async () => {}) };
  const fetch = vi.fn(async () => response('network'));
  const caches = { open: vi.fn(async () => cache), match: vi.fn(async () => cached) };
  runInNewContext(template, {
    self: { location: { origin: 'https://app.example' }, addEventListener: (name, callback) => { handlers[name] = callback; } },
    caches, fetch, URL,
  });
  const request = (path, method = 'GET') => {
    const event = { request: { url: 'https://app.example' + path, method }, respondWith: vi.fn(), waitUntil: vi.fn() };
    handlers.fetch(event);
    return event;
  };
  return { request, fetch, cache };
}

describe('public shell caching', () => {
  it('loads cached content-hashed assets without a network request', async () => {
    const cached = response('cached');
    const { request, fetch } = worker(cached);
    expect(await request(asset).respondWith.mock.calls[0][0]).toBe(cached);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('fetches and stores a missing asset', async () => {
    const { request, fetch, cache } = worker();
    expect((await request(asset).respondWith.mock.calls[0][0]).name).toBe('network');
    expect(fetch).toHaveBeenCalledOnce();
    expect(cache.put).toHaveBeenCalledOnce();
  });

  it('fetches current HTML and falls back to the shell only when offline', async () => {
    const cached = response('cached HTML');
    const { request, fetch } = worker(cached);
    expect((await request('/').respondWith.mock.calls[0][0]).name).toBe('network');
    fetch.mockRejectedValueOnce(new Error('offline'));
    expect(await request('/').respondWith.mock.calls[0][0]).toBe(cached);
  });

  it('leaves account responses and writes outside the shell cache', () => {
    const { request, fetch, cache } = worker(response('cached'));
    for (const [path, method] of [['/api/state', 'GET'], ['/api/session', 'GET'], ['/api/reserve', 'POST'], [asset, 'POST']])
      expect(request(path, method).respondWith).not.toHaveBeenCalled();
    expect(cache.match).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
