from __future__ import annotations

import datetime as dt
from typing import Any


def render_full_report(
    report_date: str,
    global_summary: str,
    papers: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    groups: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"# astroReport 日报 - {report_date}",
        "",
        "## 今日概览",
        global_summary,
        "",
        f"共收录 {len(papers)} 篇文献。",
        "",
    ]

    if groups:
        lines.append("## 主题分类")
        for g in groups:
            idx_str = "、".join(f"[{i}]" for i in g["indices"])
            lines.append(f"- **{g['label']}**：{idx_str}")
        lines.append("")

    lines.append("## 文献条目")

    for idx, paper in enumerate(papers, start=1):
        sid = paper.get("id", "")
        sitem = summaries.get(sid, {})
        keyword_text = "、".join(sitem.get(
            "keywords", [])) if sitem.get("keywords") else "无"
        authors = ", ".join(paper.get("authors", [])[:5]) or "Unknown"
        affil = paper.get("first_author_affiliation", "")
        lines.extend([
            f"### {idx}. {paper.get('title', 'Untitled')}",
            f"- Authors: {authors}",
        ])
        if affil:
            lines.append(f"- Affiliation: {affil}")
        lines.extend([
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
        "- Generator: GitHub Actions + LLM",
        "",
    ])
    return "\n".join(lines)


def render_digest(
    report_date: str,
    global_summary: str,
    papers: list[dict[str, Any]],
    summaries: dict[str, dict[str, Any]],
    report_url: str,
    groups: list[dict[str, Any]] | None = None,
) -> str:
    paper_count = len(papers)
    lines = [
        f"# astroReport 精简版 - {report_date}",
        "",
        global_summary,
        "",
        f"共收录 {paper_count} 篇文献。",
        "",
        f"完整版: {report_url}",
        "",
    ]

    if groups:
        lines.append("## 主题分类")
        for g in groups:
            idx_str = "、".join(str(i) for i in g["indices"])
            lines.append(f"- **{g['label']}**：{idx_str}")
        lines.append("")

    lines.append("## 今日文献")

    for paper in papers:
        sid = paper.get("id", "")
        sitem = summaries.get(sid, {})
        authors = ", ".join(paper.get("authors", [])[:5]) or "Unknown"
        affil = paper.get("first_author_affiliation", "")
        lines.extend([
            f"- {paper.get('title', 'Untitled')}",
            f"  - 作者: {authors}",
        ])
        if affil:
            lines.append(f"  - 单位: {affil}")
        lines.extend([
            f"  - 摘要: {sitem.get('summary', '')}",
            f"  - 链接: {paper.get('link', '')}",
        ])

    return "\n".join(lines)
