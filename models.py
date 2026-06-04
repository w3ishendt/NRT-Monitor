import sqlite3
from pathlib import Path


DB_PATH = Path(__file__).with_name("nrt_dashboard.db")

SITE_STATUSES_TABLE = "site_statuses"
SITE_EMAIL_SETTINGS_TABLE = "site_email_settings"

SITE_STATUSES_COLUMNS = [
    ("site_id", "TEXT PRIMARY KEY"),
    ("site_name", "TEXT NOT NULL"),
    ("site_code", "TEXT"),
    ("database_name", "TEXT"),
    ("server_name", "TEXT"),
    ("status", "TEXT NOT NULL DEFAULT 'Green'"),
    ("terminal_count", "INTEGER NOT NULL DEFAULT 0"),
    ("batch_count", "INTEGER NOT NULL DEFAULT 0"),
    ("latest_operdate", "TEXT"),
    ("oldest_operdate", "TEXT"),
    ("oldest_age_hours", "INTEGER"),
    ("issue_message", "TEXT"),
    ("checked_at", "TEXT"),
    ("received_at", "TEXT"),
    ("payload_json", "TEXT NOT NULL"),
]

SITE_EMAIL_SETTINGS_COLUMNS = [
    ("site_id", "TEXT PRIMARY KEY"),
    ("recipient_emails", "TEXT"),
    ("email_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("last_alert_key", "TEXT"),
    ("last_alert_sent_at", "TEXT"),
    ("last_alert_error", "TEXT"),
    ("updated_at", "TEXT"),
]


def get_db_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def create_site_statuses_table(connection):
    columns_sql = ",\n                ".join(
        f"{column_name} {column_definition}"
        for column_name, column_definition in SITE_STATUSES_COLUMNS
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SITE_STATUSES_TABLE} (
                {columns_sql}
        )
        """
    )


def create_site_email_settings_table(connection):
    columns_sql = ",\n                ".join(
        f"{column_name} {column_definition}"
        for column_name, column_definition in SITE_EMAIL_SETTINGS_COLUMNS
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {SITE_EMAIL_SETTINGS_TABLE} (
                {columns_sql}
        )
        """
    )


def get_existing_columns(connection, table_name):
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row[1] for row in rows}


def add_missing_columns(connection, table_name):
    existing_columns = get_existing_columns(connection, table_name)

    for column_name, column_definition in SITE_STATUSES_COLUMNS:
        if column_name in existing_columns:
            continue

        # SQLite supports ADD COLUMN for non-key columns; the primary key
        # column is present when the table is first created.
        if "PRIMARY KEY" in column_definition.upper():
            continue

        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def create_indexes(connection):
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{SITE_STATUSES_TABLE}_status "
        f"ON {SITE_STATUSES_TABLE}(status)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{SITE_EMAIL_SETTINGS_TABLE}_enabled "
        f"ON {SITE_EMAIL_SETTINGS_TABLE}(email_enabled)"
    )


def init_db():
    connection = get_db_connection()
    try:
        create_site_statuses_table(connection)
        create_site_email_settings_table(connection)
        add_missing_columns(connection, SITE_STATUSES_TABLE)
        add_missing_columns(connection, SITE_EMAIL_SETTINGS_TABLE)
        create_indexes(connection)
        connection.commit()
    finally:
        connection.close()