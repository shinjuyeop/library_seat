import { timeLabel } from '../model';
import NotificationSettings from './NotificationSettings';

export default function Settings({ data, busy, reachable, onDisconnect, onLogout, push }) {
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
    {push && <NotificationSettings push={push} busy={busy} reachable={reachable} />}
    <section className="card help-card" aria-labelledby="guide-heading"><h2 id="guide-heading">이용 안내</h2>
      <ul><li><strong>예약과 동시에 배정확정</strong><p>공통 태그로 모든 열람실에서 자동 확정합니다. 확정에 실패하면 공식 앱에서 NFC 인증을 확인해 주세요.</p></li>
        <li><strong>자동 예약은 화면을 닫아도 계속</strong><p>중지하려면 내 좌석에서 대기를 끄세요. 로그아웃해도 대기는 유지됩니다.</p></li>
        <li><strong>빈자리는 바로 예약·확정</strong><p>사용 중인 좌석만 대기합니다. 임시배정 자동 재예약은 사용하지 않습니다.</p></li></ul>
    </section>
    <details className="history"><summary>최근 활동 <span>{data.events.length}건</span></summary>
      <ol id="events">{data.events.map((item, index) => <li key={item.time + ':' + index}><time>{timeLabel(item.time)}</time><span>{item.text}</span></li>)}</ol>
      {!data.events.length && <p className="fine">아직 활동 기록이 없습니다.</p>}
    </details>
  </>;
}
