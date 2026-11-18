"""Import an MLH/Devpost submissions export.

Prize categories become tracks. A project may belong to several. Re-import
matches an existing project by Page URL, or by project name when Page is empty.
"""

import csv
import io
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project, Track
from app.services.assignments import sync_project_tracks
from app.services.domain import DomainError
from app.services.evaluations import utcnow

EXPECTED = {
    "project",
    "table",
    "prize categories",
    "winner",
    "team",
    "source",
    "demo",
    "video",
    "short description",
    "submitted at",
    "page",
}


def _clean_url(value: str, label: str) -> str:
    value = value.strip()
    if value and not value.startswith(("http://", "https://")):
        raise DomainError(f"{label} must start with http:// or https://")
    return value


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in value.split(";") if part.strip()]


def _parse_submitted_at(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise DomainError("Submitted at must include a timezone offset")
    return parsed


def parse_submission_rows(text: str) -> tuple[list[dict], list[dict]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DomainError("CSV is missing a header row")
    headers = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = sorted(EXPECTED - set(headers))
    if missing:
        raise DomainError("CSV is missing columns: " + ", ".join(missing))
    rows: list[dict] = []
    skipped: list[dict] = []
    for index, raw in enumerate(reader, start=2):
        normalized = {(key or "").strip().lower(): (value or "").strip() for key, value in raw.items()}
        name = normalized.get("project", "")
        if not name:
            skipped.append({"row": index, "message": "Project name is required"})
            continue
        try:
            parsed = {
                "row": index,
                "name": name,
                "table_location": normalized.get("table", ""),
                "categories": _split_list(normalized.get("prize categories", "")),
                "winner_label": normalized.get("winner", ""),
                "team_members": _split_list(normalized.get("team", "")),
                "github_url": _clean_url(normalized.get("source", ""), "Source"),
                "demo_url": _clean_url(normalized.get("demo", ""), "Demo"),
                "video_url": _clean_url(normalized.get("video", ""), "Video"),
                "description": normalized.get("short description", ""),
                "submitted_at": _parse_submitted_at(normalized.get("submitted at", "")),
                "submission_url": _clean_url(normalized.get("page", ""), "Page"),
            }
        except DomainError as exc:
            skipped.append({"row": index, "message": exc.message})
            continue
        if not parsed["categories"]:
            skipped.append({"row": index, "project": name, "message": "Prize categories are required"})
            continue
        rows.append(parsed)
    return rows, skipped


def _track(db: Session, name: str, cache: dict[str, Track]) -> Track:
    key = name.lower()
    found = cache.get(key)
    if found is not None:
        return found
    found = db.scalar(select(Track).where(func.lower(Track.name) == key))
    if found is None:
        found = Track(name=name, description="")
        db.add(found)
        db.flush()
    cache[key] = found
    return found


def _find_project(db: Session, row: dict) -> Project | None:
    if row["submission_url"]:
        return db.scalar(select(Project).where(Project.submission_url == row["submission_url"]))
    return db.scalar(select(Project).where(func.lower(Project.name) == row["name"].lower()))


def import_submissions(db: Session, text: str, dry_run: bool = False) -> dict:
    parsed, skipped = parse_submission_rows(text)
    cache: dict[str, Track] = {}
    created = 0
    updated = 0
    warnings: list[dict] = []
    seen_pages: set[str] = set()
    seen_names: set[str] = set()
    checkpoint = db.begin_nested() if dry_run else None
    for row in parsed:
        page = row["submission_url"].lower()
        name_key = row["name"].lower()
        if page and page in seen_pages:
            skipped.append({"row": row["row"], "project": row["name"], "message": "Duplicate Page URL in this file"})
            continue
        if not page and name_key in seen_names:
            skipped.append({"row": row["row"], "project": row["name"], "message": "Duplicate project name in this file"})
            continue
        if page:
            seen_pages.add(page)
        seen_names.add(name_key)
        tracks = [_track(db, category, cache) for category in row["categories"]]
        project = _find_project(db, row)
        if project is None:
            project = Project(
                name=row["name"],
                description=row["description"],
                team_members=row["team_members"],
                github_url=row["github_url"],
                demo_url=row["demo_url"],
                video_url=row["video_url"],
                submission_url=row["submission_url"],
                table_location=row["table_location"],
                submitted_at=row["submitted_at"],
                winner_label=row["winner_label"],
                updated_at=utcnow(),
            )
            db.add(project)
            db.flush()
            created += 1
        else:
            project.name = row["name"]
            project.description = row["description"]
            project.team_members = row["team_members"]
            project.github_url = row["github_url"]
            project.demo_url = row["demo_url"]
            project.video_url = row["video_url"]
            project.submission_url = row["submission_url"]
            project.table_location = row["table_location"]
            project.submitted_at = row["submitted_at"]
            project.winner_label = row["winner_label"]
            project.updated_at = utcnow()
            updated += 1
        for message in sync_project_tracks(db, project, tracks):
            warnings.append({"row": row["row"], "project": project.name, "message": message})
    track_names = sorted({track.name for track in cache.values()})
    summary = {
        "created": created,
        "updated": updated,
        "tracks": track_names,
        "skipped": skipped,
        "warnings": warnings,
        "dry_run": dry_run,
    }
    if checkpoint is not None:
        checkpoint.rollback()
    return summary
