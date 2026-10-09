import { useMemo } from 'react';
import { timeLabel } from '../model';

export default function RoomOverview({ data, onOpen }) {
  const rooms = useMemo(() => {
    const totals = new Map(data.rooms.map(room => [String(room.id), { ...room, total: 0, free: 0, occupied: 0, checkedAt: null }]));
    for (const seat of data.seats) {
      const room = totals.get(String(seat.roomId));
      if (!room) continue;
      room.total++;
      if (seat.occupied === false) room.free++;
      if (seat.occupied === true) room.occupied++;
      const checked = seat.checkedAt || data.lastChecked;
      if (checked) room.checkedAt = room.checkedAt ? Math.min(room.checkedAt, checked) : checked;
    }
    return [...totals.values()];
  }, [data.rooms, data.seats, data.lastChecked]);

  return <div id="room-overview" className="room-overview" aria-label="열람실별 좌석 현황">
    {rooms.map(room => {
      const unknown = room.total - room.free - room.occupied;
      return <button key={room.id} id={`room-card-${room.id}`} className="room-card"
        aria-label={`${room.name} 좌석 보기`} aria-describedby={`room-stats-${room.id}`} onClick={() => onOpen(room.id)}>
        <span className="availability-ring" aria-hidden="true">
          <svg viewBox="0 0 112 112" fill="none"><circle className="ring-track" cx="56" cy="56" r="49" strokeWidth="7" />
            <circle className="ring-free" cx="56" cy="56" r="49" strokeWidth="7" pathLength="100"
              strokeDasharray={`${room.total ? room.free / room.total * 100 : 0} 100`} transform="rotate(-90 56 56)" /></svg>
          <span className="ring-label"><strong>{room.total ? room.free : '—'}</strong><span>빈자리</span></span>
        </span>
        <strong className="room-card-name">{room.name}</strong>
        <span id={`room-stats-${room.id}`} className="room-card-stats">
          <span className="sr-only">빈자리 {room.total ? `${room.free}석` : '미확인'}, </span>
          {room.total ? `사용 ${room.occupied} / 전체 ${room.total}` : '좌석 정보 확인 중'}
          {unknown > 0 && <span>상태 미확인 {unknown}석</span>}
        </span>
        <span className="room-card-time">{room.checkedAt ? `${timeLabel(room.checkedAt)} 조회` : '조회 전'}</span>
      </button>;
    })}
  </div>;
}
