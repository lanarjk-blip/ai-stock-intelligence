"""Streamlit access gate for AI Stock Intelligence.

The dashboard is public only at the transport layer. Application content is
available only after authentication against the runtime API. Admin users can
create/disable Operator accounts without exposing database credentials to the
APK or Streamlit client.

Until a runtime API URL is configured, the gate stays dormant so a partially
configured deployment cannot lock the owner out of the existing dashboard.
"""
from __future__ import annotations

import os

import requests

import persistent_bridge

_TIMEOUT = 10


def _api_base() -> str:
    value = str(os.environ.get("ASI_RUNTIME_API_URL") or persistent_bridge.runtime_api_url() or "").strip()
    return value.rstrip("/")


def _headers(st):
    token = str(st.session_state.get("asi_auth_token") or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _api(method: str, path: str, *, st=None, json=None):
    base = _api_base()
    if not base:
        raise RuntimeError("Runtime API belum dikonfigurasi")
    headers = _headers(st) if st is not None else {}
    response = requests.request(method, base + path, json=json, headers=headers, timeout=_TIMEOUT)
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        raise RuntimeError(str(detail or f"HTTP {response.status_code}"))
    return response.json() if response.content else {}


def _clear(st):
    for key in ("asi_auth_token", "asi_auth_user"):
        st.session_state.pop(key, None)


def _validate_session(st):
    token = str(st.session_state.get("asi_auth_token") or "").strip()
    if not token:
        return None
    try:
        user = _api("GET", "/auth/me", st=st)
        st.session_state["asi_auth_user"] = user
        return user
    except Exception:
        _clear(st)
        return None


def _login_screen(st):
    st.markdown("## 🔐 AI Stock Intelligence")
    st.caption("Akses hanya untuk akun yang diizinkan Admin.")
    with st.form("asi_login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Kata sandi", type="password")
        submitted = st.form_submit_button("Masuk", use_container_width=True)
    if submitted:
        try:
            payload = _api("POST", "/auth/login", json={"username": username, "password": password})
            st.session_state["asi_auth_token"] = payload.get("token")
            st.session_state["asi_auth_user"] = payload.get("user") or {}
            st.rerun()
        except Exception as exc:
            st.error(f"Login gagal: {exc}")
    st.stop()


def _change_password_if_required(st, user):
    if not bool(user.get("must_change_password")):
        return
    st.warning("🔑 Kata sandi sementara harus diganti sebelum dashboard digunakan.")
    with st.form("asi_change_password_required"):
        current = st.text_input("Kata sandi sementara", type="password")
        new = st.text_input("Kata sandi baru", type="password")
        confirm = st.text_input("Ulangi kata sandi baru", type="password")
        submit = st.form_submit_button("Ganti kata sandi", use_container_width=True)
    if submit:
        if new != confirm:
            st.error("Konfirmasi kata sandi tidak sama.")
        elif len(new) < 8:
            st.error("Kata sandi baru minimal 8 karakter.")
        else:
            try:
                _api("POST", "/auth/change-password", st=st, json={"current_password": current, "new_password": new})
                user["must_change_password"] = False
                st.session_state["asi_auth_user"] = user
                st.success("Kata sandi berhasil diganti.")
                st.rerun()
            except Exception as exc:
                st.error(f"Gagal mengganti kata sandi: {exc}")
    st.stop()


def _account_sidebar(st, user):
    with st.sidebar:
        st.markdown("### 👤 Akun")
        st.caption(f"{user.get('display_name') or user.get('username')} • {user.get('role')}")
        if st.button("Keluar", key="asi_logout", use_container_width=True):
            try:
                _api("POST", "/auth/logout", st=st)
            except Exception:
                pass
            _clear(st)
            st.rerun()


def _admin_sidebar(st, user):
    if str(user.get("role") or "").upper() != "ADMIN":
        return
    with st.sidebar.expander("🛡️ Admin • Kelola Operator", expanded=False):
        try:
            users = _api("GET", "/auth/users", st=st)
        except Exception as exc:
            st.error(f"Daftar akun gagal dimuat: {exc}")
            users = []

        if users:
            for item in users:
                name = item.get("display_name") or item.get("username")
                status = "AKTIF" if item.get("active") else "NONAKTIF"
                st.caption(f"{name} • {item.get('role')} • {status}")
                if str(item.get("role")) == "OPERATOR":
                    label = "Nonaktifkan" if item.get("active") else "Aktifkan"
                    if st.button(label, key=f"asi_toggle_{item.get('id')}", use_container_width=True):
                        try:
                            _api(
                                "PATCH",
                                f"/auth/users/{int(item.get('id'))}/active",
                                st=st,
                                json={"active": not bool(item.get("active"))},
                            )
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

        st.markdown("**Tambah Operator**")
        with st.form("asi_create_operator"):
            username = st.text_input("Username operator")
            display_name = st.text_input("Nama operator")
            password = st.text_input("Kata sandi sementara", type="password")
            submit = st.form_submit_button("Buat akun Operator", use_container_width=True)
        if submit:
            try:
                _api(
                    "POST",
                    "/auth/users",
                    st=st,
                    json={
                        "username": username,
                        "display_name": display_name,
                        "password": password,
                        "role": "OPERATOR",
                    },
                )
                st.success("Akun Operator dibuat.")
                st.rerun()
            except Exception as exc:
                st.error(f"Gagal membuat akun: {exc}")


def require_access(st):
    """Block dashboard only after the authentication backend is configured."""
    if not _api_base():
        return {
            "username": "owner",
            "display_name": "Lana Admin",
            "role": "ADMIN",
            "active": True,
            "must_change_password": False,
            "auth_status": "BACKEND_PENDING",
        }
    user = _validate_session(st)
    if user is None:
        _login_screen(st)
    _change_password_if_required(st, user)
    _account_sidebar(st, user)
    _admin_sidebar(st, user)
    return user
