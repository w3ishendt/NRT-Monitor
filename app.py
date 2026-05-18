import json
import os
import re
from datetime import datetime

import requests
from flask import Flask, abort, jsonify, render_template, request

from models import get_db_connection, init_db

app = Flask(__name__)

DEFAULT_GOOGLE_SHEET_SYNC_URL = (
    "https://script.google.com/macros/s/AKfycbxIAwFbkot1Q5X54EbhLik3NLq85KcpYVkeOyh-9Rjgui0nrcI5zIvQaVckHQWAxZju/exec"
)
DEFAULT_GOOGLE_SHEET_API_KEY = "nrt_8F2xQ9mL7vP3zK1cR6wT4yH0bN5sJ8"


def get_google_sync_config():
    return {
        "url": os.getenv("GOOGLE_SHEET_SYNC_URL", DEFAULT_GOOGLE_SHEET_SYNC_URL).strip(),
        "api_key": os.getenv("GOOGLE_SHEET_API_KEY", DEFAULT_GOOGLE_SHEET_API_KEY).strip(),
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
    payload = {}

    if isinstance(payload_json, str) and payload_json:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
    elif isinstance(payload_json, dict):
        payload = dict(payload_json)

    payload.update({
        "site_id": row.get("site_id") or payload.get("site_id"),
        "site_name": row.get("site_name") or payload.get("site_name"),
        "site_code": row.get("site_code") or payload.get("site_code"),
        "server_name": row.get("server_name") or payload.get("server_name"),
        "database": row.get("database") or row.get("database_name") or payload.get("database"),
        "status": row.get("status") or payload.get("status") or "Green",
        "terminal_count": coerce_int(row.get("terminal_count")) if row.get("terminal_count") is not None else payload.get("terminal_count", 0),
        "batch_count": coerce_int(row.get("batch_count")) if row.get("batch_count") is not None else payload.get("batch_count", 0),
        "latest_operdate": row.get("latest_operdate") or payload.get("latest_operdate"),
        "oldest_operdate": row.get("oldest_operdate") or payload.get("oldest_operdate"),
        "oldest_age_hours": coerce_int(row.get("oldest_age_hours")) if row.get("oldest_age_hours") is not None else payload.get("oldest_age_hours"),
        "issue_message": row.get("issue_message") or payload.get("issue_message"),
        "checked_at": row.get("checked_at") or payload.get("checked_at"),
    })

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

    stored_data = dict(data)
    stored_data["site_id"] = site_id
    stored_data["received_at"] = received_at

    conn = get_db_connection()
    try:
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
                received_at
            FROM site_statuses
            WHERE status != 'Green'
            ORDER BY CASE status WHEN 'Red' THEN 0 WHEN 'Orange' THEN 1 ELSE 2 END,
                     site_name COLLATE NOCASE
            """
        ).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]


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
    return json.loads(row["payload_json"])


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


@app.route("/api/nrt-status", methods=["POST"])
def receive_nrt_status():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"message": "Request body must be valid JSON"}), 400

    stored_data = save_site_status(data)

    print("Received NRT status:")
    print(stored_data)

    return jsonify({
        "message": "Status received",
        "site_id": stored_data["site_id"],
        "data": stored_data,
    }), 200


init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True, port=5000)
