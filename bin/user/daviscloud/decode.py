# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Normalize WeatherLink sensor records into observations in WeeWX US units.

Field names and units follow the manufacturer's universal sensor catalog. A
decoder never combines records from different archive timestamps or guesses
which of two transmitters belongs to the primary weather station.
"""

import collections
import logging
import math
import re

import weewx
import weewx.units

log = logging.getLogger(__name__)

CURRENT = frozenset((1, 2, 6, 9, 10, 12, 16, 19, 21, 23, 25))
ARCHIVE = frozenset((3, 4, 7, 9, 11, 13, 17, 20, 22, 24, 26))
HEALTH = frozenset((14, 15, 18, 27))

# Ordered aliases express the statistic to use, not merely similar field names.
# In particular, an archive average must not be replaced by an interval maximum.
COMMON = {
    'outTemp': ('temp_out', 'temp', 'temp_avg', 'temp_last'),
    'outHumidity': ('hum_out', 'hum', 'hum_last'),
    'inTemp': ('temp_in', 'temp_in_last'),
    'inHumidity': ('hum_in', 'hum_in_last'),
    'barometer': ('bar_sea_level', 'bar_noaa', 'bar'),
    'pressure': ('bar_absolute', 'abs_press'),
    'altimeter': ('bar_alt',),
    'windSpeed': ('wind_speed_avg', 'wind_speed_last', 'wind_speed'),
    'windDir': ('wind_dir_of_prevail', 'wind_dir_last', 'wind_dir'),
    'windGust': ('wind_speed_hi', 'wind_speed_hi_last_2_min', 'wind_speed_hi_last_10_min', 'wind_gust_10_min'),
    'windGustDir': ('wind_speed_hi_dir', 'wind_dir_of_hi',
                    'wind_dir_at_hi_speed_last_2_min', 'wind_dir_at_hi_speed_last_10_min'),
    'dewpoint': ('dew_point_out', 'dew_point', 'dew_point_last'),
    'heatindex': ('heat_index_out', 'heat_index', 'heat_index_last'),
    'inDewpoint': ('dew_point_in', 'dew_point_in_last'),
    'outWetbulb': ('wet_bulb', 'wet_bulb_last'),
    'THSW': ('thsw_index', 'thsw_index_last'),
    'windSpeed10': ('wind_speed_10_min_avg', 'wind_speed_avg_last_10_min'),
    'windchill': ('wind_chill', 'wind_chill_last'),
    'radiation': ('solar_rad_avg', 'solar_rad'),
    'UV': ('uv_index_avg', 'uv_index', 'uv'),
    'ET': ('et',),
    'dayET': ('et_day',),
    'monthET': ('et_month',),
    'yearET': ('et_year',),
    'pm1_0': ('pm_1', 'pm_1_avg'),
    'pm2_5': ('pm_2p5', 'pm_2p5_avg'),
    'pm10_0': ('pm_10', 'pm_10_avg'),
    'rxCheckPercent': ('reception', 'reception_day', 'iss_reception'),
    'txBatteryStatus': ('trans_battery_flag',),
}
for _index in range(1, 9):
    COMMON['extraTemp%d' % _index] = ('temp_extra_%d' % _index,)
    COMMON['extraHumid%d' % _index] = ('hum_extra_%d' % _index,)
for _index in range(1, 5):
    COMMON['soilTemp%d' % _index] = ('temp_soil_%d' % _index, 'temp_%d' % _index,
                                    'temp_last_%d' % _index)
    COMMON['soilMoist%d' % _index] = ('moist_soil_%d' % _index, 'moist_soil_last_%d' % _index)
    COMMON['leafTemp%d' % _index] = ('temp_leaf_%d' % _index,)
    COMMON['leafWet%d' % _index] = ('wet_leaf_%d' % _index, 'wet_leaf_last_%d' % _index)

HEALTH_FIELDS = {
    'davisBatteryVoltage': (('battery_voltage', 'backup_battery_voltage', 'lead_acid_battery_voltage'), 0.001, 'group_volt'),
    'davisInputVoltage': (('input_voltage', 'power_adapter_input_voltage'), 0.001, 'group_volt'),
    'davisBatteryPercent': (('battery_percent',), 1.0, 'group_percent'),
    'davisUptime': (('os_uptime', 'uptime'), 1.0, 'group_deltatime'),
    'davisAppUptime': (('app_uptime',), 1.0, 'group_deltatime'),
    'davisWifiRSSI': (('wifi_rssi',), 1.0, 'group_db'),
    'davisLinkUptime': (('link_uptime',), 1.0, 'group_deltatime'),
}
GROUPS = {name: spec[2] for name, spec in HEALTH_FIELDS.items()}
GROUPS['davisBatteryTemp'] = 'group_temperature'


def number(value):
    """Return a finite observation, preserving zero and rejecting booleans.

    Args:
        value (object): A JSON measurement.

    Returns:
        float|None: Numeric value, or None for missing/non-numeric input.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError):
        return None


