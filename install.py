# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the cloud driver without changing the active station or its schema."""

from weecfg.extension import ExtensionInstaller


def loader():
    return DavisCloudInstaller()


class DavisCloudInstaller(ExtensionInstaller):
    def __init__(self):
        super().__init__(
            version='0.1.0a2', name='davis-cloud',
            description='Unified Davis WeatherLink cloud driver and optional health service',
            author='weewx-packages contributors',
            files=[('bin/user/daviscloud', [
                'bin/user/daviscloud/__init__.py',
                'bin/user/daviscloud/api.py',
                'bin/user/daviscloud/decode.py',
                'bin/user/daviscloud/driver.py',
                'bin/user/daviscloud/service.py',
                'bin/user/daviscloud/__main__.py',
            ])])
