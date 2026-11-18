from app.models import Criterion, Evaluation, Project


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


def project_dict(project: Project) -> dict:
    memberships = sorted(project.memberships, key=lambda item: item.track.name.lower() if item.track is not None else "")
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "team_members": list(project.team_members or []),
        "github_url": project.github_url,
        "demo_url": project.demo_url,
        "video_url": project.video_url,
        "submission_url": project.submission_url,
        "table_location": project.table_location,
        "submitted_at": iso(project.submitted_at),
        "winner_label": project.winner_label,
        "presentation_minutes": project.presentation_minutes,
        "qa_minutes": project.qa_minutes,
        "presentation_extension_minutes": project.presentation_extension_minutes,
        "qa_extension_minutes": project.qa_extension_minutes,
        "tracks": [
            {
                "id": member.track_id,
                "name": member.track.name if member.track is not None else "",
                "presentation_order": member.presentation_order,
            }
            for member in memberships
        ],
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
        "track_id": evaluation.track_id,
        "track_name": evaluation.track.name if evaluation.track is not None else "",
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
        "reopened": evaluation.submission_count > 0 and evaluation.status != "submitted",
    }
    if include_private:
        data["private_notes"] = evaluation.private_notes
    return data
