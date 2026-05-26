import atexit
import ctypes
import json
from datetime import datetime
from pathlib import Path
import os
import re
import sys
import time

import pyodbc
import requests

SQL_SERVER = r"(local)\SQLEXPRESS"
DATABASE = "NRT-V2"
SITE_NAME = "Test Site"
SITE_CODE = DATABASE
API_URL = "https://script.google.com/macros/s/your_api_url/exec"
API_KEY = "your_api_key"
DASHBOARD_API_URL = "http://127.0.0.1:5000/api/nrt-status"
GOOGLE_DRIVE_FOLDER_ID = "google_drive_folder_id"
GOOGLE_DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/google_drive_folder_url"

ALERT_THRESHOLD_HOURS = 48
COLLECTION_INTERVAL_MINUTES = 1
STARTUP_LAUNCHER_BASENAME = "NRT Monitor Collector"
STARTUP_LAUNCHER_NAME = f"{STARTUP_LAUNCHER_BASENAME}.vbs"
LEGACY_STARTUP_LAUNCHER_NAME = f"{STARTUP_LAUNCHER_BASENAME}.cmd"
SINGLE_INSTANCE_MUTEX_NAME = "Global\\NRTMonitorCollectorSingleton"
LOG_PATH = Path(__file__).with_name("agent.log")
STATUS_PATH = Path(__file__).with_name("agent_status.json")
STATUS_SUMMARY_PATH = Path(__file__).with_name("agent_status.txt")
SITE_LOGS_DIR = Path(__file__).with_name("site_logs")

CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={DATABASE};"
    "UID=your_UID;"
    "PWD=your_PWD;"
    "Trusted_Connection=yes;" # Comment this if using SQL auth and provide UID/PWD
)

ERROR_ALREADY_EXISTS = 183
_single_instance_handle = None
# Creates a named Windows mutex to ensure only one instance of the application runs.
# Returns False if another instance already exists; otherwise stores the handle for cleanup.
def acquire_single_instance_mutex():
    global _single_instance_handle

    mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX_NAME)
    if not mutex_handle:
        raise OSError("Unable to create collector instance mutex")

    last_error = ctypes.windll.kernel32.GetLastError()
    if last_error == ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(mutex_handle)
        return False

    _single_instance_handle = mutex_handle
    atexit.register(release_single_instance_mutex)
    return True
# Releases the mutex handle during application shutdown
# so other application instances can run later.
def release_single_instance_mutex():
    global _single_instance_handle

    if _single_instance_handle:
        ctypes.windll.kernel32.CloseHandle(_single_instance_handle)
        _single_instance_handle = None


def append_log_entry(message):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(f"[{timestamp}] {message}\n")


def log_message(*parts):
    message = " ".join(str(part) for part in parts)
    print(message)
    append_log_entry(message)


def build_status_snapshot(status_data, delivery_ok):
    return {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "site_name": status_data.get("site_name"),
        "site_code": status_data.get("site_code"),
        "server_name": status_data.get("server_name"),
        "database": status_data.get("database"),
        "checked_at": status_data.get("checked_at"),
        "database_time": status_data.get("database_time"),
        "status": status_data.get("status"),
        "issue_message": status_data.get("issue_message"),
        "batch_count": status_data.get("batch_count"),
        "terminal_count": status_data.get("terminal_count"),
        "last_batch_no": status_data.get("last_batch_no"),
        "latest_operdate": status_data.get("latest_operdate"),
        "oldest_operdate": status_data.get("oldest_operdate"),
        "oldest_age_hours": status_data.get("oldest_age_hours"),
        "delivery_ok": delivery_ok,
        "log_file_name": status_data.get("log_file_name"),
        "log_file_url": status_data.get("log_file_url"),
        "apps_script_url": API_URL,
        "dashboard_api_url": DASHBOARD_API_URL,
        "control_batches": status_data.get("control_batches", []),
    }


def build_status_summary(snapshot):
    lines = [
        f"Updated At: {snapshot.get('updated_at')}",
        f"Site: {snapshot.get('site_name')} ({snapshot.get('site_code')})",
        f"Server: {snapshot.get('server_name')}",
        f"Database: {snapshot.get('database')}",
        f"Checked At: {snapshot.get('checked_at')}",
        f"Database Time: {snapshot.get('database_time')}",
        f"Status: {snapshot.get('status')}",
        f"Issue: {snapshot.get('issue_message')}",
        f"Batch Count: {snapshot.get('batch_count')}",
        f"Terminal Count: {snapshot.get('terminal_count')}",
        f"Last Batch No: {snapshot.get('last_batch_no')}",
        f"Latest Operdate: {snapshot.get('latest_operdate')}",
        f"Oldest Operdate: {snapshot.get('oldest_operdate')}",
        f"Oldest Age Hours: {snapshot.get('oldest_age_hours')}",
        f"Delivery OK: {snapshot.get('delivery_ok')}",
        f"Log File Name: {snapshot.get('log_file_name')}",
        f"Log File URL: {snapshot.get('log_file_url')}",
        f"Apps Script URL: {snapshot.get('apps_script_url')}",
        f"Dashboard API URL: {snapshot.get('dashboard_api_url')}",
    ]
    return "\n".join(lines)


