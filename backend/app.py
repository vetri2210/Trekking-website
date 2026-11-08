import json
import logging
import os
from datetime import date
from functools import wraps
from pathlib import Path

import redis
from flask import Flask, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from db import connect, initialize_database

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
app.secret_key = os.environ.get("TMA_SECRET_KEY", "local-development-key-change-me")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("TMA_HTTPS", "").lower() == "true",
)
cache_client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

initialize_database()


def response_error(message, status=400):
    return jsonify({"error": message}), status


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with connect() as db:
        row = db.execute(
            "SELECT id, name, email, phone, role, status FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    if not row or row["status"] != "active":
        session.clear()
        return None
    return dict(row)


def login_required(*roles):
    def decorator(handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return response_error("Please sign in to continue.", 401)
            if roles and user["role"] not in roles:
                return response_error("You do not have permission to do that.", 403)
            return handler(*args, **kwargs)

        return wrapped

    return decorator


def invalidate_treks():
    try:
        cache_client.delete("treks:open")
    except redis.RedisError:
        logger.warning("Redis unavailable; trek cache invalidation was skipped.")


def validate_trek(payload, existing=None):
    values = {}
    for field in ("name", "location", "difficulty", "start_date", "end_date"):
        value = payload.get(field, existing[field] if existing else "")
        if not isinstance(value, str) or not value.strip():
            return None, f"{field.replace('_', ' ').capitalize()} is required."
        values[field] = value.strip()
    if values["difficulty"] not in ("Easy", "Moderate", "Hard"):
        return None, "Choose Easy, Moderate, or Hard difficulty."
    try:
        duration = int(payload.get("duration_days", existing["duration_days"] if existing else 0))
        slots = int(payload.get("available_slots", existing["available_slots"] if existing else 0))
        start = date.fromisoformat(values["start_date"])
        end = date.fromisoformat(values["end_date"])
    except (ValueError, TypeError):
        return None, "Enter valid dates, duration, and available slots."
    if duration < 1 or slots < 0:
        return None, "Duration must be at least one day and slots cannot be negative."
    if end < start or (end - start).days + 1 != duration:
        return None, "End date must be on or after the start date and match the trek duration."
    status = payload.get("status", existing["status"] if existing else "Pending")
    if status not in ("Pending", "Approved", "Open", "Closed", "Completed"):
        return None, "Choose a valid trek status."
    staff_id = payload.get("staff_id", existing["staff_id"] if existing else None)
    if staff_id in ("", None):
        staff_id = None
    else:
        try:
            staff_id = int(staff_id)
        except (ValueError, TypeError):
            return None, "Choose a valid staff member."
    values.update(
        duration_days=duration,
        available_slots=slots,
        status=status,
        staff_id=staff_id,
        description=str(payload.get("description", existing["description"] if existing else "")).strip(),
        image_url=str(payload.get("image_url", existing["image_url"] if existing else "")).strip(),
    )
    return values, None


@app.get("/")
def home():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/api/session")
def api_session():
    return jsonify({"user": current_user()})


@app.post("/api/auth/register")
def register():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = payload.get("password", "")
    if len(name) < 2 or "@" not in email or not isinstance(password, str) or len(password) < 8:
        return response_error("Add your name, a valid email, and a password of at least 8 characters.")
    try:
        with connect() as db:
            cursor = db.execute(
                "INSERT INTO users (name, email, phone, password_hash, role) VALUES (?, ?, ?, ?, 'user')",
                (name, email, str(payload.get("phone", "")).strip(), generate_password_hash(password)),
            )
            user_id = cursor.lastrowid
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            return response_error("An account already exists for that email.")
        raise
    session["user_id"] = user_id
    return jsonify({"user": current_user()}), 201


@app.post("/api/auth/login")
def login():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    password = payload.get("password", "")
    with connect() as db:
        user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return response_error("That email and password combination did not work.", 401)
    if user["status"] != "active":
        return response_error("This account is currently inactive. Please contact an administrator.", 403)
    session.clear()
    session["user_id"] = user["id"]
    return jsonify({"user": current_user()})


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify({"message": "Signed out."})


@app.get("/api/profile")
@login_required()
def get_profile():
    return jsonify({"user": current_user()})


@app.patch("/api/profile")
@login_required("user")
def update_profile():
    user = current_user()
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    phone = str(payload.get("phone", "")).strip()
    if len(name) < 2:
        return response_error("Your name must be at least two characters.")
    with connect() as db:
        db.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name, phone, user["id"]))
    return jsonify({"user": current_user()})


