'use strict';
const $ = (id) => document.getElementById(id);
let csrf = '', state = null, selected = new Set(), dirty = false, room = 102;
let busy = false, authorized = false, reachable = true, fetching = false, toastTimer;
let directLogin = true;
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
  $('updated').textContent = state.lastChecked ? `${timeLabel(state.lastChecked)} 기준` : '조회 전';
  $('interval').textContent = state.running ? `${state.interval}초 간격` : '';
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
  $('action-title').textContent = state.running ? `${state.targets.length}개 좌석 대기 중` : selected.size ? `${selected.size}개 선택` : '좌석을 선택하세요';
  $('start-stop').textContent = busy ? '처리 중…' : state.running ? '자동 예약 중지' : '자동 예약 시작';
  $('start-stop').disabled = busy || !reachable || (!state.running && (!canAct || !selected.size || !!reservation));
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

function renderSeats(canAct) {
  const container = $('seats');
  container.replaceChildren();
  const seats = state.seats.filter((seat) => seat.roomId === room);
  $('empty-seats').hidden = seats.length > 0;
  seats.forEach((seat) => {
    const chosen = selected.has(seat.key), free = seat.occupied === false;
    const tile = document.createElement('div');
    tile.className = 'seat-tile' + (chosen ? ' selected' : '');
    const button = document.createElement('button');
    button.className = 'seat-select'; button.type = 'button';
    button.setAttribute('aria-pressed', String(chosen));
    button.setAttribute('aria-label', `${seat.number}번 ${free ? '빈자리' : '사용 중'} 대기 선택`);
    button.disabled = !canAct || state.running || !!state.reservation;
    const top = document.createElement('span'), number = document.createElement('span'), check = document.createElement('span');
    top.className = 'seat-top'; number.className = 'seat-number'; check.className = 'seat-check';
    number.textContent = seat.number; check.textContent = '✓'; top.append(number, check);
    const info = document.createElement('span'), dot = document.createElement('i'), label = document.createElement('span');
    info.className = 'seat-info'; dot.className = 'dot ' + (free ? 'free' : 'occupied');
    label.textContent = free ? '빈자리' : seat.occupied === null ? '확인 중' : Number.isFinite(Number(seat.remainingTime)) && seat.remainingTime != null ? `${seat.remainingTime}분 남음` : '사용 중';
    info.append(dot, label); button.append(top, info);
    button.addEventListener('click', () => { if (chosen) selected.delete(seat.key); else selected.add(seat.key); dirty = true; render(); });
    tile.append(button);
    if (free) {
      const reserve = document.createElement('button'); reserve.className = 'reserve-now'; reserve.type = 'button'; reserve.textContent = '바로 예약';
      reserve.disabled = !canAct || !!state.reservation;
      reserve.addEventListener('click', async () => {
        if (await confirmAction('이 좌석을 예약할까요?', `${seat.roomName} ${seat.number}번 좌석을 예약합니다. 성공하면 나머지 대기는 종료됩니다.`)) {
          await action(async () => { await api('reserve', {key:seat.key}); dirty = false; toast('예약 상태를 확인해 주세요.'); });
        }
      });
      tile.append(reserve);
    }
    container.append(tile);
  });
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
  if (await confirmAction('웹앱에서 로그아웃할까요?', '서버에 등록한 자동 예약 대기는 계속됩니다. 대기를 끝내려면 먼저 자동 예약을 중지해 주세요.')) {
    await action(async () => { await api('logout', {}); authorized = false; state = null; selected.clear(); dirty = false; await initSession(); });
  }
});
document.querySelectorAll('[data-room]').forEach((tab) => tab.addEventListener('click', () => {
  room = Number(tab.dataset.room);
  document.querySelectorAll('[data-room]').forEach((item) => { const active = item === tab; item.classList.toggle('active', active); item.setAttribute('aria-pressed', String(active)); });
  if (state) render();
}));
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshState(); });
window.addEventListener('online', refreshState);
window.addEventListener('offline', () => { reachable = false; $('offline').hidden = false; if (state) render(); });
setInterval(() => { if (!document.hidden) refreshState(); }, 5000);
if ('serviceWorker' in navigator && window.isSecureContext) navigator.serviceWorker.register('/sw.js').catch(() => {});
initSession();
