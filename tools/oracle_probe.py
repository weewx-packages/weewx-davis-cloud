# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare pinned existing decoders in isolated, network-disabled processes.

Oracle sources stay in research and are never included in the extension. Their
hashes and revisions are recorded in docs/oracles.json. Fixtures describe
protocol observations, not code copied from those implementations.
"""

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import urllib.request
from unittest.mock import Mock, patch


def compare(expected, actual, fields):
    """Require each chosen measurement in both implementations.

    Args:
        expected (dict): Existing implementation's result.
        actual (dict): New driver's result.
        fields (list[str]): Explicitly required observations.

    Returns:
        dict: Compared values, indexed by observation name.
    """
    result = {}
    for field in fields:
        if field not in expected or field not in actual:
            raise AssertionError('Missing oracle observation: ' + field)
        old = expected[field]
        new = actual[field]
        if abs(old - new) > 1e-8:
            raise AssertionError('Oracle difference for %s: %r != %r' % (field, old, new))
        result[field] = new
    return result


def worker(name, directory, root):
    """Load one original decoder and compare only its acquisition boundary.

    Args:
        name (str): Oracle name in the manifest.
        directory (Path): Verified, private source cache.
        root (Path): This project's root.

    Returns:
        dict: Measurement comparisons and explicitly tested semantic differences.
    """
    import requests
    from user.daviscloud.decode import Decoder
    from user.daviscloud.driver import legacy_response

    manifest = json.loads((root / 'docs/oracles.json').read_text())[name]
    path = directory / (name + '.py')
    if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256']:
        raise ValueError('Oracle source checksum mismatch')
    spec = importlib.util.spec_from_file_location('oracle_' + name, path)
    if spec is None or spec.loader is None:
        raise ValueError('Cannot load the verified oracle module')
    module = importlib.util.module_from_spec(spec)
    # Two old modules bind UDP port 22222 at import time. Only that hardware
    # boundary is replaced; their weather decoding functions remain unchanged.
    with patch.object(socket, 'socket', Mock()), \
            patch.object(socket, 'create_connection', side_effect=AssertionError('Oracle network access')), \
            patch.object(requests, 'get', side_effect=AssertionError('Oracle HTTP access')):
        spec.loader.exec_module(module)
        fixtures = json.loads((root / 'tests/fixtures/oracles.json').read_text())
        weather = ['outTemp', 'outHumidity', 'windSpeed', 'windDir', 'windGust', 'windGustDir']
        result = {'source': manifest, 'comparisons': [], 'differences': []}
        if name == 'console':
            response = fixtures['console']
            old_driver = module.DavisConsoleAPIDriver(api_key='fixture-key', api_secret='fixture-secret', station_id=123)
            setattr(module, 'get_json', lambda *args: copy.deepcopy(response))
            old = old_driver.get_data(old_driver)
            new = Decoder({}).current(response, 1700000100)
            result['comparisons'].append(compare(old, new, weather + ['UV', 'radiation', 'dewpoint']))
            response['sensors'][0]['data'][0]['temp'] = 0
            old = old_driver.get_data(old_driver)
            new = Decoder({}).current(response, 1700000100)
            if 'outTemp' in old or new['outTemp'] != 0:
                raise AssertionError('Console zero-temperature behavior changed')
            result['differences'].append({'case': 'zero Fahrenheit is a valid temperature',
                                          'old': 'outTemp absent', 'new': new['outTemp']})
        elif name == 'wlip':
            response = fixtures['wlip_current']
            old = module.map_current_record(response['sensors'][0]['data'][0])
            new = Decoder({}).current(response, 1700000100)
            result['comparisons'].append(compare(old, new, ['outTemp', 'outHumidity', 'windSpeed', 'windDir', 'barometer']))
            response = fixtures['wlip_archive']
            old = module.map_historic_record(response['sensors'][0]['data'][0])
            new = next(Decoder({}).archive(response, 1700000000, 1700000200))
            result['comparisons'].append(compare(old, new, weather + ['rain', 'interval', 'barometer']))
        elif name in ('wll', 'wldc'):
            options = {'device_id': 'iss:1', 'hostname': 'unused.invalid', 'port': 80, 'wl_archive_interval': 5}
            cls = module.WLLDriverAPI if name == 'wll' else module.WLDCDriverAPI
            old_driver = cls(options)
            response = fixtures['wll_archive']
            if name == 'wll':
                old = next(old_driver.data_decode_wl(response, 1699999800, 1700000100))
            else:
                old = next(old_driver.data_decode_single_wl(response, timestamp=1700000100))
            new = next(Decoder({}).archive(response, 1700000000, 1700000200))
            result['comparisons'].append(compare(old, new, weather + ['rain', 'interval']))
            response['sensors'][0]['data'][0]['temp_last'] = 70
            if name == 'wll':
                old = next(old_driver.data_decode_wl(response, 1699999800, 1700000100))
            else:
                old = next(old_driver.data_decode_single_wl(response, timestamp=1700000100))
            new = next(Decoder({}).archive(response, 1700000000, 1700000200))
            if old['outTemp'] != 70 or new['outTemp'] != 68:
                raise AssertionError('Archive average/final-value policy changed')
            result['differences'].append({'case': 'archive uses reported temperature average',
                                          'old_final': 70, 'new_average': 68})
        else:
            response = {'observation_time_rfc822': 'Tue, 14 Nov 2023 22:15:00 GMT',
                        'temp_f': 68, 'relative_humidity': 60, 'pressure_in': 29.92,
                        'wind_mph': 2, 'wind_degrees': 270,
                        'davis_current_observation': {'temp_in_f': 72, 'relative_humidity_in': 50}}
            old = module._parse_loop(response)
            new = Decoder({}).current(legacy_response(response), 1700000100)
            result['comparisons'].append(compare(old, new, ['outTemp', 'outHumidity', 'windSpeed', 'windDir', 'barometer', 'inTemp']))
            response['relative_humidity'] = 0
            old = module._parse_loop(response)
            new = Decoder({}).current(legacy_response(response), 1700000100)
            if 'outHumidity' in old or new['outHumidity'] != 0:
                raise AssertionError('Legacy zero-humidity behavior changed')
            result['differences'].append({'case': 'numeric zero humidity is retained',
                                          'old': 'outHumidity absent', 'new': 0})
    return result


def main():
    """Fetch pinned oracle files on request, then run isolated comparisons."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--worker')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.directory or root / 'research/oracles'
    manifest = json.loads((root / 'docs/oracles.json').read_text())
    if args.download:
        directory.mkdir(parents=True, exist_ok=True)
        for name, source in manifest.items():
            with urllib.request.urlopen(source['url'], timeout=30) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != source['sha256']:
                raise ValueError('Downloaded oracle checksum mismatch')
            (directory / (name + '.py')).write_bytes(data)
    if args.worker:
        result = worker(args.worker, directory, root)
    else:
        result = {}
        env = dict(os.environ, PYTHONPATH=str(root / 'bin'))
        for name in manifest:
            completed = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                        '--worker', name, '--directory', str(directory)],
                                       check=True, capture_output=True, text=True, env=env, timeout=30)
            result[name] = json.loads(completed.stdout)
    text = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
    main()
