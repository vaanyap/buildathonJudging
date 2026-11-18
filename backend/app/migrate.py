"""Idempotent schema upgrades for databases created before two-phase timers.

`create_all` builds a fresh database. It does not add columns to tables that
already exist, so startup runs this after `create_all`.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from app.database import SessionLocal
from app.services.timers import get_timer_defaults

_SLOT_COLUMNS = (
    ("presentation_minutes", "INTEGER NOT NULL DEFAULT 3"),
    ("qa_minutes", "INTEGER NOT NULL DEFAULT 2"),
    ("presentation_extension_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("qa_extension_minutes", "INTEGER NOT NULL DEFAULT 0"),
)

_TIMER_COLUMNS = (
    ("active_phase", "VARCHAR(16) NOT NULL DEFAULT 'presentation'"),
    ("presentation_accumulated_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("presentation_running_since", "TIMESTAMPTZ"),
    ("presentation_started_at", "TIMESTAMPTZ"),
    ("presentation_ended_at", "TIMESTAMPTZ"),
    ("presentation_status", "VARCHAR(16) NOT NULL DEFAULT 'idle'"),
    ("qa_accumulated_seconds", "INTEGER NOT NULL DEFAULT 0"),
    ("qa_running_since", "TIMESTAMPTZ"),
    ("qa_started_at", "TIMESTAMPTZ"),
    ("qa_ended_at", "TIMESTAMPTZ"),
    ("qa_status", "VARCHAR(16) NOT NULL DEFAULT 'idle'"),
)


def _columns(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    ).all()
    return {row[0] for row in rows}


def _add_missing(conn: Connection, table: str, columns: tuple[tuple[str, str], ...]) -> None:
    existing = _columns(conn, table)
    for name, definition in columns:
        if name in existing:
            continue
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))


def _upgrade_slots(conn: Connection) -> None:
    if "schedule_slots" not in _tables(conn):
        return
    before = _columns(conn, "schedule_slots")
    _add_missing(conn, "schedule_slots", _SLOT_COLUMNS)
    if "duration_minutes" not in before:
        return
    # Keep the old slot length. Up to 3 minutes stay on presentation and the
    # rest becomes Q&A (a 5 minute slot becomes 3 + 2). A 1 minute slot grows
    # to 1 + 1 because each phase needs a minute. The old extension lengthened
    # the single clock, so it moves to the presentation phase.
    conn.execute(
        text(
            """
            UPDATE schedule_slots
            SET presentation_minutes = CASE
                    WHEN duration_minutes <= 1 THEN 1
                    ELSE LEAST(3, duration_minutes - 1)
                END,
                qa_minutes = CASE
                    WHEN duration_minutes <= 1 THEN 1
                    ELSE duration_minutes - LEAST(3, duration_minutes - 1)
                END,
                presentation_extension_minutes = COALESCE(extension_minutes, 0),
                qa_extension_minutes = 0
            """
        )
    )
    conn.execute(text("ALTER TABLE schedule_slots DROP COLUMN IF EXISTS duration_minutes"))
    conn.execute(text("ALTER TABLE schedule_slots DROP COLUMN IF EXISTS extension_minutes"))


def _upgrade_timers(conn: Connection) -> None:
    if "timers" not in _tables(conn):
        return
    before = _columns(conn, "timers")
    _add_missing(conn, "timers", _TIMER_COLUMNS)
    if "status" not in before or "accumulated_seconds" not in before:
        return
    conn.execute(
        text(
            """
            UPDATE timers
            SET presentation_status = status,
                presentation_accumulated_seconds = accumulated_seconds,
                presentation_running_since = running_since,
                presentation_started_at = actual_started_at,
                presentation_ended_at = actual_ended_at,
                active_phase = 'presentation'
            WHERE accumulated_seconds <> 0
               OR running_since IS NOT NULL
               OR actual_started_at IS NOT NULL
               OR actual_ended_at IS NOT NULL
               OR status <> 'idle'
            """
        )
    )
    conn.execute(text("ALTER TABLE timers DROP COLUMN IF EXISTS accumulated_seconds"))
    conn.execute(text("ALTER TABLE timers DROP COLUMN IF EXISTS running_since"))
    conn.execute(text("ALTER TABLE timers DROP COLUMN IF EXISTS actual_started_at"))
    conn.execute(text("ALTER TABLE timers DROP COLUMN IF EXISTS actual_ended_at"))
    conn.execute(text("ALTER TABLE timers DROP COLUMN IF EXISTS status"))


def _tables(conn: Connection) -> set[str]:
    rows = conn.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            """
        )
    ).all()
    return {row[0] for row in rows}


_PROJECT_COLUMNS = (
    ("video_url", "VARCHAR(500) NOT NULL DEFAULT ''"),
    ("table_location", "VARCHAR(120) NOT NULL DEFAULT ''"),
    ("submitted_at", "TIMESTAMPTZ"),
    ("winner_label", "VARCHAR(200) NOT NULL DEFAULT ''"),
    ("presentation_minutes", "INTEGER"),
    ("qa_minutes", "INTEGER"),
    ("presentation_extension_minutes", "INTEGER NOT NULL DEFAULT 0"),
    ("qa_extension_minutes", "INTEGER NOT NULL DEFAULT 0"),
)


