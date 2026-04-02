from __future__ import annotations

import datetime as dt
from typing import Any


def render_full_report(
    report_date: str,
    global_summary: str,
    papers: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
) -> str:
    lines = [
        f"# astroReport 日报 - {report_date}",
        "",
        "## 今日概览",
        global_summary,
        "",
        f"共收录 {len(papers)} 篇文献。",
        "",
        "## 文献条目",
    ]

    for idx, paper in enumerate(papers, start=1):
        sid = paper.get("id", "")
        sitem = summaries.get(sid, {})
        keyword_text = "、".join(sitem.get(
            "keywords", [])) if sitem.get("keywords") else "无"
        authors = ", ".join(paper.get("authors", [])[:6]) or "Unknown"
        lines.extend([
            f"### {idx}. {paper.get('title', 'Untitled')}",
            f"- Authors: {authors}",
            f"- Published: {paper.get('published', '')}",
            f"- arXiv: {paper.get('link', '')}",
            f"- PDF: {paper.get('pdf_link', '')}",
            f"- Keywords: {keyword_text}",
            f"- Summary: {sitem.get('summary', '')}",
            "",
        ])

    lines.extend([
        "## 元数据",
        f"- Generated At (UTC): {dt.datetime.now(dt.timezone.utc).isoformat()}",
        "- Generator: GitHub Actions + OpenAI",
        "",
    ])
    return "\n".join(lines)


def render_digest(
    report_date: str,
    global_summary: str,
    papers: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    report_url: str,
) -> str:
    lines = [
        f"# astroReport 精简版 - {report_date}",
        "",
        global_summary,
        "",
        f"完整版: {report_url}",
        "",
        "## 今日前10篇",
    ]

    for paper in papers[:10]:
        sid = paper.get("id", "")
        sitem = summaries.get(sid, {})
        lines.extend([
            f"- {paper.get('title', 'Untitled')}",
            f"  - 摘要: {sitem.get('summary', '')}",
            f"  - 链接: {paper.get('link', '')}",
        ])

    return "\n".join(lines)
