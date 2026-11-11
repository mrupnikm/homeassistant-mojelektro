"""Tests for importing 15-minute readings into long-term statistics."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from custom_components.mojelektro import statistics
from custom_components.mojelektro.moj_elektro_api import MojElektroApi
from custom_components.mojelektro.statistics import (
    aggregate_hourly,
    build_statistics,
    statistic_id,
)

LJUBLJANA = ZoneInfo("Europe/Ljubljana")
UTC = timezone.utc
METER = "3-12345678"
A_PLUS = "32.0.2.4.1.2.12.0.0.0.0.0.0.0.0.3.72.0"
A_MINUS = "32.0.2.4.19.2.12.0.0.0.0.0.0.0.0.3.72.0"


def day_readings(day, value=0.25):
    """Return one local day of interval-end readings, like the Moj Elektro API."""
    start = datetime(day.year, day.month, day.day, tzinfo=LJUBLJANA)
    end = (start + timedelta(days=1)).replace(tzinfo=LJUBLJANA)
    readings = []
    current = start.astimezone(UTC)
    while current < end.astimezone(UTC):
        current += timedelta(minutes=15)
        readings.append(
            {
                "timestamp": current.astimezone(LJUBLJANA).isoformat(),
                "value": str(value),
            }
        )
    return readings


def cache_for(*days, value=0.25):
    readings = [r for day in days for r in day_readings(day, value)]
    return [
        {"readingType": A_PLUS, "intervalReadings": readings},
        {"readingType": A_MINUS, "intervalReadings": []},
    ]


def test_interval_end_shift():
    readings = [
        {"timestamp": "2026-11-10T00:15:00+01:00", "value": "1"},
        {"timestamp": "2026-11-10T00:30:00+01:00", "value": "2"},
        {"timestamp": "2026-11-10T00:45:00+01:00", "value": "3"},
        {"timestamp": "2026-11-10T01:00:00+01:00", "value": "4"},
    ]
    # Local 00:00-01:00 is 23:00 UTC on the previous day.
    assert aggregate_hourly(readings) == {
        datetime(2026, 11, 9, 23, tzinfo=UTC): 10.0
    }


def test_incomplete_hour_is_skipped():
    readings = day_readings(datetime(2026, 11, 10))
    # Drop the 00:30 reading: the first local hour has only three quarters.
    del readings[1]
    hourly = aggregate_hourly(readings)
    assert len(hourly) == 23
    assert datetime(2026, 11, 9, 23, tzinfo=UTC) not in hourly


def test_duplicate_and_malformed_readings_are_ignored():
    readings = day_readings(datetime(2026, 11, 10))
    readings.append(dict(readings[0]))
    readings.append({"timestamp": "garbage", "value": "1"})
    readings.append({"timestamp": "2026-11-10T05:00:00+01:00", "value": None})
    hourly = aggregate_hourly(readings)
    assert len(hourly) == 24
    assert all(value == 1.0 for value in hourly.values())


@pytest.mark.parametrize(
    ("day", "hours"),
    [
        (datetime(2026, 3, 29), 23),
        (datetime(2026, 10, 25), 25),
        (datetime(2026, 11, 10), 24),
    ],
)
def test_dst_days(day, hours):
    readings = day_readings(day)
    assert len(readings) == hours * 4
    hourly = aggregate_hourly(readings)
    assert len(hourly) == hours
    assert sum(hourly.values()) == pytest.approx(hours)


def test_statistic_id_is_valid():
    assert statistic_id(METER, "consumption") == "mojelektro:3_12345678_consumption"


def test_build_statistics_running_sum():
    hourly = {
        datetime(2026, 11, 10, 1, tzinfo=UTC): 2.0,
        datetime(2026, 11, 10, 0, tzinfo=UTC): 1.0,
    }
    rows = build_statistics(hourly, 10.0)
    assert [row["start"].hour for row in rows] == [0, 1]
    assert [row["state"] for row in rows] == [1.0, 2.0]
    assert [row["sum"] for row in rows] == [11.0, 13.0]


class FakeRecorder:
    """In-memory stand-in for the recorder statistics API.

    Like the real recorder, imports are queued and only become visible to
    queries once the queue has been processed (async_block_till_done).
    """

    def __init__(self):
        self.rows = {}
        self.metadata = {}
        self.pending = []

    async def async_block_till_done(self):
        pending, self.pending = self.pending, []
        for metadata, rows in pending:
            self._write(metadata, rows)

    async def async_add_executor_job(self, func, *args):
        return func(*args)

    def get_last_statistics(self, hass, number, stat_id, convert, types):
        rows = sorted(self.rows.get(stat_id, {}).values(), key=lambda r: r["start"])
        return {stat_id: rows[-number:]} if rows else {}

    def statistics_during_period(
        self, hass, start, end, stat_ids, period, units, types
    ):
        result = {}
        for stat_id in stat_ids:
            rows = [
                row
                for row in sorted(
                    self.rows.get(stat_id, {}).values(), key=lambda r: r["start"]
                )
                if start.timestamp() <= row["start"] < end.timestamp()
            ]
            if rows:
                result[stat_id] = rows
        return result

    def async_add_external_statistics(self, hass, metadata, rows):
        self.pending.append((metadata, list(rows)))

    def _write(self, metadata, rows):
        stat_id = metadata["statistic_id"]
        self.metadata[stat_id] = metadata
        stored = self.rows.setdefault(stat_id, {})
        for row in rows:
            ts = row["start"].timestamp()
            stored[ts] = {"start": ts, "state": row["state"], "sum": row["sum"]}

    def sums(self, stat_id):
        return [row["sum"] for _, row in sorted(self.rows[stat_id].items())]


@pytest.fixture
def recorder(monkeypatch):
    fake = FakeRecorder()
    monkeypatch.setattr(statistics, "get_instance", lambda hass: fake)
    monkeypatch.setattr(statistics, "get_last_statistics", fake.get_last_statistics)
    monkeypatch.setattr(
        statistics, "statistics_during_period", fake.statistics_during_period
    )
    monkeypatch.setattr(
        statistics,
        "async_add_external_statistics",
        fake.async_add_external_statistics,
    )
    return fake


CONSUMPTION = "mojelektro:3_12345678_consumption"


def run_import(cache):
    asyncio.run(statistics.async_import(None, METER, cache))


def test_first_import_writes_metadata_and_rows(recorder):
    run_import(cache_for(datetime(2026, 11, 9)))
    assert recorder.sums(CONSUMPTION)[-1] == pytest.approx(24.0)
    assert len(recorder.rows[CONSUMPTION]) == 24
    meta = recorder.metadata[CONSUMPTION]
    assert meta["source"] == "mojelektro"
    assert meta["has_sum"] is True
    assert meta["unit_of_measurement"] == "kWh"
    # No complete export hours, so no export statistic is created.
    assert "mojelektro:3_12345678_export" not in recorder.rows


def test_sum_continues_across_days(recorder):
    run_import(cache_for(datetime(2026, 11, 8)))
    run_import(cache_for(datetime(2026, 11, 9)))
    sums = recorder.sums(CONSUMPTION)
    assert len(sums) == 48
    assert sums[-1] == pytest.approx(48.0)
    assert sums == sorted(sums)


def test_reimporting_same_day_does_not_double_count(recorder):
    cache = cache_for(datetime(2026, 11, 9))
    run_import(cache)
    run_import(cache)
    assert len(recorder.rows[CONSUMPTION]) == 24
    assert recorder.sums(CONSUMPTION)[-1] == pytest.approx(24.0)


def test_overlapping_import_replaces_corrected_values(recorder):
    run_import(cache_for(datetime(2026, 11, 8)))
    run_import(cache_for(datetime(2026, 11, 9)))
    # Moj Elektro later corrects 9 Nov and also delivers 10 Nov.
    run_import(
        cache_for(datetime(2026, 11, 9), datetime(2026, 11, 10), value=0.5)
    )
    sums = recorder.sums(CONSUMPTION)
    assert len(sums) == 72
    assert sums[23] == pytest.approx(24.0)
    assert sums[47] == pytest.approx(24.0 + 48.0)
    assert sums[-1] == pytest.approx(24.0 + 96.0)


def test_hours_older_than_reimport_window_are_not_rewritten(recorder):
    run_import(cache_for(*(datetime(2026, 11, d) for d in range(1, 10))))
    before = dict(recorder.rows[CONSUMPTION])
    run_import(cache_for(datetime(2026, 11, 2), value=5))
    assert recorder.rows[CONSUMPTION] == before


def stat(suffix):
    return f"mojelektro:3_12345678_{suffix}"


def states(recorder, suffix):
    return [row["state"] for _, row in sorted(recorder.rows[stat(suffix)].items())]


def test_tariff_statistics_partition_the_total(recorder):
    # Tuesday 10 November 2026: high season workday.
    run_import(cache_for(datetime(2026, 11, 10)))
    total = states(recorder, "consumption")
    blocks = [states(recorder, f"blok_{block}") for block in range(1, 6)]
    vt = states(recorder, "vt")
    mt = states(recorder, "mt")

    assert all(len(series) == 24 for series in [*blocks, vt, mt])
    for hour, value in enumerate(total):
        assert sum(series[hour] for series in blocks) == pytest.approx(value)
        assert vt[hour] + mt[hour] == pytest.approx(value)

    # Local hours: blok 3 00-06 and 22-24, blok 1 06-12 and 17-20.
    assert sum(blocks[2]) == pytest.approx(8.0)
    assert sum(blocks[0]) == pytest.approx(9.0)
    assert sum(blocks[1]) == pytest.approx(7.0)
    assert sum(blocks[3]) == sum(blocks[4]) == 0
    assert sum(vt) == pytest.approx(16.0)
    assert sum(mt) == pytest.approx(8.0)


def test_tariff_statistics_on_dst_day(recorder):
    # Sunday 25 October 2026: low season day off, 25 hours.
    run_import(cache_for(datetime(2026, 10, 25)))
    assert len(states(recorder, "blok_4")) == 25
    assert sum(states(recorder, "blok_4")) + sum(
        states(recorder, "blok_5")
    ) == pytest.approx(25.0)
    assert sum(states(recorder, "vt")) == 0
    assert sum(states(recorder, "mt")) == pytest.approx(25.0)


def run_full_import(cache):
    asyncio.run(statistics.async_import(None, METER, cache, full=True))


def test_full_import_inserts_history_before_existing_rows(recorder):
    run_import(cache_for(datetime(2026, 11, 10)))
    run_full_import(
        cache_for(datetime(2026, 11, 8), datetime(2026, 11, 9), datetime(2026, 11, 10))
    )
    sums = recorder.sums(CONSUMPTION)
    assert len(sums) == 72
    assert sums[0] == pytest.approx(1.0)
    assert sums[-1] == pytest.approx(72.0)
    rows = sorted(recorder.rows[CONSUMPTION].values(), key=lambda r: r["start"])
    for prev, cur in zip(rows, rows[1:]):
        assert cur["sum"] - prev["sum"] == pytest.approx(cur["state"])


def test_full_import_skips_data_ending_before_stored_rows(recorder):
    run_import(cache_for(datetime(2026, 11, 10)))
    before = dict(recorder.rows[CONSUMPTION])
    run_full_import(cache_for(datetime(2026, 11, 8)))
    assert recorder.rows[CONSUMPTION] == before


def test_full_import_continues_from_older_rows(recorder):
    run_import(cache_for(datetime(2026, 10, 1)))
    run_import(cache_for(datetime(2026, 11, 9)))
    run_full_import(cache_for(datetime(2026, 11, 8), datetime(2026, 11, 9)))
    sums = recorder.sums(CONSUMPTION)
    assert sums == sorted(sums)
    assert sums[-1] == pytest.approx(24.0 * 3)


class FakeApi:
    meter_id = METER

    def __init__(self, days):
        self.days = days
        self.ranges = []

    async def getMeterReadingsRange(self, start, end):
        self.ranges.append((start, end))
        return cache_for(*self.days)


def test_backfill_runs_only_when_statistics_are_missing(recorder):
    api = FakeApi([datetime(2026, 11, 9), datetime(2026, 11, 10)])
    asyncio.run(statistics.async_backfill(None, api, date(2026, 11, 11)))
    assert api.ranges == [(date(2026, 10, 12), date(2026, 11, 11))]
    assert recorder.sums(CONSUMPTION)[-1] == pytest.approx(48.0)
    assert len(states(recorder, "blok_1")) == 48

    asyncio.run(statistics.async_backfill(None, api, date(2026, 11, 12)))
    assert len(api.ranges) == 1


def test_backfill_adds_tariff_statistics_to_existing_install(recorder):
    # Only the total exists, as after upgrading from the first 0.3.0 build.
    hourly = aggregate_hourly(
        statistics.readings_for_tag(cache_for(datetime(2026, 11, 10)), "A+")
    )
    recorder._write(
        {"statistic_id": CONSUMPTION}, build_statistics(hourly, 0.0)
    )
    api = FakeApi([datetime(2026, 11, 9), datetime(2026, 11, 10)])
    asyncio.run(statistics.async_backfill(None, api, date(2026, 11, 11)))
    assert recorder.sums(CONSUMPTION)[-1] == pytest.approx(48.0)
    assert len(recorder.rows[CONSUMPTION]) == 48
    assert len(states(recorder, "vt")) == 48


class FakeSessionApi(MojElektroApi):
    def __init__(self):
        super().__init__("t", METER, 4, None)
        self.requests = []

    async def getMeterReadings(self, rType=None, date_from=None, date_to=None):
        self.requests.append((rType, date_from, date_to))
        # Every chunk returns 1 November, so the merge sees duplicates.
        return cache_for(datetime(2026, 11, 1))


def test_meter_readings_range_is_chunked_and_merged():
    api = FakeSessionApi()
    blocks = asyncio.run(
        api.getMeterReadingsRange(date(2026, 10, 1), date(2026, 11, 11))
    )
    assert api.requests == [
        ("15min", "2026-10-01", "2026-11-01"),
        ("15min", "2026-11-01", "2026-11-11"),
    ]
    a_plus = statistics.readings_for_tag(blocks, "A+")
    assert len(a_plus) == 2 * 96
    # Duplicate readings across chunks are counted once.
    assert len(aggregate_hourly(a_plus)) == 24


def test_define_request_with_explicit_dates():
    api = MojElektroApi("t", METER, 4, None)
    url = api.define_request("15min", "2026-10-01", "2026-11-01")
    assert "startTime=2026-10-01&endTime=2026-11-01" in url
    assert "ReadingType%3D32.0.2.4.1.2.12" in url


def drops(recorder, suffix):
    rows = sorted(recorder.rows[stat(suffix)].values(), key=lambda r: r["start"])
    return [cur for prev, cur in zip(rows, rows[1:]) if cur["sum"] < prev["sum"]]


def test_daily_import_right_after_backfill_continues_sums(recorder):
    # Upgrade from the first 0.3.0 build: only the total exists. The backfill
    # runs and the daily import follows before the recorder has written it.
    hourly = aggregate_hourly(
        statistics.readings_for_tag(cache_for(datetime(2026, 11, 10)), "A+")
    )
    recorder._write({"statistic_id": CONSUMPTION}, build_statistics(hourly, 0.0))
    api = FakeApi([datetime(2026, 11, 9), datetime(2026, 11, 10)])
    asyncio.run(statistics.async_backfill(None, api, date(2026, 11, 11)))
    run_import(cache_for(datetime(2026, 11, 10)))

    for suffix in ["consumption", "mt", "vt", "blok_3"]:
        assert not drops(recorder, suffix)
    assert recorder.sums(stat("mt"))[-1] + recorder.sums(stat("vt"))[-1] == (
        pytest.approx(48.0)
    )


def test_rebuild_repairs_a_restarted_sum(recorder):
    api = FakeApi([datetime(2026, 11, 9), datetime(2026, 11, 10)])
    asyncio.run(statistics.async_rebuild(None, api, date(2026, 11, 11), 30))
    # Corrupt 10 Nov as the race did: its running sum restarts from zero.
    hourly = aggregate_hourly(
        statistics.readings_for_tag(cache_for(datetime(2026, 11, 10)), "A+"),
        statistics._is_low_energy_tariff,
    )
    recorder._write({"statistic_id": stat("mt")}, build_statistics(hourly, 0.0))
    assert drops(recorder, "mt")

    asyncio.run(statistics.async_rebuild(None, api, date(2026, 11, 11), 30))
    assert not drops(recorder, "mt")
    assert recorder.sums(stat("mt"))[-1] == pytest.approx(16.0)
