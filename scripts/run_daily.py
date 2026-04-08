from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

CONFIG_PATH = "config/config.json"
DEFAULT_RETENTION_DAYS = 15
DEFAULT_DEDUP_LOOKBACK_DAYS = 15
VERSION_SUFFIX_RE = re.compile(r"v\d+$")

try:
    from scripts.fetch_arxiv import fetch_papers
    from scripts.render_report import render_digest, render_full_report
    from scripts.send_email_resend import build_digest_html, send_digest_email
    from scripts.summarize_openai import summarize_papers
except ModuleNotFoundError:
    from fetch_arxiv import fetch_papers
    from render_report import render_digest, render_full_report
    from send_email_resend import build_digest_html, send_digest_email
    from summarize_openai import summarize_papers


def _load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_text(path: str, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _load_index(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {"reports": []}
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_index(path: str, index: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def _normalize_paper_key(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        return ""
    if "/abs/" in text:
        text = text.split("/abs/", 1)[1]
    elif "/pdf/" in text:
        text = text.split("/pdf/", 1)[1]
    if text.endswith(".pdf"):
        text = text[:-4]
    text = VERSION_SUFFIX_RE.sub("", text)
    return text


def _paper_key(paper: dict) -> str:
    for field in ("id", "link", "pdf_link"):
        key = _normalize_paper_key(str(paper.get(field, "")))
        if key:
            return key
    return ""


def _collect_recent_paper_keys(index: dict, report_date: str,
                               lookback_days: int) -> set[str]:
    today = dt.date.fromisoformat(report_date)
    cutoff = today - dt.timedelta(days=lookback_days)
    keys: set[str] = set()

    for item in index.get("reports", []):
        date_text = str(item.get("date", "")).strip()
        try:
            item_date = dt.date.fromisoformat(date_text)
        except ValueError:
            continue
        if item_date >= today or item_date < cutoff:
            continue

        links = item.get("paper_links", [])
        if not isinstance(links, list):
            continue
        for link in links:
            key = _normalize_paper_key(str(link))
            if key:
                keys.add(key)

    return keys


def main() -> int:
    shanghai_now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    now_utc = dt.datetime.now(dt.timezone.utc)
    report_date = shanghai_now.strftime("%Y-%m-%d")

    cfg = _load_config(CONFIG_PATH)
    arxiv_cfg = cfg.get("arxiv", {})
    report_cfg = cfg.get("report", {})
    dedup_lookback_days = int(
        report_cfg.get("dedup_lookback_days", DEFAULT_DEDUP_LOOKBACK_DAYS))
    retention_days = int(
        report_cfg.get("retention_days", DEFAULT_RETENTION_DAYS))
    index_path = "reports/index.json"
    index = _load_index(index_path)
    reports = index.get("reports", [])

    base_lookback_hours = int(arxiv_cfg.get("lookback_hours", 36))
    # On Monday (weekday 0), extend lookback to cover the weekend gap:
    # Friday papers have published≈Fri 18:00 UTC, but Monday runs at ~04:00 UTC
    # so the gap is ~58h; use 96h to be safe.
    weekday = shanghai_now.weekday()  # 0=Monday
    lookback_hours = max(base_lookback_hours,
                         96) if weekday == 0 else base_lookback_hours

    papers = fetch_papers(
        categories=arxiv_cfg.get("categories", []),
        max_results=int(arxiv_cfg.get("max_results", 80)),
        lookback_hours=lookback_hours,
    )
    fetched_count = len(papers)

    history_keys = _collect_recent_paper_keys(
        index=index,
        report_date=report_date,
        lookback_days=dedup_lookback_days)
    deduped_papers = []
    removed_duplicates = 0
    for paper in papers:
        key = _paper_key(paper)
        if key and key in history_keys:
            removed_duplicates += 1
            continue
        deduped_papers.append(paper)
        if key:
            history_keys.add(key)
    papers = deduped_papers

    openai_key = os.getenv("OPENAI_API_KEY", "")
    openai_cfg = cfg.get("openai", {})
    openai_model = openai_cfg.get("model", "gpt-4o-mini")
    openai_api_base = openai_cfg.get("api_base", "")
    summary_payload = summarize_papers(papers=papers,
                                       model=openai_model,
                                       api_key=openai_key,
                                       api_base=openai_api_base)

    summary_items = {
        x.get("id", ""): x
        for x in summary_payload.get("items", [])
    }
    global_summary = summary_payload.get("global_summary", "今日无更新。")
    related_ids = summary_payload.get("related_ids", [])
    groups = summary_payload.get("groups", [])

    if groups:
        group_parts = []
        for g in groups:
            idx_str = ", ".join(str(i) for i in sorted(g["indices"]))
            group_parts.append(f"{g['label']}[{idx_str}]")
        topics_line = "重点方向：" + "、".join(group_parts)
        global_summary = f"{global_summary}\n\n{topics_line}"

    repository = os.getenv("GITHUB_REPOSITORY", "")
    base_blob = f"https://github.com/{repository}/blob/main" if repository else "https://github.com"
    report_rel = f"reports/{report_date}.md"
    digest_rel = f"reports/{report_date}.digest.md"
    report_url = f"{base_blob}/{report_rel}"

    full_text = render_full_report(
        report_date=report_date,
        global_summary=global_summary,
        papers=papers,
        summaries=summary_items,
        groups=groups,
    )
    digest_text = render_digest(
        report_date=report_date,
        global_summary=global_summary,
        papers=papers,
        summaries=summary_items,
        report_url=report_url,
        groups=groups,
    )

    _save_text(report_rel, full_text)
    _save_text(digest_rel, digest_text)

    expire_at = now_utc + dt.timedelta(days=retention_days)

    guid = f"astroreport-{report_date}"
    report_entry = {
        "date": report_date,
        "title": f"astroReport 日报 {report_date}",
        "guid": guid,
        "created_at": now_utc.isoformat(),
        "expire_at": expire_at.isoformat(),
        "report_path": report_rel,
        "digest_path": digest_rel,
        "report_url": report_url,
        "digest_summary": global_summary,
        "paper_links": [p.get("link", "") for p in papers if p.get("link")],
    }

    replaced = False
    for i, item in enumerate(reports):
        if item.get("date") == report_date:
            reports[i] = report_entry
            replaced = True
            break
    if not replaced:
        reports.append(report_entry)

    index["reports"] = sorted(reports,
                              key=lambda x: x.get("created_at", ""),
                              reverse=True)
    _save_index(index_path, index)

    resend_api_key = os.getenv("RESEND_API_KEY", "")
    recipients = cfg.get("mail_list", [])
    from_email = cfg.get("email", {}).get("from_email",
                                          "onboarding@resend.dev")

    html = build_digest_html(
        report_date=report_date,
        report_url=report_url,
        digest_text=digest_text,
    )

    email_ok = send_digest_email(
        api_key=resend_api_key,
        from_email=from_email,
        to_emails=recipients,
        subject=f"[astroReport] {report_date} 精简日报",
        html_body=html,
    )

    status = {
        "report_date": report_date,
        "fetched_papers": fetched_count,
        "removed_duplicates": removed_duplicates,
        "dedup_lookback_days": dedup_lookback_days,
        "papers": len(papers),
        "email_sent": email_ok,
        "generated_at": now_utc.isoformat(),
    }
    _save_text("data/last_run.json",
               json.dumps(status, ensure_ascii=False, indent=2))

    print(f"report_date={report_date}")
    print(f"fetched_papers={fetched_count}")
    print(f"removed_duplicates={removed_duplicates}")
    print(f"papers={len(papers)}")
    print(f"email_sent={email_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
