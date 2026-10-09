import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

export const defaultFilters = { view: 'single', room: 'all', query: '', freeOnly: false, limit: 120 };
const overviewFilters = { ...defaultFilters, view: 'all' };
const historyKey = 'librarySeatBrowse';

function savedFilters(state) {
  const filters = state?.[historyKey];
  if (!filters || !['favorites', 'single', 'all', 'selected'].includes(filters.view) ||
    !['all', '102', '101', '232', '233', '234', '107'].includes(filters.room)) return null;
  return { ...defaultFilters, view: filters.view, room: filters.room,
    query: typeof filters.query === 'string' ? filters.query : '',
    freeOnly: filters.freeOnly === true, limit: Number.isInteger(filters.limit) ? Math.max(120, filters.limit) : 120 };
}

export default function useSeatNavigation({ active, onNavigate }) {
  const [filters, setFilters] = useState(() => savedFilters(window.history.state) || defaultFilters);
  const focus = useRef(null);
  const navigate = useRef(onNavigate);
  navigate.current = onNavigate;

  useEffect(() => {
    const pop = event => {
      const restored = savedFilters(event.state);
      if (!restored) return;
      focus.current = { id: event.state.librarySeatFocus, top: event.state.librarySeatScroll || 0 };
      setFilters(restored);
      navigate.current();
    };
    window.addEventListener('popstate', pop);
    return () => window.removeEventListener('popstate', pop);
  }, []);

  useEffect(() => {
    if (window.history.state?.[historyKey])
      window.history.replaceState({ ...window.history.state, [historyKey]: filters }, '');
  }, [filters]);

  useLayoutEffect(() => {
    if (!active || !focus.current) return;
    const next = focus.current;
    focus.current = null;
    document.getElementById(next.id)?.focus({ preventScroll: true });
    window.scrollTo({ top: next.top, behavior: 'instant' });
  }, [active, filters]);

  const openRoom = useCallback(roomId => {
    const room = String(roomId);
    const detail = { ...overviewFilters, room };
    window.history.replaceState({ ...window.history.state, [historyKey]: overviewFilters,
      librarySeatRoom: false, librarySeatFocus: `room-card-${room}`, librarySeatScroll: window.scrollY }, '');
    window.history.pushState({ [historyKey]: detail, librarySeatRoom: true,
      librarySeatFocus: 'room-detail-heading', librarySeatScroll: 0 }, '');
    focus.current = { id: 'room-detail-heading', top: 0 };
    setFilters(detail);
  }, []);

  const backToRooms = useCallback(() => {
    if (window.history.state?.librarySeatRoom && filters.view === 'all' && filters.room !== 'all') {
      window.history.back();
      return;
    }
    window.history.replaceState({ [historyKey]: overviewFilters, librarySeatRoom: false }, '');
    focus.current = { id: `room-card-${filters.room}`, top: 0 };
    setFilters(overviewFilters);
  }, [filters.room, filters.view]);

  return { filters, setFilters, openRoom, backToRooms };
}
