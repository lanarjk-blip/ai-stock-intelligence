"""V1.8.42 Firebase Cloud Messaging adapter.

Supports Firebase service-account credentials from:
1. A local JSON file path, or
2. JSON content stored securely in environment / Streamlit Secrets.

Never store Firebase private keys in GitHub.
"""

import os
import json
from typing import Dict, Any

import requests


# ============================================================
# CONFIG HELPERS
# ============================================================

def _value(name, cfg, config_key):
    """Read environment first, then config.json fallback."""
    return str(
        os.getenv(
            name,
            cfg.get(config_key, "")
        )
    ).strip()


def _service_account_value(cfg):
    """
    FCM_SERVICE_ACCOUNT_JSON may contain either:
    - path to a JSON file
    - complete Firebase service-account JSON string
    """
    return _value(
        "FCM_SERVICE_ACCOUNT_JSON",
        cfg,
        "fcm_service_account_json"
    )


def _service_account_available(value):
    if not value:
        return False

    # Existing local JSON file.
    if os.path.isfile(value):
        return True

    # JSON stored directly in Streamlit Secrets / environment.
    try:
        data = json.loads(value)
        return bool(
            isinstance(data, dict)
            and data.get("client_email")
            and data.get("private_key")
            and data.get("token_uri")
        )
    except Exception:
        return False


# ============================================================
# STATUS
# ============================================================

def fcm_status(config=None):
    cfg = config or {}

    enabled = (
        _value("FCM_ENABLED", cfg, "fcm_enabled").lower()
        in {"1", "true", "yes", "on"}
    )

    project = _value(
        "FCM_PROJECT_ID",
        cfg,
        "fcm_project_id"
    )

    service_account = _service_account_value(cfg)

    device_token = _value(
        "FCM_DEVICE_TOKEN",
        cfg,
        "fcm_device_token"
    )

    credentials_ready = _service_account_available(
        service_account
    )

    ready = bool(
        enabled
        and project
        and credentials_ready
        and device_token
    )

    if ready:
        mode = "FCM_SEND_READY"
    elif not enabled:
        mode = "QUEUE_ONLY"
    else:
        mode = "NOT_READY"

    return {
        "enabled": enabled,
        "project_id_present": bool(project),
        "service_account_present": bool(service_account),
        "service_account_valid": credentials_ready,
        "device_token_present": bool(device_token),
        "mode": mode,
    }


# ============================================================
# GOOGLE OAUTH
# ============================================================

def _oauth_token(service_account_value):
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request

        scopes = [
            "https://www.googleapis.com/auth/firebase.messaging"
        ]

        # Mode 1: local JSON file.
        if os.path.isfile(service_account_value):

            creds = (
                service_account.Credentials
                .from_service_account_file(
                    service_account_value,
                    scopes=scopes,
                )
            )

        # Mode 2: JSON content from Streamlit Secrets/environment.
        else:

            info = json.loads(
                service_account_value
            )

            creds = (
                service_account.Credentials
                .from_service_account_info(
                    info,
                    scopes=scopes,
                )
            )

        creds.refresh(Request())

        return creds.token

    except Exception as exc:
        raise RuntimeError(
            f"FCM OAuth unavailable: {exc}"
        )


# ============================================================
# SEND FCM
# ============================================================

def send_fcm(
    notification: Dict[str, Any],
    config=None,
    *,
    dry_run=False,
):

    status = fcm_status(config)

    if dry_run:
        return {
            "ok": True,
            "mode": "DRY_RUN",
            "status": status,
            "topic": "Stock Deep Analysis",
            "ticker": notification.get(
                "ticker",
                "",
            ),
        }

    if status["mode"] != "FCM_SEND_READY":
        return {
            "ok": False,
            "mode": status["mode"],
            "error": (
                "FCM belum dikonfigurasi lengkap."
            ),
        }

    cfg = config or {}

    project = _value(
        "FCM_PROJECT_ID",
        cfg,
        "fcm_project_id",
    )

    service_account = _service_account_value(
        cfg
    )

    device = _value(
        "FCM_DEVICE_TOKEN",
        cfg,
        "fcm_device_token",
    )

    oauth_token = _oauth_token(
        service_account
    )

    ticker = str(
        notification.get(
            "ticker",
            "UNKNOWN",
        )
    )

    severity = str(
        notification.get(
            "severity",
            "MEDIUM",
        )
    ).upper()

    title = (
        f"AI Stock Alert • {severity}"
    )

    body = str(
        notification.get("title")
        or notification.get("detail")
        or "Perubahan material terdeteksi"
    )[:180]

    data = {
        "ticker": ticker,
        "severity": severity,
        "notification_id": str(
            notification.get(
                "notification_id",
                "",
            )
        ),
        "screen": "stock_deep_analysis",
    }

    payload = {
        "message": {
            "token": device,

            "notification": {
                "title": title,
                "body": f"{ticker}: {body}",
            },

            "data": data,

            "android": {
                "priority": (
                    "high"
                    if severity == "HIGH"
                    else "normal"
                )
            },
        }
    }

    url = (
        "https://fcm.googleapis.com/v1/"
        f"projects/{project}/messages:send"
    )

    response = requests.post(
        url,
        headers={
            "Authorization": (
                f"Bearer {oauth_token}"
            ),
            "Content-Type": (
                "application/json"
            ),
        },
        json=payload,
        timeout=20,
    )

    try:
        response_data = response.json()
    except Exception:
        response_data = {
            "raw": response.text[:1000]
        }

    return {
        "ok": (
            200
            <= response.status_code
            < 300
        ),
        "status_code": (
            response.status_code
        ),
        "response": response_data,
    }


# ============================================================
# SAFE OFFLINE TEST
# ============================================================

def run_fcm_adapter_test(config=None):

    sample = {
        "ticker": "TEST",
        "severity": "HIGH",
        "title": "FCM adapter test",
        "detail": "Synthetic test only.",
        "notification_id": (
            "TEST-FCM-001"
        ),
    }

    return {
        "status": fcm_status(config),

        "dry_run": send_fcm(
            sample,
            config,
            dry_run=True,
        ),
    }
