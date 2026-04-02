from __future__ import annotations

import datetime as dt
import json
import os
from zoneinfo import ZoneInfo

try:
    from scripts.build_rss import build_rss
except ModuleNotFoundError:
    from build_rss import build_rss


def cleanup(index_path: str, repo_link: str, feed_path: str) -> int:
    now = dt.datetime.now(ZoneInfo("UTC"))

    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    reports = index.get("reports", [])
    kept = []
    removed_count = 0

    for report in reports:
        expire_at = report.get("expire_at")
        if not expire_at:
            kept.append(report)
            continue

        expire_dt = dt.datetime.fromisoformat(expire_at.replace("Z", "+00:00"))
        if expire_dt > now:
            kept.append(report)
            continue

        for key in ["report_path", "digest_path"]:
            rel_path = report.get(key, "")
            if rel_path and os.path.exists(rel_path):
                os.remove(rel_path)
        removed_count += 1

    index["reports"] = kept
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    build_rss(index_path=index_path,
              output_path=feed_path,
              repo_link=repo_link)
    return removed_count


if __name__ == "__main__":
    repository = os.getenv("GITHUB_REPOSITORY", "")
    repo_link = f"https://github.com/{repository}" if repository else "https://github.com"
    deleted = cleanup(
        index_path="reports/index.json",
        repo_link=repo_link,
        feed_path="feed/rss.xml",
    )
    print(f"cleanup_deleted={deleted}")
