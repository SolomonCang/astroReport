from __future__ import annotations

import html
import json
import re
import urllib.request

RESEND_URL = "https://api.resend.com/emails"


def send_digest_email(
    api_key: str,
    from_email: str,
    to_emails: list[str],
    subject: str,
    html_body: str,
) -> bool:
    if not api_key or not from_email or not to_emails:
        return False

    payload = {
        "from": from_email,
        "to": to_emails,
        "subject": subject,
        "html": html_body,
    }
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "astroReport-mailer/1.0 (+github-actions)",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _linkify_markdown(text: str) -> str:
    pattern = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
    parts: list[str] = []
    last = 0

    for m in pattern.finditer(text):
        parts.append(html.escape(text[last:m.start()]))
        label = html.escape(m.group(1))
        url = html.escape(m.group(2), quote=True)
        parts.append(
            f'<a href="{url}" target="_blank" rel="noopener noreferrer">{label}</a>'
        )
        last = m.end()

    parts.append(html.escape(text[last:]))
    return "".join(parts)


def _render_digest_content(digest_text: str) -> str:
    lines = digest_text.replace("\r\n", "\n").split("\n")
    intro_chunks: list[str] = []
    papers: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    in_paper_section = False

    in_group_section = False
    group_items: list[str] = []

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if line.startswith("## "):
            if in_group_section and group_items:
                tags = "".join(f'<span class="topic-tag">{t}</span>'
                               for t in group_items)
                intro_chunks.append(f'<div class="topic-group">{tags}</div>')
                group_items = []
            heading = line[3:].strip()
            in_paper_section = "今日文献" in heading
            in_group_section = "主题分类" in heading
            if not in_paper_section and not in_group_section:
                intro_chunks.append(
                    f'<h2 class="section-title">{html.escape(heading)}</h2>')
            elif in_group_section:
                intro_chunks.append(
                    f'<h2 class="section-title">{html.escape(heading)}</h2>')
            continue

        if in_group_section and line.startswith("- "):
            # e.g. "- **恒星磁场与磁活动**：10、21、22"
            item = line[2:].strip()
            item = re.sub(r"\*\*([^*]+)\*\*", r"\1", item)
            group_items.append(html.escape(item))
            continue

        if in_paper_section and line.startswith("- "):
            item = line[2:].strip()
            if item.startswith("摘要:"):
                if current:
                    current["summary"] = item[len("摘要:"):].strip()
            elif item.startswith("作者:"):
                if current:
                    current["authors"] = item[len("作者:"):].strip()
            elif item.startswith("单位:"):
                if current:
                    current["affiliation"] = item[len("单位:"):].strip()
            elif item.startswith("链接:"):
                if current:
                    current["link"] = item[len("链接:"):].strip()
            else:
                if current:
                    papers.append(current)
                current = {
                    "title": item,
                    "authors": "",
                    "affiliation": "",
                    "summary": "",
                    "link": "",
                }
            continue

        if in_paper_section:
            if current and current.get("summary"):
                current["summary"] = f'{current["summary"]} {line}'
            elif current and line.startswith("http"):
                current["link"] = line
            continue

        if line.startswith("- "):
            intro_chunks.append(
                f'<p class="intro-bullet">- {_linkify_markdown(line[2:].strip())}</p>'
            )
        else:
            intro_chunks.append(
                f'<p class="intro-text">{_linkify_markdown(line)}</p>')

    if current:
        papers.append(current)

    if group_items:
        tags = "".join(f'<span class="topic-tag">{t}</span>'
                       for t in group_items)
        intro_chunks.append(f'<div class="topic-group">{tags}</div>')

    papers_html: list[str] = []
    for idx, paper in enumerate(papers, start=1):
        title = _linkify_markdown(
            paper.get("title", "").strip() or f"文献 {idx}")
        summary = _linkify_markdown(paper.get("summary", "").strip() or "暂无摘要")
        authors_text = html.escape(paper.get("authors", "").strip())
        affil_text = html.escape(paper.get("affiliation", "").strip())
        link = paper.get("link", "").strip()
        link_html = ""
        if link:
            safe_link = html.escape(link, quote=True)
            link_html = (
                f'<a class="paper-link" href="{safe_link}" target="_blank" '
                f'rel="noopener noreferrer">查看原文</a>')
        meta_parts: list[str] = []
        if authors_text:
            meta_parts.append(f'<span class="paper-authors">{authors_text}</span>')
        if affil_text:
            meta_parts.append(f'<span class="paper-affil">{affil_text}</span>')
        meta_html = (
            f'<p class="paper-meta">{" &nbsp;|&nbsp; ".join(meta_parts)}</p>'
            if meta_parts else ""
        )

        papers_html.append("".join([
            '<article class="paper-card">',
            f'<h3 class="paper-title"><span class="paper-index">{idx}.</span> {title}</h3>',
            meta_html,
            f'<p class="paper-summary">{summary}</p>',
            link_html,
            '</article>',
        ]))

    intro_html = "".join(intro_chunks) if intro_chunks else ""
    papers_block = "".join(papers_html)
    section_title = "<h2 class=\"section-title\">今日文献</h2>" if papers_html else ""

    return "".join([
        '<div class="digest-content">',
        intro_html,
        section_title,
        papers_block,
        '</div>',
    ])


