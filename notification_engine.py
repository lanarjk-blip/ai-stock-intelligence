"""V1.8.32 Notification Queue & WhatsApp delivery engine.

This module deliberately separates AI alert generation from notification delivery.
It aggregates multiple alert events for the same ticker into one human-readable
notification, applies a cooldown/fingerprint filter, and writes a local outbox.
No WhatsApp/network call is made until a real adapter is configured.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Iterable

SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

TYPE_PRIORITY = [
    "TOP_5_ENTRY",
    "OUTSIDE_TOP5_OPPORTUNITY",
    "ENTRY_WINDOW",
    "DECISION",
    "RISK",
    "AI_SCORE",
    "HOLD",
    "OVERNIGHT",
    "NEXT_SESSION",
    "RANK",
]

EMOJI = {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🔵"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sev(v: Any) -> str:
    s = str(v or "MEDIUM").upper()
    return s if s in SEVERITY_ORDER else "MEDIUM"


def _dedupe(items: Iterable[str]) -> List[str]:
    out = []
    seen = set()
    for item in items:
        s = str(item or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def aggregate_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge multiple alert events for one ticker into one notification."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for alert in alerts or []:
        ticker = str(alert.get("ticker") or "").upper().strip()
        if ticker:
            groups.setdefault(ticker, []).append(dict(alert))

    result = []
    for ticker, items in groups.items():
        items = sorted(
            items,
            key=lambda a: (
                SEVERITY_ORDER.get(_sev(a.get("severity")), 2),
                TYPE_PRIORITY.index(a.get("alert_type")) if a.get("alert_type") in TYPE_PRIORITY else 99,
            ),
        )
        primary = items[0]
        severity = _sev(primary.get("severity"))
        alert_types = _dedupe(a.get("alert_type") for a in items)
        details = _dedupe(a.get("detail") for a in items)
        reasons = details[:5]

        result.append({
            "notification_id": "",
            "created_at": _now(),
            "ticker": ticker,
            "company": str(primary.get("company") or ticker),
            "severity": severity,
            "title": str(primary.get("title") or "Perubahan material"),
            "primary_alert_type": str(primary.get("alert_type") or "ALERT"),
            "alert_types": alert_types,
            "source_alert_count": len(items),
            "reasons": reasons,
            "old_value": str(primary.get("old_value") or "—"),
            "new_value": str(primary.get("new_value") or "—"),
            "delivery_channel": "WHATSAPP_READY",
            "delivery_status": "QUEUED",
        })

    return sorted(
        result,
        key=lambda n: (
            SEVERITY_ORDER.get(n["severity"], 2),
            n["ticker"],
        ),
    )


def notification_fingerprint(notification: Dict[str, Any]) -> str:
    """Stable-ish fingerprint for cooldown/deduplication."""
    types = ",".join(sorted(notification.get("alert_types") or []))
    reasons = "|".join(notification.get("reasons") or [])
    return f'{notification.get("ticker","")}::{types}::{reasons}'


def render_whatsapp_message(notification: Dict[str, Any]) -> str:
    sev = _sev(notification.get("severity"))
    emoji = EMOJI[sev]
    ticker = notification.get("ticker", "UNKNOWN")
    company = notification.get("company", ticker)
    title = notification.get("title", "Perubahan material")
    types = ", ".join(notification.get("alert_types") or [])
    reasons = notification.get("reasons") or []

    lines = [
        f"{emoji} *AI STOCK ALERT — {sev}*",
        f"*{ticker}* • {company}",
        "",
        f"*{title}*",
        f"Jenis: {types}",
    ]
    if notification.get("source_alert_count", 1) > 1:
        lines.append(f"Event digabung: {notification['source_alert_count']} perubahan")
    if notification.get("old_value") != "—" or notification.get("new_value") != "—":
        lines.append(f"Perubahan utama: {notification.get('old_value','—')} → {notification.get('new_value','—')}")
    if reasons:
        lines.append("")
        lines.append("*Alasan:*")
        for reason in reasons:
            lines.append(f"• {reason}")
    lines += [
        "",
        "_AI Stock Intelligence • decision support, bukan jaminan hasil._",
    ]
    return "\n".join(lines)


def _history_path(db_dir: str) -> str:
    return os.path.join(db_dir, "notification_history.json")


def _outbox_path(db_dir: str) -> str:
    return os.path.join(db_dir, "notification_outbox.json")


def _load_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(path: str, payload) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _history_entry(fp: str, item: Dict[str, Any], now: datetime, status: str = "SENT") -> Dict[str, Any]:
    return {
        "queued_at": str(item.get("created_at") or now.isoformat()),
        "delivered_at": now.isoformat() if status == "SENT" else None,
        "status": status,
        "fingerprint": fp,
        "ticker": item.get("ticker", ""),
        "severity": item.get("severity", "MEDIUM"),
        "primary_alert_type": item.get("primary_alert_type", "ALERT"),
    }


def _outbox_notifications(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, dict):
        rows = payload.get("notifications") or []
        return rows if isinstance(rows, list) else []
    return []


def enqueue_alerts(
    alerts: List[Dict[str, Any]],
    db_dir: str,
    *,
    cooldown_minutes: float = 60.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Aggregate, deduplicate and queue alerts.

    V1.8.32 semantics: notification history represents successful delivery, while
    the local outbox represents delivery work still in progress. Failed sends are
    retryable and are never treated as delivered merely because they were queued.
    """
    aggregated = aggregate_alerts(alerts)
    history = _load_json(_history_path(db_dir), [])
    outbox_path = _outbox_path(db_dir)
    existing_outbox = _load_json(outbox_path, {})
    pending = _outbox_notifications(existing_outbox)
    pending_by_fp = {str(n.get("notification_id") or notification_fingerprint(n)): n for n in pending
                     if str(n.get("delivery_status", "")).upper() not in {"SENT", "DEAD_LETTER"}}
    now = datetime.now(timezone.utc)

    queued: List[Dict[str, Any]] = []
    suppressed: List[Dict[str, Any]] = []

    for item in aggregated:
        fp = notification_fingerprint(item)
        recent = None
        for h in reversed(history):
            if h.get("fingerprint") == fp:
                recent = h
                break

        # An unsent outbox item is already work in progress. Do not duplicate it.
        if fp in pending_by_fp:
            item["delivery_status"] = "PENDING"
            item["notification_id"] = fp
            suppressed.append(item)
            continue

        blocked = False
        if recent:
            try:
                dt = datetime.fromisoformat(str(recent.get("delivered_at") or recent.get("queued_at")).replace("Z", "+00:00"))
                blocked = now - dt.astimezone(timezone.utc) < timedelta(minutes=float(cooldown_minutes))
            except Exception:
                blocked = False

        if blocked:
            item["delivery_status"] = "COOLDOWN"
            item["notification_id"] = fp
            suppressed.append(item)
            continue

        item["notification_id"] = fp
        item["whatsapp_message"] = render_whatsapp_message(item)
        item["delivery_status"] = "QUEUED"
        item["attempts"] = int(item.get("attempts") or 0)
        queued.append(item)
        pending_by_fp[fp] = item

    if not dry_run:
        # Preserve existing work, add new work, and keep a bounded local outbox.
        merged = list(pending_by_fp.values())
        merged.sort(key=lambda n: (SEVERITY_ORDER.get(_sev(n.get("severity")), 2), str(n.get("created_at", ""))))
        merged = merged[-500:]
        history = history[-500:]
        payload = {
            "generated_at": now.isoformat(),
            "channel": "WHATSAPP_CLOUD_API",
            "count": len(merged),
            "notifications": merged,
            "suppressed_count": len(suppressed),
            "queue_version": "1.8.32",
        }
        _save_json(outbox_path, payload)
        _save_json(os.path.join(db_dir, "latest_notifications.json"), {
            "generated_at": now.isoformat(),
            "count": len(merged),
            "notifications": merged,
            "suppressed_count": len(suppressed),
            "queue_version": "1.8.32",
        })
        _save_json(_history_path(db_dir), history)

    return {
        "queued": queued,
        "suppressed": suppressed,
        "aggregated_count": len(aggregated),
        "queued_count": len(queued),
        "suppressed_count": len(suppressed),
    }