def _upgrade_memberships(conn: Connection) -> None:
    if "projects" not in _tables(conn):
        return
    _add_missing(conn, "projects", _PROJECT_COLUMNS)
    project_cols = _columns(conn, "projects")
    if "track_id" in project_cols and "project_tracks" in _tables(conn):
        conn.execute(
            text(
                """
                INSERT INTO project_tracks (project_id, track_id)
                SELECT id, track_id FROM projects
                WHERE track_id IS NOT NULL
                ON CONFLICT DO NOTHING
                """
            )
        )
        conn.execute(text("ALTER TABLE projects DROP CONSTRAINT IF EXISTS projects_track_id_fkey"))
        conn.execute(text("ALTER TABLE projects DROP COLUMN track_id"))
    if "assignments" in _tables(conn) and "track_id" not in _columns(conn, "assignments"):
        conn.execute(text("ALTER TABLE assignments ADD COLUMN track_id INTEGER"))
        conn.execute(text("ALTER TABLE assignments ADD COLUMN source VARCHAR(16) NOT NULL DEFAULT 'project'"))
        conn.execute(
            text(
                """
                UPDATE assignments AS a
                SET track_id = pt.track_id
                FROM project_tracks AS pt
                WHERE pt.project_id = a.project_id AND a.track_id IS NULL
                """
            )
        )
        conn.execute(text("ALTER TABLE assignments ALTER COLUMN track_id SET NOT NULL"))
        conn.execute(text("ALTER TABLE assignments DROP CONSTRAINT IF EXISTS uq_assignment_judge_project"))
        conn.execute(
            text(
                """
                DO $$ BEGIN
                    ALTER TABLE assignments
                    ADD CONSTRAINT uq_assignment_judge_project_track UNIQUE (judge_id, project_id, track_id);
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$
                """
            )
        )
    if "evaluations" in _tables(conn) and "track_id" not in _columns(conn, "evaluations"):
        conn.execute(text("ALTER TABLE evaluations ADD COLUMN track_id INTEGER"))
        conn.execute(
            text(
                """
                UPDATE evaluations AS e
                SET track_id = a.track_id
                FROM assignments AS a
                WHERE a.id = e.assignment_id AND e.track_id IS NULL
                """
            )
        )
        conn.execute(text("ALTER TABLE evaluations ALTER COLUMN track_id SET NOT NULL"))
        conn.execute(text("ALTER TABLE evaluations DROP CONSTRAINT IF EXISTS uq_evaluation_judge_project"))
        conn.execute(
            text(
                """
                DO $$ BEGIN
                    ALTER TABLE evaluations
                    ADD CONSTRAINT uq_evaluation_judge_project_track UNIQUE (judge_id, project_id, track_id);
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$
                """
            )
        )
    if "schedule_slots" in _tables(conn):
        slot_cols = _columns(conn, "schedule_slots")
        if "presentation_minutes" in slot_cols:
            conn.execute(
                text(
                    """
                    UPDATE projects AS p
                    SET presentation_minutes = s.presentation_minutes,
                        qa_minutes = s.qa_minutes,
                        presentation_extension_minutes = s.presentation_extension_minutes,
                        qa_extension_minutes = s.qa_extension_minutes
                    FROM schedule_slots AS s
                    WHERE s.project_id = p.id
                    """
                )
            )
        elif "duration_minutes" in slot_cols:
            conn.execute(
                text(
                    """
                    UPDATE projects AS p
                    SET presentation_minutes = CASE
                            WHEN s.duration_minutes <= 1 THEN 1
                            ELSE LEAST(3, s.duration_minutes - 1)
                        END,
                        qa_minutes = CASE
                            WHEN s.duration_minutes <= 1 THEN 1
                            ELSE s.duration_minutes - LEAST(3, s.duration_minutes - 1)
                        END,
                        presentation_extension_minutes = COALESCE(s.extension_minutes, 0),
                        qa_extension_minutes = 0
                    FROM schedule_slots AS s
                    WHERE s.project_id = p.id
                    """
                )
            )
        conn.execute(text("DROP TABLE schedule_slots"))
    if "timers" in _tables(conn) and "assignment_id" in _columns(conn, "timers"):
        if "judge_id" not in _columns(conn, "timers"):
            conn.execute(text("ALTER TABLE timers ADD COLUMN judge_id INTEGER"))
            conn.execute(text("ALTER TABLE timers ADD COLUMN project_id INTEGER"))
        conn.execute(
            text(
                """
                UPDATE timers AS t
                SET judge_id = a.judge_id, project_id = a.project_id
                FROM assignments AS a
                WHERE a.id = t.assignment_id
                """
            )
        )
        conn.execute(text("ALTER TABLE timers DROP CONSTRAINT IF EXISTS timers_assignment_id_fkey"))
        conn.execute(text("ALTER TABLE timers DROP CONSTRAINT IF EXISTS timers_assignment_id_key"))
        conn.execute(text("ALTER TABLE timers DROP COLUMN assignment_id"))
        conn.execute(text("ALTER TABLE timers ALTER COLUMN judge_id SET NOT NULL"))
        conn.execute(text("ALTER TABLE timers ALTER COLUMN project_id SET NOT NULL"))
        conn.execute(
            text(
                """
                DO $$ BEGIN
                    ALTER TABLE timers
                    ADD CONSTRAINT uq_timer_judge_project UNIQUE (judge_id, project_id);
                EXCEPTION WHEN duplicate_object THEN NULL;
                END $$
                """
            )
        )


def upgrade_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        _upgrade_slots(conn)
        _upgrade_timers(conn)
        _upgrade_memberships(conn)
    db = SessionLocal()
    try:
        get_timer_defaults(db)
        from app.services.discrepancies import get_settings

        get_settings(db)
        db.commit()
    finally:
        db.close()
