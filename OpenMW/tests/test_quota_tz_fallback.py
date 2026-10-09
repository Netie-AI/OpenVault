"""A machine with no tz database must not take quota math, and so chat routing, down.

Stock Windows ships no IANA database. ``quota.window_bounds`` runs on the chat
path (hop ordering and the proxy), so one missing zone used to turn every
request that touched a keyed provider, and ``GET /api/freeroute/status``, into
an HTTP 500. ``ZoneInfo`` is made to fail here, so these tests do not depend on
whether the machine running them has ``tzdata``.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from zoneinfo import ZoneInfoNotFoundError

import pytest
from structlog.testing import capture_logs

from openmw.openvault.vault import quota

# 2023-11-14 22:13:20 UTC. Its UTC midnight is 2023-11-14 00:00:00 UTC.
_NOW = 1_700_000_000.0
_UTC_MIDNIGHT = 1_699_920_000.0
_DAY = 86_400.0


def _no_tz_database(key: str) -> object:
    raise ZoneInfoNotFoundError(f"No time zone found with key {key}")


@pytest.fixture()
def no_tz_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(quota, "ZoneInfo", _no_tz_database)


def test_utc_window_survives_a_missing_tz_database(no_tz_database: None) -> None:
    assert quota.window_bounds("UTC", _NOW) == (_UTC_MIDNIGHT, _UTC_MIDNIGHT + _DAY)


def test_an_empty_zone_name_means_utc(no_tz_database: None) -> None:
    assert quota.window_bounds("", _NOW) == (_UTC_MIDNIGHT, _UTC_MIDNIGHT + _DAY)


def test_a_non_utc_zone_falls_back_to_utc_and_says_so(no_tz_database: None) -> None:
    with capture_logs() as logs:
        bounds = quota.window_bounds("America/Los_Angeles", _NOW)

    assert bounds == (_UTC_MIDNIGHT, _UTC_MIDNIGHT + _DAY)
    warned = [e for e in logs if e["event"] == "quota_tz_missing_using_utc"]
    assert warned
    assert warned[0]["tz"] == "America/Los_Angeles"
    assert warned[0]["log_level"] == "warning"


def test_reset_countdown_and_park_wait_do_not_raise(no_tz_database: None) -> None:
    assert quota.seconds_until_reset("UTC", _NOW) == pytest.approx(_UTC_MIDNIGHT + _DAY - _NOW)
    # A daily quota park on Google still counts down to the reset zone. That
    # used to be the second crash site when the zone database was missing.
    wait = quota.park_wait_s("google", 60.0, "credits_exhausted")
    assert 0.0 <= wait <= _DAY


def test_a_zone_that_exists_is_still_the_one_used(monkeypatch: pytest.MonkeyPatch) -> None:
    pacific = timezone(timedelta(hours=-8))
    monkeypatch.setattr(quota, "ZoneInfo", lambda key: pacific)

    start, nxt = quota.window_bounds("America/Los_Angeles", _NOW)

    # 22:13 UTC is 14:13 at -08:00, so the window opened at 08:00 UTC the same day.
    assert start == _UTC_MIDNIGHT + 8 * 3600
    assert nxt == start + _DAY
