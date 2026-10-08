export default function ActionBar({
  data,
  selected,
  busy,
  reachable,
  quickSeat,
  onQuickReserve,
  onSelection,
  onReservation,
  onWait,
}) {
  const canAct = data.connected && reachable && !busy;
  const switchBlocked = data.reservation && (!data.reservationFresh ||
    !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(data.reservation.state));
  const title = data.running
    ? `${data.targets.length}개 좌석 대기 중`
    : selected.length
      ? `${selected.length}개 선택`
      : '좌석을 선택하세요';
  const detail = data.running
    ? data.interval === 1
      ? '선택 좌석을 빠르게 확인 중'
      : '0~1분 구간에 1초 집중 확인'
    : data.reservation
      ? '현재 좌석 유지 · 빈자리 확인 후 갈아타기'
      : selected.length
        ? '선택 순서대로 시도 · 하나가 잡히면 종료'
        : '여러 좌석 중 하나가 잡히면 종료됩니다.';
  return (
    <div className="action-bar">
      {data.reservation && (
        <div className="held-seat-summary">
          <span>내 좌석 · {data.reservation.roomName} {data.reservation.seatNo}번</span>
          <button className="text-button" onClick={onReservation}>내 좌석 보기</button>
        </div>
      )}
      {quickSeat && (
        <div className="quick-reserve">
          <span>
            {quickSeat.roomName} · {quickSeat.number}번
          </span>
          <button
            id="quick-reserve-button"
            className="text-button"
            disabled={!canAct || !!switchBlocked}
            onClick={() => onQuickReserve(quickSeat)}
          >
            이 자리 바로 예약
          </button>
        </div>
      )}
      <div className="action-main">
        <button
          id="selection-summary"
          className="selection-summary"
          disabled={!selected.length}
          onClick={onSelection}
        >
          <strong>{title}</strong>
          <span>{detail}</span>
        </button>
        <button
          id="start-stop"
          className="primary"
          disabled={
            busy ||
            !reachable ||
            (!data.running &&
              (!canAct || !selected.length || !!switchBlocked))
          }
          onClick={onWait}
        >
          {busy
            ? '처리 중…'
            : data.running
              ? '자동 예약 중지'
              : '자동 예약 시작'}
        </button>
      </div>
    </div>
  );
}