def build_console_status_lines(status_data, delivery_ok):
    return [
        f"Site: {status_data.get('site_name')} ({status_data.get('site_code')})",
        f"Server: {status_data.get('server_name')} | Database: {status_data.get('database')}",
        f"Checked At: {status_data.get('checked_at')} | Database Time: {status_data.get('database_time')}",
        f"Status: {status_data.get('status')} | Delivery OK: {delivery_ok}",
        f"Batch Count: {status_data.get('batch_count')} | Terminal Count: {status_data.get('terminal_count')} | Last Batch No: {status_data.get('last_batch_no')}",
        f"Latest Operdate: {status_data.get('latest_operdate')} | Oldest Operdate: {status_data.get('oldest_operdate')}",
        f"Oldest Age Hours: {status_data.get('oldest_age_hours')}",
        f"Issue: {status_data.get('issue_message')}",
    ]


def write_status_snapshot(snapshot):
    STATUS_PATH.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    STATUS_SUMMARY_PATH.write_text(build_status_summary(snapshot), encoding="utf-8")


def print_latest_status():
    if not STATUS_SUMMARY_PATH.exists():
        print("No collector status has been written yet.")
        print("Run the collector once, or wait for the background collector to finish a cycle.")
        return

    print(STATUS_SUMMARY_PATH.read_text(encoding="utf-8"))


def get_site_log_date(status_data):
    checked_at = status_data.get("checked_at")
    if isinstance(checked_at, str) and checked_at:
        return checked_at.split(" ", 1)[0]
    return datetime.now().strftime("%Y-%m-%d")


def get_site_log_filename(status_data):
    site_id = status_data.get("site_id") or build_site_id()
    return f"{site_id}__{get_site_log_date(status_data)}.log"


def get_site_log_path(status_data):
    SITE_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return SITE_LOGS_DIR / get_site_log_filename(status_data)


def build_site_log_entry(status_data):
    checked_at = status_data.get("checked_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        f"[{checked_at}] Site: {status_data.get('site_name')} ({status_data.get('site_code')})",
        f"[{checked_at}] Server: {status_data.get('server_name')} | Database: {status_data.get('database')}",
        f"[{checked_at}] Status: {status_data.get('status')}",
        f"[{checked_at}] Issue: {status_data.get('issue_message')}",
        f"[{checked_at}] Batch Count: {status_data.get('batch_count')} | Terminal Count: {status_data.get('terminal_count')} | Last Batch No: {status_data.get('last_batch_no')}",
        f"[{checked_at}] Latest Operdate: {status_data.get('latest_operdate')} | Oldest Operdate: {status_data.get('oldest_operdate')} | Oldest Age Hours: {status_data.get('oldest_age_hours')}",
    ]

    control_batches = status_data.get("control_batches") or []
    if control_batches:
        lines.append(f"[{checked_at}] Control Batches:")
        for row in control_batches:
            lines.append(
                "[{checked_at}] - terminal_id={terminal_id} batch_no={batch_no} operdate={operdate} age_hours={age_hours} status={status}".format(
                    checked_at=checked_at,
                    terminal_id=row.get("terminal_id"),
                    batch_no=row.get("batch_no"),
                    operdate=row.get("operdate"),
                    age_hours=row.get("age_hours"),
                    status=row.get("status"),
                )
            )

    return "\n".join(lines) + "\n\n"


def write_site_log(status_data):
    site_log_path = get_site_log_path(status_data)
    with site_log_path.open("a", encoding="utf-8") as site_log_file:
        site_log_file.write(build_site_log_entry(status_data))
    return site_log_path


def build_drive_upload_payload(status_data, site_log_path):
    return {
        "drive_folder_id": GOOGLE_DRIVE_FOLDER_ID,
        "drive_folder_url": GOOGLE_DRIVE_FOLDER_URL,
        "log_file_name": site_log_path.name,
        "log_file_content": site_log_path.read_text(encoding="utf-8"),
        "overwrite_log_file": True,
    }


def get_startup_folder():
    appdata = os.getenv("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not available for the current user")

    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def escape_vbs_string(value):
    return str(value).replace('"', '""')


def resolve_launch_python_executable():
    return Path(sys.executable).resolve()


