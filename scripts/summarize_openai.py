from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _load_skill_text() -> str:
    candidates = [
        Path("config/focus_area.md"),
        Path("config/skill.md"),
        Path("skill.md"),
    ]
    for path in candidates:
        try:
            if path.exists():
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    return text
        except Exception:
            continue
    return ""


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
        "global_summary":
        "今日文献已更新。以下为自动生成的精简摘要。",
        "related_ids":
        [paper.get("id", "") for paper in papers[:3] if paper.get("id", "")],
        "groups": [],
        "items":
        items,
    }


def summarize_papers(
    papers: list[dict[str, Any]],
    model: str,
    api_key: str,
    api_base: str | None = None,
) -> dict[str, Any]:
    if not papers:
        return {
            "global_summary": "今日没有匹配的新文献。",
            "related_ids": [],
            "groups": [],
            "items": []
        }

    if not api_key:
        return _fallback_summary(papers)

    compact = []
    for idx, paper in enumerate(papers, start=1):
        compact.append({
            "index": idx,
            "id": paper.get("id", ""),
            "title": paper.get("title", ""),
            "summary": paper.get("summary", ""),
            "authors": paper.get("authors", [])[:4],
            "categories": paper.get("categories", []),
            "link": paper.get("link", ""),
        })

    skill_text = _load_skill_text()

    prompt = {
        "task":
        "你是科研情报助手。请为天文文献生成中文日报摘要，并对文献进行主题分类。",
        "focus_skill":
        skill_text,
        "format": {
            "global_summary":
            "3-4句中文概览，需优先覆盖 focus_skill 相关方向，并指出今日各主题的文献分布",
            "related_ids": ["与全局总结最相关的输入文献id（arxiv URL），按相关性排序，最多5个"],
            "groups": [{
                "label": "主题名称（中文，10字以内，尽量对应 focus_skill 中的分类方向）",
                "indices": "[属于该主题的文献编号列表，使用输入中的 index 字段，整数数组]",
            }],
            "items": [{
                "id": "保持输入id（arxiv URL）",
                "summary": "1-2句中文摘要，突出方法与结论",
                "keywords": ["最多3个中文关键词，优先具体物理过程或观测手段"],
            }],
        },
        "rules": [
            "严格返回JSON对象，不要markdown代码块",
            "每篇摘要不超过120字",
            "关键词避免过泛词，优先具体",
            "如果 focus_skill 非空，优先按其要求提取重点信息",
            "global_summary 必须体现 focus_skill 关注方向，并给出 related_ids",
            "groups 必须覆盖全部文献，每篇文献只出现在一个组中，共 3-8 个组",
            "groups 的 label 优先使用 focus_skill 中的分类名称，其余文献归入'其他天文'或更具体的子类",
            "groups 的 indices 字段必须是整数数组，对应输入文献的 index 字段",
        ],
        "papers":
        compact,
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
        related_ids = parsed.get("related_ids", [])
        if not isinstance(related_ids, list):
            related_ids = []
        paper_ids = {paper.get("id", "") for paper in papers}
        related_ids = [
            rid for rid in related_ids
            if isinstance(rid, str) and rid in paper_ids
        ][:5]

        valid_range = set(range(1, len(papers) + 1))
        raw_groups = parsed.get("groups", [])
        groups: list[dict] = []
        if isinstance(raw_groups, list):
            for g in raw_groups:
                if not isinstance(g, dict):
                    continue
                label = str(g.get("label", "")).strip()
                raw_indices = g.get("indices", [])
                if not label or not isinstance(raw_indices, list):
                    continue
                valid_indices = [
                    int(i) for i in raw_indices
                    if isinstance(i, (int, float)) and int(i) in valid_range
                ]
                if valid_indices:
                    groups.append({"label": label, "indices": valid_indices})

        return {
            "global_summary": summary_text,
            "related_ids": related_ids,
            "groups": groups,
            "items": items,
        }
    except Exception:
        return _fallback_summary(papers)
