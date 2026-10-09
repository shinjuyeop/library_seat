import { timeLabel } from '../model';
import RenewalControls from './RenewalControls';

export default function ReservationCard({
  cardRef,
  data,
  notice,
  busy,
  reassigning,
  reachable,
  onRelease,
  onReassign,
  onRenew,
  onAutoRenew,
}) {
  const { reservation, reservationFresh } = data;
  if (!reservation) return null;
  const temporary = reservation?.state === 'TEMP_CHARGE';
  const confirmed = ['CHARGE', 'IN_USE'].includes(reservation?.state);
  const canAct = data.connected && reachable && !busy;
  const confirmationAvailable = data.confirmationRooms?.some(room => String(room) === String(reservation?.roomId));
  return (
    <section id="reservation" className="card reservation" ref={cardRef}
      tabIndex={-1} aria-labelledby="reservation-heading">
      <div className="section-heading">
        <h2 id="reservation-heading">현재 배정</h2>
        <span
          id="reservation-badge"
          className={
            'badge' + (temporary || !reservationFresh ? ' waiting' : '')
          }
        >
          {!reservationFresh
            ? '마지막 조회 정보'
            : temporary
              ? '임시배정 · 확정 필요'
              : confirmed
                ? '배정 확정'
                : '상태 확인 필요'}
        </span>
      </div>
      {notice && notice.id === reservation?.id && reservationFresh && (
        <p id="reservation-result" className="reservation-result">
          {notice.message.split(' · ')[0]}
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
          <RenewalControls data={data} canAct={canAct} busy={busy} reachable={reachable} onRenew={onRenew} onAutoRenew={onAutoRenew} />
          <div className="reservation-bottom">
            {!confirmed && <p className="fine">
              {temporary
                ? confirmationAvailable
                  ? '자동 확정이 완료되지 않았습니다. 공식 앱에서 배정 상태와 NFC 인증을 확인해 주세요.'
                  : '이 열람실은 현장에서 공식 앱으로 NFC 인증을 진행해 주세요.'
                : '공식 앱에서 배정 상태를 확인해 주세요.'}
            </p>}
            {confirmed && confirmationAvailable && <button id="reassign" className="primary"
              disabled={!canAct || !reservationFresh} onClick={() => onReassign(reservation)}>
              {reassigning ? '재배정 진행 중…' : '좌석 반납 후 다시 배정'}
            </button>}
            {reassigning && <p id="reassign-progress" className="fine" role="status">
              좌석 반납 → 같은 좌석 예약 → 배정 확정
            </p>}
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
    </section>
  );
}
