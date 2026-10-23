# Davis cloud for WeeWX

One driver for WeatherLink cloud observations and archives, with an optional
health service for stations that collect their weather locally.

**Pre-release 0.1.0a1.** Do not replace a working station without checking its
sensor mapping and archive configuration. Existing Davis cloud packages remain
available while migration and live device checks are incomplete.

The v2 decoder covers WeatherLinkIP/Vantage Connect, WeatherLink Live, Console
6313 and AirLink records. EnviroMonitor ISS records use the same observations;
individual EnviroMonitor nodes need explicit field and unit declarations. A v1
adapter reads current conditions. The v1 adapter has no historical download.

This is a cloud driver. It requires Internet access and WeatherLink API
credentials. Local USB, TCP, WLL UDP and SDR acquisition remain separate drivers.

## Install and select the station

Requires WeeWX 5.5 or later, below 6, and Python >=3.10,<3.15. The runtime has no
additional Python dependencies.

Install the published pre-release archive with the `weectl` belonging to your
WeeWX environment:

```sh
weectl extension install https://github.com/weewx-packages/weewx-davis-cloud/releases/download/v0.1.0a1/davis-cloud-0.1.0a1.tar.gz --config=/path/to/weewx.conf
```

Replace `/path/to/weewx.conf` with your station configuration. Stock WeeWX 5
accepts this URL or a downloaded archive; it needs no catalog integration. To
build from source, run `python tools/build.py` and install the generated
`dist/davis-cloud-0.1.0a1.tar.gz` instead.

Installation adds files only. It leaves the active driver, database binding and
schema unchanged. Add this section to `weewx.conf` and enter your own API key,
secret and station ID. Protect the file as you would other account credentials.

```ini
[DavisCloud]
    driver = user.daviscloud.driver
    api_version = 2
    api_key = YOUR_API_KEY
    api_secret = YOUR_API_SECRET
    station_id = YOUR_STATION_ID
    poll_interval = 60
    max_age = 1800
    archive = false
    archive_interval = 300
```

Use the station ID, not a WeatherLinkIP device ID. To list accessible stations:

```sh
PYTHONPATH=/path/to/station/bin python -m user.daviscloud \
    --config=/path/to/weewx.conf --stations
```

Run the same command with `--sensors` to inspect LSIDs and available fields, or
`--current` to check the decoded observations. Set `[Station] station_type =
DavisCloud` only after these checks succeed.

For current-only operation, set `[StdArchive] record_generation = software` and
choose the desired archive interval there. Polling more often does not make the
cloud observations update faster.

## Enable cloud archives

Historic access depends on the station's WeatherLink permissions. Set
`[DavisCloud] archive = true`, set `archive_interval` to the station's actual
archive interval in seconds, and set `[StdArchive] record_generation = hardware`.
WeeWX then requests cloud archives at startup and during normal operation.

With an empty database, `max_backfill_days` limits the initial import to seven
days by default. With an existing database, catch-up starts at its last committed
timestamp, including outages longer than that setting. Failed windows remain
failed; the driver never advances a persistent cursor past them.

Use `--history START END` to inspect Unix-time bounds without writing a database.
The returned range is `(START, END]`. The driver splits requests into windows of
at most 24 hours. Individual archive rows retain their reported intervals.

## Map multiple sensors

The driver refuses conflicting automatic assignments. Select the logical sensor
ID for each conflicting observation. Selectors use normalized WeeWX names, so
the same selection works for current and historical data.

```ini
    [[sensor_map]]
        outTemp = 101.outTemp
        outHumidity = 101.outHumidity
        extraTemp1 = 202.outTemp
        extraHumid1 = 202.outHumidity
        windSpeed = 303.windSpeed
        windDir = 303.windDir
        windGust = 303.windGust
        rain = 404.rain
```

Replace the example LSIDs with those reported by `--sensors`. Map each conflicting
field, including cumulative rain totals when several collectors report them.
The driver does not choose a different sensor when the selected sensor stops
reporting. See [configuration](docs/configuration.md) for custom node measurements
and [consolidation](docs/consolidation.md) before replacing an existing driver.

## Add cloud health to a local station

Keep the local station driver. Configure the same `[DavisCloud]` credentials,
then append `user.daviscloud.service.HealthService` to
`[Engine][[Services]] data_services`. Do not also enable the old cloud health
service. Health acquisition runs in a background thread and preserves readings
already present in the local LOOP packet.

Health fields are available to LOOP consumers. Storing additional health or
custom fields requires corresponding archive columns. This package does not
alter an existing database or install a skin.

## Development

Create `.venv`, install `weewx`, `pytest`, `pytest-cov`, `mypy` and `ruff`, then run
`just fast`. `just test` includes coverage reporting. Tests use independent
fixtures, install the archive into a disposable station, and exercise the real
WeeWX engine and SQLite archive. They never require a live account.

`just oracles` additionally downloads five existing decoder implementations at
the revisions and SHA-256 hashes in [the oracle manifest](docs/oracles.json).
Install `requests` and `six` for those older implementations. Each runs in a
separate process with its network boundaries disabled. Required measurements
are compared explicitly; the test does not accept an empty intersection of
fields. Sources remain in the ignored research directory and are not packaged.
The CI workflow repeats these comparisons against WeeWX master and development.

The implementation is GPL-3.0-or-later. It is written against the
[WeatherLink API contract](https://weatherlink.github.io/v2-api/), rather than
combining source files from projects with unresolved redistribution rights.

<!-- Documentation updated 2026-10-23 by weewx-packages. -->
