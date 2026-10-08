import { memo, useMemo, useRef } from 'react';
import { filterSeats, seatStatus, timeLabel } from '../model';

const Seat = memo(function Seat({
  seat,
  compact,
  order,
  disabled,
  reserveDisabled,
  onToggle,
  onReserve,
}) {
  const status = seatStatus(seat),
    chosen = order > 0;
  if (compact)
    return (
      <button
        type="button"
        className={`seat-cell ${status.className}${chosen ? ' chosen' : ''}`}
        aria-pressed={chosen}
        aria-label={`${seat.roomName} ${seat.number}번 ${status.compact}${seat.single ? ' 1인석' : ''} 대기 선택`}
        disabled={disabled}
        onClick={() => onToggle(seat.key)}
      >
        <span className="cell-number">{seat.number}</span>
        <span className="cell-status">{status.compact}</span>
        {chosen && (
          <span className="cell-order" aria-hidden="true">
            {order}
          </span>
        )}
      </button>
    );
  return (
    <div className={'seat-tile' + (chosen ? ' selected' : '')}>
      <button
        type="button"
        className="seat-select"
        aria-pressed={chosen}
        aria-label={`${seat.roomName} ${seat.number}번 ${status.compact} 대기 선택`}
        disabled={disabled}
        onClick={() => onToggle(seat.key)}
      >
        <span className="seat-room">{seat.roomName}</span>
        <span className="seat-top">
          <span className="seat-number">{seat.number}</span>
          <span className="seat-check" aria-hidden="true">
            {chosen ? order : ''}
          </span>
        </span>
        <span className={'seat-info ' + status.className}>
          <i className={'dot ' + status.className} />
          <span>{status.label}</span>
        </span>
      </button>
      <div className="seat-bottom">
        <span className="single-label">{seat.single ? '1인석' : ''}</span>
        {status.free && (
          <button
            type="button"
            className="reserve-now"
            aria-label={`${seat.roomName} ${seat.number}번 바로 예약`}
            disabled={reserveDisabled}
            onClick={() => onReserve(seat)}
          >
            바로 예약
          </button>
        )}
      </div>
    </div>
  );
});

