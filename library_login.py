"""Password login through the same endpoint used by the official library website."""
import requests

from seat_service import BASE, LibraryError


class LoginError(LibraryError):
    def __init__(self, message, *, kind='credentials'):
        super().__init__(message)
        self.kind = kind


INVALID_LOGIN = '아이디 또는 비밀번호가 올바르지 않습니다. 비밀번호를 다시 입력해 주세요.'


def login_to_library(username, password):
    # Official main.02eeab51992c2936.js AuthService.login$ (2026-10-07).
    # No SSO, privacy consent, password change, or second-factor checks are bypassed.
    with requests.Session() as session:
        session.headers.update({'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                                'Accept-Language': 'ko', 'Origin': BASE, 'Referer': BASE + '/login'})
        try:
            response = session.post(BASE + '/pyxis-api/api/login',
                                    json={'loginId': username.strip(), 'password': password,
                                          'isFamilyLogin': False, 'isMobile': False},
                                    timeout=(5, 12), allow_redirects=False)
        except requests.RequestException:
            raise LoginError('도서관 서버에 연결하지 못했습니다. 잠시 후 다시 시도해 주세요.', kind='unavailable') from None
        if response.status_code == 429:
            raise LoginError('도서관 로그인 요청이 많습니다. 잠시 후 다시 시도해 주세요.', kind='unavailable')
        if response.status_code >= 500 or response.is_redirect:
            raise LoginError('도서관 로그인 서버를 이용할 수 없습니다. 잠시 후 다시 시도해 주세요.', kind='unavailable')
        try:
            body = response.json()
        except ValueError:
            raise LoginError('도서관이 서버 접속을 차단했거나 응답을 확인할 수 없습니다. 비밀번호는 저장하지 않았습니다.', kind='unavailable') from None
        if not isinstance(body, dict):
            raise LoginError('도서관 로그인 응답을 확인할 수 없습니다.', kind='unavailable')
        code = str(body.get('code', ''))
        if 'secondary' in code.lower() or 'privacy' in code.lower() or 'expired' in code.lower() or 'locked' in code.lower():
            raise LoginError('공식 도서관 홈페이지에서 추가 인증 또는 계정 상태를 확인한 뒤 다시 로그인해 주세요.', kind='action_required')
        if response.status_code != 200 or body.get('success') is not True or code != 'success.loggedIn':
            raise LoginError(INVALID_LOGIN)
        data = body.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('accessToken'), str) or len(data['accessToken']) < 8:
            raise LoginError('도서관에서 로그인 확인 정보를 받지 못했습니다.', kind='unavailable')
        identity = data.get('id')
        if not isinstance(identity, (str, int)) or isinstance(identity, bool) or not str(identity).strip():
            raise LoginError('도서관 계정을 식별할 수 없습니다.', kind='unavailable')
        # The official site's AUTH.USE_PRIVACY_AGREE is disabled; its agreement
        # field alone does not reject a successful login. Explicit server errors
        # above still require the user to complete any additional authentication.
        if data.get('isPasswordExpired') in (True, 1, '1'):
            raise LoginError('공식 도서관 홈페이지에서 비밀번호 또는 약관 확인을 완료한 뒤 다시 로그인해 주세요.', kind='action_required')
        return {'token': data['accessToken'], 'cookies': session.cookies.get_dict(), 'identity': str(identity)}