def first(record, aliases):
    """Read the first present numeric alias, including zero.

    Args:
        record (dict): One sensor record.
        aliases (tuple[str, ...]): Source fields in preference order.

    Returns:
        float|None: The first numeric observation.
    """
    for name in aliases:
        value = number(record.get(name))
        if value is not None:
            return value
    return None


def rain_inches(record, stems):
    """Read rain in inches; click conversion requires a known bucket size.

    Args:
        record (dict): One sensor record.
        stems (tuple[str, ...]): Accepted field stems in preference order.

    Returns:
        float|None: Rain quantity, or None if it cannot be converted.
    """
    for stem in stems:
        value = number(record.get(stem + '_in'))
        if value is not None:
            return value
        value = number(record.get(stem + '_mm'))
        if value is not None:
            return value / 25.4
        clicks = number(record.get(stem + '_clicks'))
        size = number(record.get('rain_size'))
        bucket = {1: 0.01, 2: 0.2 / 25.4, 3: 0.1 / 25.4, 4: 0.001}.get(
            int(size)) if size is not None and size.is_integer() else None
        if clicks is not None and bucket is not None:
            return clicks * bucket
    return None


def normalize(record, structure):
    """Normalize one documented data structure without merging sensors.

    Args:
        record (dict): One timestamped sensor record.
        structure (int): WeatherLink data_structure_type.

    Returns:
        dict: Observations in WeeWX US units, without dateTime/usUnits.
    """
    if structure == 9:
        # EnviroMonitor nodes reuse names such as temp_last for sensors whose
        # units depend on sensor_type. These need an explicit observation rule.
        return {}
    if structure in HEALTH:
        result = {}
        for name, (aliases, factor, _group) in HEALTH_FIELDS.items():
            value = first(record, aliases)
            if value is not None:
                result[name] = value * factor
        battery_temp = number(record.get('battery_temp'))
        if battery_temp is not None:
            result['davisBatteryTemp'] = battery_temp * 1.8 + 32
        return result
    result = {name: value for name, aliases in COMMON.items()
              if (value := first(record, aliases)) is not None}
    if structure in (3, 4, 7):
        # These archive formats encode compass directions as 0..15. WLL and
        # Console archives already carry degrees, even for the same field name.
        for name in ('windDir', 'windGustDir'):
            if name in result:
                direction = result[name]
                if direction.is_integer() and 0 <= direction < 16:
                    result[name] = direction * 22.5
                else:
                    del result[name]
    for name, stems in {
        'rainRate': ('rain_rate_last', 'rain_rate', 'rain_rate_hi'),
        'dayRain': ('rainfall_day', 'rainfall_daily', 'rain_day'),
        'monthRain': ('rainfall_month', 'rainfall_monthly', 'rain_month'),
        'yearRain': ('rainfall_year', 'rain_year'),
        'stormRain': ('rain_storm_current', 'rain_storm'),
    }.items():
        value = rain_inches(record, stems)
        if value is not None:
            result[name] = value
    if structure in ARCHIVE:
        value = rain_inches(record, ('rainfall',))
        if value is not None:
            result['rain'] = value
    return result


