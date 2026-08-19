"""Deterministic, dependency-free exports for verified Alexandrian books."""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any


def safe_export_name(title: str, suffix: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return f"{stem or 'alexandrian-book'}.{suffix}"


def evidence_manifest(
    *,
    book: dict[str, Any],
    provenance: dict[str, Any],
    run_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Build a portable evidence and workflow identity record."""
    return {
        "schema_version": "alexandrian-portable-evidence.v1",
        "book": {
            "book_id": book.get("book_id"),
            "title": book.get("title"),
            "edition": book.get("edition"),
            "chapter_count": book.get("chapter_count"),
            "verified_sha256": book.get("verified_sha256"),
        },
        "evidence": {
            "source_count": provenance.get("source_count", 0),
            "sources": provenance.get("sources", []),
            "open_status_count": provenance.get("evidence_status_count", 0),
            "open_statuses": provenance.get("evidence_statuses", []),
            "gaps": provenance.get("evidence_gaps", []),
        },
        "workflow": {
            "status": run_receipt.get("status"),
            "review_state": run_receipt.get("review_state"),
            "workflow_policy_version": run_receipt.get("workflow_policy_version"),
            "finish_guardrails": run_receipt.get("finish_guardrails"),
            "provider_sequence": run_receipt.get("provider_sequence", []),
            "source_decisions": run_receipt.get("source_decisions", []),
            "transitions": run_receipt.get("transitions", []),
            "artifacts": run_receipt.get("artifacts", {}),
        },
        "generated_at": (
            (book.get("assembly") or {}).get("assembled_at")
            or run_receipt.get("generated_at")
        ),
    }


def _inline_markdown(value: str) -> str:
    escaped = html.escape(value, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\((https?://[^)]+)\)",
        r'<a href="\2">\1</a>',
        escaped,
    )
    return escaped


def markdown_to_xhtml(markdown: str) -> str:
    """Render the book's conservative Markdown subset as EPUB XHTML."""
    body: list[str] = []
    paragraph: list[str] = []
    list_kind: str | None = None

    def flush_paragraph() -> None:
        if paragraph:
            body.append(f"<p>{_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_kind
        if list_kind is not None:
            body.append(f"</{list_kind}>")
            list_kind = None

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            close_list()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            close_list()
            level = len(heading.group(1))
            body.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            continue
        unordered = re.match(r"^[-*]\s+(.+)$", line)
        ordered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if unordered or ordered:
            flush_paragraph()
            desired = "ul" if unordered else "ol"
            if list_kind != desired:
                close_list()
                body.append(f"<{desired}>")
                list_kind = desired
            match = unordered or ordered
            body.append(f"<li>{_inline_markdown(match.group(1))}</li>")
            continue
        if line.startswith(">"):
            flush_paragraph()
            close_list()
            body.append(f"<blockquote>{_inline_markdown(line[1:].strip())}</blockquote>")
            continue
        if re.fullmatch(r"-{3,}|\*{3,}", line):
            flush_paragraph()
            close_list()
            body.append("<hr />")
            continue
        paragraph.append(line)
    flush_paragraph()
    close_list()
    return "\n".join(body)


def epub_bytes(
    *,
    book_id: str,
    title: str,
    markdown: str,
    evidence: dict[str, Any],
) -> bytes:
    """Return a valid EPUB 3 archive containing the book and evidence record."""
    recorded_at = evidence.get("generated_at")
    try:
        modified_at = datetime.fromisoformat(str(recorded_at)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        modified_at = datetime(1970, 1, 1, tzinfo=timezone.utc)
    chapter = markdown_to_xhtml(markdown)
    evidence_json = html.escape(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        quote=False,
    )
    book_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head><title>{html.escape(title)}</title><link rel="stylesheet" href="style.css" type="text/css" /></head>
<body>{chapter}</body></html>'''
    evidence_xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" lang="en">
<head><title>Evidence manifest</title><link rel="stylesheet" href="style.css" type="text/css" /></head>
<body><h1>Evidence manifest</h1><p>This record travels with the verified edition.</p><pre>{evidence_json}</pre></body></html>'''
    nav_xhtml = '''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="en">
<head><title>Contents</title></head><body><nav epub:type="toc"><h1>Contents</h1><ol><li><a href="book.xhtml">Book</a></li><li><a href="evidence.xhtml">Evidence manifest</a></li></ol></nav></body></html>'''
    package = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="book-id">{html.escape(book_id)}</dc:identifier><dc:title>{html.escape(title)}</dc:title><dc:language>en</dc:language><meta property="dcterms:modified">{modified_at.strftime('%Y-%m-%dT%H:%M:%SZ')}</meta></metadata>
<manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="book" href="book.xhtml" media-type="application/xhtml+xml"/><item id="evidence" href="evidence.xhtml" media-type="application/xhtml+xml"/><item id="style" href="style.css" media-type="text/css"/></manifest>
<spine><itemref idref="book"/><itemref idref="evidence"/></spine></package>'''
    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'''
    style = "body{font-family:serif;line-height:1.55;max-width:42em;margin:auto;padding:5%;color:#241d16}h1,h2,h3{line-height:1.12}blockquote{border-left:.2em solid #9b784b;padding-left:1em}pre{white-space:pre-wrap;font:0.75em monospace}"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("EPUB/package.opf", package)
        archive.writestr("EPUB/nav.xhtml", nav_xhtml)
        archive.writestr("EPUB/book.xhtml", book_xhtml)
        archive.writestr("EPUB/evidence.xhtml", evidence_xhtml)
        archive.writestr("EPUB/style.css", style)
    return output.getvalue()
