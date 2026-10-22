# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""WeeWX driver for current conditions and WeatherLink hardware archives."""

import email.utils
import getpass
import logging
import math
import time

import weewx
import weewx.drivers
from weeutil.weeutil import to_bool

from user.daviscloud import VERSION
from user.daviscloud.api import APIError, CloudAPI
from user.daviscloud.decode import Decoder

log = logging.getLogger(__name__)
DRIVER_NAME = 'DavisCloud'


def loader(config_dict, engine):
    """Construct the configured driver using WeeWX's loader interface.

    Args:
        config_dict (dict): Station configuration.
        engine (weewx.engine.StdEngine): Owning engine.

    Returns:
        DavisCloud: Configured cloud driver.
    """
    options = config_dict[DRIVER_NAME]
    if to_bool(options.get('archive', False)):
        generation = config_dict.get('StdArchive', {}).get('record_generation', 'hardware')
        if generation != 'hardware':
            raise ValueError('DavisCloud archive requires StdArchive.record_generation = hardware')
    return DavisCloud(**options)


def confeditor_loader():
    """Return WeeWX's interactive station configuration editor."""
    return CloudConfEditor()


def legacy_response(response):
    """Translate the official v1 current-conditions response to a v2 envelope.

    Args:
        response (dict): NoaaExt.json response, including its observation time.

    Returns:
        dict: One timestamped synthetic sensor in the common response format.
    """
    try:
        # RFC 822's -0000 means an unknown local offset, not this computer's
        # timezone. mktime_tz treats it as UTC, matching the cloud timestamp.
        stamp = email.utils.mktime_tz(email.utils.parsedate_tz(response['observation_time_rfc822']))
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
        raise weewx.WeeWxIOError('WeatherLink v1 response has no valid observation time') from None
    record = {}
    for source, target in {
        'temp_f': 'temp_out', 'relative_humidity': 'hum_out',
        'wind_mph': 'wind_speed', 'wind_degrees': 'wind_dir',
        'pressure_in': 'bar',
    }.items():
        record[target] = response.get(source)
    observation = response.get('davis_current_observation', {})
    for source, target in {
        'temp_in_f': 'temp_in', 'relative_humidity_in': 'hum_in',
        'wind_ten_min_avg_mph': 'wind_speed_10_min_avg',
        'wind_ten_min_gust_mph': 'wind_gust_10_min',
        'rain_rate_in_per_hr': 'rain_rate_in', 'rain_day_in': 'rain_day_in',
        'rain_month_in': 'rain_month_in', 'rain_year_in': 'rain_year_in',
        'solar_radiation': 'solar_rad', 'uv_index': 'uv',
    }.items():
        record[target] = observation.get(source)
    for prefix, maximum in (('temp_extra', 7), ('temp_soil', 4), ('temp_leaf', 4),
                            ('moist_soil', 4), ('wet_leaf', 4)):
        for index in range(1, maximum + 1):
            name = '%s_%d' % (prefix, index)
            record[name] = observation.get(name)
    for index in range(1, 8):
        record['hum_extra_%d' % index] = observation.get('relative_humidity_%d' % index)
    record['ts'] = int(stamp)
    return {'sensors': [{'lsid': 0, 'data_structure_type': 2, 'data': [record]}]}


