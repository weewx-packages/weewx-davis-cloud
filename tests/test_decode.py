"""Check physical quantities, collision handling and archive time semantics."""

import copy
import math

import pytest
import weewx
import weewx.units

from user.daviscloud.decode import Decoder, normalize, number, rain_inches


@pytest.mark.parametrize('structure,fields', [
    (1, {'temp_out': 68, 'hum_out': 0, 'wind_speed': 2, 'wind_dir': 270}),
    (2, {'temp_out': 68, 'hum_out': 0, 'wind_speed': 2, 'wind_dir': 270}),
    (6, {'temp_out': 68, 'hum_out': 0, 'wind_speed': 2, 'wind_dir': 270}),
    (10, {'temp': 68, 'hum': 0, 'wind_speed_last': 2, 'wind_dir_last': 270}),
    (23, {'temp': 68, 'hum': 0, 'wind_speed_last': 2, 'wind_dir_last': 270}),
])
def test_weather_families_physical_values(envelope, structure, fields):
    """Different device layouts must yield the same physical weather."""
    packet = Decoder({}).current(envelope(structure, fields), 1700000100)
    assert packet == {'dateTime': 1700000100, 'usUnits': weewx.US,
                      'outTemp': 68, 'outHumidity': 0, 'windSpeed': 2, 'windDir': 270}
    metric = weewx.units.to_std_system(packet, weewx.METRICWX)
    assert metric['outTemp'] == pytest.approx(20)
    assert metric['windSpeed'] == pytest.approx(0.89408)


@pytest.mark.parametrize('structure', [3, 4, 7, 11, 24])
def test_archive_average_direction_rain_and_interval(envelope, structure):
    direction = 12 if structure in (3, 4, 7) else 270
    fields = {'temp_out': 68, 'temp_avg': 68, 'temp_last': 99,
              'wind_speed_avg': 2, 'wind_speed_hi': 8,
              'wind_dir_of_prevail': direction, 'rainfall_mm': 2.54,
              'arch_int': 900}
    records = list(Decoder({}).archive(envelope(structure, fields), 1700000000, 1700000200))
    assert len(records) == 1
    record = records[0]
    assert record['outTemp'] == 68
    assert record['windDir'] == 270
    assert record['windSpeed'] == 2
    assert record['windGust'] == 8
    assert record['rain'] == pytest.approx(0.1)
    assert record['interval'] == 15


@pytest.mark.parametrize('structure,fields,expected', [
    (12, {'bar_sea_level': 29.92, 'bar_absolute': 29.1}, {'barometer': 29.92, 'pressure': 29.1}),
    (19, {'bar_sea_level': 29.92, 'bar_absolute': 29.1}, {'barometer': 29.92, 'pressure': 29.1}),
    (21, {'temp_in': 68, 'hum_in': 0}, {'inTemp': 68, 'inHumidity': 0}),
    (25, {'temp_1': 32, 'moist_soil_1': 15, 'wet_leaf_1': 0},
     {'soilTemp1': 32, 'soilMoist1': 15, 'leafWet1': 0}),
    (16, {'pm_1': 0, 'pm_2p5': 12.5, 'pm_10': 18}, {'pm1_0': 0, 'pm2_5': 12.5, 'pm10_0': 18}),
    (17, {'pm_1_avg': 0, 'pm_2p5_avg': 12.5}, {'pm1_0': 0, 'pm2_5': 12.5}),
    (22, {'temp_in_last': 68, 'hum_in_last': 0}, {'inTemp': 68, 'inHumidity': 0}),
    (26, {'temp_last_1': 32, 'moist_soil_last_1': 15}, {'soilTemp1': 32, 'soilMoist1': 15}),
    (13, {'temp_last_1': 32, 'moist_soil_last_1': 15, 'wet_leaf_last_1': 0},
     {'soilTemp1': 32, 'soilMoist1': 15, 'leafWet1': 0}),
    (20, {'bar_sea_level': 29.92}, {'barometer': 29.92}),
])
def test_non_iss_families(structure, fields, expected):
    assert normalize(fields, structure) == expected


@pytest.mark.parametrize('structure', [14, 15, 18, 27])
def test_health_millivolts_and_celsius(envelope, structure):
    packet = Decoder({}).current(envelope(structure, {
        'battery_voltage': 4100, 'battery_temp': 20, 'wifi_rssi': -65,
        'os_uptime': 123456789012, 'battery_percent': 0}), 1700000100, health_only=True)
    assert packet['davisBatteryVoltage'] == pytest.approx(4.1)
    assert packet['davisBatteryTemp'] == 68
    assert packet['davisUptime'] == 123456789012
    assert packet['davisBatteryPercent'] == 0


@pytest.mark.parametrize('bucket,inches', [(1, 0.01), (2, 0.2 / 25.4), (3, 0.1 / 25.4), (4, 0.001)])
def test_click_size_is_not_assumed(bucket, inches):
    assert rain_inches({'rainfall_clicks': 3, 'rain_size': bucket}, ('rainfall',)) == pytest.approx(3 * inches)
    assert rain_inches({'rainfall_clicks': 3}, ('rainfall',)) is None


