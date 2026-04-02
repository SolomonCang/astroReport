from __future__ import annotations

import datetime as dt
import json
import os

UTC = dt.timezone.utc
_DEFAULT_TTL_DAYS = 15
_CONFIG_PATH = "config/config.json"


def _load_ttl_days() -> int:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return int(
            cfg.get("report", {}).get("retention_days", _DEFAULT_TTL_DAYS))
    except Exception:
        return _DEFAULT_TTL_DAYS


def _parse_datetime(value: str) -> dt.datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_expire_time(report: dict, ttl_days: int) -> dt.datetime | None:
    expire_at = report.get("expire_at", "")
    if isinstance(expire_at, str):
        expire_dt = _parse_datetime(expire_at)
        if expire_dt is not None:
            return expire_dt

    created_at = report.get("created_at", "")
    if isinstance(created_at, str):
        created_dt = _parse_datetime(created_at)
        if created_dt is not None:
            return created_dt + dt.timedelta(days=ttl_days)

    report_date = report.get("date", "")
    if isinstance(report_date, str):
        try:
            day = dt.date.fromisoformat(report_date.strip())
            created_dt = dt.datetime.combine(day, dt.time(0, tzinfo=UTC))
            return created_dt + dt.timedelta(days=ttl_days)
        except ValueError:
            return None
    return None


def _is_safe_report_path(path: str) -> bool:
    normalized = os.path.normpath(path).replace("\\", "/")
    parts = normalized.split("/")
    return normalized.startswith("reports/") and ".." not in parts


def cleanup(index_path: str) -> int:
    ttl_days = _load_ttl_days()
    now = dt.datetime.now(UTC)

    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    reports = index.get("reports", [])
    kept = []
    removed_count = 0

    for report in reports:
        expire_dt = _resolve_expire_time(report, ttl_days)
        if expire_dt is None:
            kept.append(report)
            continue

        if expire_dt > now:
            kept.append(report)
            continue

        for key in ["report_path", "digest_path"]:
            rel_path = report.get(key, "")
            if isinstance(rel_path,
                          str) and rel_path and _is_safe_report_path(rel_path):
                if os.path.exists(rel_path):
                    os.remove(rel_path)
        removed_count += 1

    index["reports"] = kept
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return removed_count


if __name__ == "__main__":
    deleted = cleanup(index_path="reports/index.json")
    print(f"cleanup_deleted={deleted}")