export default function SeatBrowser({
  data,
  selected,
  canAct,
  busy,
  filters,
  setFilters,
  onToggle,
  onReserve,
  onClear,
  headingRef,
}) {
  const searchRef = useRef(null);
  const { view, room, query, freeOnly, limit } = filters;
  const seats = useMemo(
    () => filterSeats(data.seats, { view, room, query, freeOnly, selected }),
    [data.seats, view, room, query, freeOnly, selected],
  );
  const visible = seats.slice(0, view === 'all' ? limit * 2 : limit);
  const timestamps = seats
    .map((seat) => seat.checkedAt || data.lastChecked)
    .filter(Boolean);
  const groups = new Map(),
    totals = new Map();
  for (const seat of seats) {
    const total = totals.get(seat.roomId) || { count: 0, free: 0 };
    total.count++;
    if (seat.occupied === false) total.free++;
    totals.set(seat.roomId, total);
  }
  for (const seat of visible) {
    if (!groups.has(seat.roomId)) groups.set(seat.roomId, []);
    groups.get(seat.roomId).push(seat);
  }
  const chooseView = (next) =>
    setFilters((previous) => ({
      ...previous,
      view: next,
      limit: 60,
      ...(next === 'selected'
        ? { query: '', room: 'all', freeOnly: false }
        : {}),
    }));
  const reset = () =>
    setFilters({
      view: 'all',
      room: 'all',
      query: '',
      freeOnly: false,
      limit: 60,
    });
  const props = (seat) => ({
    seat,
    compact: view === 'all',
    order: selected.indexOf(seat.key) + 1,
    disabled: !canAct || data.running || (data.reservation && (!data.reservationFresh ||
      !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(data.reservation.state))),
    reserveDisabled: !canAct || (data.reservation && (!data.reservationFresh ||
      !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(data.reservation.state))),
    onToggle,
    onReserve,
  });
  return (
    <section className="seat-browser" aria-labelledby="seat-heading">
      <div className="section-heading title-row">
        <h1 id="seat-heading" ref={headingRef} tabIndex={-1}>
          좌석 찾기
        </h1>
        <span id="selection-count" className="fine">
          {selected.length}개 선택
        </span>
      </div>
      <div className="search-box">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m16 16 4 4" />
        </svg>
        <input
          id="seat-search"
          type="search"
          ref={searchRef}
          placeholder="좌석 번호 또는 열람실 검색"
          aria-label="좌석 번호 또는 열람실 검색"
          autoComplete="off"
          spellCheck={false}
          value={query}
          onChange={(event) =>
            setFilters({
              ...filters,
              query: event.target.value,
              view: event.target.value.trim() ? 'all' : view,
              limit: 60,
            })
          }
        />
        {query && (
          <button
            id="clear-search"
            type="button"
            aria-label="검색어 지우기"
            onClick={() => {
              setFilters({ ...filters, query: '', limit: 60 });
              searchRef.current?.focus();
            }}
          >
            ×
          </button>
        )}
      </div>
      <div className="view-tabs" role="group" aria-label="좌석 목록">
        {[
          ['single', '1인석'],
          ['all', '전체 좌석'],
          ['selected', '선택한 좌석'],
        ].map(([key, label]) => (
          <button
            key={key}
            data-view={key}
            aria-pressed={view === key}
            className={view === key ? 'active' : ''}
            onClick={() => chooseView(key)}
          >
            {label}
            {key === 'selected' && (
              <>
                {' '}
                <span id="selected-tab-count">{selected.length}</span>
              </>
            )}
          </button>
        ))}
      </div>
      <div className="filters">
        <label className="select-label" htmlFor="room-filter">
          <span className="sr-only">열람실</span>
          <select
            id="room-filter"
            value={room}
            onChange={(event) =>
              setFilters({
                ...filters,
                room: event.target.value,
                view:
                  view === 'single' &&
                  !['all', '102', '101'].includes(event.target.value)
                    ? 'all'
                    : view,
                limit: 60,
              })
            }
          >
            <option value="all">모든 열람실</option>
            {data.rooms.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label className="toggle-label">
          <input
            id="free-only"
            type="checkbox"
            checked={freeOnly}
            onChange={(event) =>
              setFilters({
                ...filters,
                freeOnly: event.target.checked,
                limit: 60,
              })
            }
          />
          <span>빈자리만</span>
        </label>
      </div>
      <div className="results-line">
        <span id="result-count">
          {seats.length}석 · 빈자리{' '}
          {seats.filter((seat) => seat.occupied === false).length}
        </span>
        <span id="updated">
          {timestamps.length
            ? timeLabel(Math.min(...timestamps)) + ' 조회'
            : '조회 전'}
        </span>
      </div>
      {view === 'all' && (
        <p className="compact-guide">
          <span>
            <i className="dot free" />
            빈자리
          </span>
          <span>
            <i className="dot occupied" />
            사용 중 · 남은 분
          </span>
          <span>번호를 눌러 선택</span>
        </p>
      )}
      {view === 'selected' && selected.length > 0 && (
        <div className="selection-tools">
          <span>선택한 순서대로 예약을 시도합니다.</span>
          <button
            id="clear-selection"
            className="text-button"
            disabled={data.running || busy}
            onClick={onClear}
          >
            선택 해제
          </button>
        </div>
      )}
      <div
        id="seats"
        className={'seat-grid' + (view === 'all' ? ' compact-seats' : '')}
      >
        {view === 'all'
          ? [...groups].map(([id, items]) => (
              <section className="room-group" key={id}>
                <h2 className="room-heading">
                  {items[0].roomName}
                  <span>
                    {totals.get(id).count}석 · 빈자리 {totals.get(id).free}
                  </span>
                </h2>
                <div className="number-grid">
                  {items.map((seat) => (
                    <Seat key={seat.key} {...props(seat)} />
                  ))}
                </div>
              </section>
            ))
          : visible.map((seat) => <Seat key={seat.key} {...props(seat)} />)}
      </div>
      {seats.length === 0 && (
        <div id="empty-seats" className="empty">
          <strong>
            {view === 'selected' && !selected.length
              ? '선택한 좌석이 없습니다.'
              : '조건에 맞는 좌석이 없습니다.'}
          </strong>
          <p>
            {view === 'selected' && !selected.length
              ? '좌석을 눌러 자동 예약할 자리를 선택하세요.'
              : view === 'single'
                ? '등록된 1인석은 1열람실 A·B에 있습니다. 전체 좌석에서 다른 자리도 찾을 수 있어요.'
                : '검색어나 열람실, 빈자리 필터를 바꿔 보세요.'}
          </p>
          <button className="secondary" onClick={reset}>
            전체 좌석 보기
          </button>
        </div>
      )}
      {visible.length < seats.length && (
        <button
          id="load-more"
          className="load-more"
          onClick={() => setFilters({ ...filters, limit: limit + 60 })}
        >
          더 보기 · {visible.length} / {seats.length}
        </button>
      )}
    </section>
  );
}
