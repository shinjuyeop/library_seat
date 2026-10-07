import { useEffect, useRef } from 'react';

export default function LoginForm({ directLogin, busy, error, onLogin }) {
  const password = useRef(null);
  useEffect(() => {
    if (error) password.current?.focus();
  }, [error]);
  const submit = async (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      fields = new FormData(form);
    const value = fields.get('password');
    try {
      await onLogin(
        directLogin
          ? {
              username: fields.get('username').trim(),
              password: value,
              remember: fields.has('remember'),
            }
          : { password: value },
      );
    } finally {
      if (password.current) password.current.value = '';
    }
  };
  return (
    <section id="access" className="access card">
      <h1>로그인</h1>
      <p className="access-description">도서관 계정으로 시작하세요.</p>
      <form id="access-form" onSubmit={submit}>
        {directLogin && (
          <>
            <label htmlFor="login-id">아이디 / 학번</label>
            <input
              id="login-id"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              maxLength={256}
              required
              disabled={busy}
            />
          </>
        )}
        <label htmlFor={directLogin ? 'login-password' : 'access-password'}>
          {directLogin ? '비밀번호' : '웹앱 비밀번호'}
        </label>
        <input
          ref={password}
          id={directLogin ? 'login-password' : 'access-password'}
          name="password"
          type="password"
          autoComplete="current-password"
          maxLength={256}
          minLength={directLogin ? undefined : 16}
          required
          disabled={busy}
        />
        {directLogin && (
          <>
            <label className="check-label" htmlFor="remember">
              <input
                id="remember"
                name="remember"
                type="checkbox"
                defaultChecked
                disabled={busy}
              />
              자동로그인
            </label>
            <p className="fine">
              선택하면 로그인 정보를 암호화해 저장하고, 인증 만료 시 다시
              로그인합니다.
            </p>
          </>
        )}
        {error && (
          <p id="login-error" className="inline-error" role="alert">
            {error}
          </p>
        )}
        <button
          id="login-submit"
          className="primary wide"
          type="submit"
          disabled={busy}
        >
          {busy ? '로그인 중…' : '로그인'}
        </button>
      </form>
    </section>
  );
}

export function ConnectionCard({ data, busy, onReconnect, onConnect }) {
  const submit = (event) => {
    event.preventDefault();
    const form = event.currentTarget,
      fields = new FormData(form);
    onConnect({
      username: fields.get('username'),
      password: fields.get('password'),
    });
    form.elements.password.value = '';
  };
  return (
    <section id="connection" className="card">
      <h2>다시 로그인해 주세요</h2>
      {data.cloud ? (
        <button id="reconnect" className="primary" onClick={onReconnect}>
          로그인
        </button>
      ) : (
        <form id="connection-form" onSubmit={submit}>
          <label htmlFor="library-id">아이디 / 학번</label>
          <input
            id="library-id"
            name="username"
            autoComplete="username"
            required
            disabled={busy || data.connecting}
          />
          <label htmlFor="library-password">비밀번호</label>
          <input
            id="library-password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            disabled={busy || data.connecting}
          />
          <button
            id="connect"
            className="primary"
            disabled={busy || data.connecting}
          >
            {data.connecting ? '로그인 중…' : '도서관 연결'}
          </button>
        </form>
      )}
    </section>
  );
}
