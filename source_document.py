"""Deterministic, hash-bound source documents and evidence locators.

Source receipts preserve fetch authority and the original content hash.  This
module adds a companion representation that retains stable document structure
without changing or invalidating existing receipts.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import uuid
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


NORMALIZER_VERSION = "alexandrian-source-normalizer-v1"
MAX_PDF_PAGES = 500
MAX_PDF_EXTRACTED_CHARACTERS = 1_000_000
HASH_PATTERN = r"^[0-9a-f]{64}$"
RECEIPT_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$"


class SourceDocumentError(RuntimeError):
    """Report invalid or unsupported source-document material."""


class StrictModel(BaseModel):
    """Reject undeclared fields in durable evidence contracts."""

    model_config = ConfigDict(extra="forbid")


class SourceBlock(StrictModel):
    """One stable structural block in a normalized source."""

    block_id: str = Field(pattern=r"^B[0-9]{6}$")
    block_type: Literal[
        "heading",
        "paragraph",
        "list_item",
        "table_cell",
        "quote",
        "code",
        "text",
        "page",
    ]
    heading_path: list[str] = Field(default_factory=list)
    page_number: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    text_sha256: str = Field(pattern=HASH_PATTERN)
    document_start: int = Field(ge=0)
    document_end: int = Field(gt=0)


class SourceDocument(StrictModel):
    """Versioned companion record for one immutable source receipt."""

    schema_version: Literal["1.0"] = "1.0"
    receipt_id: str = Field(pattern=RECEIPT_PATTERN)
    source_content_sha256: str = Field(pattern=HASH_PATTERN)
    content_type: str = Field(min_length=1)
    title: str | None = None
    normalizer_version: Literal[NORMALIZER_VERSION] = NORMALIZER_VERSION
    content_truncated: bool
    blocks: list[SourceBlock] = Field(min_length=1)
    document_text_sha256: str = Field(pattern=HASH_PATTERN)
    source_document_sha256: str = Field(pattern=HASH_PATTERN)


class EvidenceLocator(StrictModel):
    """Exact, verified location of one quotation in a source document."""

    schema_version: Literal["1.0"] = "1.0"
    locator_id: str = Field(pattern=r"^locator-[0-9a-f]{24}$")
    receipt_id: str = Field(pattern=RECEIPT_PATTERN)
    source_document_sha256: str = Field(pattern=HASH_PATTERN)
    block_id: str = Field(pattern=r"^B[0-9]{6}$")
    block_type: str = Field(min_length=1)
    heading_path: list[str] = Field(default_factory=list)
    page_number: int | None = Field(default=None, ge=1)
    character_start: int = Field(ge=0)
    character_end: int = Field(gt=0)
    exact_quote: str = Field(min_length=1, max_length=4_000)
    exact_quote_sha256: str = Field(pattern=HASH_PATTERN)


@dataclass(frozen=True)
class _DraftBlock:
    block_type: str
    text: str
    heading_level: int | None = None
    page_number: int | None = None


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_hash(document: dict, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(hash_field, None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_inline_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalized_page_text(value: str) -> str:
    lines = [_normalized_inline_text(line) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


class _StructuredHTMLExtractor(HTMLParser):
    """Extract inert structural blocks without executing source content."""

    BLOCK_TAGS = {
        "blockquote": "quote",
        "code": "code",
        "h1": "heading",
        "h2": "heading",
        "h3": "heading",
        "h4": "heading",
        "h5": "heading",
        "h6": "heading",
        "li": "list_item",
        "p": "paragraph",
        "pre": "code",
        "td": "table_cell",
        "th": "table_cell",
    }
    IGNORED_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[_DraftBlock] = []
        self.title_parts: list[str] = []
        self.loose_parts: list[str] = []
        self.current_parts: list[str] = []
        self.current_tag: str | None = None
        self.current_type: str | None = None
        self.current_heading_level: int | None = None
        self.ignored_depth = 0
        self.in_title = False

    def _finish_loose(self) -> None:
        text = _normalized_inline_text(" ".join(self.loose_parts))
        self.loose_parts = []
        if text:
            self.blocks.append(_DraftBlock("text", text))

    def _finish_current(self) -> None:
        text = _normalized_inline_text(" ".join(self.current_parts))
        if text and self.current_type:
            self.blocks.append(
                _DraftBlock(
                    self.current_type,
                    text,
                    heading_level=self.current_heading_level,
                )
            )
        self.current_parts = []
        self.current_tag = None
        self.current_type = None
        self.current_heading_level = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        tag = tag.lower()
        if tag in self.IGNORED_TAGS:
            self.ignored_depth += 1
            return
        if self.ignored_depth:
            return
        if tag == "title":
            self.in_title = True
            return
        if tag in self.BLOCK_TAGS:
            self._finish_loose()
            self._finish_current()
            self.current_tag = tag
            self.current_type = self.BLOCK_TAGS[tag]
            if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
                self.current_heading_level = int(tag[1])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.IGNORED_TAGS:
            self.ignored_depth = max(0, self.ignored_depth - 1)
            return
        if self.ignored_depth:
            return
        if tag == "title":
            self.in_title = False
            return
        if tag == self.current_tag:
            self._finish_current()

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        if self.in_title:
            self.title_parts.append(data)
        elif self.current_tag:
            self.current_parts.append(data)
        else:
            self.loose_parts.append(data)

    def finish(self) -> tuple[list[_DraftBlock], str | None]:
        self._finish_current()
        self._finish_loose()
        title = _normalized_inline_text(" ".join(self.title_parts)) or None
        return self.blocks, title


def decode_pdf_content(raw_content: bytes) -> tuple[str, str | None]:
    """Extract text with form-feed page boundaries from a textual PDF."""
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise SourceDocumentError(
            "PDF source support is unavailable in this installation."
        ) from error

    try:
        reader = PdfReader(io.BytesIO(raw_content), strict=False)
        if reader.is_encrypted:
            raise SourceDocumentError(
                "Encrypted PDF research sources are not supported."
            )
        if len(reader.pages) > MAX_PDF_PAGES:
            raise SourceDocumentError(
                "PDF research source exceeds the V1 page limit."
            )
        pages = []
        extracted_characters = 0
        for page in reader.pages:
            text = _normalized_page_text(page.extract_text() or "")
            extracted_characters += len(text)
            if extracted_characters > MAX_PDF_EXTRACTED_CHARACTERS:
                raise SourceDocumentError(
                    "PDF research source exceeds the V1 extracted-text limit."
                )
            pages.append(text)
    except SourceDocumentError:
        raise
    except Exception as error:
        raise SourceDocumentError(
            "PDF research source text could not be extracted."
        ) from error

    if not any(pages):
        raise SourceDocumentError(
            "PDF research source has no extractable text; OCR is outside V1."
        )

    metadata = getattr(reader, "metadata", None)
    title = None
    if metadata is not None:
        title = _normalized_inline_text(str(getattr(metadata, "title", "") or ""))
    return "\n\f\n".join(pages), title or None


def _html_drafts(raw_content: bytes, charset: str) -> tuple[list[_DraftBlock], str | None]:
    try:
        decoded = raw_content.decode(charset, errors="replace")
    except LookupError:
        decoded = raw_content.decode("utf-8", errors="replace")
    parser = _StructuredHTMLExtractor()
    parser.feed(decoded)
    parser.close()
    return parser.finish()


def _text_drafts(text: str, content_type: str) -> list[_DraftBlock]:
    if content_type == "application/pdf":
        pages = []
        for index, raw_page in enumerate(text.split("\f"), start=1):
            page = _normalized_page_text(raw_page)
            if page:
                pages.append(_DraftBlock("page", page, page_number=index))
        return pages

    drafts: list[_DraftBlock] = []
    paragraphs = re.split(r"\n\s*\n", text.strip())
    for paragraph in paragraphs:
        cleaned = paragraph.strip()
        if not cleaned:
            continue
        heading = re.fullmatch(r"(#{1,6})\s+(.+)", cleaned)
        if heading:
            drafts.append(
                _DraftBlock(
                    "heading",
                    _normalized_inline_text(heading.group(2)),
                    heading_level=len(heading.group(1)),
                )
            )
            continue
        drafts.append(_DraftBlock("paragraph", cleaned))
    return drafts


def _bounded_drafts(
    drafts: list[_DraftBlock],
    character_limit: int,
) -> list[_DraftBlock]:
    remaining = max(1, character_limit)
    bounded: list[_DraftBlock] = []
    for draft in drafts:
        if remaining <= 0:
            break
        text = draft.text[:remaining].rstrip()
        if text:
            bounded.append(
                _DraftBlock(
                    draft.block_type,
                    text,
                    heading_level=draft.heading_level,
                    page_number=draft.page_number,
                )
            )
            remaining -= len(text)
    return bounded


def build_source_document(
    *,
    receipt_id: str,
    source_content_sha256: str,
    content_type: str,
    title: str | None,
    raw_content: bytes,
    decoded_text: str,
    charset: str = "utf-8",
    content_truncated: bool,
) -> SourceDocument:
    """Build and self-verify one deterministic companion document."""
    if content_type == "text/html":
        drafts, parsed_title = _html_drafts(raw_content, charset)
        title = title or parsed_title
    else:
        drafts = _text_drafts(decoded_text, content_type)

    if not drafts:
        fallback = _normalized_inline_text(decoded_text)
        if fallback:
            drafts = [_DraftBlock("text", fallback)]
    if not drafts:
        raise SourceDocumentError("Research source produced no indexable text.")

    drafts = _bounded_drafts(drafts, len(decoded_text))
    heading_path: list[str] = []
    blocks: list[dict] = []
    cursor = 0
    for index, draft in enumerate(drafts, start=1):
        if draft.block_type == "heading":
            level = draft.heading_level or 1
            heading_path = heading_path[: level - 1]
            heading_path.append(draft.text)
        block = {
            "block_id": f"B{index:06d}",
            "block_type": draft.block_type,
            "heading_path": list(heading_path),
            "page_number": draft.page_number,
            "text": draft.text,
            "text_sha256": _text_sha256(draft.text),
            "document_start": cursor,
            "document_end": cursor + len(draft.text),
        }
        blocks.append(block)
        cursor = block["document_end"] + 2

    document_text = "\n\n".join(block["text"] for block in blocks)
    payload = {
        "schema_version": "1.0",
        "receipt_id": receipt_id,
        "source_content_sha256": source_content_sha256,
        "content_type": content_type,
        "title": title,
        "normalizer_version": NORMALIZER_VERSION,
        "content_truncated": content_truncated,
        "blocks": blocks,
        "document_text_sha256": _text_sha256(document_text),
    }
    payload["source_document_sha256"] = _canonical_hash(
        payload,
        "source_document_sha256",
    )
    document = SourceDocument.model_validate(payload)
    verify_source_document(document)
    return document


def verify_source_document(document: SourceDocument) -> None:
    """Reject any structural, offset, or hash mutation."""
    dumped = document.model_dump(mode="json")
    if document.source_document_sha256 != _canonical_hash(
        dumped,
        "source_document_sha256",
    ):
        raise SourceDocumentError("Source document hash is invalid.")

    expected_ids = [f"B{index:06d}" for index in range(1, len(document.blocks) + 1)]
    if [block.block_id for block in document.blocks] != expected_ids:
        raise SourceDocumentError("Source document block identities are invalid.")

    cursor = 0
    for block in document.blocks:
        if block.text_sha256 != _text_sha256(block.text):
            raise SourceDocumentError(
                f"Source document block hash is invalid: {block.block_id}"
            )
        if block.document_start != cursor or block.document_end != cursor + len(block.text):
            raise SourceDocumentError(
                f"Source document block offsets are invalid: {block.block_id}"
            )
        cursor = block.document_end + 2

    document_text = "\n\n".join(block.text for block in document.blocks)
    if document.document_text_sha256 != _text_sha256(document_text):
        raise SourceDocumentError("Source document text hash is invalid.")


def source_document_relative_path(receipt_id: str) -> Path:
    """Return the stable local path for one receipt companion."""
    if not re.fullmatch(RECEIPT_PATTERN, receipt_id):
        raise SourceDocumentError("Source receipt identity is unsafe.")
    return Path("data") / "source_documents" / f"{receipt_id}.json"


def write_source_document(project_path: Path, document: SourceDocument) -> Path:
    """Atomically persist one immutable companion document."""
    verify_source_document(document)
    path = project_path / source_document_relative_path(document.receipt_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SourceDocumentError(
            f"Source document already exists: {document.receipt_id}"
        )
    temporary_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with open(temporary_path, "x", encoding="utf-8") as output:
            output.write(
                json.dumps(
                    document.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            )
        os.replace(temporary_path, path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise SourceDocumentError("Source document could not be saved.") from error
    return path


def clone_source_document(
    document: SourceDocument,
    receipt_id: str,
) -> SourceDocument:
    """Bind already-verified normalized content to a new receipt lineage."""
    verify_source_document(document)
    payload = document.model_dump(mode="json")
    payload["receipt_id"] = receipt_id
    payload["source_document_sha256"] = _canonical_hash(
        payload,
        "source_document_sha256",
    )
    cloned = SourceDocument.model_validate(payload)
    verify_source_document(cloned)
    return cloned


def load_source_document(project_path: Path, receipt_id: str) -> SourceDocument:
    """Load and hash-check one source-document companion."""
    path = project_path / source_document_relative_path(receipt_id)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document = SourceDocument.model_validate(payload)
    except FileNotFoundError as error:
        raise SourceDocumentError(
            f"Source document is unavailable: {receipt_id}"
        ) from error
    except (OSError, ValueError) as error:
        raise SourceDocumentError(
            f"Source document is invalid: {receipt_id}"
        ) from error
    verify_source_document(document)
    return document


def locate_exact_quote(
    document: SourceDocument,
    exact_quote: str,
) -> EvidenceLocator:
    """Resolve one unambiguous quotation to an exact structural block."""
    if not exact_quote or len(exact_quote) > 4_000:
        raise SourceDocumentError("Evidence quotation length is invalid.")
    matches: list[tuple[SourceBlock, int]] = []
    for block in document.blocks:
        start = 0
        while True:
            start = block.text.find(exact_quote, start)
            if start < 0:
                break
            matches.append((block, start))
            start += 1

    if not matches:
        raise SourceDocumentError(
            "Evidence quotation does not occur in the structured source document."
        )
    if len(matches) > 1:
        raise SourceDocumentError(
            "Evidence quotation is ambiguous in the structured source document."
        )

    block, start = matches[0]
    quote_hash = _text_sha256(exact_quote)
    identity_payload = (
        f"{document.receipt_id}\n{document.source_document_sha256}\n"
        f"{block.block_id}\n{start}\n{quote_hash}"
    )
    locator_id = "locator-" + hashlib.sha256(
        identity_payload.encode("utf-8")
    ).hexdigest()[:24]
    return EvidenceLocator(
        locator_id=locator_id,
        receipt_id=document.receipt_id,
        source_document_sha256=document.source_document_sha256,
        block_id=block.block_id,
        block_type=block.block_type,
        heading_path=list(block.heading_path),
        page_number=block.page_number,
        character_start=start,
        character_end=start + len(exact_quote),
        exact_quote=exact_quote,
        exact_quote_sha256=quote_hash,
    )


def resolve_formatting_variant_quote(
    document: SourceDocument,
    proposed_quote: str,
) -> str:
    """Return one exact stored quote after harmless formatting drift only."""
    try:
        locate_exact_quote(document, proposed_quote)
        return proposed_quote
    except SourceDocumentError as error:
        if str(error) != (
            "Evidence quotation does not occur in the structured source document."
        ):
            raise

    if not proposed_quote or len(proposed_quote) > 4_000:
        raise SourceDocumentError("Evidence quotation length is invalid.")

    pattern_parts: list[str] = []
    previous_was_space = False
    quote_equivalents = {
        "'": "['‘’]",
        "‘": "['‘’]",
        "’": "['‘’]",
        '"': '["“”]',
        "“": '["“”]',
        "”": '["“”]',
        "-": "[-–—]",
        "–": "[-–—]",
        "—": "[-–—]",
    }
    for character in proposed_quote:
        if character.isspace():
            if not previous_was_space:
                pattern_parts.append(r"\s+")
            previous_was_space = True
            continue
        previous_was_space = False
        pattern_parts.append(
            quote_equivalents.get(character, re.escape(character))
        )

    pattern = re.compile("".join(pattern_parts))
    matches: list[str] = []
    for block in document.blocks:
        matches.extend(match.group(0) for match in pattern.finditer(block.text))

    if not matches:
        raise SourceDocumentError(
            "Evidence quotation does not occur in the structured source document."
        )
    if len(matches) > 1:
        raise SourceDocumentError(
            "Evidence quotation is ambiguous in the structured source document."
        )

    exact_quote = matches[0]
    locate_exact_quote(document, exact_quote)
    return exact_quote


def validate_evidence_locator(
    document: SourceDocument,
    locator: EvidenceLocator,
) -> None:
    """Verify a supplied locator against its immutable document."""
    if (
        locator.receipt_id != document.receipt_id
        or locator.source_document_sha256 != document.source_document_sha256
    ):
        raise SourceDocumentError("Evidence locator source identity is invalid.")
    blocks = {block.block_id: block for block in document.blocks}
    block = blocks.get(locator.block_id)
    if block is None:
        raise SourceDocumentError("Evidence locator block does not exist.")
    if (
        locator.block_type != block.block_type
        or locator.heading_path != block.heading_path
        or locator.page_number != block.page_number
        or locator.character_end > len(block.text)
        or block.text[locator.character_start : locator.character_end]
        != locator.exact_quote
        or locator.exact_quote_sha256 != _text_sha256(locator.exact_quote)
    ):
        raise SourceDocumentError("Evidence locator content is invalid.")
