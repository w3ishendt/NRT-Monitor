import json
import re
from datetime import datetime

from flask import Flask, abort, jsonify, render_template, request

from models import get_db_connection, init_db

app = Flask(__name__)


def slugify_site_id(site_name, site_code, server_name):
    raw_value = f"{site_name}-{site_code}-{server_name}"
    slug = re.sub(r"[^a-z0-9]+", "-", raw_value.lower()).strip("-")
    return slug or "site-status"


def save_site_status(data):
    received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    site_id = slugify_site_id(
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
    )


@app.route("/health")
def health_check():
    return jsonify({"status": "ok"}), 200
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