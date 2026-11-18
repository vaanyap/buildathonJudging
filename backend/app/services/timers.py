"""Server-side presentation and Q&A timers.

Each assignment has two phases. The judge starts presentation, then starts Q&A
with a separate action. Q&A never starts on its own. Remaining time is derived
from the phase length plus that phase's organizer extension, the accumulated
seconds, and whether that phase's clock is running. Scoring does not read this
state.
"""

from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assignment, Project, Timer, TimerDefaults
from app.services.domain import DomainError
from app.services.evaluations import utcnow

PhaseName = Literal["presentation", "qa"]
PHASES: tuple[PhaseName, ...] = ("presentation", "qa")
PHASE_LABELS: dict[PhaseName, str] = {"presentation": "Presentation", "qa": "Q&A"}
DEFAULT_PRESENTATION_MINUTES = 3
DEFAULT_QA_MINUTES = 2


def other_phase(phase: PhaseName) -> PhaseName:
    return "qa" if phase == "presentation" else "presentation"


def warning_threshold(allowed_seconds: int) -> int:
    """One minute when the phase is longer than two minutes, otherwise 30 seconds."""
    if allowed_seconds > 120:
        return 60
    if allowed_seconds > 45:
        return 30
    return 0


def get_timer_defaults(db: Session) -> TimerDefaults:
    row = db.get(TimerDefaults, 1)
    if row is None:
        row = TimerDefaults(
            id=1,
            presentation_minutes=DEFAULT_PRESENTATION_MINUTES,
            qa_minutes=DEFAULT_QA_MINUTES,
            updated_at=utcnow(),
        )
        db.add(row)
        db.flush()
    return row


def phase_lengths(project: Project | None, defaults: TimerDefaults, phase: PhaseName) -> tuple[int, int]:
    if phase == "presentation":
        if project is None or project.presentation_minutes is None:
            scheduled = defaults.presentation_minutes
            extension = 0 if project is None else project.presentation_extension_minutes
            return scheduled, extension
        return project.presentation_minutes, project.presentation_extension_minutes
    if project is None or project.qa_minutes is None:
        scheduled = defaults.qa_minutes
        extension = 0 if project is None else project.qa_extension_minutes
        return scheduled, extension
    return project.qa_minutes, project.qa_extension_minutes


def allowed_seconds(project: Project | None, defaults: TimerDefaults, phase: PhaseName) -> int:
    scheduled, extension = phase_lengths(project, defaults, phase)
    return max(1, (scheduled + extension) * 60)


def _get(timer: Timer, phase: PhaseName, field: str):
    return getattr(timer, f"{phase}_{field}")


def _set(timer: Timer, phase: PhaseName, field: str, value) -> None:
    setattr(timer, f"{phase}_{field}", value)


def _elapsed(timer: Timer, phase: PhaseName, now: datetime) -> int:
    extra = 0
    if _get(timer, phase, "status") == "running" and _get(timer, phase, "running_since") is not None:
        extra = max(0, int((now - _get(timer, phase, "running_since")).total_seconds()))
    return _get(timer, phase, "accumulated_seconds") + extra


def _reconcile_active(timer: Timer) -> None:
    if timer.qa_status == "running":
        timer.active_phase = "qa"
        return
    if timer.presentation_status == "running":
        timer.active_phase = "presentation"
        return
    if timer.qa_status in {"paused", "time_up"}:
        timer.active_phase = "qa"
        return
    if timer.presentation_status == "time_up" and timer.qa_status == "idle":
        timer.active_phase = "qa"
        return
    if timer.presentation_status == "paused":
        timer.active_phase = "presentation"
        return
    if timer.active_phase not in PHASES:
        timer.active_phase = "presentation"


def _mark_phase_time_up(timer: Timer, phase: PhaseName, allowed: int, now: datetime) -> None:
    """Record the moment the clock actually hit zero, even if we notice later."""
    running_since = _get(timer, phase, "running_since")
    bank = _get(timer, phase, "accumulated_seconds")
    segment = max(0, allowed - bank)
    ended = running_since + timedelta(seconds=segment) if running_since is not None else now
    _set(timer, phase, "accumulated_seconds", allowed)
    _set(timer, phase, "running_since", None)
    _set(timer, phase, "status", "time_up")
    if _get(timer, phase, "ended_at") is None:
        _set(timer, phase, "ended_at", ended)
    timer.updated_at = now


def _sync_phase(timer: Timer, project: Project | None, defaults: TimerDefaults, phase: PhaseName, now: datetime) -> None:
    allowed = allowed_seconds(project, defaults, phase)
    if _get(timer, phase, "status") == "running" and _elapsed(timer, phase, now) >= allowed:
        _mark_phase_time_up(timer, phase, allowed, now)


