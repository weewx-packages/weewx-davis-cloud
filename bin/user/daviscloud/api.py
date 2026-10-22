# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Authenticated WeatherLink requests with a shared, per-key request budget."""

import collections
import email.utils
import hashlib
import http.client
import json
import math
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import weewx


class APIError(weewx.HardwareError):
    """A sanitized API failure. The status is None for transport failures."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep credentials on the intended host, including on HTTPS redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise APIError('WeatherLink returned an unexpected redirect', code)


class RequestBudget:
    """Serialize all attempts made with one key in this Python process."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock = clock
        self.sleep = sleep
        self.attempts = collections.deque()
        self.blocked_until = 0.0
        self.lock = threading.Lock()

    def acquire(self):
        """Reserve one request, including retries and failed attempts."""
        with self.lock:
            while True:
                now = self.clock()
                while self.attempts and self.attempts[0] <= now - 3600:
                    self.attempts.popleft()
                ready = self.blocked_until
                if self.attempts:
                    ready = max(ready, self.attempts[-1] + 0.11)
                if len(self.attempts) >= 1000:
                    ready = max(ready, self.attempts[0] + 3600)
                if now >= ready:
                    self.attempts.append(now)
                    return
                self.sleep(ready - now)

    def defer(self, seconds):
        """Apply a server cooldown to every consumer of this request budget.

        Args:
            seconds (int|float): Minimum delay before the next attempt.
        """
        with self.lock:
            self.blocked_until = max(self.blocked_until, self.clock() + seconds)


_budgets = {}
_budgets_lock = threading.Lock()


def shared_budget(key):
    """Return the process-wide budget without retaining the cleartext key.

    Args:
        key (str): API key or legacy device ID.

    Returns:
        RequestBudget: The budget for this credential identity.
    """
    digest = hashlib.sha256(key.encode()).digest()
    with _budgets_lock:
        return _budgets.setdefault(digest, RequestBudget())


def station_id(value):
    """Validate a v2 station identifier before placing it in a URL path.

    Args:
        value (str|int): A positive station number or station UUID.

    Returns:
        str: A normalized station identifier.
    """
    text = str(value)
    if text.isascii() and text.isdecimal() and int(text) > 0:
        return str(int(text))
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        raise ValueError('station_id must be a positive number or UUID') from None


def retry_delay(value, now=None):
    """Read Retry-After seconds or an HTTP date, with a conservative default.

    Args:
        value (str|None): The response header.
        now (int|float|None): Wall-clock time. Default is the current time.

    Returns:
        float: A finite, nonnegative delay in seconds.
    """
    if value is None:
        return 60.0
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(0.0, seconds)
    except (ValueError, TypeError):
        pass
    try:
        stamp = email.utils.parsedate_to_datetime(value).timestamp()
        return max(0.0, stamp - (time.time() if now is None else now))
    except (ValueError, TypeError, OverflowError, AttributeError):
        return 60.0


