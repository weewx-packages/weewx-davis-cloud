# Configuration reference

All options below belong to `[DavisCloud]` unless a subsection is shown.

* `driver`: `user.daviscloud.driver`.
* `api_version`: `2` by default. `1` enables legacy current conditions only.
* `api_key`, `api_secret`: Required for v2.
* `station_id`: A positive numeric ID or UUID. If omitted, discovery requires
  exactly one accessible station.
* `device_id`, `password`, `api_token`: Legacy v1 credentials. The token is optional.
* `poll_interval`: Cloud polling period in seconds. Default 60, minimum 10.
* `max_age`: Maximum age of source observations in seconds. Default 1800.
  Samples more than 30 seconds in the future are excluded.
* `timeout`: HTTP request timeout in seconds. Default 15.
* `retries`: Additional attempts on connection errors, HTTP 429 and HTTP 5xx.
  Default 2, maximum 5. Authorization failures are not retried.
* `archive`: Enable v2 cloud hardware archives. Default false.
* `archive_interval`: Station archive interval in seconds. Default 300. Must be
  a positive whole number of minutes. Each historic record retains its own interval.
* `max_backfill_days`: Initial import span for an empty database. Default 7.
* `node_archive_interval`: Explicit interval in seconds for a node-only archive
  whose records have no `arch_int`. No inferred default.
* `health_interval`: Poll period for the optional health service. Default 900,
  minimum 60, maximum 86400 seconds.
* `demo`: Include the API's `demo=true` parameter. Default false. Valid v2
  credentials and explicit selection of the demo station are still required.

## Sensor selectors

`[[sensor_map]]` maps an output observation to `LSID.observation`. Both names
use WeeWX observations, not raw API field names. Their unit groups must match.
LSIDs remain the identity even if a transmitter's channel number changes.

Automatic mappings include outdoor and indoor weather, rain, solar radiation,
UV, additional temperature/humidity, leaf/soil observations, and AirLink PM1,
PM2.5 and PM10 mass concentrations. Each alias selects the appropriate statistic:
archive temperature averages, for example, take precedence over final values.

## Custom node observations

`[[observations]]` declares raw fields whose units cannot be inferred safely.
Each subsection names a WeeWX output observation and requires `sensor`, `field`,
`unit` and `group`.

```ini
    [[observations]]
        [[[extraTemp1]]]
            sensor = 555
            field = temp_last
            unit = degree_C
            group = group_temperature
```

The unit must be convertible by WeeWX. The group must match an existing
observation of that name, or be a known WeeWX group for a new name. Arbitrary
node sensor names do not establish a unit. For instance, soil moisture reported
as a percentage cannot be assigned to a centibar observation.

## Health observations

The normalized fields are `davisBatteryVoltage`, `davisInputVoltage`,
`davisBatteryPercent`, `davisBatteryTemp`, `davisUptime`, `davisAppUptime`,
`davisWifiRSSI` and `davisLinkUptime`. Voltages are converted from millivolts to
volts. Battery temperature is converted from Celsius to the packet's unit
system. Uptime values are seconds. Other numeric health fields can use explicit
custom observation rules. String versions and network addresses are not emitted
as numeric archive observations.

## Acquisition semantics

LOOP timestamps are local poll times, after checking each source record's age.
The health-only service preserves the oldest accepted source timestamp so a
cached packet cannot extend an old reading's lifetime.

Rain increments use one collector's cumulative year, month or day counter, in
that order. The first sample, a counter decrease or a change of counter identity
establishes a new baseline. That transition returns no estimated rain increment.
Repeated and out-of-order samples do not change the baseline. Rolling 15-minute
totals are never treated as interval increments. A restart establishes a fresh
baseline; cloud archives provide historical rain when enabled.

Archive records merge only at identical source timestamps and with compatible
intervals. Missing observations remain missing. The driver validates each whole
request window before returning its first record. Deprecated high/low-only
structures 5 and 8 are not archive observations.

Requests use verified HTTPS and reject redirects. The process shares a rolling
1000-request/hour budget per API credential, spaces attempts by at least 0.11
seconds and honors HTTP Retry-After. Separate WeeWX processes and other API
applications still share the server-side quota. Secrets are never included in
diagnostic output or error text.
