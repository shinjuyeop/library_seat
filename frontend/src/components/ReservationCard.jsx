import { useEffect, useState } from 'react';
import { timeLabel } from '../model';

function RepeatCountdown({ repeat, paused }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!repeat) return;
    const update = () => {
      if (!document.hidden) setNow(Date.now() / 1000);
    };
    update();
    const timer = setInterval(update, 1000);
    document.addEventListener('visibilitychange', update);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', update);
    };
  }, [repeat?.dueAt]);
  const remaining = repeat ? Math.max(0, Math.ceil(repeat.dueAt - now)) : 0;
  return (
    <p id="repeat-status" className="fine">
      {!repeat
        ? '배정 후 9분마다 취소하고 같은 좌석 예약'
        : paused
          ? '연결 확인 중 · 내 좌석을 확인해 주세요'
          : remaining
            ? `${Math.floor(remaining / 60)}분 ${String(remaining % 60).padStart(2, '0')}초 후 재예약`
            : '재예약 시간 · 서버 확인 중'}
    </p>
  );
}

export default function ReservationCard({
  cardRef,
  data,
  notice,
  busy,
  reachable,
  onRelease,
  onRepeat,
}) {
  const { reservation, reservationFresh, repeat } = data;
  if (!reservation && !repeat) return null;
  const temporary = reservation?.state === 'TEMP_CHARGE';
  const confirmed = ['CHARGE', 'IN_USE'].includes(reservation?.state);
  const canAct = data.connected && reachable && !busy;
  return (
    <section id="reservation" className="card reservation" ref={cardRef}
      tabIndex={-1} aria-labelledby="reservation-heading">
      <div className="section-heading">
        <h2 id="reservation-heading">내 좌석</h2>
        <span
          id="reservation-badge"
          className={
            'badge' + (temporary || !reservationFresh ? ' waiting' : '')
          }
        >
          {!reservationFresh
            ? '마지막 조회 정보'
            : temporary
              ? '임시배정 · NFC 필요'
              : confirmed
                ? '배정 확정'
                : '상태 확인 필요'}
        </span>
      </div>
      {notice && notice.id === reservation?.id && reservationFresh && (
        <p id="reservation-result" className="reservation-result">
          {notice.message.startsWith('자동 재예약') ? '자동 재예약 완료' : '배정 완료'}
          {' · '}{timeLabel(notice.at, true)} 확인
        </p>
      )}
      {reservation && (
        <>
          <p id="reservation-seat" className="seat-display">
            {reservation.roomName} · {reservation.seatNo}번
          </p>
          <p id="reservation-time" className="muted">
            {reservation.endTime
              ? `종료 ${reservation.endTime}`
              : reservation.remainingTime != null
                ? `마지막 조회 기준 ${reservation.remainingTime}분 남음`
                : ''}
          </p>
          <div className="reservation-bottom">
            <p className="fine">
              {temporary
                ? '제한 시간 안에 현장에서 공식 앱으로 NFC 인증을 완료하세요.'
                : confirmed
                  ? '배정 확정'
                  : '공식 앱에서 배정 상태를 확인해 주세요.'}
            </p>
            <button
              id="release"
              className="secondary"
              disabled={
                !canAct || !reservationFresh || (!temporary && !confirmed)
              }
              onClick={() => onRelease(reservation)}
            >
              {temporary ? '임시배정 취소' : '좌석 반납'}
            </button>
          </div>
        </>
      )}
      {(temporary || repeat) && (
        <div id="repeat-controls" className="repeat-controls">
          <div className="section-heading">
            <div>
              <h3>임시배정 자동 재예약</h3>
              <RepeatCountdown
                repeat={repeat}
                paused={!reachable || !data.connected || !!data.error}
              />
            </div>
            <button
              id="repeat-toggle"
              className="switch"
              role="switch"
              aria-checked={!!repeat}
              aria-label="임시배정 자동 재예약"
              disabled={
                busy ||
                !reachable ||
                (!repeat && (!canAct || !reservationFresh))
              }
              onClick={() => onRepeat(!repeat, reservation?.id || '')}
            >
              <span />
            </button>
          </div>
          <p className="repeat-note">
            서버에서 실행 · NFC 인증 시 종료
            <br />
            취소 직후 다른 사람이 예약하면 자리를 잃을 수 있습니다.
          </p>
        </div>
      )}
    </section>
  );
}