def run_queue_simulator() -> List[Dict[str, Any]]:
    """Acceptance tests for notification aggregation and cooldown."""
    from smart_alerts import evaluate_transition, simulator_cases

    rows = []
    for case in simulator_cases():
        alerts = evaluate_transition(case["old"], case["cur"])
        batch = enqueue_alerts(alerts, os.getcwd(), dry_run=True)
        positive = bool(case["expected"])
        expected_queue = 1 if positive else 0
        passed = batch["queued_count"] == expected_queue
        rows.append({
            "Scenario": case["name"],
            "Alert Engine": "PASS" if (
                (bool(alerts) == positive) and
                (not positive or case["expected_type"] in [a["alert_type"] for a in alerts])
            ) else "FAIL",
            "Expected Notifications": expected_queue,
            "Actual Notifications": batch["queued_count"],
            "Merged Events": batch["aggregated_count"],
            "Result": "PASS" if passed else "FAIL",
        })

    # Explicit multi-event aggregation test: 4 alert events for one ticker -> 1 notification.
    synthetic = [
        {"ticker":"QUEUE1","company":"Queue Test","severity":"HIGH","alert_type":"TOP_5_ENTRY",
         "title":"Masuk Dynamic Top-5","detail":"Rank #8 → #3","old_value":"#8","new_value":"#3"},
        {"ticker":"QUEUE1","company":"Queue Test","severity":"HIGH","alert_type":"ENTRY_WINDOW",
         "title":"Entry Window berubah","detail":"WAIT → ACTIVE","old_value":"WAIT","new_value":"ACTIVE"},
        {"ticker":"QUEUE1","company":"Queue Test","severity":"MEDIUM","alert_type":"RISK",
         "title":"Risk berubah","detail":"HIGH → MEDIUM","old_value":"HIGH","new_value":"MEDIUM"},
        {"ticker":"QUEUE1","company":"Queue Test","severity":"HIGH","alert_type":"AI_SCORE",
         "title":"AI Score berubah signifikan","detail":"64 → 79 (+15)","old_value":"64","new_value":"79"},
    ]
    batch = enqueue_alerts(synthetic, os.getcwd(), dry_run=True)
    rows.append({
        "Scenario": "MULTI_EVENT_AGGREGATION",
        "Alert Engine": "PASS",
        "Expected Notifications": 1,
        "Actual Notifications": batch["queued_count"],
        "Merged Events": batch["aggregated_count"],
        "Result": "PASS" if batch["queued_count"] == 1 and batch["aggregated_count"] == 1 else "FAIL",
    })
    return rows

