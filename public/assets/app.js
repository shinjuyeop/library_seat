'use strict';
const $ = (id) => document.getElementById(id);
let csrf = '', state = null, selected = new Set(), dirty = false;
let view = 'single', visibleLimit = 60, lastSeatRender = '', pollTimer;
let busy = false, authorized = false, reachable = true, fetching = false, toastTimer;
let directLogin = true;
let inspectedKey = null;
if (window.matchMedia('(display-mode: standalone)').matches || navigator.standalone) document.documentElement.classList.add('standalone');
const timeLabel = (seconds) => seconds ? new Date(seconds * 1000).toLocaleTimeString('ko-KR', {timeZone:'Asia/Seoul', hour:'2-digit', minute:'2-digit', hour12:false}) : '';

function toast(message) {
  $('toast').textContent = message;
  $('toast').hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $('toast').hidden = true; }, 6500);
}

async function api(path, body) {
  const options = {credentials:'same-origin', cache:'no-store'};
  if (body !== undefined) {
    options.method = 'POST';
    options.headers = {'Content-Type':'application/json', 'X-CSRF-Token':csrf};
    options.body = JSON.stringify(body);
  }
  let response;
  try { response = await fetch('/api/' + path, options); }
  catch (_) { throw new Error('서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.'); }
  const data = await response.json().catch(() => ({error:'서버 응답을 확인하지 못했습니다. 잠시 후 다시 확인해 주세요.'}));
  if (!response.ok) {
    if (response.status === 401 && !['login', 'library-login'].includes(path)) {
      authorized = false;
      showScreen();
      await initSession();
    }
    throw new Error(data.error || '요청을 처리하지 못했습니다.');
  }
  return data;
}

function showScreen() {
  $('access').hidden = authorized;
  $('dashboard').hidden = !authorized;
}

async function initSession() {
  try {
    const info = await api('session');
    csrf = info.csrf;
    authorized = info.authorized;
    directLogin = info.directLogin;
    $('direct-fields').hidden = !directLogin;
    $('local-fields').hidden = directLogin;
    $('login-id').disabled = $('login-password').disabled = !directLogin;
    $('access-password').disabled = directLogin;
    $('access-password').required = !directLogin;
    $('demo-banner').hidden = !info.demo;
    showScreen();
    if (authorized) await refreshState();
  } catch (error) { $('access').hidden = false; toast(error.message); }
}

async function refreshState() {
  if (!authorized || fetching) return;
  fetching = true;
  try {
    const previous = state;
    state = await api('state');
    reachable = true;
    $('offline').hidden = true;
    if (!dirty || state.running || (previous?.running && !state.running)) {
      selected = new Set(state.targets);
      dirty = false;
    }
    render();
  } catch (error) {
    reachable = false;
    $('offline').hidden = false;
    if (state) render();
  } finally { fetching = false; }
}

async function action(callback) {
  if (busy) return;
  busy = true;
  if (state) render();
  try { await callback(); }
  catch (error) { toast(error.message); }
  finally { busy = false; if (authorized) await refreshState(); if (state) render(); }
}

function confirmAction(title, message) {
  $('confirm-title').textContent = title;
  $('confirm-message').textContent = message;
  $('confirm-dialog').returnValue = '';
  $('confirm-dialog').showModal();
  return new Promise((resolve) => $('confirm-dialog').addEventListener('close', () => resolve($('confirm-dialog').returnValue === 'confirm'), {once:true}));
}

