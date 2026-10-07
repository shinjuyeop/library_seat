"""Single-user mobile web app. Run one process / one replica per account."""
import argparse
import atexit
import os
import secrets
import threading
import time
from collections import OrderedDict
from datetime import timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from seat_service import DemoClient, LibraryError, SeatService, SettingsStore


def create_app(service, password, *, secret=None, secure_cookie=True):
    if len(password) < 16:
        raise ValueError('LIBRARY_WEB_PASSWORD must be at least 16 characters.')
    app = Flask(__name__, static_folder='public/assets', static_url_path='/assets')
    app.config.update(
        SECRET_KEY=secret or secrets.token_hex(32), MAX_CONTENT_LENGTH=8192,
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
        SESSION_COOKIE_SECURE=secure_cookie, PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        TRUSTED_HOSTS=os.getenv('LIBRARY_TRUSTED_HOSTS', '').split(',') if os.getenv('LIBRARY_TRUSTED_HOSTS') else None,
    )
    password_hash = generate_password_hash(password)
    attempts = OrderedDict()
    throttle_lock = threading.Lock()

    def throttle(label, limit, seconds):
        if getattr(service, 'cloud', False):
            return service.throttle(request.remote_addr, label, limit, seconds)
        # Behind the supplied proxy all visitors share a bucket. Do not trust arbitrary X-Forwarded-For.
        key = (request.remote_addr, label)
        now = time.monotonic()
        with throttle_lock:
            recent = [stamp for stamp in attempts.get(key, []) if now - stamp < seconds]
            if len(recent) >= limit:
                return True
            attempts[key] = recent + [now]
            attempts.move_to_end(key)
            while len(attempts) > 512:
                attempts.popitem(last=False)
        return False

    @app.before_request
    def protect_api():
        if not request.path.startswith('/api/'):
            return None
        if request.path == '/api/cron':
            expected = os.getenv('CRON_SECRET', '')
            if len(expected) < 32 or not secrets.compare_digest(request.headers.get('Authorization', ''), 'Bearer ' + expected):
                return jsonify(error='Unauthorized'), 401
            return None
        if request.method != 'GET':
            expected = session.get('csrf')
            actual = request.headers.get('X-CSRF-Token', '')
            if not expected or not secrets.compare_digest(expected, actual):
                return jsonify(error='접속 시간이 만료되었습니다. 화면을 새로고침해 주세요.'), 403
            if not request.is_json:
                return jsonify(error='JSON 요청이 필요합니다.'), 415
        if request.path not in ('/api/session', '/api/login') and not session.get('authorized'):
            return jsonify(error='웹앱에 먼저 로그인해 주세요.'), 401
        return None

    @app.after_request
    def security_headers(response):
        response.headers['Cache-Control'] = 'no-store' if request.path.startswith('/api/') else 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        return response

    @app.errorhandler(LibraryError)
    def library_error(error):
        return jsonify(error=str(error)), 409

    @app.get('/')
    def index():
        return send_from_directory(Path(app.root_path) / 'public', 'index.html')

    @app.get('/sw.js')
    def worker():
        return send_from_directory(Path(app.root_path) / 'public', 'sw.js')

    @app.get('/healthz')
    def health():
        if getattr(service, 'cloud', False):
            return jsonify(status='ok', scheduler='supabase-cron')
        healthy = bool(service.thread and service.thread.is_alive())
        return jsonify(status='ok' if healthy else 'worker_stopped'), 200 if healthy else 503

    @app.get('/api/session')
    def session_info():
        if 'csrf' not in session:
            session['csrf'] = secrets.token_urlsafe(32)
        return jsonify(authorized=bool(session.get('authorized')), csrf=session['csrf'], demo=service.demo)

    @app.post('/api/login')
    def login():
        if throttle('login', 5, 60):
            return jsonify(error='시도가 너무 많습니다. 1분 후 다시 접속해 주세요.'), 429
        body = request.get_json()
        candidate = body.get('password') if isinstance(body, dict) else None
        if not isinstance(candidate, str) or not check_password_hash(password_hash, candidate):
            return jsonify(error='웹앱 접속 비밀번호가 올바르지 않습니다.'), 401
        session.clear()
        session['authorized'] = True
        session['csrf'] = secrets.token_urlsafe(32)
        session.permanent = True
        return jsonify(csrf=session['csrf'])

    @app.post('/api/logout')
    def logout():
        session.clear()
        return jsonify(ok=True)

    @app.get('/api/state')
    def state():
        return jsonify(service.snapshot())

    @app.post('/api/connect')
    def connect():
        if service.demo:
            return jsonify(error='데모에서는 실제 계정을 연결하지 않습니다.'), 409
        if throttle('connect', 3, 300):
            return jsonify(error='로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.'), 429
        body = request.get_json()
        if not isinstance(body, dict):
            return jsonify(error='계정 정보를 입력해 주세요.'), 400
        username, library_password = body.get('username'), body.get('password')
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in (username, library_password)):
            return jsonify(error='학번과 도서관 비밀번호를 입력해 주세요.'), 400
        service.connect(username, library_password)
        return jsonify(ok=True), 202

    @app.post('/api/disconnect')
    def disconnect():
        service.disconnect()
        return jsonify(ok=True)

    @app.post('/api/wait')
    def wait():
        body = request.get_json()
        if not isinstance(body, dict):
            return jsonify(error='좌석을 선택해 주세요.'), 400
        service.set_wait(body.get('targets'), body.get('running'))
        return jsonify(ok=True)

    @app.post('/api/reserve')
    def reserve():
        body = request.get_json()
        if not isinstance(body, dict) or not isinstance(body.get('key'), str):
            return jsonify(error='좌석을 선택해 주세요.'), 400
        service.reserve(body['key'])
        return jsonify(ok=True)

    @app.post('/api/release')
    def release():
        body = request.get_json()
        if not isinstance(body, dict) or not all(isinstance(body.get(key), str) for key in ('id', 'state')):
            return jsonify(error='반납할 좌석을 확인해 주세요.'), 400
        service.release(body['id'], body['state'])
        return jsonify(ok=True)

    @app.post('/api/refresh')
    def refresh():
        if throttle('refresh', 1, 10):
            return jsonify(error='새로고침은 10초마다 가능합니다.'), 429
        if getattr(service, 'cloud', False):
            service.refresh()
        else:
            service.wake.set()
        return jsonify(ok=True)

    @app.post('/api/connect-token')
    def connect_token():
        if not getattr(service, 'cloud', False):
            return jsonify(error='클라우드 연결 전용입니다.'), 404
        body = request.get_json()
        if not isinstance(body, dict) or not isinstance(body.get('token'), str) or not 8 <= len(body['token']) <= 4096:
            return jsonify(error='연결 정보가 올바르지 않습니다.'), 400
        cookies = body.get('cookies', {})
        if not isinstance(cookies, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in cookies.items()):
            return jsonify(error='연결 정보가 올바르지 않습니다.'), 400
        service.connect_token(body['token'], cookies)
        return jsonify(ok=True)

    @app.post('/api/cron')
    def cron():
        if not getattr(service, 'cloud', False):
            return jsonify(error='Not available'), 404
        service.tick()
        return jsonify(ok=True)

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='Use simulated seats; no library requests.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8080')))
    args = parser.parse_args()
    password = os.getenv('LIBRARY_WEB_PASSWORD', '')
    if len(password) < 16:
        parser.error('Set LIBRARY_WEB_PASSWORD to a unique password of at least 16 characters.')
    data_dir = Path(os.getenv('LIBRARY_DATA_DIR', 'data'))
    store = SettingsStore(data_dir / ('demo.sqlite3' if args.demo else 'state.sqlite3'))
    service = SeatService(store, interval=int(os.getenv('LIBRARY_POLL_SECONDS', '30')),
                          client=DemoClient() if args.demo else None, demo=args.demo)
    app = create_app(service, password, secret=os.getenv('LIBRARY_SECRET_KEY'),
                     secure_cookie=os.getenv('LIBRARY_COOKIE_SECURE', '1') == '1')
    service.start()
    atexit.register(service.stop)
    username, library_password = os.getenv('KONKUK_LIBRARY_ID'), os.getenv('KONKUK_LIBRARY_PW')
    if not args.demo and username and library_password:
        service.connect(username, library_password)
    from waitress import serve
    print(f'Library web app: http://{args.host}:{args.port} (demo={args.demo})', flush=True)
    serve(app, host=args.host, port=args.port, threads=8)


if __name__ == '__main__':
    main()
