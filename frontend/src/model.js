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
      const repeated = changed && previous?.repeatId === previous?.id &&
        previous?.roomName === reservation.roomName &&
        previous?.seatNo === reservation.seatNo;
      const confirmed = verified && previous?.id === reservation.id &&
        previous.state === 'TEMP_CHARGE' && ['CHARGE', 'IN_USE'].includes(reservation.state);
      const reassigned = changed && ['CHARGE', 'IN_USE'].includes(reservation.state) &&
        ['CHARGE', 'IN_USE'].includes(previous?.state) &&
        previous.roomName === reservation.roomName && previous.seatNo === reservation.seatNo;
      const allocationConfirmed = verified && ['CHARGE', 'IN_USE'].includes(reservation.state);
      const notice = !action.data.error && (changed || confirmed) ? {
        id: reservation.id,
        at: Date.now() / 1000,
        message: `${reassigned ? '재배정·확정 완료' : allocationConfirmed ? '배정 확정 완료' : repeated ? '자동 재예약 완료' : '배정 완료'} · ${reservation.roomName} ${reservation.seatNo}번`,
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
          observedReservation: {
            ...reservation, repeatId: action.data.repeat?.reservationId,
          },
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