function render() {
  if (!state) return;
  $('demo-banner').hidden = !state.demo;
  const canAct = state.connected && reachable && !busy;
  $('status-badge').textContent = !reachable ? '연결 끊김' : state.connecting ? '로그인 중' : !state.connected ? '로그인 필요' : state.error ? '확인 필요' : state.running ? '예약 대기 중' : state.reservation ? '배정 있음' : '연결됨';
  $('status-badge').classList.toggle('waiting', !!state.error || !state.connected);
  $('wait-status').hidden = !state.running;
  $('wait-title').textContent = `${state.targets.length}개 좌석 자동 예약 대기 중`;
  $('interval').textContent = state.interval === 1 ? '1초 집중 확인' : `${state.interval}초 확인`;
  $('interval').title = '0~1분 남은 선택 좌석은 1초 주기로 확인합니다. 실제 간격에는 서버 응답 시간이 영향을 줍니다.';
  $('service-error').hidden = !state.error;
  $('service-error').textContent = state.error || '';
  $('connection').hidden = state.connected || state.demo;
  $('connection-form').hidden = !!state.cloud;
  $('reconnect').hidden = !state.cloud;
  $('auto-login-status').textContent = state.autoLogin ? '자동로그인 켜짐' : '';
  $('connect').disabled = busy || state.connecting;
  $('connect').textContent = state.connecting ? '로그인 중…' : '도서관 연결';
  $('refresh').disabled = !canAct;
  $('disconnect').hidden = state.demo || !state.connected;
  $('disconnect').disabled = busy || !reachable;
  const reservation = state.reservation;
  $('reservation').hidden = !reservation;
  $('repeat-controls').hidden = reservation?.state !== 'TEMP_CHARGE' && !state.repeat;
  $('repeat-toggle').setAttribute('aria-checked', String(!!state.repeat));
  $('repeat-toggle').disabled = busy || !reachable || (!state.repeat && (!canAct || !state.reservationFresh));
  renderRepeatCountdown();
  if (reservation) {
    const temporary = reservation.state === 'TEMP_CHARGE';
    const confirmed = ['CHARGE','IN_USE'].includes(reservation.state);
    $('reservation-badge').textContent = !state.reservationFresh ? '마지막 조회 정보' : temporary ? '임시배정 · NFC 필요' : confirmed ? '배정 확정' : '상태 확인 필요';
    $('reservation-badge').classList.toggle('waiting', temporary || !state.reservationFresh);
    $('reservation-seat').textContent = `${reservation.roomName} · ${reservation.seatNo}번`;
    $('reservation-time').textContent = reservation.endTime ? `종료 ${reservation.endTime}` : reservation.remainingTime != null ? `마지막 조회 기준 ${reservation.remainingTime}분 남음` : '';
    $('reservation-guide').textContent = temporary ? '제한 시간 안에 현장에서 공식 앱으로 NFC 인증을 완료하세요.' : confirmed ? '배정 확정' : '공식 앱에서 배정 상태를 확인해 주세요.';
    $('release').textContent = temporary ? '임시배정 취소' : '좌석 반납';
    $('release').disabled = !canAct || !state.reservationFresh || (!temporary && !confirmed);
  }
  $('selection-count').textContent = `${selected.size}개 선택`;
  $('selected-tab-count').textContent = selected.size;
  $('action-title').textContent = state.running ? `${state.targets.length}개 좌석 대기 중` : selected.size ? `${selected.size}개 선택` : '좌석을 선택하세요';
  $('start-stop').textContent = busy ? '처리 중…' : state.running ? '자동 예약 중지' : '자동 예약 시작';
  $('start-stop').disabled = busy || !reachable || (!state.running && (!canAct || !selected.size || !!reservation));
  $('action-detail').textContent = state.running ? (state.interval === 1 ? '선택 좌석을 빠르게 확인 중' : '0~1분 구간에 1초 집중 확인') : selected.size ? '선택 순서대로 시도 · 하나가 잡히면 종료' : '여러 좌석 중 하나가 잡히면 종료됩니다.';
  $('selection-summary').disabled = !selected.size;
  $('selection-tools').hidden = view !== 'selected' || !selected.size;
  $('clear-selection').disabled = state.running || busy;
  const quickSeat = state.seats.find(seat => seat.key === inspectedKey);
  $('quick-reserve').hidden = view !== 'all' || !quickSeat || !selected.has(inspectedKey) || quickSeat.occupied !== false || !!reservation || state.running;
  $('quick-seat').textContent = quickSeat ? `${quickSeat.roomName} · ${quickSeat.number}번` : '';
  $('quick-reserve-button').disabled = !canAct;
  document.body.classList.toggle('has-quick-reserve', !$('quick-reserve').hidden);
  renderSeats(canAct);
  const events = $('events');
  events.replaceChildren();
  state.events.forEach((item) => {
    const li = document.createElement('li'), stamp = document.createElement('time'), text = document.createElement('span');
    stamp.textContent = timeLabel(item.time); text.textContent = item.text;
    li.append(stamp, text); events.append(li);
  });
  $('empty-events').hidden = state.events.length > 0;
}

