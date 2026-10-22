# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inspect stations, sensors and archive data without changing configuration."""

import argparse
import json
import sys

import weecfg
import weewx

from user.daviscloud.api import APIError, CloudAPI
from user.daviscloud.driver import DavisCloud


def inventory(payload):
    """Describe sensor identities and fields without exposing raw health data.

    Args:
        payload (dict): Current v2 observations.

    Returns:
        list[dict]: LSIDs, sensor/structure types and available field names.
    """
    return [{'lsid': sensor['lsid'], 'sensor_type': sensor.get('sensor_type'),
             'data_structure_type': sensor['data_structure_type'],
             'fields': sorted({name for record in sensor.get('data', []) for name in record})}
            for sensor in payload.get('sensors', [])]


def main():
    """Run the read-only cloud diagnostic and return a shell status.

    Returns:
        int: Zero on success, two on configuration or acquisition failure.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Path to weewx.conf')
    commands = parser.add_mutually_exclusive_group(required=True)
    commands.add_argument('--stations', action='store_true')
    commands.add_argument('--sensors', action='store_true')
    commands.add_argument('--current', action='store_true')
    commands.add_argument('--history', nargs=2, type=int, metavar=('START', 'END'))
    args = parser.parse_args()
    try:
        _, config = weecfg.read_config(args.config)
        options = config['DavisCloud']
        if args.stations:
            api = CloudAPI(options)
            if api.version != '2':
                raise ValueError('Station discovery requires API v2')
            result = [{key: station.get(key) for key in ('station_id', 'station_id_uuid', 'station_name')}
                      for station in api.get('stations').get('stations', [])]
        else:
            driver = DavisCloud(**options)
            if args.sensors:
                if driver.api.version != '2':
                    raise ValueError('Sensor discovery requires API v2')
                result = inventory(driver.api.current(driver.station))
            elif args.history:
                result = list(driver.history(*args.history))
            else:
                result = driver.get_current()
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (KeyError, ValueError, OSError, APIError, weewx.WeeWxIOError,
            weewx.ViolatedPrecondition, NotImplementedError) as error:
        # Never print arbitrary configuration exceptions: their values may
        # include credentials. Transport exceptions are sanitized at the API.
        message = str(error) if isinstance(error, (APIError, weewx.WeeWxIOError,
                                                  weewx.ViolatedPrecondition)) else type(error).__name__
        print('DavisCloud diagnostic failed: ' + message, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
