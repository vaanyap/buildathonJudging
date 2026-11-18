from tests.helpers import evaluation_body, full_scores, login, make_user


HIGH = "HIGH-PRIVATE-NOTE"
LOW = "LOW-PRIVATE-NOTE"
MID = "MID-PRIVATE-NOTE"
QUIET = "UNFLAGGED-PRIVATE-NOTE"


def _setup(client, db):
    make_user(db, "organizer", "org@example.com", "Organizer")
    organizer = login(client, "org@example.com")
    track = client.post("/api/organizer/tracks", json={"name": "Open Source", "description": ""}, headers=organizer).json()
    other = client.post("/api/organizer/tracks", json={"name": "Voice", "description": ""}, headers=organizer).json()
    flagged = client.post(
        "/api/organizer/projects",
        json={"name": "JARVIS", "description": "", "track_ids": [track["id"]], "team_members": ["Ada"], "github_url": "", "demo_url": "", "submission_url": ""},
        headers=organizer,
    ).json()
    quiet = client.post(
        "/api/organizer/projects",
        json={"name": "CityLine", "description": "", "track_ids": [other["id"]], "team_members": ["Grace"], "github_url": "", "demo_url": "", "submission_url": ""},
        headers=organizer,
    ).json()
    judges = {}
    for name, email in (("Alex", "alex@example.com"), ("Maya", "maya@example.com"), ("Jordan", "jordan@example.com")):
        created = client.post(
            "/api/organizer/judges",
            json={"name": name, "email": email, "password": "password123"},
            headers=organizer,
        ).json()
        judges[name] = (created["id"], login(client, email))
    for name in judges:
        client.post(
            "/api/organizer/assignments",
            json={"judge_id": judges[name][0], "project_id": flagged["id"], "track_id": track["id"]},
            headers=organizer,
        )
    client.post(
        "/api/organizer/assignments",
        json={"judge_id": judges["Alex"][0], "project_id": quiet["id"], "track_id": other["id"]},
        headers=organizer,
    )
    return organizer, track, other, flagged, quiet, judges


def _submit(client, headers, project_id, track_id, scores, private_notes):
    saved = client.put(
        f"/api/judge/projects/{project_id}/tracks/{track_id}/evaluation",
        headers=headers,
        json=evaluation_body(scores=scores, private_notes=private_notes, formal_feedback="Public formal feedback."),
    )
    assert saved.status_code == 200, saved.text
    submitted = client.post(
        f"/api/judge/projects/{project_id}/tracks/{track_id}/evaluation/submit",
        headers=headers,
    )
    assert submitted.status_code == 200, submitted.text


def test_private_notes_are_visible_only_for_an_active_discrepancy(client, db):
    organizer, track, other, flagged, quiet, judges = _setup(client, db)
    _submit(client, judges["Alex"][1], flagged["id"], track["id"], full_scores(5), HIGH)
    _submit(client, judges["Maya"][1], flagged["id"], track["id"], full_scores(1), LOW)
    _submit(client, judges["Jordan"][1], flagged["id"], track["id"], full_scores(3), MID)
    _submit(client, judges["Alex"][1], quiet["id"], other["id"], full_scores(4), QUIET)

    listing = client.get("/api/organizer/discrepancies", headers=organizer)
    assert listing.status_code == 200, listing.text
    assert HIGH not in listing.text
    assert LOW not in listing.text
    assert MID not in listing.text
    assert QUIET not in listing.text
    assert len(listing.json()["projects"]) == 1
    assert listing.json()["projects"][0]["project_name"] == "JARVIS"

    detail = client.get(f"/api/organizer/discrepancies/{flagged['id']}/tracks/{track['id']}", headers=organizer)
    assert detail.status_code == 200, detail.text
    notes = {judge["judge_name"]: judge["private_notes"] for judge in detail.json()["judges"]}
    assert notes["Alex"] == HIGH
    assert notes["Maya"] == LOW
    assert notes["Jordan"] is None
    assert QUIET not in detail.text
    assert "technical" in detail.json()["divergent_criteria"]

    results = client.get("/api/organizer/results", headers=organizer).text
    feedback = client.get("/api/organizer/export/feedback.csv", headers=organizer).text
    exported = client.get("/api/organizer/export/results.csv", headers=organizer).text
    winners = client.get("/api/organizer/winners", headers=organizer).text
    winners_csv = client.get("/api/organizer/winners.csv", headers=organizer).text
    winners_md = client.get("/api/organizer/winners.md", headers=organizer).text
    for blob in (results, feedback, exported, winners, winners_csv, winners_md):
        assert HIGH not in blob
        assert LOW not in blob
        assert MID not in blob
        assert QUIET not in blob
    assert "Discrepancy" in winners or '"discrepancy": true' in winners or '"discrepancy":true' in winners

    hidden = client.get(f"/api/organizer/discrepancies/{quiet['id']}/tracks/{other['id']}", headers=organizer)
    assert hidden.status_code == 404

    audits = client.get("/api/organizer/results", headers=organizer).json()
    jarvis = next(row for row in audits["projects"] if row["project_name"] == "JARVIS")
    viewed = [
        audit
        for evaluation in jarvis["evaluations"]
        for audit in evaluation["audits"]
        if audit["action"] == "private_notes_viewed"
    ]
    assert len(viewed) == 2
    assert {audit["actor_name"] for audit in viewed} == {"Organizer"}

    resolved = client.patch(
        f"/api/organizer/discrepancies/{flagged['id']}/tracks/{track['id']}",
        headers=organizer,
        json={"status": "resolved", "note": "Talked with both judges."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["active"] is False
    after = client.get(f"/api/organizer/discrepancies/{flagged['id']}/tracks/{track['id']}", headers=organizer)
    assert after.status_code == 404
    marked = client.get("/api/organizer/results", headers=organizer).json()
    jarvis = next(row for row in marked["projects"] if row["project_name"] == "JARVIS")
    assert jarvis["discrepancy"] is False