function renderRepeatCountdown() {
  if (!state?.repeat) { $('repeat-status').textContent = '배정 후 9분마다 취소하고 같은 좌석 예약'; return; }
  const remaining = Math.max(0, Math.ceil(state.repeat.dueAt - Date.now() / 1000));
  $('repeat-status').textContent = !reachable || !state.connected || state.error ? '연결 확인 중 · 내 좌석을 확인해 주세요' : remaining ? `${Math.floor(remaining / 60)}분 ${String(remaining % 60).padStart(2, '0')}초 후 재예약` : '재예약 시간 · 서버 확인 중';
}

async function reserveSeat(seat) {
  if (await confirmAction('이 좌석을 예약할까요?', seat.roomName + ' ' + seat.number + '번을 예약합니다. 성공하면 나머지 대기는 종료됩니다.')) {
    await action(async () => { await api('reserve', {key:seat.key}); dirty = false; inspectedKey = null; toast('예약 상태를 확인해 주세요.'); });
  }
}

function compactSearch(value) {
  return value.toLowerCase().replace(/열람실|좌석|제|번|[\s()\-]/g, '');
}

function filteredSeats() {
  const query = compactSearch($('seat-search').value.trim());
  const room = $('room-filter').value;
  let seats = state.seats.filter(seat =>
    (view !== 'single' || seat.single) &&
    (view !== 'selected' || selected.has(seat.key)) &&
    (room === 'all' || String(seat.roomId) === room) &&
    (!$('free-only').checked || seat.occupied === false) &&
    (!query || (/^\d+$/.test(query)
      ? seat.number.startsWith(query)
      : compactSearch(seat.roomName + seat.number).includes(query)))
  );
  if (view === 'selected') seats.sort((a, b) => [...selected].indexOf(a.key) - [...selected].indexOf(b.key));
  else if (/^\d+$/.test(query)) seats.sort((a, b) => Number(b.number === query) - Number(a.number === query));
  return seats;
}

