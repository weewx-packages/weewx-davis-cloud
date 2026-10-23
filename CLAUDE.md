# weewx-davis-cloud

One WeeWX driver and optional health service for the WeatherLink cloud APIs.

## Status

Pre-release. Existing cloud packages remain available until device coverage and
configuration migration have been independently checked. Offline fixtures do
not establish that a physical station or a paid account works.

## Structure

* `bin/user/daviscloud/` contains the transport, decoders and WeeWX integration.
* `tests/` exercises API boundaries, observation semantics and installation.
* `docs/` contains configuration and consolidation requirements.
* `research/` holds local primary sources and is excluded from distribution.

## Conventions

Follow WeeWX's Python style, with Google docstrings and no runtime annotations.
Keep the API transport separate from station-specific decoding. Do not silently
choose among conflicting sensors, fill historical gaps with current values or
write configuration and archive databases from the installer. Published assets
must remain immutable. Credentials must never appear in diagnostics or logs.
README follows the weewx-packages schema and names no versions or archive URLs;
installation links to the catalog page.

## Commands

* `just fast`: lint, type check and 99 tests, about one second locally.
* `just test`: the same test suite with coverage reporting.
* `just oracles`: fetch hash-pinned decoder sources and compare their results.
* `python tools/build.py`: create the reproducible extension archive under dist.

The oracle probe needs requests and six. Runtime code uses only WeeWX and the
standard library. The workflow checks Python 3.10/3.14 against official WeeWX
master/development on pushes, pull requests and Thursdays.
