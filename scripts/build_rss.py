from __future__ import annotations

import datetime as dt
import email.utils
import json
import xml.etree.ElementTree as ET
from typing import Any


def _to_rfc2822(iso_text: str) -> str:
    d = dt.datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    return email.utils.format_datetime(d)


def build_rss(index_path: str, output_path: str, repo_link: str) -> None:
    with open(index_path, "r", encoding="utf-8") as f:
        index = json.load(f)

    root = ET.Element("rss", version="2.0")
    channel = ET.SubElement(root, "channel")
    ET.SubElement(channel, "title").text = "astroReport Daily Feed"
    ET.SubElement(channel, "link").text = repo_link
    ET.SubElement(
        channel,
        "description").text = "Daily arXiv astronomy reports for Zotero."
    ET.SubElement(channel, "language").text = "zh-cn"

    reports: list[dict[str, Any]] = index.get("reports", [])
    reports = sorted(reports,
                     key=lambda x: x.get("created_at", ""),
                     reverse=True)

    for report in reports:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = report.get("title", "astroReport")
        ET.SubElement(item, "link").text = report.get("report_url", repo_link)
        ET.SubElement(item, "guid").text = report.get(
            "guid", report.get("report_url", repo_link))
        if report.get("created_at"):
            ET.SubElement(item,
                          "pubDate").text = _to_rfc2822(report["created_at"])

        top_links = report.get("paper_links", [])[:5]
        desc_parts = [report.get("digest_summary", "")]
        if top_links:
            desc_parts.append("Top papers:")
            desc_parts.extend(top_links)
        ET.SubElement(item, "description").text = "\n".join(desc_parts).strip()

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(output_path, encoding="utf-8", xml_declaration=True)