class Decoder:
    """Select observations by LSID and refuse ambiguous automatic mappings."""

    def __init__(self, options):
        """Register standard units and validate explicit sensor selections.

        Args:
            options (dict): sensor_map and optional observations subsections.
        """
        self.sensor_map = dict(options.get('sensor_map', {}))
        self.observations = dict(options.get('observations', {}))
        self.node_archive_interval = number(options.get('node_archive_interval'))
        self.rain_state = {}
        self.unknown = set()
        for name, group in GROUPS.items():
            weewx.units.obs_group_dict.setdefault(name, group)
        for target, spec in self.observations.items():
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', target):
                raise ValueError('Invalid observation name')
            if target in ('dateTime', 'usUnits', 'interval', 'rain'):
                raise ValueError('Reserved custom observation: ' + target)
            group = spec['group']
            unit = spec['unit']
            if group not in weewx.units.USUnits:
                raise ValueError('Unknown WeeWX observation group: ' + group)
            existing = weewx.units.obs_group_dict.get(target)
            if existing is not None and existing != group:
                raise ValueError('Conflicting unit group for ' + target)
            # Check conversion before a service starts, not on its first packet.
            weewx.units.convertStd(weewx.units.ValueTuple(1, unit, group), weewx.US)
            weewx.units.obs_group_dict[target] = group
        for target, selector in self.sensor_map.items():
            if target in ('dateTime', 'usUnits', 'interval') or target not in weewx.units.obs_group_dict:
                raise ValueError('Unknown or reserved sensor_map target: ' + target)
            if not re.fullmatch(r'\d+\.[A-Za-z][A-Za-z0-9_]*', selector):
                raise ValueError('sensor_map values must be LSID.observation')
            source = selector.split('.', 1)[1]
            source_group = weewx.units.obs_group_dict.get(source)
            if source_group != weewx.units.obs_group_dict[target]:
                raise ValueError('sensor_map source and target must have the same unit group')

    def records(self, payload, historic=False, health_only=False):
        """Validate the common response envelope and yield individual records.

        Args:
            payload (dict): Current or historical API response.
            historic (bool): True for archive observations.
            health_only (bool): True to exclude weather observations.

        Yields:
            tuple[str, int, dict]: LSID, structure type and source record.
        """
        sensors = payload.get('sensors')
        if not isinstance(sensors, list):
            raise weewx.WeeWxIOError('WeatherLink response has no sensor list')
        allowed = HEALTH if health_only else (ARCHIVE if historic else CURRENT | HEALTH)
        for sensor in sensors:
            try:
                structure = int(sensor['data_structure_type'])
                lsid = str(int(sensor['lsid']))
                records = sensor['data']
            except (KeyError, ValueError, TypeError):
                raise weewx.WeeWxIOError('Malformed WeatherLink sensor envelope') from None
            if structure not in CURRENT | ARCHIVE | HEALTH:
                if structure not in self.unknown:
                    log.warning('Unknown WeatherLink data structure %d', structure)
                    self.unknown.add(structure)
            if structure not in allowed:
                continue
            if not isinstance(records, list):
                raise weewx.WeeWxIOError('WeatherLink sensor data is not a list')
            for record in records:
                if not isinstance(record, dict) or number(record.get('ts')) is None:
                    raise weewx.WeeWxIOError('WeatherLink sensor record has no timestamp')
                yield lsid, structure, record

    def merge(self, records):
        """Resolve a collection of sensor records without first-sensor wins.

        Args:
            records (list[tuple[str, int, dict]]): Records for a current poll or
                exactly one archive timestamp.

        Returns:
            tuple[dict, tuple|None]: Packet fields and cumulative-rain identity.
        """
        candidates = collections.defaultdict(list)
        rain = {}
        for lsid, structure, raw in records:
            decoded = normalize(raw, structure)
            if structure not in ARCHIVE:
                # A rolling 15-minute total is not an interval increment. Only
                # a cumulative counter can be differenced between cloud polls.
                for period in ('yearRain', 'monthRain', 'dayRain'):
                    if period in decoded:
                        rain[lsid] = (lsid, raw['ts'], period, decoded[period])
                        decoded['rain'] = decoded[period]
                        break
            for target, spec in self.observations.items():
                if lsid == str(spec['sensor']):
                    value = number(raw.get(spec['field']))
                    if value is not None:
                        decoded[target] = weewx.units.convertStd(
                            weewx.units.ValueTuple(value, spec['unit'], spec['group']), weewx.US)[0]
            for name, value in decoded.items():
                if not name.startswith('_'):
                    candidates[name].append((lsid, value))
        packet = {}
        rain_source = ''
        for target in candidates.keys() | self.sensor_map.keys():
            if target in self.sensor_map:
                selected, source = self.sensor_map[target].split('.', 1)
                choices = [item for item in candidates[source] if item[0] == selected]
            else:
                choices = candidates[target]
            # A selected source may also be exported under a separate target,
            # e.g. extraTemp1 = 456.outTemp, while outTemp selects another ISS.
            if len(choices) > 1:
                raise weewx.ViolatedPrecondition(
                    'Ambiguous WeatherLink observation %s from LSIDs %s; set sensor_map' %
                    (target, ', '.join(sorted({item[0] for item in choices}))))
            if choices:
                selected, value = choices[0]
                packet[target] = value
                if target == 'rain':
                    rain_source = selected
        return packet, rain.get(rain_source)

    def current(self, payload, now, max_age=1800, health_only=False):
        """Decode current values, rejecting stale and future sensor samples.

        Args:
            payload (dict): Current API response.
            now (int|float): Time of the local poll.
            max_age (int|float): Oldest acceptable sensor age, in seconds.
            health_only (bool): True to include only health structures.

        Returns:
            dict: LOOP packet in US units. Rain is a counter delta or None.
        """
        records = [item for item in self.records(payload, health_only=health_only)
                   if now - max_age <= float(item[2]['ts']) <= now + 30]
        if not records:
            raise weewx.WeeWxIOError('WeatherLink has no fresh observations')
        if not health_only and not any(item[1] in CURRENT for item in records):
            raise weewx.WeeWxIOError('WeatherLink weather observations are stale')
        packet, identity = self.merge(records)
        if not packet:
            raise weewx.WeeWxIOError('WeatherLink has no mapped observations')
        if identity is not None:
            packet['rain'] = None
            old = self.rain_state.get('last')
            if old is None or identity[0] != old[0] or identity[1] > old[1]:
                if old and identity[0] == old[0] and identity[2] == old[2] and identity[3] >= old[3]:
                    packet['rain'] = identity[3] - old[3]
                self.rain_state['last'] = identity
        # A health service caches the result. Preserve the oldest sample time
        # there so polling cannot extend an already old reading's lifetime.
        stamp = min(float(item[2]['ts']) for item in records) if health_only else now
        packet.update(dateTime=int(stamp), usUnits=weewx.US)
        return packet

    def archive(self, payload, start, end):
        """Decode ordered (start, end] archive rows, merging exact timestamps.

        Args:
            payload (dict): Historical API response.
            start (int): Exclusive lower boundary.
            end (int): Inclusive upper boundary.

        Yields:
            dict: Archive records with their own interval, in US units.
        """
        grouped = collections.defaultdict(list)
        for lsid, structure, raw in self.records(payload, historic=True):
            stamp = number(raw['ts'])
            if stamp is None or not stamp.is_integer():
                raise weewx.WeeWxIOError('Invalid WeatherLink archive timestamp')
            if start < stamp <= end:
                grouped[int(stamp)].append((lsid, structure, raw))
        for stamp in sorted(grouped):
            records = grouped[stamp]
            intervals = {number(raw.get('arch_int')) for _, structure, raw in records
                         if structure != 9 or 'arch_int' in raw}
            if not intervals:
                intervals = {self.node_archive_interval}
            if len(intervals) != 1 or None in intervals:
                raise weewx.WeeWxIOError('Missing or conflicting WeatherLink archive intervals')
            interval = intervals.pop()
            if interval is None or interval <= 0 or interval % 60:
                raise weewx.WeeWxIOError('WeatherLink archive interval must be whole minutes')
            packet, _ = self.merge(records)
            if packet:
                packet.update(dateTime=stamp, interval=int(interval / 60), usUnits=weewx.US)
                yield packet
