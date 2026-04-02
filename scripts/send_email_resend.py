from __future__ import annotations

import json
import urllib.request

RESEND_URL = "https://api.resend.com/emails"


def send_digest_email(
    api_key: str,
    from_email: str,
    to_email: str,
    subject: str,
    html_body: str,
) -> bool:
    if not api_key or not from_email or not to_email:
        return False

    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
    }
    req = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def build_digest_html(report_date: str, report_url: str, digest_text: str,
                      top_links: list[str]) -> str:
    links_html = "".join(
        [f'<li><a href="{url}">{url}</a></li>' for url in top_links])
    digest_html = digest_text.replace("\n", "<br/>")
    return (f"<h2>astroReport 精简日报 - {report_date}</h2>"
            f"<p><a href=\"{report_url}\">点击查看完整版报告</a></p>"
            f"<p>{digest_html}</p>"
            f"<h3>论文链接</h3><ul>{links_html}</ul>")
