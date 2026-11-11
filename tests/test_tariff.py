"""Tests for the Slovenian network tariff profile."""

from datetime import date

import pytest

from custom_components.mojelektro.tariff import (
    is_high_energy_tariff,
    is_slovenian_day_off,
    network_tariff_block,
)


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # Higher season, workday (Monday 12 January 2026).
        ("2026-01-12T06:15:00+01:00", 1),
        ("2026-01-12T12:15:00+01:00", 2),
        ("2026-01-12T17:15:00+01:00", 1),
        ("2026-01-12T20:15:00+01:00", 2),
        ("2026-01-12T22:15:00+01:00", 3),
        # Higher season, day off.
        ("2026-01-11T06:15:00+01:00", 3),
        ("2026-01-11T12:15:00+01:00", 4),
        # Lower season, workday.
        ("2026-07-13T06:15:00+02:00", 3),
        ("2026-07-13T12:15:00+02:00", 4),
        ("2026-07-13T22:15:00+02:00", 5),
        # Lower season, day off.
        ("2026-07-12T06:15:00+02:00", 4),
        ("2026-07-12T12:15:00+02:00", 5),
    ],
)
def test_current_network_tariff_schedule(timestamp, expected):
    assert network_tariff_block(timestamp) == expected


def test_easter_monday_is_day_off():
    assert is_slovenian_day_off(date(2026, 4, 6))


def test_non_work_free_state_holiday_is_not_forced_day_off():
    assert not is_slovenian_day_off(date(2026, 6, 8))


@pytest.mark.parametrize(
    ("timestamp", "expected"),
    [
        # Workday (Tuesday 10 November 2026): VT 06-22.
        ("2026-11-10T06:00:00+01:00", False),
        ("2026-11-10T06:15:00+01:00", True),
        ("2026-11-10T22:00:00+01:00", True),
        ("2026-11-10T22:15:00+01:00", False),
        # Saturday and a work-free holiday are MT all day.
        ("2026-11-14T12:15:00+01:00", False),
        ("2026-12-25T12:15:00+01:00", False),
        # Summer time workday.
        ("2026-07-13T12:15:00+02:00", True),
    ],
)
def test_energy_tariff(timestamp, expected):
    assert is_high_energy_tariff(timestamp) is expected
