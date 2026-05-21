import pyodbc
import requests
from datetime import datetime
from pathlib import Path
import os
import sys
import time

SQL_SERVER = r"(local)\SQLEXPRESS"
DATABASE = "NRT-V2"
SITE_NAME = "Test Site"
SITE_CODE = DATABASE
API_URL = "https://script.google.com/macros/s/your_api_url/exec"
API_KEY = "your_api_key"
DASHBOARD_API_URL = "http://127.0.0.1:5000/api/nrt-status"

ALERT_THRESHOLD_HOURS = 48
COLLECTION_INTERVAL_MINUTES = 40
STARTUP_LAUNCHER_NAME = "NRT Monitor Collector.cmd"

CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={DATABASE};"
    "UID=your_UID;"
    "PWD=your_PWD;"
    "Trusted_Connection=yes;" # Comment this if using SQL auth and provide UID/PWD
)


def get_startup_folder():
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not available for the current user")

    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def build_startup_launcher_content():
    project_dir = Path(__file__).resolve().parent
    python_executable = Path(sys.executable).resolve()
    script_path = Path(__file__).resolve()

    return "\n".join(
        [
            "@echo off",
            "setlocal",
            f'cd /d "{project_dir}"',
            f'"{python_executable}" "{script_path}"',
            "endlocal",
            "",
        ]
    )


def install_startup_launcher():
    startup_folder = get_startup_folder()
    startup_folder.mkdir(parents=True, exist_ok=True)
    launcher_path = startup_folder / STARTUP_LAUNCHER_NAME
    launcher_path.write_text(build_startup_launcher_content(), encoding="utf-8")
    return launcher_path


def remove_startup_launcher():
    launcher_path = get_startup_folder() / STARTUP_LAUNCHER_NAME
    if launcher_path.exists():
        launcher_path.unlink()
        return launcher_path, True
    return launcher_path, False

def serialize_datetime(value):
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None

