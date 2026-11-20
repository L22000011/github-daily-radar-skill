"""Historical trending snapshot persistence and deduplication utilities."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from config import HISTORY_DIR

SNAPSHOT_FILE = HISTORY_DIR / "trending_snapshots.jsonl"


def _read_snapshots(snapshot_file: Path = SNAPSHOT_FILE) -> list[dict[str, Any]]:
    if not snapshot_file.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in snapshot_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def append_snapshot(
    period: str,
    raw_top10: list[dict[str, Any]],
    selected_repos: list[str],
    snapshot_file: Path = SNAPSHOT_FILE,
) -> Path:
    """Append one run's verified Top 10 stats + selected repos into JSONL history."""
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    record = {
        "timestamp_utc": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "period": period,
        "selected_repos": selected_repos,
        "repos": [
            {
                "trending_rank": r.get("trending_rank"),
                "full_name": r.get("full_name"),
                "stargazers_count": r.get("stargazers_count"),
                "stars_delta": r.get("stars_delta"),
                "language": r.get("language"),
            }
            for r in raw_top10
        ],
    }
    with snapshot_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return snapshot_file


def load_recent_selected(days: int = 3, snapshot_file: Path = SNAPSHOT_FILE) -> set[str]:
    """Return full_names featured within the last `days` days (excluding today's exact run)."""
    if days <= 0:
        return set()
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    recent: set[str] = set()
    for rec in _read_snapshots(snapshot_file):
        try:
            ts = dt.datetime.fromisoformat(str(rec.get("timestamp_utc", "")))
            if ts >= cutoff:
                for fn in rec.get("selected_repos") or []:
                    recent.add(str(fn))
        except Exception:
            continue
    return recent


def compute_historical_delta(
    full_name: str,
    current_stars: int,
    snapshot_file: Path = SNAPSHOT_FILE,
) -> dict[str, Any] | None:
    """Compute real delta vs the earliest prior snapshot (>= 6h older) if one exists."""
    now = dt.datetime.now(dt.timezone.utc)
    prior_matches: list[tuple[dt.datetime, int]] = []
    for rec in _read_snapshots(snapshot_file):
        try:
            ts = dt.datetime.fromisoformat(str(rec.get("timestamp_utc", "")))
        except Exception:
            continue
        if (now - ts).total_seconds() < 6 * 3600:
            continue
        for r in rec.get("repos") or []:
            if r.get("full_name") == full_name and r.get("stargazers_count") is not None:
                prior_matches.append((ts, int(r["stargazers_count"])))
    if not prior_matches:
        return None
    prior_matches.sort(key=lambda x: x[0], reverse=True)
    prev_ts, prev_stars = prior_matches[0]
    return {
        "previous_timestamp_utc": prev_ts.isoformat(),
        "previous_stars": prev_stars,
        "snapshot_delta": current_stars - prev_stars,
    }
