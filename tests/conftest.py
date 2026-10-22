"""Independent cloud samples with deliberately different sensor timestamps."""

import pytest


@pytest.fixture
def envelope():
    """Build a sensor envelope using values chosen by each test."""
    def build(structure, values, lsid=101, stamp=1700000100, **extra):
        return {'station_id': 123, 'sensors': [{
            'lsid': lsid, 'sensor_type': 37, 'data_structure_type': structure,
            'data': [dict(values, ts=stamp, **extra)]}]}
    return build


@pytest.fixture(autouse=True)
def no_external_http(monkeypatch):
    """Fail accidental network calls instead of contacting a real account."""
    import socket

    def deny(*args, **kwargs):
        raise AssertionError('Tests must not contact the public network')

    monkeypatch.setattr(socket, 'create_connection', deny)
