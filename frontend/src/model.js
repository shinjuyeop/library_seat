export const initialModel = {
  session: null,
  data: null,
  selected: [],
  dirty: false,
  reachable: true,
  busy: false,
  loginError: '',
  initError: '',
  toast: '',
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
          : { data: null, selected: [], dirty: false }),
      };
    case 'snapshot': {
      const reset =
        !state.dirty ||
        action.data.running ||
        (state.data?.running && !action.data.running) ||
        !!action.data.reservation;
      return {
        ...state,
        data: action.data,
        reachable: true,
        ...(reset ? { selected: action.data.targets, dirty: false } : {}),
      };
    }
    case 'toggle': {
      if (
        !state.reachable ||
        state.busy ||
        !state.data?.connected ||
        state.data.running ||
        state.data.reservation
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

export function filterSeats(seats, { view, room, query, freeOnly, selected }) {
  const compact = (value) =>
    value.toLowerCase().replace(/열람실|좌석|제|번|[\s()\-]/g, '');
  const search = compact(query.trim());
  const result = seats.filter(
    (seat) =>
      (view !== 'single' || seat.single) &&
      (view !== 'selected' || selected.includes(seat.key)) &&
      (room === 'all' || String(seat.roomId) === room) &&
      (!freeOnly || seat.occupied === false) &&
      (!search ||
        (/^\d+$/.test(search)
          ? seat.number.startsWith(search)
          : compact(seat.roomName + seat.number).includes(search))),
  );
  if (view === 'selected')
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

export const timeLabel = (seconds) =>
  seconds
    ? new Date(seconds * 1000).toLocaleTimeString('ko-KR', {
        timeZone: 'Asia/Seoul',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false,
      })
    : '';
