import { timeLabel } from '../model';
import NotificationSettings from './NotificationSettings';
import FavoriteSeats from './FavoriteSeats';

export default function Settings({ data, busy, reachable, onDisconnect, onLogout, push, onRemoveFavorite, onViewFavorites }) {
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
    <FavoriteSeats data={data} busy={busy} reachable={reachable} onRemove={onRemoveFavorite} onView={onViewFavorites} />
    {push && <NotificationSettings push={push} busy={busy} reachable={reachable} />}
    <section className="card help-card" aria-labelledby="guide-heading"><h2 id="guide-heading">이용 안내</h2>
      <ul><li><strong>빈자리는 바로 예약·확정</strong><p>빈 좌석을 선택하면 바로 예약하고 배정확정합니다. 자주 쓰는 좌석은 상세창에서 선호좌석으로 등록하고 시간 예약에서도 선택할 수 있습니다.</p></li>
        <li><strong>여러 좌석 대기와 갈아타기</strong><p>사용 중인 좌석을 여러 개 선택해 대기할 수 있습니다. 먼저 확보한 한 자리로 확정하며, 내 좌석이 있으면 유지하다가 갈아탑니다. 실패 시 원래 자리의 복구·확정을 시도하지만 자리를 잃을 수 있습니다.</p></li>
        <li><strong>자동 연장은 기본으로 켜짐</strong><p>잔여 1시간 59분에 연장합니다. 횟수를 모두 사용하면 같은 자리 재배정·확정 후 계속 연장합니다. 내 좌석에서 끌 수 있으며, 운영시간 제한으로 연장이 실패하면 자동 연장을 끕니다.</p></li>
        <li><strong>다음 오전 시간 예약</strong><p>전날 오후 12시부터 당일 오전 5시 전까지 등록하세요. 오전 5시~11시 50분 중 10분 단위로 예약·확정하고 자동 연장을 켭니다. 실패하면 종료하며 이용 중인 좌석은 반납하지 않습니다.</p></li>
        <li><strong>화면을 닫아도 실행·알림</strong><p>운영 서버에서 대기·시간 예약·자동 연장을 계속합니다. 위 알림 설정에서 성공·실패 알림을 켜세요. iPhone은 홈 화면에 추가한 웹앱에서 허용해야 합니다.</p></li>
        <li><strong>배정 상태 확인</strong><p>확정에 실패하거나 결과가 불명확하면 공식 앱에서 상태와 NFC 인증을 확인하세요. 반납 후 다시 배정은 내 좌석에서 실행할 수 있습니다.</p></li></ul>
    </section>
    <details className="history"><summary>최근 활동 <span>{data.events.length}건</span></summary>
      <ol id="events">{data.events.map((item, index) => <li key={item.time + ':' + index}><time>{timeLabel(item.time)}</time><span>{item.text}</span></li>)}</ol>
      {!data.events.length && <p className="fine">아직 활동 기록이 없습니다.</p>}
    </details>
  </>;
}
