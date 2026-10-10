"""V1.8.35 Firebase Cloud Messaging adapter.

Safe by default: FCM live delivery is OFF until FCM_PROJECT_ID and a service-account
JSON path are configured locally. The adapter sends data-only push messages so the
Android app can open Stock Deep Analysis for the affected ticker.
"""
import os, json, time
from typing import Dict, Any
import requests


def fcm_status(config=None):
    cfg=config or {}
    enabled = str(os.getenv("FCM_ENABLED", cfg.get("fcm_enabled", False))).lower() in {"1","true","yes","on"}
    project = os.getenv("FCM_PROJECT_ID", str(cfg.get("fcm_project_id", "")))
    sa = os.getenv("FCM_SERVICE_ACCOUNT_JSON", str(cfg.get("fcm_service_account_json", "")))
    token = os.getenv("FCM_DEVICE_TOKEN", str(cfg.get("fcm_device_token", "")))
    ready = bool(enabled and project and sa and token and os.path.exists(sa))
    return {"enabled": enabled, "project_id_present": bool(project), "service_account_present": bool(sa), "service_account_exists": bool(sa and os.path.exists(sa)), "device_token_present": bool(token), "mode": "FCM_SEND_READY" if ready else ("QUEUE_ONLY" if not enabled else "NOT_READY")}


def _oauth_token(service_account_json: str):
    # Uses google-auth when installed. Kept optional so the base app still runs without it.
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request
        creds = service_account.Credentials.from_service_account_file(service_account_json, scopes=["https://www.googleapis.com/auth/firebase.messaging"])
        creds.refresh(Request())
        return creds.token
    except Exception as exc:
        raise RuntimeError(f"FCM OAuth unavailable: {exc}")


def send_fcm(notification: Dict[str, Any], config=None, *, dry_run=False):
    st=fcm_status(config)
    if dry_run:
        return {"ok": True, "mode": "DRY_RUN", "topic": "Stock Deep Analysis", "ticker": notification.get("ticker", "")}
    if st["mode"] != "FCM_SEND_READY":
        return {"ok": False, "mode": st["mode"], "error": "FCM belum dikonfigurasi lengkap."}
    cfg=config or {}
    project=os.getenv("FCM_PROJECT_ID", str(cfg.get("fcm_project_id", "")))
    sa=os.getenv("FCM_SERVICE_ACCOUNT_JSON", str(cfg.get("fcm_service_account_json", "")))
    device=os.getenv("FCM_DEVICE_TOKEN", str(cfg.get("fcm_device_token", "")))
    token=_oauth_token(sa)
    ticker=str(notification.get("ticker", "UNKNOWN"))
    sev=str(notification.get("severity", "MEDIUM")).upper()
    title=f"AI Stock Alert • {sev}"
    body=str(notification.get("title") or notification.get("detail") or "Perubahan material terdeteksi")[:180]
    data={"ticker":ticker,"severity":sev,"notification_id":str(notification.get("notification_id", "")),"screen":"stock_deep_analysis"}
    payload={"message":{"token":device,"notification":{"title":title,"body":f"{ticker}: {body}"},"data":data,"android":{"priority":"high" if sev=="HIGH" else "normal"}}}
    url=f"https://fcm.googleapis.com/v1/projects/{project}/messages:send"
    r=requests.post(url,headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json=payload,timeout=20)
    try: resp=r.json()
    except Exception: resp={"raw":r.text[:1000]}
    return {"ok":200 <= r.status_code < 300,"status_code":r.status_code,"response":resp}


def run_fcm_adapter_test(config=None):
    sample={"ticker":"TEST","severity":"HIGH","title":"FCM adapter test","detail":"Synthetic test only.","notification_id":"TEST-FCM-001"}
    return {"status":fcm_status(config),"dry_run":send_fcm(sample,config,dry_run=True)}
