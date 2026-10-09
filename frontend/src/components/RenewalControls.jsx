import { useEffect, useState } from 'react';
import { libraryTimestamp, timeLabel } from '../model';

export default function RenewalControls({ data, canAct, busy, reachable, onRenew, onAutoRenew }) {
  const { reservation, autoRenew, reservationFresh } = data;
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => { if (!document.hidden) setNow(Date.now() / 1000); }, 15000);
    return () => clearInterval(timer);
  }, []);
  const confirmed = ['CHARGE', 'IN_USE'].includes(reservation?.state);
  if (!confirmed && !autoRenew) return null;
  const supported = data.confirmationRooms?.some(room => String(room) === String(reservation?.roomId));
  const end = libraryTimestamp(reservation?.endTime);
  const remaining = reservation?.renewableCnt, limit = reservation?.renewalLimit;
  const canRenew = supported && confirmed && end > now && end - now <= 7200 &&
    (!reservation?.renewableAt || reservation.renewableAt <= now) && remaining !== 0 &&
    reservation?.isRenewable !== false && reservation?.isRenewalImpossible !== true;
  const status = autoRenew
    ? autoRenew.status === 'scheduled'
      ? !reachable || !data.connected ? '연결 확인 중' : `${timeLabel(autoRenew.dueAt)} 자동 연장 예정`
      : autoRenew.message
    : '자동 연장 꺼짐';
  return <div id="renewal-controls" className="renewal-controls">
    <div className="section-heading">
      <div><h3>좌석 연장</h3><p id="renewal-count" className="fine">
        {remaining == null ? '남은 횟수 확인 중' : `남은 ${remaining}${limit == null ? '' : ` / ${limit}`}회`}
      </p></div>
      {supported && <button id="renew-seat" className="text-button" disabled={!canAct || !reservationFresh || !canRenew}
        onClick={() => onRenew(reservation)}>지금 연장</button>}
    </div>
    {supported || autoRenew ? <>
      <div className="section-heading auto-renew-row"><div><h3>자동 연장</h3>
        <p id="auto-renew-status" className="fine" role="status">{status}</p></div>
        <button id="auto-renew-toggle" className="switch" role="switch" aria-label="자동 연장" aria-checked={!!autoRenew}
          disabled={busy || !reachable || (!autoRenew && (!canAct || !reservationFresh || !confirmed || !end))}
          onClick={() => onAutoRenew(!autoRenew, reservation?.id || '')}><span /></button>
      </div>
      {!autoRenew && reservation?.renewableAt > now && <p className="fine">{timeLabel(reservation.renewableAt)}부터 연장 가능</p>}
    </> : <p className="fine">공식 앱에서 NFC로 연장해 주세요.</p>}
  </div>;
}