def get_age_hours(reference_time, target_time):
    if not reference_time or not target_time:
        return None
    return max(int((reference_time - target_time).total_seconds() // 3600), 0)

def get_color_status(record_count, age_hours):
    if record_count == 0 or age_hours is None:
        return "Green"
    if age_hours >= ALERT_THRESHOLD_HOURS:
        return "Red"
    return "Orange"

def build_site_id():
    return SITE_NAME.lower().replace(" ", "-")
# Generate human-readable NRT status message based on monitoring result.
# Returns Green/Orange/Red alert message with pending batch information.
def build_issue_message(status, batch_count, oldest_age_hours):
    if status == "Green":
        return "NRT healthy - no stale control batch records detected"
    if status == "Orange":
        return (
            f"NRT warning - {batch_count} control batch records pending, "
            f"oldest operdate is {oldest_age_hours} hours old"
        )
    return (
        f"NRT alert - {batch_count} control batch records pending, "
        f"oldest operdate is {oldest_age_hours} hours old"
    )
# Connect to SQL Server and collect latest TB_TNG_CONTROL_BATCH records.
# Used to calculate NRT health, stale records, and dashboard status.
def collect_nrt_status():
    conn = pyodbc.connect(CONNECTION_STRING)
    try:
        cursor = conn.cursor()
        # Retrieve current SQL Server datetime for consistent monitoring calculations.
        # Avoids relying on local machine/server system time.
        cursor.execute("SELECT GETDATE() AS current_db_time")
        database_time = cursor.fetchone()[0]

        cursor.execute("""
            SELECT
                SITEID,
                TERMINALID,
                BATCHNO,
                BATCHTRANTYPE,
                OPERDATE,
                EOJDATETIME,
                RECORD_COUNT
            FROM dbo.TNG_CONTROL_BATCH
            ORDER BY OPERDATE DESC, BATCHNO DESC
        """)

        control_batch_rows = cursor.fetchall()
    finally:
        conn.close()

    batch_count = len(control_batch_rows)
    latest_batch = control_batch_rows[0] if control_batch_rows else None
    oldest_operdate = min((row.OPERDATE for row in control_batch_rows), default=None)
    oldest_age_hours = get_age_hours(database_time, oldest_operdate)
    terminal_count = len({row.TERMINALID for row in control_batch_rows})
    status = get_color_status(batch_count, oldest_age_hours)

    control_batches = []
    for row in control_batch_rows:
        row_age_hours = get_age_hours(database_time, row.OPERDATE)
        control_batches.append({
            "site_id": row.SITEID,
            "terminal_id": row.TERMINALID,
            "batch_no": row.BATCHNO,
            "batch_tran_type": row.BATCHTRANTYPE,
            "operdate": serialize_datetime(row.OPERDATE),
            "eojdatetime": serialize_datetime(row.EOJDATETIME),
            "record_count": row.RECORD_COUNT,
            "age_hours": row_age_hours,
            "status": get_color_status(1, row_age_hours),
        })

    return {
        "api_key": API_KEY,
        "site_id": build_site_id(),
        "site_name": SITE_NAME,
        "site_code": SITE_CODE,
        "server_name": SQL_SERVER,
        "database": DATABASE,
        "database_time": serialize_datetime(database_time),
        "last_batch_no": latest_batch.BATCHNO if latest_batch else None,
        "latest_operdate": serialize_datetime(latest_batch.OPERDATE) if latest_batch else None,
        "last_eojdatetime": serialize_datetime(latest_batch.EOJDATETIME) if latest_batch else None,
        "batch_count": batch_count,
        "terminal_count": terminal_count,
        "oldest_operdate": serialize_datetime(oldest_operdate),
        "oldest_age_hours": oldest_age_hours,
        "status": status,
        "issue_message": build_issue_message(status, batch_count, oldest_age_hours),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "control_batches": control_batches,
    }

def get_nrt_status():
    return collect_nrt_status()

def post_json(url, data, target_name):
    try:
        response = requests.post(url, json=data, timeout=10)
        print(f"{target_name} response:", response.status_code)

        response_payload = None
        if response.text:
            print(response.text)
            try:
                response_payload = response.json()
            except ValueError:
                response_payload = None

        if not response.ok:
            return False

        if isinstance(response_payload, dict) and response_payload.get("success") is False:
            return False

        return True
    except requests.RequestException as exc:
        print(f"{target_name} send failed: {exc}")
        return False


def send_to_api(data):
    apps_script_ok = post_json(API_URL, data, "Apps Script")

    dashboard_ok = True
    if DASHBOARD_API_URL:
        dashboard_ok = post_json(DASHBOARD_API_URL, data, "Dashboard API")

    return apps_script_ok and dashboard_ok

def build_error_payload(error_message):
    return {
        "api_key": API_KEY,
        "site_id": build_site_id(),
        "site_name": SITE_NAME,
        "site_code": SITE_CODE,
        "server_name": SQL_SERVER,
        "database": DATABASE,
        "status": "Red",
        "issue_message": f"Collector error: {error_message}",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "batch_count": 0,
        "terminal_count": 0,
        "oldest_operdate": None,
        "oldest_age_hours": None,
        "latest_operdate": None,
        "last_batch_no": None,
        "last_eojdatetime": None,
        "database_time": None,
        "control_batches": [],
    }

# Runs one NRT collection cycle and sends the result to the API.
def run_collector():
    try:
        status_data = collect_nrt_status()
    except Exception as exc:
        status_data = build_error_payload(str(exc))

    print(status_data)
    return send_to_api(status_data)

# Continuously runs the NRT collector at the configured interval.
def run_collector_loop(interval_minutes=COLLECTION_INTERVAL_MINUTES):
    interval_seconds = max(interval_minutes, 1) * 60
    print(
        f"Starting passive collector for {SITE_NAME}. "
        f"Collecting every {interval_minutes} minutes."
    )

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{started_at}] Running NRT collection cycle")
        if not run_collector():
            print("Collector will retry on the next scheduled cycle")
        print(f"Sleeping for {interval_minutes} minutes before next cycle")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    if "--install-startup" in sys.argv:
        launcher_path = install_startup_launcher()
        print(f"Startup launcher installed at: {launcher_path}")
    elif "--remove-startup" in sys.argv:
        launcher_path, removed = remove_startup_launcher()
        if removed:
            print(f"Startup launcher removed from: {launcher_path}")
        else:
            print(f"Startup launcher was not present: {launcher_path}")
    elif "--once" in sys.argv:
        run_collector()
    else:
        run_collector_loop()