def _phase_view(timer: Timer, project: Project | None, defaults: TimerDefaults, phase: PhaseName, now: datetime) -> dict:
    allowed = allowed_seconds(project, defaults, phase)
    scheduled, extension = phase_lengths(project, defaults, phase)
    status = _get(timer, phase, "status")
    if status == "running":
        elapsed = _elapsed(timer, phase, now)
    else:
        elapsed = _get(timer, phase, "accumulated_seconds")
    elapsed = min(elapsed, allowed)
    remaining = max(0, allowed - elapsed)
    threshold = warning_threshold(allowed)
    started = _get(timer, phase, "started_at")
    ended = _get(timer, phase, "ended_at")
    return {
        "key": phase,
        "label": PHASE_LABELS[phase],
        "status": status,
        "elapsed_seconds": elapsed,
        "remaining_seconds": remaining,
        "allowed_seconds": allowed,
        "warning_seconds": threshold,
        "warning": status == "running" and threshold > 0 and 0 < remaining <= threshold,
        "time_up": status == "time_up",
        "actual_started_at": started.isoformat() if started else None,
        "actual_ended_at": ended.isoformat() if ended else None,
        "scheduled_minutes": scheduled,
        "extension_minutes": extension,
    }


def sync_timer(
    timer: Timer,
    project: Project | None,
    defaults: TimerDefaults,
    now: datetime | None = None,
) -> dict:
    now = now or utcnow()
    for phase in PHASES:
        _sync_phase(timer, project, defaults, phase, now)
    _reconcile_active(timer)
    current = timer.active_phase if timer.active_phase in PHASES else "presentation"
    return {
        "current_phase": current,
        "phases": {phase: _phase_view(timer, project, defaults, phase, now) for phase in PHASES},
    }


def _pause_phase(
    timer: Timer,
    project: Project | None,
    defaults: TimerDefaults,
    phase: PhaseName,
    now: datetime,
) -> None:
    if _get(timer, phase, "status") != "running":
        return
    allowed = allowed_seconds(project, defaults, phase)
    elapsed = _elapsed(timer, phase, now)
    if elapsed >= allowed:
        _mark_phase_time_up(timer, phase, allowed, now)
        return
    _set(timer, phase, "accumulated_seconds", elapsed)
    _set(timer, phase, "running_since", None)
    _set(timer, phase, "status", "paused")
    timer.updated_at = now


def _mark_left(timer: Timer, phase: PhaseName, now: datetime) -> None:
    """Record an end when the judge leaves a phase that already started."""
    if _get(timer, phase, "started_at") is None or _get(timer, phase, "ended_at") is not None:
        return
    if _get(timer, phase, "status") == "idle":
        return
    _set(timer, phase, "ended_at", now)


def start_timer(timer: Timer, project: Project | None, defaults: TimerDefaults, phase: PhaseName) -> dict:
    now = utcnow()
    sync_timer(timer, project, defaults, now)
    if _get(timer, phase, "status") == "running":
        timer.active_phase = phase
        return sync_timer(timer, project, defaults, now)
    remaining = allowed_seconds(project, defaults, phase) - _get(timer, phase, "accumulated_seconds")
    if remaining <= 0:
        _set(timer, phase, "status", "time_up")
        _set(timer, phase, "running_since", None)
        if _get(timer, phase, "ended_at") is None and _get(timer, phase, "started_at") is not None:
            _set(timer, phase, "ended_at", now)
        timer.active_phase = phase
        timer.updated_at = now
        raise DomainError(f"{PHASE_LABELS[phase]} time is up. An organizer can extend this phase.", 400)
    _pause_phase(timer, project, defaults, other_phase(phase), now)
    _mark_left(timer, other_phase(phase), now)
    _set(timer, phase, "ended_at", None)
    _set(timer, phase, "status", "running")
    _set(timer, phase, "running_since", now)
    if _get(timer, phase, "started_at") is None:
        _set(timer, phase, "started_at", now)
    timer.active_phase = phase
    timer.updated_at = now
    return sync_timer(timer, project, defaults, now)


def pause_timer(timer: Timer, project: Project | None, defaults: TimerDefaults, phase: PhaseName) -> dict:
    now = utcnow()
    sync_timer(timer, project, defaults, now)
    _pause_phase(timer, project, defaults, phase, now)
    timer.active_phase = phase
    return sync_timer(timer, project, defaults, now)


def reset_timer(timer: Timer, project: Project | None, defaults: TimerDefaults, phase: PhaseName) -> dict:
    now = utcnow()
    _set(timer, phase, "accumulated_seconds", 0)
    _set(timer, phase, "running_since", None)
    _set(timer, phase, "started_at", None)
    _set(timer, phase, "ended_at", None)
    _set(timer, phase, "status", "idle")
    timer.active_phase = phase
    timer.updated_at = now
    return sync_timer(timer, project, defaults, now)


def refresh_all_timers(db: Session) -> None:
    defaults = get_timer_defaults(db)
    timers = db.scalars(
        select(Timer).options(
            selectinload(Timer.project)
        )
    ).all()
    now = utcnow()
    for timer in timers:
        sync_timer(timer, timer.project, defaults, now)


def parse_phase(value: str) -> PhaseName:
    if value not in PHASES:
        raise DomainError("Unknown timer phase.", 400)
    return value  # type: ignore[return-value]
