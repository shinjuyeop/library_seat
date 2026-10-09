"""Standard encrypted Web Push. Provider addresses and keys never enter public state."""
import base64
import hashlib
import json
import os
import re
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

from seat_service import LibraryError

DEFAULT_PREFERENCES = {'assignment': True, 'renewal': True, 'failure': True}


def configuration():
    key = os.getenv('VAPID_PUBLIC_KEY', '')
    return {'configured': bool(key and os.getenv('VAPID_PRIVATE_KEY')), 'publicKey': key}


def device_id(endpoint):
    if not isinstance(endpoint, str) or len(endpoint) > 2048:
        raise LibraryError('알림 기기 정보를 확인해 주세요.')
    try:
        url = urlsplit(endpoint)
        host = url.hostname or ''
        allowed = (host == 'fcm.googleapis.com' or host == 'updates.push.services.mozilla.com'
                   or host.endswith('.push.apple.com'))
        if (not allowed or url.scheme != 'https' or url.username or url.password or url.fragment
                or url.port not in (None, 443) or not url.path.startswith('/') or not url.path.strip('/')
                or re.search(r'[\s\\]', endpoint)):
            raise ValueError()
    except ValueError:
        raise LibraryError('지원하는 브라우저의 알림 주소가 아닙니다.') from None
    return hashlib.sha256(endpoint.encode()).hexdigest()


def subscription_info(value):
    if not isinstance(value, dict):
        raise LibraryError('알림 등록 정보를 확인해 주세요.')
    device_id(value.get('endpoint'))
    keys = value.get('keys')
    try:
        if not isinstance(keys, dict):
            raise ValueError()
        decoded = {}
        for name, size in [('auth', 16), ('p256dh', 65)]:
            text = keys.get(name)
            if not isinstance(text, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}={0,2}', text):
                raise ValueError()
            decoded[name] = base64.urlsafe_b64decode(text + '=' * (-len(text) % 4))
            if len(decoded[name]) != size:
                raise ValueError()
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), decoded['p256dh'])
    except (ValueError, TypeError):
        raise LibraryError('알림 암호화 키를 확인할 수 없습니다. 알림을 다시 켜 주세요.') from None
    return {'endpoint': value['endpoint'], 'keys': {name: keys[name] for name in ('auth', 'p256dh')}}


def preferences(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_PREFERENCES) or any(type(v) is not bool for v in value.values()):
        raise LibraryError('알림 종류를 확인해 주세요.')
    return value


class NoRedirectSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs['allow_redirects'] = False
        return super().request(method, url, **kwargs)


def send_notification(subscription, notification):
    """Return delivery status without exposing provider errors or retrying an unknown write."""
    try:
        subscription = subscription_info(subscription)
        if not configuration()['configured']:
            return 'failed'
        with NoRedirectSession() as session:
            response = webpush(subscription_info=subscription,
                     data=json.dumps({key: notification[key] for key in ('id', 'title', 'body', 'url')}),
                     vapid_private_key=os.environ['VAPID_PRIVATE_KEY'],
                     vapid_claims={'sub': os.getenv('VAPID_SUBJECT', 'https://library-seat-dusky.vercel.app')},
                     ttl=3600, timeout=5, requests_session=session,
                     headers={'Urgency': 'normal', 'Topic': hashlib.sha256(notification['id'].encode()).hexdigest()[:32]})
        return 'sent' if 200 <= response.status_code < 300 else 'failed'
    except WebPushException as error:
        return 'expired' if error.status_code in (404, 410) else 'failed'
    except (LibraryError, requests.RequestException, ValueError, TypeError, KeyError):
        return 'failed'
