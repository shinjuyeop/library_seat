import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useLibrary } from './useLibrary';
import useSeatNavigation, { defaultFilters } from './useSeatNavigation';
import LoginForm, { ConnectionCard } from './components/LoginForm';
import ReservationCard from './components/ReservationCard';
import SeatBrowser from './components/SeatBrowser';
import SeatSheet from './components/SeatSheet';
import ActionBar, { Navigation } from './components/ActionBar';
import WaitingList from './components/WaitingList';
import Settings from './components/Settings';
import Icon from './components/Icon';
import ScheduledBooking from './components/ScheduledBooking';
import usePushNotifications from './usePushNotifications';

const titles = { find: '좌석 찾기', my: '내 좌석', schedule: '시간 예약', settings: '설정' };

export default function App() {
  const library = useLibrary();
  const { session, data, selected, reachable, busy, dispatch, mutate } = library;
  const push = usePushNotifications(session);
  const [scheduleDraft, setScheduleDraft] = useState(null);
  const [tab, setTab] = useState('find');
  const [selecting, setSelecting] = useState(false);
  const [inspectedKey, setInspectedKey] = useState(null);
  const [confirmation, setConfirmation] = useState(null);
  const dialog = useRef(null), pageHeading = useRef(null), reservationCard = useRef(null);
  const displayedNotice = useRef(null);
  const scrolls = useRef({}), currentTab = useRef('find'), navigated = useRef(false);
  const goTo = useCallback(next => {
    if (next === currentTab.current) return;
    scrolls.current[currentTab.current] = window.scrollY;
    currentTab.current = next;
    navigated.current = true;
    setTab(next);
  }, []);
  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get('tab');
    if (session?.authorized && titles[target]) goTo(target);
  }, [session?.authorized, goTo]);
  useEffect(() => {
    const open = event => { if (event.data?.type === 'open-tab' && titles[event.data.tab]) goTo(event.data.tab); };
    navigator.serviceWorker?.addEventListener('message', open);
    return () => navigator.serviceWorker?.removeEventListener('message', open);
  }, [goTo]);
  useLayoutEffect(() => {
    if (navigated.current) {
      window.scrollTo({ top: scrolls.current[tab] || 0, behavior: 'instant' });
      pageHeading.current?.focus({ preventScroll: true });
    }
  }, [tab]);
  const { filters, setFilters, openRoom, backToRooms } = useSeatNavigation({
    active: tab === 'find', onNavigate: () => {
      setInspectedKey(null);
      dialog.current?.close();
      setConfirmation(null);
      goTo('find');
    },
  });
  useEffect(() => {
    const notice = library.reservationNotice;
    if (!notice || notice.message.startsWith('자동 재예약') || notice.message.startsWith('연장 완료')) return;
    setInspectedKey(null);
    setSelecting(false);
    goTo('my');
  }, [library.reservationNotice, goTo]);
  useLayoutEffect(() => {
    const notice = library.reservationNotice;
    if (tab === 'my' && notice && notice !== displayedNotice.current && !notice.message.startsWith('자동 재예약')) {
      displayedNotice.current = notice;
      reservationCard.current?.focus({ preventScroll: true });
      window.scrollTo({ top: 0, behavior: 'instant' });
    }
  }, [tab, library.reservationNotice]);
  useEffect(() => {
    if (confirmation && !dialog.current?.open) dialog.current?.showModal();
  }, [confirmation]);
  useEffect(() => {
    if (session && !session.authorized) {
      dialog.current?.close();
      setConfirmation(null);
      setInspectedKey(null);
      setSelecting(false);
      setScheduleDraft(null);
      setFilters(defaultFilters);
      scrolls.current = {};
      currentTab.current = 'find';
      setTab('find');
    }
  }, [session?.authorized]);
  // Keep the fixed navigation out of the software keyboard's way.
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const update = () => {
      const editing = document.activeElement?.matches('input:not([type=checkbox]), textarea');
      document.documentElement.classList.toggle('keyboard-open', !!editing && window.innerHeight - viewport.height > 140);
    };
    viewport.addEventListener('resize', update);
    document.addEventListener('focusin', update);
    document.addEventListener('focusout', update);
    return () => {
      viewport.removeEventListener('resize', update);
      document.removeEventListener('focusin', update);
      document.removeEventListener('focusout', update);
      document.documentElement.classList.remove('keyboard-open');
    };
  }, []);
  const confirm = (title, message, execute, action = '확인') => setConfirmation({ title, message, execute, action });
  const toggle = useCallback(key => dispatch({ type: 'toggle', key }), [dispatch]);
  const updateWait = (key, enabled) => mutate('wait/seat', { key, enabled }, {
    resetSelection: true,
    message: enabled ? '진행 중인 대기에 추가했습니다.' : '대기에서 제외했습니다.',
  });
  const changeSelection = key => data.running ? updateWait(key, !data.targets.includes(key)) : toggle(key);
  const reserve = seat => confirm(data.reservation ? '이 좌석으로 갈아탈까요?' : '이 좌석을 예약할까요?',
    `${seat.roomName} ${seat.number}번을 예약합니다. ${data.reservation
      ? '기존 좌석을 취소·반납하고, 새 좌석 예약에 실패하면 원래 좌석 재예약을 시도합니다. 원래 좌석을 잃을 수 있습니다. ' : ''}
성공하면 나머지 대기는 종료되고, 임시배정은 9분마다 자동 재예약합니다.`,
    () => mutate('reserve', { key: seat.key }, { resetSelection: true }), data.reservation ? '갈아타기' : '예약하기');
  const release = reservation => confirm(reservation.state === 'TEMP_CHARGE' ? '임시배정을 취소할까요?' : '좌석을 반납할까요?',
    `${reservation.roomName} ${reservation.seatNo}번을 해제합니다. 이 작업은 되돌릴 수 없습니다.`,
    () => mutate('release', { id: reservation.id, state: reservation.state }, { resetSelection: true }),
    reservation.state === 'TEMP_CHARGE' ? '임시배정 취소' : '반납하기');
  const confirmAllocation = reservation => confirm('배정을 확정할까요?',
    `${reservation.roomName} ${reservation.seatNo}번을 확정합니다. 등록된 열람실 태그 정보로 확인을 요청하므로 도서관 현장에서 진행해 주세요. 요청을 시작하면 자동 재예약과 갈아타기 대기는 중지됩니다.`,
    () => mutate('confirm', { id: reservation.id }, { resetSelection: true }), '배정 확정');
  const reassign = reservation => confirm('반납 후 같은 좌석을 다시 배정할까요?',
    `${reservation.roomName} ${reservation.seatNo}번을 반납한 뒤 바로 같은 좌석을 예약하고 배정확정까지 진행합니다. 반납 직후 다른 사람이 예약하면 자리를 잃을 수 있습니다. 자동 재예약과 갈아타기 대기는 중지됩니다. 도서관 현장에서 진행해 주세요.`,
    () => mutate('reassign', { id: reservation.id }, { resetSelection: true }), '반납 후 다시 배정');
  const renew = reservation => confirm('좌석을 연장할까요?',
    `${reservation.roomName} ${reservation.seatNo}번의 이용 시간을 연장합니다. 등록된 열람실 태그로 확인하므로 도서관 현장에서 진행해 주세요.`,
    () => mutate('renew', { id: reservation.id }), '연장하기');
  const autoRenew = (enabled, id) => enabled ? confirm('자동 연장을 켤까요?',
    '잔여 1시간 59분에 연장합니다. 남은 횟수가 0이면 같은 좌석을 반납·재배정·확정하고 자동 연장을 계속합니다. 반납 사이 다른 사람이 예약하면 자리를 잃을 수 있습니다. 23시~05시에는 실패 후 재시도와 자동 재배정을 멈춥니다. 도서관에서 이용 중일 때 켜 주세요.',
    () => mutate('auto-renew', { enabled, id }), '자동 연장 켜기') : mutate('auto-renew', { enabled, id });
  const repeat = (enabled, id) => enabled ? confirm('자동 재예약을 켤까요?',
    '임시배정 후 9분마다 취소하고 같은 좌석을 다시 예약합니다. 취소 직후 다른 사람이 잡으면 자리를 잃을 수 있습니다. 화면을 닫아도 계속되며, NFC 인증을 마치면 종료됩니다.',
    () => mutate('repeat', { enabled, id }), '자동 재예약 켜기') : mutate('repeat', { enabled, id });
  const startWait = targets => {
    const execute = async () => {
      const ok = await mutate('wait', { targets, running: true }, { resetSelection: true });
      if (ok) { setSelecting(false); scrolls.current.my = 0; goTo('my'); }
    };
    if (data.reservation) confirm('자동 갈아타기를 시작할까요?',
      '선택한 자리가 비면 갈아타고, 예약이 거절되면 원래 좌석 복구를 시도합니다. 태그가 등록된 열람실은 새 좌석과 복구 좌석 모두 자동으로 배정확정합니다. 복구·확정에 성공하면 대기를 계속하고, 확정에 실패하면 대기를 중지합니다. 다른 사람이 먼저 잡으면 자리를 잃을 수 있습니다.',
      execute, '갈아타기 대기 시작');
    else execute();
  };
  const showSelected = () => {
    setFilters({ ...defaultFilters, view: 'selected' });
    setSelecting(true);
    scrolls.current.find = 0;
    goTo('find');
    if (tab === 'find') pageHeading.current?.scrollIntoView({ block: 'start' });
  };
  const findMoreSeats = () => {
    setSelecting(false);
    setFilters(previous => previous.view === 'selected' ? { ...previous, view: 'all' } : previous);
    goTo('find');
  };
  const canAct = data?.connected && reachable && !busy;
  const ready = session?.authorized && data;
  const inspectedSeat = data?.seats.find(seat => seat.key === inspectedKey);
  const hasDock = ready && ((tab === 'find' && !data.running && (selecting || selected.length > 0)) ||
    (tab !== 'my' && (data.reservation || data.running || data.repeat)));
  const status = !reachable ? '연결 끊김' : data?.connecting ? '로그인 중' : !data?.connected ? '로그인 필요'
    : data?.error ? '확인 필요' : '연결됨';
  const waitingList = ready && <WaitingList data={data} selected={selected} busy={busy} reachable={reachable} onSelection={showSelected} onAdd={findMoreSeats}
    onStop={() => mutate('wait', { targets: data.targets, running: false }, { resetSelection: true })} />;
  return <div className={'app' + (ready ? ' signed-in' : '') + (hasDock ? ' has-dock' : '')}>
    <div className="page">
      <header className="app-header"><button className="brand" onClick={() => goTo('find')} aria-label="도서관 좌석 홈">
        <img src="/assets/icon.svg?v=5" width="30" height="30" alt="" /><span>도서관 좌석</span></button>
        <span className="school">건국대학교</span></header>
      {(data?.demo || session?.demo) && <div id="demo-banner" className="demo-banner">데모 · 실제 예약은 발생하지 않습니다.</div>}
      <main>
        {!session ? <section className="access card" aria-live="polite"><h1>{library.initError ? '연결을 확인해 주세요' : '불러오는 중'}</h1>
          {library.initError && <><p className="notice warning">{library.initError}</p><button className="primary" onClick={library.retry}>다시 연결</button></>}
        </section> : !session.authorized ? <LoginForm directLogin={session.directLogin} busy={busy} error={library.loginError} onLogin={library.login} />
          : !data ? <section className="card" aria-live="polite"><h1>좌석 확인 중</h1><p className="fine">{reachable ? '저장된 예약과 좌석 현황을 불러옵니다.' : '네트워크 연결 후 다시 시도해 주세요.'}</p>
            {!reachable && <button className="secondary" onClick={library.refresh}>다시 시도</button>}</section> : <div id="dashboard">
            <div className="page-heading">
              <h1 ref={pageHeading} tabIndex={-1}>{titles[tab]}</h1>
              {tab !== 'settings' && <button id="refresh" className="refresh-button" disabled={!canAct}
                onClick={() => mutate('refresh', {}, { message: '좌석 현황을 확인하고 있습니다.' })}><Icon name="refresh" /><span>{busy ? '확인 중…' : '새로고침'}</span></button>}
            </div>
            {!reachable && <p id="offline" className="notice warning" role="status">연결 끊김 · 마지막으로 확인한 정보를 표시합니다.</p>}
            {data.error && <p id="service-error" className="notice warning" role="status">{data.error}</p>}
            {!data.connected && !data.demo && tab !== 'settings' && <div className="notice warning">도서관에 다시 연결해 주세요.<button className="text-button" onClick={() => goTo('settings')}>연결 설정</button></div>}
            <div hidden={tab !== 'find'} id="panel-find">
              <SeatBrowser data={data} selected={selected} canAct={canAct} busy={busy} filters={filters} setFilters={setFilters}
                onOpenRoom={openRoom} onBackToRooms={backToRooms}
                selecting={selecting && !data.running} onSelecting={setSelecting} onToggle={changeSelection} onInspect={setInspectedKey}
                onClear={() => dispatch({ type: 'clear-selection' })} />
            </div>
            <div hidden={tab !== 'my'} id="panel-my" className="detail-page">
              {data.running && waitingList}
              {!data.reservation && !data.repeat && <section className="empty my-empty"><Icon name="seat" />
                <strong>{data.reservationFresh ? data.running ? '빈자리를 기다리고 있어요' : '아직 배정된 좌석이 없어요' : '내 좌석을 확인하고 있어요'}</strong>
                <p>{data.running ? '예약에 성공하면 여기에 좌석이 표시됩니다.' : data.reservationFresh ? '원하는 자리를 찾고 예약을 시작해 보세요.' : '다시 연결한 뒤 최신 배정 상태를 확인해 주세요.'}</p>
                <button className="secondary" onClick={() => goTo('find')}>좌석 찾아보기</button></section>}
              <ReservationCard cardRef={reservationCard} data={data} notice={library.reservationNotice} busy={busy} reassigning={library.busyAction === 'reassign'} reachable={reachable} onRelease={release} onRepeat={repeat} onConfirm={confirmAllocation} onReassign={reassign} onRenew={renew} onAutoRenew={autoRenew} />
              {!data.running && waitingList}
              {data.reservation && <button className="browse-link" onClick={() => goTo('find')}>다른 좌석 찾아보기<Icon name="chevron" /></button>}
            </div>
            <div hidden={tab !== 'schedule'} id="panel-schedule" className="detail-page">
              <ScheduledBooking key={scheduleDraft?.key || data.scheduledBooking?.id || 'new'} data={data} busy={busy} reachable={reachable} draft={scheduleDraft}
                onSave={async body => { const ok = await mutate('schedule', body, { message: '시간 예약을 저장했습니다.' }); if (ok) setScheduleDraft(null); return ok; }}
                onCancel={id => mutate('schedule/cancel', { id }, { message: '시간 예약을 취소했습니다.' })} />
            </div>
            <div hidden={tab !== 'settings'} id="panel-settings" className="detail-page">
              <div className="status-line"><span id="status-badge" className={'status-badge' + (status !== '연결됨' ? ' waiting' : '')}>{status}</span></div>
              {!data.connected && !data.demo && <ConnectionCard data={data} busy={busy} onReconnect={library.reconnect}
                onConnect={body => mutate('connect', body, { message: '도서관 로그인을 시작했습니다.' })} />}
              <Settings data={data} busy={busy || push.loading} reachable={reachable} push={push}
                onDisconnect={() => confirm('도서관 연결을 해제할까요?', '서버의 자동 예약을 중지하고 저장된 도서관 연결을 삭제합니다. 이미 배정된 좌석은 반납하지 않습니다.',
                  () => mutate('disconnect', {}, { resetSelection: true }), '연결 해제')}
                onLogout={() => confirm('웹앱에서 로그아웃할까요?', '시간 예약과 자동 예약·연장은 서버에서 계속됩니다. 이 기기의 알림은 꺼집니다. 자동 실행을 종료하려면 먼저 해당 기능을 꺼 주세요.',
                  async () => { try { await push.disconnect(); await mutate('logout', {}); } catch (error) { dispatch({ type: 'patch', patch: { toast: error.message } }); } }, '로그아웃')} />
            </div>
          </div>}
      </main>
    </div>
    {ready && <div className="bottom-dock"><ActionBar data={data} selected={selected} selecting={selecting} tab={tab} busy={busy} reachable={reachable}
      onSelection={showSelected} onReservation={() => goTo('my')} onWait={startWait} /><Navigation tab={tab} onChange={goTo} /></div>}
    {library.toast && <div id="toast" role="status">{library.toast}</div>}
    {ready && inspectedSeat && <SeatSheet key={inspectedSeat.key} seat={inspectedSeat} data={data} selected={selected} canAct={canAct}
      onClose={() => setInspectedKey(null)} onReserve={reserve} onWait={startWait} onUpdateWait={updateWait} onReservation={() => goTo('my')}
      onSchedule={seat => { setScheduleDraft(seat); goTo('schedule'); }}
      onSelect={key => { toggle(key); setSelecting(true); }} />}
    <dialog id="confirm-dialog" ref={dialog} aria-labelledby="confirm-title" onClose={() => setConfirmation(null)}>
      <h2 id="confirm-title">{confirmation?.title}</h2><p>{confirmation?.message}</p>
      <div className="dialog-actions"><button className="secondary" onClick={() => dialog.current.close()} autoFocus>취소</button>
        <button className="primary" disabled={busy || !reachable} onClick={() => {
          const execute = confirmation?.execute;
          dialog.current.close(); setConfirmation(null); execute?.();
        }}>{confirmation?.action || '확인'}</button></div>
    </dialog>
  </div>;
}