def test_multiple_sensors_require_mapping_and_can_be_remapped(envelope):
    response = envelope(23, {'temp': 68, 'hum': 60})
    response['sensors'] += envelope(23, {'temp': 32, 'hum': 20}, lsid=202)['sensors']
    with pytest.raises(weewx.ViolatedPrecondition, match='Ambiguous.*out'):
        Decoder({}).current(response, 1700000100)
    packet = Decoder({'sensor_map': {'outTemp': '101.outTemp', 'outHumidity': '101.outHumidity',
                                    'extraTemp1': '202.outTemp', 'extraHumid1': '202.outHumidity'}}).current(
                                        response, 1700000100)
    assert packet['outTemp'] == 68
    assert packet['extraTemp1'] == 32
    assert packet['extraHumid1'] == 20


def test_current_rain_is_delta_not_rolling_sum(envelope):
    decoder = Decoder({})
    first = envelope(23, {'temp': 68, 'rainfall_year_in': 10, 'rainfall_last_15_min_in': 7})
    assert decoder.current(first, 1700000100)['rain'] is None
    second = envelope(23, {'temp': 68, 'rainfall_year_in': 10.1}, stamp=1700000160)
    assert decoder.current(second, 1700000160)['rain'] == pytest.approx(0.1)
    assert decoder.current(second, 1700000161)['rain'] is None
    assert decoder.current(first, 1700000162)['rain'] is None
    third = envelope(23, {'temp': 68, 'rainfall_year_in': 10.2}, stamp=1700000220)
    assert decoder.current(third, 1700000220)['rain'] == pytest.approx(0.1)
    reset = envelope(23, {'temp': 68, 'rainfall_year_in': 0.2}, stamp=1700000280)
    assert decoder.current(reset, 1700000280)['rain'] is None
    after = envelope(23, {'temp': 68, 'rainfall_year_in': 0.3}, stamp=1700000340)
    assert decoder.current(after, 1700000340)['rain'] == pytest.approx(0.1)


def test_same_timestamp_archive_sensors_only(envelope):
    response = envelope(24, {'temp_avg': 68, 'arch_int': 300}, stamp=1700000100)
    barometer = envelope(20, {'bar_sea_level': 29.92, 'arch_int': 300}, lsid=202, stamp=1700000400)
    response['sensors'] += barometer['sensors']
    records = list(Decoder({}).archive(response, 1700000000, 1700000400))
    assert [r['dateTime'] for r in records] == [1700000100, 1700000400]
    assert 'barometer' not in records[0]
    assert 'outTemp' not in records[1]
    assert not list(Decoder({}).archive(response, 1700000400, 1700000500))


@pytest.mark.parametrize('interval', [None, 0, 30, 90, float('nan')])
def test_invalid_archive_intervals_fail(envelope, interval):
    with pytest.raises(weewx.WeeWxIOError, match='interval'):
        list(Decoder({}).archive(envelope(24, {'temp_avg': 68, 'arch_int': interval}),
                                 1700000000, 1700000200))


def test_fresh_health_does_not_hide_stale_weather(envelope):
    payload = envelope(23, {'temp': 68}, stamp=1600000000)
    payload['sensors'] += envelope(27, {'battery_voltage': 4100}, lsid=202)['sensors']
    with pytest.raises(weewx.WeeWxIOError, match='stale'):
        Decoder({}).current(payload, 1700000100)
    assert Decoder({}).current(payload, 1700000100, health_only=True)['davisBatteryVoltage'] == pytest.approx(4.1)


def test_enviro_nodes_require_declared_units(envelope):
    response = envelope(9, {'temp_last': 20})
    with pytest.raises(weewx.WeeWxIOError, match='mapped'):
        Decoder({}).current(response, 1700000100)
    decoder = Decoder({'observations': {'extraTemp1': {
        'sensor': 101, 'field': 'temp_last', 'unit': 'degree_C', 'group': 'group_temperature'}}})
    assert decoder.current(response, 1700000100)['extraTemp1'] == 68


@pytest.mark.parametrize('value', [None, '', 'unknown', math.nan, math.inf, True])
def test_missing_numbers_are_not_zero(value):
    assert number(value) is None


def test_input_is_not_modified(envelope):
    response = envelope(23, {'temp': 0, 'rainfall_year_in': 0})
    before = copy.deepcopy(response)
    Decoder({}).current(response, 1700000100)
    assert response == before


def test_unknown_structure_is_reported_once(envelope, caplog):
    decoder = Decoder({})
    for _ in range(2):
        with pytest.raises(weewx.WeeWxIOError):
            decoder.current(envelope(999, {'temp': 68}), 1700000100)
    assert caplog.text.count('Unknown WeatherLink data structure 999') == 1
