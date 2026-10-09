import { useEffect, useRef } from 'react';
import { seatStatus, timeLabel } from '../model';
import Icon from './Icon';

export default function SeatSheet({ seat, data, selected, canAct, onClose, onReserve, onWait, onUpdateWait, onSelect, onReservation }) {
  const dialog = useRef(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  const status = seatStatus(seat);
  const own = data.reservation?.roomName === seat.roomName && data.reservation?.seatNo === seat.number;
  const waiting = data.running && data.targets.includes(seat.key);
  const full = data.running && !waiting && data.targets.length >= 50;
  const blocked = !canAct || full || seat.occupied == null || (data.reservation &&
    (!data.reservationFresh || !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(data.reservation.state)));
  const act = action => { dialog.current.close(); action(); };
  return <dialog id="seat-sheet" className="bottom-sheet" ref={dialog} aria-labelledby="seat-sheet-title"
    onClose={onClose} onClick={event => { if (event.target === event.currentTarget) dialog.current.close(); }}>
    <div className="sheet-content">
      <div className="sheet-handle" aria-hidden="true" />
      <div className="section-heading"><span className="eyebrow">{seat.roomName}{seat.single ? ' · 1인석' : ''}</span>
        <button className="icon-button" aria-label="좌석 상세 닫기" onClick={() => dialog.current.close()} autoFocus><Icon name="close" /></button></div>
      <h2 id="seat-sheet-title" className="sheet-seat-number">{seat.number}<span>번 좌석</span></h2>
      <span className={'badge ' + (own ? 'free' : status.className)}>{own ? '현재 내 좌석' : status.free ? '빈자리 · 예약 가능' : seat.occupied == null ? '상태 확인 필요' : '사용 중 · ' + status.label}</span>
      <p className="sheet-description">{own ? '내 좌석에서 배정 상태와 남은 시간을 확인하세요.' : data.running
        ? waiting ? data.targets.length === 1 ? '이 좌석을 기다리고 있습니다. 제외하면 대기가 종료됩니다.' : '이 좌석을 기다리고 있습니다. 제외해도 다른 좌석의 대기는 계속됩니다.'
          : full ? '최대 50개 좌석까지 대기할 수 있습니다.' : '진행 중인 대기에 추가합니다. 한 자리가 예약되면 전체 대기가 끝납니다.' : status.free
          ? data.reservation ? '현재 좌석을 반납하고 이 자리로 옮깁니다.' : '예약 후 도서관 현장에서 배정을 확정해 주세요.'
          : '자리가 비면 자동으로 예약합니다. 화면을 닫아도 대기는 계속됩니다.'}</p>
      {seat.checkedAt && <p className="fine">{timeLabel(seat.checkedAt)} 조회 · 실행 전에 상태를 다시 확인합니다.</p>}
      <div className="sheet-actions">
        {own ? <button className="primary wide" onClick={() => act(onReservation)}>내 좌석 보기</button> : data.running ?
          <button id="update-wait-seat" className={waiting ? 'secondary wide' : 'primary wide'} disabled={waiting ? !canAct : !!blocked}
            onClick={() => act(() => onUpdateWait(seat.key, !waiting))}>{waiting ? '대기에서 제외' : '대기에 추가'}</button> : <>
          <button id="quick-reserve-button" className="primary wide" disabled={!!blocked}
            onClick={() => act(() => status.free ? onReserve(seat) : onWait([seat.key]))}>
            {status.free ? data.reservation ? '이 자리로 갈아타기' : '이 자리 바로 예약' : '이 좌석 대기 시작'}
          </button>
          <button className="secondary wide" disabled={!!blocked} onClick={() => act(() => onSelect(seat.key))}>
            {selected.includes(seat.key) ? '선택에서 빼기' : '여러 좌석 선택에 추가'}
          </button>
        </>}
      </div>
    </div>
  </dialog>;
}
