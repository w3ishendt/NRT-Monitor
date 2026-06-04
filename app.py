import json
import os
import re
from datetime import datetime
from email.message import EmailMessage
import smtplib
import ssl

import requests
from flask import Flask, abort, jsonify, render_template, request

from models import SITE_EMAIL_SETTINGS_TABLE, get_db_connection, init_db

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

DEFAULT_FLASK_DEBUG = os.getenv("FLASK_DEBUG", "0").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_FLASK_USE_RELOADER = os.getenv("FLASK_USE_RELOADER", "0").strip().lower() in {"1", "true", "yes", "on"}

DEFAULT_GOOGLE_SHEET_SYNC_URL = (
    "https://script.google.com/macros/s/AKfycbxIAwFbkot1Q5X54EbhLik3NLq85KcpYVkeOyh-9Rjgui0nrcI5zIvQaVckHQWAxZju/exec"
)
DEFAULT_GOOGLE_SHEET_API_KEY = "nrt_8F2xQ9mL7vP3zK1cR6wT4yH0bN5sJ8"
DEFAULT_SMTP_SERVER = "mail.dtechdigital.com.my"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_USERNAME = "support@dtechdigital.com.my"
DEFAULT_SMTP_PASSWORD = "n^;J#O76;Xre"
EMAIL_ALERT_THRESHOLD_HOURS = 24


def get_google_sync_config():
    return {
        "url": os.getenv("GOOGLE_SHEET_SYNC_URL", DEFAULT_GOOGLE_SHEET_SYNC_URL).strip(),
        "api_key": os.getenv("GOOGLE_SHEET_API_KEY", DEFAULT_GOOGLE_SHEET_API_KEY).strip(),
    }


def get_smtp_config():
    return {
        "server": os.getenv("SMTP_SERVER", DEFAULT_SMTP_SERVER).strip(),
        "port": int(os.getenv("SMTP_PORT", str(DEFAULT_SMTP_PORT))),
        "username": os.getenv("SMTP_USERNAME", DEFAULT_SMTP_USERNAME).strip(),
        "password": os.getenv("SMTP_PASSWORD", DEFAULT_SMTP_PASSWORD),
    }


def slugify_site_id(site_name, site_code, server_name):
    raw_value = f"{site_name}-{site_code}-{server_name}"
    slug = re.sub(r"[^a-z0-9]+", "-", raw_value.lower()).strip("-")
    return slug or "site-status"


def coerce_int(value):
    if value in (None, ""):
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def coerce_bool(value, default=False):
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def normalize_recipient_emails(value):
    if value in (None, ""):
        return ""

    if isinstance(value, list):
        items = value
    else:
        items = str(value).replace(";", ",").split(",")

    normalized_items = []
    for item in items:
        email = str(item).strip()
        if email and email not in normalized_items:
            normalized_items.append(email)

    return ", ".join(normalized_items)


def split_recipient_emails(value):
    normalized = normalize_recipient_emails(value)
    return [email.strip() for email in normalized.split(",") if email.strip()]


def format_display_datetime(value):
    if value in (None, ""):
        return value

    if not isinstance(value, str):
        return value

    normalized = value.strip()
    if not normalized:
        return normalized

    parse_candidates = [normalized]
    if normalized.endswith("Z"):
        parse_candidates.append(normalized[:-1] + "+00:00")

    for candidate in parse_candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

    return normalized.replace("T", " ").replace(".000Z", "")

