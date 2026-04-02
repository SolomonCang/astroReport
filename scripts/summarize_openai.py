from __future__ import annotations

import json
import urllib.request
from typing import Any

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _build_chat_url(api_base: str | None) -> str:
    if not api_base:
        return OPENAI_CHAT_URL

    base = api_base.strip()
    if not base:
        return OPENAI_CHAT_URL

    # Accept a full endpoint URL directly.
    if base.endswith("/chat/completions"):
        return base

    # Accept OpenAI-compatible base URLs such as ".../v1".
    return base.rstrip("/") + "/chat/completions"


def _fallback_summary(papers: list[dict[str, Any]]) -> dict[str, Any]:
    items = []
    for paper in papers:
        short = paper.get("summary", "").replace("\n", " ").strip()
        if len(short) > 220:
            short = short[:217] + "..."
        items.append({
            "id": paper.get("id", ""),
            "summary": short or "无摘要",
            "keywords": paper.get("categories", [])[:3],
        })
    return {
        "global_summary": "今日文献已更新。以下为自动生成的精简摘要。",
        "items": items,
    }


def summarize_papers(
    papers: list[dict[str, Any]],
    model: str,
    api_key: str,
    api_base: str | None = None,
) -> dict[str, Any]:
    if not papers:
        return {"global_summary": "今日没有匹配的新文献。", "items": []}

    if not api_key:
        return _fallback_summary(papers)

    compact = []
    for paper in papers:
        compact.append({
            "id": paper.get("id", ""),
            "title": paper.get("title", ""),
            "summary": paper.get("summary", ""),
            "authors": paper.get("authors", [])[:4],
            "categories": paper.get("categories", []),
            "link": paper.get("link", ""),
        })

    prompt = {
        "task": "你是科研情报助手。请为天文文献生成中文日报摘要。",
        "format": {
            "global_summary":
            "2-3句中文概览",
            "items": [{
                "id": "保持输入id",
                "summary": "1-2句中文摘要，突出方法与结论",
                "keywords": ["最多3个中文关键词"],
            }],
        },
        "rules": [
            "严格返回JSON对象，不要markdown",
            "每篇摘要不超过120字",
            "关键词避免过泛词",
        ],
        "papers": compact,
    }

    payload = {
        "model":
        model,
        "temperature":
        0.2,
        "response_format": {
            "type": "json_object"
        },
        "messages": [
            {
                "role": "system",
                "content": "你是严谨的学术摘要助手。"
            },
            {
                "role": "user",
                "content": json.dumps(prompt, ensure_ascii=False)
            },
        ],
    }

    req = urllib.request.Request(
        _build_chat_url(api_base),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            content = json.loads(resp.read().decode("utf-8"))
        raw = content["choices"][0]["message"]["content"]
        parsed = json.loads(raw)

        items_by_id = {
            item.get("id", ""): item
            for item in parsed.get("items", [])
        }
        items = []
        for paper in papers:
            item = items_by_id.get(paper.get("id", ""), {})
            items.append({
                "id": paper.get("id", ""),
                "summary": item.get("summary", "").strip() or "摘要生成失败，已降级。",
                "keywords": item.get("keywords", [])[:3],
            })

        summary_text = parsed.get("global_summary", "").strip() or "今日文献摘要已生成。"
        return {
            "global_summary": summary_text,
            "items": items,
        }
    except Exception:
        return _fallback_summary(papers)
