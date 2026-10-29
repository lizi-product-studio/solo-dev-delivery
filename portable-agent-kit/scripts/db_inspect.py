#!/usr/bin/env python3
"""Inspect a SQLite database without opening it for writes.

This helper deliberately supports only local SQLite files and read-only
``SELECT``/``WITH`` statements.  It inventories schema objects or returns a
bounded result set; it does not migrate, mutate, vacuum, or infer a remote
database connection.  The caller remains responsible for authorizing the
database path and handling sensitive query results.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


FORMAT = "sqlite-inspection/1"
_SELECT = re.compile(r"^(?:select|with)\b", re.IGNORECASE)
_WRITE = re.compile(r"\b(?:attach|create|delete|detach|drop|insert|alter| pragma|reindex|replace|rollback|vacuum|update)\b", re.IGNORECASE)


def _db_uri(path):
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(str(target))
    return target.as_uri() + "?mode=ro"


def _identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def _select_sql(sql):
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("sql must be a non-empty SELECT statement")
    statement = sql.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if ";" in statement or not _SELECT.match(statement) or _WRITE.search(statement):
        raise ValueError("only one read-only SELECT or WITH statement is allowed")
    return statement


def _connect(path):
    return sqlite3.connect(_db_uri(path), uri=True, timeout=5)


def inventory(path):
    """Return table, column and index metadata from a read-only connection."""
    db = _connect(path)
    try:
        tables = []
        objects = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (name,) in objects:
            columns = [
                {"name": row[1], "type": row[2], "not_null": bool(row[3]),
                 "default": row[4], "primary_key_position": row[5]}
                for row in db.execute("PRAGMA table_info(" + _identifier(name) + ")")
            ]
            indexes = []
            for index_row in db.execute("PRAGMA index_list(" + _identifier(name) + ")"):
                index_name = index_row[1]
                index_columns = [
                    row[2] for row in db.execute("PRAGMA index_info(" + _identifier(index_name) + ")")
                ]
                indexes.append({"name": index_name, "unique": bool(index_row[2]), "columns": index_columns})
            tables.append({"name": name, "columns": columns, "indexes": indexes})
    finally:
        db.close()
    return {"format": FORMAT, "status": "PASS", "database": str(Path(path).expanduser().resolve()), "tables": tables,
            "limits": "Schema metadata only; no writes and no production performance claim."}


def query(path, sql, params=None, limit=100):
    """Run one bounded read-only query and return column names plus rows."""
    statement = _select_sql(sql)
    if isinstance(params, tuple):
        params = list(params)
    if params is None:
        params = []
    if not isinstance(params, list):
        raise ValueError("params must be a JSON list")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 10000:
        raise ValueError("limit must be an integer between 1 and 10000")
    bounded = "SELECT * FROM (" + statement + ") AS _codex_rows LIMIT ?"
    db = _connect(path)
    try:
        cursor = db.execute(bounded, [*params, limit])
        columns = [description[0] for description in cursor.description or []]
        rows = [list(row) for row in cursor.fetchall()]
    finally:
        db.close()
    return {"format": FORMAT, "status": "PASS", "database": str(Path(path).expanduser().resolve()),
            "columns": columns, "rows": rows, "row_count": len(rows), "limit": limit,
            "limits": "Bounded read-only query; results are not redacted or a production performance claim."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--db", required=True, type=Path)
    select = sub.add_parser("query")
    select.add_argument("--db", required=True, type=Path)
    select.add_argument("--sql", required=True)
    select.add_argument("--params", default="[]", help="JSON list of bound parameters")
    select.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            result = inventory(args.db)
        else:
            result = query(args.db, args.sql, json.loads(args.params), args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as error:
        print(json.dumps({"format": FORMAT, "status": "INVALID", "error": str(error)}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
