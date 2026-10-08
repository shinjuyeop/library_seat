import { timeLabel } from '../model';

export default function Settings({ data, busy, reachable, onDisconnect, onLogout }) {
  return <>
    <section className="card settings-card" aria-labelledby="account-heading">
      <h2 id="account-heading">도서관 연결</h2>
      <dl className="settings-list"><div><dt>건국대학교 도서관</dt><dd className={data.connected ? 'free' : 'urgent'}>{data.connected ? '연결됨' : '연결 필요'}</dd></div>
        <div><dt>자동로그인</dt><dd id="auto-login-status">{data.autoLogin ? '켜짐' : '꺼짐'}</dd></div></dl>
      <div className="settings-actions">
        {!data.demo && data.connected && <button id="disconnect" className="text-button destructive" disabled={busy || !reachable} onClick={onDisconnect}>연결 해제</button>}
        <button id="logout" className="text-button" disabled={busy || !reachable} onClick={onLogout}>로그아웃</button>
      </div>
    </section>
    <section className="card help-card" aria-labelledby="guide-heading"><h2 id="guide-heading">이용 안내</h2>
      <ul><li><strong>임시배정 후 NFC 인증</strong><p>현장에서 공식 도서관 앱으로 인증해야 배정이 확정됩니다.</p></li>
        <li><strong>자동 예약은 화면을 닫아도 계속</strong><p>중지하려면 내 좌석에서 대기를 끄세요. 로그아웃해도 대기는 유지됩니다.</p></li>
        <li><strong>자동 재예약은 9분마다</strong><p>임시배정을 취소하고 같은 좌석을 다시 예약합니다. 취소 사이에 자리를 잃을 수 있습니다.</p></li></ul>
    </section>
    <details className="history"><summary>최근 활동 <span>{data.events.length}건</span></summary>
      <ol id="events">{data.events.map((item, index) => <li key={item.time + ':' + index}><time>{timeLabel(item.time)}</time><span>{item.text}</span></li>)}</ol>
      {!data.events.length && <p className="fine">아직 활동 기록이 없습니다.</p>}
    </details>
    <p className="app-footnote">도서관 좌석 · 건국대학교</p>
  </>;
}
