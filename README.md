# Flight-Log Analytics (ArduPilot + MySQL)

**Status:** in progress (SQL learning project).

Real ArduPilot DataFlash logs (`.BIN`) from my own drone flights, parsed with Python,
loaded into MySQL 8, and analyzed with SQL.

Raw logs are **not** included in this repository because they contain GPS positions.

## Layout
```
data/raw/            # .BIN logs (git-ignored)
data/processed/      # CSV exports (git-ignored)
src/                 # Python pipeline: parse_logs.py, load_mysql.py
sql/schema/          # table and index definitions
sql/analysis/        # my analysis queries
sql/challenges.md    # SQL practice challenges
docs/                # schema diagram, notes
```

## Setup (Windows)
```
py -3.11 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # then edit DB credentials
```

## Findings
_To be added._
