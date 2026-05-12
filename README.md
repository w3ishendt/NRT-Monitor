# NRT Monitor

## Project Overview
NRT Monitor is a Python Flask dashboard and site-side collection workflow built to detect failed or delayed Touch 'n Go NRT processing before settlement issues are discovered by clients. A scheduled collector reads site data from SQL Server, evaluates whether control batch records remain stale beyond the configured threshold, and posts the latest status to a central dashboard.

The dashboard is designed as an early-warning view for the internal support team. Only sites with failed NRT detection are shown as status cards, allowing support staff to quickly identify affected locations and open a detailed breakdown of the latest control batch records.

## Project Objectives
The project objective is to provide a lightweight watchdog system for NRT monitoring. It helps the team detect cases where files or lane transaction data are not being processed because of internet issues, lane or PMS transfer failures, Touch 'n Go pickup problems, or backend insertion errors.

The current dashboard behavior follows the monitoring discussion:

- Sites with failed NRT detection are displayed in card format on the main dashboard.
- Clicking a site card reveals the latest site summary and a detailed TNG_CONTROL_BATCH table.
- **Red** indicates stale records older than 48 hours.
- **Orange** indicates stale records under 48 hours but still requiring attention.
- **Green** indicates successful detection and those sites are not shown on the dashboard.

This gives internal support a fast operational view of which sites need investigation before missing or low settlement amounts are reported by clients.

## Tech Stack

| Category              | Tools/Libraries                        |
| --------------------- | -------------------------------------- |
| Frontend              | HTML, CSS, Bootstrap 5, Jinja2         |
| Backend               | Python, Flask                          |
| Data Collection       | requests, pyodbc                       |
| Database              | SQLite, SQL Server (source system)     |
| Environment/Runtime   | Python venv                            |


# Instructions to Run
1. **Clone the repository**
   ```bash
   git clone https://github.com/weishen1113/NRT-Monitor.git
   cd NRT-Monitor
   ```

2. **Create a virtual environment**
   ```bash
   py -m venv venv
   .\venv\Scripts\activate  # On Linux / Mac OS: source venv/bin/activate
   ```
   - If error occurred, run this:
   ```bash
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   
4. **Edit the credentials**
    - In the `agent.py`
   ```bash
   SQL_SERVER = r"your_server_name" # eg.,: “(local)\SQLEXPRESS” or “DESKTOP-TAICUPD\SQLEXPRESS”
    DATABASE = "NRT-V2"
    SITE_NAME = "example: Penang Courtyard"
    SITE_CODE = DATABASE
    API_URL = "http://the_server_api_url_or_local/api/nrt-status"
    
    ALERT_THRESHOLD_HOURS = 48
    COLLECTION_INTERVAL_MINUTES = 40

    CONNECTION_STRING = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={DATABASE};"
        # "UID=your_ssms_uid;"
        # "PWD=your_ssms_pw;"
        "Trusted_Connection=yes;" # Comment this if using SQL auth and provide UID/PWD
    )
   ```

5. **Run the application**
    - Place both program one in the Central Server, another in the client site
    - On the Central Server, run:
   ```bash
   python app.py
   ```
   - On the client's site, run::
   ```bash
   python agent.py
   ```

# User Interface Layout
<p align="center">
<img src="./static/images/ui-layout.png" alt="UI Layout" height="300"/>
</p>