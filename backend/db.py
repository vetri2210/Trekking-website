import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATABASE_PATH = Path(os.environ.get("TMA_DATABASE", Path(__file__).with_name("trekking.db")))


@contextmanager
def connect():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                phone TEXT DEFAULT '',
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'staff', 'user')),
                status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'deactivated', 'blacklisted')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS only_one_admin
                ON users(role) WHERE role = 'admin';
            CREATE TABLE IF NOT EXISTS treks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                difficulty TEXT NOT NULL CHECK (difficulty IN ('Easy', 'Moderate', 'Hard')),
                duration_days INTEGER NOT NULL CHECK (duration_days > 0),
                available_slots INTEGER NOT NULL DEFAULT 0 CHECK (available_slots >= 0),
                staff_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL DEFAULT 'Pending'
                    CHECK (status IN ('Pending', 'Approved', 'Open', 'Closed', 'Completed')),
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                trek_id INTEGER NOT NULL REFERENCES treks(id),
                booking_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'Booked' CHECK (status IN ('Booked', 'Cancelled', 'Completed')),
                payment_status TEXT NOT NULL DEFAULT 'Not required',
                UNIQUE(user_id, trek_id)
            );
            CREATE TABLE IF NOT EXISTS exports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL DEFAULT 'queued',
                file_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        admin_email = os.environ.get("TMA_ADMIN_EMAIL", "admin@trailwise.local")
        admin_password = os.environ.get("TMA_ADMIN_PASSWORD", "TrailAdmin!2026")
        admin = db.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()
        if not admin:
            from werkzeug.security import generate_password_hash

            db.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                ("Trailwise Admin", admin_email, generate_password_hash(admin_password)),
            )
        if db.execute("SELECT COUNT(*) FROM treks").fetchone()[0] == 0:
            db.executemany(
                """INSERT INTO treks
                   (name, location, difficulty, duration_days, available_slots, status,
                    start_date, end_date, description, image_url)
                   VALUES (?, ?, ?, ?, ?, 'Open', ?, ?, ?, ?)""",
                [
                    (
                        "Misty Munnar Ridge",
                        "Munnar, Kerala",
                        "Moderate",
                        2,
                        12,
                        "2027-01-16",
                        "2027-01-17",
                        "Follow tea trails into the clouds, with a slow sunrise walk and a cosy camp-side evening.",
                        "https://images.unsplash.com/photo-1500530855697-b586d89ba3ee?auto=format&fit=crop&w=1200&q=85",
                    ),
                    (
                        "Kudremukh Green Escape",
                        "Chikkamagaluru, Karnataka",
                        "Hard",
                        2,
                        8,
                        "2027-01-23",
                        "2027-01-24",
                        "A rewarding climb across rolling grasslands, misty valleys and one very memorable summit.",
                        "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=1200&q=85",
                    ),
                    (
                        "Coorg Forest & Falls",
                        "Coorg, Karnataka",
                        "Easy",
                        1,
                        16,
                        "2027-02-06",
                        "2027-02-06",
                        "A beginner-friendly forest ramble with birdsong, coffee country and a waterfall picnic.",
                        "https://images.unsplash.com/photo-1448375240586-882707db888b?auto=format&fit=crop&w=1200&q=85",
                    ),
                ],
            )
