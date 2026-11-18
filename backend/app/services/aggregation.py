"""Submitted-only aggregation, rankings, discrepancies, progress, and cross-track review.

Drafts, viewed, and ready-to-submit evaluations never affect averages or ranks.
Each project is ranked separately inside every prize category it belongs to.
Track alignment is reported beside the quality score and is not mixed into it.
"""

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Assignment, AuditEvent, DiscrepancyReview, Evaluation, Project, ProjectTrack, Score, Track
from app.serialize import criterion_dict, evaluation_dict
from app.services.discrepancies import analyze_project, get_settings, is_active, review_for, status_of
from app.services.evaluations import active_criteria, utcnow
from app.services.timers import get_timer_defaults, refresh_all_timers, sync_timer


def project_load_options():
    return (
        selectinload(Project.memberships).selectinload(ProjectTrack.track),
        selectinload(Project.timers),
        selectinload(Project.assignments).selectinload(Assignment.judge),
        selectinload(Project.assignments).selectinload(Assignment.track),
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
        selectinload(Project.assignments).selectinload(Assignment.evaluation).selectinload(Evaluation.track),
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
    settings = get_settings(db)
    reviews = {
        (row.project_id, row.track_id): row
        for row in db.scalars(select(DiscrepancyReview).options(selectinload(DiscrepancyReview.updated_by))).all()
    }
    rows: list[dict] = []
    for project in load_projects(db):
        for member in sorted(project.memberships, key=lambda item: item.track.name.lower()):
            if track_id is not None and member.track_id != track_id:
                continue
            track = member.track
            assignments = [item for item in project.assignments if item.track_id == member.track_id]
            judges = []
            evaluations = []
            overalls: list[float] = []
            criterion_values: dict[str, list[int]] = {criterion.key: [] for criterion in criteria}
            alignment_counts = {"strong": 0, "partial": 0, "weak": 0}
            for assignment in sorted(assignments, key=lambda item: item.judge.name.lower()):
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
                if evaluation is None or (evaluation.status == "not_started" and evaluation.submission_count <= 0):
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
            analysis = analyze_project(project, criteria, settings, member.track_id)
            review = reviews.get((project.id, member.track_id))
            active = is_active(analysis, review)
            rows.append(
                {
                    "project_id": project.id,
                    "project_name": project.name,
                    "track_id": track.id,
                    "track_name": track.name,
                    "description": project.description,
                    "team_members": list(project.team_members or []),
                    "table_location": project.table_location,
                    "presentation_order": member.presentation_order,
                    "assigned_count": len(judges),
                    "submitted_count": sum(1 for judge in judges if judge["status"] == "submitted"),
                    "overall": project_overall,
                    "rank": None,
                    "criterion_averages": averages,
                    "alignment_counts": alignment_counts,
                    "discrepancy": active,
                    "discrepancy_status": status_of(review) if analysis is not None else None,
                    "discrepancy_reasons": analysis["reasons"] if analysis is not None else [],
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


def _timer_for(project: Project, judge_id: int):
    for timer in project.timers:
        if timer.judge_id == judge_id:
            return timer
    return None


def _timer_summary(project: Project, judge_id: int, defaults) -> dict:
    timer = _timer_for(project, judge_id)
    if timer is None:
        return {
            "current_phase": "presentation",
            "presentation_status": "idle",
            "qa_status": "idle",
        }
    view = sync_timer(timer, project, defaults)
    return {
        "current_phase": view["current_phase"],
        "presentation_status": view["phases"]["presentation"]["status"],
        "qa_status": view["phases"]["qa"]["status"],
    }


def progress_snapshot(db: Session) -> dict:
    refresh_all_timers(db)
    defaults = get_timer_defaults(db)
    results = build_results(db)
    loaded = {project.id: project for project in load_projects(db)}
    counts = {
        "projects": len({row["project_id"] for row in results["projects"]}),
        "assignments": 0,
        "submitted": 0,
        "ready_to_submit": 0,
        "draft_saved": 0,
        "viewed": 0,
        "not_started": 0,
    }
    projects = []
    for row in results["projects"]:
        project = loaded.get(row["project_id"])
        judges = []
        for judge in row["judges"]:
            counts["assignments"] += 1
            status = judge["status"]
            if status in counts:
                counts[status] += 1
            summary = (
                _timer_summary(project, judge["judge_id"], defaults)
                if project is not None
                else {"current_phase": "presentation", "presentation_status": "idle", "qa_status": "idle"}
            )
            judges.append(
                {
                    "judge_id": judge["judge_id"],
                    "judge_name": judge["judge_name"],
                    "status": status,
                    **summary,
                }
            )
        projects.append(
            {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "track_id": row["track_id"],
                "track_name": row["track_name"],
                "assigned_count": row["assigned_count"],
                "submitted_count": row["submitted_count"],
                "judges": judges,
            }
        )
    assignments = counts["assignments"]
    counts["completion_rate"] = (counts["submitted"] / assignments) if assignments else 0.0
    return {"counts": counts, "projects": projects}


def cross_track_review(db: Session) -> dict:
    evaluations = db.scalars(
        select(Evaluation)
        .where(Evaluation.cross_track_flag.is_(True))
        .options(
            selectinload(Evaluation.project),
            selectinload(Evaluation.track),
            selectinload(Evaluation.judge),
            selectinload(Evaluation.recommended_track),
        )
    ).all()
    grouped: dict[tuple[int, int], dict] = {}
    for evaluation in evaluations:
        project = evaluation.project
        key = (project.id, evaluation.track_id)
        bucket = grouped.get(key)
        if bucket is None:
            bucket = {
                "project_id": project.id,
                "project_name": project.name,
                "original_track": evaluation.track.name if evaluation.track is not None else "",
                "track_id": evaluation.track_id,
                "flags": [],
            }
            grouped[key] = bucket
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
    projects.sort(key=lambda project: (-project["flag_count"], project["project_name"].lower(), project["original_track"].lower()))
    return {"projects": projects}


def track_list(db: Session) -> list[Track]:
    return list(db.scalars(select(Track).order_by(Track.name)).all())