class CloudAPI:
    """Read cloud metadata and observations. Error text never contains a URL."""

    def __init__(self, options, opener=None, budget=None):
        """Build the client without contacting WeatherLink.

        Args:
            options (dict): Credentials, api_version, timeout and retry count.
            opener (urllib.request.OpenerDirector|None): HTTP transport.
            budget (RequestBudget|None): Shared request limiter.
        """
        self.version = str(options.get('api_version', '2'))
        if self.version not in ('1', '2'):
            raise ValueError('api_version must be 1 or 2')
        names = ('api_key', 'api_secret') if self.version == '2' else ('device_id', 'password')
        if any(not str(options.get(name, '')).strip() for name in names):
            raise ValueError('Missing WeatherLink credentials: ' + ', '.join(names))
        self.credentials = {name: str(options[name]) for name in names}
        if self.version == '2' and any(
                not all(33 <= ord(char) <= 126 for char in value)
                for value in self.credentials.values()):
            raise ValueError('WeatherLink v2 credentials must contain printable ASCII without spaces')
        self.token = str(options.get('api_token', ''))
        self.demo = str(options.get('demo', 'false')).lower() in ('true', '1', 'yes')
        self.timeout = float(options.get('timeout', 15))
        self.retries = int(options.get('retries', 2))
        if not math.isfinite(self.timeout) or self.timeout <= 0 or not 0 <= self.retries <= 5:
            raise ValueError('timeout must be positive; retries must be between 0 and 5')
        self.opener = opener or urllib.request.build_opener(NoRedirect())
        self.budget = budget or shared_budget(self.version + ':' + self.credentials[names[0]])

    def get(self, endpoint, parameters=None):
        """Make one logical request and retry only transient failures.

        Args:
            endpoint (str): Relative, validated API endpoint.
            parameters (dict|None): Non-credential query parameters.

        Returns:
            dict: Parsed JSON response.

        Raises:
            APIError: HTTP, transport, size or JSON validation failure.
        """
        if self.version == '2':
            parts = endpoint.split('/')
            if len(parts) == 2 and parts[0] in ('current', 'historic'):
                endpoint = parts[0] + '/' + station_id(parts[1])
            elif endpoint not in ('stations', 'sensors', 'sensor-catalog'):
                raise ValueError('Unsupported WeatherLink endpoint')
            query = dict(parameters or {}, **{'api-key': self.credentials['api_key']})
            if self.demo:
                query['demo'] = 'true'
            headers = {'X-Api-Secret': self.credentials['api_secret']}
        else:
            if endpoint not in ('NoaaExt.json', 'StationStatus.json'):
                raise ValueError('Unsupported WeatherLink v1 endpoint')
            query = dict(parameters or {}, user=self.credentials['device_id'],
                         **{'pass': self.credentials['password'], 'apiToken': self.token})
            headers = {}
        url = 'https://api.weatherlink.com/v%s/%s?%s' % (
            self.version, endpoint, urllib.parse.urlencode(query))
        request = urllib.request.Request(url, headers=headers)
        for attempt in range(self.retries + 1):
            self.budget.acquire()
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    payload = response.read(16 * 1024 * 1024 + 1)
                if len(payload) > 16 * 1024 * 1024:
                    raise APIError('WeatherLink response exceeds 16 MiB')
                try:
                    result = json.loads(payload)
                except (ValueError, UnicodeError):
                    raise APIError('WeatherLink returned invalid JSON') from None
                if not isinstance(result, dict):
                    raise APIError('WeatherLink returned an unexpected JSON shape')
                return result
            except urllib.error.HTTPError as error:
                status = error.code
                delay = retry_delay(error.headers.get('Retry-After')) if status == 429 else 2 ** attempt
                error.close()
                if status == 429 or 500 <= status < 600:
                    self.budget.defer(delay)
                    if attempt < self.retries:
                        continue
                raise APIError('WeatherLink HTTP %d' % status, status) from None
            except (urllib.error.URLError, socket.timeout, OSError, http.client.HTTPException):
                self.budget.defer(2 ** attempt)
                if attempt == self.retries:
                    raise APIError('WeatherLink connection failed') from None
        raise APIError('WeatherLink retries exhausted')

    def current(self, selected):
        """Return current observations for the selected station.

        Args:
            selected (str): V2 station ID. Unused for v1.

        Returns:
            dict: The API response.
        """
        endpoint = 'current/' + station_id(selected) if self.version == '2' else 'NoaaExt.json'
        return self.get(endpoint)

    def historic(self, selected, start, end):
        """Return one historical window no longer than 24 hours.

        Args:
            selected (str): V2 station ID.
            start (int): Window start, Unix seconds.
            end (int): Window end, Unix seconds.

        Returns:
            dict: The API response.
        """
        if self.version != '2':
            raise NotImplementedError('The legacy API adapter has no archive access')
        if not 0 <= start < end <= start + 86400:
            raise ValueError('Historical windows must be positive and at most 86400 seconds')
        return self.get('historic/' + station_id(selected),
                        {'start-timestamp': int(start), 'end-timestamp': int(end)})

    def select_station(self, selected=None):
        """Select explicitly or require exactly one accessible v2 station.

        Args:
            selected (str|int|None): Configured station ID.

        Returns:
            str: Validated station ID, or the v1 device ID.
        """
        if self.version == '1':
            return self.credentials['device_id']
        if selected:
            return station_id(selected)
        stations = self.get('stations').get('stations', [])
        if len(stations) != 1:
            raise ValueError('Set station_id explicitly; the account does not contain exactly one station')
        return station_id(stations[0].get('station_id_uuid') or stations[0]['station_id'])