def _whatsapp_config(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = config or {}
    return {
        "enabled": (bool(cfg.get("whatsapp_enabled", False)) or str(os.getenv("WHATSAPP_ENABLED", "false")).lower() in {"1", "true", "yes", "on"}),
        "send_enabled": str(os.getenv("WHATSAPP_SEND_ENABLED", "false")).lower() in {"1", "true", "yes", "on"},
        "access_token": os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
        "phone_number_id": os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        "recipient": os.getenv("WHATSAPP_TO", ""),
        "graph_version": os.getenv("WHATSAPP_GRAPH_VERSION", str(cfg.get("whatsapp_graph_version", "v24.0"))),
        "timeout": float(os.getenv("WHATSAPP_TIMEOUT_SECONDS", str(cfg.get("whatsapp_timeout_seconds", 15)))),
    }

def whatsapp_status(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Report readiness without contacting WhatsApp."""
    w = _whatsapp_config(config)
    credentials = bool(w["access_token"] and w["phone_number_id"] and w["recipient"])
    send_ready = bool(w["enabled"] and w["send_enabled"] and credentials)
    if send_ready:
        mode = "WHATSAPP_SEND_READY"
        note = "WhatsApp Cloud API adapter aktif; pengiriman diizinkan oleh config + environment flag."
    elif credentials and w["enabled"]:
        mode = "CREDENTIALS_READY"
        note = "Credentials tersedia, tetapi WHATSAPP_SEND_ENABLED belum true."
    elif w["enabled"]:
        mode = "CONFIG_ENABLED_NO_CREDENTIALS"
        note = "whatsapp_enabled=true, tetapi credentials WhatsApp belum lengkap."
    else:
        mode = "QUEUE_ONLY"
        note = "Tidak ada WhatsApp API call. Adapter tetap nonaktif sampai di-enable secara eksplisit."
    return {
        "enabled": w["enabled"],
        "send_enabled": w["send_enabled"],
        "credentials_present": credentials,
        "mode": mode,
        "note": note,
    }

def _whatsapp_endpoint(w: Dict[str, Any]) -> str:
    return f"https://graph.facebook.com/{w['graph_version']}/{w['phone_number_id']}/messages"

def send_whatsapp_text(message: str, config: Dict[str, Any] | None = None, *, dry_run: bool = False) -> Dict[str, Any]:
    """Send one text message through Meta WhatsApp Cloud API.

    Safety: dry_run never calls the network. Real sending requires config enablement,
    WHATSAPP_SEND_ENABLED=true, access token, phone number ID and recipient.
    """
    w = _whatsapp_config(config)
    if dry_run:
        return {"ok": True, "mode": "DRY_RUN", "endpoint": _whatsapp_endpoint(w) if w["phone_number_id"] else "", "recipient_present": bool(w["recipient"])}
    if not w["enabled"]:
        return {"ok": False, "mode": "BLOCKED", "error": "whatsapp_enabled=false"}
    if not w["send_enabled"]:
        return {"ok": False, "mode": "BLOCKED", "error": "WHATSAPP_SEND_ENABLED is not true"}
    missing = [k for k in ("access_token", "phone_number_id", "recipient") if not w[k]]
    if missing:
        return {"ok": False, "mode": "BLOCKED", "error": "Missing: " + ", ".join(missing)}
    try:
        import requests
        payload = {
            "messaging_product": "whatsapp",
            "to": w["recipient"],
            "type": "text",
            "text": {"preview_url": False, "body": str(message)},
        }
        headers = {"Authorization": f"Bearer {w['access_token']}", "Content-Type": "application/json"}
        resp = requests.post(_whatsapp_endpoint(w), headers=headers, json=payload, timeout=w["timeout"])
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:1000]}
        return {"ok": 200 <= resp.status_code < 300, "status_code": resp.status_code, "response": body}
    except Exception as exc:
        return {"ok": False, "mode": "ERROR", "error": str(exc)[:500]}

def deliver_notifications(notifications: List[Dict[str, Any]], config: Dict[str, Any] | None = None, *, dry_run: bool = False) -> Dict[str, Any]:
    results = []
    for n in notifications or []:
        r = send_whatsapp_text(str(n.get("whatsapp_message", "")), config, dry_run=dry_run)
        row = {"notification_id": n.get("notification_id", ""), "ticker": n.get("ticker", ""), **r}
        results.append(row)
    sent = sum(1 for r in results if r.get("ok"))
    return {"attempted": len(results), "sent": sent, "failed": len(results)-sent, "results": results}

def run_whatsapp_adapter_test(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Offline adapter acceptance test. Never sends to WhatsApp."""
    status = whatsapp_status(config)
    sample = {
        "ticker": "TEST", "company": "Synthetic Test Stock", "severity": "HIGH",
        "title": "WhatsApp adapter test", "alert_types": ["TOP_5_ENTRY"],
        "source_alert_count": 1, "reasons": ["Synthetic test only"],
        "old_value": "WAIT", "new_value": "ACTIVE",
    }
    msg = render_whatsapp_message(sample)
    dry = send_whatsapp_text(msg, config, dry_run=True)
    return {"status": status, "dry_run": dry, "message_preview": msg}




def _save_outbox_payload(db_dir: str, notifications: List[Dict[str, Any]], suppressed_count: int = 0) -> None:
    now = _now()
    payload = {
        "generated_at": now,
        "channel": "WHATSAPP_CLOUD_API",
        "count": len(notifications),
        "notifications": notifications[-500:],
        "suppressed_count": int(suppressed_count),
        "queue_version": "1.8.32",
    }
    _save_json(_outbox_path(db_dir), payload)
    _save_json(os.path.join(db_dir, "latest_notifications.json"), payload)


def delivery_status(db_dir: str) -> Dict[str, Any]:
    """Return local outbox delivery counts without contacting WhatsApp."""
    payload = _load_json(_outbox_path(db_dir), {})
    rows = _outbox_notifications(payload)
    counts = {k: 0 for k in ("QUEUED", "SENDING", "SENT", "FAILED", "DEAD_LETTER", "COOLDOWN", "PENDING")}
    for row in rows:
        st = str(row.get("delivery_status") or "QUEUED").upper()
        counts[st] = counts.get(st, 0) + 1
    return {
        "total": len(rows),
        "counts": counts,
        "last_generated_at": payload.get("generated_at"),
        "queue_version": payload.get("queue_version", "1.8.30"),
    }


def deliver_pending_notifications(
    db_dir: str,
    config: Dict[str, Any] | None = None,
    *,
    max_attempts: int = 3,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Deliver queued/failed notifications and persist delivery state.

    When WhatsApp is not explicitly enabled, this function safely skips without
    converting queued work into failures. This keeps the queue ready while the
    real adapter remains disabled.
    """
    status = whatsapp_status(config)
    payload = _load_json(_outbox_path(db_dir), {})
    rows = _outbox_notifications(payload)
    if not rows:
        return {"attempted": 0, "sent": 0, "failed": 0, "skipped": 0, "mode": "EMPTY"}

    if not dry_run and status.get("mode") != "WHATSAPP_SEND_READY":
        return {
            "attempted": 0, "sent": 0, "failed": 0,
            "skipped": len([r for r in rows if str(r.get("delivery_status", "")).upper() in {"QUEUED", "FAILED"}]),
            "mode": "NOT_READY",
            "reason": status.get("mode"),
        }

    attempted = sent = failed = skipped = 0
    history = _load_json(_history_path(db_dir), [])
    now = datetime.now(timezone.utc)

    for n in rows:
        state = str(n.get("delivery_status") or "QUEUED").upper()
        attempts = int(n.get("attempts") or 0)
        if state not in {"QUEUED", "FAILED"}:
            continue
        if attempts >= int(max_attempts) and not dry_run:
            n["delivery_status"] = "DEAD_LETTER"
            continue
        n["delivery_status"] = "SENDING"
        n["last_attempt_at"] = now.isoformat()
        if not dry_run:
            _save_outbox_payload(db_dir, rows, payload.get("suppressed_count", 0))
        attempted += 1
        result = send_whatsapp_text(str(n.get("whatsapp_message", "")), config, dry_run=dry_run)
        n["attempts"] = attempts + 1
        if result.get("ok"):
            sent += 1
            n["delivery_status"] = "SENT"
            n["sent_at"] = datetime.now(timezone.utc).isoformat()
            n.pop("last_error", None)
            if not dry_run:
                fp = str(n.get("notification_id") or notification_fingerprint(n))
                history.append(_history_entry(fp, n, datetime.now(timezone.utc), "SENT"))
        else:
            failed += 1
            n["delivery_status"] = "FAILED"
            n["last_error"] = str(result.get("error") or result.get("response") or "Unknown WhatsApp API error")[:500]
        if dry_run:
            # Dry-run is intentionally non-mutating.
            n["delivery_status"] = state

    if not dry_run:
        history = history[-500:]
        _save_json(_history_path(db_dir), history)
        _save_outbox_payload(db_dir, rows, payload.get("suppressed_count", 0))

    return {
        "attempted": attempted,
        "sent": sent,
        "failed": failed,
        "skipped": skipped,
        "mode": "DRY_RUN" if dry_run else "LIVE",
        "queue_remaining": sum(1 for r in rows if str(r.get("delivery_status", "")).upper() in {"QUEUED", "FAILED", "SENDING"}),
    }
