# Moj Elektro for Home Assistant

[![Version](https://img.shields.io/badge/version-0.3.2-blue.svg)](https://github.com/mrupnikm/homeassistant-mojelektro)
[![Python check](https://github.com/mrupnikm/homeassistant-mojelektro/actions/workflows/python_check.yml/badge.svg)](https://github.com/mrupnikm/homeassistant-mojelektro/actions/workflows/python_check.yml)
[![Release validation](https://github.com/mrupnikm/homeassistant-mojelektro/actions/workflows/release_validation.yml/badge.svg)](https://github.com/mrupnikm/homeassistant-mojelektro/actions/workflows/release_validation.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/mrupnikm/homeassistant-mojelektro)](https://github.com/mrupnikm/homeassistant-mojelektro/stargazers)

Home Assistant custom integration for electricity-meter data from the Slovenian **Moj Elektro** service using the official Informatika.si API.

> [!IMPORTANT]
> **This repository is a maintained fork of [frlequ/homeassistant-mojelektro](https://github.com/frlequ/homeassistant-mojelektro).**
>
> It continues from the maintained fork [jursko90/homeassistant-mojelektro](https://github.com/jursko90/homeassistant-mojelektro), which provided the 0.2.7 compatibility release.
>
> The original project, authorship and MIT license are preserved. This fork exists to keep the integration compatible with current Home Assistant releases, fix outstanding issues and continue development against the current Moj Elektro API.

## Current status

**Maintained fork:** `mrupnikm/homeassistant-mojelektro`  
**Maintainer:** [`mrupnikm`](https://github.com/mrupnikm)  
**Current version:** `0.3.2`  
**Home Assistant baseline:** `2025.11+`  
**API:** `https://api.informatika.si/mojelektro/v1`

Version 0.3.x adds hourly long-term statistics with correct historic timestamps for the Energy dashboard, including per-tariff-block and VT/MT statistics. Further API functionality is planned for the 0.3.x series.

## What the integration currently provides

- hourly long-term statistics for imported and exported energy, per network tariff block and per VT/MT energy tariff, placed on the hour the energy was actually used, with a 30-day backfill
- 15-minute imported and exported energy readings
- daily imported/exported energy
- monthly imported/exported energy
- peak/off-peak tariff readings where available
- daily consumption grouped by network tariff block
- contracted power for blocks 1-5
- Home Assistant UI config flow
- configurable decimal precision
- automatic token reauthentication flow
- stable entities across temporary partial API responses

Moj Elektro is **not a real-time source**. Meter readings are typically available with an approximately **24-hour delay**. Data can also be incomplete or temporarily inaccurate while the upstream service aggregates readings, especially during the overnight period (roughly midnight to 06:00). Dashboards and automations should therefore not treat the 15-minute sensors as near-real-time measurements.

## Energy dashboard

Sensors can only record a value at the moment Home Assistant reads it, so the `daily_input` sensor shows yesterday's whole consumption as a single spike at the time it was fetched. Since 0.3.0 the integration also writes Moj Elektro's 15-minute readings, summed per hour, directly into Home Assistant's long-term statistics with their original timestamps:

| Statistic | Statistic ID | Contains |
| --- | --- | --- |
| Moj Elektro consumption | `mojelektro:<eimm>_consumption` | all imported energy (A+) |
| Moj Elektro export | `mojelektro:<eimm>_export` | all exported energy (A−), only when the meter reports it |
| Moj Elektro consumption blok 1 … blok 5 | `mojelektro:<eimm>_blok_1` … `_blok_5` | imported energy per network tariff block |
| Moj Elektro consumption VT | `mojelektro:<eimm>_vt` | imported energy in the higher energy tariff (workdays 06:00–22:00) |
| Moj Elektro consumption MT | `mojelektro:<eimm>_mt` | imported energy in the lower energy tariff (nights, weekends, work-free holidays) |

`<eimm>` is your EIMM in lowercase with non-alphanumeric characters replaced by `_` (for example `3-12345678` → `3_12345678`). These are statistics, not sensors: in the Energy settings they appear in the same picker as sensors, under the names above.

The block and VT/MT statistics are different ways of splitting the same total. Every hour, blok 1–5 add up to **Moj Elektro consumption**, and so do VT + MT. Hours outside a block or tariff are stored as 0 kWh. Home Assistant adds all grid consumption sources together, so pick **one** of these setups under **Settings → Dashboards → Energy → Electricity grid → Grid consumption**:

| Setup | Add as grid consumption | Use when |
| --- | --- | --- |
| Total | Moj Elektro consumption | you want one consumption line and one price |
| Network tariff blocks | Moj Elektro consumption blok 1, 2, 3, 4 and 5 (five sources) | you want usage split and priced per tariff block |
| Energy tariffs | Moj Elektro consumption VT and MT (two sources) | you want usage split and priced per VT/MT |

Do not combine the setups, and remove `daily_input` and the other `sensor.moj_elektro_*` sensors from the Energy settings; otherwise energy is counted more than once. If you export energy, add **Moj Elektro export** under **Return to grid**.

Notes:

- data still arrives about 24 hours late, but it lands on the correct hour: yesterday is filled in the next morning and today stays empty
- resolution is hourly, which is the finest resolution of long-term statistics
- an hour is imported only once all four of its 15-minute readings are available
- the first time any consumption statistic is missing (new install or upgrade), the last 30 days are fetched from Moj Elektro and imported (backfill); it runs once per Home Assistant start and is retried after a restart if it fails
- after that, only yesterday is fetched each day; re-importing the same data never double counts, but corrections Moj Elektro makes to older days are only picked up by the `mojelektro.rebuild_statistics` action (below)
- history recorded by the old sensors stays on those sensors and is not moved to the new statistics
- all existing sensors are kept unchanged for backward compatibility
- you can check the imported rows under **Developer tools → Statistics**, or with the `recorder.get_statistics` action under **Developer tools → Actions**

### Rebuilding statistics

The **Moj Elektro: Rebuild statistics** action (`mojelektro.rebuild_statistics`) fetches the 15-minute readings of the last `days` days (default 30, up to 366) again for every configured meter and rewrites all Moj Elektro statistics for that period. Use it to pick up readings Moj Elektro corrected later, or to repair statistics. Run it under **Developer tools → Actions**:

```yaml
action: mojelektro.rebuild_statistics
data:
  days: 30
```

## Why this fork exists

The original integration remained useful, but several Home Assistant compatibility issues accumulated while upstream development slowed down.

The first maintained-fork release addresses, among other things:

- blocking file I/O inside the Home Assistant event loop
- Home Assistant 2026.12 device-registry compatibility
- modern `SensorEntity.native_value` usage
- safer `DataUpdateCoordinator` startup and update handling
- proper authentication, connection and API-request error handling
- reauthentication when an API token expires or is revoked
- duplicate EIMM/config-entry protection
- deterministic entity unique IDs
- safer handling of incomplete Moj Elektro API responses
- contracted-power validity on the final day of a validity period
- removal of unused runtime dependencies
- refreshed HACS and CI metadata

See [CHANGELOG.md](CHANGELOG.md) for release details.

## Upstream work reviewed

This fork does not claim the upstream work as new work. Relevant open upstream pull requests and issues were reviewed and either incorporated, adapted or intentionally deferred.

| Upstream item | Contributor | Status in this fork | Notes |
| --- | --- | --- | --- |
| [PR #59](https://github.com/frlequ/homeassistant-mojelektro/pull/59) | [`kosl`](https://github.com/kosl) | **Adapted / superseded** | Fix for blocking `manifest.json` reads. The fork removes the runtime file read entirely and uses the integration version directly. |
| [PR #60](https://github.com/frlequ/homeassistant-mojelektro/pull/60) | [`mikrohard`](https://github.com/mikrohard) | **Included** | Fixes contracted-power sensors on the last day of the validity period by comparing dates instead of full datetimes. |
| [PR #64](https://github.com/frlequ/homeassistant-mojelektro/pull/64) | [`MaJerle`](https://github.com/MaJerle) | **Included** | Documentation updated to the current Moj Elektro portal wording: `API storitve`. |
| [PR #57](https://github.com/frlequ/homeassistant-mojelektro/pull/57) | [`kosl`](https://github.com/kosl) | **Reviewed / deferred** | Changes the 15-minute-data window to avoid incomplete overnight data. This needs validation against the current API before inclusion. |
| [PR #58](https://github.com/frlequ/homeassistant-mojelektro/pull/58) | [`kosl`](https://github.com/kosl) | **Reviewed / planned** | Adds total meter readings. Planned as part of the 0.3.x API refresh rather than mixed into the stability release. |

### Upstream issues addressed in 0.2.7

- [#54 – blocking call](https://github.com/frlequ/homeassistant-mojelektro/issues/54)
- [#62 – blocking/database executor warnings](https://github.com/frlequ/homeassistant-mojelektro/issues/62)
- [#63 – integration slowdowns caused by blocking I/O](https://github.com/frlequ/homeassistant-mojelektro/issues/63)
- [#65 – non-string device model, breaking in HA 2026.12](https://github.com/frlequ/homeassistant-mojelektro/issues/65)

Upstream [issue #66](https://github.com/frlequ/homeassistant-mojelektro/issues/66) requesting **souporaba** data is part of the planned 0.3.x work.

## Installation

### HACS

This fork is installed as a **custom HACS repository**.

1. Open **HACS**.
2. Open the menu and choose **Custom repositories**.
3. Add:

   `https://github.com/mrupnikm/homeassistant-mojelektro`

4. Select category **Integration**.
5. Install **Moj Elektro**.
6. Restart Home Assistant.

If the original HACS-default integration is already installed, remove or replace it with this repository before testing the maintained fork.

### Manual installation

Copy:

`custom_components/mojelektro`

into your Home Assistant:

`/config/custom_components/mojelektro`

Then restart Home Assistant.

## Moj Elektro API setup

Official API documentation:

https://docs.informatika.si/mojelektro/api/

1. Sign in to **Moj Elektro**.
2. Open **API storitve**.
3. Create an API token with the desired expiration.
4. Copy the generated token.
5. Find the required **EIMM** identifier under your metering-point information.
6. In Home Assistant open **Settings → Devices & services → Add integration → Moj Elektro**.
7. Enter the token and EIMM.

No `configuration.yaml` entry is required.

## Roadmap

### 0.2.x — compatibility and stability

- [x] Home Assistant 2026 compatibility
- [x] remove event-loop blocking file reads
- [x] fix device-registry model type
- [x] modern sensor API
- [x] robust coordinator/error handling
- [x] token reauthentication
- [x] stable config-entry and entity IDs
- [x] contracted-power validity boundary fix
- [x] safer partial/malformed API-response handling
- [ ] add automated unit tests for API parsing and config flow
- [ ] validate CI/HACS workflows on the fork

### 0.3.x — Moj Elektro API refresh

- [ ] use official `/reading-type` instead of maintaining the full reading-type list manually
- [ ] use `/reading-qualities` to expose or reject low-quality readings correctly
- [ ] add `/souporaba` support
- [ ] add total meter-reading sensors
- [x] import 15-minute data into long-term statistics on the correct hour (0.3.0)
- [x] backfill statistics history on first setup (0.3.1)
- [x] per-tariff-block and VT/MT statistics (0.3.1)
- [ ] expose useful metering-point metadata from `/merilno-mesto/{identifikator}`
- [ ] improve contracted-power data from `/merilna-tocka/{gsrn}`
- [ ] review the 15-minute-data window against current API behaviour
- [ ] add Slovenian translations

### Later

- [ ] diagnostics download suitable for bug reports without exposing the API token
- [ ] broader automated test coverage against recorded/sanitized API payloads
- [ ] release automation and tagged HACS releases
- [ ] continue tracking upstream changes where useful

## Relationship to the original project

The original repository is:

**[frlequ/homeassistant-mojelektro](https://github.com/frlequ/homeassistant-mojelektro)**

This maintained fork builds on that work, and on the 0.2.7 release from [jursko90/homeassistant-mojelektro](https://github.com/jursko90/homeassistant-mojelektro), and remains licensed under the same **MIT License**. Original copyright and license notices are retained.

Where upstream pull requests or fixes are incorporated, they are documented above and in the changelog.

## Support the original author

The original integration was created and maintained by **frlequ**. If the original project has been useful to you, you can support its author here:

<a href="https://www.buymeacoffee.com/frlequ"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me A Coffee" height="50"></a>

## Issues and contributions

For problems specific to this maintained fork, use the fork issue tracker once GitHub Issues are enabled for the repository:

https://github.com/mrupnikm/homeassistant-mojelektro/issues

When reporting a problem, please include:

- Home Assistant version
- Moj Elektro integration version
- relevant Home Assistant log messages
- whether the problem also occurs after restarting Home Assistant

**Never post your Moj Elektro API token in an issue or log excerpt.**

Pull requests are welcome. Changes should remain focused, Home Assistant-compatible and avoid exposing credentials or personal metering data.

## License

MIT License. See [LICENSE](LICENSE).

This fork preserves the license and attribution of the original project.
