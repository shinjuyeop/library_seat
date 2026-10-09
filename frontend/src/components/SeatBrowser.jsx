import { memo, useMemo, useRef } from 'react';
import { filterSeats, seatStatus, timeLabel } from '../model';
import Icon from './Icon';
import RoomOverview from './RoomOverview';

const Seat = memo(function Seat({ seat, order, selecting, own, disabled, onInspect, onToggle }) {
  const status = seatStatus(seat), chosen = order > 0;
  return <button type="button"
    className={`seat-cell ${status.className}${chosen ? ' chosen' : ''}${own ? ' own' : ''}`}
    aria-label={`${seat.roomName} ${seat.number}번 ${own ? '내 좌석' : status.compact} ${selecting ? '대기 선택' : '상세 보기'}`}
    aria-pressed={selecting ? chosen : undefined} disabled={selecting && (disabled || own)}
    onClick={() => selecting ? onToggle(seat.key) : onInspect(seat.key)}>
    <span className="cell-number">{seat.number}</span>
    <span className="cell-status">{own ? '내 좌석' : status.compact}</span>
    {chosen && <span className="cell-order" aria-hidden="true">{order}<Icon name="check" /></span>}
  </button>;
});

export default function SeatBrowser({ data, selected, canAct, busy, filters, setFilters,
  selecting, onSelecting, onToggle, onInspect, onClear, onOpenRoom, onBackToRooms }) {
  const searchRef = useRef(null);
  const { view, room, query, freeOnly, limit } = filters;
  const overview = view === 'all' && room === 'all' && !query.trim();
  const roomDetail = view === 'all' && room !== 'all';
  const roomName = data.rooms.find(item => String(item.id) === room)?.name;
  const seats = useMemo(() => filterSeats(data.seats, { view, room, query, freeOnly, selected }),
    [data.seats, view, room, query, freeOnly, selected]);
  const visible = seats.slice(0, limit);
  const selectedOrder = new Map(selected.map((key, index) => [key, index + 1]));
  const groups = new Map(), totals = new Map();
  for (const seat of seats) if (seat.occupied === false) totals.set(seat.roomId, (totals.get(seat.roomId) || 0) + 1);
  for (const seat of visible) {
    if (!groups.has(seat.roomId)) groups.set(seat.roomId, []);
    groups.get(seat.roomId).push(seat);
  }
  const timestamps = seats.map(seat => seat.checkedAt || data.lastChecked).filter(Boolean);
  const blocked = !canAct || data.running || (data.reservation && (!data.reservationFresh ||
    !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(data.reservation.state)));
  const change = patch => setFilters(previous => ({ ...previous, ...patch, limit: 120 }));
  const reset = () => change({ view: 'all', room: 'all', query: '', freeOnly: false });
  const seatButton = seat => <Seat key={seat.key} seat={seat} order={selectedOrder.get(seat.key) || 0}
    selecting={selecting} disabled={blocked} onInspect={onInspect} onToggle={onToggle}
    own={!!data.reservation && data.reservation.roomName === seat.roomName && data.reservation.seatNo === seat.number} />;
  return <section className="seat-browser" aria-label="좌석 탐색">
    <div className="search-box"><Icon name="search" />
      <input id="seat-search" type="search" ref={searchRef} placeholder="좌석 번호"
        aria-label="좌석 번호" autoComplete="off" autoCapitalize="none"
        spellCheck={false} enterKeyHint="search" value={query}
        onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur(); }}
        onChange={event => change({ query: event.target.value, view: event.target.value.trim() ? 'all' : view })} />
      {query && <button id="clear-search" className="icon-button" aria-label="검색어 지우기"
        onClick={() => { change({ query: '' }); searchRef.current?.focus(); }}><Icon name="close" /></button>}
    </div>
    <div className="view-tabs" role="group" aria-label="좌석 목록">
      {[['single', '1인석'], ['all', '전체 좌석'], ['selected', data.running ? '대기 중' : '선택한 좌석']].map(([key, label]) =>
        <button key={key} data-view={key} aria-pressed={view === key} className={view === key ? 'active' : ''}
          onClick={() => change({ view: key, query: '', room: 'all', freeOnly: false })}>
          {label}{key === 'selected' && <> <span id="selected-tab-count">{selected.length}</span></>}
        </button>)}
    </div>
    {view === 'all' && !overview && <div className="room-detail-nav">
      <button id="back-to-rooms" className="room-back" onClick={onBackToRooms}><Icon name="back" />열람실 목록</button>
      <h2 id="room-detail-heading" tabIndex={-1}>{roomDetail ? roomName : '검색 결과'}</h2>
    </div>}
    {!overview && <div className={'filters' + (view === 'all' ? ' room-seat-filters' : '')}>
      {view !== 'all' && <label className="select-label" htmlFor="room-filter"><span className="sr-only">열람실</span>
        <select id="room-filter" value={room} onChange={event => change({ room: event.target.value,
          view: view === 'single' && !['all', '102', '101'].includes(event.target.value) ? 'all' : view })}>
          <option value="all">모든 열람실</option>
          {data.rooms.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>}
      <label className="filter-check"><input id="free-only" type="checkbox" checked={freeOnly}
        onChange={event => change({ freeOnly: event.target.checked })} /><span>빈자리만</span></label>
    </div>}
    <div className="browse-toolbar">
      <span id="result-count">{overview ? `${data.rooms.length}개 열람실` : `${seats.length}석`} <span className="muted">· 빈자리 {overview && !data.seats.length ? '—' : seats.filter(seat => seat.occupied === false).length}</span></span>
      {!data.running && <button className={'text-button' + (selecting ? ' active' : '')} aria-pressed={selecting}
        disabled={busy} onClick={() => onSelecting(!selecting)}>
        <Icon name={selecting ? 'check' : 'list'} />{selecting ? '선택 마치기' : '여러 좌석 선택'}
      </button>}
    </div>
    {selecting && !overview && <div className="selection-guide" role="status"><span>선택한 순서대로 빈자리를 기다립니다.</span>
      <button id="clear-selection" className="text-button" disabled={blocked || !selected.length} onClick={onClear}>선택 해제</button>
    </div>}
    {data.running && !overview && <p className="inline-guide">좌석을 눌러 대기에 추가하거나 제외할 수 있어요.</p>}
    {overview ? <RoomOverview data={data} onOpen={onOpenRoom} /> : <>
    {view === 'selected' && selected.length > 0
      ? <ol id="seats" className="selected-list">{visible.map(seat => <li key={seat.key}>
        <span className="priority">{selectedOrder.get(seat.key)}</span>
        <button className="seat-list-button" onClick={() => onInspect(seat.key)}><span><strong>{seat.roomName} · {seat.number}번</strong>
          <small className={seatStatus(seat).className}>{seatStatus(seat).label}</small></span><Icon name="chevron" /></button>
        <button className="icon-button" aria-label={`${seat.roomName} ${seat.number}번 ${data.running ? '대기에서 제외' : '선택 해제'}`}
          disabled={data.running ? !canAct : blocked} onClick={() => onToggle(seat.key)}><Icon name="close" /></button>
      </li>)}</ol>
      : <div id="seats" className="seat-grid">{[...groups].map(([id, items]) => <section className="room-group" key={id}>
        {!roomDetail && <div className="room-heading"><h2>{items[0].roomName}</h2><span>빈자리 {totals.get(id) || 0}</span></div>}
        <div className="number-grid">{items.map(seatButton)}</div>
      </section>)}</div>}
    {seats.length === 0 && <div id="empty-seats" className="empty"><Icon name="search" />
      <strong>{view === 'selected' ? '선택한 좌석이 없습니다' : '조건에 맞는 좌석이 없습니다'}</strong>
      <p>{view === 'selected' ? '여러 좌석 선택을 눌러 기다릴 자리를 골라보세요.' : view === 'single'
        ? '1인석은 1열람실 A·B에서 찾을 수 있어요.' : '검색어나 열람실, 빈자리 필터를 바꿔보세요.'}</p>
      <button className="secondary" onClick={reset}>전체 좌석 보기</button>
    </div>}
    {visible.length < seats.length && <button id="load-more" className="load-more" onClick={() => setFilters(p => ({ ...p, limit: p.limit + 120 }))}>더 보기 · {visible.length} / {seats.length}</button>}
    <div className="browse-footnote"><span><i className="dot free" />빈자리</span><span><i className="dot occupied" />사용 중 · 남은 분</span>
      <span id="updated">{timestamps.length ? timeLabel(Math.min(...timestamps)) + ' 조회' : '조회 전'}</span></div></>}
  </section>;
}
