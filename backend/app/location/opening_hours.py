"""Minimal parser for OpenStreetMap `opening_hours` values.

Handles the common forms: "24/7", "Mo-Su 10:00-23:00", "Mo-Fr 09:00-18:00; Sa 10:00-14:00",
"10:00-02:00" (every day, past midnight), "Mo-Sa 11:00-15:00,19:00-23:30", "Su off".
Anything more complex (public holidays, months, sunrise...) returns "unknown"
rather than a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

DELHI = ZoneInfo("Asia/Kolkata")
_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_RULE = re.compile(r"^(?:(?P<days>[A-Za-z,\- ]+?)\s+)?(?P<times>off|closed|\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}(?:\s*,\s*\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})*)$")


@dataclass
class OpeningStatus:
    open_now: bool | None  # None = unknown / unparseable
    late_night: bool | None  # open at or after 23:00 on some day


def _parse_days(spec: str | None) -> set[int] | None:
    if not spec:
        return set(range(7))
    days: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            if a not in _DAYS or b not in _DAYS:
                return None
            i, j = _DAYS.index(a), _DAYS.index(b)
            rng = range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)]
            days.update(rng)
        elif part in _DAYS:
            days.add(_DAYS.index(part))
        else:
            return None
    return days


def _t(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def parse(value: str | None) -> dict[int, list[tuple[int, int]]] | None:
    """Day index -> list of (start_min, end_min); end may exceed 1440 (past midnight)."""
    if not value:
        return None
    v = value.strip()
    if v == "24/7":
        return {d: [(0, 1440)] for d in range(7)}
    schedule: dict[int, list[tuple[int, int]]] = {}
    for rule in [r.strip() for r in v.split(";") if r.strip()]:
        m = _RULE.match(rule)
        if not m:
            return None
        days = _parse_days(m.group("days"))
        if days is None:
            return None
        times = m.group("times")
        for d in days:
            schedule[d] = []  # later rules override earlier ones (OSM semantics)
        if times in ("off", "closed"):
            continue
        for rng in times.split(","):
            a, b = [x.strip() for x in rng.split("-")]
            start, end = _t(a), _t(b)
            if end <= start:
                end += 1440
            for d in days:
                schedule[d].append((start, end))
    return schedule


def status(value: str | None, now: datetime | None = None) -> OpeningStatus:
    sched = parse(value)
    if sched is None:
        return OpeningStatus(None, None)
    now = (now or datetime.now(DELHI)).astimezone(DELHI)
    minutes = now.hour * 60 + now.minute
    today, yesterday = now.weekday(), (now.weekday() - 1) % 7
    is_open = any(s <= minutes < e for s, e in sched.get(today, [])) or any(
        s <= minutes + 1440 < e for s, e in sched.get(yesterday, [])
    )
    late = any(e >= 23 * 60 for ranges in sched.values() for _, e in ranges)
    return OpeningStatus(is_open, late)


def format_close(value: str | None, now: datetime | None = None) -> str | None:
    """Human hint like 'until 23:00' for today, when simple to compute."""
    sched = parse(value)
    if not sched:
        return None
    now = (now or datetime.now(DELHI)).astimezone(DELHI)
    minutes = now.hour * 60 + now.minute
    for s, e in sched.get(now.weekday(), []):
        if s <= minutes < e:
            end = (datetime.combine(now.date(), time()) + timedelta(minutes=e)).strftime("%H:%M")
            return f"until {end}"
    return None
