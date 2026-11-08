import csv
import logging
import os
import smtplib
from datetime import datetime, timedelta
from html import escape
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from celery import Celery
from celery.schedules import crontab

from db import connect

logger = logging.getLogger(__name__)
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
celery = Celery("trailwise", broker=REDIS_URL, backend=REDIS_URL)
celery.conf.update(
    timezone="Asia/Kolkata",
    enable_utc=True,
    beat_schedule={
        "daily-upcoming-trek-reminders": {
            "task": "tasks.send_daily_reminders",
            "schedule": crontab(minute=0, hour=8),
        },
        "monthly-admin-activity-report": {
            "task": "tasks.send_monthly_report",
            "schedule": crontab(minute=0, hour=9, day_of_month=1),
        },
    },
)
EXPORT_DIR = Path(__file__).resolve().parent / "exports"


@celery.task(name="tasks.export_booking_history")
def export_booking_history(export_id, user_id):
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    target = EXPORT_DIR / f"booking-history-{user_id}-{export_id}.csv"
    try:
        with connect() as db:
            rows = db.execute(
                """SELECT u.id AS user_id, t.name AS trek_name, t.location,
                   b.status AS booking_status, b.booking_date, t.start_date, t.end_date
                   FROM bookings b JOIN treks t ON t.id=b.trek_id
                   JOIN users u ON u.id=b.user_id
                   WHERE b.user_id=? ORDER BY b.booking_date DESC""",
                (user_id,),
            ).fetchall()
            with target.open("w", newline="", encoding="utf-8-sig") as file:
                writer = csv.writer(file)
                writer.writerow(
                    ["User ID", "Trek Name", "Location", "Booking Status", "Booking Date", "Start Date", "End Date"]
                )
                writer.writerows([tuple(row) for row in rows])
            db.execute(
                "UPDATE exports SET status='completed', file_path=?, error=NULL WHERE id=? AND user_id=?",
                (str(target.resolve()), export_id, user_id),
            )
        return str(target)
    except Exception as exc:
        logger.exception("Booking export %s failed.", export_id)
        with connect() as db:
            db.execute("UPDATE exports SET status='failed', error=? WHERE id=?", (str(exc)[:500], export_id))
        raise


def deliver_email(subject, html, recipient):
    host = os.environ.get("SMTP_HOST")
    if not host or not recipient:
        logger.info("Email not sent: configure SMTP_HOST and recipient email.")
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("SMTP_FROM", "hello@trailwise.local")
    message["To"] = recipient
    message.set_content("Please view this message in an HTML-capable email client.")
    message.add_alternative(html, subtype="html")
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=20) as server:
        server.starttls()
        username = os.environ.get("SMTP_USERNAME")
        if username:
            server.login(username, os.environ.get("SMTP_PASSWORD", ""))
        server.send_message(message)
    return True


@celery.task(name="tasks.send_daily_reminders")
def send_daily_reminders():
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    through = today + timedelta(days=2)
    with connect() as db:
        users = db.execute(
            """SELECT DISTINCT u.id, u.name, u.email, t.name AS trek_name, t.location,
               t.start_date, t.description FROM bookings b
               JOIN users u ON u.id=b.user_id JOIN treks t ON t.id=b.trek_id
               WHERE b.status='Booked' AND t.start_date BETWEEN ? AND ? AND u.status='active'""",
            (today.isoformat(), through.isoformat()),
        ).fetchall()
    webhook = os.environ.get("GOOGLE_CHAT_WEBHOOK_URL")
    delivered = 0
    for row in users:
        text = (
            f"Hi {row['name']}! A little trail reminder: {row['trek_name']} in {row['location']} "
            f"starts on {row['start_date']}. {row['description']} Pack water, check the weather, "
            "and give yourself a good night's rest. See you out there!"
        )
        if webhook:
            response = requests.post(webhook, json={"text": text}, timeout=15)
            response.raise_for_status()
            delivered += 1
        else:
            logger.info("Reminder for %s: %s", row["email"], text)
    return {"reminders_sent": delivered, "bookings_found": len(users)}


@celery.task(name="tasks.send_monthly_report")
def send_monthly_report():
    month = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m")
    with connect() as db:
        conducted = db.execute(
            "SELECT COUNT(*) FROM treks WHERE status='Completed' AND substr(end_date, 1, 7)=?",
            (month,),
        ).fetchone()[0]
        participants = db.execute(
            """SELECT COUNT(DISTINCT b.user_id) FROM bookings b JOIN treks t ON t.id=b.trek_id
               WHERE b.status='Completed' AND substr(t.end_date, 1, 7)=?""",
            (month,),
        ).fetchone()[0]
        popular = db.execute(
            """SELECT t.name, t.location, COUNT(b.id) AS participants
               FROM treks t LEFT JOIN bookings b ON b.trek_id=t.id AND b.status='Completed'
               WHERE substr(t.end_date, 1, 7)=? GROUP BY t.id ORDER BY participants DESC LIMIT 5""",
            (datetime.now().strftime("%Y-%m"),),
        ).fetchall()
        admin = db.execute("SELECT email FROM users WHERE role='admin' LIMIT 1").fetchone()
    rows = "".join(
    f"<tr><td>{escape(item['name'])}</td><td>{escape(item['location'])}</td><td>{item['participants']}</td></tr>"
        for item in popular
    ) or "<tr><td colspan='3'>No completed treks yet. Here is to next month's adventures.</td></tr>"
    html = f"""<html><body style="font-family:Arial,sans-serif;color:#26382d">
      <h1>Your Trailwise monthly recap</h1><p>A little snapshot of the journeys your community shared.</p>
      <p><strong>Treks completed:</strong> {conducted}<br>
      <strong>Unique participants:</strong> {participants}</p>
      <h2>Community favourites</h2><table cellpadding="8" cellspacing="0" border="1">
      <tr><th>Trek</th><th>Location</th><th>Participants</th></tr>{rows}</table>
      <p>Generated on {datetime.now(ZoneInfo('Asia/Kolkata')).strftime('%B %d, %Y')}.</p></body></html>"""
    sent = deliver_email("Trailwise monthly activity report", html, admin["email"] if admin else None)
    if not sent:
        logger.info("Monthly activity report generated but not emailed.")
    return {"treks_conducted": conducted, "participants": participants, "email_sent": sent}
