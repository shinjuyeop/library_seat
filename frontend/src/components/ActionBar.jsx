import Icon from './Icon';

export function Navigation({ tab, onChange }) {
  return <nav className="bottom-nav" aria-label="주요 메뉴">
    {[['find', 'search', '좌석 찾기'], ['my', 'seat', '내 좌석'], ['settings', 'settings', '설정']].map(([key, icon, label]) =>
      <button key={key} id={`nav-${key}`} className={tab === key ? 'active' : ''} aria-current={tab === key ? 'page' : undefined}
        onClick={() => onChange(key)}><Icon name={icon} /><span>{label}</span></button>)}
  </nav>;
}

export default function ActionBar({ data, selected, selecting, tab, busy, reachable, onSelection, onReservation, onWait }) {
  const canAct = data.connected && reachable && !busy;
  const blocked = data.reservation && (!data.reservationFresh || !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(data.reservation.state));
  if (tab === 'find' && !data.running && (selecting || selected.length > 0)) return <div className="action-bar selection-bar">
    <button id="selection-summary" className="selection-summary" onClick={onSelection} disabled={!selected.length}>
      <strong>{selected.length ? `${selected.length}개 좌석 선택` : '기다릴 좌석을 선택하세요'}</strong>
      <span>{data.reservation ? '현재 좌석 유지 후 갈아타기' : '선택한 순서대로 예약 시도'}</span>
    </button>
    <button id="start-stop" className="primary" disabled={!canAct || !selected.length || !!blocked} onClick={() => onWait(selected)}>
      {busy ? '처리 중…' : data.reservation ? '갈아타기 대기' : '자동 예약 시작'}
    </button>
  </div>;
  if (tab === 'my' || (!data.reservation && !data.running && !data.repeat)) return null;
  const reservation = data.reservation;
  const state = !reachable || !data.connected || data.error || !data.reservationFresh ? '상태 확인 필요'
    : reservation?.state === 'TEMP_CHARGE' ? '임시배정 · NFC 필요'
      : ['CHARGE', 'IN_USE'].includes(reservation?.state) ? '배정 확정' : '상태 확인 필요';
  return <button className="action-bar mini-bar" aria-label="내 좌석 보기" onClick={onReservation}>
    <span className="mini-icon"><Icon name={reservation ? 'seat' : 'clock'} /></span>
    <span className="mini-copy"><strong>{reservation ? `${reservation.roomName} · ${reservation.seatNo}번` : `${data.targets.length}개 좌석 대기 중`}</strong>
      <span>{reservation ? state : !reachable || !data.connected || data.error ? '연결 상태 확인 필요' : '빈자리가 나면 자동 예약'}{data.running && reservation ? ` · 갈아타기 ${data.targets.length}곳` : ''}</span></span>
    <Icon name="chevron" />
  </button>;
}
