"""Import Moj Elektro 15-minute readings into long-term statistics.

Moj Elektro publishes readings with a delay of about one day. Writing them as
external statistics with their original timestamps lets the Energy dashboard
show each hour's usage on the hour it actually happened.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import partial
import json
import logging

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import BACKFILL_DAYS, DOMAIN, READING_TYPE_ARRAY
from .tariff import is_high_energy_tariff, network_tariff_block

_LOGGER = logging.getLogger(__name__)

# Hours newer than (last stored hour - REIMPORT_WINDOW) are rewritten, so a
# repeated import of the same data never double counts.
REIMPORT_WINDOW = timedelta(days=3)
# How far back to look for the row whose sum the re-imported window continues.
SUM_LOOKBACK = timedelta(days=7)
# How far back a full (backfill) import looks for an earlier row.
FULL_LOOKBACK = timedelta(days=3650)
QUARTER = timedelta(minutes=15)
QUARTERS_PER_HOUR = 4


@dataclass(frozen=True)
class StatisticDefinition:
    """One external statistic derived from a reading type."""

    tag: str
    suffix: str
    name: str
    # Selects which quarter-hours count, by interval-end timestamp.
    # None counts every quarter-hour.
    include: Callable[[str], bool] | None = None


def _in_block(block: int, timestamp: str) -> bool:
    return network_tariff_block(timestamp) == block


def _is_low_energy_tariff(timestamp: str) -> bool:
    return not is_high_energy_tariff(timestamp)


STATISTICS = (
    StatisticDefinition("A+", "consumption", "Moj Elektro consumption"),
    StatisticDefinition("A-", "export", "Moj Elektro export"),
    *(
        StatisticDefinition(
            "A+",
            f"blok_{block}",
            f"Moj Elektro consumption blok {block}",
            partial(_in_block, block),
        )
        for block in range(1, 6)
    ),
    StatisticDefinition(
        "A+", "vt", "Moj Elektro consumption VT", is_high_energy_tariff
    ),
    StatisticDefinition(
        "A+", "mt", "Moj Elektro consumption MT", _is_low_energy_tariff
    ),
)


def statistic_id(meter_id: str, suffix: str) -> str:
    """Return the external statistic id for a meter, e.g. mojelektro:x_consumption."""
    return f"{DOMAIN}:{slugify(str(meter_id))}_{suffix}"


def readings_for_tag(cache_15, tag: str) -> list:
    """Return the interval readings for a reading-type tag such as A+.

    Readings from several blocks of the same type (for example from a
    backfill fetched in chunks) are concatenated.
    """
    reading_type = None
    for item in json.loads(READING_TYPE_ARRAY):
        if item.get("oznaka") == tag:
            reading_type = item.get("readingType")
            break

    readings = []
    for block in cache_15 or []:
        if reading_type is not None and block.get("readingType") == reading_type:
            readings.extend(block.get("intervalReadings") or [])
    return readings


def aggregate_hourly(
    readings, include: Callable[[str], bool] | None = None
) -> dict[datetime, float]:
    """Sum 15-minute readings into complete UTC hours.

    Timestamps mark the interval end, so the reading stamped 01:00 belongs to
    00:00-01:00. Hours without all four quarter-hour readings are dropped.
    With include, only matching quarter-hours are summed; complete hours with
    no matching quarter-hours are kept with 0 so the series stays contiguous.
    """
    totals: dict[datetime, float] = defaultdict(float)
    quarters: dict[datetime, set[datetime]] = defaultdict(set)

    for reading in readings or []:
        try:
            timestamp = reading["timestamp"]
            interval_end = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            value = float(reading["value"])
            counted = include is None or include(timestamp)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if interval_end.tzinfo is None:
            continue

        interval_start = (interval_end - QUARTER).astimezone(timezone.utc)
        hour = interval_start.replace(minute=0, second=0, microsecond=0)
        if interval_start in quarters[hour]:
            continue
        quarters[hour].add(interval_start)
        if counted:
            totals[hour] += value

    return {
        hour: totals[hour]
        for hour in sorted(quarters)
        if len(quarters[hour]) == QUARTERS_PER_HOUR
    }


def build_statistics(
    hourly: dict[datetime, float], base_sum: float
) -> list[StatisticData]:
    """Build hourly statistic rows whose running sum continues from base_sum."""
    rows: list[StatisticData] = []
    running_sum = base_sum
    for hour in sorted(hourly):
        running_sum += hourly[hour]
        rows.append(
            StatisticData(
                start=hour,
                state=round(hourly[hour], 6),
                sum=round(running_sum, 6),
            )
        )
    return rows


def _row_start(value) -> datetime:
    """Return a recorder row start (timestamp or datetime) as a UTC datetime."""
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromtimestamp(value, timezone.utc)


async def _async_last_row(hass: HomeAssistant, stat_id: str):
    """Return the newest stored row of a statistic, or None."""
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    rows = last.get(stat_id)
    return rows[0] if rows else None


async def _async_resume_point(
    hass: HomeAssistant,
    stat_id: str,
    hourly: dict[datetime, float],
    full: bool,
) -> tuple[dict[datetime, float], float]:
    """Return the hours to (re)write and the sum the first of them continues.

    A normal import only rewrites the last REIMPORT_WINDOW. A full import
    rewrites everything it has data for, which lets a backfill insert hours
    before the existing rows; it requires data up to the newest stored hour
    so the sums of later rows stay consistent.
    """
    last_row = await _async_last_row(hass, stat_id)
    if last_row is None:
        return hourly, 0.0

    last_start = _row_start(last_row["start"])
    last_sum = last_row.get("sum") or 0.0

    if full:
        if max(hourly) < last_start:
            _LOGGER.debug(
                "Backfill for %s ends before the newest stored hour; skipping",
                stat_id,
            )
            return {}, last_sum
    else:
        cutoff = last_start - REIMPORT_WINDOW
        hourly = {hour: value for hour, value in hourly.items() if hour >= cutoff}
        if not hourly:
            return {}, last_sum

    first_hour = min(hourly)
    if first_hour > last_start:
        return hourly, last_sum

    lookback = FULL_LOOKBACK if full else SUM_LOOKBACK
    previous = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        first_hour - lookback,
        first_hour,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    previous_rows = previous.get(stat_id) or []
    if previous_rows:
        return hourly, previous_rows[-1].get("sum") or 0.0
    if full:
        # Nothing stored before the backfill: it becomes the start of the series.
        return hourly, 0.0

    # No row before the window to continue from: only append new hours so
    # already stored hours are never counted twice.
    return (
        {hour: value for hour, value in hourly.items() if hour > last_start},
        last_sum,
    )


async def async_needs_backfill(hass: HomeAssistant, meter_id: str) -> bool:
    """Return whether any consumption statistic has no rows yet."""
    for definition in STATISTICS:
        if definition.tag != "A+":
            continue
        stat_id = statistic_id(meter_id, definition.suffix)
        if await _async_last_row(hass, stat_id) is None:
            return True
    return False


async def async_rebuild(hass: HomeAssistant, api, end: date, days: int) -> None:
    """Fetch the given number of days before end and rewrite their statistics."""
    start = end - timedelta(days=days)
    _LOGGER.debug("Rebuilding statistics from %s to %s", start, end)
    readings = await api.getMeterReadingsRange(start, end)
    await async_import(hass, api.meter_id, readings, full=True)


async def async_backfill(hass: HomeAssistant, api, end: date) -> None:
    """Import the last BACKFILL_DAYS before end when statistics are missing."""
    if await async_needs_backfill(hass, api.meter_id):
        await async_rebuild(hass, api, end, BACKFILL_DAYS)


async def async_import(
    hass: HomeAssistant, meter_id: str, cache_15, full: bool = False
) -> None:
    """Import hourly statistics from 15-minute data.

    full=True rewrites every hour in the data (used for backfill); otherwise
    only the last REIMPORT_WINDOW is rewritten.
    """
    for definition in STATISTICS:
        hourly = aggregate_hourly(
            readings_for_tag(cache_15, definition.tag), definition.include
        )
        if not hourly:
            _LOGGER.debug("No complete hours to import for %s", definition.tag)
            continue

        stat_id = statistic_id(meter_id, definition.suffix)
        hourly, base_sum = await _async_resume_point(hass, stat_id, hourly, full)
        if not hourly:
            _LOGGER.debug("Statistics for %s are already up to date", stat_id)
            continue

        metadata = StatisticMetaData(
            source=DOMAIN,
            statistic_id=stat_id,
            name=definition.name,
            unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            unit_class="energy",
            has_sum=True,
            mean_type=StatisticMeanType.NONE,
        )
        rows = build_statistics(hourly, base_sum)
        async_add_external_statistics(hass, metadata, rows)
        _LOGGER.debug(
            "Imported %s hourly rows into %s (%s to %s)",
            len(rows),
            stat_id,
            rows[0]["start"],
            rows[-1]["start"],
        )

    # Imports are queued in the recorder. Wait until they are written, so a
    # following import (e.g. the daily one right after a backfill) reads the
    # new rows instead of starting its running sum again from zero.
    await get_instance(hass).async_block_till_done()
