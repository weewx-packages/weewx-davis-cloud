"""Install a release and run its driver through the actual WeeWX engine."""

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys


def test_installed_engine_archive_and_provenance(tmp_path):
    """Use installed code, real StdArchive and SQLite, and preserve config/DBs."""
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('daviscloud_build', root / 'tools/build.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = module.build()
    first = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert hashlib.sha256(module.build().read_bytes()).hexdigest() == first
    station = tmp_path / 'station'
    executable = Path(sys.executable).parent / 'weectl'
    subprocess.run([str(executable), 'station', 'create', str(station), '--no-prompt'],
                   check=True, capture_output=True, text=True)
    config = station / 'weewx.conf'
    original_config = config.read_bytes()
    env = dict(os.environ, PYTHONPATH=str(station / 'bin'))
    installation = subprocess.run([str(executable), 'extension', 'install', str(archive),
                                   '--config', str(config), '--yes'],
                                  env=env, capture_output=True, text=True)
    assert installation.returncode == 0, installation.stdout + installation.stderr
    # No station activation, config writes, custom schema or database migrations
    # belong in an extension installer. Activation is a separate operator action.
    assert config.read_bytes() == original_config
    for source in (root / 'bin/user/daviscloud').glob('*.py'):
        assert (station / 'bin/user/daviscloud' / source.name).read_bytes() == source.read_bytes()
    code = '''
from pathlib import Path
import sys
import time
from unittest.mock import Mock
import weecfg
import weewx
import weewx.engine
import user.daviscloud.driver as driver

station = Path(sys.argv[1])
assert Path(driver.__file__).resolve() == station / 'bin/user/daviscloud/driver.py'
_, config = weecfg.read_config(str(station / 'weewx.conf'))
config['Station']['station_type'] = 'DavisCloud'
config['DavisCloud'] = {'driver': 'user.daviscloud.driver', 'archive': 'true'}
config['StdConvert']['target_unit'] = 'METRICWX'
config['StdArchive']['record_generation'] = 'hardware'
config['Engine']['Services']['restful_services'] = []
config['Engine']['Services']['report_services'] = []
now = int(time.time())
stamp = (now // 300 - 1) * 300
api = Mock(version='2')
api.select_station.return_value = '123'
api.historic.return_value = {'sensors': [{'lsid': 101, 'data_structure_type': 24,
    'data': [{'ts': stamp, 'arch_int': 300, 'temp_avg': 68, 'rainfall_in': 0.1}]}]}
api.current.return_value = {'sensors': [{'lsid': 101, 'data_structure_type': 23,
    'data': [{'ts': now, 'temp': 68, 'hum': 60}]}]}
driver.CloudAPI = lambda options: api
engine = weewx.engine.StdEngine(config)
try:
    engine.dispatchEvent(weewx.Event(weewx.STARTUP))
    manager = engine.db_binder.get_manager('wx_binding')
    saved = manager.getRecord(stamp)
    assert saved['outTemp'] == 20.0, saved
    assert abs(saved['rain'] - 2.54) < 1e-8, saved
    assert saved['usUnits'] == weewx.METRICWX
    event = weewx.Event(weewx.NEW_LOOP_PACKET, packet=engine.console.get_current())
    engine.dispatchEvent(event)
    assert event.packet['outTemp'] == 20.0, event.packet
    assert event.packet['outHumidity'] == 60
finally:
    engine.shutDown()
print('installed-engine-and-sqlite-ok')
'''
    result = subprocess.run([sys.executable, '-c', code, str(station)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'installed-engine-and-sqlite-ok' in result.stdout
