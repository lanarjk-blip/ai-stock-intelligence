"""Focused regression tests for Batch 1 Live Engine.

These tests use temporary SQLite files and do not require Firebase credentials.
Run with: python -m unittest test_batch1_live_engine.py
"""
import os
import tempfile
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pandas as pd

import market_session
import top5_timeline

WIB = ZoneInfo("Asia/Jakarta")


def dynamic_frame(state="WATCH", entry_status="NEUTRAL"):
    return pd.DataFrame([
        {
            "rank": 1,
            "ticker": "AAAA",
            "state": state,
            "entry_status": entry_status,
            "p_up": 0.70,
            "risk_reward": 2.5,
            "risk": "MEDIUM",
        },
        {"rank": 2, "ticker": "BBBB", "state": "WATCH", "entry_status": "NEUTRAL"},
        {"rank": 3, "ticker": "CCCC", "state": "WATCH", "entry_status": "NEUTRAL"},
        {"rank": 4, "ticker": "DDDD", "state": "WATCH", "entry_status": "NEUTRAL"},
        {"rank": 5, "ticker": "EEEE", "state": "WATCH", "entry_status": "NEUTRAL"},
    ])


class MarketSessionTests(unittest.TestCase):
    def test_weekend_is_blocked(self):
        result = market_session.session_status(now=datetime(2026, 10, 11, 10, 0, tzinfo=WIB))
        self.assertFalse(result["live"])
        self.assertEqual(result["status"], "WEEKEND")

    def test_monday_session_one_is_live(self):
        result = market_session.session_status(now=datetime(2026, 10, 12, 10, 0, tzinfo=WIB))
        self.assertTrue(result["live"])
        self.assertEqual(result["status"], "SESSION_1")

    def test_after_close_is_blocked(self):
        result = market_session.session_status(now=datetime(2026, 10, 12, 17, 7, tzinfo=WIB))
        self.assertFalse(result["live"])
        self.assertEqual(result["status"], "CLOSED")


class TimelineTransitionTests(unittest.TestCase):
    def setUp(self):
        handle, self.db = tempfile.mkstemp(suffix=".db")
        os.close(handle)

    def tearDown(self):
        try:
            os.remove(self.db)
        except FileNotFoundError:
            pass

    def test_non_live_sync_creates_no_actionable_event(self):
        closed = {"live": False, "status": "CLOSED"}
        with patch.object(top5_timeline, "session_status", return_value=closed):
            top5_timeline.record_top5(self.db, dynamic_frame(), config={})
            result = top5_timeline.record_top5(
                self.db,
                dynamic_frame(state="ENTRY READY", entry_status="ACTIVE"),
                config={},
            )
        self.assertEqual(result["status"], "SYNC_NON_LIVE")
        self.assertEqual(result["events"], 0)
        self.assertEqual(top5_timeline.recent_events(self.db), [])

    def test_entry_ready_only_once_until_state_resets(self):
        live = {"live": True, "status": "SESSION_1"}
        with patch.object(top5_timeline, "session_status", return_value=live):
            top5_timeline.record_top5(self.db, dynamic_frame(), config={})
            first = top5_timeline.record_top5(
                self.db,
                dynamic_frame(state="ENTRY READY", entry_status="ACTIVE"),
                config={},
            )
            repeated = top5_timeline.record_top5(
                self.db,
                dynamic_frame(state="ENTRY READY", entry_status="ACTIVE"),
                config={},
            )
            top5_timeline.record_top5(self.db, dynamic_frame(), config={})
            reentered = top5_timeline.record_top5(
                self.db,
                dynamic_frame(state="ENTRY READY", entry_status="ACTIVE"),
                config={},
            )

        self.assertEqual(first["entry_ready_events"], 1)
        self.assertEqual(repeated["entry_ready_events"], 0)
        self.assertEqual(reentered["entry_ready_events"], 1)
        ready_events = [e for e in top5_timeline.recent_events(self.db) if e["event_type"] == "ENTRY_READY"]
        self.assertEqual(len(ready_events), 2)


if __name__ == "__main__":
    unittest.main()
