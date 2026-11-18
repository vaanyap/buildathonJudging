"""Live view of where each track is in its presentation order."""

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Track, TrackAssignment
from app.services.timers import get_timer_defaults, refresh_all_timers, sync_timer


def _phase_done(timer, phase: str) -> bool:
    return getattr(timer, f"{phase}_status") == "time_up" or getattr(timer, f"{phase}_ended_at") is not None


def _project_state(timers: list) -> tuple[str, str]:
    if not timers or all(
        timer.presentation_status == "idle" and timer.qa_status == "idle" and timer.presentation_started_at is None
        for timer in timers
    ):
        return "not_started", "Not started"
    if any(timer.qa_status == "running" for timer in timers):
        return "qa", "Q&A is running"
    if any(timer.presentation_status == "running" for timer in timers):
        return "presentation", "Presentation is running"
    if all(_phase_done(timer, "presentation") and _phase_done(timer, "qa") for timer in timers):
        return "done", "Both phases finished"
    if any(timer.qa_status == "paused" for timer in timers):
        return "qa", "Q&A is paused"
    if any(_phase_done(timer, "presentation") for timer in timers) and all(timer.qa_status == "idle" for timer in timers):
        return "between", "Presentation ended and Q&A has not started"
    if any(timer.presentation_status == "paused" for timer in timers):
        return "presentation", "Presentation is paused"
    return "presentation", "Presentation is in progress"


def track_board(db: Session) -> dict:
    refresh_all_timers(db)
    defaults = get_timer_defaults(db)
    from app.services.aggregation import load_projects

    projects = load_projects(db)
    by_track: dict[int, list] = {}
    for project in projects:
        for member in project.memberships:
            by_track.setdefault(member.track_id, []).append((project, member))
    tracks = db.scalars(select(Track).options(selectinload(Track.track_assignments).selectinload(TrackAssignment.judge)).order_by(Track.name)).all()
    rows = []
    for track in tracks:
        ordered = sorted(
            by_track.get(track.id, []),
            key=lambda pair: (
                pair[1].presentation_order is None,
                pair[1].presentation_order or 0,
                pair[0].name.lower(),
            ),
        )
        project_rows = []
        for project, member in ordered:
            timers = [timer for timer in project.timers if any(
                assignment.judge_id == timer.judge_id and assignment.track_id == track.id
                for assignment in project.assignments
            )]
            state, detail = _project_state(timers)
            judges = []
            for assignment in sorted(
                (item for item in project.assignments if item.track_id == track.id),
                key=lambda item: item.judge.name.lower(),
            ):
                timer = next((item for item in project.timers if item.judge_id == assignment.judge_id), None)
                view = sync_timer(timer, project, defaults) if timer is not None else None
                presentation = view["phases"]["presentation"] if view else None
                qa = view["phases"]["qa"] if view else None
                judges.append(
                    {
                        "judge_id": assignment.judge_id,
                        "judge_name": assignment.judge.name,
                        "current_phase": view["current_phase"] if view else "presentation",
                        "presentation_status": presentation["status"] if presentation else "idle",
                        "qa_status": qa["status"] if qa else "idle",
                        "presentation_started_at": presentation["actual_started_at"] if presentation else None,
                        "presentation_ended_at": presentation["actual_ended_at"] if presentation else None,
                        "qa_started_at": qa["actual_started_at"] if qa else None,
                        "qa_ended_at": qa["actual_ended_at"] if qa else None,
                    }
                )
            project_rows.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "presentation_order": member.presentation_order,
                    "presentation_minutes": project.presentation_minutes,
                    "qa_minutes": project.qa_minutes,
                    "presentation_extension_minutes": project.presentation_extension_minutes,
                    "qa_extension_minutes": project.qa_extension_minutes,
                    "state": state,
                    "detail": detail,
                    "judges": judges,
                }
            )
        running = [item for item in project_rows if item["state"] in {"presentation", "qa", "between"}]
        unfinished = [item for item in project_rows if item["state"] != "done"]
        current = running[0] if running else (unfinished[0] if unfinished else None)
        rows.append(
            {
                "track_id": track.id,
                "track_name": track.name,
                "judges": [
                    {"judge_id": item.judge_id, "judge_name": item.judge.name}
                    for item in sorted(track.track_assignments, key=lambda item: item.judge.name.lower())
                ],
                "project_count": len(project_rows),
                "finished_projects": sum(1 for item in project_rows if item["state"] == "done"),
                "current_project_id": current["project_id"] if current else None,
                "current_project_name": current["project_name"] if current else None,
                "current_state": current["state"] if current else "done",
                "current_detail": current["detail"] if current else "Every project has finished both phases",
                "projects": project_rows,
            }
        )
    return {"tracks": rows}
