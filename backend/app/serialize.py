from app.models import Criterion, Evaluation, Project, ScheduleSlot


def iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def criterion_dict(criterion: Criterion) -> dict:
    return {
        "id": criterion.id,
        "key": criterion.key,
        "name": criterion.name,
        "description": criterion.description,
        "min_score": criterion.min_score,
        "max_score": criterion.max_score,
        "sort_order": criterion.sort_order,
        "required": criterion.required,
    }


def schedule_fields(slot: ScheduleSlot | None) -> dict:
    if slot is None:
        return {
            "starts_at": None,
            "duration_minutes": None,
            "extension_minutes": 0,
            "location": "",
        }
    return {
        "starts_at": iso(slot.starts_at),
        "duration_minutes": slot.duration_minutes,
        "extension_minutes": slot.extension_minutes,
        "location": slot.location,
    }


def project_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "track_id": project.track_id,
        "track_name": project.track.name if project.track is not None else "",
        "team_members": list(project.team_members or []),
        "github_url": project.github_url,
        "demo_url": project.demo_url,
        "submission_url": project.submission_url,
        **schedule_fields(project.schedule),
    }


def evaluation_dict(evaluation: Evaluation, include_private: bool) -> dict:
    ordered = sorted(
        evaluation.scores,
        key=lambda score: (
            score.criterion.sort_order if score.criterion is not None else 0,
            score.criterion_id,
        ),
    )
    scores = []
    for score in ordered:
        if score.criterion is None:
            continue
        scores.append(
            {
                "key": score.criterion.key,
                "name": score.criterion.name,
                "description": score.criterion.description,
                "min_score": score.criterion.min_score,
                "max_score": score.criterion.max_score,
                "value": score.value,
            }
        )
    data = {
        "id": evaluation.id,
        "project_id": evaluation.project_id,
        "judge_id": evaluation.judge_id,
        "status": evaluation.status,
        "formal_feedback": evaluation.formal_feedback,
        "alignment": evaluation.alignment,
        "alignment_explanation": evaluation.alignment_explanation,
        "cross_track_flag": evaluation.cross_track_flag,
        "recommended_track_id": evaluation.recommended_track_id,
        "recommended_track_name": evaluation.recommended_track.name if evaluation.recommended_track else None,
        "cross_track_explanation": evaluation.cross_track_explanation,
        "scores": scores,
        "submitted_at": iso(evaluation.submitted_at),
        "updated_at": iso(evaluation.updated_at),
        "submission_count": evaluation.submission_count,
        "revision": evaluation.revision,
        "reopened": evaluation.submission_count > 0 and evaluation.status != "submitted",
    }
    if include_private:
        data["private_notes"] = evaluation.private_notes
    return data