def extract_sheet_rows(payload):
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        for key in ("rows", "sites", "statuses", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value

        if any(key in payload for key in ("site_id", "site_name", "status")):
            return [payload]

    raise ValueError("Unsupported Google Sheet response format")

def normalize_sheet_row(row):
    payload_json = row.get("payload_json")
    payload = {
        key: value
        for key, value in row.items()
        if key != "payload_json"
    }

    if isinstance(payload_json, str) and payload_json:
        try:
            payload.update(json.loads(payload_json))
        except json.JSONDecodeError:
            pass
    elif isinstance(payload_json, dict):
        payload.update(payload_json)

    payload.update({
        "site_id": row.get("site_id") or payload.get("site_id"),
        "site_name": row.get("site_name") or payload.get("site_name"),
        "site_code": row.get("site_code") or payload.get("site_code"),
        "server_name": row.get("server_name") or payload.get("server_name"),
        "database": row.get("database") or row.get("database_name") or payload.get("database"),
        "status": row.get("status") or payload.get("status") or "Green",
        "log_file_url": row.get("log_file_url") or row.get("log_url") or row.get("drive_log_url") or payload.get("log_file_url") or payload.get("log_url") or payload.get("drive_log_url"),
        "terminal_count": coerce_int(row.get("terminal_count")) if row.get("terminal_count") is not None else payload.get("terminal_count", 0),
        "batch_count": coerce_int(row.get("batch_count")) if row.get("batch_count") is not None else payload.get("batch_count", 0),
        "latest_operdate": row.get("latest_operdate") or payload.get("latest_operdate"),
        "oldest_operdate": row.get("oldest_operdate") or payload.get("oldest_operdate"),
        "oldest_age_hours": coerce_int(row.get("oldest_age_hours")) if row.get("oldest_age_hours") is not None else payload.get("oldest_age_hours"),
        "issue_message": row.get("issue_message") or payload.get("issue_message"),
        "checked_at": row.get("checked_at") or payload.get("checked_at"),
    })

    for key in ("latest_operdate", "oldest_operdate", "checked_at", "received_at", "database_time", "last_eojdatetime"):
        if key in payload:
            payload[key] = format_display_datetime(payload.get(key))

    control_batches = payload.get("control_batches")
    if isinstance(control_batches, list):
        normalized_batches = []
        for batch in control_batches:
            if not isinstance(batch, dict):
                normalized_batches.append(batch)
                continue

            normalized_batch = dict(batch)
            normalized_batch["operdate"] = format_display_datetime(normalized_batch.get("operdate"))
            normalized_batch["eojdatetime"] = format_display_datetime(normalized_batch.get("eojdatetime"))
            normalized_batches.append(normalized_batch)

        payload["control_batches"] = normalized_batches

    payload["site_id"] = payload.get("site_id") or slugify_site_id(
        payload.get("site_name", "site"),
        payload.get("site_code") or payload.get("database") or "database",
        payload.get("server_name", "server"),
    )

    if payload.get("terminal_count") is None:
        payload["terminal_count"] = 0
    if payload.get("batch_count") is None:
        payload["batch_count"] = 0

    return payload


def build_email_alert_key(site_data):
    batch_count = site_data.get("batch_count") or 0
    oldest_age_hours = coerce_int(site_data.get("oldest_age_hours"))
    if batch_count <= 0 or oldest_age_hours is None or oldest_age_hours < EMAIL_ALERT_THRESHOLD_HOURS:
        return None

    oldest_operdate = site_data.get("oldest_operdate") or "none"
    return f"{site_data.get('site_id')}|{oldest_operdate}"


def build_alert_email_subject(site_data):
    return f"{site_data.get('site_name')} NRT submission failed more than {EMAIL_ALERT_THRESHOLD_HOURS} hours"


def build_alert_email_body(site_data):
    return "\n".join([
        "Dear Sir/Madam,",
        "",
        f"Please be informed that we have detected the NRT submission to TNG has been failing for more than {EMAIL_ALERT_THRESHOLD_HOURS} hours.",
        "",
        "NRT is the transaction settlement/collection mechanism used by TNG. In the event of internet or network interruption at site, the NRT submission process may be affected.",
        "",
        "Kindly check and ensure the internet/network connectivity at site is functioning properly.",
        "",
        "This is an auto-generated email notification.",
        "",
        "Thank you.",
    ])


def get_table_columns(table_name):
    conn = get_db_connection()
    try:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    finally:
        conn.close()

    return {row[1] for row in rows}


def load_site_email_settings(site_id):
    conn = get_db_connection()
    try:
        row = conn.execute(
            f"SELECT * FROM {SITE_EMAIL_SETTINGS_TABLE} WHERE site_id = ?",
            (site_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return {
            "site_id": site_id,
            "recipient_emails": "",
            "email_enabled": False,
            "last_alert_key": None,
            "last_alert_sent_at": None,
            "last_alert_error": None,
            "updated_at": None,
            "site_name": None,
            "site_code": None,
            "database_name": None,
            "server_name": None,
            "status": "Green",
            "terminal_count": 0,
            "batch_count": 0,
            "payload_json": "{}",
        }

    return {
        "site_id": row["site_id"],
        "recipient_emails": normalize_recipient_emails(row["recipient_emails"]),
        "email_enabled": coerce_bool(row["email_enabled"]),
        "last_alert_key": row["last_alert_key"],
        "last_alert_sent_at": row["last_alert_sent_at"],
        "last_alert_error": row["last_alert_error"],
        "updated_at": row["updated_at"],
        "site_name": row["site_name"] if "site_name" in row.keys() else None,
        "site_code": row["site_code"] if "site_code" in row.keys() else None,
        "database_name": row["database_name"] if "database_name" in row.keys() else None,
        "server_name": row["server_name"] if "server_name" in row.keys() else None,
        "status": row["status"] if "status" in row.keys() else "Green",
        "terminal_count": row["terminal_count"] if "terminal_count" in row.keys() else 0,
        "batch_count": row["batch_count"] if "batch_count" in row.keys() else 0,
        "payload_json": row["payload_json"] if "payload_json" in row.keys() else "{}",
    }


def upsert_site_email_settings(site_id, recipient_emails, email_enabled, last_alert_key=None, last_alert_sent_at=None, last_alert_error=None):
    normalized_recipients = normalize_recipient_emails(recipient_emails)
    normalized_enabled = 1 if coerce_bool(email_enabled) else 0
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    existing_settings = load_site_email_settings(site_id)
    table_columns = get_table_columns(SITE_EMAIL_SETTINGS_TABLE)

    row_values = {
        "site_id": site_id,
        "recipient_emails": normalized_recipients,
        "email_enabled": normalized_enabled,
        "last_alert_key": last_alert_key,
        "last_alert_sent_at": last_alert_sent_at,
        "last_alert_error": last_alert_error,
        "updated_at": updated_at,
    }

    if "site_name" in table_columns:
        row_values["site_name"] = existing_settings.get("site_name") or site_id
    if "site_code" in table_columns:
        row_values["site_code"] = existing_settings.get("site_code")
    if "database_name" in table_columns:
        row_values["database_name"] = existing_settings.get("database_name")
    if "server_name" in table_columns:
        row_values["server_name"] = existing_settings.get("server_name")
    if "status" in table_columns:
        row_values["status"] = existing_settings.get("status") or "Green"
    if "terminal_count" in table_columns:
        row_values["terminal_count"] = existing_settings.get("terminal_count") or 0
    if "batch_count" in table_columns:
        row_values["batch_count"] = existing_settings.get("batch_count") or 0
    if "payload_json" in table_columns:
        existing_payload = {}
        payload_json = existing_settings.get("payload_json")
        if isinstance(payload_json, str) and payload_json:
            try:
                existing_payload = json.loads(payload_json)
            except json.JSONDecodeError:
                existing_payload = {}
        existing_payload.update({
            "site_id": site_id,
            "recipient_emails": normalized_recipients,
            "email_enabled": bool(normalized_enabled),
            "last_alert_key": last_alert_key,
            "last_alert_sent_at": last_alert_sent_at,
            "last_alert_error": last_alert_error,
        })
        row_values["payload_json"] = json.dumps(existing_payload)

    column_names = list(row_values.keys())
    placeholders = ", ".join("?" for _ in column_names)
    update_assignments = ",\n                ".join(
        f"{column_name} = excluded.{column_name}"
        for column_name in column_names
        if column_name != "site_id"
    )

    conn = get_db_connection()
    try:
        conn.execute(
            f"""
            INSERT INTO {SITE_EMAIL_SETTINGS_TABLE} (
                {", ".join(column_names)}
            ) VALUES ({placeholders})
            ON CONFLICT(site_id) DO UPDATE SET
                {update_assignments}
            """,
            tuple(row_values[column_name] for column_name in column_names),
        )
        conn.commit()
    finally:
        conn.close()

    return load_site_email_settings(site_id)


def send_site_alert_email(site_data, recipient_emails):
    smtp_config = get_smtp_config()
    recipients = split_recipient_emails(recipient_emails)
    if not recipients:
        raise RuntimeError("No recipient email configured for this site")

    message = EmailMessage()
    message["Subject"] = build_alert_email_subject(site_data)
    message["From"] = smtp_config["username"]
    message["To"] = ", ".join(recipients)
    message.set_content(build_alert_email_body(site_data))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_config["server"], smtp_config["port"], context=context, timeout=30) as smtp_server:
        smtp_server.login(smtp_config["username"], smtp_config["password"])
        smtp_server.send_message(message)


def maybe_send_site_email_alert(site_data):
    site_id = site_data["site_id"]
    settings = load_site_email_settings(site_id)
    alert_key = build_email_alert_key(site_data)

    if not alert_key:
        if settings["last_alert_key"] or settings["last_alert_error"] or settings["last_alert_sent_at"]:
            settings = upsert_site_email_settings(
                site_id,
                settings["recipient_emails"],
                settings["email_enabled"],
                last_alert_key=None,
                last_alert_sent_at=None,
                last_alert_error=None,
            )
        return {"active": False, "sent": False, "reason": "recovered", "settings": settings}

    if not settings["email_enabled"]:
        return {"active": True, "sent": False, "reason": "disabled", "settings": settings}

    if not split_recipient_emails(settings["recipient_emails"]):
        return {"active": True, "sent": False, "reason": "no_recipients", "settings": settings}

    if settings["last_alert_sent_at"]:
        if settings["last_alert_key"] != alert_key:
            settings = upsert_site_email_settings(
                site_id,
                settings["recipient_emails"],
                settings["email_enabled"],
                last_alert_key=alert_key,
                last_alert_sent_at=settings["last_alert_sent_at"],
                last_alert_error=None,
            )
        return {"active": True, "sent": False, "reason": "already_sent_until_recovery", "settings": settings}

    try:
        send_site_alert_email(site_data, settings["recipient_emails"])
        settings = upsert_site_email_settings(
            site_id,
            settings["recipient_emails"],
            settings["email_enabled"],
            last_alert_key=alert_key,
            last_alert_sent_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            last_alert_error=None,
        )
        return {"active": True, "sent": True, "settings": settings}
    except Exception as exc:
        settings = upsert_site_email_settings(
            site_id,
            settings["recipient_emails"],
            settings["email_enabled"],
            last_alert_key=alert_key,
            last_alert_sent_at=settings["last_alert_sent_at"],
            last_alert_error=str(exc),
        )
        return {"active": True, "sent": False, "reason": "send_failed", "error": str(exc), "settings": settings}

def sync_google_sheet_data():
    sync_config = get_google_sync_config()
    sync_url = sync_config["url"]
    sync_api_key = sync_config["api_key"]

    if not sync_url:
        return {"synced": 0, "source": None}

    params = {}
    if sync_api_key:
        params["api_key"] = sync_api_key

    response = requests.get(sync_url, params=params, timeout=15)
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("Google Sheet sync endpoint did not return valid JSON") from exc

    rows = extract_sheet_rows(payload)
    normalized_rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        normalized_rows.append(normalize_sheet_row(row))

    for row in normalized_rows:
        save_site_status(row)

    replace_site_statuses(normalized_rows)

    return {"synced": len(normalized_rows), "source": response.url}

def save_site_status(data):
    received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    site_id = data.get("site_id") or slugify_site_id(
        data.get("site_name", "site"),
        data.get("site_code") or data.get("database") or "database",
        data.get("server_name", "server"),
    )

    conn = get_db_connection()
    try:
        existing_row = conn.execute(
            "SELECT payload_json FROM site_statuses WHERE site_id = ?",
            (site_id,),
        ).fetchone()

        existing_payload = {}
        if existing_row and existing_row["payload_json"]:
            try:
                existing_payload = json.loads(existing_row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                existing_payload = {}

        stored_data = dict(existing_payload)
        stored_data.update(data)
        stored_data["site_id"] = site_id
        stored_data["received_at"] = received_at

        conn.execute(
            """
            INSERT INTO site_statuses (
                site_id,
                site_name,
                site_code,
                database_name,
                server_name,
                status,
                terminal_count,
                batch_count,
                latest_operdate,
                oldest_operdate,
                oldest_age_hours,
                issue_message,
                checked_at,
                received_at,
                payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(site_id) DO UPDATE SET
                site_name = excluded.site_name,
                site_code = excluded.site_code,
                database_name = excluded.database_name,
                server_name = excluded.server_name,
                status = excluded.status,
                terminal_count = excluded.terminal_count,
                batch_count = excluded.batch_count,
                latest_operdate = excluded.latest_operdate,
                oldest_operdate = excluded.oldest_operdate,
                oldest_age_hours = excluded.oldest_age_hours,
                issue_message = excluded.issue_message,
                checked_at = excluded.checked_at,
                received_at = excluded.received_at,
                payload_json = excluded.payload_json
            """,
            (
                site_id,
                stored_data.get("site_name"),
                stored_data.get("site_code"),
                stored_data.get("database"),
                stored_data.get("server_name"),
                stored_data.get("status", "Green"),
                stored_data.get("terminal_count", 0),
                stored_data.get("batch_count", 0),
                stored_data.get("latest_operdate"),
                stored_data.get("oldest_operdate"),
                stored_data.get("oldest_age_hours"),
                stored_data.get("issue_message"),
                stored_data.get("checked_at"),
                received_at,
                json.dumps(stored_data),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return stored_data

def replace_site_statuses(site_rows):
    conn = get_db_connection()
    try:
        current_site_ids = {
            row["site_id"]
            for row in conn.execute("SELECT site_id FROM site_statuses").fetchall()
        }
        incoming_site_ids = {row["site_id"] for row in site_rows}

        stale_site_ids = current_site_ids - incoming_site_ids
        if stale_site_ids:
            placeholders = ", ".join("?" for _ in stale_site_ids)
            conn.execute(
                f"DELETE FROM site_statuses WHERE site_id IN ({placeholders})",
                tuple(stale_site_ids),
            )
            conn.commit()
    finally:
        conn.close()

def load_failed_sites():
    conn = get_db_connection()
    try:
        rows = conn.execute(
            """
            SELECT
                site_id,
                site_name,
                site_code,
                database_name,
                server_name,
                status,
                terminal_count,
                batch_count,
                latest_operdate,
                oldest_operdate,
                oldest_age_hours,
                issue_message,
                checked_at,
                received_at,
                payload_json
            FROM site_statuses
            WHERE status != 'Green'
            ORDER BY CASE status WHEN 'Red' THEN 0 WHEN 'Orange' THEN 1 ELSE 2 END,
                     site_name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    return [normalize_sheet_row(dict(row)) for row in rows]

def load_site_snapshot(site_id):
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT payload_json FROM site_statuses WHERE site_id = ?",
            (site_id,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        abort(404)
    return normalize_sheet_row(json.loads(row["payload_json"]))

@app.route("/")
def dashboard():
    sync_error = None
    sync_result = None

    if get_google_sync_config()["url"]:
        try:
            sync_result = sync_google_sheet_data()
        except (requests.RequestException, ValueError) as exc:
            sync_error = str(exc)

    statuses = load_failed_sites()
    selected_site = None
    selected_site_id = request.args.get("site")

    if statuses and selected_site_id:
        available_site_ids = {site["site_id"] for site in statuses}
        if selected_site_id in available_site_ids:
            selected_site = load_site_snapshot(selected_site_id)
        else:
            selected_site_id = None

    return render_template(
        "dashboard.html",
        statuses=statuses,
        selected_site=selected_site,
        selected_site_id=selected_site_id,
        sync_result=sync_result,
        sync_error=sync_error,
    )

@app.route("/health")
def health_check():
    return jsonify({"status": "ok"}), 200

@app.route("/sync-google-sheet", methods=["POST", "GET"])
def sync_google_sheet():
    if not get_google_sync_config()["url"]:
        return jsonify({"message": "GOOGLE_SHEET_SYNC_URL is not configured"}), 400

    try:
        result = sync_google_sheet_data()
    except (requests.RequestException, ValueError) as exc:
        return jsonify({"message": str(exc)}), 502

    return jsonify({"message": "Google Sheet sync completed", **result}), 200


@app.route("/api/site-email-settings/<site_id>", methods=["GET", "POST"])
def site_email_settings(site_id):
    if request.method == "GET":
        return jsonify(load_site_email_settings(site_id)), 200

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"message": "Request body must be valid JSON"}), 400

    settings = upsert_site_email_settings(
        site_id,
        data.get("recipient_emails", ""),
        data.get("email_enabled", False),
        last_alert_key=data.get("last_alert_key"),
        last_alert_sent_at=data.get("last_alert_sent_at"),
        last_alert_error=data.get("last_alert_error"),
    )
    return jsonify({"message": "Site email settings updated", "data": settings}), 200

@app.route("/api/nrt-status", methods=["POST"])
def receive_nrt_status():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"message": "Request body must be valid JSON"}), 400

    stored_data = save_site_status(data)
    email_result = maybe_send_site_email_alert(stored_data)

    return jsonify({
        "message": "Status received",
        "site_id": stored_data["site_id"],
        "email_result": email_result,
        "data": stored_data,
    }), 200


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        debug=DEFAULT_FLASK_DEBUG,
        use_reloader=DEFAULT_FLASK_USE_RELOADER,
        port=5000,
    )
