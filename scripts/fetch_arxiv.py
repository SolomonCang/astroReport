from __future__ import annotations

import datetime as dt
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _build_query(categories: list[str]) -> str:
    return " OR ".join(f"cat:{c}" for c in categories)


def _parse_entry(entry: ET.Element) -> dict[str, Any]:
    title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
    summary = (entry.findtext("atom:summary", default="", namespaces=ATOM_NS) or "").strip()
    entry_id = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
    published = entry.findtext("atom:published", default="", namespaces=ATOM_NS) or ""
    updated = entry.findtext("atom:updated", default="", namespaces=ATOM_NS) or ""

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

    categories = [cat.attrib.get("term", "") for cat in entry.findall("atom:category", ATOM_NS)]

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


def fetch_papers(categories: list[str], max_results: int, lookback_hours: int) -> list[dict[str, Any]]:
    query = _build_query(categories)
    params = {
        "search_query": query,
        "start": 0,
        "max_results": max_results,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"

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
        except Exception as err:
            last_error = err
            time.sleep(2 * (attempt + 1))

    if last_error is not None:
        return []

    root = ET.fromstring(content)
    entries = root.findall("atom:entry", ATOM_NS)

    now_utc = dt.datetime.now(dt.timezone.utc)
    cutoff = now_utc - dt.timedelta(hours=lookback_hours)

    papers: list[dict[str, Any]] = []
    for entry in entries:
        paper = _parse_entry(entry)
        if not paper["published"]:
            continue
        try:
            published_at = dt.datetime.fromisoformat(paper["published"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if published_at >= cutoff:
            papers.append(paper)

    dedup: dict[str, dict[str, Any]] = {}
    for paper in papers:
        dedup[paper["id"]] = paper

    sorted_papers = sorted(dedup.values(), key=lambda x: x["published"], reverse=True)
    return sorted_papers
