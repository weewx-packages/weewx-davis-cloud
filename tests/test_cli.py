"""Read-only diagnostics must expose neither credentials nor raw health data."""

import json
import sys
from unittest.mock import Mock

import pytest

from user.daviscloud import __main__ as cli
from user.daviscloud.api import APIError


def test_sensor_inventory_omits_values():
    value = cli.inventory({'sensors': [{'lsid': 1, 'sensor_type': 509, 'data_structure_type': 27,
                                       'data': [{'ts': 100, 'ip_v4_address': 'private-address',
                                                 'console_sw_version': 'private-version'}]}]})
    text = json.dumps(value)
    assert 'private-address' not in text
    assert 'private-version' not in text
    assert value[0]['fields'] == ['console_sw_version', 'ip_v4_address', 'ts']


@pytest.mark.parametrize('command', [['--current'], ['--sensors'], ['--history', '1', '300']])
def test_diagnostic_actions_are_read_only(command, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['daviscloud', '--config', 'private.conf', *command])
    options = {'api_key': 'private-key', 'api_secret': 'private-secret'}
    monkeypatch.setattr(cli.weecfg, 'read_config', lambda path: (path, {'DavisCloud': options}))
    driver = Mock()
    driver.api.version = '2'
    driver.api.current.return_value = {'sensors': []}
    driver.get_current.return_value = {'outTemp': 68}
    driver.history.return_value = iter([{'outTemp': 68}])
    monkeypatch.setattr(cli, 'DavisCloud', lambda **kwargs: driver)
    assert cli.main() == 0
    text = capsys.readouterr().out
    assert 'private-key' not in text and 'private-secret' not in text
    assert json.loads(text) is not None


def test_station_list_only_exposes_selection_metadata(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['daviscloud', '--config', 'private.conf', '--stations'])
    monkeypatch.setattr(cli.weecfg, 'read_config', lambda path: (path, {'DavisCloud': {}}))
    api = Mock(version='2')
    api.get.return_value = {'stations': [{'station_id': 123, 'station_name': 'Example',
                                        'latitude': 'private-coordinate', 'api_key': 'private-key'}]}
    monkeypatch.setattr(cli, 'CloudAPI', lambda options: api)
    assert cli.main() == 0
    text = capsys.readouterr().out
    assert 'private-coordinate' not in text and 'private-key' not in text
    assert json.loads(text)[0]['station_id'] == 123


def test_configuration_error_text_cannot_expose_credentials(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['daviscloud', '--config', 'private.conf', '--current'])
    monkeypatch.setattr(cli.weecfg, 'read_config', lambda path: (path, {'DavisCloud': {}}))
    monkeypatch.setattr(cli, 'DavisCloud', Mock(side_effect=ValueError('private-secret')))
    assert cli.main() == 2
    assert 'private-secret' not in capsys.readouterr().err
    monkeypatch.setattr(cli, 'DavisCloud', Mock(side_effect=APIError('WeatherLink HTTP 401', 401)))
    assert cli.main() == 2
    assert 'HTTP 401' in capsys.readouterr().err
