#!/usr/bin/env python3
"""本地预览：读取既有完整报告，用新版渲染器生成预览文件。

用法（仓库根目录执行）：
    python scripts/preview_layout.py [YYYY-MM-DD]

默认读取 reports/2026-08-07.md，输出到 tmp/preview/。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 让 scripts 目录内的导入在仓库根目录执行时可用
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.render_report import render_digest, render_full_report
from scripts.send_email_resend import build_digest_html


def _parse_old_full_report(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.replace("\r\n", "\n").split("\n")

    # 全局总结：在 ## 今日概览 之后、下一个 ## 或 "共收录" 之前
    global_summary_lines: list[str] = []
    in_overview = False
    for line in lines:
        if line.startswith("## 今日概览"):
            in_overview = True
            continue
        if in_overview:
            if line.startswith("##") or line.startswith("共收录"):
                break
            global_summary_lines.append(line)
    global_summary = "\n".join(global_summary_lines).strip()

    # 主题分类
    groups: list[dict] = []
    in_groups = False
    group_re = re.compile(r"-\s*\*\*([^*]+)\*\*\s*：\s*(.+)")
    for line in lines:
        if line.startswith("## 主题分类"):
            in_groups = True
            continue
        if in_groups:
            if line.startswith("##"):
                break
            m = group_re.match(line.strip())
            if m:
                label = m.group(1).strip()
                idx_part = m.group(2)
                indices = [
                    int(x) for x in re.findall(r"\[(\d+)\]", idx_part)
                ]
                groups.append({
                    "label": label,
                    "intro": "",
                    "indices": indices,
                })

    # 文献条目
    papers: list[dict] = []
    summaries: dict[str, dict] = {}
    paper_re = re.compile(r"###\s*(\d+)\.\s+(.*)")
    current: dict | None = None
    current_sid = ""
    for line in lines:
        m = paper_re.match(line)
        if m:
            if current:
                papers.append(current)
                summaries[current_sid] = {
                    "id": current_sid,
                    "summary": current.pop("_summary", ""),
                    "keywords": current.pop("_keywords", []),
                }
            idx = int(m.group(1))
            title = m.group(2).strip()
            current = {
                "_idx": idx,
                "title": title,
                "authors": "",
                "first_author_affiliation": "",
                "published": "",
                "link": "",
                "pdf_link": "",
                "_keywords": [],
                "_summary": "",
            }
            current_sid = ""
            continue

        if current is None:
            continue

        stripped = line.strip()
        if stripped.startswith("- Authors:"):
            current["authors"] = stripped[len("- Authors:"):].strip().split(", ")
        elif stripped.startswith("- Affiliation:"):
            current["first_author_affiliation"] = stripped[len("- Affiliation:"):].strip()
        elif stripped.startswith("- Published:"):
            current["published"] = stripped[len("- Published:"):].strip()
        elif stripped.startswith("- arXiv:"):
            current["link"] = stripped[len("- arXiv:"):].strip()
            current_sid = current["link"]
        elif stripped.startswith("- PDF:"):
            current["pdf_link"] = stripped[len("- PDF:"):].strip()
        elif stripped.startswith("- Keywords:"):
            kw_text = stripped[len("- Keywords:"):].strip()
            current["_keywords"] = [k.strip() for k in kw_text.split("、") if k.strip()]
        elif stripped.startswith("- Summary:"):
            current["_summary"] = stripped[len("- Summary:"):].strip()
        elif stripped:
            # 摘要跨行（旧数据一般不存在，但做兼容）
            current["_summary"] += " " + stripped

    if current:
        papers.append(current)
        summaries[current_sid] = {
            "id": current_sid,
            "summary": current.pop("_summary", ""),
            "keywords": current.pop("_keywords", []),
        }

    # 按全局编号排序（旧报告通常已排序）
    papers.sort(key=lambda p: p.get("_idx", 0))
    for p in papers:
        p.pop("_idx", None)

    return {
        "report_date": path.stem,
        "global_summary": global_summary,
        "papers": papers,
        "summaries": summaries,
        "groups": groups,
    }


def _enrich_groups(groups: list[dict], papers: list[dict],
                   summaries: dict) -> list[dict]:
    enriched: list[dict] = []
    for g in groups:
        group_papers: list[dict] = []
        for idx in g.get("indices", []):
            if not (1 <= idx <= len(papers)):
                continue
            paper = papers[idx - 1]
            sid = paper.get("id", paper.get("link", ""))
            sitem = summaries.get(sid, {})
            group_papers.append({
                "title": paper.get("title", "Untitled"),
                "authors": ", ".join(paper.get("authors", [])[:5]) or "Unknown",
                "affiliation": paper.get("first_author_affiliation", ""),
                "summary": sitem.get("summary", ""),
                "keywords": sitem.get("keywords", []),
                "link": paper.get("link", ""),
                "pdf_link": paper.get("pdf_link", ""),
            })
        enriched.append({
            "label": g.get("label", "未分类"),
            "intro": g.get("intro", ""),
            "indices": list(g.get("indices", [])),
            "papers": group_papers,
        })
    return enriched


def main() -> int:
    report_date = sys.argv[1] if len(sys.argv) > 1 else "2026-08-07"
    src = Path(f"reports/{report_date}.md")
    if not src.exists():
        print(f"未找到 {src}")
        return 1

    data = _parse_old_full_report(src)
    papers = data["papers"]
    summaries = data["summaries"]
    groups = data["groups"]
    global_summary = data["global_summary"]
    report_url = f"https://github.com/SolomonCang/astroReport/blob/main/reports/{report_date}.md"

    # 模拟精选亮点：取前 3 篇
    highlights: list[dict] = []
    for paper in papers[:3]:
        sid = paper.get("id", paper.get("link", ""))
        sitem = summaries.get(sid, {})
        highlights.append({
            "title": paper.get("title", "Untitled"),
            "authors": ", ".join(paper.get("authors", [])[:5]) or "Unknown",
            "affiliation": paper.get("first_author_affiliation", ""),
            "summary": sitem.get("summary", ""),
            "keywords": sitem.get("keywords", []),
            "link": paper.get("link", ""),
            "pdf_link": paper.get("pdf_link", ""),
        })

    full_md = render_full_report(
        report_date=report_date,
        global_summary=global_summary,
        papers=papers,
        summaries=summaries,
        groups=groups,
    )
    digest_md = render_digest(
        report_date=report_date,
        global_summary=global_summary,
        papers=papers,
        summaries=summaries,
        report_url=report_url,
        groups=groups,
    )
    email_html = build_digest_html(
        report_date=report_date,
        report_url=report_url,
        global_summary=global_summary,
        paper_count=len(papers),
        groups=_enrich_groups(groups, papers, summaries),
        highlights=highlights,
    )

    out_dir = Path("tmp/preview")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{report_date}.md").write_text(full_md, encoding="utf-8")
    (out_dir / f"{report_date}.digest.md").write_text(digest_md, encoding="utf-8")
    (out_dir / f"{report_date}.html").write_text(email_html, encoding="utf-8")

    print(f"已生成预览文件：")
    print(f"  - {out_dir / f'{report_date}.md'}")
    print(f"  - {out_dir / f'{report_date}.digest.md'}")
    print(f"  - {out_dir / f'{report_date}.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
