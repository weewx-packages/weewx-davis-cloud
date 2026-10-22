"""Run the real driver interface and write its records to a WeeWX database."""

import copy
import email.utils
from unittest.mock import Mock

import pytest
import weewx
import weewx.manager
import weewx.schemas.wview_extended

from user.daviscloud.api import APIError
from user.daviscloud.driver import DavisCloud, CloudConfEditor, legacy_response, loader


def client():
    """Return an API boundary with no HTTP calls or public credentials."""
    api = Mock(version='2')
    api.select_station.return_value = '123'
    return api


def test_instantiated_driver_loop_values_and_rain(envelope, monkeypatch):
    api = client()
    api.current.side_effect = [
        envelope(23, {'temp': 68, 'rainfall_year_in': 1}),
        envelope(23, {'temp': 69, 'rainfall_year_in': 1.1}, stamp=1700000160)]
    driver = DavisCloud(client=api)
    monkeypatch.setattr('user.daviscloud.driver.time.time', lambda: 1700000160)
    assert driver.get_current()['outTemp'] == 68
    assert driver.get_current()['rain'] == pytest.approx(0.1)
    assert driver.hardware_name == 'Davis WeatherLink Cloud'
    assert driver.archive_interval == 300


def test_real_archive_database_round_trip(envelope, tmp_path):
    api = client()
    response = envelope(24, {'temp_avg': 68, 'rainfall_in': 0.1, 'arch_int': 300})
    response['sensors'] += envelope(20, {'bar_sea_level': 29.92, 'arch_int': 300}, lsid=202)['sensors']
    api.historic.return_value = response
    driver = DavisCloud(client=api, archive=True)
    database = {'database_name': str(tmp_path / 'archive.sdb'), 'driver': 'weedb.sqlite'}
    with weewx.manager.DaySummaryManager.open_with_create(
            database, schema=weewx.schemas.wview_extended.schema) as manager:
        records = list(driver.history(1700000000, 1700000200))
        assert len(records) == 1
        manager.addRecord(records)
        saved = manager.getRecord(1700000100)
        assert saved['outTemp'] == 68
        assert saved['barometer'] == 29.92
        assert saved['rain'] == pytest.approx(0.1)
        assert saved['interval'] == 5
        assert saved['usUnits'] == weewx.US


def test_history_windows_are_contiguous_without_duplicates(envelope):
    api = client()

    def response(selected, start, end):
        result = envelope(24, {'temp_avg': 68, 'arch_int': 300}, stamp=start)
        result['sensors'][0]['data'].append({'temp_avg': 69, 'arch_int': 300, 'ts': end})
        return result

    api.historic.side_effect = response
    driver = DavisCloud(client=api, archive=True)
    records = list(driver.history(100, 100 + 2 * 86400 + 300))
    assert [r['dateTime'] for r in records] == [86500, 172900, 173200]
    assert api.historic.call_args_list[1].args == ('123', 86500, 172900)


def test_existing_database_cursor_is_not_truncated(monkeypatch):
    driver = DavisCloud(client=client(), archive=True, max_backfill_days=1)
    monkeypatch.setattr('user.daviscloud.driver.time.time', lambda: 1000000)
    driver.history = Mock(return_value=iter([]))
    assert list(driver.genStartupRecords(100)) == []
    driver.history.assert_called_once_with(100, 1000000)
    driver.history.reset_mock()
    assert list(driver.genStartupRecords(None)) == []
    driver.history.assert_called_once_with(913600, 1000000)


def test_malformed_window_is_not_partially_committed(envelope):
    api = client()
    response = envelope(24, {'temp_avg': 68, 'arch_int': 300})
    response['sensors'][0]['data'].append({'temp_avg': 69, 'arch_int': 90, 'ts': 1700000160})
    api.historic.return_value = response
    driver = DavisCloud(client=api, archive=True)
    with pytest.raises(weewx.WeeWxIOError):
        next(driver.history(1700000000, 1700000200))


def test_archive_permission_failure_is_not_silently_downgraded():
    api = client()
    api.historic.side_effect = APIError('WeatherLink HTTP 403', 403)
    driver = DavisCloud(client=api, archive=True)
    with pytest.raises(APIError):
        list(driver.history(1, 300))
    driver = DavisCloud(client=api, archive=False)
    with pytest.raises(NotImplementedError):
        list(driver.history(1, 300))


def test_archive_and_engine_generation_must_match():
    options = {'client': client(), 'archive': True}
    config = {'DavisCloud': options, 'StdArchive': {'record_generation': 'software'}}
    with pytest.raises(ValueError, match='hardware'):
        loader(config, None)


def test_legacy_current_timestamp_and_fields():
    response = {'observation_time_rfc822': email.utils.formatdate(1700000100),
                'temp_f': '68', 'relative_humidity': '0', 'wind_mph': 2,
                'pressure_in': 29.92, 'davis_current_observation': {
                    'rain_year_in': 1.5, 'temp_in_f': 72, 'relative_humidity_in': 50}}
    api = client()
    api.version = '1'
    api.current.return_value = response
    driver = DavisCloud(client=api)
    packet = driver.decoder.current(legacy_response(response), 1700000100)
    assert packet['outTemp'] == 68
    assert packet['outHumidity'] == 0
    assert packet['barometer'] == 29.92
    with pytest.raises(ValueError, match='v2'):
        DavisCloud(client=api, archive=True)
    with pytest.raises(weewx.WeeWxIOError, match='observation time'):
        legacy_response({})


@pytest.mark.parametrize('options', [
    {'poll_interval': 1}, {'max_age': 0}, {'poll_interval': 'nan'},
    {'max_backfill_days': 'inf'}, {'archive_interval': 90},
])
def test_bad_acquisition_options(options):
    with pytest.raises(ValueError):
        DavisCloud(client=client(), **options)


def test_configuration_template_does_not_replace_existing_database():
    import configobj

    editor = CloudConfEditor()
    stanza = configobj.ConfigObj(editor.default_stanza.splitlines())
    assert stanza['DavisCloud']['driver'] == 'user.daviscloud.driver'
    assert 'DataBindings' not in stanza
    assert stanza['DavisCloud']['archive'] == 'false'


def test_loop_close_and_permanent_failure(monkeypatch):
    driver = DavisCloud(client=client())
    driver.get_current = Mock(side_effect=APIError('WeatherLink HTTP 401', 401))
    with pytest.raises(APIError):
        next(driver.genLoopPackets())
    driver.closePort()
    assert list(driver.genLoopPackets()) == []


def test_loader_leaves_configuration_untouched():
    config = {'DavisCloud': {'client': client()}}
    before = copy.deepcopy({k: v for k, v in config['DavisCloud'].items() if k != 'client'})
    loader(config, None)
    assert {k: v for k, v in config['DavisCloud'].items() if k != 'client'} == before
