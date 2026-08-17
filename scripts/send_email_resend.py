from __future__ import annotations

import html
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

RESEND_URL = "https://api.resend.com/emails"

# 模板目录与渲染环境
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


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
    """把 markdown 链接 [label](url) 转成 HTML 链接。"""
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


def _plain_to_html(text: str) -> str:
    """将普通文本转成 HTML：转义、换行转 br、识别 markdown 链接。"""
    linked = _linkify_markdown(text)
    return linked.replace("\n", "<br/>")


def build_digest_html(
    report_date: str,
    report_url: str,
    global_summary: str,
    paper_count: int,
    groups: list[dict[str, Any]],
    highlights: list[dict[str, Any]] | None = None,
) -> str:
    """基于结构化分组数据渲染邮件 HTML。

    参数：
        report_date: 报告日期，如 "2026-08-07"。
        report_url: 完整版报告链接。
        global_summary: 全局总结纯文本。
        paper_count: 收录文献总数。
        groups: 分组列表，每项包含 label、intro、indices、papers。
            papers 为字典列表，键：title, authors, affiliation, summary,
            keywords, link, pdf_link。
        highlights: 精选亮点论文列表，结构与 papers 单篇一致。
    """
    template = _jinja_env.get_template("email_digest.html")
    return template.render(
        report_date=report_date,
        report_url=report_url,
        global_summary_html=_plain_to_html(global_summary),
        paper_count=paper_count,
        groups=groups or [],
        highlights=highlights or [],
    )
