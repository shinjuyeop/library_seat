import { useEffect, useRef, useState } from 'react';
import { request } from './api';

const defaults = { assignment: true, renewal: true, failure: true };
const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
const decode = value => Uint8Array.from(atob(value.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - value.length % 4) % 4)), c => c.charCodeAt(0));

export default function usePushNotifications(session) {
  const [state, setState] = useState({ supported: supported(), configured: false, loading: false, enabled: false, preferences: defaults, error: '', message: '' });
  const subscription = useRef(null), config = useRef(null), active = useRef(session);
  active.current = session;
  const patch = value => setState(previous => ({ ...previous, ...value }));
  const post = (path, body) => {
    if (active.current?.csrf !== session?.csrf || !active.current?.authorized) throw new Error('로그인 상태가 바뀌었습니다. 다시 확인해 주세요.');
    return request('push/' + path, { body, csrf: session.csrf });
  };
  useEffect(() => {
    let disposed = false;
    subscription.current = null;
    config.current = null;
    setState({ supported: supported(), configured: false, loading: false, enabled: false, preferences: defaults, error: '', message: '' });
    if (!session?.authorized || !supported()) return;
    const init = async () => {
      const settings = await request('push/config');
      const registration = await navigator.serviceWorker.getRegistration('/');
      const sub = await registration?.pushManager.getSubscription();
      if (disposed) return;
      const status = sub ? await post('status', { endpoint: sub.endpoint }) : null;
      if (disposed) return;
      // A shared browser must stop delivering notifications for a previous account.
      if (sub && !status?.enabled) await sub.unsubscribe();
      if (disposed) return;
      config.current = settings;
      subscription.current = status?.enabled ? sub : null;
      patch({ configured: settings.configured, enabled: !!status?.enabled, preferences: status?.preferences || defaults });
    };
    init().catch(() => { if (!disposed) patch({ error: '알림 설정을 불러오지 못했습니다. 화면을 다시 열어 주세요.' }); });
    return () => { disposed = true; };
  }, [session?.authorized, session?.csrf]);

  const run = async action => {
    patch({ loading: true, error: '', message: '' });
    try { await action(); }
    catch (error) { if (active.current?.csrf === session?.csrf) patch({ error: error.message || '알림 설정에 실패했습니다.' }); }
    finally { if (active.current?.csrf === session?.csrf) patch({ loading: false }); }
  };
  const enable = () => {
    // Keep the permission request directly in the user gesture, including on iOS.
    const permission = Notification.requestPermission();
    return run(async () => {
      if (await permission !== 'granted') throw new Error('알림 권한이 꺼져 있습니다. 기기 설정에서 허용해 주세요.');
      const registration = await navigator.serviceWorker.ready;
      let sub = await registration.pushManager.getSubscription();
      if (!sub) sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: decode(config.current.publicKey) });
      try { await post('subscribe', { subscription: sub.toJSON(), preferences: state.preferences }); }
      catch (error) { await sub.unsubscribe(); throw error; }
      subscription.current = sub;
      patch({ enabled: true, message: '이 기기의 알림을 켰습니다.' });
    });
  };
  const disconnect = async () => {
    const registration = supported() ? await navigator.serviceWorker.getRegistration('/') : null;
    const sub = subscription.current || await registration?.pushManager.getSubscription();
    if (sub) {
      await post('unsubscribe', { endpoint: sub.endpoint });
      await sub.unsubscribe();  // false also means the browser subscription is already gone.
    }
    subscription.current = null;
    patch({ enabled: false });
  };
  return { ...state, enable, disconnect,
    disable: () => run(disconnect),
    update: preferences => run(async () => {
      if (!subscription.current) return;
      await post('subscribe', { subscription: subscription.current.toJSON(), preferences });
      patch({ preferences });
    }),
    test: () => run(async () => {
      await post('test', { endpoint: subscription.current.endpoint });
      patch({ message: '테스트 알림을 발송했습니다. 기기 알림을 확인해 주세요.' });
    }),
  };
}
