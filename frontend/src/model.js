export const initialModel = {
  session: null,
  data: null,
  selected: [],
  dirty: false,
  reachable: true,
  busy: false,
  busyAction: null,
  loginError: '',
  initError: '',
  toast: '',
  reservationNotice: null,
  observedReservation: null,
  pollRevision: 0,
};

export const libraryTimestamp = value => {
  if (typeof value !== 'string' || !value.trim()) return null;
  const iso = value.trim().replace(' ', 'T');
  const milliseconds = Date.parse(iso + (/Z$|[+-]\d{2}:?\d{2}$/.test(iso) ? '' : '+09:00'));
  return Number.isFinite(milliseconds) ? milliseconds / 1000 : null;
};

export function libraryReducer(state, action) {
  switch (action.type) {
    case 'session':
      return {
        ...state,
        session: action.session,
        initError: '',
        loginError: '',
        ...(action.session.authorized
          ? {}
          : {
              data: null, selected: [], dirty: false,
              reservationNotice: null, observedReservation: null, toast: '',
            }),
      };
    case 'snapshot': {
      const reservation = action.data.reservation;
      const verified = action.data.reservationFresh && reservation &&
        ['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(reservation.state);
      const previous = state.observedReservation;
      const changed = verified && state.data &&
        (!previous || previous.id !== reservation.id ||
          (previous.startedAt && reservation.startedAt &&
            previous.startedAt !== reservation.startedAt));
      const confirmed = verified && previous?.id === reservation.id &&
        previous.state === 'TEMP_CHARGE' && ['CHARGE', 'IN_USE'].includes(reservation.state);
      const renewed = verified && previous?.id === reservation.id &&
        ['CHARGE', 'IN_USE'].includes(previous?.state) &&
        libraryTimestamp(previous.endTime) && libraryTimestamp(reservation.endTime) > libraryTimestamp(previous.endTime);
      const reassigned = changed && ['CHARGE', 'IN_USE'].includes(reservation.state) &&
        ['CHARGE', 'IN_USE'].includes(previous?.state) &&
        previous.roomName === reservation.roomName && previous.seatNo === reservation.seatNo;
      const allocationConfirmed = verified && ['CHARGE', 'IN_USE'].includes(reservation.state);
      const notice = !action.data.error && (changed || confirmed || renewed) ? {
        id: reservation.id,
        at: Date.now() / 1000,
        message: `${renewed ? '연장 완료' : reassigned ? '재배정·확정 완료' : allocationConfirmed ? '배정 확정 완료' : '배정 완료'} · ${reservation.roomName} ${reservation.seatNo}번`,
      } : null;
      const reset =
        !state.dirty ||
        action.data.running ||
        (state.data?.running && !action.data.running);
      return {
        ...state,
        data: action.data,
        reachable: true,
        ...(verified ? {
          observedReservation: { ...reservation },
        } : {}),
        ...(notice ? { reservationNotice: notice, toast: notice.message } : {}),
        ...(reset ? { selected: action.data.targets, dirty: false } : {}),
      };
    }
    case 'toggle': {
      if (
        !state.reachable ||
        state.busy ||
        !state.data?.connected ||
        state.data.running ||
        (state.data.reservation && (!state.data.reservationFresh ||
          !['TEMP_CHARGE', 'CHARGE', 'IN_USE'].includes(state.data.reservation.state)))
      )
        return state;
      if (!state.selected.includes(action.key) && state.selected.length >= 50)
        return { ...state, toast: '최대 50개 좌석을 선택할 수 있습니다.' };
      return {
        ...state,
        dirty: true,
        selected: state.selected.includes(action.key)
          ? state.selected.filter((key) => key !== action.key)
          : [...state.selected, action.key],
      };
    }
    case 'clear-selection':
      return state.busy || state.data?.running
        ? state
        : { ...state, selected: [], dirty: true };
    case 'saved-selection':
      return { ...state, dirty: false };
    case 'patch':
      return { ...state, ...action.patch };
    default:
      return state;
  }
}

export const shortSeatLabel = seat => `${({ 102: '1A', 101: '1B', 232: '2', 233: '3A', 234: '3B', 107: '5' })[seat.roomId] || seat.roomName} · ${seat.number}번`;

export function favoriteSeats(data) {
  const catalog = new Map(data.seats.map(seat => [seat.key, seat]));
  return (data.favorites || []).map(key => {
    if (catalog.has(key)) return catalog.get(key);
    const [roomId, number] = key.split(':');
    const room = data.rooms.find(room => String(room.id) === roomId);
    return room ? { key, roomId: room.id, roomName: room.name, number, occupied: null } : null;
  }).filter(Boolean);
}

export function filterSeats(seats, { view, room, query, freeOnly, selected, favorites = [] }) {
  const compact = (value) =>
    value.toLowerCase().replace(/열람실|좌석|제|번|[\s()\-]/g, '');
  const search = compact(query.trim());
  const result = seats.filter(
    (seat) =>
      (view !== 'single' || seat.single) &&
      (view !== 'favorites' || favorites.includes(seat.key)) &&
      (view !== 'selected' || selected.includes(seat.key)) &&
      (room === 'all' || String(seat.roomId) === room) &&
      (!freeOnly || seat.occupied === false) &&
      (!search ||
        (/^\d+$/.test(search)
          ? seat.number.startsWith(search)
          : compact(seat.roomName + seat.number).includes(search))),
  );
  if (view === 'favorites')
    result.sort((a, b) => favorites.indexOf(a.key) - favorites.indexOf(b.key));
  else if (view === 'selected')
    result.sort((a, b) => selected.indexOf(a.key) - selected.indexOf(b.key));
  else if (/^\d+$/.test(search))
    result.sort(
      (a, b) => Number(b.number === search) - Number(a.number === search),
    );
  return result;
}

export function seatStatus(seat) {
  const minutes =
    seat.remainingTime == null || seat.remainingTime === ''
      ? NaN
      : Number(seat.remainingTime);
  const free = seat.occupied === false;
  const urgent =
    seat.occupied === true &&
    Number.isFinite(minutes) &&
    minutes >= 0 &&
    minutes <= 1;
  const compact = free
    ? '빈자리'
    : seat.occupied == null
      ? '확인 필요'
      : Number.isFinite(minutes) && minutes >= 0
        ? Math.ceil(minutes) + '분'
        : '사용 중';
  return {
    free,
    className: free ? 'free' : urgent ? 'urgent' : 'occupied',
    compact,
    label: free
      ? '예약 가능'
      : compact.endsWith('분')
        ? compact + ' 남음'
        : compact,
  };
}

export const timeLabel = (seconds, showSeconds = false) =>
  seconds
    ? new Date(seconds * 1000).toLocaleTimeString('ko-KR', {
        timeZone: 'Asia/Seoul',
        hour: '2-digit',
        minute: '2-digit',
        ...(showSeconds ? { second: '2-digit' } : {}),
        hour12: false,
      })
    : '';
