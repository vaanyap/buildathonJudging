"""Server-side presentation timers.

Timer state is independent of the evaluation. Remaining time is derived from
the schedule duration plus organizer extensions, the accumulated seconds, and
whether the clock is currently running.
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assignment, Project, ScheduleSlot, Timer
from app.services.domain import DomainError
from app.services.evaluations import utcnow


def allowed_seconds(slot: ScheduleSlot | None) -> int:
    if slot is None:
        return 5 * 60
    return max(1, (slot.duration_minutes + slot.extension_minutes)) * 60


def _elapsed(timer: Timer, now: datetime) -> int:
    extra = 0
    if timer.status == "running" and timer.running_since is not None:
        extra = max(0, int((now - timer.running_since).total_seconds()))
    return timer.accumulated_seconds + extra


def _mark_time_up(timer: Timer, allowed: int, now: datetime) -> None:
    """Record the moment the clock actually hit zero, even if we notice later."""
    bank = timer.accumulated_seconds
    segment = max(0, allowed - bank)
    ended = timer.running_since + timedelta(seconds=segment) if timer.running_since is not None else now
    timer.accumulated_seconds = allowed
    timer.running_since = None
    timer.status = "time_up"
    if timer.actual_ended_at is None:
        timer.actual_ended_at = ended
    timer.updated_at = now


def sync_timer(timer: Timer, slot: ScheduleSlot | None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    allowed = allowed_seconds(slot)
    if timer.status == "running" and _elapsed(timer, now) >= allowed:
        _mark_time_up(timer, allowed, now)

    if timer.status == "running":
        elapsed = _elapsed(timer, now)
    else:
        elapsed = timer.accumulated_seconds
    elapsed = min(elapsed, allowed)
    remaining = max(0, allowed - elapsed)
    return {
        "status": timer.status,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "allowed_seconds": allowed,
        "warning": timer.status == "running" and 0 < remaining <= 60,
        "time_up": timer.status == "time_up",
        "actual_started_at": timer.actual_started_at.isoformat() if timer.actual_started_at else None,
        "actual_ended_at": timer.actual_ended_at.isoformat() if timer.actual_ended_at else None,
        "scheduled_duration_minutes": slot.duration_minutes if slot else 5,
        "extension_minutes": slot.extension_minutes if slot else 0,
        "starts_at": slot.starts_at.isoformat() if slot else None,
        "location": slot.location if slot else "",
    }


def start_timer(timer: Timer, slot: ScheduleSlot | None) -> dict:
    now = utcnow()
    sync_timer(timer, slot, now)
    if timer.status == "running":
        return sync_timer(timer, slot, now)
    remaining = allowed_seconds(slot) - timer.accumulated_seconds
    if remaining <= 0:
        timer.status = "time_up"
        timer.running_since = None
        if timer.actual_ended_at is None and timer.actual_started_at is not None:
            timer.actual_ended_at = now
        timer.updated_at = now
        raise DomainError("Time is up. An organizer can extend this presentation.", 400)
    if timer.status == "time_up":
        timer.actual_ended_at = None
    timer.status = "running"
    timer.running_since = now
    if timer.actual_started_at is None:
        timer.actual_started_at = now
    timer.updated_at = now
    return sync_timer(timer, slot, now)


def pause_timer(timer: Timer, slot: ScheduleSlot | None) -> dict:
    now = utcnow()
    if timer.status != "running":
        return sync_timer(timer, slot, now)
    allowed = allowed_seconds(slot)
    elapsed = _elapsed(timer, now)
    if elapsed >= allowed:
        _mark_time_up(timer, allowed, now)
    else:
        timer.accumulated_seconds = elapsed
        timer.running_since = None
        timer.status = "paused"
        timer.updated_at = now
    return sync_timer(timer, slot, now)


def reset_timer(timer: Timer, slot: ScheduleSlot | None) -> dict:
    now = utcnow()
    timer.accumulated_seconds = 0
    timer.running_since = None
    timer.actual_started_at = None
    timer.actual_ended_at = None
    timer.status = "idle"
    timer.updated_at = now
    return sync_timer(timer, slot, now)


def refresh_all_timers(db: Session) -> None:
    timers = db.scalars(
        select(Timer).options(
            selectinload(Timer.assignment).selectinload(Assignment.project).selectinload(Project.schedule)
        )
    ).all()
    now = utcnow()
    for timer in timers:
        assignment = timer.assignment
        slot = assignment.project.schedule if assignment is not None and assignment.project is not None else None
        sync_timer(timer, slot, now)
