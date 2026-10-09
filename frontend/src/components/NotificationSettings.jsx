export default function NotificationSettings({ push, busy, reachable }) {
  const blocked = busy || push.loading || !reachable;
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches || navigator.standalone;
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  return <section className="card settings-card" aria-labelledby="notification-heading">
    <h2 id="notification-heading">알림</h2>
    <div className="notification-toggle"><div><strong>이 기기에서 알림 받기</strong><p className="fine">배정과 연장 결과를 알려드려요.</p></div>
      <button role="switch" aria-checked={push.enabled} aria-label="이 기기 알림" className={'push-switch ' + (push.enabled ? 'on' : '')}
        disabled={blocked || !push.supported || !push.configured || (ios && !standalone)} onClick={push.enabled ? push.disable : push.enable}><span /></button></div>
    {ios && !standalone ? <p className="notice">Safari의 공유 → 홈 화면에 추가 후, 홈 화면에서 앱을 열고 알림을 켜 주세요.</p>
      : !push.supported ? <p className="fine">이 브라우저에서는 푸시 알림을 지원하지 않습니다. 아이폰은 iOS 16.4 이상의 홈 화면 웹앱에서 사용할 수 있어요.</p>
        : !push.configured && <p className="fine">서버 알림 설정을 확인 중입니다.</p>}
    {push.enabled && <><fieldset className="notification-options" disabled={blocked}><legend className="sr-only">받을 알림</legend>
      {[['assignment', '좌석 예약·배정확정'], ['renewal', '좌석 연장'], ['failure', '실패·확인 필요']].map(([key, label]) =>
        <label key={key}><span>{label}</span><input type="checkbox" checked={push.preferences[key]} onChange={event => push.update({ ...push.preferences, [key]: event.target.checked })} /></label>)}</fieldset>
      <button className="text-button" disabled={blocked} onClick={push.test}>테스트 알림 보내기</button></>}
    {(push.error || push.message) && <p role="status" className={'fine ' + (push.error ? 'urgent' : '')}>{push.error || push.message}</p>}
  </section>;
}