class DavisCloud(weewx.drivers.AbstractDevice):
    """One selected cloud station, with explicit optional hardware archives."""

    def __init__(self, **options):
        """Initialize acquisition and select the station.

        Args:
            options (dict): The DavisCloud configuration section. A test may
                supply a CloudAPI instance as client to avoid live requests.
        """
        self.api = options.get('client') or CloudAPI(options)
        self.station = self.api.select_station(options.get('station_id'))
        self.decoder = Decoder(options)
        self.poll_interval = float(options.get('poll_interval', 60))
        self.max_age = float(options.get('max_age', 1800))
        self.max_backfill = float(options.get('max_backfill_days', 7)) * 86400
        self._archive_interval = int(options.get('archive_interval', 300))
        self.archive_enabled = to_bool(options.get('archive', False))
        if any(not math.isfinite(value) or value <= 0 for value in (
                self.poll_interval, self.max_age, self.max_backfill)):
            raise ValueError('Polling, freshness and backfill limits must be positive and finite')
        if self.poll_interval < 10:
            raise ValueError('Cloud poll_interval must be at least 10 seconds')
        if self._archive_interval <= 0 or self._archive_interval % 60:
            raise ValueError('archive_interval must be positive whole minutes, in seconds')
        if self.archive_enabled and self.api.version != '2':
            raise ValueError('Cloud archives require API v2')
        self.closed = False
        log.info('DavisCloud %s, API v%s, hardware archive %s',
                 VERSION, self.api.version, self.archive_enabled)

    @property
    def hardware_name(self):
        return 'Davis WeatherLink Cloud'

    @property
    def archive_interval(self):
        return self._archive_interval

    def get_current(self):
        """Fetch one packet, using host time only for a fresh measured sample.

        Returns:
            dict: A LOOP packet in US units.
        """
        payload = self.api.current(self.station)
        if self.api.version == '1':
            payload = legacy_response(payload)
        return self.decoder.current(payload, time.time(), self.max_age)

    def genLoopPackets(self):
        """Yield current packets, retrying transient outages on the next poll.

        Yields:
            dict: LOOP packets. Stale weather is never emitted as fresh data.
        """
        while not self.closed:
            started = time.monotonic()
            try:
                yield self.get_current()
            except APIError as error:
                if error.status in (400, 401, 403, 404):
                    raise
                log.error('%s', error)
            except weewx.WeeWxIOError as error:
                log.error('%s', error)
            # Sleep in short pieces so closePort also stops a waiting generator.
            deadline = started + self.poll_interval
            while not self.closed and time.monotonic() < deadline:
                time.sleep(min(1, max(0, deadline - time.monotonic())))

    def history(self, start, end):
        """Read (start, end] in bounded windows without skipping failed windows.

        Args:
            start (int): Exclusive lower timestamp.
            end (int): Inclusive upper timestamp.

        Yields:
            dict: Ordered hardware archive rows.
        """
        if not self.archive_enabled:
            raise NotImplementedError('Set archive = true to enable cloud archive access')
        if not 0 <= start <= end:
            raise ValueError('Invalid archive range')
        cursor = int(start)
        while cursor < end:
            stop = min(cursor + 86400, int(end))
            response = self.api.historic(self.station, cursor, stop)
            # Validate the entire window before exposing rows to the database.
            # A malformed later sensor must not leave half of a window imported.
            records = list(self.decoder.archive(response, cursor, stop))
            yield from records
            cursor = stop

    def genArchiveRecords(self, lastgood_ts):
        """Supply WeeWX's normal catch-up interface.

        Args:
            lastgood_ts (int|None): Last committed archive timestamp.

        Yields:
            dict: Cloud archive records following the last committed row.
        """
        end = int(time.time())
        start = max(0, int(end - self.max_backfill)) if lastgood_ts is None else int(lastgood_ts)
        # An existing archive may be older than max_backfill_days. Do not move
        # that cursor forward, which would make a real outage permanently vanish.
        yield from self.history(start, end)

    def closePort(self):
        self.closed = True


class CloudConfEditor(weewx.drivers.AbstractConfEditor):
    """Create the standard driver stanza without changing database bindings."""

    @property
    def default_stanza(self):
        return '''
[DavisCloud]
    driver = user.daviscloud.driver
    api_version = 2
    api_key = REPLACE_ME
    api_secret = REPLACE_ME
    # Use the station ID, not the WeatherLinkIP DID.
    station_id = REPLACE_ME
    poll_interval = 60
    max_age = 1800
    # Requires historic API access for this station.
    archive = false
    archive_interval = 300
    max_backfill_days = 7
'''

    def prompt_for_settings(self):
        """Read credentials without echoing the API secret to the terminal."""
        return {'api_key': getpass.getpass('WeatherLink API key: '),
                'api_secret': getpass.getpass('WeatherLink API secret: '),
                'station_id': self._prompt('station_id')}
