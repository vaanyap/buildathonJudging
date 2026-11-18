"""Submitted-only aggregation, rankings, discrepancies, progress, and cross-track review.

Drafts, viewed, and ready-to-submit evaluations never affect averages or ranks.
Track alignment is reported beside the quality score and is not mixed into it.
"""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assignment, AuditEvent, Evaluation, Project, Score, Track
from app.serialize import criterion_dict, evaluation_dict
from app.services.evaluations import active_criteria, utcnow
from app.services.timers import refresh_all_timers

OVERALL_GAP = 1.5
CRITERION_GAP = 2


def project_load_options():
    return (
        selectinload(Project.track),
        selectinload(Project.schedule),
        selectinload(Project.assignments).selectinload(Assignment.judge),
        selectinload(Project.assignments).selectinload(Assignment.timer),
        selectinload(Project.assignments)
        .selectinload(Assignment.evaluation)
        .selectinload(Evaluation.scores)
        .selectinload(Score.criterion),
        selectinload(Project.assignments)
        .selectinload(Assignment.evaluation)
        .selectinload(Evaluation.audits)
        .selectinload(AuditEvent.actor),
        selectinload(Project.assignments)
        .selectinload(Assignment.evaluation)
        .selectinload(Evaluation.recommended_track),
    )


def load_projects(db: Session) -> list[Project]:
    return list(db.scalars(select(Project).options(*project_load_options()).order_by(Project.id)).unique().all())


def get_project(db: Session, project_id: int) -> Project | None:
    return db.scalar(select(Project).where(Project.id == project_id).options(*project_load_options()))


def _judge_overall(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def _audit_dict(audit: AuditEvent) -> dict:
    return {
        "id": audit.id,
        "action": audit.action,
        "actor_name": audit.actor.name if audit.actor is not None else None,
        "changes": audit.changes,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
    }


def build_results(db: Session, track_id: int | None = None) -> dict:
    criteria = active_criteria(db)
    rows: list[dict] = []
    for project in load_projects(db):
        if track_id is not None and project.track_id != track_id:
            continue
        judges = []
        evaluations = []
        overalls: list[float] = []
        criterion_values: dict[str, list[int]] = {criterion.key: [] for criterion in criteria}
        alignment_counts = {"strong": 0, "partial": 0, "weak": 0}

        for assignment in sorted(project.assignments, key=lambda item: item.judge.name.lower()):
            evaluation = assignment.evaluation
            status = evaluation.status if evaluation is not None else "not_started"
            judges.append(
                {
                    "assignment_id": assignment.id,
                    "judge_id": assignment.judge_id,
                    "judge_name": assignment.judge.name,
                    "status": status,
                    "evaluation_id": evaluation.id if evaluation is not None else None,
                }
            )
            if evaluation is None:
                continue
            # Organizers can inspect in-progress scorecards. Private notes are omitted
            # by evaluation_dict. Averages below still use submitted rows only.
            if evaluation.status == "not_started" and evaluation.submission_count <= 0:
                continue
            score_values: list[int] = []
            for criterion in criteria:
                value = next(
                    (score.value for score in evaluation.scores if score.criterion_id == criterion.id),
                    None,
                )
                if value is not None and evaluation.status == "submitted":
                    criterion_values[criterion.key].append(value)
                    score_values.append(value)
            overall = _judge_overall(score_values) if evaluation.status == "submitted" else None
            if overall is not None:
                overalls.append(overall)
            if evaluation.status == "submitted" and evaluation.alignment in alignment_counts:
                alignment_counts[evaluation.alignment] += 1
            payload = evaluation_dict(evaluation, include_private=False)
            payload["judge_name"] = assignment.judge.name
            payload["overall"] = overall
            payload["audits"] = [
                _audit_dict(audit)
                for audit in sorted(evaluation.audits, key=lambda item: item.created_at or utcnow())
            ]
            evaluations.append(payload)

        averages = {}
        for criterion in criteria:
            values = criterion_values[criterion.key]
            averages[criterion.key] = round(sum(values) / len(values), 4) if values else None
        project_overall = round(sum(overalls) / len(overalls), 4) if overalls else None
        reasons: list[str] = []
        if len(overalls) >= 2 and max(overalls) - min(overalls) >= OVERALL_GAP:
            reasons.append(f"Overall scores range from {min(overalls):.2f} to {max(overalls):.2f}")
        for criterion in criteria:
            values = criterion_values[criterion.key]
            if len(values) >= 2 and max(values) - min(values) >= CRITERION_GAP:
                reasons.append(f"{criterion.name} ranges from {min(values)} to {max(values)}")

        rows.append(
            {
                "project_id": project.id,
                "project_name": project.name,
                "track_id": project.track_id,
                "track_name": project.track.name,
                "description": project.description,
                "team_members": list(project.team_members or []),
                "assigned_count": len(judges),
                "submitted_count": sum(1 for judge in judges if judge["status"] == "submitted"),
                "overall": project_overall,
                "rank": None,
                "criterion_averages": averages,
                "alignment_counts": alignment_counts,
                "discrepancy": bool(reasons),
                "discrepancy_reasons": reasons,
                "judges": judges,
                "evaluations": evaluations,
            }
        )

    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["track_id"]].append(row)
    for group in grouped.values():
        ranked = sorted(
            (row for row in group if row["overall"] is not None),
            key=lambda row: (-row["overall"], row["project_name"].lower()),
        )
        dense = 0
        previous: float | None = None
        for row in ranked:
            if previous is None or row["overall"] != previous:
                dense += 1
                previous = row["overall"]
            row["rank"] = dense

    rows.sort(key=lambda row: (row["track_name"].lower(), row["rank"] is None, row["rank"] or 0, row["project_name"].lower()))
    return {"criteria": [criterion_dict(criterion) for criterion in criteria], "projects": rows}


