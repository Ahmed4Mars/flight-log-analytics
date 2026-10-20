"""Run one or more .sql files against MySQL, using credentials from .env.

Usage:
    python src/run_sql.py sql/schema/01_create_tables.sql sql/schema/02_indexes.sql
"""

import sys
from pathlib import Path

from pymysql.constants import CLIENT

from db import connect


def run_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    # database="" : the schema file creates the database itself, so don't require it yet
    with connect(database="", client_flag=CLIENT.MULTI_STATEMENTS) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            while cur.nextset():  # step through every statement's result
                pass
        conn.commit()
    print(f"ok  {path}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for arg in sys.argv[1:]:
        run_file(Path(arg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
