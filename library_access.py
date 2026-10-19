"""Shared access hold and bounded retries; no implicit HTTP retransmission."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timezone
from email.utils import parsedate_to_datetime
from functools import wraps
import time


RETRY_DELAYS = (300, 900, 3600)
WAIT_SECONDS = 30 * 60
WAIT_CHECKS = 60
_store = ContextVar('library_access_store', default=None)
_state = ContextVar('library_access_state', default=None)


@contextmanager
def access_context(store):
    token = _store.set(store)
    state_token = _state.set(None)
    try:
        yield
    finally:
        _store.reset(token)
        _state.reset(state_token)


def guarded_access(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with access_context(self.store):
            return method(self, *args, **kwargs)
    return guarded


def bind_retry_state(state):
    _state.set(state)


def check_shared_access():
    from seat_service import LibraryError
    retry = (_state.get() or {}).get('connectionRetry')
    if retry and (retry['status'] != 'waiting' or time.time() < retry['retryAt']):
        raise LibraryError('도서관 연결이 일시 중지되어 있습니다. 다음 시도 시각 또는 수동 확인을 기다려 주세요.', kind='cooldown')
    store = _store.get()
    if store is None:
        return
    status = store.access_status()
    if status.get('blocked'):
        raise LibraryError('도서관 연결이 중지되어 있습니다. 접속 제한을 확인한 뒤 운영자가 재개해야 합니다.',
                           blocked=True, retry_at=status.get('retryAt'))


def record_access_failure(error):
    store = _store.get()
    if store is not None and error.blocked and not getattr(error, '_access_recorded', False):
        store.stop_access(error.kind or 'blocked', error.retry_at)
        error._access_recorded = True


def retry_after(response):
    value = response.headers.get('Retry-After')
    if not isinstance(value, str):
        return None
    try:
        seconds = int(value)
        return time.time() + max(0, seconds)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(time.time(), date.timestamp())
        except (ValueError, TypeError, OverflowError):
            return None


def response_error(response, *, write=False):
    from seat_service import LibraryError
    status = response.status_code
    error = None
    if status == 403:
        error = LibraryError('도서관 서버가 접속을 거부했습니다. 자동 연결을 중지합니다.',
                             blocked=True, kind='blocked', uncertain=write)
    elif status == 429:
        error = LibraryError('도서관 요청 한도에 도달해 자동 연결을 중지했습니다.', blocked=True,
                             kind='rate_limited', retry_at=retry_after(response), uncertain=write)
    elif status in (301, 302, 303, 307, 308):
        error = LibraryError('도서관 응답 경로가 변경되어 자동 연결을 중지했습니다.',
                             blocked=True, kind='unexpected_response', uncertain=write)
    elif status in (500, 502, 503, 504):
        error = LibraryError('도서관 서버에 일시적인 오류가 발생했습니다.', retryable=True,
                             retry_at=retry_after(response), uncertain=write, kind='unavailable')
    if error:
        record_access_failure(error)
    return error


def invalid_response(*, write=False):
    from seat_service import LibraryError
    error = LibraryError('도서관에서 예상한 API 응답을 받지 못해 자동 연결을 중지했습니다.',
                         blocked=True, kind='unexpected_response', uncertain=write)
    record_access_failure(error)
    return error


def work_due(state, now=None):
    """Only explicit active jobs cause provider requests in the background."""
    now = time.time() if now is None else now
    due = []
    if state.get('running'):
        due.append(state.get('nextCheck') or now)
    plan = state.get('autoRenew')
    if plan and plan['status'] in {'scheduled', 'retry', 'working'}:
        due.append(max(plan['dueAt'], plan.get('retryAt') or 0, state.get('renewNightUntil') or 0))
    job = state.get('scheduledBooking')
    if job and job['status'] in {'pending', 'working'}:
        due.append(max(job['dueAt'], job.get('retryAt') or 0))
    retry = state.get('connectionRetry')
    if retry:
        if retry['status'] != 'waiting':
            return None
        if due:
            due = [max(min(due), retry['retryAt'])]
    return min(due) if due else None
