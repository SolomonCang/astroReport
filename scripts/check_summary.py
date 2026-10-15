"""Exercise the configured summarizer on real abstracts without sending mail."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import time
import urllib.parse
import urllib.request

from scripts import summarize_openai as summarizer
from scripts.fetch_arxiv import ARXIV_API, ATOM_NS, _parse_entry, _request_xml
from scripts.render_report import render_digest, render_full_report
from scripts.run_daily import _split_oversized_groups, _validate_groups


class DiagnosticTimeout(BaseException):
    """Keep diagnostic deadlines from being mistaken for a model fallback."""


@contextmanager
def deadline(seconds: int):
    def expire(*_):
        raise DiagnosticTimeout(f"Stage exceeded {seconds} seconds")

    previous = signal.signal(signal.SIGALRM, expire)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def sample_papers(limit: int) -> list[dict]:
    ids: list[str] = []
    for path in sorted(Path("reports").glob("*.md"), reverse=True):
        if path.name.endswith(".digest.md"):
            continue
        for value in re.findall(r"^- arXiv:\s+https?://arxiv.org/abs/(\S+)",
                                path.read_text(), re.M):
            if value not in ids:
                ids.append(value)
            if len(ids) >= limit:
                break
        if len(ids) >= limit:
            break
    if not ids:
        raise ValueError("No report paper IDs available for a real-input check")
    params = urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": limit})
    root = _request_xml(f"{ARXIV_API}?{params}")
    papers = [_parse_entry(entry) for entry in root.findall("atom:entry", ATOM_NS)]
    if len(papers) != len(ids) or any(not p["summary"] for p in papers):
        raise ValueError(f"Incomplete arXiv sample: expected {len(ids)}, got {len(papers)}")
    return papers


def service_probes(url: str, key: str, model: str) -> list[dict]:
    """Compare short public-content requests without depending on paper inputs."""
    base = url.removesuffix("/chat/completions").removesuffix("/v1")
    variants = [
        ("configured_plain", url, model, False),
        ("configured_json", url, model, True),
    ]
    if urllib.parse.urlsplit(url).hostname == "api.deepseek.com":
        variants.extend([
            ("v1_json", base + "/v1/chat/completions", model, True),
            ("pro_json", base + "/chat/completions", "deepseek-v4-pro", True),
        ])
    original_timeout = summarizer.REQUEST_TIMEOUT_SECONDS
    summarizer.REQUEST_TIMEOUT_SECONDS = 30

    def probe(variant):
        name, endpoint, selected_model, json_mode = variant
        payload = {"model": selected_model, "max_tokens": 256,
                   "messages": [{"role": "user", "content": (
                       'Return this JSON object only: {"status":"ok"}' if json_mode else 'Reply with OK only.')} ]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        result = {"name": name, "model": selected_model, "endpoint": endpoint, "passed": False}
        try:
            response = summarizer._call_api(endpoint, key, payload)
            content = response["choices"][0]["message"].get("content", "")
            result["content"] = content
            result["passed"] = (json.loads(content).get("status") == "ok" if json_mode
                                else content.strip().strip(".").upper() == "OK")
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {str(exc).replace(key, '[redacted]')}"
        result["seconds"] = round(time.monotonic() - started, 2)
        return result

    try:
        with ThreadPoolExecutor(max_workers=len(variants)) as pool:
            return list(pool.map(probe, variants))
    finally:
        summarizer.REQUEST_TIMEOUT_SECONDS = original_timeout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paper-limit", type=int, default=16)
    parser.add_argument("--output-dir", default="tmp/summary-check")
    parser.add_argument("--input-file", help="Use an existing public-paper input snapshot")
    parser.add_argument("--batch-size", type=int, default=summarizer.BATCH_SIZE)
    parser.add_argument("--service-probe", action="store_true", help="Compare model/API formats without generating reports")
    args = parser.parse_args()
    if not 1 <= args.paper_limit <= 40 or args.batch_size < 1:
        parser.error("paper-limit must be between 1 and 40")
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(Path("config/config.json").read_text())["openai"]
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise SystemExit("OPENAI_API_KEY is required")
    check: dict = {"model": cfg["model"], "api_base": cfg["api_base"],
                   "stage": "model_health", "calls": [], "passed": False}
    original_call = summarizer._call_api

    def instrumented_call(url, api_key, payload):
        started = time.monotonic()
        response = original_call(url, api_key, payload)
        choice = response.get("choices", [{}])[0]
        meta = {"seconds": round(time.monotonic() - started, 2),
                "model": response.get("model"),
                "finish_reason": choice.get("finish_reason"),
                "usage": response.get("usage")}
        check["calls"].append(meta)
        (output / f"response-{len(check['calls'])}.json").write_text(
            json.dumps(response, ensure_ascii=False, indent=2))
        print("LLM response: " + json.dumps(meta, ensure_ascii=False), flush=True)
        return response

    summarizer._call_api = instrumented_call
    (output / "check.json").write_text(json.dumps(check, ensure_ascii=False, indent=2))
    started = time.monotonic()
    try:
        print("[1/3] Checking production model credentials", flush=True)
        catalog_url = summarizer._build_chat_url(cfg["api_base"]).removesuffix("/chat/completions") + "/models"
        request = urllib.request.Request(catalog_url, headers={"Authorization": f"Bearer {key}"})
        with deadline(30):
            with urllib.request.urlopen(request, timeout=25) as response:
                catalog = json.load(response)
        check["available_models"] = [item["id"] for item in catalog.get("data", [])]
        print("Available models: " + json.dumps(check["available_models"]), flush=True)
        if urllib.parse.urlsplit(cfg["api_base"]).hostname == "api.deepseek.com":
            balance_request = urllib.request.Request(
                "https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"})
            with deadline(30):
                with urllib.request.urlopen(balance_request, timeout=25) as response:
                    check["account_available"] = json.load(response).get("is_available")
            print("Account available: " + str(check["account_available"]), flush=True)
            if check["account_available"] is False:
                raise ValueError("DeepSeek reports insufficient available balance for API calls")
        if args.service_probe:
            check["stage"] = "service_probes"
            # The normal instrumentation writes sequential response files; these
            # parallel probes save only their fixed public-content results.
            summarizer._call_api = original_call
            check["probes"] = service_probes(summarizer._build_chat_url(cfg["api_base"]), key, cfg["model"])
            check["passed"] = any(item["passed"] for item in check["probes"])
            check["stage"] = "complete"
            return 0 if check["passed"] else 1
        if cfg["model"] not in check["available_models"]:
            raise ValueError("Configured model is absent from the provider model catalog")
        check["stage"] = "completion_probe"
        with deadline(120):
            response = instrumented_call(
                summarizer._build_chat_url(cfg["api_base"]), key,
                {"model": cfg["model"], "max_tokens": 256, "response_format": {"type": "json_object"},
                 "messages": [{"role": "user", "content": 'Return JSON only: {"status":"ok"}'}]})
        health = json.loads(response["choices"][0]["message"]["content"])
        if health.get("status") != "ok":
            raise ValueError("Model health response failed validation")

        check["stage"] = "input_fetch"
        print("[2/3] Loading paper inputs", flush=True)
        if args.input_file:
            snapshot = json.loads(Path(args.input_file).read_text())
            papers = snapshot["papers"][:args.paper_limit]
            check["input_source"] = snapshot.get("description", args.input_file)
        else:
            with deadline(180):
                papers = sample_papers(args.paper_limit)
        (output / "papers.json").write_text(json.dumps(papers, ensure_ascii=False, indent=2))
        check["paper_count"] = len(papers)

        check["stage"] = "summarize"
        print(f"[3/3] Summarizing {len(papers)} papers with the current implementation", flush=True)
        with deadline(360):
            summary = summarizer.summarize_papers(
                papers=papers, model=cfg["model"], api_key=key, api_base=cfg["api_base"],
                batch_size=args.batch_size)
        (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        items = summary["items"]
        invalid = [item["id"] for item in items
                   if not re.search(r"[\u4e00-\u9fff]", item.get("summary", ""))
                   or "降级" in item.get("summary", "")]
        groups = _split_oversized_groups(_validate_groups(summary["groups"], len(papers)), 12)
        indices = [index for group in groups for index in group["indices"]]
        check.update({"fallback_paper_ids": invalid,
                      "group_count": len(groups),
                      "groups_cover_all_once": sorted(indices) == list(range(1, len(papers) + 1)),
                      "summary_characters": len(summary["global_summary"]),
                      "author_names_in_global_summary": [name for paper in papers for name in paper["authors"]
                          if name in summary["global_summary"]],
                      "over_120_characters": [item["id"] for item in items if len(item["summary"]) > 120]})
        summaries = {item["id"]: item for item in items}
        report_args = dict(report_date="diagnostic", global_summary=summary["global_summary"],
                           papers=papers, summaries=summaries, groups=groups)
        (output / "report.md").write_text(render_full_report(**report_args))
        (output / "digest.md").write_text(render_digest(
            **report_args, report_url="https://github.com/SolomonCang/astroReport"))
        check["passed"] = (len(items) == len(papers) and not invalid
                           and check["groups_cover_all_once"]
                           and "失败" not in summary["global_summary"])
        check["stage"] = "complete"
    except (Exception, DiagnosticTimeout) as exc:
        check["error"] = f"{type(exc).__name__}: {str(exc).replace(key, '[redacted]')}"
        print(check["error"], flush=True)
    finally:
        check["seconds"] = round(time.monotonic() - started, 2)
        (output / "check.json").write_text(json.dumps(check, ensure_ascii=False, indent=2))
        print(json.dumps(check, ensure_ascii=False, indent=2), flush=True)
    return 0 if check["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
