import csv
import io
from pathlib import Path

from app.services.attendance import NameRecord, classify_member, normalize_name, pair_quality, parse_attendee_csv
from tests.helpers import evaluation_body, login, make_user

SIGN_IN = Path(__file__).resolve().parents[2] / "examples" / "sign_in.csv"
SUBMISSIONS = Path(__file__).resolve().parents[2] / "examples" / "submissions_export.csv"


def _person(row_id: int, first: str, last: str, email: str = "") -> NameRecord:
    return NameRecord(row_id, first, last, email or f"{row_id}@example.com")


def test_sign_in_headers_are_flexible_and_values_are_trimmed():
    parsed, skipped, warnings = parse_attendee_csv(
        "first_name,last_name,email\n  Ada ,  Lovelace , Ada@Example.com \n"
    )
    assert skipped == []
    assert warnings == []
    assert parsed == [{"row": 2, "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"}]

    short, _, _ = parse_attendee_csv("First,Last,Email\nGrace,Hopper,grace@example.com\n")
    assert short[0]["first_name"] == "Grace"
    given, _, _ = parse_attendee_csv("Given Name,Surname,E-mail\nKen,Thompson,ken@example.com\n")
    assert given[0]["email"] == "ken@example.com"

    duplicate, skipped, warnings = parse_attendee_csv(
        "First Name,Last Name,Email\nAda,Lovelace,ada@example.com\nAda,King,ADA@example.com\n"
    )
    assert skipped == []
    assert len(warnings) == 1
    assert duplicate == [{"row": 3, "first_name": "Ada", "last_name": "King", "email": "ada@example.com"}]

    try:
        parse_attendee_csv("First Name,Last Name,Email,Phone\nA,B,a@b.co,1\n")
    except Exception as exc:
        assert "exactly 3" in str(exc)
    else:
        raise AssertionError("extra column should be rejected")


def test_name_matching_rules():
    assert normalize_name("  José   García ") == "jose garcia"
    assert pair_quality("Valaya Choudhary", "Valaya Choudháry") == "attended"
    assert pair_quality("David   Ren", "david ren") == "attended"
    assert pair_quality("Ahbab Al Mohammad Siddiquee", "Ahbab Siddiquee") == "attended"
    assert pair_quality("David A. Ren", "David Ren") == "attended"
    assert pair_quality("Stephen MO", "Stephan Mo") == "possible"
    assert pair_quality("D Ren", "David Ren") == "possible"
    assert pair_quality("Romir Basu", "David Ren") is None

    exact = classify_member("David Ren", [_person(1, "David", "Ren"), _person(2, "Dave", "Ren")])
    assert exact["status"] == "attended"
    assert [person.id for person in exact["candidates"]] == [1]

    ambiguous = classify_member("Sam Lee", [_person(1, "Sam", "Lee"), _person(2, "Sam", "Lee", "other@example.com")])
    assert ambiguous["status"] == "possible"
    assert len(ambiguous["candidates"]) == 2

    missing = classify_member("Riaan Verma", [_person(1, "David", "Ren")])
    assert missing == {"status": "not_found", "candidates": []}


def _upload(client, headers, text: str, name: str = "sign_in.csv"):
    return client.post(
        "/api/organizer/attendees/import",
        headers=headers,
        files={"file": (name, text, "text/csv")},
    )


