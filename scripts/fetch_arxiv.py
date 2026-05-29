from __future__ import annotations

import datetime as dt
from email.utils import parsedate_to_datetime
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_RSS = "https://export.arxiv.org/rss/{category}"
USER_AGENT = "astroReportBot/1.0 (contact: report@astrocang.space)"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
ARXIV_NS = {"arxiv": "http://arxiv.org/schemas/atom"}
DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}

_SUMMARY_PREFIX_RE = re.compile(
    r"^(?:arXiv:[^\n]+\s+Announce Type:[^\n]+\s+)?Abstract:\s*", re.I)


def _build_query(categories: list[str]) -> str:
    return " OR ".join(f"cat:{c}" for c in categories)


def _normalize_paper_key(value: str) -> str:
    text = value.strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("oai:arXiv.org:"):
        text = text.split(":", 2)[2]
    if "/abs/" in text:
        text = text.split("/abs/", 1)[1]
    elif "/pdf/" in text:
        text = text.split("/pdf/", 1)[1]
    if text.endswith(".pdf"):
        text = text[:-4]
    text = re.sub(r"v\d+$", "", text)
    return text


def _paper_key(paper: dict[str, Any]) -> str:
    for field in ("id", "link", "pdf_link"):
        key = _normalize_paper_key(str(paper.get(field, "")))
        if key:
            return key
    return ""


def _parse_published(value: str) -> dt.datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _clean_summary(text: str) -> str:
    summary = _SUMMARY_PREFIX_RE.sub("", text or "")
    summary = summary.replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", summary).strip()


def _request_xml(url: str) -> ET.Element:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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
        raise RuntimeError(f"请求失败: {url}") from last_error

    return ET.fromstring(content)


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
    first_author_affiliation: str = ""
    for i, author in enumerate(entry.findall("atom:author", ATOM_NS)):
        name = author.findtext("atom:name", default="", namespaces=ATOM_NS)
        if name:
            authors.append(name)
        if i == 0:
            affil = author.findtext("arxiv:affiliation",
                                    default="",
                                    namespaces=ARXIV_NS)
            if affil:
                first_author_affiliation = affil.strip()

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
        "first_author_affiliation": first_author_affiliation,
        "published": published,
        "updated": updated,
        "link": html_link or entry_id,
        "pdf_link": pdf_link,
        "categories": [c for c in categories if c],
    }


def _request_entries(query: str, start: int,
                     page_size: int) -> list[ET.Element]:
    params = {
        "search_query": query,
        "start": start,
        "max_results": page_size,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
    root = _request_xml(url)
    return root.findall("atom:entry", ATOM_NS)


def _request_rss_items(category: str) -> list[ET.Element]:
    url = ARXIV_RSS.format(category=urllib.parse.quote(category, safe=""))
    root = _request_xml(url)
    channel = root.find("channel")
    if channel is None:
        return []
    return channel.findall("item")


def _parse_rss_item(item: ET.Element) -> dict[str, Any]:
    title = (item.findtext("title", default="") or "").strip()
    description = item.findtext("description", default="") or ""
    link = item.findtext("link", default="") or ""
    guid = item.findtext("guid", default="") or ""
    published = item.findtext("pubDate", default="") or ""

    authors_text = item.findtext("dc:creator", default="", namespaces=DC_NS)
    authors = [
        author.strip() for author in authors_text.split(",") if author.strip()
    ]

    categories = [(category.text or "").strip()
                  for category in item.findall("category")
                  if (category.text or "").strip()]

    paper_id = _normalize_paper_key(guid or link)
    published_at = _parse_published(published)
    published_iso = published_at.isoformat() if published_at else ""

    return {
        "id": paper_id,
        "title": title,
        "summary": _clean_summary(description),
        "authors": authors,
        "first_author_affiliation": "",
        "published": published_iso,
        "updated": published_iso,
        "link": link or f"https://arxiv.org/abs/{paper_id}",
        "pdf_link":
        f"https://arxiv.org/pdf/{paper_id}.pdf" if paper_id else "",
        "categories": categories,
    }


def _fetch_api_papers(categories: list[str], max_results: int,
                      lookback_hours: int) -> list[dict[str, Any]]:
    query = _build_query(categories)
    page_size = max(1, int(max_results))

    now_utc = dt.datetime.now(dt.timezone.utc)
    cutoff = now_utc - dt.timedelta(hours=lookback_hours)

    papers: list[dict[str, Any]] = []
    start = 0
    max_pages = 20

    for _ in range(max_pages):
        entries = _request_entries(query=query,
                                   start=start,
                                   page_size=page_size)
        if not entries:
            break

        reached_cutoff = False
        for entry in entries:
            paper = _parse_entry(entry)
            published_at = _parse_published(paper["published"])
            if published_at is None:
                continue
            if published_at >= cutoff:
                papers.append(paper)
                continue
            reached_cutoff = True
            break

        if reached_cutoff or len(entries) < page_size:
            break
        start += page_size

    return papers


def _fetch_rss_papers(categories: list[str],
                      lookback_hours: int) -> list[dict[str, Any]]:
    now_utc = dt.datetime.now(dt.timezone.utc)
    cutoff = now_utc - dt.timedelta(hours=lookback_hours)

    papers: list[dict[str, Any]] = []
    for category in categories:
        for item in _request_rss_items(category):
            paper = _parse_rss_item(item)
            published_at = _parse_published(paper["published"])
            if published_at is None or published_at < cutoff:
                continue
            if category not in paper["categories"]:
                paper["categories"] = [category, *paper["categories"]]
            papers.append(paper)

    return papers


def fetch_papers(categories: list[str], max_results: int,
                 lookback_hours: int) -> list[dict[str, Any]]:
    try:
        papers = _fetch_api_papers(
            categories=categories,
            max_results=max_results,
            lookback_hours=lookback_hours,
        )
    except Exception:
        papers = _fetch_rss_papers(
            categories=categories,
            lookback_hours=lookback_hours,
        )

    dedup: dict[str, dict[str, Any]] = {}
    for paper in papers:
        key = _paper_key(paper)
        if key:
            dedup[key] = paper

    sorted_papers = sorted(dedup.values(),
                           key=lambda x: x["published"],
                           reverse=True)
    return sorted_papers
