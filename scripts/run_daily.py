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
    from scripts.summarize_openai import _fallback_summary, summarize_papers
except ModuleNotFoundError:
    from fetch_arxiv import fetch_papers
    from render_report import render_digest, render_full_report
    from send_email_resend import build_digest_html, send_digest_email
    from summarize_openai import _fallback_summary, summarize_papers


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
    print(f"[1/7] 初始化  报告日期={report_date}")

    cfg = _load_config(CONFIG_PATH)
    print(f"      配置已加载: {CONFIG_PATH}")
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

    categories = arxiv_cfg.get("categories", [])
    print(
        f"[2/7] 拉取 arXiv  categories={categories}  lookback_hours={lookback_hours}"
    )
    papers = fetch_papers(
        categories=categories,
        max_results=int(arxiv_cfg.get("max_results", 80)),
        lookback_hours=lookback_hours,
    )
    fetched_count = len(papers)
    print(f"      获取到 {fetched_count} 篇论文")

    print(f"[3/7] 去重  lookback_days={dedup_lookback_days}")
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
    print(f"      去除重复 {removed_duplicates} 篇，剩余 {len(papers)} 篇")

    openai_cfg = cfg.get("openai", {})
    openai_key = os.getenv("OPENAI_API_KEY", "")
    resend_api_key = os.getenv("RESEND_API_KEY", "")
    if openai_key:
        print(f"      接收到 LLM 的 API Key ({openai_key[:8]}...)")
    else:
        print("      未检测到 LLM API Key")
    if resend_api_key:
        print(f"      接收到 Resend 的 API Key ({resend_api_key[:8]}...)")
    else:
        print("      未检测到 Resend API Key")
    openai_model = os.getenv("OPENAI_MODEL") or openai_cfg.get("model", "")
    openai_api_base = os.getenv("OPENAI_API_BASE") or openai_cfg.get(
        "api_base", "")
    repository = os.getenv("GITHUB_REPOSITORY", "")
    base_blob = f"https://github.com/{repository}/blob/main" if repository else "https://github.com"
    report_rel = f"reports/{report_date}.md"
    digest_rel = f"reports/{report_date}.digest.md"
    report_url = f"{base_blob}/{report_rel}"

    # ── 步骤 4a：先用降级内容生成占位报告写入磁盘 ────────────────
    fallback_payload = _fallback_summary(papers)
    fallback_items = {x["id"]: x for x in fallback_payload["items"]}
    print("[4/7] 生成占位报告（降级内容）并写入磁盘")
    _save_text(
        report_rel,
        render_full_report(
            report_date=report_date,
            global_summary="（摘要生成中，请稍候…）",
            papers=papers,
            summaries=fallback_items,
            groups=[],
        ),
    )
    print(f"      已保存占位  {report_rel}")

    # ── 步骤 4b：调用 LLM 总结，每批完成后刷新报告 ───────────────
    def _on_batch(items_so_far: list) -> None:
        partial_items = {x["id"]: x for x in items_so_far}
        _save_text(
            report_rel,
            render_full_report(
                report_date=report_date,
                global_summary="（全局总结生成中…）",
                papers=papers,
                summaries=partial_items,
                groups=[],
            ),
        )
        print(f"      报告已更新至第 {len(items_so_far)} 篇")

    if not openai_key:
        print("      跳过 LLM 摘要  (OPENAI_API_KEY 未设置)")
        summary_payload = {
            "global_summary": "API Key 缺失或错误，未能加载 LLM。",
            "related_ids": [],
            "groups": [],
            "items": fallback_payload["items"],
        }
    else:
        print(
            f"      调用 LLM 摘要  model={openai_model or '(config)'}  papers={len(papers)}"
        )
        summary_payload = summarize_papers(
            papers=papers,
            model=openai_model,
            api_key=openai_key,
            api_base=openai_api_base,
            on_batch=_on_batch,
        )
        print(
            f"      摘要完成  groups={len(summary_payload.get('groups', []))}  items={len(summary_payload.get('items', []))}"
        )

    summary_items = {
        x.get("id", ""): x
        for x in summary_payload.get("items", [])
    }
    global_summary = summary_payload.get("global_summary", "今日无更新。")
    groups = summary_payload.get("groups", [])

    if groups:
        group_parts = []
        for g in groups:
            idx_str = ", ".join(str(i) for i in sorted(g["indices"]))
            group_parts.append(f"{g['label']}[{idx_str}]")
        topics_line = "重点方向：" + "、".join(group_parts)
        global_summary = f"{global_summary}\n\n{topics_line}"

    # ── 按 groups 对论文重排序，同组文章紧邻 ──────────────────────
    if groups:
        ordered_indices: list[int] = []
        for g in groups:
            for i in sorted(g["indices"]):
                if 1 <= i <= len(papers) and i not in ordered_indices:
                    ordered_indices.append(i)
        # 将未被任意 group 覆盖的文章追加到末尾
        all_indices = set(range(1, len(papers) + 1))
        for i in sorted(all_indices - set(ordered_indices)):
            ordered_indices.append(i)

        papers = [papers[i - 1] for i in ordered_indices]

        # 重建 groups 的 indices，使其对应新顺序中的位置
        old_to_new = {
            old: new
            for new, old in enumerate(ordered_indices, start=1)
        }
        groups = [{
            "label":
            g["label"],
            "indices":
            sorted(old_to_new[i] for i in g["indices"] if i in old_to_new)
        } for g in groups]
        # 更新拼接到 global_summary 的 topics_line
        group_parts = []
        for g in groups:
            idx_str = ", ".join(str(i) for i in g["indices"])
            group_parts.append(f"{g['label']}[{idx_str}]")
        topics_line = "重点方向：" + "、".join(group_parts)
        # 替换原有 topics_line（最后一行）
        global_summary = global_summary.rsplit("\n\n",
                                               1)[0] + f"\n\n{topics_line}"

    # ── 步骤 5：最终完整渲染（含全局总结和分组）─────────────────
    print("[5/7] 最终渲染报告")
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
    print(f"      已保存  {report_rel}")
    print(f"      已保存  {digest_rel}")

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

    recipients = cfg.get("mail_list", [])
    from_email = cfg.get("email", {}).get("from_email",
                                          "onboarding@resend.dev")
    print(f"[6/7] 发送邮件  收件人={recipients}")
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
    print(f"      邮件发送{'成功' if email_ok else '失败'}")

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
    print("[7/7] 完成")
    print(f"      report_date={report_date}")
    print(f"      fetched_papers={fetched_count}")
    print(f"      removed_duplicates={removed_duplicates}")
    print(f"      papers={len(papers)}")
    print(f"      email_sent={email_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