@app.get("/api/treks")
def list_treks():
    user = current_user()
    admin_view = user and user["role"] == "admin"
    staff_view = user and user["role"] == "staff"
    cache_key = "treks:open"
    rows = None
    filters_applied = any(request.args.get(key) for key in ("q", "difficulty", "location", "min_days", "max_days"))
    if not admin_view and not staff_view and not filters_applied:
        try:
            cached = cache_client.get(cache_key)
            if cached:
                rows = json.loads(cached)
        except (redis.RedisError, json.JSONDecodeError):
            logger.warning("Redis unavailable or cache invalid; reading treks from SQLite.")
    if rows is None:
        query = """SELECT t.*, u.name AS staff_name FROM treks t
                   LEFT JOIN users u ON u.id = t.staff_id WHERE 1=1"""
        params = []
        if not admin_view and not staff_view:
            query += " AND t.status = 'Open' AND t.start_date >= ?"
            params.append(date.today().isoformat())
        if staff_view:
            query += " AND t.staff_id = ?"
            params.append(user["id"])
        search = request.args.get("q", "").strip()
        if search:
            query += " AND (t.name LIKE ? OR t.location LIKE ?)"
            params.extend((f"%{search}%", f"%{search}%"))
        for field in ("difficulty", "location"):
            value = request.args.get(field, "").strip()
            if value:
                query += f" AND t.{field} LIKE ?"
                params.append(f"%{value}%")
        for field, operator in (("min_days", ">="), ("max_days", "<=")):
            value = request.args.get(field, "").strip()
            if value:
                try:
                    params.append(int(value))
                except ValueError:
                    return response_error("Duration filters must be whole numbers.")
                query += f" AND t.duration_days {operator} ?"
        query += " ORDER BY t.start_date ASC"
        with connect() as db:
            rows = [dict(row) for row in db.execute(query, params).fetchall()]
        if not admin_view and not staff_view and not filters_applied:
            try:
                cache_client.setex(cache_key, 300, json.dumps(rows))
            except redis.RedisError:
                logger.warning("Redis unavailable; trek response was not cached.")
    return jsonify({"treks": rows})


@app.get("/api/treks/<int:trek_id>")
def get_trek(trek_id):
    with connect() as db:
        row = db.execute(
            """SELECT t.*, u.name AS staff_name FROM treks t
               LEFT JOIN users u ON u.id = t.staff_id WHERE t.id = ?""",
            (trek_id,),
        ).fetchone()
    if not row:
        return response_error("We could not find that trek.", 404)
    user = current_user()
    if row["status"] != "Open" and (not user or user["role"] != "admin"):
        return response_error("That trek is not currently available.", 404)
    return jsonify({"trek": dict(row)})


@app.post("/api/admin/treks")
@login_required("admin")
def create_trek():
    payload = request.get_json(silent=True) or {}
    values, error = validate_trek(payload)
    if error:
        return response_error(error)
    if values["staff_id"] and not is_staff(values["staff_id"]):
        return response_error("Assigned staff must be an active staff account.")
    with connect() as db:
        cursor = db.execute(
            """INSERT INTO treks (name, location, difficulty, duration_days, available_slots,
               staff_id, status, start_date, end_date, description, image_url)
               VALUES (:name, :location, :difficulty, :duration_days, :available_slots,
               :staff_id, :status, :start_date, :end_date, :description, :image_url)""",
            values,
        )
        trek_id = cursor.lastrowid
    invalidate_treks()
    return jsonify({"id": trek_id, "message": "Trek created."}), 201


@app.put("/api/admin/treks/<int:trek_id>")
@login_required("admin")
def edit_trek(trek_id):
    payload = request.get_json(silent=True) or {}
    with connect() as db:
        existing = db.execute("SELECT * FROM treks WHERE id = ?", (trek_id,)).fetchone()
        if not existing:
            return response_error("We could not find that trek.", 404)
        values, error = validate_trek(payload, existing)
        if error:
            return response_error(error)
        if values["staff_id"] and not is_staff(values["staff_id"]):
            return response_error("Assigned staff must be an active staff account.")
        db.execute(
            """UPDATE treks SET name=:name, location=:location, difficulty=:difficulty,
               duration_days=:duration_days, available_slots=:available_slots, staff_id=:staff_id,
               status=:status, start_date=:start_date, end_date=:end_date,
               description=:description, image_url=:image_url WHERE id=:id""",
            {**values, "id": trek_id},
        )
    invalidate_treks()
    return jsonify({"message": "Trek updated."})


