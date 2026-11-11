# Changelog

All notable changes to this maintained fork are documented here.

This project is a maintained fork of [frlequ/homeassistant-mojelektro](https://github.com/frlequ/homeassistant-mojelektro). Upstream authorship and the original MIT license are preserved.

## [0.3.1] - Unreleased

### Energy dashboard

- new hourly statistics per network tariff block, `mojelektro:<eimm>_blok_1` … `_blok_5`, and per energy tariff, `mojelektro:<eimm>_vt` and `_mt`; each set adds up to the total consumption every hour, so it can replace the total in the Energy dashboard with a separate price per block or tariff
- backfill: when a consumption statistic has no data yet, the last 30 days of 15-minute readings are fetched (in monthly chunks) and imported; existing rows are rewritten so the running sum stays continuous
- the 15-minute API request accepts an explicit date range

### Documentation

- explain the three Energy dashboard setups (total, blocks, VT/MT) and that they must not be combined
- corrected the note on re-imports: they prevent double counting, but only yesterday is fetched daily, so later corrections to older days are not picked up

## [0.3.0] - Unreleased

### Energy dashboard

- import validated Moj Elektro 15-minute readings into Home Assistant long-term statistics with their original timestamps, so the Energy dashboard shows each hour's usage on the hour it happened
- new external statistics `mojelektro:<eimm>_consumption` (A+) and `mojelektro:<eimm>_export` (A−), hourly resolution
- interval-end timestamps are shifted to the hour they belong to (the reading stamped 01:00 belongs to 00:00–01:00); incomplete hours are skipped and DST days produce 23/25 hours
- 15-minute cache validation now uses the readings of the previous day instead of a fixed 96 readings, so 23- and 25-hour DST days validate and import
- the last three days are rewritten on each import, so importing the same data again (for example after a restart) does not double count
- existing sensors are unchanged; history recorded by the old sensors stays on those sensors

### Compatibility

- the integration now depends on `recorder`
- minimum Home Assistant version raised to 2025.11 (statistics metadata `unit_class`)

## [0.2.7] - 2026-09-21

First maintained-fork release.

### Home Assistant compatibility

- migrate legacy suffixed sensor unique IDs (for example `_2`, `_3`) in the entity registry before using deterministic IDs, preserving existing entity IDs, history, dashboards and automations
- fixed the device-registry `model` value so it is a string and remains compatible with Home Assistant 2026.12+
- removed synchronous `manifest.json` reads from the Home Assistant event loop
- migrated sensor state handling to `SensorEntity.native_value`
- switched startup to `DataUpdateCoordinator.async_config_entry_first_refresh()`
- updated config-entry unloading to current Home Assistant helpers
- added `strings.json` for current config-flow validation
- refreshed CI for Python 3.14 and Home Assistant 2026.9.x

### Authentication and configuration

- added separate authentication, invalid-request and connection errors
- added Home Assistant reauthentication when an API token expires or is revoked
- added duplicate EIMM protection in the config flow
- migrate legacy config entries to a stable EIMM unique ID when safe
- made entity unique IDs deterministic while preserving the historic ID shape

### API and data robustness

- correct Slovenian network tariff-block hour boundaries and work-free-day handling against the current Energy Agency schedule
- preserve prior sensor values when Moj Elektro temporarily returns an empty successful payload instead of synthesizing zero values
- preserve `TOTAL_INCREASING` statistics from false zero/reset/rebound sequences
- fixed crashes caused by missing or unknown reading types
- fixed accidental list access using index `-1` when a required reading type is absent
- preserve the expected entity set when Moj Elektro temporarily returns partial data
- preserve previous values where appropriate during partial responses
- validate API payload shapes more defensively
- derive validation and tariff reading types from the integration's mapping rather than duplicating IDs
- removed unused `requests` dependency
- removed `dateutil` dependency and use Python's built-in datetime parser

### Contracted power

- included the upstream fix for contracted-power values becoming unavailable on the last day of their validity period

### Documentation and packaging

- changed project documentation to clearly identify this repository as a maintained fork
- updated the Moj Elektro token location to **API storitve**
- changed HACS instructions to use this repository as a custom integration
- updated repository URLs in the integration manifest
- set integration version to `0.2.7`

### Upstream work incorporated or adapted

- [upstream PR #59](https://github.com/frlequ/homeassistant-mojelektro/pull/59) — blocking `manifest.json` file read  
  Adapted and superseded by removing the runtime manifest read entirely.

- [upstream PR #60](https://github.com/frlequ/homeassistant-mojelektro/pull/60) — contracted-power validity on the final day  
  Incorporated into 0.2.7.

- [upstream PR #64](https://github.com/frlequ/homeassistant-mojelektro/pull/64) — new Moj Elektro portal wording  
  Incorporated into the documentation.

### Upstream issues addressed

- [#54](https://github.com/frlequ/homeassistant-mojelektro/issues/54) — blocking call
- [#62](https://github.com/frlequ/homeassistant-mojelektro/issues/62) — database executor / blocking-call warnings
- [#63](https://github.com/frlequ/homeassistant-mojelektro/issues/63) — integration slowdowns caused by blocking I/O
- [#65](https://github.com/frlequ/homeassistant-mojelektro/issues/65) — non-string device model

### Reviewed but intentionally deferred

- [upstream PR #57](https://github.com/frlequ/homeassistant-mojelektro/pull/57) — alternate 15-minute cache window  
  Needs validation against current API behaviour before inclusion.

- [upstream PR #58](https://github.com/frlequ/homeassistant-mojelektro/pull/58) — total meter readings  
  Planned for the 0.3.x API refresh.

- [upstream issue #66](https://github.com/frlequ/homeassistant-mojelektro/issues/66) — souporaba  
  Planned for 0.3.x using the official `GET /souporaba` endpoint.

## Planned: 0.3.x

The next feature series is intended to align the integration more closely with the current official Moj Elektro API.

Planned work includes:

- dynamic reading types from `GET /reading-type`
- reading quality handling from `GET /reading-qualities`
- souporaba support from `GET /souporaba`
- total meter-reading sensors
- improved long-term statistics and Energy Dashboard integration
- useful metering-point metadata from `GET /merilno-mesto/{identifikator}`
- improved contracted-power metadata from `GET /merilna-tocka/{gsrn}`
- automated API/config-flow tests
- Slovenian translations
