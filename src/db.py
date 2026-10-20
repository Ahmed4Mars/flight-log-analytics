"""Database connection settings, read from .env (never committed)."""

import os
from pathlib import Path

import pymysql
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(REPO_ROOT / ".env")


def connect(database: str | None = None, **kwargs) -> pymysql.Connection:
    """Open a pymysql connection. database=None uses DB_NAME from .env."""
    return pymysql.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database=database if database is not None else os.getenv("DB_NAME", "flight_logs"),
        charset="utf8mb4",
        **kwargs,
    )
