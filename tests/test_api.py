"""Probe authentication, HTTP failures and budgets without a live API key."""

import io
import json
import urllib.error
import urllib.parse
from unittest.mock import Mock

import pytest

from user.daviscloud.api import APIError, CloudAPI, NoRedirect, RequestBudget, retry_delay, station_id


class FakeTime:
    """Advance a monotonic clock when a limiter asks to sleep."""

    def __init__(self):
        self.now = 10000.0

    def clock(self):
        return self.now

    def sleep(self, seconds):
        assert seconds >= 0
        self.now += seconds


def api_for(responses, **options):
    """Build a client with a recorded HTTP boundary and an isolated budget."""
    clock = FakeTime()
    opener = Mock()
    values = []
    for value in responses:
        if isinstance(value, Exception):
            values.append(value)
        else:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.return_value = value if isinstance(value, bytes) else json.dumps(value).encode()
            values.append(response)
    opener.open.side_effect = values
    return CloudAPI(dict(api_key='test-key', api_secret='test-secret', **options),
                    opener, RequestBudget(clock.clock, clock.sleep)), opener, clock


def test_v2_credentials_and_parameters():
    api, opener, _ = api_for([{'sensors': []}])
    api.historic('123', 100, 200)
    request = opener.open.call_args.args[0]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
    assert query == {'api-key': ['test-key'], 'start-timestamp': ['100'], 'end-timestamp': ['200']}
    assert request.get_header('X-api-secret') == 'test-secret'
    assert 'test-secret' not in request.full_url
    assert 'api-signature' not in request.full_url


def test_v1_uses_official_api_not_html():
    api, opener, _ = api_for([{}], api_version='1', device_id='DID', password='private-password', api_token='token')
    api.current('DID')
    url = urllib.parse.urlparse(opener.open.call_args.args[0].full_url)
    assert url.scheme == 'https'
    assert url.path == '/v1/NoaaExt.json'
    assert urllib.parse.parse_qs(url.query) == {'user': ['DID'], 'pass': ['private-password'], 'apiToken': ['token']}
    with pytest.raises(NotImplementedError):
        api.historic('DID', 1, 2)


@pytest.mark.parametrize('status', [400, 401, 403, 404])
def test_permanent_errors_are_not_retried_or_leaked(status):
    error = urllib.error.HTTPError('https://private?secret=test-secret', status, 'test-secret', {}, io.BytesIO())
    api, opener, _ = api_for([error])
    with pytest.raises(APIError, match='HTTP %d' % status) as result:
        api.current('123')
    assert result.value.status == status
    assert 'test-secret' not in str(result.value)
    assert opener.open.call_count == 1


def test_429_cooldown_and_retry_budget():
    error = urllib.error.HTTPError('private', 429, 'private', {'Retry-After': '120'}, io.BytesIO())
    api, opener, clock = api_for([error, {'sensors': []}])
    assert api.current('123') == {'sensors': []}
    assert clock.now == 10120
    assert len(api.budget.attempts) == 2
    assert opener.open.call_count == 2


def test_server_errors_and_transport_failures_are_bounded():
    error = urllib.error.URLError('test-secret')
    api, opener, _ = api_for([error, error, error])
    with pytest.raises(APIError, match='connection failed'):
        api.current('123')
    assert opener.open.call_count == 3


@pytest.mark.parametrize('payload', [b'<html>test-secret</html>', b'[]', b'null', b'\xff'])
def test_bad_response_body_is_sanitized(payload):
    api, _, _ = api_for([payload])
    with pytest.raises(APIError) as result:
        api.current('123')
    assert 'test-secret' not in str(result.value)


def test_shared_request_limits_include_hour_boundary():
    clock = FakeTime()
    budget = RequestBudget(clock.clock, clock.sleep)
    for _ in range(1001):
        budget.acquire()
    assert clock.now >= 13600
    assert len(budget.attempts) <= 1000
    assert all(b - a >= 0.109 for a, b in zip(budget.attempts, list(budget.attempts)[1:]))


@pytest.mark.parametrize('value', ['../current', 'https://evil', '0', '-1', '123?api-key=evil'])
def test_station_id_path_injection(value):
    with pytest.raises(ValueError):
        station_id(value)


def test_ambiguous_station_is_not_first_station():
    api, _, _ = api_for([{'stations': [{'station_id': 1}, {'station_id': 2}]}])
    with pytest.raises(ValueError, match='explicitly'):
        api.select_station()
    api, _, _ = api_for([{'stations': [{'station_id': 123}]}])
    assert api.select_station() == '123'


def test_redirect_never_forwards_credentials():
    with pytest.raises(APIError, match='redirect'):
        NoRedirect().redirect_request(None, None, 302, 'private-secret', {}, 'https://elsewhere')


def test_retry_after_date_and_invalid_values():
    assert retry_delay('Thu, 01 Jan 1970 00:02:00 GMT', now=100) == 20
    assert retry_delay('nan') == 60
    assert retry_delay(None) == 60
    assert retry_delay('-3') == 0


def test_history_limit_is_enforced_before_network():
    api, opener, _ = api_for([])
    for start, end in [(2, 1), (-1, 2), (0, 86401)]:
        with pytest.raises(ValueError):
            api.historic('123', start, end)
    opener.open.assert_not_called()
