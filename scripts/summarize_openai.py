from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# Phase 1 每批处理的论文数量，可通过 summarize_papers(batch_size=N) 覆盖
BATCH_SIZE = 15
REQUEST_TIMEOUT_SECONDS = 90


def _paper_id(value: str) -> str:
    """Compare arXiv identifiers independently of URL scheme and version."""
    value = str(value).strip().rstrip("/")
    for marker in ("/abs/", "/pdf/", "oai:arXiv.org:"):
        if marker in value:
            value = value.split(marker, 1)[1]
            break
    value = value.removeprefix("arXiv:").split("?", 1)[0].split("#", 1)[0]
    return re.sub(r"v\d+$", "", value.removesuffix(".pdf"))


def _response_json(response: dict) -> dict:
    choice = response["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("LLM output was truncated at the token limit")
    raw = choice["message"].get("content", "")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("LLM returned empty content")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("LLM output must be a JSON object")
    return parsed


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
        raise ValueError("api_base 未配置，请在 config.json 中设置 api_base 字段。")

    base = api_base.strip()
    if not base:
        raise ValueError("api_base 不能为空字符串，请在 config.json 中设置有效的 api_base 字段。")

    # Accept a full endpoint URL directly.
    if base.endswith("/chat/completions"):
        return base

    # Accept OpenAI-compatible base URLs such as ".../v1".
    return base.rstrip("/") + "/chat/completions"


def _call_api(url: str, api_key: str, payload: dict) -> dict:
    """Bound the whole request, including responses kept alive with blank chunks."""
    hostname = urllib.parse.urlsplit(url).hostname
    if hostname == "api.openai.com":
        payload = dict(payload)
        if "max_tokens" in payload:
            payload.setdefault("max_completion_tokens", payload.pop("max_tokens"))
        # The daily summarizer uses GPT-6 Luna without reasoning so its token
        # budget goes to the final JSON and temperature remains supported.
        model = payload.get("model", "")
        if model == "gpt-6-luna" or model.startswith("gpt-5.4"):
            payload.setdefault("reasoning_effort", "none")
    elif hostname == "api.deepseek.com":
        # Current DeepSeek models default to thinking; summaries need final JSON.
        payload = {"thinking": {"type": "disabled"}, **payload}
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--request-worker"],
            input=json.dumps({"url": url, "api_key": api_key, "payload": payload}),
            capture_output=True, text=True, timeout=REQUEST_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"LLM request exceeded {REQUEST_TIMEOUT_SECONDS} seconds in total"
        ) from exc
    if completed.returncode != 0:
        raise RuntimeError("LLM request worker exited unexpectedly")
    result = json.loads(completed.stdout)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["response"]