def build_startup_launcher_content():
    project_dir = Path(__file__).resolve().parent
    python_executable = resolve_launch_python_executable()
    script_path = Path(__file__).resolve()
    launch_command = f'"{python_executable}" "{script_path}"'

    return "\n".join(
        [
            'Set shell = CreateObject("WScript.Shell")',
            f'shell.CurrentDirectory = "{escape_vbs_string(project_dir)}"',
            f'shell.Run "{escape_vbs_string(launch_command)}", 2, False',
            "",
        ]
    )


def install_startup_launcher():
    startup_folder = get_startup_folder()
    startup_folder.mkdir(parents=True, exist_ok=True)
    launcher_path = startup_folder / STARTUP_LAUNCHER_NAME
    launcher_path.write_text(build_startup_launcher_content(), encoding="utf-8")

    legacy_launcher_path = startup_folder / LEGACY_STARTUP_LAUNCHER_NAME
    if legacy_launcher_path.exists():
        legacy_launcher_path.unlink()

    return launcher_path


def remove_startup_launcher():
    startup_folder = get_startup_folder()
    launcher_paths = [
        startup_folder / STARTUP_LAUNCHER_NAME,
        startup_folder / LEGACY_STARTUP_LAUNCHER_NAME,
    ]
    removed_paths = []

    for launcher_path in launcher_paths:
        if launcher_path.exists():
            launcher_path.unlink()
            removed_paths.append(launcher_path)

    return launcher_paths[0], removed_paths

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
    slug = re.sub(r"[^a-z0-9]+", "-", SITE_NAME.lower()).strip("-")
    return slug or "site-status"
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
        log_message(f"{target_name} response:", response.status_code)

        response_payload = None
        if response.text:
            log_message(response.text)
            try:
                response_payload = response.json()
            except ValueError:
                response_payload = None

        if not response.ok:
            return {"ok": False, "payload": response_payload}

        if isinstance(response_payload, dict) and response_payload.get("success") is False:
            return {"ok": False, "payload": response_payload}

        return {"ok": True, "payload": response_payload}
    except requests.RequestException as exc:
        log_message(f"{target_name} send failed: {exc}")
        return {"ok": False, "payload": None}


def send_to_api(data, site_log_path):
    apps_script_payload = dict(data)
    apps_script_payload.update(build_drive_upload_payload(data, site_log_path))
    apps_script_result = post_json(API_URL, apps_script_payload, "Apps Script")

    response_payload = apps_script_result.get("payload")
    if isinstance(response_payload, dict):
        data["log_file_url"] = (
            response_payload.get("log_file_url")
            or response_payload.get("drive_log_url")
            or response_payload.get("file_url")
            or data.get("log_file_url")
        )

    dashboard_ok = {"ok": True, "payload": None}
    if DASHBOARD_API_URL:
        dashboard_ok = post_json(DASHBOARD_API_URL, data, "Dashboard API")

    return {
        "apps_script_ok": apps_script_result["ok"],
        "dashboard_ok": dashboard_ok["ok"],
        "delivery_ok": apps_script_result["ok"] and dashboard_ok["ok"],
        "log_file_url": data.get("log_file_url"),
    }

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

    site_log_path = write_site_log(status_data)
    status_data["log_file_name"] = site_log_path.name

    delivery_result = send_to_api(status_data, site_log_path)
    status_data["log_file_url"] = delivery_result.get("log_file_url")

    delivery_ok = delivery_result["delivery_ok"]
    status_snapshot = build_status_snapshot(status_data, delivery_ok)
    write_status_snapshot(status_snapshot)

    for line in build_console_status_lines(status_data, delivery_ok):
        log_message(line)

    return delivery_ok

# Continuously runs the NRT collector at the configured interval.
def run_collector_loop(interval_minutes=COLLECTION_INTERVAL_MINUTES):
    interval_seconds = max(interval_minutes, 1) * 60
    log_message(
        f"Starting passive collector for {SITE_NAME}. "
        f"Collecting every {interval_minutes} minutes."
    )

    while True:
        started_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message(f"[{started_at}] Running NRT collection cycle")
        if not run_collector():
            log_message("Collector will retry on the next scheduled cycle")
        log_message(f"Sleeping for {interval_minutes} minutes before next cycle")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    if "--install-startup" in sys.argv:
        launcher_path = install_startup_launcher()
        log_message(f"Startup launcher installed at: {launcher_path}")
    elif "--status" in sys.argv:
        print_latest_status()
    elif "--remove-startup" in sys.argv:
        launcher_path, removed_paths = remove_startup_launcher()
        if removed_paths:
            log_message("Startup launcher removed from:")
            for removed_path in removed_paths:
                log_message(f"- {removed_path}")
        else:
            log_message(f"Startup launcher was not present: {launcher_path}")
    else:
        if not acquire_single_instance_mutex():
            log_message("Another NRT collector instance is already running on this PC. Exiting.")
            raise SystemExit(0)

        if "--once" in sys.argv:
            run_collector()
        else:
            run_collector_loop()