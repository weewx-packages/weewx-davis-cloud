# Copyright 2026 weewx-packages contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional cloud health enrichment for stations using a local driver."""

import logging
import threading
import time

import weewx
import weewx.engine
import weewx.units

from user.daviscloud.api import APIError, CloudAPI
from user.daviscloud.decode import Decoder

log = logging.getLogger(__name__)


class HealthService(weewx.engine.StdService):
    """Fetch health in the background so LOOP delivery never waits on HTTP."""

    def __init__(self, engine, config_dict):
        super().__init__(engine, config_dict)
        options = config_dict['DavisCloud']
        self.api = CloudAPI(options)
        if self.api.version != '2':
            raise ValueError('HealthService requires API v2')
        self.station = self.api.select_station(options.get('station_id'))
        self.decoder = Decoder(options)
        self.interval = float(options.get('health_interval', 900))
        self.max_age = float(options.get('max_age', 1800))
        if not 60 <= self.interval <= 86400 or not 0 < self.max_age <= 86400:
            raise ValueError('Invalid health polling or freshness limit')
        self.packet = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.bind(weewx.NEW_LOOP_PACKET, self.new_loop_packet)
        self.thread = threading.Thread(target=self.run, name='DavisCloudHealth', daemon=True)
        self.thread.start()

    def run(self):
        """Refresh the cached health packet until the owning engine stops."""
        while not self.stop.is_set():
            try:
                packet = self.decoder.current(self.api.current(self.station),
                                              time.time(), self.max_age, health_only=True)
                with self.lock:
                    self.packet = packet
            except (APIError, weewx.WeeWxIOError, weewx.ViolatedPrecondition) as error:
                log.error('DavisCloud health: %s', error)
            self.stop.wait(self.interval)

    def new_loop_packet(self, event):
        """Add fresh health in the event's units, preserving existing readings.

        Args:
            event (weewx.Event): NEW_LOOP_PACKET event.
        """
        with self.lock:
            packet = dict(self.packet) if self.packet is not None else None
        if packet is None or time.time() - packet['dateTime'] > self.max_age:
            return
        converted = weewx.units.to_std_system(packet, event.packet['usUnits'])
        for name, value in converted.items():
            if name not in ('dateTime', 'usUnits'):
                event.packet.setdefault(name, value)

    def shutDown(self):
        self.stop.set()
        self.thread.join(timeout=1)