def progress_snapshot(db: Session) -> dict:
    refresh_all_timers(db)
    results = build_results(db)
    counts = {
        "projects": len(results["projects"]),
        "assignments": 0,
        "submitted": 0,
        "ready_to_submit": 0,
        "draft_saved": 0,
        "viewed": 0,
        "not_started": 0,
    }
    projects = []
    for row in results["projects"]:
        judges = []
        for judge in row["judges"]:
            counts["assignments"] += 1
            status = judge["status"]
            if status in counts:
                counts[status] += 1
            judges.append(
                {"judge_id": judge["judge_id"], "judge_name": judge["judge_name"], "status": status}
            )
        projects.append(
            {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "track_name": row["track_name"],
                "assigned_count": row["assigned_count"],
                "submitted_count": row["submitted_count"],
                "judges": judges,
            }
        )
    assignments = counts["assignments"]
    counts["completion_rate"] = (counts["submitted"] / assignments) if assignments else 0.0
    return {"counts": counts, "projects": projects}


def list_delays(db: Session) -> list[dict]:
    refresh_all_timers(db)
    now = utcnow()
    delays = []
    for project in load_projects(db):
        slot = project.schedule
        if slot is None or not project.assignments:
            continue
        end = slot.starts_at + timedelta(minutes=slot.duration_minutes + slot.extension_minutes)
        timers = [assignment.timer for assignment in project.assignments if assignment.timer is not None]
        any_started = any(timer.actual_started_at is not None for timer in timers)
        any_running = any(timer.status == "running" for timer in timers)
        if now > end and not any_started:
            kind, detail = "missed", "Scheduled time passed and no judge started a timer"
        elif any_running and now > end:
            kind, detail = "overrun", "A timer is still running past the scheduled end"
        elif now > slot.starts_at + timedelta(minutes=1) and not any_started:
            kind, detail = "late_start", "Presentation has not started"
        else:
            continue
        delays.append(
            {
                "project_id": project.id,
                "project_name": project.name,
                "track_name": project.track.name,
                "location": slot.location,
                "starts_at": slot.starts_at.isoformat(),
                "kind": kind,
                "detail": detail,
            }
        )
    delays.sort(key=lambda item: item["starts_at"])
    return delays


def cross_track_review(db: Session) -> dict:
    evaluations = db.scalars(
        select(Evaluation)
        .where(Evaluation.cross_track_flag.is_(True))
        .options(
            selectinload(Evaluation.project).selectinload(Project.track),
            selectinload(Evaluation.judge),
            selectinload(Evaluation.recommended_track),
        )
    ).all()
    grouped: dict[int, dict] = {}
    for evaluation in evaluations:
        project = evaluation.project
        bucket = grouped.get(project.id)
        if bucket is None:
            bucket = {
                "project_id": project.id,
                "project_name": project.name,
                "original_track": project.track.name,
                "flags": [],
            }
            grouped[project.id] = bucket
        bucket["flags"].append(
            {
                "evaluation_id": evaluation.id,
                "judge_id": evaluation.judge_id,
                "judge_name": evaluation.judge.name,
                "recommended_track": evaluation.recommended_track.name if evaluation.recommended_track else None,
                "explanation": evaluation.cross_track_explanation,
                "status": evaluation.status,
                "submitted": evaluation.status == "submitted",
            }
        )
    projects = list(grouped.values())
    for project in projects:
        project["flag_count"] = len({flag["judge_id"] for flag in project["flags"]})
        project["flags"].sort(key=lambda flag: flag["judge_name"].lower())
    projects.sort(key=lambda project: (-project["flag_count"], project["project_name"].lower()))
    return {"projects": projects}


def track_list(db: Session) -> list[Track]:
    return list(db.scalars(select(Track).order_by(Track.name)).all())
