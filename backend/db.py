"""
ClinicalScribe AI — Relational database layer (SQLite).

Schema (3 core tables + audit log):
  patients      — demographics, one row per patient
  consultations — each visit/encounter, linked to a patient
  soap_notes    — the drafted SOAP note, linked to a consultation
  audit_log     — who/what changed and when

Backwards-compatible: the old single-table `sessions` layout is still
readable via `migrate_legacy_sessions()` if you already have data.
"""

import sqlite3
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
from .order_routing import extract_lab_tests_from_soap, extract_medications_from_plan

DB_PATH = os.environ.get(
    "CLINICAL_SCRIBE_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions.db"),
)


class OrdersAlreadyRoutedError(ValueError):
    """Raised when an already-routed consultation is submitted again."""


class InvalidLaboratoryOrderTransitionError(ValueError):
    """Raised when a laboratory order is updated after leaving the pending state."""


class InvalidPharmacyOrderTransitionError(ValueError):
    """Raised when a pharmacy order is updated after leaving the pending state."""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Create all tables if they don't exist. Safe to run on every startup."""
    conn = get_conn()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS patients (
            id            TEXT PRIMARY KEY,
            first_name    TEXT NOT NULL DEFAULT '',
            last_name     TEXT NOT NULL DEFAULT '',
            date_of_birth TEXT,
            gender        TEXT,
            phone         TEXT,
            email         TEXT,
            address       TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS consultations (
            id              TEXT PRIMARY KEY,
            patient_id      TEXT NOT NULL,
            chief_complaint TEXT DEFAULT '',
            transcript      TEXT DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'draft',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS soap_notes (
            id              TEXT PRIMARY KEY,
            consultation_id TEXT NOT NULL,
            subjective      TEXT DEFAULT '[]',
            objective       TEXT DEFAULT '[]',
            assessment      TEXT DEFAULT '[]',
            plan            TEXT DEFAULT '[]',
            unclassified    TEXT DEFAULT '[]',
            sources         TEXT DEFAULT '{}',
            engine          TEXT NOT NULL DEFAULT 'rule_based',
            completeness    TEXT DEFAULT '{}',
            issues          TEXT DEFAULT '[]',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id          TEXT PRIMARY KEY,
            action      TEXT NOT NULL,
            table_name  TEXT NOT NULL,
            record_id   TEXT NOT NULL,
            old_values  TEXT,
            new_values  TEXT,
            created_at  TEXT NOT NULL
        );

        -- Pharmacy orders: prescriptions extracted from SOAP Plan section
        CREATE TABLE IF NOT EXISTS pharmacy_orders (
            id              TEXT PRIMARY KEY,
            consultation_id TEXT NOT NULL,
            patient_id      TEXT NOT NULL,
            medication      TEXT NOT NULL,
            dosage          TEXT DEFAULT '',
            frequency       TEXT DEFAULT '',
            duration        TEXT DEFAULT '',
            instructions    TEXT DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending',
            source_section  TEXT DEFAULT 'plan',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE,
            FOREIGN KEY (patient_id)      REFERENCES patients(id) ON DELETE CASCADE
        );

        -- Laboratory orders: tests extracted from SOAP Objective / Plan sections
        CREATE TABLE IF NOT EXISTS laboratory_orders (
            id              TEXT PRIMARY KEY,
            consultation_id TEXT NOT NULL,
            patient_id      TEXT NOT NULL,
            test_name       TEXT NOT NULL,
            test_code       TEXT DEFAULT '',
            specimen        TEXT DEFAULT '',
            status          TEXT NOT NULL DEFAULT 'pending',
            result          TEXT DEFAULT '',
            source_section  TEXT DEFAULT 'objective',
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (consultation_id) REFERENCES consultations(id) ON DELETE CASCADE,
            FOREIGN KEY (patient_id)      REFERENCES patients(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_consultations_patient
            ON consultations(patient_id);
        CREATE INDEX IF NOT EXISTS idx_consultations_created
            ON consultations(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_soap_notes_consultation
            ON soap_notes(consultation_id);
        CREATE INDEX IF NOT EXISTS idx_audit_log_created
            ON audit_log(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_pharmacy_orders_consultation
            ON pharmacy_orders(consultation_id);
        CREATE INDEX IF NOT EXISTS idx_pharmacy_orders_status
            ON pharmacy_orders(status);
        CREATE INDEX IF NOT EXISTS idx_lab_orders_consultation
            ON laboratory_orders(consultation_id);
        CREATE INDEX IF NOT EXISTS idx_lab_orders_status
            ON laboratory_orders(status);
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------
def _audit(conn: sqlite3.Connection, action: str, table_name: str,
           record_id: str, old_values: Optional[dict], new_values: Optional[dict]) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (id, action, table_name, record_id, old_values, new_values, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            str(uuid.uuid4()), action, table_name, record_id,
            json.dumps(old_values) if old_values is not None else None,
            json.dumps(new_values) if new_values is not None else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
def create_patient(patient: Dict[str, Any]) -> str:
    """Insert a new patient and return the generated id."""
    conn = get_conn()
    try:
        pid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO patients
               (id, first_name, last_name, date_of_birth, gender, phone, email, address, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pid,
                patient.get("first_name", ""),
                patient.get("last_name", ""),
                patient.get("date_of_birth", ""),
                patient.get("gender", ""),
                patient.get("phone", ""),
                patient.get("email", ""),
                patient.get("address", ""),
                now, now,
            ),
        )
        _audit(conn, "insert", "patients", pid, None, patient)
        conn.commit()
        return pid
    finally:
        conn.close()


def get_patient(patient_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_patients(limit: int = 50) -> List[dict]:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM patients ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_patient(patient_id: str, patient: Dict[str, Any]) -> bool:
    conn = get_conn()
    try:
        old = get_patient(patient_id)
        if old is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """UPDATE patients SET
               first_name = COALESCE(?, first_name),
               last_name  = COALESCE(?, last_name),
               date_of_birth = COALESCE(?, date_of_birth),
               gender     = COALESCE(?, gender),
               phone      = COALESCE(?, phone),
               email      = COALESCE(?, email),
               address    = COALESCE(?, address),
               updated_at = ?
               WHERE id = ?""",
            (
                patient.get("first_name"),
                patient.get("last_name"),
                patient.get("date_of_birth"),
                patient.get("gender"),
                patient.get("phone"),
                patient.get("email"),
                patient.get("address"),
                now,
                patient_id,
            ),
        )
        _audit(conn, "update", "patients", patient_id, old, patient)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_patient(patient_id: str) -> bool:
    conn = get_conn()
    try:
        old = get_patient(patient_id)
        if old is None:
            return False
        conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        _audit(conn, "delete", "patients", patient_id, old, None)
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Consultations
# ---------------------------------------------------------------------------
def create_consultation(patient_id: str, chief_complaint: str = "",
                        transcript: str = "") -> str:
    """Create a new consultation (draft) for a patient. Returns consultation id."""
    conn = get_conn()
    try:
        cid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO consultations
               (id, patient_id, chief_complaint, transcript, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (cid, patient_id, chief_complaint, transcript, "draft", now, now),
        )
        _audit(conn, "insert", "consultations", cid, None,
               {"patient_id": patient_id, "chief_complaint": chief_complaint})
        conn.commit()
        return cid
    finally:
        conn.close()


def get_consultation(consultation_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM consultations WHERE id = ?", (consultation_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_consultations(patient_id: Optional[str] = None,
                       limit: int = 50) -> List[dict]:
    conn = get_conn()
    try:
        if patient_id:
            rows = conn.execute(
                """SELECT c.*, p.first_name, p.last_name
                   FROM consultations c
                   JOIN patients p ON c.patient_id = p.id
                   WHERE c.patient_id = ?
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (patient_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT c.*, p.first_name, p.last_name
                   FROM consultations c
                   JOIN patients p ON c.patient_id = p.id
                   ORDER BY c.updated_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_consultation(consultation_id: str, fields: Dict[str, Any]) -> bool:
    conn = get_conn()
    try:
        old = get_consultation(consultation_id)
        if old is None:
            return False

        allowed = {"chief_complaint", "transcript", "status"}
        updates = []
        params = []
        for key in allowed:
            if key in fields:
                updates.append(f"{key} = ?")
                params.append(fields[key])

        if not updates:
            return False

        updates.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(consultation_id)

        conn.execute(
            f"UPDATE consultations SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        _audit(conn, "update", "consultations", consultation_id, old, fields)
        conn.commit()
        return True
    finally:
        conn.close()


def delete_consultation(consultation_id: str) -> bool:
    conn = get_conn()
    try:
        old = get_consultation(consultation_id)
        if old is None:
            return False
        conn.execute("DELETE FROM consultations WHERE id = ?", (consultation_id,))
        _audit(conn, "delete", "consultations", consultation_id, old, None)
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# SOAP Notes
# ---------------------------------------------------------------------------
def save_soap_note(consultation_id: str, note: Dict[str, Any],
                   engine: str = "rule_based") -> str:
    """Upsert a SOAP note for a consultation. Returns note id."""
    conn = get_conn()
    try:
        now = datetime.now(timezone.utc).isoformat()
        existing = conn.execute(
            "SELECT id FROM soap_notes WHERE consultation_id = ?",
            (consultation_id,),
        ).fetchone()

        if existing:
            note_id = existing["id"]
            old = dict(conn.execute(
                "SELECT * FROM soap_notes WHERE id = ?", (note_id,)
            ).fetchone())
            conn.execute(
                """UPDATE soap_notes SET
                   subjective = ?, objective = ?, assessment = ?, plan = ?,
                   unclassified = ?, sources = ?, engine = ?,
                   completeness = ?, issues = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    json.dumps(note.get("subjective", [])),
                    json.dumps(note.get("objective", [])),
                    json.dumps(note.get("assessment", [])),
                    json.dumps(note.get("plan", [])),
                    json.dumps(note.get("unclassified", [])),
                    json.dumps(note.get("sources", {})),
                    engine,
                    json.dumps(note.get("completeness", {})),
                    json.dumps(note.get("issues", [])),
                    now,
                    note_id,
                ),
            )
            _audit(conn, "update", "soap_notes", note_id, old, note)
        else:
            note_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO soap_notes
                   (id, consultation_id, subjective, objective, assessment, plan,
                    unclassified, sources, engine, completeness, issues,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    note_id, consultation_id,
                    json.dumps(note.get("subjective", [])),
                    json.dumps(note.get("objective", [])),
                    json.dumps(note.get("assessment", [])),
                    json.dumps(note.get("plan", [])),
                    json.dumps(note.get("unclassified", [])),
                    json.dumps(note.get("sources", {})),
                    engine,
                    json.dumps(note.get("completeness", {})),
                    json.dumps(note.get("issues", [])),
                    now, now,
                ),
            )
            _audit(conn, "insert", "soap_notes", note_id, None, note)

        conn.execute(
            "UPDATE consultations SET status = 'completed', updated_at = ? WHERE id = ?",
            (now, consultation_id),
        )
        conn.commit()
        return note_id
    finally:
        conn.close()


def get_soap_note(consultation_id: str) -> Optional[dict]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM soap_notes WHERE consultation_id = ?",
            (consultation_id,),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        for key in ("subjective", "objective", "assessment", "plan",
                    "unclassified", "sources", "completeness", "issues"):
            try:
                d[key] = json.loads(d[key]) if d[key] else (
                    {} if key in ("sources", "completeness") else []
                )
            except (json.JSONDecodeError, TypeError):
                d[key] = {} if key in ("sources", "completeness") else []
        return d
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Combined history view (for the sidebar)
# ---------------------------------------------------------------------------
def list_sessions_with_patients(limit: int = 50) -> List[dict]:
    """Return consultations with patient name + SOAP note summary —
    replaces the old list_sessions() for the frontend sidebar."""
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT
                   c.id             AS session_id,
                   c.patient_id,
                   p.first_name,
                   p.last_name,
                   c.chief_complaint,
                   c.status,
                   c.created_at,
                   c.updated_at,
                   s.engine,
                   s.completeness
               FROM consultations c
               JOIN patients p ON c.patient_id = p.id
               LEFT JOIN soap_notes s ON s.consultation_id = c.id
               ORDER BY c.updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session_full(session_id: str) -> Optional[dict]:
    """Full consultation + patient + SOAP note — replaces old get_session()."""
    consultation = get_consultation(session_id)
    if not consultation:
        return None
    patient = get_patient(consultation["patient_id"])
    note = get_soap_note(session_id)
    return {
        "consultation": consultation,
        "patient": patient,
        "soap_note": note,
    }


def delete_session(session_id: str) -> bool:
    """Permanently remove one saved consultation and its associated PHI."""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        consultation = conn.execute(
            "SELECT patient_id FROM consultations WHERE id = ?",
            (session_id,),
        ).fetchone()
        if consultation is None:
            conn.rollback()
            return False

        patient_id = consultation["patient_id"]
        record_ids = [session_id]
        for table in ("soap_notes", "pharmacy_orders", "laboratory_orders"):
            rows = conn.execute(
                f"SELECT id FROM {table} WHERE consultation_id = ?",
                (session_id,),
            ).fetchall()
            record_ids.extend(row["id"] for row in rows)

        placeholders = ",".join("?" for _ in record_ids)
        conn.execute(
            f"DELETE FROM audit_log WHERE record_id IN ({placeholders})",
            record_ids,
        )
        conn.execute("DELETE FROM consultations WHERE id = ?", (session_id,))

        remaining = conn.execute(
            "SELECT 1 FROM consultations WHERE patient_id = ? LIMIT 1",
            (patient_id,),
        ).fetchone()
        if remaining is None:
            conn.execute("DELETE FROM audit_log WHERE record_id = ?", (patient_id,))
            conn.execute("DELETE FROM patients WHERE id = ?", (patient_id,))

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Legacy migration (optional)
# ---------------------------------------------------------------------------
def migrate_legacy_sessions() -> int:
    """If the old flat `sessions` table exists, copy its rows into the new
    relational schema (one patient + one consultation per row). Returns count."""
    conn = get_conn()
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        ).fetchone()
        if not table:
            return 0

        rows = conn.execute("SELECT * FROM sessions").fetchall()
        count = 0
        for r in rows:
            d = dict(r)
            pid = str(uuid.uuid4())
            now = d.get("created_at", datetime.now(timezone.utc).isoformat())
            conn.execute(
                """INSERT OR IGNORE INTO patients
                   (id, first_name, last_name, date_of_birth, gender, phone, email, address, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (pid, d.get("patient_name", "Unnamed"), "", "", "", "", "", "", now, now),
            )
            cid = d.get("id", str(uuid.uuid4()))
            conn.execute(
                """INSERT OR IGNORE INTO consultations
                   (id, patient_id, chief_complaint, transcript, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (cid, pid, d.get("chief_complaint", ""), d.get("transcript", ""),
                 "completed", now, now),
            )
            note_raw = d.get("note_json", "{}")
            try:
                note = json.loads(note_raw)
            except (json.JSONDecodeError, TypeError):
                note = {}
            conn.execute(
                """INSERT OR IGNORE INTO soap_notes
                   (id, consultation_id, subjective, objective, assessment, plan,
                    unclassified, sources, engine, completeness, issues,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), cid,
                    json.dumps(note.get("subjective", [])),
                    json.dumps(note.get("objective", [])),
                    json.dumps(note.get("assessment", [])),
                    json.dumps(note.get("plan", [])),
                    json.dumps(note.get("unclassified", [])),
                    json.dumps(note.get("sources", {})),
                    note.get("engine", "rule_based"),
                    json.dumps(note.get("completeness", {})),
                    json.dumps(note.get("issues", [])),
                    now, now,
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Pharmacy Orders — prescriptions extracted from SOAP Plan section
# ---------------------------------------------------------------------------
def create_pharmacy_order(consultation_id: str, patient_id: str,
                          medication: str, dosage: str = "",
                          frequency: str = "", duration: str = "",
                          instructions: str = "") -> str:
    conn = get_conn()
    try:
        oid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO pharmacy_orders
               (id, consultation_id, patient_id, medication, dosage, frequency,
                duration, instructions, status, source_section, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (oid, consultation_id, patient_id, medication, dosage, frequency,
             duration, instructions, "pending", "plan", now, now),
        )
        _audit(conn, "insert", "pharmacy_orders", oid, None,
               {"medication": medication, "dosage": dosage})
        conn.commit()
        return oid
    finally:
        conn.close()


def list_pharmacy_orders(consultation_id: Optional[str] = None,
                         status: Optional[str] = None,
                         limit: int = 100) -> List[dict]:
    conn = get_conn()
    try:
        query = """SELECT po.*, p.first_name, p.last_name
                   FROM pharmacy_orders po
                   JOIN patients p ON po.patient_id = p.id"""
        params = []
        conditions = []
        if consultation_id:
            conditions.append("po.consultation_id = ?")
            params.append(consultation_id)
        if status:
            conditions.append("po.status = ?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY po.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_pharmacy_order_status(order_id: str, status: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM pharmacy_orders WHERE id = ?", (order_id,)
        ).fetchone()
        if not row:
            return False
        if status not in {"pending", "dispensed", "cancelled"}:
            raise ValueError(f"Unsupported pharmacy order status: {status}")
        if row["status"] != "pending":
            raise InvalidPharmacyOrderTransitionError(
                f"Cannot change a pharmacy order from {row['status']} to {status}."
            )
        old = dict(row)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE pharmacy_orders SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, order_id),
        )
        _audit(conn, "update", "pharmacy_orders", order_id, old, {"status": status})
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Laboratory Orders — tests extracted from SOAP Objective / Plan sections
# ---------------------------------------------------------------------------
def create_laboratory_order(consultation_id: str, patient_id: str,
                            test_name: str, test_code: str = "",
                            specimen: str = "") -> str:
    conn = get_conn()
    try:
        oid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """INSERT INTO laboratory_orders
               (id, consultation_id, patient_id, test_name, test_code,
                specimen, status, source_section, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (oid, consultation_id, patient_id, test_name, test_code,
             specimen, "pending", "objective", now, now),
        )
        _audit(conn, "insert", "laboratory_orders", oid, None,
               {"test_name": test_name, "test_code": test_code})
        conn.commit()
        return oid
    finally:
        conn.close()


def list_laboratory_orders(consultation_id: Optional[str] = None,
                           status: Optional[str] = None,
                           limit: int = 100) -> List[dict]:
    conn = get_conn()
    try:
        query = """SELECT lo.*, p.first_name, p.last_name
                   FROM laboratory_orders lo
                   JOIN patients p ON lo.patient_id = p.id"""
        params = []
        conditions = []
        if consultation_id:
            conditions.append("lo.consultation_id = ?")
            params.append(consultation_id)
        if status:
            conditions.append("lo.status = ?")
            params.append(status)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY lo.created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_laboratory_order_status(order_id: str, status: str,
                                   result: Optional[str] = None) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM laboratory_orders WHERE id = ?", (order_id,)
        ).fetchone()
        if not row:
            return False
        if status not in {"completed", "cancelled"}:
            raise ValueError(f"Unsupported laboratory order status: {status}")
        if row["status"] != "pending":
            raise InvalidLaboratoryOrderTransitionError(
                f"Cannot change a laboratory order from {row['status']} to {status}."
            )
        if status == "completed" and not (result and result.strip()):
            raise ValueError("A result is required to complete a laboratory order.")
        old = dict(row)
        now = datetime.now(timezone.utc).isoformat()
        if result is not None:
            conn.execute(
                "UPDATE laboratory_orders SET status = ?, result = ?, updated_at = ? WHERE id = ?",
                (status, result.strip(), now, order_id),
            )
            _audit(conn, "update", "laboratory_orders", order_id, old,
                   {"status": status, "result": result.strip()})
        else:
            conn.execute(
                "UPDATE laboratory_orders SET status = ?, updated_at = ? WHERE id = ?",
                (status, now, order_id),
            )
            _audit(conn, "update", "laboratory_orders", order_id, old,
                   {"status": status})
        conn.commit()
        return True
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Submit SOAP note — auto-creates pharmacy + lab orders
# ---------------------------------------------------------------------------
def submit_soap_note(consultation_id: str, note: Dict[str, Any],
                     engine: str = "rule_based",
                     auto_route: bool = False) -> Dict[str, Any]:
    consultation = get_consultation(consultation_id)
    if not consultation:
        raise ValueError(f"Consultation {consultation_id} not found")
    patient_id = consultation["patient_id"]

    if auto_route:
        conn = get_conn()
        try:
            existing_orders = conn.execute(
                """SELECT
                       (SELECT COUNT(*) FROM pharmacy_orders WHERE consultation_id = ?) +
                       (SELECT COUNT(*) FROM laboratory_orders WHERE consultation_id = ?)""",
                (consultation_id, consultation_id),
            ).fetchone()[0]
        finally:
            conn.close()
        if existing_orders:
            raise OrdersAlreadyRoutedError(
                "Draft orders already exist for this consultation; review them in the queue instead of submitting again."
            )

    note_id = save_soap_note(consultation_id, note, engine=engine)

    result = {
        "note_id": note_id,
        "consultation_id": consultation_id,
        "pharmacy_order_ids": [],
        "laboratory_order_ids": [],
    }

    if not auto_route:
        return result

    plan_items = note.get("plan", [])
    objective_items = note.get("objective", [])

    medications = extract_medications_from_plan(plan_items)
    for med in medications:
        oid = create_pharmacy_order(
            consultation_id=consultation_id,
            patient_id=patient_id,
            medication=med["medication"],
            dosage=med.get("dosage", ""),
            frequency=med.get("frequency", ""),
            duration=med.get("duration", ""),
            instructions=med.get("instructions", ""),
        )
        result["pharmacy_order_ids"].append(oid)

    lab_tests = extract_lab_tests_from_soap(objective_items, plan_items)
    for test in lab_tests:
        oid = create_laboratory_order(
            consultation_id=consultation_id,
            patient_id=patient_id,
            test_name=test["test_name"],
            test_code=test.get("test_code", ""),
            specimen=test.get("specimen", ""),
        )
        result["laboratory_order_ids"].append(oid)

    return result


# ---------------------------------------------------------------------------
# Backwards-compatible wrappers (old single-table API)
# ---------------------------------------------------------------------------
def save_session(patient_name, patient_age, patient_gender, chief_complaint,
                 transcript, note: dict) -> str:
    """Legacy wrapper: creates a patient, consultation, and SOAP note in one call."""
    pid = create_patient({
        "first_name": patient_name or "Unnamed",
        "last_name": "",
        "gender": patient_gender or "",
    })
    cid = create_consultation(pid, chief_complaint=chief_complaint or "", transcript=transcript)
    note_id = save_soap_note(cid, note, engine=note.get("engine", "rule_based"))
    return cid  # return consultation id as the "session id" for compatibility


def list_sessions(limit: int = 20) -> List[dict]:
    """Legacy wrapper: returns sessions in the old flat format."""
    rows = list_sessions_with_patients(limit=limit)
    out = []
    for r in rows:
        out.append({
            "id": r["session_id"],
            "patient_name": f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "Unnamed patient",
            "patient_age": "",
            "patient_gender": "",
            "chief_complaint": r.get("chief_complaint", ""),
            "engine": r.get("engine", "rule_based"),
            "created_at": r.get("created_at", ""),
        })
    return out


def get_session(session_id: str) -> Optional[dict]:
    """Legacy wrapper: returns a flat dict similar to the old sessions table."""
    full = get_session_full(session_id)
    if not full:
        return None
    c = full["consultation"]
    p = full["patient"]
    n = full["soap_note"] or {}
    return {
        "id": c["id"],
        "patient_name": f"{p.get('first_name', '')} {p.get('last_name', '')}".strip() or "Unnamed patient",
        "patient_age": p.get("date_of_birth", ""),
        "patient_gender": p.get("gender", ""),
        "chief_complaint": c.get("chief_complaint", ""),
        "transcript": c.get("transcript", ""),
        "note_json": json.dumps(n),
        "engine": n.get("engine", "rule_based"),
        "created_at": c.get("created_at", ""),
        "note": n,
    }


# ---------------------------------------------------------------------------
# Initialize on import
# ---------------------------------------------------------------------------
init_db()
migrate_legacy_sessions()
