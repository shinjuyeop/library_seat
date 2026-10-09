import { useState } from 'react';
import Icon from './Icon';
import { shortSeatLabel } from '../model';

const slots = Array.from({ length: 42 }, (_, i) => `${String(5 + Math.floor(i / 6)).padStart(2, '0')}:${String(i % 6 * 10).padStart(2, '0')}`);
const formatDate = stamp => new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'long', day: 'numeric', weekday: 'short', hour: '2-digit', minute: '2-digit', hour12: false,
}).format(stamp * 1000);

export default function ScheduledBooking({ data, busy, reachable, draft, onSave, onCancel }) {
  const job = data.scheduledBooking;
  const [editing, setEditing] = useState(false);
  const [room, setRoom] = useState(() => String(draft?.roomId || job?.roomId || data.confirmationRooms?.[0] || ''));
  const [key, setKey] = useState(() => draft?.key || job?.key || '');
  const [time, setTime] = useState(() => job ? new Date(job.dueAt * 1000 + 9 * 3600000).toISOString().slice(11, 16) : '05:00');
  const window = data.scheduleWindow;
  const pending = job?.status === 'pending', working = job?.status === 'working';
  const rooms = data.rooms.filter(item => data.confirmationRooms?.includes(item.id));
  const catalog = new Map(data.seats.map(seat => [seat.key, seat]));
  const favorites = (data.favorites || []).map(key => catalog.get(key))
    .filter(seat => seat && data.confirmationRooms?.includes(seat.roomId));
  const seats = room === 'favorites' ? favorites : data.seats.filter(item => String(item.roomId) === room);
  const allowed = data.connected && reachable && !busy && window?.open && !working;
  const form = !working && (!pending || editing || draft);
  const save = async event => {
    event.preventDefault();
    const dueAt = Date.parse(`${window.date}T${time}:00+09:00`) / 1000;
    if (await onSave({ key, dueAt })) setEditing(false);
  };
  return <>
    {job && <section className={'card schedule-summary ' + (job.status === 'failed' ? 'schedule-failed' : '')} aria-labelledby="scheduled-heading">
      <div className="section-heading"><h2 id="scheduled-heading">{pending ? '예약 대기 중' : working ? '예약 실행 중' : job.status === 'succeeded' ? '시간 예약 완료' : job.status === 'failed' ? '시간 예약 실패' : '시간 예약 취소됨'}</h2><Icon name="clock" /></div>
      <p className="schedule-date">{formatDate(job.dueAt)}</p>
      <strong>{job.roomName} · {job.number}번</strong>
      <p className="fine" role="status">{pending ? '좌석 예약 → 배정확정 → 자동 연장' : working ? `${job.stage} 중…` : `${job.status === 'failed' ? job.stage + ' · ' : ''}${job.result}`}</p>
      {pending && <div className="schedule-actions"><button className="secondary" disabled={busy || !reachable || !window?.open} onClick={() => setEditing(!editing)}>{editing ? '변경 닫기' : '변경'}</button>
        <button className="text-button destructive" disabled={busy || !reachable} onClick={() => onCancel(job.id)}>시간 예약 취소</button></div>}
    </section>}
    {form && <section className="card schedule-card" aria-labelledby="schedule-heading">
      <h2 id="schedule-heading">{pending ? '예약 변경' : '오전 좌석 예약'}</h2>
      {window && <p className="schedule-date">{formatDate(window.closesAt).replace(/ 05:00$/, '')}</p>}
      <form onSubmit={save} className="schedule-form">
        <label>예약 시간<select aria-label="예약 시간" value={time} onChange={e => setTime(e.target.value)} disabled={!allowed}>
          {slots.map(slot => <option key={slot} value={slot}>{slot}</option>)}</select></label>
        <div className="schedule-fields"><label>열람실<select aria-label="시간 예약 열람실" value={room} disabled={!allowed} onChange={e => { setRoom(e.target.value); setKey(''); }}>
          <option value="favorites">선호좌석</option>
          {!rooms.length && <option value="">지원 열람실 없음</option>}{rooms.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <label>좌석 번호<select aria-label="시간 예약 좌석 번호" value={key} disabled={!allowed} onChange={e => setKey(e.target.value)}>
            <option value="">{room === 'favorites' && !seats.length ? '등록한 선호좌석 없음' : '선택'}</option>{seats.map(seat => <option key={seat.key} value={seat.key}>{room === 'favorites' ? shortSeatLabel(seat) : `${seat.number}번`}</option>)}</select></label></div>
        <p className="fine">배정확정 가능한 열람실만 표시됩니다. 현재 사용 중인 좌석도 선택할 수 있어요.</p>
        <button className="primary wide" type="submit" disabled={!allowed || !seats.some(seat => seat.key === key)}>{busy ? '저장 중…' : pending ? '시간 예약 변경' : '시간 예약 등록'}</button>
      </form>
    </section>}
    {!window?.open && <p className="notice">등록·변경은 전날 낮 12시부터 당일 오전 5시 전까지 가능합니다.</p>}
    <div className="schedule-guide">
      <p>앱을 닫아도 예약 시간에 한 번 실행합니다. 실패하면 종료하며, 결과는 이 화면과 알림에서 확인할 수 있어요.</p>
      <p>실행할 때 이용 중인 좌석이 있으면 현재 좌석을 유지하고 시간 예약은 종료합니다. 예약을 시작하면 기존 좌석 대기는 종료됩니다.</p>
      <p>자동 연장은 잔여 1시간 59분에 실행됩니다. 연장 횟수가 0이면 반납 후 재배정하므로 자리를 잃을 수 있어요.</p>
    </div>
  </>;
}
