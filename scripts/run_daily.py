from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

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


def main() -> int:
    shanghai_now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    now_utc = dt.datetime.now(dt.timezone.utc)
    report_date = shanghai_now.strftime("%Y-%m-%d")

    cfg = _load_config("config/arxiv.json")

    papers = fetch_papers(
        categories=cfg.get("categories", []),
        max_results=int(cfg.get("max_results", 80)),
        lookback_hours=int(cfg.get("lookback_hours", 36)),
    )
    papers = papers[:int(cfg.get("max_papers_in_report", 25))]

    openai_key = os.getenv("OPENAI_API_KEY", "")
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_api_base = os.getenv("OPENAI_API_BASE", "")
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

    id_to_index = {
        paper.get("id", ""): idx
        for idx, paper in enumerate(papers, start=1) if paper.get("id", "")
    }
    related_indices = []
    if isinstance(related_ids, list):
        related_indices = [
            id_to_index[rid] for rid in related_ids if rid in id_to_index
        ]
    if related_indices:
        prefix = "[相关文献编号: " + ", ".join(str(i) for i in related_indices) + "]"
        global_summary = f"{prefix} {global_summary}".strip()

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
    )
    digest_text = render_digest(
        report_date=report_date,
        global_summary=global_summary,
        papers=papers,
        summaries=summary_items,
        report_url=report_url,
    )

    _save_text(report_rel, full_text)
    _save_text(digest_rel, digest_text)

    expire_at = now_utc + dt.timedelta(days=10)

    index_path = "reports/index.json"
    index = _load_index(index_path)
    reports = index.get("reports", [])

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
    recipient = os.getenv("REPORT_RECIPIENT_EMAIL", "")
    from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")

    html = build_digest_html(
        report_date=report_date,
        report_url=report_url,
        digest_text=digest_text,
    )

    email_ok = send_digest_email(
        api_key=resend_api_key,
        from_email=from_email,
        to_email=recipient,
        subject=f"[astroReport] {report_date} 精简日报",
        html_body=html,
    )

    status = {
        "report_date": report_date,
        "papers": len(papers),
        "email_sent": email_ok,
        "generated_at": now_utc.isoformat(),
    }
    _save_text("data/last_run.json",
               json.dumps(status, ensure_ascii=False, indent=2))

    print(f"report_date={report_date}")
    print(f"papers={len(papers)}")
    print(f"email_sent={email_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
