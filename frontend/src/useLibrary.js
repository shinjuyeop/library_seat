import { useCallback, useEffect, useReducer, useRef } from 'react';
import { request } from './api';
import { initialModel, libraryReducer } from './model';

export function pollDelay(data, now = Date.now() / 1000, settlingUntil = 0) {
  if (!data) return 5000;
  if (now < settlingUntil) return 1000;
  if (data.running) return data.interval === 1 ? 1000 : 2000;
  if (data.repeat) {
    const untilFastPoll = (data.repeat.dueAt - now - 10) * 1000;
    return Math.max(1000, Math.min(5000, untilFastPoll));
  }
  return 15000;
}

export function useLibrary() {
  const [model, dispatch] = useReducer(libraryReducer, initialModel);
  const latest = useRef(model);
  latest.current = model;
  const session = useRef(null),
    activeRead = useRef(null),
    revision = useRef(0),
    operation = useRef(false),
    settling = useRef({ until: 0, reservationId: null });
  const patch = useCallback(
    (value) => dispatch({ type: 'patch', patch: value }),
    [],
  );
  const invalidate = useCallback(() => {
    revision.current++;
    activeRead.current?.abort();
    activeRead.current = null;
  }, []);

  const loadSession = useCallback(async (signal) => {
    const info = await request('session', { signal });
    if (signal?.aborted) return;
    session.current = info;
    dispatch({ type: 'session', session: info });
    return info;
  }, []);

  const refresh = useCallback(async () => {
    if (!session.current?.authorized || activeRead.current || operation.current)
      return;
    const controller = new AbortController(),
      version = revision.current;
    activeRead.current = controller;
    try {
      const data = await request('state', { signal: controller.signal });
      if (!controller.signal.aborted && revision.current === version) {
        const previous = latest.current.data;
        // The server disarms a job before writing. Keep checking through that gap.
        if (!data.error &&
          ((previous?.running && !data.running && !data.reservation) ||
            (previous?.repeat && !data.repeat))) {
          settling.current = {
            until: Date.now() / 1000 + 15,
            reservationId: previous.reservation?.id,
          };
        }
        if (data.error || (data.reservationFresh && data.reservation &&
          data.reservation.id !== settling.current.reservationId))
          settling.current.until = 0;
        dispatch({ type: 'snapshot', data });
        return data;
      }
    } catch (error) {
      if (controller.signal.aborted || revision.current !== version) return;
      if (error.status === 401) {
        invalidate();
        session.current = { ...session.current, authorized: false };
        dispatch({ type: 'session', session: session.current });
        await loadSession().catch(() => patch({ reachable: false }));
      } else patch({ reachable: false });
    } finally {
      if (activeRead.current === controller) activeRead.current = null;
    }
  }, [invalidate, loadSession, patch]);

  useEffect(() => {
    const controller = new AbortController();
    loadSession(controller.signal)
      .then((info) => {
        if (info?.authorized) refresh();
      })
      .catch((error) => {
        if (!controller.signal.aborted) patch({ initError: error.message });
      });
    return () => {
      controller.abort();
      invalidate();
    };
  }, [loadSession, refresh, invalidate, patch]);

  useEffect(() => {
    let timer,
      disposed = false;
    const poll = async () => {
      const data = !document.hidden ? await refresh() : null;
      if (disposed) return;
      timer = setTimeout(poll, pollDelay(
        data || latest.current.data, Date.now() / 1000, settling.current.until,
      ));
    };
    const resume = () => {
      if (!document.hidden) refresh();
    };
    const offline = () => patch({ reachable: false });
    timer = setTimeout(poll, pollDelay(
      latest.current.data, Date.now() / 1000, settling.current.until,
    ));
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('online', resume);
    window.addEventListener('focus', resume);
    window.addEventListener('offline', offline);
    return () => {
      disposed = true;
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('online', resume);
      window.removeEventListener('focus', resume);
      window.removeEventListener('offline', offline);
    };
  }, [refresh, patch, model.pollRevision, model.data?.running, model.data?.interval, model.data?.repeat?.dueAt]);

  useEffect(() => {
    if (!model.toast) return;
    const timer = setTimeout(() => patch({ toast: '' }), 6500);
    return () => clearTimeout(timer);
  }, [model.toast, patch]);

  const login = async (fields) => {
    if (operation.current) return false;
    operation.current = true;
    invalidate();
    patch({ busy: true, loginError: '' });
    try {
      const info = session.current || (await loadSession());
      const response = await request(
        info.directLogin ? 'library-login' : 'login',
        { body: fields, csrf: info.csrf },
      );
      session.current = { ...info, authorized: true, csrf: response.csrf };
      dispatch({ type: 'session', session: session.current });
      dispatch({ type: 'saved-selection' });
      patch({ data: null, selected: [], reachable: true });
      return true;
    } catch (error) {
      patch({ loginError: error.message });
      return false;
    } finally {
      operation.current = false;
      patch({ busy: false, pollRevision: revision.current });
      await refresh();
    }
  };

  const mutate = async (
    path,
    body,
    { resetSelection = false, message = '' } = {},
  ) => {
    if (operation.current) return false;
    operation.current = true;
    invalidate();
    patch({ busy: true, busyAction: path });
    try {
      await request(path, { body, csrf: session.current?.csrf });
      if (path === 'reserve' || path === 'confirm' || path === 'reassign' || (path === 'wait' && body.running) ||
        (path === 'wait/seat' && body.enabled))
        settling.current = { until: Date.now() / 1000 + 15, reservationId: null };
      if (path === 'release' || (path === 'wait' && !body.running) ||
        (path === 'repeat' && !body.enabled))
        settling.current.until = 0;
      if (path === 'release')
        patch({ reservationNotice: null, observedReservation: null, toast: '' });
      if (resetSelection) dispatch({ type: 'saved-selection' });
      if (message) patch({ toast: message });
      if (path === 'logout') {
        session.current = { ...session.current, authorized: false };
        dispatch({ type: 'session', session: session.current });
        await loadSession();
      }
      return true;
    } catch (error) {
      patch({ toast: error.message });
      if (error.status === 401) {
        session.current = { ...session.current, authorized: false };
        dispatch({ type: 'session', session: session.current });
        await loadSession().catch(() => {});
      }
      return false;
    } finally {
      operation.current = false;
      patch({ busy: false, busyAction: null, pollRevision: revision.current });
      await refresh();
    }
  };

  const reconnect = () => {
    invalidate();
    session.current = { ...session.current, authorized: false };
    dispatch({ type: 'session', session: session.current });
  };
  return {
    ...model,
    dispatch,
    login,
    mutate,
    reconnect,
    refresh,
    retry: () =>
      loadSession()
        .then(refresh)
        .catch((error) => patch({ initError: error.message })),
  };
}