function renderSeats(canAct) {
  document.querySelectorAll('[data-view]').forEach(tab => {
    const active = tab.dataset.view === view;
    tab.classList.toggle('active', active); tab.setAttribute('aria-pressed', String(active));
  });
  const seats = filteredSeats(), visible = seats.slice(0, view === 'all' ? visibleLimit * 2 : visibleLimit);
  const freeCount = seats.filter(seat => seat.occupied === false).length;
  $('result-count').textContent = seats.length + '석 · 빈자리 ' + freeCount;
  const timestamps = seats.map(seat => seat.checkedAt || state.lastChecked).filter(Boolean);
  $('updated').textContent = timestamps.length ? timeLabel(Math.min(...timestamps)) + ' 조회' : '조회 전';
  $('clear-search').hidden = !$('seat-search').value;
  $('empty-seats').hidden = seats.length > 0;
  $('empty-title').textContent = view === 'selected' && !selected.size ? '선택한 좌석이 없습니다.' : '조건에 맞는 좌석이 없습니다.';
  $('empty-message').textContent = view === 'selected' && !selected.size ? '좌석을 눌러 자동 예약할 자리를 선택하세요.' : view === 'single' ? '등록된 1인석은 1열람실 A·B에 있습니다. 전체 좌석에서 다른 자리도 찾을 수 있어요.' : '검색어나 열람실, 빈자리 필터를 바꿔 보세요.';
  $('load-more').hidden = visible.length >= seats.length;
  $('compact-guide').hidden = view !== 'all';
  $('load-more').textContent = '더 보기 · ' + visible.length + ' / ' + seats.length;
  const signature = JSON.stringify([view, canAct, state.running, !!state.reservation, [...selected], visible.map(seat => [seat.key, seat.number, seat.roomName, seat.single, seat.occupied, seat.remainingTime])]);
  if (signature === lastSeatRender) return;
  lastSeatRender = signature;
  const focused = document.activeElement?.dataset?.focusKey;
  const container = $('seats'), fragment = document.createDocumentFragment();
  container.classList.toggle('compact-seats', view === 'all');
  const roomGrids = new Map();
  visible.forEach(seat => {
    const chosen = selected.has(seat.key), free = seat.occupied === false;
    const minutes = seat.remainingTime == null || seat.remainingTime === '' ? NaN : Number(seat.remainingTime);
    const urgent = seat.occupied === true && Number.isFinite(minutes) && minutes >= 0 && minutes <= 1;
    if (view === 'all') {
      let roomGrid = roomGrids.get(seat.roomId);
      if (!roomGrid) {
        const group = document.createElement('section'), heading = document.createElement('h2'), count = document.createElement('span');
        group.className = 'room-group'; heading.className = 'room-heading';
        heading.textContent = seat.roomName;
        const inRoom = seats.filter(item => item.roomId === seat.roomId);
        count.textContent = `${inRoom.length}석 · 빈자리 ${inRoom.filter(item => item.occupied === false).length}`;
        heading.append(count); roomGrid = document.createElement('div'); roomGrid.className = 'number-grid';
        roomGrids.set(seat.roomId, roomGrid);
        group.append(heading, roomGrid); fragment.append(group);
      }
      const cell = document.createElement('button'), number = document.createElement('span'), status = document.createElement('span');
      cell.type = 'button'; cell.className = 'seat-cell' + (free ? ' free' : urgent ? ' urgent' : '') + (chosen ? ' chosen' : '');
      cell.dataset.focusKey = 'select-' + seat.key;
      cell.setAttribute('aria-pressed', String(chosen));
      const statusText = free ? '빈자리' : seat.occupied === null ? '확인 필요' : Number.isFinite(minutes) && minutes >= 0 ? Math.ceil(minutes) + '분' : '사용 중';
      cell.setAttribute('aria-label', `${seat.roomName} ${seat.number}번 ${statusText}${seat.single ? ' 1인석' : ''} 대기 선택`);
      cell.disabled = !canAct || state.running || !!state.reservation;
      number.className = 'cell-number'; number.textContent = seat.number;
      status.className = 'cell-status'; status.textContent = statusText;
      cell.append(number, status);
      if (chosen) { const order = document.createElement('span'); order.className = 'cell-order'; order.textContent = [...selected].indexOf(seat.key) + 1; order.setAttribute('aria-hidden', 'true'); cell.append(order); }
      cell.addEventListener('click', () => {
        if (selected.has(seat.key)) selected.delete(seat.key);
        else if (selected.size >= 50) return toast('최대 50개 좌석을 선택할 수 있습니다.');
        else selected.add(seat.key);
        inspectedKey = seat.key; dirty = true; render();
      });
      roomGrid.append(cell);
      return;
    }
    const tile = document.createElement('div'); tile.className = 'seat-tile' + (chosen ? ' selected' : '');
    const button = document.createElement('button'); button.className = 'seat-select'; button.type = 'button';
    button.dataset.focusKey = 'select-' + seat.key;
    button.setAttribute('aria-pressed', String(chosen));
    button.setAttribute('aria-label', seat.roomName + ' ' + seat.number + '번 ' + (free ? '빈자리' : '사용 중') + ' 대기 선택');
    button.disabled = !canAct || state.running || !!state.reservation;
    const roomLabel = document.createElement('span'); roomLabel.className = 'seat-room'; roomLabel.textContent = seat.roomName;
    const top = document.createElement('span'), number = document.createElement('span'), check = document.createElement('span');
    top.className = 'seat-top'; number.className = 'seat-number'; check.className = 'seat-check';
    number.textContent = seat.number; check.textContent = chosen ? [...selected].indexOf(seat.key) + 1 : '';
    check.setAttribute('aria-hidden', 'true'); top.append(number, check);
    const info = document.createElement('span'), dot = document.createElement('i'), label = document.createElement('span');
    const statusClass = free ? 'free' : urgent ? 'urgent' : 'occupied';
    info.className = 'seat-info ' + statusClass; dot.className = 'dot ' + statusClass;
    label.textContent = free ? '예약 가능' : seat.occupied === null ? '확인 필요' : Number.isFinite(minutes) && minutes >= 0 ? Math.ceil(minutes) + '분 남음' : '사용 중';
    info.append(dot, label); button.append(roomLabel, top, info);
    button.addEventListener('click', () => {
      if (chosen) selected.delete(seat.key);
      else if (selected.size >= 50) return toast('최대 50개 좌석을 선택할 수 있습니다.');
      else selected.add(seat.key);
      dirty = true; render();
    });
    tile.append(button);
    const bottom = document.createElement('div'); bottom.className = 'seat-bottom';
    const single = document.createElement('span'); single.className = 'single-label'; single.textContent = seat.single ? '1인석' : '';
    bottom.append(single);
    if (free) {
      const reserve = document.createElement('button'); reserve.className = 'reserve-now'; reserve.type = 'button'; reserve.textContent = '바로 예약';
      reserve.dataset.focusKey = 'reserve-' + seat.key;
      reserve.setAttribute('aria-label', seat.roomName + ' ' + seat.number + '번 바로 예약');
      reserve.disabled = !canAct || !!state.reservation;
      reserve.addEventListener('click', () => reserveSeat(seat));
      bottom.append(reserve);
    }
    tile.append(bottom); fragment.append(tile);
  });
  container.replaceChildren(fragment);
  if (focused) [...container.querySelectorAll('[data-focus-key]')].find(button => button.dataset.focusKey === focused)?.focus({preventScroll:true});
}

