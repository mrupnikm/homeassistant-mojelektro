"""Import Moj Elektro 15-minute readings into long-term statistics.

Moj Elektro publishes readings with a delay of about one day. Writing them as
external statistics with their original timestamps lets the Energy dashboard
show each hour's usage on the hour it actually happened.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
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

from .const import DOMAIN, READING_TYPE_ARRAY

_LOGGER = logging.getLogger(__name__)

# Hours newer than (last stored hour - REIMPORT_WINDOW) are rewritten, so late
# corrections from Moj Elektro replace earlier values instead of being lost.
REIMPORT_WINDOW = timedelta(days=3)
# How far back to look for the row whose sum the re-imported window continues.
SUM_LOOKBACK = timedelta(days=7)
QUARTER = timedelta(minutes=15)
QUARTERS_PER_HOUR = 4

# (reading-type tag, statistic id suffix, display name)
STATISTICS = (
    ("A+", "consumption", "Moj Elektro consumption"),
    ("A-", "export", "Moj Elektro export"),
)


def statistic_id(meter_id: str, suffix: str) -> str:
    """Return the external statistic id for a meter, e.g. mojelektro:x_consumption."""
    return f"{DOMAIN}:{slugify(str(meter_id))}_{suffix}"


def readings_for_tag(cache_15, tag: str) -> list:
    """Return the interval readings for a reading-type tag such as A+."""
    reading_type = None
    for item in json.loads(READING_TYPE_ARRAY):
        if item.get("oznaka") == tag:
            reading_type = item.get("readingType")
            break

    for block in cache_15 or []:
        if reading_type is not None and block.get("readingType") == reading_type:
            return block.get("intervalReadings") or []
    return []


def aggregate_hourly(readings) -> dict[datetime, float]:
    """Sum 15-minute readings into complete UTC hours.

    Timestamps mark the interval end, so the reading stamped 01:00 belongs to
    00:00-01:00. Hours without all four quarter-hour readings are dropped.
    """
    totals: dict[datetime, float] = defaultdict(float)
    quarters: dict[datetime, set[datetime]] = defaultdict(set)

    for reading in readings or []:
        try:
            interval_end = datetime.fromisoformat(
                reading["timestamp"].replace("Z", "+00:00")
            )
            value = float(reading["value"])
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if interval_end.tzinfo is None:
            continue

        interval_start = (interval_end - QUARTER).astimezone(timezone.utc)
        hour = interval_start.replace(minute=0, second=0, microsecond=0)
        if interval_start in quarters[hour]:
            continue
        quarters[hour].add(interval_start)
        totals[hour] += value

    return {
        hour: totals[hour]
        for hour in sorted(totals)
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


async def _async_resume_point(
    hass: HomeAssistant, stat_id: str, hourly: dict[datetime, float]
) -> tuple[dict[datetime, float], float]:
    """Return the hours to (re)write and the sum the first of them continues."""
    recorder = get_instance(hass)
    last = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    if not last.get(stat_id):
        return hourly, 0.0

    last_row = last[stat_id][0]
    last_start = _row_start(last_row["start"])
    last_sum = last_row.get("sum") or 0.0

    cutoff = last_start - REIMPORT_WINDOW
    hourly = {hour: value for hour, value in hourly.items() if hour >= cutoff}
    if not hourly:
        return {}, last_sum

    first_hour = min(hourly)
    if first_hour > last_start:
        return hourly, last_sum

    previous = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        first_hour - SUM_LOOKBACK,
        first_hour,
        {stat_id},
        "hour",
        None,
        {"sum"},
    )
    previous_rows = previous.get(stat_id) or []
    if previous_rows:
        return hourly, previous_rows[-1].get("sum") or 0.0

    # No row before the window to continue from: only append new hours so
    # already stored hours are never counted twice.
    return (
        {hour: value for hour, value in hourly.items() if hour > last_start},
        last_sum,
    )


async def async_import(hass: HomeAssistant, meter_id: str, cache_15) -> None:
    """Import hourly consumption and export statistics from 15-minute data."""
    for tag, suffix, name in STATISTICS:
        hourly = aggregate_hourly(readings_for_tag(cache_15, tag))
        if not hourly:
            _LOGGER.debug("No complete hours to import for %s", tag)
            continue

        stat_id = statistic_id(meter_id, suffix)
        hourly, base_sum = await _async_resume_point(hass, stat_id, hourly)
        if not hourly:
            _LOGGER.debug("Statistics for %s are already up to date", stat_id)
            continue

        metadata = StatisticMetaData(
            source=DOMAIN,
            statistic_id=stat_id,
            name=name,
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
