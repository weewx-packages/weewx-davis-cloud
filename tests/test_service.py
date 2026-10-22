"""Exercise actual health callbacks against an independent LOOP event."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import weewx

from user.daviscloud.decode import Decoder
from user.daviscloud.service import HealthService


def test_health_callback_units_freshness_and_existing_values(envelope, monkeypatch):
    # Construct only the background worker's completed state. This callback is
    # the one bound by the real service, not a duplicate implementation.
    service = HealthService.__new__(HealthService)
    service.lock = threading.Lock()
    service.max_age = 1800
    service.packet = Decoder({}).current(envelope(27, {
        'battery_temp': 20, 'battery_voltage': 4100}), 1700000100, health_only=True)
    monkeypatch.setattr('user.daviscloud.service.time.time', lambda: 1700000101)
    event = SimpleNamespace(packet={'dateTime': 1700000101, 'usUnits': weewx.METRIC,
                                    'outTemp': 10, 'davisBatteryVoltage': 5})
    service.new_loop_packet(event)
    assert event.packet['davisBatteryTemp'] == pytest.approx(20)
    assert event.packet['davisBatteryVoltage'] == 5
    assert event.packet['outTemp'] == 10
    assert event.packet['dateTime'] == 1700000101
    monkeypatch.setattr('user.daviscloud.service.time.time', lambda: 1700003000)
    event.packet.pop('davisBatteryTemp')
    service.new_loop_packet(event)
    assert 'davisBatteryTemp' not in event.packet


def test_real_service_initialization_and_worker(envelope, monkeypatch):
    api = Mock(version='2')
    api.select_station.return_value = '123'
    api.current.return_value = envelope(27, {'battery_voltage': 4100})
    monkeypatch.setattr('user.daviscloud.service.CloudAPI', lambda options: api)
    monkeypatch.setattr('user.daviscloud.service.time.time', lambda: 1700000100)
    monkeypatch.setattr('user.daviscloud.service.threading.Thread.start', lambda thread: None)
    engine = Mock()
    service = HealthService(engine, {'DavisCloud': {}})
    engine.bind.assert_called_once_with(weewx.NEW_LOOP_PACKET, service.new_loop_packet)
    service.stop.wait = Mock(side_effect=lambda interval: service.stop.set())
    service.run()
    assert service.packet['davisBatteryVoltage'] == pytest.approx(4.1)
    assert service.stop.is_set()


def test_health_cache_keeps_the_original_sample_age(envelope):
    """A later poll must not give an old sample another full freshness period."""
    sample = envelope(27, {'battery_voltage': 4100}, stamp=1700000000)
    packet = Decoder({}).current(sample, 1700001700, max_age=1800, health_only=True)
    assert packet['dateTime'] == 1700000000