def _project(client, headers, name: str, members: list[str], track_id: int, submission_url: str = "") -> dict:
    response = client.post(
        "/api/organizer/projects",
        headers=headers,
        json={
            "name": name,
            "description": "",
            "track_ids": [track_id],
            "team_members": members,
            "github_url": "",
            "demo_url": "",
            "submission_url": submission_url,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_import_is_idempotent_and_sample_list_flags_the_expected_teams(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    submissions = client.post(
        "/api/organizer/projects/import",
        headers=organizer,
        files={"file": ("submissions_export.csv", SUBMISSIONS.read_text(encoding="utf-8"), "text/csv")},
    )
    assert submissions.status_code == 200, submissions.text

    text = SIGN_IN.read_text(encoding="utf-8")
    imported = _upload(client, organizer, text)
    assert imported.status_code == 200, imported.text
    summary = imported.json()
    assert summary["created"] == 38
    assert summary["skipped"] == []
    assert summary["total"] == 38

    again = _upload(client, organizer, text)
    assert again.status_code == 200, again.text
    assert again.json()["created"] == 0
    assert again.json()["updated"] == 0
    assert again.json()["unchanged"] == 38
    assert again.json()["removed"] == 0

    report = client.get("/api/organizer/attendance", headers=organizer)
    assert report.status_code == 200, report.text
    projects = {item["project_name"]: item for item in report.json()["projects"]}

    jarvis = {member["name"]: member for member in projects["JARVIS"]["members"]}
    assert jarvis["David Ren"]["status"] == "attended"
    assert jarvis["David Ren"]["label"] == "Signed in"
    assert jarvis["Stephen MO"]["status"] == "possible"
    assert jarvis["Stephen MO"]["label"] == "Possible match"
    assert jarvis["Stephen MO"]["present"] is False
    assert projects["JARVIS"]["flagged"] is True

    liftr = projects["LIFTR"]
    assert liftr["fully_absent"] is True
    assert liftr["flagged"] is True
    assert {member["label"] for member in liftr["members"]} == {"Not signed in"}

    assert projects["Hodlings"]["fully_absent"] is True
    fallguard = projects["the-kitchen-table (Project 1): FallGuard"]["members"][0]
    assert fallguard["status"] == "attended"
    assert fallguard["label"] == "Signed in"
    cityline = {member["name"]: member for member in projects["CityLine"]["members"]}
    assert cityline["Valaya Choudhary"]["status"] == "attended"
    assert cityline["Romir Basu"]["label"] == "Not signed in"
    assert projects["perps.fun"]["flagged"] is False

    replaced = _upload(
        client,
        organizer,
        "First Name,Last Name,Email\nDavid,Ren,david.ren@nyu.edu\n",
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["removed"] == 37
    assert replaced.json()["unchanged"] == 1


def test_overrides_survive_sign_in_and_submission_reimports(client, db):
    make_user(db, "organizer", "org@example.com", "Avery Shah")
    organizer = login(client, "org@example.com")
    track = client.post("/api/organizer/tracks", json={"name": "Open", "description": ""}, headers=organizer)
    assert track.status_code == 201, track.text
    track_id = track.json()["id"]
    project = _project(
        client,
        organizer,
        "Ghost",
        ["Ghost Person", "Ada Lovelace"],
        track_id,
        "https://example.com/ghost",
    )
    first = _upload(
        client,
        organizer,
        "first_name,last_name,email\nAda,Lovelace,ada@example.com\nBob,Builder,bob@example.com\n",
    )
    assert first.status_code == 200, first.text

    report = client.get("/api/organizer/attendance", headers=organizer).json()
    listed = {person["email"]: person for person in report["attendees"]}
    linked = client.put(
        "/api/organizer/attendance/members",
        headers=organizer,
        json={"name": "Ghost Person", "decision": "attended", "attendee_id": listed["bob@example.com"]["id"]},
    )
    assert linked.status_code == 200, linked.text
    ghost = next(member for member in linked.json()["projects"][0]["members"] if member["name"] == "Ghost Person")
    assert ghost["label"] == "Manually confirmed"
    assert ghost["present"] is True
    assert ghost["matched_attendee"]["email"] == "bob@example.com"
    assert ghost["override"]["updated_by"] == "Avery Shah"

    replaced = _upload(
        client,
        organizer,
        "Email,First,Last\n BOB@example.com ,Robert,Builder\nada@example.com,Ada,Lovelace\n",
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["updated"] == 1
    assert replaced.json()["removed"] == 0
    after = client.get("/api/organizer/attendance", headers=organizer).json()
    ghost = next(member for item in after["projects"] if item["project_id"] == project["id"] for member in item["members"] if member["name"] == "Ghost Person")
    assert ghost["label"] == "Manually confirmed"
    assert ghost["matched_attendee"]["name"] == "Robert Builder"
    assert ghost["matched_attendee"]["id"] == listed["bob@example.com"]["id"]

    submissions = (
        "Project,Table,Prize categories,Winner,Team,Source,Demo,Video,Short description,Submitted at,Page\n"
        'Ghost,,Open,"",Ghost Person; Ada Lovelace,,,,,2026-10-24T14:48:57-04:00,https://example.com/ghost\n'
    )
    imported = client.post(
        "/api/organizer/projects/import",
        headers=organizer,
        files={"file": ("projects.csv", submissions, "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    still = client.get("/api/organizer/attendance", headers=organizer).json()
    ghost = next(member for item in still["projects"] if item["project_name"] == "Ghost" for member in item["members"] if member["name"] == "Ghost Person")
    assert ghost["status"] == "confirmed"
    assert ghost["override"]["attendee_id"] == listed["bob@example.com"]["id"]

    absent = client.put(
        "/api/organizer/attendance/members",
        headers=organizer,
        json={"name": "  Ada   Lovelace ", "decision": "absent"},
    )
    assert absent.status_code == 200, absent.text
    ada = next(member for item in absent.json()["projects"] if item["project_name"] == "Ghost" for member in item["members"] if "Ada" in member["name"])
    assert ada["label"] == "Manually confirmed"
    assert ada["detail"] == "Marked not signed in"
    assert ada["present"] is False
    kept = client.get("/api/organizer/attendance", headers=organizer).json()
    ada = next(member for item in kept["projects"] if item["project_name"] == "Ghost" for member in item["members"] if member["name"] == "Ada Lovelace")
    assert ada["present"] is False


def test_review_is_audited_and_does_not_change_rank(client, db):
    make_user(db, "organizer", "org@example.com", "Avery Shah")
    organizer = login(client, "org@example.com")
    track = client.post("/api/organizer/tracks", json={"name": "Open", "description": ""}, headers=organizer)
    track_id = track.json()["id"]
    project = _project(client, organizer, "Quiz", ["Nobody Signed"], track_id)
    other = _project(client, organizer, "Anchor", ["Ada Lovelace"], track_id)
    _upload(client, organizer, "First,Last,Email\nAda,Lovelace,ada@example.com\n")
    judge = client.post(
        "/api/organizer/judges",
        headers=organizer,
        json={"name": "Maya Chen", "email": "maya@example.com", "password": "password123"},
    )
    assert judge.status_code == 201, judge.text
    for row in (project, other):
        assigned = client.post(
            "/api/organizer/assignments",
            headers=organizer,
            json={"judge_id": judge.json()["id"], "project_id": row["id"], "track_id": track_id},
        )
        assert assigned.status_code == 201, assigned.text
    maya = login(client, "maya@example.com")
    for row, score in ((project, 5), (other, 3)):
        saved = client.put(
            f"/api/judge/projects/{row['id']}/tracks/{track_id}/evaluation",
            headers=maya,
            json=evaluation_body(scores={"technical": score, "innovation": score, "impact": score, "presentation": score}),
        )
        assert saved.status_code == 200, saved.text
        submitted = client.post(
            f"/api/judge/projects/{row['id']}/tracks/{track_id}/evaluation/submit",
            headers=maya,
        )
        assert submitted.status_code == 200, submitted.text

    before = {item["project_name"]: item for item in client.get("/api/organizer/results", headers=organizer).json()["projects"]}
    assert before["Quiz"]["attendance_flagged"] is True
    assert before["Quiz"]["rank"] == 1
    assert before["Quiz"]["overall"] == 5
    assert before["Anchor"]["attendance_flagged"] is False
    assert before["Anchor"]["rank"] == 2

    review = client.patch(
        f"/api/organizer/projects/{project['id']}/attendance",
        headers=organizer,
        json={"status": "disqualified", "note": "Submitted without signing in"},
    )
    assert review.status_code == 200, review.text
    assert review.json()["review"]["status"] == "disqualified"
    assert review.json()["review"]["label"] == "Disqualified-note"
    assert review.json()["review"]["updated_by"] == "Avery Shah"
    assert review.json()["review"]["note"] == "Submitted without signing in"
    assert review.json()["audits"][-1]["actor_name"] == "Avery Shah"
    assert review.json()["audits"][-1]["changes"]["status"]["new"] == "disqualified"
    assert review.json()["flagged"] is True

    after = {item["project_name"]: item for item in client.get("/api/organizer/results", headers=organizer).json()["projects"]}
    assert after["Quiz"]["rank"] == before["Quiz"]["rank"]
    assert after["Quiz"]["overall"] == before["Quiz"]["overall"]
    assert after["Anchor"]["rank"] == before["Anchor"]["rank"]
    winners = client.get("/api/organizer/winners?per_track=2", headers=organizer).json()
    names = [winner["project_name"] for winner in winners["tracks"][0]["winners"]]
    assert names == ["Quiz", "Anchor"]
    assert winners["tracks"][0]["winners"][0]["attendance_flagged"] is True

    exported = client.get("/api/organizer/export/results.csv", headers=organizer)
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    quiz_rows = [row for row in rows if row["project"] == "Quiz"]
    assert quiz_rows[0]["sign_in_flagged"] == "yes"
    assert quiz_rows[0]["sign_in_review"] == "Disqualified-note"
    assert "Submitted without signing in" in quiz_rows[0]["sign_in_note"]
    assert "Nobody Signed: Not signed in" in quiz_rows[0]["sign_in_members"]
    anchor_rows = [row for row in rows if row["project"] == "Anchor"]
    assert "Ada Lovelace: Signed in" in anchor_rows[0]["sign_in_members"]


def test_sign_in_endpoints_are_organizer_only(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    make_user(db, "judge", "judge@example.com", "Judge")
    organizer = login(client, "org@example.com")
    judge = login(client, "judge@example.com")
    anonymous = client.get("/api/organizer/attendance")
    assert anonymous.status_code == 401

    assert client.get("/api/organizer/attendance", headers=judge).status_code == 403
    denied_import = _upload(client, judge, "First,Last,Email\nAda,Lovelace,ada@example.com\n")
    assert denied_import.status_code == 403
    denied_member = client.put(
        "/api/organizer/attendance/members",
        headers=judge,
        json={"name": "Ada Lovelace", "decision": "attended"},
    )
    assert denied_member.status_code == 403
    denied_review = client.patch(
        "/api/organizer/projects/1/attendance",
        headers=judge,
        json={"status": "reviewed_ok", "note": "no"},
    )
    assert denied_review.status_code == 403

    allowed = _upload(client, organizer, "First,Last,Email\nAda,Lovelace,ada@example.com\n")
    assert allowed.status_code == 200, allowed.text
    assert client.get("/api/organizer/attendance", headers=organizer).status_code == 200
