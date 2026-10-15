from datetime import datetime, timedelta, timezone

from app import db
from app.config import settings
from app.services import devices

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=IST).timestamp()


def _ago(days, hour=10):
    return datetime(2026, 10, 15, hour, 0, tzinfo=IST).timestamp() - days * 86400


def test_install_counts_and_daily_series(monkeypatch):
    monkeypatch.setattr(settings, "active_devices_since", _ago(40))
    rows = [
        {"created_at": _ago(0), "last_seen_at": NOW, "platform": "android"},
        {"created_at": _ago(0, hour=1), "last_seen_at": NOW, "platform": "android"},
        {"created_at": _ago(3), "last_seen_at": _ago(1), "platform": "ios"},
        {"created_at": _ago(20), "last_seen_at": _ago(15), "platform": "android"},
        {"created_at": _ago(35), "last_seen_at": _ago(35), "platform": "android"},   # before the 30-day window
    ]
    monkeypatch.setattr(db, "query", lambda sql, params=(): rows)
    s = devices.install_stats(30, now=NOW)
    assert s["total"] == 5 and s["today"] == 2
    assert s["last_7_days"] == 3 and s["last_30_days"] == 4
    assert s["active_7_days"] == 3
    assert s["platforms"] == {"android": 4, "ios": 1}
    assert len(s["daily"]) == 30 and s["daily"][-1] == {"day": "2026-10-15", "installs": 2}
    assert sum(d["installs"] for d in s["daily"]) == 4
