import pyodbc
import requests
from datetime import datetime
import sys
import time

SQL_SERVER = r"(local)\SQLEXPRESS"
DATABASE = "NRT-V2"
SITE_NAME = "Test Site"
SITE_CODE = DATABASE
API_URL = "http://the_server_api_url_or_local/api/nrt-status"

ALERT_THRESHOLD_HOURS = 48
COLLECTION_INTERVAL_MINUTES = 40

CONNECTION_STRING = (
    "DRIVER={ODBC Driver 17 for SQL Server};"
    f"SERVER={SQL_SERVER};"
    f"DATABASE={DATABASE};"
    # "UID=your_UID;"
    # "PWD=your_PWD;"
    "Trusted_Connection=yes;" # Comment this if using SQL auth and provide UID/PWD
)


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


def collect_nrt_status():
    conn = pyodbc.connect(CONNECTION_STRING)
    try:
        cursor = conn.cursor()

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


def send_to_api(data):
    try:
        response = requests.post(API_URL, json=data, timeout=10)
        print("API response:", response.status_code)
        print(response.text)
        return True
    except requests.RequestException as exc:
        print(f"API send failed: {exc}")
        return False


def build_error_payload(error_message):
    return {
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
    if "--once" in sys.argv:
        run_collector()
    else:
        run_collector_loop()