function selectView(next) {
  view = next; visibleLimit = 60;
  if (next === 'selected') { $('seat-search').value = ''; $('room-filter').value = 'all'; $('free-only').checked = false; }
  if (state) render();
}

$('access-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  $('login-error').hidden = true;
  $('login-submit').disabled = true;
  $('login-submit').textContent = '로그인 중…';
  const passwordInput = directLogin ? $('login-password') : $('access-password');
  try {
    const response = await api(directLogin ? 'library-login' : 'login', directLogin
      ? {username:$('login-id').value.trim(), password:passwordInput.value, remember:$('remember').checked}
      : {password:passwordInput.value});
    csrf = response.csrf; authorized = true; state = null; selected.clear(); dirty = false;
    showScreen();
    await refreshState();
  } catch (error) {
    $('login-error').textContent = error.message;
    $('login-error').hidden = false;
    passwordInput.focus();
  } finally {
    passwordInput.value = '';
    busy = false;
    $('login-submit').disabled = false;
    $('login-submit').textContent = '로그인';
    if (state) render();
  }
});
$('reconnect').addEventListener('click', () => { authorized = false; state = null; selected.clear(); dirty = false; showScreen(); $('login-password').focus(); });
$('connection-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const username = $('library-id').value, password = $('library-password').value;
  $('library-password').value = '';
  action(async () => { await api('connect', {username, password}); toast('도서관 로그인을 시작했습니다. 잠시 기다려 주세요.'); });
});
$('start-stop').addEventListener('click', () => action(async () => {
  await api('wait', {targets:[...selected], running:!state.running}); dirty = false;
}));
$('refresh').addEventListener('click', () => action(async () => { await api('refresh', {}); toast('좌석 현황을 확인하고 있습니다.'); }));
$('quick-reserve-button').addEventListener('click', () => { const seat = state?.seats.find(item => item.key === inspectedKey); if (seat) reserveSeat(seat); });
$('repeat-toggle').addEventListener('click', async () => {
  const enabled = !state.repeat, id = state.reservation?.id || '';
  if (enabled && !await confirmAction('자동 재예약을 켤까요?', '임시배정 후 9분마다 취소하고 같은 좌석을 다시 예약합니다. 취소 직후 다른 사람이 잡으면 자리를 잃을 수 있습니다. 화면을 닫아도 계속되며, NFC 인증을 마치면 종료됩니다.')) return;
  await action(async () => { await api('repeat', {enabled, id}); });
});
$('release').addEventListener('click', async () => {
  const reservation = {...state.reservation};
  if (await confirmAction(reservation.state === 'TEMP_CHARGE' ? '임시배정을 취소할까요?' : '좌석을 반납할까요?', `${reservation.roomName} ${reservation.seatNo}번을 해제합니다. 이 작업은 되돌릴 수 없습니다.`)) {
    await action(async () => { await api('release', {id:reservation.id, state:reservation.state}); dirty = false; });
  }
});
$('disconnect').addEventListener('click', async () => {
  if (await confirmAction('도서관 연결을 해제할까요?', '서버의 자동 예약을 중지하고 저장된 도서관 연결을 삭제합니다. 이미 배정된 좌석은 반납하지 않습니다.')) {
    await action(async () => { await api('disconnect', {}); dirty = false; });
  }
});
$('logout').addEventListener('click', async () => {
  if (await confirmAction('웹앱에서 로그아웃할까요?', '자동 예약 대기와 임시배정 자동 재예약은 서버에서 계속됩니다. 종료하려면 먼저 해당 기능을 꺼 주세요.')) {
    await action(async () => { await api('logout', {}); authorized = false; state = null; selected.clear(); dirty = false; await initSession(); });
  }
});
document.querySelectorAll('[data-view]').forEach(tab => tab.addEventListener('click', () => selectView(tab.dataset.view)));
$('seat-search').addEventListener('input', () => { if ($('seat-search').value.trim()) view = 'all'; visibleLimit = 60; if (state) render(); });
$('clear-search').addEventListener('click', () => { $('seat-search').value = ''; visibleLimit = 60; if (state) render(); $('seat-search').focus(); });
$('room-filter').addEventListener('change', () => { if (!['all', '102', '101'].includes($('room-filter').value) && view === 'single') view = 'all'; visibleLimit = 60; if (state) render(); });
$('free-only').addEventListener('change', () => { visibleLimit = 60; if (state) render(); });
$('load-more').addEventListener('click', () => { visibleLimit += 60; render(); });
$('reset-filters').addEventListener('click', () => { $('seat-search').value = ''; $('room-filter').value = 'all'; $('free-only').checked = false; selectView('all'); });
$('selection-summary').addEventListener('click', () => { selectView('selected'); $('seat-heading').scrollIntoView({behavior:'smooth',block:'start'}); });
$('clear-selection').addEventListener('click', () => { if (state.running || busy) return; selected.clear(); dirty = true; render(); });
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshState(); });
window.addEventListener('online', refreshState);
window.addEventListener('offline', () => { reachable = false; $('offline').hidden = false; if (state) render(); });
async function pollState() {
  if (!document.hidden) await refreshState();
  pollTimer = setTimeout(pollState, state?.running && state?.interval === 1 ? 2000 : state?.running || state?.repeat ? 5000 : 15000);
}
setInterval(() => { if (!document.hidden) renderRepeatCountdown(); }, 1000);
pollTimer = setTimeout(pollState, 5000);
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
initSession();
