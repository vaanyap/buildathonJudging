from fastapi.testclient import TestClient

from app.auth import hash_password
from app.models import User


def make_user(db, role: str, email: str, name: str, password: str = "password123") -> User:
    user = User(
        email=email,
        name=name,
        password_hash=hash_password(password),
        role=role,
        active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def login(client: TestClient, email: str, password: str = "password123") -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def full_scores(value: int = 4) -> dict[str, int]:
    return {"technical": value, "innovation": value, "impact": value, "presentation": value}


def evaluation_body(**overrides) -> dict:
    body = {
        "scores": full_scores(),
        "private_notes": "",
        "formal_feedback": "Clear demo and a specific user.",
        "alignment": "strong",
        "alignment_explanation": "The project does the job this track is for.",
        "cross_track_flag": False,
        "recommended_track_id": None,
        "cross_track_explanation": "",
    }
    body.update(overrides)
    return body
