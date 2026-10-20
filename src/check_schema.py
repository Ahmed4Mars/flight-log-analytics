"""Read-only check that the schema in MySQL matches sql/schema/*.sql.

Prints tables, primary keys, foreign keys, secondary indexes and lookup
row counts from information_schema. Changes nothing.

Usage:
    python src/check_schema.py
"""

import sys

from db import connect

EXPECTED_TABLES = {
    "flights", "gps", "battery", "attitude", "altitude", "vibration", "params",
    "modes", "events", "errors", "messages",
    "mode_names", "event_names", "error_subsystems",
}
EXPECTED_LOOKUP_ROWS = {"mode_names": 29, "event_names": 49, "error_subsystems": 31}


def main() -> int:
    ok = True
    with connect() as conn, conn.cursor() as cur:
        db = conn.db.decode()

        cur.execute("""
            SELECT table_name, engine
            FROM information_schema.tables
            WHERE table_schema = %s
            ORDER BY table_name""", (db,))
        tables = dict(cur.fetchall())
        print(f"Database {db}: {len(tables)} tables")
        missing = EXPECTED_TABLES - tables.keys()
        extra = tables.keys() - EXPECTED_TABLES
        if missing or extra:
            ok = False
            print(f"  MISSING: {sorted(missing)}  EXTRA: {sorted(extra)}")
        not_innodb = [t for t, e in tables.items() if e != "InnoDB"]
        if not_innodb:
            ok = False
            print(f"  NOT InnoDB (no FKs, no clustered PK): {not_innodb}")

        print("\nIndexes (PRIMARY first; columns in index order):")
        cur.execute("""
            SELECT table_name, index_name, non_unique,
                   GROUP_CONCAT(column_name ORDER BY seq_in_index) AS cols
            FROM information_schema.statistics
            WHERE table_schema = %s
            GROUP BY table_name, index_name, non_unique
            ORDER BY table_name, index_name <> 'PRIMARY', index_name""", (db,))
        for table, index, non_unique, cols in cur.fetchall():
            kind = "PK    " if index == "PRIMARY" else ("index " if non_unique else "unique")
            print(f"  {table:17} {kind} {index:26} ({cols})")

        print("\nForeign keys:")
        cur.execute("""
            SELECT k.table_name, k.constraint_name, k.column_name,
                   k.referenced_table_name, k.referenced_column_name, r.delete_rule
            FROM information_schema.key_column_usage k
            JOIN information_schema.referential_constraints r
              ON r.constraint_schema = k.constraint_schema
             AND r.constraint_name   = k.constraint_name
            WHERE k.table_schema = %s
            ORDER BY k.table_name""", (db,))
        fks = cur.fetchall()
        for table, name, col, ref_table, ref_col, rule in fks:
            print(f"  {table:10} {name:22} {col} -> {ref_table}.{ref_col}  ON DELETE {rule}")
        if len(fks) != 11:  # 10 child tables -> flights, plus modes -> mode_names
            ok = False
            print(f"  EXPECTED 11 foreign keys, found {len(fks)}")

        print("\nLookup rows:")
        for table, expected in EXPECTED_LOOKUP_ROWS.items():
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            n = cur.fetchone()[0]
            flag = "" if n == expected else f"  EXPECTED {expected}"
            ok &= n == expected
            print(f"  {table:17} {n}{flag}")

    print("\nRESULT:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