def _request_worker() -> None:
    """Use stdin for credentials so they never appear in process arguments."""
    args = json.load(sys.stdin)
    key = args["api_key"]
    req = urllib.request.Request(
        args["url"],
        data=json.dumps(args["payload"]).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            result = {"response": json.loads(resp.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:
        result = {"error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}"}
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    output = json.dumps(result, ensure_ascii=False)
    print(output.replace(key, "[redacted]") if key else output)


def _log_error(msg: str) -> None:
    try:
        with open("summarize_openai_error.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


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
    batch_size: int = BATCH_SIZE,
    on_batch: Any | None = None,
) -> dict[str, Any]:
    """on_batch(items_so_far) 在每批 Phase 1 完成后被调用（可用于实时写入报告）。"""
    if not papers:
        return {
            "global_summary": "今日没有匹配的新文献。",
            "related_ids": [],
            "groups": [],
            "items": [],
        }

    if not api_key:
        return _fallback_summary(papers)

    url = _build_chat_url(api_base)
    skill_text = _load_skill_text()

    # ── Phase 1: 分批逐篇摘要 ──────────────────────────────────────
    all_items: list[dict[str, Any]] = []
    batches = [
        papers[i:i + batch_size] for i in range(0, len(papers), batch_size)
    ]
    total_batches = len(batches)
    for batch_no, batch in enumerate(batches, start=1):
        batch_start = (batch_no - 1) * batch_size + 1
        print(f"      [Phase 1] 批次 {batch_no}/{total_batches}，"
              f"论文 #{batch_start}–#{batch_start + len(batch) - 1}")
        try:
            batch_items = _summarize_batch_items(
                papers_batch=batch,
                batch_start_index=batch_start,
                url=url,
                model=model,
                api_key=api_key,
                skill_text=skill_text,
            )
            all_items.extend(batch_items)
            if on_batch is not None:
                on_batch(list(all_items))
        except Exception as e:
            import traceback
            _log_error(
                f"Phase1 batch {batch_no} failed: {e}\n{traceback.format_exc()}"
            )
            print(f"      [Phase 1] 批次 {batch_no} 失败，降级处理")
            for paper in batch:
                short = paper.get("summary", "").replace("\n", " ").strip()
                if len(short) > 220:
                    short = short[:217] + "..."
                all_items.append({
                    "id": paper.get("id", ""),
                    "summary": short or "摘要生成失败，已降级。",
                    "keywords": paper.get("categories", [])[:3],
                })

    # ── Phase 2: 全局总结 + 主题分组 ─────────────────────────────
    print(f"      [Phase 2] 全局总结 + 主题分组（共 {len(papers)} 篇）")
    try:
        global_summary, related_ids, groups = _summarize_global_and_groups(
            papers=papers,
            items=all_items,
            url=url,
            model=model,
            api_key=api_key,
            skill_text=skill_text,
        )
    except Exception as e:
        import traceback
        _log_error(f"Phase2 failed: {e}\n{traceback.format_exc()}")
        print("      [Phase 2] 失败，降级处理")
        global_summary = "全局总结生成失败，已降级。"
        related_ids = []
        groups = []

    return {
        "global_summary": global_summary,
        "related_ids": related_ids,
        "groups": groups,
        "items": all_items,
    }


def _summarize_batch_items(
    papers_batch: list[dict[str, Any]],
    batch_start_index: int,
    url: str,
    model: str,
    api_key: str,
    skill_text: str,
) -> list[dict[str, Any]]:
    """Phase 1：对一批论文逐篇生成中文摘要，返回 items 列表。"""
    compact = []
    for idx, paper in enumerate(papers_batch, start=batch_start_index):
        compact.append({
            "index": idx,
            "id": paper.get("id", ""),
            "title": paper.get("title", ""),
            "authors": paper.get("authors", []),
            "abstract": paper.get("summary", ""),
            "categories": paper.get("categories", []),
        })

    prompt = {
        "task":
        "为以下天文论文生成中文逐篇摘要。",
        "focus_skill":
        skill_text,
        "format": {
            "items": [{
                "id": "原始 arxiv URL（与输入 id 一致）",
                "summary": "1-2句中文摘要，突出方法与结论，不超过120字",
                "keywords": ["最多3个中文关键词，优先具体物理过程或观测手段"],
            }],
        },
        "rules": [
            "严格返回 JSON 对象，根键为 'items'，不要 markdown 代码块",
            "每篇摘要不超过120字",
            "关键词避免过泛词，优先具体术语",
            "如果 focus_skill 非空，优先突出与其相关的方法和结论",
            "保留原文的适用范围、关键数值、否定与不确定性，不得把相关性写成因果或把候选写成确认",
            "专业术语无准确中文译名时保留英文；Mega-Earths（超大质量岩质行星）不得泛化成全部 Super-Earths（超级地球）",
        ],
        "papers":
        compact,
    }

    payload = {
        "model":
        model,
        "temperature":
        0.3,
        "max_tokens": 8192,
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

    content = _call_api(url, api_key, payload)
    parsed = _response_json(content)

    items_by_id = {
        _paper_id(item.get("id", "")): item
        for item in parsed.get("items", [])
        if isinstance(item, dict) and item.get("id")
    }
    results = []
    for paper in papers_batch:
        pid = paper.get("id", "")
        item = items_by_id.get(_paper_id(pid), {})
        results.append({
            "id": pid,
            "summary": item.get("summary", "").strip() or "摘要生成失败，已降级。",
            "keywords": item.get("keywords", [])[:3],
        })
    return results


def _summarize_global_and_groups(
    papers: list[dict[str, Any]],
    items: list[dict[str, Any]],
    url: str,
    model: str,
    api_key: str,
    skill_text: str,
) -> tuple[str, list[str], list[dict]]:
    """Phase 2：基于已压缩摘要生成全局总结和主题分组。"""
    items_by_id = {item["id"]: item for item in items}
    compact = []
    for idx, paper in enumerate(papers, start=1):
        pid = paper.get("id", "")
        compact.append({
            "index": idx,
            "id": pid,
            "title": paper.get("title", ""),
            "authors": paper.get("authors", []),
            "summary": items_by_id.get(pid, {}).get("summary", ""),
            "categories": paper.get("categories", []),
        })

    prompt = {
        "task":
        "基于以下天文文献的压缩摘要，生成全局总结和主题分组。",
        "focus_skill":
        skill_text,
        "format": {
            "global_summary":
            "5-6句中文概览（约250字）：①各方向文献分布概况；②重点方向核心发现；③点名1-2篇最值得关注的论文及其贡献；优先覆盖 focus_skill 相关方向",
            "related_ids": ["与全局总结最相关的文献 arxiv URL，按相关性排序，最多5个"],
            "groups": [{
                "label": "主题名称（中文，10字以内，尽量对应 focus_skill 中的分类方向）",
                "intro": "该主题一句话概述（15字以内），说明本组共同关注点",
                "indices": "[属于该主题的文献编号列表，整数数组，对应输入的 index 字段]",
            }],
        },
        "rules": [
            "严格返回 JSON 对象，不要 markdown 代码块",
            "global_summary 必须体现 focus_skill 关注方向，并给出 related_ids",
            f"groups 必须覆盖全部文献，每篇只出现在一个组中，共 1-{min(8, len(papers))} 个非空组；按实际主题划分，不得凑组",
            "重点论文必须使用输入 authors 中提供的作者姓名指代，不得虚构作者或使用文献序号代替作者",
            "保留逐篇摘要中的限定条件与不确定性，不得扩大科学结论的适用范围",
            "groups 的 label 优先使用 focus_skill 中的分类名称；其余文献按具体物理主题归入更细的子类，不得已才使用'其他天文'",
            "单个 group 的文献数不得超过 12 篇；若某主题文献过多，请拆成 2 个更具体的子主题",
            "若使用'其他天文'，其文献数不得超过总文献数的 20%",
            "groups 的 indices 必须是整数数组",
        ],
        "papers":
        compact,
    }

    payload = {
        "model":
        model,
        "temperature":
        0.3,
        "max_tokens": 8192,
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

    content = _call_api(url, api_key, payload)
    parsed = _response_json(content)

    summary_text = parsed.get("global_summary", "").strip() or "今日文献摘要已生成。"
    related_ids = parsed.get("related_ids", [])
    if not isinstance(related_ids, list):
        related_ids = []
    paper_ids = {_paper_id(paper.get("id", "")): paper.get("id", "") for paper in papers}
    related_ids = [
        paper_ids[_paper_id(rid)] for rid in related_ids
        if isinstance(rid, str) and _paper_id(rid) in paper_ids
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
            intro = str(g.get("intro", "")).strip()
            if not label or not isinstance(raw_indices, list):
                continue
            valid_indices = [
                int(i) for i in raw_indices
                if isinstance(i, (int, float)) and int(i) in valid_range
            ]
            if valid_indices:
                groups.append({"label": label, "intro": intro, "indices": valid_indices})

    return summary_text, related_ids, groups


if __name__ == "__main__" and sys.argv[1:] == ["--request-worker"]:
    _request_worker()
