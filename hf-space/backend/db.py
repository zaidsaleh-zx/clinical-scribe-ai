"""
Lightweight session history using SQLite — good enough for a student project demo
("recent consultations" sidebar) without standing up a real database server.
"""

import sqlite3
import json
import os
import uuid
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            patient_name TEXT,
            patient_age TEXT,
            patient_gender TEXT,
            chief_complaint TEXT,
            transcript TEXT,
            note_json TEXT,
            engine TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def save_session(patient_name, patient_age, patient_gender, chief_complaint, transcript, note: dict):
    conn = get_conn()
    session_id = str(uuid.uuid4())
    conn.execute(
        """INSERT INTO sessions
           (id, patient_name, patient_age, patient_gender, chief_complaint, transcript, note_json, engine, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id, patient_name or "Unnamed patient", patient_age or "", patient_gender or "",
            chief_complaint or "", transcript, json.dumps(note), note.get("engine", "unknown"),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    conn.close()
    return session_id


def list_sessions(limit=20):
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, patient_name, chief_complaint, engine, created_at FROM sessions ORDER BY created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session(session_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["note"] = json.loads(d.pop("note_json"))
    return d


init_db()
