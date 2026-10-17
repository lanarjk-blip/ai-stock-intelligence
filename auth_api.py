"""Authentication and role-management API for AI Stock Intelligence.

Uses the existing Neon/Postgres connection through persistent_bridge. Passwords
are stored only as PBKDF2-SHA256 hashes with per-user salts. Session tokens are
never stored in plaintext; only SHA-256 token hashes are persisted.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

import persistent_bridge

ITERATIONS = 310_000
SESSION_DAYS = 30


class LoginBody(BaseModel):
    username: str
    password: str


class CreateUserBody(BaseModel):
    username: str
    display_name: str = ""
    password: str = Field(min_length=8)
    role: str = "OPERATOR"


class ActiveBody(BaseModel):
    active: bool


class ResetPasswordBody(BaseModel):
    password: str = Field(min_length=8)


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


def _ensure_schema():
    if not persistent_bridge.postgres_enabled():
        raise HTTPException(status_code=503, detail="authentication database unavailable")
    con = persistent_bridge._pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                display_name TEXT,
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('ADMIN','OPERATOR')),
                active BOOLEAN NOT NULL DEFAULT TRUE,
                must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                last_login_at TIMESTAMPTZ
            )""")
            cur.execute("""CREATE TABLE IF NOT EXISTS asi_auth_sessions (
                token_hash TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES asi_users(id) ON DELETE CASCADE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                expires_at TIMESTAMPTZ NOT NULL,
                revoked_at TIMESTAMPTZ,
                user_agent TEXT
            )""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_asi_auth_sessions_user ON asi_auth_sessions(user_id)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_asi_auth_sessions_expiry ON asi_auth_sessions(expires_at)")
        con.commit()
    finally:
        con.close()


def _hash_password(password: str, salt: bytes | None = None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


def _verify_password(password: str, salt_text: str, hash_text: str) -> bool:
    try:
        salt = base64.b64decode(salt_text.encode("ascii"))
        expected = base64.b64decode(hash_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _bearer(authorization: str | None) -> str:
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="login required")
    token = value[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="login required")
    return token


def _session_user(authorization: str | None):
    _ensure_schema()
    token = _bearer(authorization)
    con = persistent_bridge._pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute(
                """SELECT u.id,u.username,u.display_name,u.role,u.active,u.must_change_password,s.expires_at
                   FROM asi_auth_sessions s JOIN asi_users u ON u.id=s.user_id
                   WHERE s.token_hash=%s AND s.revoked_at IS NULL""",
                (_token_hash(token),),
            )
            row = cur.fetchone()
        con.rollback()
    finally:
        con.close()
    if not row or not bool(row[4]):
        raise HTTPException(status_code=401, detail="session invalid")
    expires = row[6]
    if expires is None or expires <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="session expired")
    return {
        "id": int(row[0]),
        "username": str(row[1]),
        "display_name": str(row[2] or row[1]),
        "role": str(row[3]),
        "active": bool(row[4]),
        "must_change_password": bool(row[5]),
    }


def _require_admin(authorization: str | None):
    user = _session_user(authorization)
    if user["role"] != "ADMIN":
        raise HTTPException(status_code=403, detail="admin access required")
    return user


def install(app):
    """Register auth routes without requiring a DB connection at import time."""
    if getattr(app.state, "asi_auth_installed", False):
        return app
    app.state.asi_auth_installed = True

    @app.post("/auth/login")
    def login(body: LoginBody, user_agent: str | None = Header(default=None)):
        _ensure_schema()
        username = body.username.strip().lower()
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute(
                    "SELECT id,username,display_name,password_salt,password_hash,role,active,must_change_password FROM asi_users WHERE lower(username)=lower(%s)",
                    (username,),
                )
                row = cur.fetchone()
                if not row or not bool(row[6]) or not _verify_password(body.password, row[3], row[4]):
                    raise HTTPException(status_code=401, detail="username atau kata sandi salah")
                token = secrets.token_urlsafe(32)
                expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
                cur.execute(
                    "INSERT INTO asi_auth_sessions(token_hash,user_id,expires_at,user_agent) VALUES(%s,%s,%s,%s)",
                    (_token_hash(token), int(row[0]), expires, str(user_agent or "")[:500]),
                )
                cur.execute("UPDATE asi_users SET last_login_at=now(),updated_at=now() WHERE id=%s", (int(row[0]),))
            con.commit()
        finally:
            con.close()
        return {
            "token": token,
            "expires_at": expires.isoformat(),
            "user": {
                "id": int(row[0]),
                "username": str(row[1]),
                "display_name": str(row[2] or row[1]),
                "role": str(row[5]),
                "must_change_password": bool(row[7]),
            },
        }

    @app.get("/auth/me")
    def me(authorization: str | None = Header(default=None)):
        return _session_user(authorization)

    @app.post("/auth/logout")
    def logout(authorization: str | None = Header(default=None)):
        _ensure_schema()
        token = _bearer(authorization)
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute("UPDATE asi_auth_sessions SET revoked_at=now() WHERE token_hash=%s", (_token_hash(token),))
            con.commit()
        finally:
            con.close()
        return {"ok": True}

    @app.get("/auth/users")
    def list_users(authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT id,username,display_name,role,active,must_change_password,created_at,last_login_at FROM asi_users ORDER BY id")
                rows = cur.fetchall()
            con.rollback()
        finally:
            con.close()
        return [
            {
                "id": int(r[0]), "username": str(r[1]), "display_name": str(r[2] or r[1]),
                "role": str(r[3]), "active": bool(r[4]), "must_change_password": bool(r[5]),
                "created_at": r[6].isoformat() if r[6] else None,
                "last_login_at": r[7].isoformat() if r[7] else None,
            }
            for r in rows
        ]

    @app.post("/auth/users")
    def create_user(body: CreateUserBody, authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        role = body.role.strip().upper()
        if role not in {"ADMIN", "OPERATOR"}:
            raise HTTPException(status_code=400, detail="invalid role")
        username = body.username.strip().lower()
        if not username:
            raise HTTPException(status_code=400, detail="username required")
        salt, pwd_hash = _hash_password(body.password)
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                try:
                    cur.execute(
                        """INSERT INTO asi_users(username,display_name,password_salt,password_hash,role,active,must_change_password)
                           VALUES(%s,%s,%s,%s,%s,TRUE,TRUE) RETURNING id""",
                        (username, body.display_name.strip(), salt, pwd_hash, role),
                    )
                    user_id = int(cur.fetchone()[0])
                except Exception as exc:
                    con.rollback()
                    raise HTTPException(status_code=409, detail="username sudah digunakan") from exc
            con.commit()
        finally:
            con.close()
        return {"id": user_id, "username": username, "role": role, "active": True}

    @app.patch("/auth/users/{user_id}/active")
    def set_active(user_id: int, body: ActiveBody, authorization: str | None = Header(default=None)):
        admin = _require_admin(authorization)
        if int(user_id) == int(admin["id"]) and not body.active:
            raise HTTPException(status_code=400, detail="admin aktif tidak dapat menonaktifkan dirinya sendiri")
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute("UPDATE asi_users SET active=%s,updated_at=now() WHERE id=%s", (bool(body.active), int(user_id)))
                if not body.active:
                    cur.execute("UPDATE asi_auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (int(user_id),))
            con.commit()
        finally:
            con.close()
        return {"ok": True, "active": bool(body.active)}

    @app.post("/auth/users/{user_id}/password")
    def reset_password(user_id: int, body: ResetPasswordBody, authorization: str | None = Header(default=None)):
        _require_admin(authorization)
        salt, pwd_hash = _hash_password(body.password)
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute(
                    "UPDATE asi_users SET password_salt=%s,password_hash=%s,must_change_password=TRUE,updated_at=now() WHERE id=%s",
                    (salt, pwd_hash, int(user_id)),
                )
                cur.execute("UPDATE asi_auth_sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (int(user_id),))
            con.commit()
        finally:
            con.close()
        return {"ok": True}

    @app.post("/auth/change-password")
    def change_password(body: ChangePasswordBody, authorization: str | None = Header(default=None)):
        user = _session_user(authorization)
        con = persistent_bridge._pg_connect()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT password_salt,password_hash FROM asi_users WHERE id=%s", (int(user["id"]),))
                row = cur.fetchone()
                if not row or not _verify_password(body.current_password, row[0], row[1]):
                    raise HTTPException(status_code=401, detail="kata sandi lama salah")
                salt, pwd_hash = _hash_password(body.new_password)
                cur.execute(
                    "UPDATE asi_users SET password_salt=%s,password_hash=%s,must_change_password=FALSE,updated_at=now() WHERE id=%s",
                    (salt, pwd_hash, int(user["id"])),
                )
            con.commit()
        finally:
            con.close()
        return {"ok": True}

    return app
