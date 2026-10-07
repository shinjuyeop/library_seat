"""Transfer a login directly to YOUR deployed HTTPS app. Never prints the library token."""
import argparse
import getpass
import os
from urllib.parse import urlsplit

import requests

from library_auth import get_token_automatically


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Your production HTTPS Vercel app URL')
    args = parser.parse_args()
    parsed = urlsplit(args.url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        parser.error('Use the HTTPS origin of your own deployed app, without a path or query.')
    base = args.url.rstrip('/')
    print('도서관 연결 대상: ' + base)
    app_password = getpass.getpass('이 웹앱의 접속 비밀번호: ')
    with requests.Session() as session:
        def api(method, path, **kwargs):
            response = session.request(method, base + path, timeout=120, allow_redirects=False, **kwargs)
            if response.status_code not in (200, 202):
                raise RuntimeError('서버 요청에 실패했습니다. 배포 주소, 접속 비밀번호, 서버 설정을 확인하세요.')
            return response.json()
        csrf = api('GET', '/api/session')['csrf']
        csrf = api('POST', '/api/login', json={'password': app_password}, headers={'X-CSRF-Token': csrf})['csrf']
        app_password = None
        token, _, cookies = get_token_automatically(os.getenv('KONKUK_LIBRARY_ID'), os.getenv('KONKUK_LIBRARY_PW'))
        if not token:
            raise RuntimeError('도서관 로그인에 실패했습니다.')
        api('POST', '/api/connect-token', json={'token': token, 'cookies': cookies or {}},
            headers={'X-CSRF-Token': csrf})
        print('연결 완료. 휴대폰 웹앱에서 좌석을 선택하고 자동 예약을 시작하세요. 이제 PC를 꺼도 됩니다.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, requests.RequestException, ValueError, KeyError):
        print('연결하지 못했습니다. 도서관 로그인 및 웹앱의 배포·환경 변수 설정을 확인해 주세요.')
        raise SystemExit(1)