def build_digest_html(report_date: str, report_url: str,
                      digest_text: str) -> str:
    safe_report_url = html.escape(report_url, quote=True)
    digest_html = _render_digest_content(digest_text)

    return f"""<!doctype html>
<html lang="zh-CN">
    <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <style>
            body {{
                margin: 0;
                padding: 0;
                background: #eef3f8;
                color: #1f2a37;
                font-family: "Avenir Next", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            }}
            .wrap {{
                width: 100%;
                padding: 28px 14px;
                box-sizing: border-box;
            }}
            .card {{
                max-width: 760px;
                margin: 0 auto;
                background: #ffffff;
                border: 1px solid #dbe3ec;
                border-radius: 14px;
                overflow: hidden;
            }}
            .hero {{
                background: linear-gradient(135deg, #0b3a5b 0%, #175676 100%);
                color: #ffffff;
                padding: 22px 24px 20px;
            }}
            .tag {{
                display: inline-block;
                padding: 4px 10px;
                font-size: 12px;
                letter-spacing: 0.03em;
                border-radius: 999px;
                background: rgba(255, 255, 255, 0.2);
                margin-bottom: 10px;
            }}
            .title {{
                margin: 0;
                font-size: 24px;
                line-height: 1.3;
                font-weight: 700;
            }}
            .date {{
                margin-top: 6px;
                opacity: 0.9;
                font-size: 14px;
            }}
            .body {{
                padding: 22px 24px 20px;
            }}
            .cta {{
                display: inline-block;
                margin-bottom: 18px;
                background: #0b3a5b;
                color: #ffffff !important;
                text-decoration: none;
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 14px;
                font-weight: 600;
            }}
            .summary {{
                font-size: 15px;
                line-height: 1.82;
                color: #1f2a37;
                word-break: break-word;
            }}
            .section-title {{
                margin: 18px 0 12px;
                font-size: 18px;
                line-height: 1.4;
                color: #0b3a5b;
            }}
            .intro-text {{
                margin: 0 0 10px;
            }}
            .intro-bullet {{
                margin: 0 0 8px;
                color: #2d3a48;
            }}
            .topic-group {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                margin: 4px 0 16px;
            }}
            .topic-tag {{
                display: inline-block;
                background: #e8f0f8;
                color: #0b3a5b;
                font-size: 13px;
                line-height: 1.5;
                padding: 4px 10px;
                border-radius: 6px;
                border: 1px solid #c4d8eb;
                word-break: break-all;
            }}
            .paper-card {{
                border: 1px solid #e2eaf3;
                border-radius: 10px;
                padding: 14px 14px 12px;
                margin: 0 0 12px;
                background: #f9fbfe;
            }}
            .paper-title {{
                margin: 0 0 8px;
                font-size: 16px;
                line-height: 1.5;
                color: #12344d;
            }}
            .paper-index {{
                color: #175676;
                font-weight: 700;
            }}
            .paper-summary {{
                margin: 0;
                font-size: 14px;
                line-height: 1.75;
                color: #2d3a48;
            }}            .paper-meta {
                margin: 0 0 8px;
                font-size: 13px;
                color: #5b6b7d;
                line-height: 1.5;
            }
            .paper-authors {
                font-style: italic;
            }
            .paper-affil {
                color: #7a8fa0;
            }            .paper-link {{
                display: inline-block;
                margin-top: 10px;
                font-size: 13px;
                color: #0b3a5b !important;
                text-decoration: none;
                border-bottom: 1px solid #9fb7cc;
            }}
            .foot {{
                padding: 12px 24px 18px;
                border-top: 1px solid #e6edf5;
                font-size: 12px;
                color: #5b6b7d;
            }}
        </style>
    </head>
    <body>
        <div class="wrap">
            <div class="card">
                <div class="hero">
                    <div class="tag">astroReport</div>
                    <h1 class="title">每日文献精简日报</h1>
                    <div class="date">{report_date}</div>
                </div>
                <div class="body">
                    <a class="cta" href="{safe_report_url}" target="_blank" rel="noopener noreferrer">查看完整版报告</a>
                    <div class="summary">{digest_html}</div>
                </div>
                <div class="foot">此邮件为自动发送。如有问题，请联系管理员: <a href="mailto:astrocang@gmail.com">astrocang@gmail.com</a>。</div>
            </div>
        </div>
    </body>
</html>
"""
