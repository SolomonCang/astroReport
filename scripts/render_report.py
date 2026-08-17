from __future__ import annotations

import datetime as dt
from typing import Any


def _render_paper_full(
    idx: int,
    paper: dict[str, Any],
    summary_item: dict[str, Any],
) -> list[str]:
    keyword_text = "、".join(
        summary_item.get("keywords", [])) if summary_item.get("keywords") else "无"
    authors = ", ".join(paper.get("authors", [])[:5]) or "Unknown"
    affil = paper.get("first_author_affiliation", "")
    lines = [
        f"### {idx}. {paper.get('title', 'Untitled')}",
        f"- Authors: {authors}",
    ]
    if affil:
        lines.append(f"- Affiliation: {affil}")
    lines.extend([
        f"- Published: {paper.get('published', '')}",
        f"- arXiv: {paper.get('link', '')}",
        f"- PDF: {paper.get('pdf_link', '')}",
        f"- Keywords: {keyword_text}",
        f"- Summary: {summary_item.get('summary', '')}",
        "",
    ])
    return lines


def _render_paper_digest(
    paper: dict[str, Any],
    summary_item: dict[str, Any],
) -> list[str]:
    authors = ", ".join(paper.get("authors", [])[:5]) or "Unknown"
    affil = paper.get("first_author_affiliation", "")
    keywords = summary_item.get("keywords", [])
    keyword_text = "、".join(keywords) if keywords else ""
    lines = [
        f"- **{paper.get('title', 'Untitled')}**",
        f"  - 作者: {authors}",
    ]
    if affil:
        lines.append(f"  - 单位: {affil}")
    if keyword_text:
        lines.append(f"  - 关键词: {keyword_text}")
    lines.extend([
        f"  - 摘要: {summary_item.get('summary', '')}",
        f"  - 链接: {paper.get('link', '')}",
    ])
    if paper.get("pdf_link"):
        lines.append(f"  - PDF: {paper.get('pdf_link', '')}")
    return lines


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
        lines.append("## 目录")
        for cat_idx, g in enumerate(groups, start=1):
            label = g.get("label", "未分类")
            count = len(g.get("indices", []))
            lines.append(f"- [{label}（{count} 篇）](#cat-{cat_idx})")
        lines.append("")

        for cat_idx, g in enumerate(groups, start=1):
            label = g.get("label", "未分类")
            count = len(g.get("indices", []))
            intro = g.get("intro", "").strip()
            lines.append(
                f"## {label}（{count} 篇） <a id=\"cat-{cat_idx}\"></a>")
            if intro:
                lines.append(intro)
                lines.append("")
            for idx in g.get("indices", []):
                if not (1 <= idx <= len(papers)):
                    continue
                paper = papers[idx - 1]
                sid = paper.get("id", "")
                lines.extend(
                    _render_paper_full(idx, paper, summaries.get(sid, {})))
    else:
        lines.append("## 文献条目")
        for idx, paper in enumerate(papers, start=1):
            sid = paper.get("id", "")
            lines.extend(
                _render_paper_full(idx, paper, summaries.get(sid, {})))

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
        lines.append("## 分类速览")
        pills: list[str] = []
        for cat_idx, g in enumerate(groups, start=1):
            label = g.get("label", "未分类")
            count = len(g.get("indices", []))
            pills.append(f"[{label} {count}](#cat-{cat_idx})")
        lines.append(" · ".join(pills))
        lines.append("")

        for cat_idx, g in enumerate(groups, start=1):
            label = g.get("label", "未分类")
            count = len(g.get("indices", []))
            intro = g.get("intro", "").strip()
            lines.append(
                f"## {label}（{count} 篇） <a id=\"cat-{cat_idx}\"></a>")
            if intro:
                lines.append(intro)
                lines.append("")
            for idx in g.get("indices", []):
                if not (1 <= idx <= len(papers)):
                    continue
                paper = papers[idx - 1]
                sid = paper.get("id", "")
                lines.extend(
                    _render_paper_digest(paper, summaries.get(sid, {})))
    else:
        lines.append("## 今日文献")
        for paper in papers:
            sid = paper.get("id", "")
            lines.extend(
                _render_paper_digest(paper, summaries.get(sid, {})))

    return "\n".join(lines)
