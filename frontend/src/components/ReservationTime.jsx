import { useEffect, useState } from 'react';
import { libraryTimestamp } from '../model';

const endLabel = new Intl.DateTimeFormat('ko-KR', {
  timeZone: 'Asia/Seoul', month: 'long', day: 'numeric',
  hour: '2-digit', minute: '2-digit', hour12: false,
});

export default function ReservationTime({ reservation, fresh }) {
  const end = libraryTimestamp(reservation.endTime);
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (end == null) return;
    const update = () => { if (!document.hidden) setNow(Date.now() / 1000); };
    update();
    const timer = setInterval(update, 15000);
    document.addEventListener('visibilitychange', update);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', update);
    };
  }, [end]);

  const reported = reservation.remainingTime;
  const fallback = (typeof reported === 'number' || (typeof reported === 'string' && reported.trim()))
    && Number.isFinite(Number(reported)) && Number(reported) >= 0 ? Math.ceil(Number(reported)) : null;
  const minutes = end == null ? fallback : Math.max(0, Math.ceil((end - now) / 60));
  const expired = end != null && end <= now;
  const hours = minutes == null ? 0 : Math.floor(minutes / 60);
  const remainder = minutes == null ? 0 : minutes % 60;
  const remaining = minutes == null ? '남은 시간 확인 필요' : expired ? '종료 시각 지남'
    : `${hours ? `${hours}시간${remainder ? ' ' : ''}` : ''}${remainder || !hours ? `${remainder}분` : ''} 남음`;

  return <div className="reservation-time-block">
    <p id="reservation-remaining" className={'reservation-remaining' + (minutes == null ? ' unknown' : '')}>
      {remaining}
    </p>
    <p id="reservation-time" className="muted">
      {end != null ? `종료 ${endLabel.format(end * 1000)}`
        : minutes != null ? '마지막 조회 기준' : '종료 시각을 확인하지 못했습니다.'}
    </p>
    {end != null && !fresh && <p className="fine reservation-time-note">마지막으로 확인한 종료 시각 기준</p>}
  </div>;
}