@app.delete("/api/admin/treks/<int:trek_id>")
@login_required("admin")
def remove_trek(trek_id):
    with connect() as db:
        bookings = db.execute("SELECT COUNT(*) FROM bookings WHERE trek_id = ?", (trek_id,)).fetchone()[0]
        if bookings:
            return response_error("This trek has booking history, so it cannot be deleted. You can close it instead.")
        cursor = db.execute("DELETE FROM treks WHERE id = ?", (trek_id,))
        if not cursor.rowcount:
            return response_error("We could not find that trek.", 404)
    invalidate_treks()
    return jsonify({"message": "Trek removed."})


def is_staff(staff_id):
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE id = ? AND role = 'staff' AND status = 'active'", (staff_id,)
        ).fetchone()
    return bool(row)


@app.get("/api/admin/staff")
@login_required("admin")
def list_staff():
    with connect() as db:
        rows = db.execute(
            """SELECT u.id, u.name, u.email, u.phone, u.status, COUNT(t.id) AS assigned_treks
               FROM users u LEFT JOIN treks t ON t.staff_id = u.id
               WHERE u.role = 'staff' GROUP BY u.id ORDER BY u.name"""
        ).fetchall()
    return jsonify({"staff": [dict(row) for row in rows]})


@app.post("/api/admin/staff")
@login_required("admin")
def create_staff():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    email = str(payload.get("email", "")).strip().lower()
    password = payload.get("password", "")
    if len(name) < 2 or "@" not in email or not isinstance(password, str) or len(password) < 8:
        return response_error("Add a name, valid email, and password of at least 8 characters.")
    try:
        with connect() as db:
            cursor = db.execute(
                """INSERT INTO users (name, email, phone, password_hash, role)
                   VALUES (?, ?, ?, ?, 'staff')""",
                (name, email, str(payload.get("phone", "")).strip(), generate_password_hash(password)),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            return response_error("An account already exists for that email.")
        raise
    return jsonify({"id": cursor.lastrowid, "message": "Staff account created."}), 201


@app.patch("/api/admin/users/<int:user_id>/status")
@login_required("admin")
def update_account_status(user_id):
    payload = request.get_json(silent=True) or {}
    status = payload.get("status")
    if status not in ("active", "deactivated", "blacklisted"):
        return response_error("Choose active, deactivated, or blacklisted.")
    with connect() as db:
        cursor = db.execute(
            "UPDATE users SET status = ? WHERE id = ? AND role IN ('user', 'staff')",
            (status, user_id),
        )
        if not cursor.rowcount:
            return response_error("That user or staff account could not be found.", 404)
    return jsonify({"message": "Account status updated."})


@app.get("/api/admin/users")
@login_required("admin")
def list_users():
    search = request.args.get("q", "").strip()
    role = request.args.get("role", "").strip()
    query = "SELECT id, name, email, phone, role, status, created_at FROM users WHERE role != 'admin'"
    params = []
    if role in ("user", "staff"):
        query += " AND role = ?"
        params.append(role)
    if search:
        query += " AND (name LIKE ? OR email LIKE ?)"
        params.extend((f"%{search}%", f"%{search}%"))
    query += " ORDER BY created_at DESC"
    with connect() as db:
        rows = db.execute(query, params).fetchall()
    return jsonify({"users": [dict(row) for row in rows]})


@app.get("/api/admin/dashboard")
@login_required("admin")
def admin_dashboard():
    with connect() as db:
        stats = {
            "treks": db.execute("SELECT COUNT(*) FROM treks").fetchone()[0],
            "users": db.execute("SELECT COUNT(*) FROM users WHERE role = 'user' AND status = 'active'").fetchone()[0],
            "staff": db.execute("SELECT COUNT(*) FROM users WHERE role = 'staff' AND status = 'active'").fetchone()[0],
            "bookings": db.execute("SELECT COUNT(*) FROM bookings WHERE status = 'Booked'").fetchone()[0],
        }
        recent = db.execute(
            """SELECT b.id, b.status, b.booking_date, u.name AS user_name, t.name AS trek_name
               FROM bookings b JOIN users u ON u.id=b.user_id JOIN treks t ON t.id=b.trek_id
               ORDER BY b.booking_date DESC LIMIT 6"""
        ).fetchall()
    return jsonify({"stats": stats, "recent_bookings": [dict(row) for row in recent]})


@app.get("/api/admin/bookings")
@login_required("admin")
def admin_bookings():
    with connect() as db:
        rows = db.execute(
            """SELECT b.id, b.booking_date, b.status, b.payment_status,
               u.name AS user_name, u.email, t.name AS trek_name, t.location,
               t.start_date, t.end_date FROM bookings b
               JOIN users u ON u.id = b.user_id JOIN treks t ON t.id = b.trek_id
               ORDER BY b.booking_date DESC"""
        ).fetchall()
    return jsonify({"bookings": [dict(row) for row in rows]})


@app.get("/api/staff/dashboard")
@login_required("staff")
def staff_dashboard():
    user = current_user()
    with connect() as db:
        treks = db.execute(
            """SELECT t.*, COUNT(CASE WHEN b.status='Booked' THEN 1 END) AS registered
               FROM treks t LEFT JOIN bookings b ON b.trek_id=t.id
               WHERE t.staff_id=? GROUP BY t.id ORDER BY t.start_date""",
            (user["id"],),
        ).fetchall()
    return jsonify({"treks": [dict(row) for row in treks]})


@app.patch("/api/staff/treks/<int:trek_id>")
@login_required("staff")
def staff_update_trek(trek_id):
    user = current_user()
    payload = request.get_json(silent=True) or {}
    with connect() as db:
        trek = db.execute("SELECT * FROM treks WHERE id = ? AND staff_id = ?", (trek_id, user["id"])).fetchone()
        if not trek:
            return response_error("You can only manage treks assigned to you.", 403)
        slots = payload.get("available_slots", trek["available_slots"])
        status = payload.get("status", trek["status"])
        if isinstance(slots, bool) or not str(slots).isdigit() or status not in ("Open", "Closed", "Completed"):
            return response_error("Enter a non-negative slot count and a valid staff trek status.")
        slots = int(slots)
        db.execute("UPDATE treks SET available_slots = ?, status = ? WHERE id = ?", (slots, status, trek_id))
        if status == "Completed":
            db.execute("UPDATE bookings SET status = 'Completed' WHERE trek_id = ? AND status = 'Booked'", (trek_id,))
    invalidate_treks()
    return jsonify({"message": "Trek details updated."})


@app.get("/api/staff/treks/<int:trek_id>/participants")
@login_required("staff")
def trek_participants(trek_id):
    user = current_user()
    with connect() as db:
        if not db.execute("SELECT id FROM treks WHERE id=? AND staff_id=?", (trek_id, user["id"])).fetchone():
            return response_error("You can only view participants for treks assigned to you.", 403)
        rows = db.execute(
            """SELECT u.id, u.name, u.email, u.phone, b.status, b.booking_date
               FROM bookings b JOIN users u ON u.id=b.user_id
               WHERE b.trek_id=? AND b.status != 'Cancelled' ORDER BY b.booking_date""",
            (trek_id,),
        ).fetchall()
    return jsonify({"participants": [dict(row) for row in rows]})


@app.get("/api/bookings")
@login_required("user")
def user_bookings():
    user = current_user()
    with connect() as db:
        rows = db.execute(
            """SELECT b.id, b.booking_date, b.status, b.payment_status, t.id AS trek_id,
               t.name, t.location, t.difficulty, t.start_date, t.end_date, t.image_url
               FROM bookings b JOIN treks t ON t.id=b.trek_id
               WHERE b.user_id=? ORDER BY b.booking_date DESC""",
            (user["id"],),
        ).fetchall()
    return jsonify({"bookings": [dict(row) for row in rows]})


@app.post("/api/bookings")
@login_required("user")
def book_trek():
    user = current_user()
    payload = request.get_json(silent=True) or {}
    try:
        trek_id = int(payload.get("trek_id"))
    except (ValueError, TypeError):
        return response_error("Choose a trek to book.")
    try:
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            trek = db.execute("SELECT * FROM treks WHERE id = ?", (trek_id,)).fetchone()
            if not trek or trek["status"] != "Open" or trek["start_date"] < date.today().isoformat():
                return response_error("This trek is no longer open for bookings.")
            if trek["available_slots"] < 1:
                return response_error("This trek is full. We hope you find another adventure.")
            if db.execute(
                "SELECT id FROM bookings WHERE user_id = ? AND trek_id = ? AND status != 'Cancelled'",
                (user["id"], trek_id),
            ).fetchone():
                return response_error("You already have an active booking for this trek.")
            prior = db.execute(
                "SELECT id FROM bookings WHERE user_id = ? AND trek_id = ?", (user["id"], trek_id)
            ).fetchone()
            if prior:
                db.execute(
                    "UPDATE bookings SET status='Booked', booking_date=CURRENT_TIMESTAMP WHERE id=?",
                    (prior["id"],),
                )
            else:
                db.execute("INSERT INTO bookings (user_id, trek_id) VALUES (?, ?)", (user["id"], trek_id))
            db.execute("UPDATE treks SET available_slots = available_slots - 1 WHERE id = ?", (trek_id,))
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            return response_error("You already have a booking record for this trek.")
        raise
    invalidate_treks()
    return jsonify({"message": "You're booked! We'll see you on the trail."}), 201


@app.patch("/api/bookings/<int:booking_id>/cancel")
@login_required("user")
def cancel_booking(booking_id):
    user = current_user()
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        booking = db.execute(
            "SELECT b.*, t.id AS trek_id FROM bookings b JOIN treks t ON t.id=b.trek_id WHERE b.id=? AND b.user_id=?",
            (booking_id, user["id"]),
        ).fetchone()
        if not booking:
            return response_error("We could not find that booking.", 404)
        if booking["status"] != "Booked":
            return response_error("Only active bookings can be cancelled.")
        db.execute("UPDATE bookings SET status='Cancelled' WHERE id=?", (booking_id,))
        db.execute("UPDATE treks SET available_slots=available_slots+1 WHERE id=?", (booking["trek_id"],))
    invalidate_treks()
    return jsonify({"message": "Your booking has been cancelled."})


@app.post("/api/exports")
@login_required("user")
def request_export():
    user = current_user()
    with connect() as db:
        cursor = db.execute("INSERT INTO exports (user_id) VALUES (?)", (user["id"],))
        export_id = cursor.lastrowid
    try:
        from tasks import export_booking_history

        export_booking_history.delay(export_id, user["id"])
    except Exception as exc:
        with connect() as db:
            db.execute("UPDATE exports SET status='failed', error=? WHERE id=?", (str(exc)[:500], export_id))
        logger.exception("Could not enqueue CSV export task.")
        return response_error("The export service is unavailable right now. Please try again later.", 503)
    return jsonify({"export_id": export_id, "message": "Your export is being prepared. You can check back here shortly."}), 202


@app.get("/api/exports/<int:export_id>")
@login_required("user")
def export_status(export_id):
    user = current_user()
    with connect() as db:
        row = db.execute(
            "SELECT id, status, error, created_at FROM exports WHERE id=? AND user_id=?",
            (export_id, user["id"]),
        ).fetchone()
    if not row:
        return response_error("We could not find that export.", 404)
    result = dict(row)
    if result["status"] == "completed":
        result["download_url"] = f"/api/exports/{export_id}/download"
    return jsonify({"export": result})


@app.get("/api/exports/<int:export_id>/download")
@login_required("user")
def download_export(export_id):
    user = current_user()
    with connect() as db:
        row = db.execute(
            "SELECT file_path, status FROM exports WHERE id=? AND user_id=?", (export_id, user["id"])
        ).fetchone()
    if not row or row["status"] != "completed" or not row["file_path"]:
        return response_error("Your export is not ready to download yet.", 404)
    path = Path(row["file_path"]).resolve()
    export_dir = (ROOT / "backend" / "exports").resolve()
    if path.parent != export_dir or not path.is_file():
        return response_error("The export file is no longer available.", 404)
    return send_from_directory(export_dir, path.name, as_attachment=True)


@app.get("/api/exports")
@login_required("user")
def list_exports():
    user = current_user()
    with connect() as db:
        rows = db.execute(
            "SELECT id, status, error, created_at FROM exports WHERE user_id=? ORDER BY id DESC LIMIT 10",
            (user["id"],),
        ).fetchall()
    exports = []
    for row in rows:
        item = dict(row)
        if item["status"] == "completed":
            item["download_url"] = f"/api/exports/{item['id']}/download"
        exports.append(item)
    return jsonify({"exports": exports})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=True)
