"""One-time Admin bootstrap from deployment environment variables.

ASI_ADMIN_USERNAME and ASI_ADMIN_PASSWORD are read only by the runtime API
process. If no user exists yet, one ADMIN account is created and marked to
change its temporary password on first login. Existing installations are never
overwritten by bootstrap values.
"""
from __future__ import annotations

import os

import auth_api
import persistent_bridge


def bootstrap_from_env():
    username = str(os.environ.get("ASI_ADMIN_USERNAME") or "").strip().lower()
    password = str(os.environ.get("ASI_ADMIN_PASSWORD") or "")
    display_name = str(os.environ.get("ASI_ADMIN_DISPLAY_NAME") or "Lana Admin").strip()
    if not username or len(password) < 8 or not persistent_bridge.postgres_enabled():
        return {"status": "SKIPPED"}

    auth_api._ensure_schema()
    con = persistent_bridge._pg_connect()
    try:
        with con.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM asi_users")
            count = int(cur.fetchone()[0] or 0)
            if count > 0:
                con.rollback()
                return {"status": "EXISTS", "count": count}
            salt, pwd_hash = auth_api._hash_password(password)
            cur.execute(
                """INSERT INTO asi_users
                   (username,display_name,password_salt,password_hash,role,active,must_change_password)
                   VALUES(%s,%s,%s,%s,'ADMIN',TRUE,TRUE)""",
                (username, display_name, salt, pwd_hash),
            )
        con.commit()
        return {"status": "CREATED", "username": username}
    finally:
        con.close()
