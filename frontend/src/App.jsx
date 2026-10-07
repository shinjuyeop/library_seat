import { useCallback, useEffect, useRef, useState } from 'react';
import { useLibrary } from './useLibrary';
import { timeLabel } from './model';
import LoginForm, { ConnectionCard } from './components/LoginForm';
import ReservationCard from './components/ReservationCard';
import SeatBrowser from './components/SeatBrowser';
import ActionBar from './components/ActionBar';

export default function App() {
  const library = useLibrary();
  const { session, data, selected, reachable, busy, dispatch, mutate } =
    library;
  const [filters, setFilters] = useState({
    view: 'single',
    room: 'all',
    query: '',
    freeOnly: false,
    limit: 60,
  });
  const [inspectedKey, setInspectedKey] = useState(null),
    [confirmation, setConfirmation] = useState(null);
  const dialog = useRef(null),
    heading = useRef(null),
    reservationCard = useRef(null);
  const showReservation = useCallback(() => {
    reservationCard.current?.scrollIntoView({
      behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
        ? 'instant' : 'smooth',
      block: 'start',
    });
    reservationCard.current?.focus({ preventScroll: true });
  }, []);
  useEffect(() => {
    if (library.reservationNotice) showReservation();
  }, [library.reservationNotice, showReservation]);
  useEffect(() => {
    if (confirmation && !dialog.current?.open) dialog.current?.showModal();
  }, [confirmation]);
  useEffect(() => {
    if (!session?.authorized) {
      dialog.current?.close();
      setConfirmation(null);
      setInspectedKey(null);
    }
  }, [session?.authorized]);
  const confirm = (title, message, execute) =>
    setConfirmation({ title, message, execute });
  const select = useCallback(
    (key) => {
      dispatch({ type: 'toggle', key });
      setInspectedKey(key);
    },
    [dispatch],
  );
  const reserve = (seat) =>
    confirm(
      '이 좌석을 예약할까요?',
      `${seat.roomName} ${seat.number}번을 예약합니다. 성공하면 나머지 대기는 종료됩니다.`,
      () =>
        mutate(
          'reserve',
          { key: seat.key },
          { resetSelection: true },
        ),
    );
  const release = (reservation) =>
    confirm(
      reservation.state === 'TEMP_CHARGE'
        ? '임시배정을 취소할까요?'
        : '좌석을 반납할까요?',
      `${reservation.roomName} ${reservation.seatNo}번을 해제합니다. 이 작업은 되돌릴 수 없습니다.`,
      () =>
        mutate(
          'release',
          { id: reservation.id, state: reservation.state },
          { resetSelection: true },
        ),
    );
  const repeat = (enabled, id) =>
    enabled
      ? confirm(
          '자동 재예약을 켤까요?',
          '임시배정 후 9분마다 취소하고 같은 좌석을 다시 예약합니다. 취소 직후 다른 사람이 잡으면 자리를 잃을 수 있습니다. 화면을 닫아도 계속되며, NFC 인증을 마치면 종료됩니다.',
          () => mutate('repeat', { enabled, id }, { resetSelection: true }),
        )
      : mutate('repeat', { enabled, id });
  const canAct = data?.connected && reachable && !busy;
  const quickSeat =
    filters.view === 'all' &&
    !data?.reservation &&
    !data?.running &&
    selected.includes(inspectedKey)
      ? data?.seats.find(
          (seat) => seat.key === inspectedKey && seat.occupied === false,
        )
      : null;
  const status = !reachable
    ? '연결 끊김'
    : data?.connecting
      ? '로그인 중'
      : !data?.connected
        ? '로그인 필요'
        : data?.error
          ? '확인 필요'
          : data?.running
            ? '예약 대기 중'
            : data?.reservation
              ? data.reservation.state === 'TEMP_CHARGE'
                ? '임시배정 완료'
                : ['CHARGE', 'IN_USE'].includes(data.reservation.state)
                  ? '배정 확정'
                  : '배정 확인 필요'
              : '연결됨';
  const showSelected = () => {
    setFilters({
      view: 'selected',
      room: 'all',
      query: '',
      freeOnly: false,
      limit: 60,
    });
    heading.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    heading.current?.focus({ preventScroll: true });
  };
  return (
    <div className={quickSeat ? 'app has-quick-reserve' : 'app'}>
      <div className="page">
        <header className="app-header">
          <a className="brand" href="/">
            <img src="/assets/icon.svg?v=5" width="32" height="32" alt="" />
            <span>도서관 좌석</span>
          </a>
          <span className="school">건국대학교</span>
        </header>
        {(data?.demo || session?.demo) && (
          <div id="demo-banner" className="notice">
            데모 · 실제 예약은 발생하지 않습니다.
          </div>
        )}
        {!reachable && session?.authorized && (
          <div id="offline" className="notice warning" role="status">
            연결 끊김 · 마지막으로 확인한 정보를 표시합니다.
          </div>
        )}
        <main>
          {!session ? (
            <section className="access card" aria-live="polite">
              <h1>
                {library.initError ? '연결을 확인해 주세요' : '불러오는 중'}
              </h1>
              {library.initError && (
                <>
                  <p className="notice warning">{library.initError}</p>
                  <button className="primary" onClick={library.retry}>
                    다시 연결
                  </button>
                </>
              )}
            </section>
          ) : !session.authorized ? (
            <LoginForm
              directLogin={session.directLogin}
              busy={busy}
              error={library.loginError}
              onLogin={library.login}
            />
          ) : !data ? (
            <section className="card" aria-live="polite">
              <h1>좌석 확인 중</h1>
              <p className="fine">
                {reachable
                  ? '저장된 예약과 좌석 현황을 불러옵니다.'
                  : '네트워크 연결 후 다시 시도해 주세요.'}
              </p>
              {!reachable && (
                <button className="secondary" onClick={library.refresh}>
                  다시 시도
                </button>
              )}
            </section>
          ) : (
            <div id="dashboard">
              <div className="status-line">
                <span
                  id="status-badge"
                  className={
                    'status-badge' +
                    (data.error || !data.connected ? ' waiting' : '')
                  }
                >
                  {status}
                </span>
                <button
                  id="refresh"
                  className="refresh-button"
                  disabled={!canAct}
                  onClick={() =>
                    mutate(
                      'refresh',
                      {},
                      { message: '좌석 현황을 확인하고 있습니다.' },
                    )
                  }
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 6M4 12l2 6a7 7 0 0 0 12-1" />
                  </svg>
                  <span>{busy ? '확인 중…' : '새로고침'}</span>
                </button>
              </div>
              {data.error && (
                <p id="service-error" className="notice warning" role="status">
                  {data.error}
                </p>
              )}
              {!data.connected && !data.demo && (
                <ConnectionCard
                  data={data}
                  busy={busy}
                  onReconnect={library.reconnect}
                  onConnect={(body) =>
                    mutate('connect', body, {
                      message: '도서관 로그인을 시작했습니다.',
                    })
                  }
                />
              )}
              <ReservationCard
                cardRef={reservationCard}
                data={data}
                notice={library.reservationNotice}
                busy={busy}
                reachable={reachable}
                onRelease={release}
                onRepeat={repeat}
              />
              {data.running && (
                <section
                  id="wait-status"
                  className="wait-status"
                  aria-label="자동 예약 상태"
                >
                  <div className="section-heading">
                    <strong>
                      {data.targets.length}개 좌석 자동 예약 대기 중
                    </strong>
                    <span
                      className="badge"
                      title="0~1분 남은 선택 좌석은 1초 주기로 확인합니다. 실제 간격에는 서버 응답 시간이 영향을 줍니다."
                    >
                      {data.interval === 1
                        ? '1초 집중 확인'
                        : `${data.interval}초 확인`}
                    </span>
                  </div>
                  <p>화면을 닫아도 서버에서 계속 확인합니다.</p>
                </section>
              )}
              <SeatBrowser
                data={data}
                selected={selected}
                canAct={canAct}
                busy={busy}
                filters={filters}
                setFilters={setFilters}
                onToggle={select}
                onReserve={reserve}
                onClear={() => dispatch({ type: 'clear-selection' })}
                headingRef={heading}
              />
              <details className="history">
                <summary>최근 활동</summary>
                <ol id="events">
                  {data.events.map((item, index) => (
                    <li key={item.time + ':' + index}>
                      <time>{timeLabel(item.time)}</time>
                      <span>{item.text}</span>
                    </li>
                  ))}
                </ol>
                {!data.events.length && <p className="fine">기록 없음</p>}
              </details>
              <footer>
                <p>임시배정 후 현장에서 공식 앱으로 NFC 인증해야 합니다.</p>
                <div>
                  <span id="auto-login-status">
                    {data.autoLogin ? '자동로그인 켜짐' : ''}
                  </span>
                  {!data.demo && data.connected && (
                    <button
                      id="disconnect"
                      className="text-button"
                      disabled={busy || !reachable}
                      onClick={() =>
                        confirm(
                          '도서관 연결을 해제할까요?',
                          '서버의 자동 예약을 중지하고 저장된 도서관 연결을 삭제합니다. 이미 배정된 좌석은 반납하지 않습니다.',
                          () =>
                            mutate('disconnect', {}, { resetSelection: true }),
                        )
                      }
                    >
                      연결 해제
                    </button>
                  )}
                  <button
                    id="logout"
                    className="text-button"
                    disabled={busy || !reachable}
                    onClick={() =>
                      confirm(
                        '웹앱에서 로그아웃할까요?',
                        '자동 예약 대기와 임시배정 자동 재예약은 서버에서 계속됩니다. 종료하려면 먼저 해당 기능을 꺼 주세요.',
                        () => mutate('logout', {}),
                      )
                    }
                  >
                    로그아웃
                  </button>
                </div>
              </footer>
              <ActionBar
                data={data}
                selected={selected}
                busy={busy}
                reachable={reachable}
                quickSeat={quickSeat}
                onQuickReserve={reserve}
                onSelection={showSelected}
                onReservation={showReservation}
                onWait={() =>
                  mutate(
                    'wait',
                    { targets: selected, running: !data.running },
                    { resetSelection: true },
                  )
                }
              />
            </div>
          )}
        </main>
      </div>
      {library.toast && (
        <div id="toast" role="status">
          {library.toast}
        </div>
      )}
      <dialog
        id="confirm-dialog"
        ref={dialog}
        aria-labelledby="confirm-title"
        onClose={() => setConfirmation(null)}
      >
        <h2 id="confirm-title">{confirmation?.title}</h2>
        <p>{confirmation?.message}</p>
        <div className="dialog-actions">
          <button className="secondary" onClick={() => dialog.current.close()}>
            취소
          </button>
          <button
            className="primary"
            onClick={() => {
              const execute = confirmation?.execute;
              dialog.current.close();
              setConfirmation(null);
              execute?.();
            }}
          >
            확인
          </button>
        </div>
      </dialog>
    </div>
  );
}
