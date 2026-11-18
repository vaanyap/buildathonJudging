"""Match submitted team names to the day-of sign-in list.

Only registered people can sign in, and only signed-in people should submit.
The list organizers upload is who actually signed in (first name, last name,
email). The submissions export only has names, so each teammate is matched on
a normalized full name: case, extra whitespace, and accents do not matter, and
a unique first-and-last pair still matches when middle names or middle initials
differ. A fuzzy or ambiguous comparison is a possible match, not signed in.

A project is flagged when any teammate is missing from that list, so organizers
can review someone who may have submitted without signing in. Overrides are
stored by the normalized name and survive a new sign-in upload or a submissions
re-import. The flag does not change scores, rank, or eligibility.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import AttendanceEvent, AttendanceOverride, AttendanceReview, Attendee, Project, User
from app.services.domain import DomainError
from app.services.evaluations import utcnow

STATUS_ATTENDED = "attended"
STATUS_POSSIBLE = "possible"
STATUS_NOT_FOUND = "not_found"
STATUS_CONFIRMED = "confirmed"
MEMBER_STATUSES = (STATUS_ATTENDED, STATUS_POSSIBLE, STATUS_NOT_FOUND, STATUS_CONFIRMED)
STATUS_LABELS = {
    STATUS_ATTENDED: "Signed in",
    STATUS_POSSIBLE: "Possible match",
    STATUS_NOT_FOUND: "Not signed in",
    STATUS_CONFIRMED: "Manually confirmed",
}

REVIEW_OPEN = "open"
REVIEW_OK = "reviewed_ok"
REVIEW_DISQUALIFIED = "disqualified"
REVIEW_STATUSES = (REVIEW_OPEN, REVIEW_OK, REVIEW_DISQUALIFIED)
REVIEW_LABELS = {
    REVIEW_OPEN: "Open",
    REVIEW_OK: "Reviewed OK",
    REVIEW_DISQUALIFIED: "Disqualified-note",
}

DECISION_ATTENDED = "attended"
DECISION_ABSENT = "absent"
DECISION_CLEAR = "clear"

_FIRST_HEADERS = {"first", "first name", "firstname", "given", "given name"}
_LAST_HEADERS = {"last", "last name", "lastname", "surname", "family", "family name"}
_EMAIL_HEADERS = {"email", "e-mail", "email address", "e mail"}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_COMBINING = re.compile(r"[\u0300-\u036f]")


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = _COMBINING.sub("", text)
    text = text.replace("'", "").replace("’", "").replace("`", "")
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _header_key(value: str) -> str:
    text = (value or "").replace("\ufeff", "").strip().lower().replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


def _clean_person(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for index, char in enumerate(left, start=1):
        current = [index]
        for other_index, other in enumerate(right, start=1):
            current.append(
                min(
                    current[other_index - 1] + 1,
                    previous[other_index] + 1,
                    previous[other_index - 1] + (char != other),
                )
            )
        previous = current
    return previous[-1]


def _similar_token(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) >= 4 and len(right) >= 4 and _levenshtein(left, right) <= 1:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 3 and long.startswith(short) and len(long) - len(short) <= 3


def _significant(tokens: list[str]) -> list[str]:
    kept = [token for token in tokens if len(token) > 1]
    return kept or tokens


def pair_quality(member_name: str, attendee_name: str) -> str | None:
    """Return attended, possible, or None for one submitted name against one attendee."""
    member = normalize_name(member_name)
    attendee = normalize_name(attendee_name)
    if not member or not attendee:
        return None
    if member == attendee:
        return STATUS_ATTENDED
    member_tokens = member.split()
    attendee_tokens = attendee.split()
    member_sig = _significant(member_tokens)
    attendee_sig = _significant(attendee_tokens)
    if member_sig and member_sig == attendee_sig:
        return STATUS_ATTENDED
    if member_sig and sorted(member_sig) == sorted(attendee_sig):
        return STATUS_ATTENDED
    if (
        len(member_sig) >= 2
        and len(attendee_sig) >= 2
        and member_sig[0] == attendee_sig[0]
        and member_sig[-1] == attendee_sig[-1]
    ):
        return STATUS_ATTENDED
    if _fuzzy(member, attendee, member_tokens, attendee_tokens, member_sig, attendee_sig):
        return STATUS_POSSIBLE
    return None


def _fuzzy(
    member: str,
    attendee: str,
    member_tokens: list[str],
    attendee_tokens: list[str],
    member_sig: list[str],
    attendee_sig: list[str],
) -> bool:
    if len(member) >= 6 and len(attendee) >= 6:
        # Local import keeps difflib off the exact-match path.
        from difflib import SequenceMatcher

        if SequenceMatcher(None, member, attendee).ratio() >= 0.92:
            return True
    if len(member_sig) >= 2 and len(attendee_sig) >= 2:
        first_close = _similar_token(member_sig[0], attendee_sig[0])
        last_close = _similar_token(member_sig[-1], attendee_sig[-1])
        if first_close and last_close and (member_sig[0] != attendee_sig[0] or member_sig[-1] != attendee_sig[-1]):
            return True
    if _initial_and_last(member_tokens, attendee_sig):
        return True
    if _initial_and_last(attendee_tokens, member_sig):
        return True
    return False


def _initial_and_last(raw_tokens: list[str], other_sig: list[str]) -> bool:
    if len(raw_tokens) < 2 or len(other_sig) < 2:
        return False
    if len(raw_tokens[0]) == 1 and raw_tokens[-1] == other_sig[-1]:
        return other_sig[0].startswith(raw_tokens[0])
    if len(raw_tokens[-1]) == 1 and raw_tokens[0] == other_sig[0]:
        return other_sig[-1].startswith(raw_tokens[-1])
    return False


def attendee_full_name(attendee: Attendee | NameRecord) -> str:
    return _clean_person(f"{attendee.first_name} {attendee.last_name}")


@dataclass(frozen=True)
class NameRecord:
    id: int
    first_name: str
    last_name: str
    email: str = ""


def classify_member(member_name: str, attendees: list) -> dict:
    confident: list = []
    fuzzy: list = []
    for attendee in attendees:
        quality = pair_quality(member_name, attendee_full_name(attendee))
        if quality == STATUS_ATTENDED:
            confident.append(attendee)
        elif quality == STATUS_POSSIBLE:
            fuzzy.append(attendee)
    if len(confident) == 1:
        return {"status": STATUS_ATTENDED, "candidates": confident}
    if len(confident) > 1:
        return {"status": STATUS_POSSIBLE, "candidates": confident}
    if fuzzy:
        return {"status": STATUS_POSSIBLE, "candidates": fuzzy}
    return {"status": STATUS_NOT_FOUND, "candidates": []}


def parse_attendee_csv(text: str) -> tuple[list[dict], list[dict], list[dict]]:
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise DomainError("CSV is missing a header row")
    headers = list(reader.fieldnames)
    if len(headers) != 3:
        raise DomainError("Day-of sign-in CSV must have exactly 3 columns: first name, last name, and email")
    roles: dict[str, str] = {}
    unknown: list[str] = []
    for header in headers:
        key = _header_key(header)
        if key in _FIRST_HEADERS:
            role = "first"
        elif key in _LAST_HEADERS:
            role = "last"
        elif key in _EMAIL_HEADERS:
            role = "email"
        else:
            unknown.append(header.strip() or "(blank)")
            continue
        if role in roles:
            raise DomainError("Day-of sign-in CSV repeats a column. Expected first name, last name, and email")
        roles[role] = header
    if unknown or set(roles) != {"first", "last", "email"}:
        found = ", ".join(header.strip() or "(blank)" for header in headers)
        raise DomainError(f"Unrecognized sign-in columns ({found}). Expected first name, last name, and email")

    rows: list[dict] = []
    skipped: list[dict] = []
    warnings: list[dict] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(reader, start=2):
        first = _clean_person(raw.get(roles["first"]) or "")
        last = _clean_person(raw.get(roles["last"]) or "")
        email = (raw.get(roles["email"]) or "").strip().lower()
        if not first and not last and not email:
            continue
        if not first or not last:
            skipped.append({"row": index, "message": "First name and last name are required"})
            continue
        if not _EMAIL.match(email):
            skipped.append({"row": index, "email": email, "message": "Email must look like name@example.com"})
            continue
        if email in seen:
            warnings.append({"row": seen[email], "email": email, "message": "Duplicate email; kept the later row"})
        seen[email] = index
        rows.append({"row": index, "first_name": first, "last_name": last, "email": email})
    deduped: dict[str, dict] = {}
    for row in rows:
        deduped[row["email"]] = row
    return list(deduped.values()), skipped, warnings


def import_attendees(db: Session, text: str) -> dict:
    rows, skipped, warnings = parse_attendee_csv(text)
    if not rows and skipped:
        raise DomainError("No valid sign-in rows. The current list was left unchanged.")
    existing = {attendee.email: attendee for attendee in db.scalars(select(Attendee)).all()}
    seen: set[str] = set()
    created = updated = unchanged = 0
    now = utcnow()
    for row in rows:
        seen.add(row["email"])
        current = existing.get(row["email"])
        if current is None:
            db.add(
                Attendee(
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    email=row["email"],
                    updated_at=now,
                )
            )
            created += 1
            continue
        if current.first_name != row["first_name"] or current.last_name != row["last_name"]:
            current.first_name = row["first_name"]
            current.last_name = row["last_name"]
            current.updated_at = now
            updated += 1
        else:
            unchanged += 1
    removed = 0
    for email, attendee in existing.items():
        if email in seen:
            continue
        db.delete(attendee)
        removed += 1
    db.flush()
    return {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "removed": removed,
        "total": created + updated + unchanged,
        "skipped": skipped,
        "warnings": warnings,
    }


def _attendee_dict(attendee: Attendee | None) -> dict | None:
    if attendee is None:
        return None
    return {
        "id": attendee.id,
        "first_name": attendee.first_name,
        "last_name": attendee.last_name,
        "email": attendee.email,
        "name": attendee_full_name(attendee),
    }


def load_attendance_index(db: Session) -> dict:
    attendees = list(db.scalars(select(Attendee).order_by(Attendee.last_name, Attendee.first_name, Attendee.email)).all())
    overrides = list(
        db.scalars(
            select(AttendanceOverride).options(
                selectinload(AttendanceOverride.updated_by),
                selectinload(AttendanceOverride.attendee),
            )
        ).all()
    )
    reviews = list(
        db.scalars(select(AttendanceReview).options(selectinload(AttendanceReview.updated_by))).all()
    )
    events = list(
        db.scalars(
            select(AttendanceEvent).options(selectinload(AttendanceEvent.actor)).order_by(AttendanceEvent.id)
        ).all()
    )
    return {
        "attendees": attendees,
        "by_id": {attendee.id: attendee for attendee in attendees},
        "overrides": {row.member_key: row for row in overrides},
        "reviews": {row.project_id: row for row in reviews},
        "events": events,
    }


def _event_dict(event: AttendanceEvent) -> dict:
    return {
        "id": event.id,
        "action": event.action,
        "actor_name": event.actor.name if event.actor is not None else None,
        "member_key": event.member_key,
        "project_id": event.project_id,
        "changes": event.changes,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _review_dict(review: AttendanceReview | None) -> dict:
    status = review.status if review is not None else REVIEW_OPEN
    if status not in REVIEW_LABELS:
        status = REVIEW_OPEN
    return {
        "status": status,
        "label": REVIEW_LABELS[status],
        "note": review.note if review is not None else "",
        "updated_by": review.updated_by.name if review is not None and review.updated_by is not None else None,
        "updated_at": review.updated_at.isoformat() if review is not None and review.updated_at else None,
    }


def describe_member(name: str, index: dict) -> dict:
    cleaned = _clean_person(name)
    key = normalize_name(cleaned)
    automatic = classify_member(cleaned, index["attendees"])
    candidates = [_attendee_dict(attendee) for attendee in automatic["candidates"]]
    override: AttendanceOverride | None = index["overrides"].get(key)
    matched = None
    detail = ""
    if override is not None and override.decision == DECISION_ABSENT:
        status = STATUS_CONFIRMED
        present = False
        resolution = DECISION_ABSENT
        detail = "Marked not signed in"
    elif override is not None:
        status = STATUS_CONFIRMED
        present = True
        resolution = DECISION_ATTENDED
        linked = override.attendee if override.attendee_id else None
        matched = _attendee_dict(linked)
        detail = f"Linked to {matched['name']}" if matched else "Marked signed in"
    else:
        status = automatic["status"]
        present = status == STATUS_ATTENDED
        resolution = status
        if status == STATUS_ATTENDED and automatic["candidates"]:
            matched = _attendee_dict(automatic["candidates"][0])
        elif status == STATUS_POSSIBLE and len(automatic["candidates"]) > 1:
            detail = "More than one signed-in person matches"
        elif status == STATUS_POSSIBLE and automatic["candidates"]:
            detail = f"Similar to {attendee_full_name(automatic['candidates'][0])}"
    override_payload = None
    if override is not None:
        override_payload = {
            "decision": override.decision,
            "attendee_id": override.attendee_id,
            "updated_by": override.updated_by.name if override.updated_by is not None else None,
            "updated_at": override.updated_at.isoformat() if override.updated_at else None,
        }
    return {
        "name": cleaned,
        "member_key": key,
        "status": status,
        "label": STATUS_LABELS[status],
        "present": present,
        "resolution": resolution,
        "detail": detail,
        "matched_attendee": matched,
        "candidates": [item for item in candidates if item is not None],
        "override": override_payload,
    }


def describe_project(project: Project, index: dict) -> dict:
    members = [describe_member(str(name), index) for name in (project.team_members or []) if str(name).strip()]
    missing = [member["name"] for member in members if not member["present"]]
    flagged = bool(missing)
    fully_absent = bool(members) and all(not member["present"] for member in members)
    keys = {member["member_key"] for member in members}
    audits = [
        _event_dict(event)
        for event in index["events"]
        if event.project_id == project.id or (event.member_key and event.member_key in keys)
    ]
    tracks = []
    for member in sorted(project.memberships, key=lambda item: item.track.name.lower() if item.track is not None else ""):
        tracks.append({"id": member.track_id, "name": member.track.name if member.track is not None else ""})
    return {
        "project_id": project.id,
        "project_name": project.name,
        "tracks": tracks,
        "flagged": flagged,
        "fully_absent": fully_absent,
        "missing_members": missing,
        "members": members,
        "review": _review_dict(index["reviews"].get(project.id)),
        "audits": audits,
    }


def attendance_report(db: Session, projects: list[Project] | None = None) -> dict:
    if projects is None:
        projects = list(db.scalars(select(Project).options(selectinload(Project.memberships))).all())
        for project in projects:
            for membership in project.memberships:
                _ = membership.track
    index = load_attendance_index(db)
    described = [describe_project(project, index) for project in projects]

    def _sort_key(item: dict) -> tuple:
        has_possible = any(member["status"] == STATUS_POSSIBLE for member in item["members"])
        return (not item["flagged"], not item["fully_absent"], not has_possible, item["project_name"].lower())

    described.sort(key=_sort_key)
    return {
        "attendee_count": len(index["attendees"]),
        "flagged_count": sum(1 for item in described if item["flagged"]),
        "attendees": [_attendee_dict(attendee) for attendee in index["attendees"]],
        "projects": described,
    }


def _record_event(db: Session, actor: User, action: str, changes: dict, project_id: int | None, member_key: str) -> None:
    db.add(
        AttendanceEvent(
            actor_id=actor.id,
            action=action,
            project_id=project_id,
            member_key=member_key,
            changes=changes,
            created_at=utcnow(),
        )
    )


def save_member_override(
    db: Session,
    actor: User,
    name: str,
    decision: str,
    attendee_id: int | None,
) -> None:
    if decision not in {DECISION_ATTENDED, DECISION_ABSENT, DECISION_CLEAR}:
        raise DomainError("Decision must be attended, absent, or clear")
    cleaned = _clean_person(name)
    key = normalize_name(cleaned)
    if not key:
        raise DomainError("Team member name is required")
    if decision == DECISION_ABSENT and attendee_id is not None:
        raise DomainError("Mark the member not signed in, or link someone from the sign-in list. Not both.")
    if decision == DECISION_CLEAR:
        attendee_id = None
    elif decision != DECISION_ATTENDED:
        attendee_id = None
    attendee = None
    if attendee_id is not None:
        attendee = db.get(Attendee, attendee_id)
        if attendee is None:
            raise DomainError("That person is not on the sign-in list", 404)
    existing = db.scalar(select(AttendanceOverride).where(AttendanceOverride.member_key == key))
    previous = None
    if existing is not None:
        previous = {"decision": existing.decision, "attendee_id": existing.attendee_id, "name": existing.display_name}
    if decision == DECISION_CLEAR:
        if existing is None:
            return
        db.delete(existing)
        _record_event(
            db,
            actor,
            "override",
            {"name": cleaned, "decision": {"old": previous["decision"] if previous else None, "new": DECISION_CLEAR}},
            None,
            key,
        )
        db.flush()
        return
    now = utcnow()
    if existing is None:
        existing = AttendanceOverride(
            member_key=key,
            display_name=cleaned,
            decision=decision,
            attendee_id=attendee.id if attendee is not None else None,
            updated_by_id=actor.id,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.display_name = cleaned
        existing.decision = decision
        existing.attendee_id = attendee.id if attendee is not None else None
        existing.updated_by_id = actor.id
        existing.updated_at = now
    _record_event(
        db,
        actor,
        "override",
        {
            "name": cleaned,
            "decision": {"old": previous["decision"] if previous else None, "new": decision},
            "attendee_id": {"old": previous["attendee_id"] if previous else None, "new": existing.attendee_id},
        },
        None,
        key,
    )
    db.flush()


def save_project_review(db: Session, actor: User, project: Project, status: str, note: str) -> AttendanceReview:
    if status not in REVIEW_STATUSES:
        raise DomainError("Review status must be open, reviewed_ok, or disqualified")
    review = db.scalar(select(AttendanceReview).where(AttendanceReview.project_id == project.id))
    previous_status = review.status if review is not None else REVIEW_OPEN
    previous_note = review.note if review is not None else ""
    cleaned = note.strip()
    now = utcnow()
    if review is None:
        review = AttendanceReview(
            project_id=project.id,
            status=status,
            note=cleaned,
            updated_by_id=actor.id,
            updated_at=now,
        )
        db.add(review)
    else:
        review.status = status
        review.note = cleaned
        review.updated_by_id = actor.id
        review.updated_at = now
    _record_event(
        db,
        actor,
        "review",
        {
            "status": {"old": previous_status, "new": status},
            "note": {"old": previous_note, "new": cleaned},
        },
        project.id,
        "",
    )
    db.flush()
    return review
