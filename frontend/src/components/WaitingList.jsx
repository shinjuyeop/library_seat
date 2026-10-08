import Icon from './Icon';

export default function WaitingList({ data, selected, busy, reachable, onStop, onSelection }) {
  const keys = data.running ? data.targets : selected;
  if (!keys.length && !data.running) return null;
  const seats = new Map(data.seats.map(seat => [seat.key, seat]));
  return <section id="wait-status" className="card waiting-card" aria-label="자동 예약 상태">
    <div className="section-heading"><h2>{data.reservation ? '갈아타기 대기' : '자동 예약 대기'}</h2>
      <span className={'badge' + (data.running ? ' waiting' : ' neutral')}>{data.running ? '대기 중' : '시작 전'}</span></div>
    <p className="section-description">{data.running ? data.reservation
      ? '현재 좌석을 유지하며 대기합니다. 빈자리가 나면 갈아타기를 시도합니다.'
      : '화면을 닫아도 계속 확인합니다. 한 자리를 잡으면 대기가 끝납니다.' : '선택한 좌석을 확인하고 대기를 시작하세요.'}</p>
    <ol className="waiting-list">{keys.map((key, i) => {
      const seat = seats.get(key);
      return <li key={key}><span className="priority">{i + 1}</span><strong>{seat ? `${seat.roomName} · ${seat.number}번` : '좌석 정보 확인 중'}</strong><Icon name="clock" /></li>;
    })}</ol>
    {data.running ? <button id="stop-wait" className="secondary wide" disabled={busy || !reachable}
      onClick={onStop}>자동 예약 중지</button> : <button className="secondary wide" onClick={onSelection}>선택한 좌석 확인</button>}
  </section>;
}
