"""Acces a la base de donnees SQLite (schema partage par tous les scripts)."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "db.sqlite3")

SCHEMA = """
CREATE TABLE IF NOT EXISTS guests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prenom TEXT NOT NULL,
    nom TEXT NOT NULL,
    email TEXT,
    categorie TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'individuel',
    token TEXT UNIQUE NOT NULL,
    amount_cents INTEGER NOT NULL DEFAULT 0,
    payment_status TEXT NOT NULL DEFAULT 'invite',
    stripe_session_id TEXT,
    checked_in INTEGER NOT NULL DEFAULT 0,
    checked_in_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    # migration douce si une ancienne base sans la colonne "type" existe deja
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(guests)")]
    if "type" not in cols:
        conn.execute("ALTER TABLE guests ADD COLUMN type TEXT NOT NULL DEFAULT 'individuel'")
    conn.commit()
    conn.close()


def find_guests_by_session(conn, stripe_session_id):
    return conn.execute(
        "SELECT * FROM guests WHERE stripe_session_id = ?", (stripe_session_id,)
    ).fetchall()


def find_guest_by_token(conn, token):
    return conn.execute("SELECT * FROM guests WHERE token = ?", (token,)).fetchone()


def find_guest(conn, prenom, nom, categorie):
    return conn.execute(
        "SELECT * FROM guests WHERE prenom = ? AND nom = ? AND categorie = ?",
        (prenom, nom, categorie),
    ).fetchone()


def count_individual_sold(conn):
    return conn.execute(
        "SELECT COUNT(*) FROM guests WHERE type = 'individuel' AND payment_status = 'paye'"
    ).fetchone()[0]


def create_guest(conn, prenom, nom, email, categorie, token, amount_cents=0,
                  payment_status="invite", stripe_session_id=None, type_="individuel"):
    conn.execute(
        """
        INSERT INTO guests (prenom, nom, email, categorie, type, token, amount_cents, payment_status, stripe_session_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (prenom, nom, email, categorie, type_, token, amount_cents, payment_status, stripe_session_id),
    )
    conn.commit()


def mark_checked_in(conn, guest_id, timestamp):
    conn.execute(
        "UPDATE guests SET checked_in = 1, checked_in_at = ? WHERE id = ?",
        (timestamp, guest_id),
    )
    conn.commit()


def stats(conn):
    total = conn.execute("SELECT COUNT(*) FROM guests").fetchone()[0]
    checked_in = conn.execute("SELECT COUNT(*) FROM guests WHERE checked_in = 1").fetchone()[0]
    revenue_cents = conn.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM guests WHERE payment_status = 'paye'"
    ).fetchone()[0]
    individual_sold = count_individual_sold(conn)
    return {
        "total": total,
        "checked_in": checked_in,
        "revenue_cents": revenue_cents,
        "individual_sold": individual_sold,
    }


def list_guests(conn):
    return conn.execute(
        "SELECT * FROM guests ORDER BY created_at DESC"
    ).fetchall()
