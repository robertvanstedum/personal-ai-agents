"""
minimoi_portal/domain_auth.py — PostgreSQL-backed auth for multi-user domains.

Separate from auth.py (JSON-based auth for Robert) — this handles domain
users (e.g. daughters) who access via one-time login links and then
email + password.
"""

import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from werkzeug.security import check_password_hash, generate_password_hash

_DEFAULT_DB_URL = "postgresql://minimoi:simple123@localhost:5432/personal_agents"


def _db_url() -> str:
    return os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)


def _query(sql: str, params=None) -> list:
    conn = psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        rows = list(cur.fetchall())
    conn.close()
    return rows


def _execute(sql: str, params=None):
    conn = psycopg2.connect(_db_url())
    result = None
    with conn.cursor() as cur:
        cur.execute(sql, params or [])
        if cur.description:
            result = cur.fetchone()
    conn.commit()
    conn.close()
    return result


def create_user(email: str, name: str, role: str = "user") -> int:
    """Create or update an auth.users record. Returns the user id."""
    row = _execute(
        "INSERT INTO auth.users (email, name, role) VALUES (%s, %s, %s) "
        "ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name RETURNING id",
        [email.lower().strip(), name, role],
    )
    return row[0]


def grant_domain_access(user_id: int, domain: str, granted_by_id: int = None) -> None:
    _execute(
        "INSERT INTO auth.domain_access (user_id, domain, granted_by) "
        "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
        [user_id, domain, granted_by_id],
    )


def create_login_token(user_id: int) -> str:
    """Generate a 48h single-use login token. Returns the token string."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
    _execute(
        "INSERT INTO auth.login_tokens (user_id, token, expires_at) VALUES (%s, %s, %s)",
        [user_id, token, expires_at],
    )
    return token


def consume_login_token(token: str) -> dict | None:
    """
    Validate, mark used, and return user dict for a login token.
    Returns None if not found, expired, already used, or user inactive.
    Uses FOR UPDATE to prevent duplicate consumption from concurrent requests.
    """
    conn = psycopg2.connect(_db_url(), cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT t.id, t.user_id, t.expires_at, t.used, "
                "       u.email, u.name, u.role, u.is_active "
                "FROM auth.login_tokens t "
                "JOIN auth.users u ON u.id = t.user_id "
                "WHERE t.token = %s FOR UPDATE",
                [token],
            )
            row = cur.fetchone()
            if not row or row["used"] or not row["is_active"]:
                return None
            expires_at = row["expires_at"]
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                return None
            cur.execute(
                "UPDATE auth.login_tokens SET used=TRUE WHERE id=%s", [row["id"]]
            )
            cur.execute(
                "UPDATE auth.users SET last_login=NOW() WHERE id=%s", [row["user_id"]]
            )
        conn.commit()
        return {
            "id": row["user_id"],
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
        }
    except Exception:
        conn.rollback()
        return None
    finally:
        conn.close()


def get_user_by_id(user_id: int) -> dict | None:
    rows = _query("SELECT * FROM auth.users WHERE id=%s", [user_id])
    return dict(rows[0]) if rows else None


def get_user_by_email(email: str) -> dict | None:
    rows = _query("SELECT * FROM auth.users WHERE email=%s", [email.lower().strip()])
    return dict(rows[0]) if rows else None


def set_password(user_id: int, password: str) -> None:
    pw_hash = generate_password_hash(password)
    _execute("UPDATE auth.users SET password_hash=%s WHERE id=%s", [pw_hash, user_id])


def authenticate_password(email: str, password: str) -> dict | None:
    """Returns user dict on success, None on failure."""
    user = get_user_by_email(email)
    if not user or not user.get("password_hash") or not user.get("is_active"):
        return None
    if not check_password_hash(user["password_hash"], password):
        return None
    _execute("UPDATE auth.users SET last_login=NOW() WHERE id=%s", [user["id"]])
    return user


def has_domain_access(user_id: int, domain: str) -> bool:
    rows = _query(
        "SELECT 1 FROM auth.domain_access WHERE user_id=%s AND domain=%s",
        [user_id, domain],
    )
    return len(rows) > 0


def list_users_with_access() -> list:
    """All users with their comma-separated domain list, newest first."""
    return _query(
        "SELECT u.id, u.email, u.name, u.role, u.created_at, u.last_login, u.is_active, "
        "       STRING_AGG(da.domain, ', ' ORDER BY da.domain) AS domains "
        "FROM auth.users u "
        "LEFT JOIN auth.domain_access da ON da.user_id = u.id "
        "GROUP BY u.id ORDER BY u.created_at DESC"
    )
