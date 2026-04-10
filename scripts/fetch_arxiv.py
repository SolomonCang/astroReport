from __future__ import annotations

import datetime as dt
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _build_query(categories: list[str]) -> str:
    return " OR ".join(f"cat:{c}" for c in categories)


def _parse_entry(entry: ET.Element) -> dict[str, Any]:
    title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS)
             or "").strip()
    summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS)
               or "").strip()
    entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
    published = entry.findtext(
        "atom:published", default="", namespaces=ATOM_NS) or ""
    updated = entry.findtext("atom:updated", default="",
                             namespaces=ATOM_NS) or ""

    authors: list[str] = []
    for author in entry.findall("atom:author", ATOM_NS):
        name = author.findtext("atom:name", default="", namespaces=ATOM_NS)
        if name:
            authors.append(name)

    links = entry.findall("atom:link", ATOM_NS)
    html_link = ""
    pdf_link = ""
    for link in links:
        href = link.attrib.get("href", "")
        rel = link.attrib.get("rel", "")
        link_type = link.attrib.get("type", "")
        title_attr = link.attrib.get("title", "")
        if rel == "alternate" and link_type == "text/html":
            html_link = href
        if title_attr == "pdf" or "pdf" in href:
            pdf_link = href

    categories = [
        cat.attrib.get("term", "")
        for cat in entry.findall("atom:category", ATOM_NS)
    ]

    return {
        "id": entry_id,
        "title": title,
        "summary": summary,
        "authors": authors,
        "published": published,
        "updated": updated,
        "link": html_link or entry_id,
        "pdf_link": pdf_link,
        "categories": [c for c in categories if c],
    }


def _request_entries(query: str, start: int,
                     page_size: int) -> tuple[list[ET.Element], bool]:
    """Fetch one page of arXiv entries.

    Returns a tuple of (entries, api_ok).  api_ok is False when all retry
    attempts failed so callers can distinguish a real API error from a
    legitimate empty result set.
    """
    params = {
        "search_query": query,
        "start": start,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    print(f"      [arxiv] GET {url}")

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "astroReportBot/1.0 (+https://github.com)",
        },
    )

    content = b""
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                content = resp.read()
            last_error = None
            break
        except urllib.error.HTTPError as err:
            last_error = err
            print(
                f"      [arxiv] 请求失败 (attempt {attempt + 1}/3): "
                f"HTTP {err.code} {err.reason}"
            )
        except Exception as err:
            last_error = err
            print(
                f"      [arxiv] 请求失败 (attempt {attempt + 1}/3): "
                f"{type(err).__name__}: {err}"
            )
        if attempt < 2:
            time.sleep(2 * (attempt + 1))

    if last_error is not None:
        print("      [arxiv] 所有重试均失败，返回空结果")
        return [], False

    root = ET.fromstring(content)
    return root.findall("atom:entry", ATOM_NS), True


def fetch_papers(categories: list[str], max_results: int,
                 lookback_hours: int) -> tuple[list[dict[str, Any]], bool]:
    """Fetch arXiv papers for the given categories within the lookback window.

    Returns a tuple of (papers, api_ok).  api_ok is False when at least one
    page request failed on all retries, meaning the result set may be
    incomplete.
    """
    query = _build_query(categories)
    page_size = max(1, int(max_results))

    now_utc = dt.datetime.now(dt.timezone.utc)
    cutoff = now_utc - dt.timedelta(hours=lookback_hours)

    papers: list[dict[str, Any]] = []
    start = 0
    # Cap pages to protect the workflow from pathological loops.
    max_pages = 20
    api_ok = True

    for _ in range(max_pages):
        entries, page_ok = _request_entries(query=query,
                                            start=start,
                                            page_size=page_size)
        if not page_ok:
            api_ok = False
            break
        if not entries:
            break

        reached_cutoff = False
        for entry in entries:
            paper = _parse_entry(entry)
            if not paper["published"]:
                continue
            try:
                published_at = dt.datetime.fromisoformat(
                    paper["published"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if published_at >= cutoff:
                papers.append(paper)
                continue
            reached_cutoff = True
            break

        if reached_cutoff or len(entries) < page_size:
            break
        start += page_size

    dedup: dict[str, dict[str, Any]] = {}
    for paper in papers:
        dedup[paper["id"]] = paper

    sorted_papers = sorted(dedup.values(),
                           key=lambda x: x["published"],
                           reverse=True)
    return sorted_papers, api_ok
