"""Detect score discrepancies and decide when organizers may see private notes.

A project is flagged from submitted evaluations only. The flag stays active
until an organizer marks it resolved. Private notes are revealed only for the
judges whose submitted scores create that active flag, and only from the
discrepancy detail used by organizers. Nothing else in the organizer API
should call `revealed_private_notes`.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Criterion, DiscrepancyReview, DiscrepancySettings, Evaluation, Project, User
from app.services.domain import DomainError
from app.services.evaluations import utcnow

STATUSES = ("open", "discussing", "resolved")
DEFAULT_OVERALL_SPREAD = 1.5
DEFAULT_HIGH_SCORE = 4
DEFAULT_LOW_SCORE = 2


def get_settings(db: Session) -> DiscrepancySettings:
    row = db.get(DiscrepancySettings, 1)
    if row is None:
        row = DiscrepancySettings(
            id=1,
            overall_spread=DEFAULT_OVERALL_SPREAD,
            high_score=DEFAULT_HIGH_SCORE,
            low_score=DEFAULT_LOW_SCORE,
            updated_at=utcnow(),
        )
        db.add(row)
        db.flush()
    return row


def review_for(db: Session, project_id: int, track_id: int) -> DiscrepancyReview | None:
    return db.scalar(
        select(DiscrepancyReview).where(
            DiscrepancyReview.project_id == project_id,
            DiscrepancyReview.track_id == track_id,
        )
    )


def status_of(review: DiscrepancyReview | None) -> str:
    if review is None:
        return "open"
    return review.status


def is_active(analysis: dict | None, review: DiscrepancyReview | None) -> bool:
    return analysis is not None and status_of(review) != "resolved"


def _score_value(evaluation: Evaluation, criterion: Criterion) -> int | None:
    for score in evaluation.scores:
        if score.criterion_id == criterion.id:
            return score.value
    return None


def _overall(scores: dict[str, int | None], criteria: list[Criterion]) -> float | None:
    values = [scores[criterion.key] for criterion in criteria if scores.get(criterion.key) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def analyze_project(
    project: Project,
    criteria: list[Criterion],
    settings: DiscrepancySettings,
    track_id: int,
) -> dict | None:
    judges = []
    assignments = [item for item in project.assignments if item.track_id == track_id]
    for assignment in sorted(assignments, key=lambda item: item.judge.name.lower()):
        evaluation = assignment.evaluation
        if evaluation is None or evaluation.status != "submitted":
            continue
        scores = {criterion.key: _score_value(evaluation, criterion) for criterion in criteria}
        judges.append(
            {
                "judge_id": assignment.judge_id,
                "judge_name": assignment.judge.name,
                "evaluation_id": evaluation.id,
                "scores": scores,
                "overall": _overall(scores, criteria),
                "alignment": evaluation.alignment,
                "alignment_explanation": evaluation.alignment_explanation,
                "formal_feedback": evaluation.formal_feedback,
                "_private_notes": evaluation.private_notes,
            }
        )
    scored = [judge for judge in judges if judge["overall"] is not None]
    if len(scored) < 2:
        return None

    involved: set[int] = set()
    reasons: list[str] = []
    divergent: list[str] = []
    overall_values = [judge["overall"] for judge in scored]
    low = min(overall_values)
    high = max(overall_values)
    spread = high - low
    if spread + 1e-9 >= float(settings.overall_spread):
        reasons.append(f"Overall scores range from {low:.2f} to {high:.2f}")
        for judge in scored:
            if abs(judge["overall"] - low) < 1e-9 or abs(judge["overall"] - high) < 1e-9:
                involved.add(judge["judge_id"])

    for criterion in criteria:
        pairs = [
            (judge["judge_id"], judge["scores"][criterion.key])
            for judge in judges
            if judge["scores"].get(criterion.key) is not None
        ]
        if len(pairs) < 2:
            continue
        highs = [judge_id for judge_id, value in pairs if value >= settings.high_score]
        lows = [judge_id for judge_id, value in pairs if value <= settings.low_score]
        if highs and lows:
            divergent.append(criterion.key)
            involved.update(highs)
            involved.update(lows)
            top = max(value for _judge_id, value in pairs)
            bottom = min(value for _judge_id, value in pairs)
            reasons.append(f"{criterion.name} ranges from {bottom} to {top}")

    if not reasons:
        return None
    return {
        "reasons": reasons,
        "divergent_criteria": divergent,
        "involved_judge_ids": sorted(involved),
        "overall_spread": round(spread, 4),
        "judges": judges,
    }


def apply_note_visibility(analysis: dict, review: DiscrepancyReview | None, reveal: bool) -> list[Evaluation]:
    """Attach private notes only for involved judges on an active flag.

    Returns the evaluations whose notes were actually included, so the caller
    can write the view audit. The raw note never stays on the payload when
    `reveal` is false.
    """
    active = is_active(analysis, review)
    involved = set(analysis["involved_judge_ids"])
    revealed_ids: list[int] = []
    for judge in analysis["judges"]:
        notes = judge.pop("_private_notes", "")
        visible = reveal and active and judge["judge_id"] in involved
        judge["private_notes"] = notes if visible else None
        judge["private_notes_visible"] = visible
        if visible and str(notes).strip():
            revealed_ids.append(judge["evaluation_id"])
    return revealed_ids


def log_private_note_views(db: Session, actor: User, project: Project, evaluation_ids: list[int]) -> None:
    now = utcnow()
    for evaluation_id in evaluation_ids:
        db.add(
            AuditEvent(
                evaluation_id=evaluation_id,
                actor_id=actor.id,
                action="private_notes_viewed",
                changes={"project_id": project.id},
                created_at=now,
            )
        )


def public_analysis(analysis: dict) -> dict:
    """Drop anything an organizer list or export must not carry."""
    judges = []
    for judge in analysis["judges"]:
        item = {key: value for key, value in judge.items() if key != "_private_notes"}
        item["private_notes"] = None
        item["private_notes_visible"] = False
        judges.append(item)
    return {**analysis, "judges": judges}


def save_review(db: Session, project: Project, track_id: int, actor: User, status: str, note: str) -> DiscrepancyReview:
    if status not in STATUSES:
        raise DomainError("Status must be open, discussing, or resolved.")
    row = review_for(db, project.id, track_id)
    now = utcnow()
    if row is None:
        row = DiscrepancyReview(
            project_id=project.id,
            track_id=track_id,
            status=status,
            note=note.strip(),
            updated_by_id=actor.id,
            created_at=now,
            updated_at=now,
        )
        row.updated_by = actor
        db.add(row)
    else:
        row.status = status
        row.note = note.strip()
        row.updated_by_id = actor.id
        row.updated_by = actor
        row.updated_at = now
    db.flush()
    return row
