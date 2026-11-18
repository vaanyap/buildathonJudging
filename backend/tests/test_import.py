from pathlib import Path

from tests.helpers import login, make_user

CSV = Path(__file__).resolve().parents[2] / "examples" / "submissions_export.csv"


def test_submissions_export_imports_multiple_tracks_and_is_idempotent(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    text = CSV.read_text(encoding="utf-8")
    preview = client.post(
        "/api/organizer/projects/import?dry_run=true",
        headers=organizer,
        files={"file": ("submissions_export.csv", text, "text/csv")},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["created"] == 21
    assert preview.json()["dry_run"] is True
    assert client.get("/api/organizer/projects", headers=organizer).json() == []

    imported = client.post(
        "/api/organizer/projects/import",
        headers=organizer,
        files={"file": ("submissions_export.csv", text, "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    body = imported.json()
    assert body["created"] == 21
    assert body["updated"] == 0
    assert body["skipped"] == []
    assert "Best use of True Markets Gateway API" in body["tracks"]
    assert "SpaceX Super Intelligence Track" in body["tracks"]
    assert len(body["tracks"]) == 5

    projects = client.get("/api/organizer/projects", headers=organizer).json()
    jarvis = next(project for project in projects if project["name"] == "JARVIS")
    assert sorted(track["name"] for track in jarvis["tracks"]) == [
        "Best Open-Source AI Project",
        "Best use of True Markets Gateway API",
        "Most creative use of ElevenLabs",
        "SpaceX Super Intelligence Track",
    ]
    assert jarvis["team_members"] == ["David Ren", "Stephen MO", "Kenny Tang"]
    assert jarvis["github_url"].startswith("https://github.com/")
    assert jarvis["demo_url"].startswith("https://")
    assert jarvis["video_url"] == ""
    assert jarvis["description"] == ""
    assert jarvis["table_location"] == ""
    assert jarvis["submission_url"].startswith("https://www.mlh.com/")
    assert jarvis["winner_label"] == ""
    assert jarvis["submitted_at"]

    liftr = next(project for project in projects if project["name"] == "LIFTR")
    assert liftr["demo_url"] == ""
    assert liftr["video_url"] == ""

    again = client.post(
        "/api/organizer/projects/import",
        headers=organizer,
        files={"file": ("submissions_export.csv", text, "text/csv")},
    )
    assert again.status_code == 200, again.text
    assert again.json()["created"] == 0
    assert again.json()["updated"] == 21
    assert len(client.get("/api/organizer/projects", headers=organizer).json()) == 21
    assert len(client.get("/api/organizer/tracks", headers=organizer).json()) == 5


def test_import_skips_a_row_without_categories(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    text = (
        "Project,Table,Prize categories,Winner,Team,Source,Demo,Video,Short description,Submitted at,Page\n"
        'No Prize,,,"",Ada,https://github.com/ada/nope,"","",Empty,2026-10-24T14:48:57-04:00,https://example.com/no-prize\n'
    )
    imported = client.post(
        "/api/organizer/projects/import",
        headers=organizer,
        files={"file": ("submissions_export.csv", text, "text/csv")},
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["created"] == 0
    assert imported.json()["skipped"][0]["message"] == "Prize categories are required"
    assert client.get("/api/organizer/projects", headers=organizer).json() == []
