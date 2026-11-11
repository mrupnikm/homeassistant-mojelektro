"""Tests for 15-minute cache validation."""

from datetime import datetime, timedelta

import pytest

from custom_components.mojelektro import moj_elektro_api
from custom_components.mojelektro.moj_elektro_api import MojElektroApi
from test_statistics import A_PLUS, day_readings

ET = "32.0.4.1.1.2.12.0.0.0.0.0.0.0.0.3.72.0"


def frozen_now(value):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return value

    return FrozenDatetime


def cache_for_day(day, value=0.25):
    readings = day_readings(day, value)
    total = value * len(readings)
    return {
        "15": [{"readingType": A_PLUS, "intervalReadings": readings}],
        "meter": [
            {
                "readingType": ET,
                "intervalReadings": [
                    {"timestamp": "previous", "value": "1000"},
                    {"timestamp": "latest", "value": str(1000 + total)},
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "day",
    [datetime(2026, 3, 29), datetime(2026, 10, 25), datetime(2026, 11, 10)],
)
def test_validate_dst_days(monkeypatch, day):
    # validateData runs the next morning and compares against yesterday.
    monkeypatch.setattr(
        moj_elektro_api, "datetime", frozen_now(day + timedelta(days=1, hours=7))
    )
    api = MojElektroApi("t", "m", 4, None)
    api.validateData(cache_for_day(day))
    assert api.cacheOK


def test_validate_ignores_readings_outside_yesterday(monkeypatch):
    day = datetime(2026, 11, 10)
    monkeypatch.setattr(
        moj_elektro_api, "datetime", frozen_now(day + timedelta(days=1, hours=7))
    )
    cache = cache_for_day(day)
    cache["15"][0]["intervalReadings"].append(
        {"timestamp": "2026-11-11T00:15:00+01:00", "value": "5"}
    )
    api = MojElektroApi("t", "m", 4, None)
    api.validateData(cache)
    assert api.cacheOK
