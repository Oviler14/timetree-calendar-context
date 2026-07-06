#!/usr/bin/env python3
"""Convert a TimeTree-exported ICS file into compact planner calendar context."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import recurring_ical_events
from icalendar import Calendar


DAY_START = time(7, 0)
DAY_END = time(22, 0)
MORNING = (time(7, 0), time(12, 0))
AFTERNOON = (time(12, 0), time(18, 0))
EVENING = (time(18, 0), time(22, 0))

TRAVEL_WORDS = ("travel", "flight", "train", "airport", "hotel", "trip", "drive")
HOLIDAY_WORDS = ("holiday", "vacation", "abroad", "away", "lanzarote")
PASSIVE_ALL_DAY_WORDS = ("birthday", "anniversary")
BLOCKING_ALL_DAY_WORDS = ("blocked", "unavailable", "sick", "ill", "hospital", "wedding")
DEADLINE_WORDS = ("deadline", "due", "payment", "pay", "renew", "expires")
APPOINTMENT_WORDS = ("dentist", "doctor", "appointment", "gp", "hospital", "therapy")
SOCIAL_WORDS = ("dinner", "drinks", "party", "wedding", "lunch", "meet")
GUEST_WORDS = ("visiting", "visit", "staying", "guest", "guests")

COLOR_RULES = {
    "#f35f8c": {
        "participation": "eleanor",
        "involves": ("eleanor",),
        "affects_capacity": False,
        "discard": False,
    },
    "#3dc2c8": {
        "participation": "oliver",
        "involves": ("oliver",),
        "affects_capacity": True,
        "discard": False,
    },
    "#b38bdc": {
        "participation": "both",
        "involves": ("oliver", "eleanor"),
        "affects_capacity": True,
        "discard": False,
    },
    "#c0ca33": {
        "participation": "birthday",
        "involves": (),
        "affects_capacity": False,
        "discard": True,
    },
    "#e73b3b": {
        "participation": "liverpool",
        "involves": ("oliver",),
        "affects_capacity": True,
        "discard": False,
    },
}

UNKNOWN_COLOR_RULE = {
    "participation": "unknown",
    "involves": (),
    "affects_capacity": True,
    "discard": False,
}


@dataclass(frozen=True)
class Event:
    title: str
    start: datetime | date
    end: datetime | date
    all_day: bool
    location: str
    has_rrule: bool
    labels: tuple[str, ...]
    color: str | None

    @property
    def text(self) -> str:
        return f"{self.title} {self.location} {' '.join(self.labels)}".lower()

    @property
    def is_passive_all_day(self) -> bool:
        return self.all_day and any(word in self.text for word in PASSIVE_ALL_DAY_WORDS)

    @property
    def all_day_duration_days(self) -> int:
        if not self.all_day:
            return 0
        return max(1, (self.end - self.start).days)

    @property
    def is_multi_day_all_day(self) -> bool:
        return self.all_day_duration_days > 1

    @property
    def is_explicit_blocking_all_day(self) -> bool:
        return (
            self.affects_capacity
            and self.all_day
            and not self.is_passive_all_day
            and any(word in self.text for word in BLOCKING_ALL_DAY_WORDS)
        )

    @property
    def color_rule(self) -> dict:
        if not self.color:
            return UNKNOWN_COLOR_RULE
        return COLOR_RULES.get(self.color.lower(), UNKNOWN_COLOR_RULE)

    @property
    def participation(self) -> str:
        return str(self.color_rule["participation"])

    @property
    def involves(self) -> tuple[str, ...]:
        return tuple(self.color_rule["involves"])

    @property
    def affects_capacity(self) -> bool:
        return bool(self.color_rule["affects_capacity"])

    @property
    def should_discard(self) -> bool:
        return bool(self.color_rule["discard"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ics", required=True, type=Path)
    parser.add_argument("--timezone", default="Europe/London")
    parser.add_argument("--date", help="Planning date in YYYY-MM-DD form.")
    parser.add_argument("--lookahead-days", type=int, default=14)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def decoded(component, key: str):
    if not component.get(key):
        return None
    return component.decoded(key)


def text_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def extract_labels(component) -> tuple[str, ...]:
    raw = decoded(component, "categories")
    if raw is None:
        return ()

    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    labels: list[str] = []
    for value in values:
        for part in text_value(value).split(","):
            label = part.strip()
            if label and label not in labels:
                labels.append(label)
    return tuple(labels)


def extract_color(component) -> str | None:
    for key in ("color", "x-apple-calendar-color", "x-color"):
        color = component.get(key)
        if color:
            return text_value(color).strip().lower() or None
    return None


def normalize_datetime(value: datetime, timezone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone)
    return value.astimezone(timezone)


def load_events(
    path: Path, timezone: ZoneInfo, target: date, lookahead_days: int
) -> tuple[list[Event], bool]:
    calendar = Calendar.from_ical(path.read_bytes())
    window_start = datetime.combine(target, time.min, tzinfo=timezone)
    window_end = datetime.combine(target + timedelta(days=lookahead_days + 1), time.min, tzinfo=timezone)
    components = recurring_ical_events.of(calendar).between(window_start, window_end)
    events: list[Event] = []
    saw_rrule = any(bool(component.get("rrule")) for component in calendar.walk("VEVENT"))

    for component in components:
        start = decoded(component, "dtstart")
        if start is None:
            continue

        end = decoded(component, "dtend")
        all_day = isinstance(start, date) and not isinstance(start, datetime)

        if all_day:
            end = end or (start + timedelta(days=1))
        else:
            start = normalize_datetime(start, timezone)
            end = normalize_datetime(end, timezone) if end else start + timedelta(hours=1)

        has_rrule = bool(component.get("rrule"))
        events.append(
            Event(
                title=str(component.get("summary", "")).strip() or "Untitled event",
                start=start,
                end=end,
                all_day=all_day,
                location=str(component.get("location", "")).strip(),
                has_rrule=has_rrule,
                labels=extract_labels(component),
                color=extract_color(component),
            )
        )

    return events, saw_rrule


def event_overlaps_day(event: Event, target: date, timezone: ZoneInfo) -> bool:
    if event.all_day:
        return event.start <= target < event.end

    day_start = datetime.combine(target, time.min, tzinfo=timezone)
    day_end = day_start + timedelta(days=1)
    return event.start < day_end and event.end > day_start


def event_starts_within(event: Event, start_date: date, end_date: date, timezone: ZoneInfo) -> bool:
    event_date = event.start if event.all_day else event.start.astimezone(timezone).date()
    return start_date <= event_date <= end_date


def category(event: Event) -> str:
    text = event.text
    if any(word in text for word in TRAVEL_WORDS + HOLIDAY_WORDS):
        return "travel"
    if any(word in text for word in GUEST_WORDS):
        return "guest"
    if any(word in text for word in DEADLINE_WORDS):
        return "deadline"
    if any(word in text for word in APPOINTMENT_WORDS):
        return "appointment"
    if any(word in text for word in SOCIAL_WORDS):
        return "social"
    if event.is_passive_all_day:
        return "passive_reminder"
    return "event"


def planning_relevance(event: Event) -> str:
    if event.participation == "eleanor":
        return "Eleanor-only context; does not reduce Oliver's capacity but may affect shared plans or free time"
    if event.participation == "liverpool":
        return "Liverpool match; Oliver is likely to watch and it should count against available time"

    event_category = category(event)
    if event_category == "travel":
        if event.all_day:
            return "all-day or multi-day away context; keep the plan very light and prefer portable, low-friction tasks"
        return "travel may reduce capacity; keep the plan light"
    if event_category == "guest":
        return "guest or visitor context; reduce the plan and prefer approachable or home-friendly tasks"
    if event_category == "deadline":
        return "upcoming deadline may justify a small supporting task"
    if event_category == "appointment":
        return "fixed appointment; avoid scheduling deep work nearby"
    if event_category == "social":
        return "social commitment may reduce evening capacity"
    if event_category == "passive_reminder":
        return "passive all-day reminder; do not reduce capacity by itself"
    if event.is_explicit_blocking_all_day:
        return "explicit all-day blocker; keep discretionary tasks to a minimum"
    if event.all_day and event.affects_capacity:
        if event.is_multi_day_all_day:
            return "multi-day all-day context; likely away or disrupted routine, so keep the plan very light"
        if event.title.strip().endswith(("'s", "’s")):
            return "single-day all-day social context, likely an evening plan; protect evening capacity but keep daytime tasks possible"
        return "single-day all-day context; use the event title to infer availability instead of treating the whole day as blocked"
    return "calendar event may affect available time"


def iso_value(value: datetime | date) -> str:
    return value.isoformat()


def all_day_interpretation(event: Event) -> str | None:
    if not event.all_day:
        return None
    if event.is_passive_all_day:
        return "passive_reminder"
    if event.is_explicit_blocking_all_day:
        return "blocking"
    event_category = category(event)
    if event_category == "guest":
        return "guest_context"
    if event_category == "travel" or event.is_multi_day_all_day:
        return "away_or_multi_day"
    if event.title.strip().endswith(("'s", "’s")):
        return "likely_evening_context"
    return "context"


def event_payload(event: Event, target: date | None = None) -> dict:
    payload = {
        "title": event.title,
        "starts_at": iso_value(event.start),
        "all_day": event.all_day,
        "labels": list(event.labels),
        "color": event.color,
        "participation": event.participation,
        "involves": list(event.involves),
        "affects_capacity": event.affects_capacity,
        "category": category(event),
        "planning_relevance": planning_relevance(event),
    }
    if not event.all_day:
        payload["ends_at"] = iso_value(event.end)
    else:
        payload["ends_at"] = iso_value(event.end)
        payload["duration_days"] = event.all_day_duration_days
        payload["all_day_interpretation"] = all_day_interpretation(event)
    if target is not None:
        event_date = event.start if event.all_day else event.start.date()
        payload["days_until"] = (event_date - target).days
    return payload


def clipped_busy_intervals(
    events: list[Event], target: date, timezone: ZoneInfo
) -> tuple[list[tuple[datetime, datetime]], bool]:
    work_start = datetime.combine(target, DAY_START, tzinfo=timezone)
    work_end = datetime.combine(target, DAY_END, tzinfo=timezone)
    intervals: list[tuple[datetime, datetime]] = []
    all_day_blocking = False

    for event in events:
        if event.all_day:
            all_day_blocking = all_day_blocking or event.is_explicit_blocking_all_day
            continue
        start = max(event.start, work_start)
        end = min(event.end, work_end)
        if start < end:
            intervals.append((start, end))

    intervals.sort()
    merged: list[tuple[datetime, datetime]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))

    return merged, all_day_blocking


def minutes_between(start: datetime, end: datetime) -> int:
    return int((end - start).total_seconds() // 60)


def largest_free_block(intervals: list[tuple[datetime, datetime]], target: date, timezone: ZoneInfo) -> int:
    day_start = datetime.combine(target, DAY_START, tzinfo=timezone)
    day_end = datetime.combine(target, DAY_END, tzinfo=timezone)
    cursor = day_start
    largest = 0

    for start, end in intervals:
        largest = max(largest, minutes_between(cursor, start))
        cursor = max(cursor, end)

    return max(largest, minutes_between(cursor, day_end))


def segment_status(
    intervals: list[tuple[datetime, datetime]], target: date, timezone: ZoneInfo, bounds: tuple[time, time]
) -> str:
    start = datetime.combine(target, bounds[0], tzinfo=timezone)
    end = datetime.combine(target, bounds[1], tzinfo=timezone)
    segment_minutes = minutes_between(start, end)
    busy = 0
    event_count = 0

    for event_start, event_end in intervals:
        overlap_start = max(start, event_start)
        overlap_end = min(end, event_end)
        if overlap_start < overlap_end:
            event_count += 1
            busy += minutes_between(overlap_start, overlap_end)

    if busy == 0:
        return "open"
    if busy >= segment_minutes * 0.7:
        return "busy"
    if event_count > 1 or busy >= segment_minutes * 0.3:
        return "fragmented"
    return "mostly_open"


def all_day_load_minutes(events: list[Event]) -> int:
    load = 0
    for event in events:
        if not event.all_day or not event.affects_capacity or event.is_passive_all_day:
            continue
        event_category = category(event)
        if event.is_explicit_blocking_all_day:
            load += 480
        elif event_category == "travel" or event.is_multi_day_all_day:
            load += 300
        elif event_category == "guest":
            load += 180
        elif event.participation == "liverpool":
            load += 120
        else:
            load += 90
    return load


def contextual_segment_status(base: str, events: list[Event], segment: str) -> str:
    if base in ("busy", "fragmented"):
        return base

    contextual_events = [
        event
        for event in events
        if event.all_day and event.affects_capacity and not event.is_passive_all_day
    ]
    if not contextual_events:
        return base
    if any(event.is_explicit_blocking_all_day for event in contextual_events):
        return "busy"
    if any(category(event) == "guest" or category(event) == "travel" or event.is_multi_day_all_day for event in contextual_events):
        return "context_limited"
    if segment == "evening":
        return "context_limited"
    return base


def build_context(events: list[Event], saw_rrule: bool, target: date, timezone: ZoneInfo, lookahead: int) -> dict:
    events = [event for event in events if not event.should_discard]
    today_events = [event for event in events if event_overlaps_day(event, target, timezone)]
    capacity_today_events = [event for event in today_events if event.affects_capacity]
    upcoming_start = target + timedelta(days=1)
    upcoming_end = target + timedelta(days=lookahead)
    upcoming_events = [
        event for event in events if event_starts_within(event, upcoming_start, upcoming_end, timezone)
    ]

    intervals, all_day_blocking = clipped_busy_intervals(capacity_today_events, target, timezone)
    busy_minutes = sum(minutes_between(start, end) for start, end in intervals)
    all_day_context_minutes = all_day_load_minutes(capacity_today_events)
    effective_busy_minutes = busy_minutes + all_day_context_minutes
    largest_block = 0 if all_day_blocking else largest_free_block(intervals, target, timezone)
    event_count = len([event for event in capacity_today_events if not event.is_passive_all_day])
    has_travel = any(category(event) == "travel" for event in capacity_today_events)
    has_evening_commitment = any(
        not event.all_day and event.end > datetime.combine(target, time(18, 0), tzinfo=timezone)
        for event in capacity_today_events
    ) or any(
        event.all_day
        and event.affects_capacity
        and not event.is_passive_all_day
        and not (category(event) == "travel" or category(event) == "guest" or event.is_multi_day_all_day)
        for event in capacity_today_events
    )

    if all_day_blocking:
        label = "blocked"
    elif busy_minutes == 0 and not today_events:
        label = "none"
    elif effective_busy_minutes > 300 or has_travel or largest_block < 90:
        label = "heavy"
    elif effective_busy_minutes < 120 and largest_block >= 180:
        label = "light"
    else:
        label = "moderate"

    score = min(
        1.0,
        round((busy_minutes / 600) + (all_day_context_minutes / 720) + (event_count * 0.05) + (0.25 if has_travel else 0), 2),
    )
    generated_at = datetime.now(timezone)
    warnings = []
    if saw_rrule:
        warnings.append("ICS contains recurrence rules; recurring events were expanded for the planning window.")
    unknown_colors = sorted({event.color for event in events if event.color and event.color not in COLOR_RULES})
    if unknown_colors:
        warnings.append(f"Unmapped TimeTree colors present: {', '.join(unknown_colors)}.")

    return {
        "success": True,
        "source": "timetree",
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "timezone": str(timezone),
        "date": target.isoformat(),
        "freshness": {"status": "fresh", "age_minutes": 0},
        "calendar_load": {
            "label": label,
            "score": score,
            "busy_minutes": busy_minutes,
            "all_day_context_minutes": all_day_context_minutes,
            "event_count": event_count,
            "context_event_count": len(today_events),
            "has_travel": has_travel,
            "has_evening_commitment": has_evening_commitment,
        },
        "availability": {
            "morning": contextual_segment_status(
                "busy" if all_day_blocking else segment_status(intervals, target, timezone, MORNING),
                capacity_today_events,
                "morning",
            ),
            "afternoon": contextual_segment_status(
                "busy" if all_day_blocking else segment_status(intervals, target, timezone, AFTERNOON),
                capacity_today_events,
                "afternoon",
            ),
            "evening": contextual_segment_status(
                "busy" if all_day_blocking else segment_status(intervals, target, timezone, EVENING),
                capacity_today_events,
                "evening",
            ),
            "largest_free_block_minutes": largest_block,
        },
        "today_events": [event_payload(event) for event in today_events],
        "upcoming_events": [event_payload(event, target) for event in upcoming_events[:10]],
        "warnings": warnings,
    }


def main() -> None:
    args = parse_args()
    timezone = ZoneInfo(args.timezone)
    target = date.fromisoformat(args.date) if args.date else datetime.now(timezone).date()
    events, saw_rrule = load_events(args.ics, timezone, target, args.lookahead_days)
    context = build_context(events, saw_rrule, target, timezone, args.lookahead_days)
    output = json.dumps(context, indent=2)

    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
