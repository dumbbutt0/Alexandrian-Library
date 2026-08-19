import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CoverDesignError(RuntimeError):
    """Reject stale, unsafe, or incomplete cover artifacts."""


BOOK_ID_PATTERN = re.compile(r"^book-[A-Za-z0-9._-]+$")
HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
FALLBACK_STYLES = {
    "archive_geometry",
    "evidence_threads",
    "stained_glass_diagram",
}
GRAPHICAL_FALLBACK_PROFILE = "forbidden-archive-graphical-v2"
GRAPHICAL_PROMPT_TERMS = (
    "collage",
    "illustration",
    "illustrated",
    "painted",
    "photomontage",
    "stained glass",
    "torn paper",
    "layered material",
)
LINE_STYLE_PROMPT_TERMS = (
    "line art",
    "line-art",
    "outline-only",
    "wireframe-only",
    "thin-line",
)
TOPIC_STOP_WORDS = {
    "about", "after", "again", "against", "being", "between", "could",
    "does", "from", "have", "into", "itself", "should", "their", "there",
    "these", "thing", "those", "through", "under", "what", "when", "where",
    "which", "while", "with", "without", "would", "your",
}
RASTER_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def current_utc_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise CoverDesignError(f"Cover input could not be hashed: {path}") from error


def canonical_json_sha256(document: dict, hash_field: str) -> str:
    payload = dict(document)
    payload.pop(hash_field, None)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, label: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverDesignError(f"{label} could not be read: {path}") from error
    if not isinstance(document, dict):
        raise CoverDesignError(f"{label} must contain a JSON object.")
    return document


def _write_text_atomically(path: Path, content: str) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise CoverDesignError(f"Cover artifact could not be written: {path}") from error


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise CoverDesignError(f"Cover asset could not be written: {path}") from error


def cover_directory(project_path: Path, book_id: str) -> Path:
    if not BOOK_ID_PATTERN.fullmatch(book_id):
        raise CoverDesignError("Book ID is invalid.")
    return project_path.resolve() / "data" / "books" / book_id / "cover"


def design_path(project_path: Path, book_id: str) -> Path:
    return cover_directory(project_path, book_id) / "design.json"


def _final_book_context(project_path: Path, book_id: str) -> dict:
    book_directory = cover_directory(project_path, book_id).parent
    assembly_path = book_directory / "assembly.json"
    manifest_path = book_directory / "manifest.json"
    book_path = book_directory / "book.md"
    assembly = _read_json(assembly_path, "Final assembly")
    manifest = _read_json(manifest_path, "Book manifest")
    if assembly.get("book_id") != book_id or manifest.get("book_id") != book_id:
        raise CoverDesignError("Cover inputs do not match the requested book.")
    if not book_path.is_file():
        raise CoverDesignError("Final book is missing.")
    book_hash = file_sha256(book_path)
    if assembly.get("book_sha256") != book_hash:
        raise CoverDesignError("Final book no longer matches its assembly.")
    return {
        "book_directory": book_directory,
        "assembly_path": assembly_path,
        "assembly": assembly,
        "manifest": manifest,
        "book_path": book_path,
        "book_text": book_path.read_text(encoding="utf-8"),
        "book_sha256": book_hash,
    }


def _validated_unique_strings(
    value: Any,
    label: str,
    minimum: int,
    maximum: int,
) -> list[str]:
    if (
        not isinstance(value, list)
        or not minimum <= len(value) <= maximum
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise CoverDesignError(
            f"{label} must contain {minimum} to {maximum} non-empty strings."
        )
    normalized = [item.strip() for item in value]
    if len(normalized) != len(set(normalized)):
        raise CoverDesignError(f"{label} contains duplicate values.")
    return normalized


def _recent_cover_designs(
    project_path: Path,
    book_id: str,
    *,
    limit: int = 4,
) -> list[dict]:
    designs = []
    books_path = project_path.resolve() / "data" / "books"
    for prior_path in books_path.glob("*/cover/design.json"):
        if prior_path.parent.parent.name == book_id:
            continue
        try:
            prior = _read_json(prior_path, "Prior cover design")
        except CoverDesignError:
            continue
        designs.append(
            (
                str(prior.get("created_at", "")),
                str(prior_path),
                prior,
            )
        )
    designs.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in designs[-limit:]]


def _quote_location(book_text: str, quote: str) -> dict:
    start = book_text.find(quote)
    if start < 0:
        raise CoverDesignError("Cover quotation is not exact final-book text.")
    heading = None
    for line in book_text[:start].splitlines():
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
    return {
        "character_start": start,
        "character_end": start + len(quote),
        "section_heading": heading,
    }


def _graphical_prompt(image_prompt: str) -> None:
    normalized = image_prompt.casefold()
    for term in LINE_STYLE_PROMPT_TERMS:
        start = 0
        while (index := normalized.find(term, start)) >= 0:
            prefix = normalized[max(0, index - 36):index]
            negated = re.search(
                r"(?:\bnot|\bno|\bnever|\bavoid|\bwithout)"
                r"(?:\s+\w+){0,3}\s+$",
                prefix,
            )
            if negated is None:
                raise CoverDesignError(
                    "Cover image prompt cannot request an outline-only line "
                    "treatment."
                )
            start = index + len(term)
    if not any(term in normalized for term in GRAPHICAL_PROMPT_TERMS):
        raise CoverDesignError(
            "Cover image prompt must request a graphical collage or illustrated "
            "material treatment."
        )


def _topic_terms(text: str) -> list[str]:
    """Return stable content words that bind visual direction to one book."""
    return sorted(
        {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{3,}", text)
            if token.casefold() not in TOPIC_STOP_WORDS
        }
    )


def _validate_topic_specificity(question: str, image_prompt: str) -> list[str]:
    topic_terms = _topic_terms(question)
    if not topic_terms:
        return []
    matched = sorted(set(topic_terms) & set(_topic_terms(image_prompt)))
    if not matched:
        raise CoverDesignError(
            "Cover image prompt must name at least one topic-specific term from "
            "the final book's core question."
        )
    return matched


def _collage_fragments(palette: list[str], digest: bytes) -> str:
    fragments = []
    for index in range(9):
        x = 48 + (digest[index] * 3) % 780
        y = 170 + (digest[index + 9] * 5) % 1070
        width = 130 + digest[(index + 18) % len(digest)] % 260
        height = 100 + digest[(index + 23) % len(digest)] % 330
        notch = 12 + digest[(index + 5) % len(digest)] % 32
        color = palette[(index + 2) % len(palette)]
        fragments.append(
            f'<path d="M{x + notch} {y}H{x + width}L{x + width - notch} '
            f'{y + height}H{x}L{x + notch // 2} {y + height // 2}Z" '
            f'fill="{color}" fill-opacity="{0.12 + (index % 4) * 0.055:.3f}" '
            f'stroke="{palette[1 % len(palette)]}" stroke-opacity=".22" '
            'stroke-width="3"/>'
        )
    return "".join(fragments)


def _archive_geometry_centerpiece(palette: list[str]) -> str:
    return (
        '<g filter="url(#shadow)">'
        '<path d="M244 1130V535Q244 390 390 338H634Q780 390 780 535V1130Z" '
        'fill="url(#vault)" stroke="url(#metal)" stroke-width="18"/>'
        '<path d="M322 1080V590Q322 468 430 426H594Q702 468 702 590V1080Z" '
        f'fill="{palette[0]}" fill-opacity=".82" stroke="{palette[2 % len(palette)]}" '
        'stroke-opacity=".68" stroke-width="10"/>'
        '<circle cx="512" cy="642" r="178" fill="url(#halo)" '
        f'stroke="{palette[1 % len(palette)]}" stroke-width="24"/>'
        '<circle cx="512" cy="642" r="108" fill="url(#glass)" '
        f'stroke="{palette[3 % len(palette)]}" stroke-width="8"/>'
        '<path d="M512 392V892M262 642H762M335 465L689 819M689 465L335 819" '
        f'stroke="{palette[1 % len(palette)]}" stroke-opacity=".78" '
        'stroke-width="14"/>'
        '<path d="M370 1082L420 850H604L654 1082Z" fill="url(#paper)" '
        f'stroke="{palette[1 % len(palette)]}" stroke-width="10"/>'
        '<path d="M420 850L512 914L604 850V1036L512 990L420 1036Z" '
        f'fill="{palette[2 % len(palette)]}" fill-opacity=".72"/>'
        '</g>'
    )


def _evidence_threads_centerpiece(palette: list[str]) -> str:
    thread_colors = [
        palette[index % len(palette)] for index in (2, 4, 1, 3, 5)
    ]
    threads = "".join(
        f'<path d="M-80 {490 + index * 34}C210 {410 + index * 58},330 '
        f'{650 + index * 18},512 738C694 {825 - index * 12},820 '
        f'{620 + index * 42},1104 {560 + index * 30}" stroke="{color}" '
        f'stroke-width="{18 + index * 3}" stroke-opacity=".78"/>'
        for index, color in enumerate(thread_colors)
    )
    return (
        '<g fill="none" stroke-linecap="round">'
        + threads
        + '</g><g filter="url(#shadow)">'
        '<rect x="206" y="270" width="612" height="916" rx="42" '
        'fill="url(#vault)" stroke="url(#metal)" stroke-width="18"/>'
        '<path d="M326 360H698L746 590L512 790L278 590Z" fill="url(#paper)" '
        f'stroke="{palette[1 % len(palette)]}" stroke-width="12"/>'
        '<path d="M512 790L654 982H370Z" fill="url(#glass)" '
        f'stroke="{palette[1 % len(palette)]}" stroke-width="14"/>'
        '<circle cx="512" cy="790" r="54" fill="url(#halo)" '
        f'stroke="{palette[3 % len(palette)]}" stroke-width="10"/>'
        '<path d="M266 1008H758V1112H266Z" fill="url(#paper)" '
        f'stroke="{palette[1 % len(palette)]}" stroke-width="8"/>'
        '<path d="M512 1008V1112M348 1008V1112M676 1008V1112" '
        f'stroke="{palette[2 % len(palette)]}" stroke-width="12"/>'
        '</g>'
    )


def _stained_glass_centerpiece(palette: list[str]) -> str:
    cells = []
    for index in range(12):
        angle = index * 30
        cells.append(
            f'<path d="M512 596L512 246A350 350 0 0 1 687 293Z" '
            f'transform="rotate({angle} 512 596)" '
            f'fill="{palette[(index + 2) % len(palette)]}" fill-opacity=".58" '
            f'stroke="{palette[1 % len(palette)]}" stroke-width="9"/>'
        )
    return (
        '<g filter="url(#shadow)"><circle cx="512" cy="596" r="382" '
        'fill="url(#vault)" stroke="url(#metal)" stroke-width="24"/>'
        + "".join(cells)
        + '<circle cx="512" cy="596" r="126" fill="url(#halo)" '
        f'stroke="{palette[3 % len(palette)]}" stroke-width="16"/>'
        '<path d="M372 1330L394 850Q402 744 512 720Q622 744 630 850L652 1330Z" '
        f'fill="{palette[0]}" stroke="{palette[1 % len(palette)]}" '
        'stroke-width="14"/>'
        '<path d="M402 874Q512 734 622 874L574 930H450Z" fill="url(#vault)"/>'
        '<path d="M420 1010L512 964L604 1010V1160L512 1110L420 1160Z" '
        'fill="url(#paper)" stroke="url(#metal)" stroke-width="12"/>'
        '<path d="M512 964V1110" '
        f'stroke="{palette[2 % len(palette)]}" stroke-width="10"/>'
        '</g>'
    )


def _fallback_svg(design: dict) -> str:
    palette = design["palette"]
    digest = hashlib.sha256(
        (design["book_id"] + "\n" + "\n".join(design["visual_motifs"])).encode(
            "utf-8"
        )
    ).digest()
    accent = palette[2 % len(palette)]
    secondary = palette[3 % len(palette)]
    centerpiece = {
        "archive_geometry": _archive_geometry_centerpiece,
        "evidence_threads": _evidence_threads_centerpiece,
        "stained_glass_diagram": _stained_glass_centerpiece,
    }[design["fallback_style"]](palette)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1536" '
        'data-render-profile="'
        + GRAPHICAL_FALLBACK_PROFILE
        + '"><defs><radialGradient id="halo"><stop stop-color="'
        + accent
        + '" stop-opacity=".92"/><stop offset="1" stop-color="'
        + palette[0]
        + '" stop-opacity=".08"/></radialGradient>'
        '<linearGradient id="vault" x2="1" y2="1"><stop stop-color="'
        + palette[0]
        + '"/><stop offset=".52" stop-color="'
        + accent
        + '" stop-opacity=".5"/><stop offset="1" stop-color="#03040a"/></linearGradient>'
        '<linearGradient id="metal"><stop stop-color="'
        + palette[1 % len(palette)]
        + '"/><stop offset=".5" stop-color="'
        + secondary
        + '"/><stop offset="1" stop-color="'
        + palette[1 % len(palette)]
        + '"/></linearGradient>'
        '<linearGradient id="paper" x2=".7" y2="1"><stop stop-color="'
        + secondary
        + '"/><stop offset="1" stop-color="'
        + palette[1 % len(palette)]
        + '" stop-opacity=".62"/></linearGradient>'
        '<radialGradient id="glass"><stop stop-color="'
        + secondary
        + '" stop-opacity=".9"/><stop offset="1" stop-color="'
        + accent
        + '" stop-opacity=".34"/></radialGradient>'
        '<filter id="grain"><feTurbulence baseFrequency=".55" numOctaves="4" '
        f'seed="{7 + digest[0] % 19}" result="n"/><feBlend in="SourceGraphic" '
        'in2="n" mode="soft-light"/></filter>'
        '<filter id="shadow"><feDropShadow dx="0" dy="18" stdDeviation="24" '
        'flood-color="#000" flood-opacity=".78"/></filter>'
        '<pattern id="halftone" width="18" height="18" patternUnits="userSpaceOnUse">'
        '<circle cx="3" cy="3" r="2.4" fill="'
        + secondary
        + '" fill-opacity=".16"/></pattern></defs>'
        '<rect width="1024" height="1536" fill="#03040a"/>'
        '<rect width="1024" height="1536" fill="url(#vault)"/>'
        '<g filter="url(#grain)">'
        + _collage_fragments(palette, digest)
        + centerpiece
        + '</g><rect width="1024" height="1536" fill="url(#halftone)" opacity=".55"/>'
        '<path d="M54 116Q512 54 970 116V1420Q512 1482 54 1420Z" fill="none" '
        'stroke="'
        + palette[1 % len(palette)]
        + '" stroke-width="12" opacity=".78"/>'
        '<rect x="38" y="38" width="948" height="1460" rx="22" fill="none" stroke="'
        + accent
        + '" stroke-width="4" opacity=".48"/></svg>\n'
    )


def submit_design(
    project_path: Path,
    proposal: dict,
    designer_host: str,
) -> dict:
    book_id = proposal.get("book_id")
    if not isinstance(book_id, str):
        raise CoverDesignError("Cover design has no valid book ID.")
    context = _final_book_context(project_path, book_id)
    canonical_path = design_path(project_path, book_id)
    if canonical_path.exists():
        raise CoverDesignError("A canonical cover design already exists.")
    assembly_hash = file_sha256(context["assembly_path"])
    if proposal.get("assembly_sha256") != assembly_hash:
        raise CoverDesignError("Cover design targets a stale assembly.")
    core_question = proposal.get("core_question")
    manifest_question = str(
        context["manifest"].get("central_question") or "its central inquiry"
    ).strip()
    if not isinstance(core_question, str) or core_question.strip() != manifest_question:
        raise CoverDesignError(
            "Cover core question must exactly match the final manifest."
        )
    question_tension = proposal.get("question_tension")
    if (
        not isinstance(question_tension, str)
        or not 40 <= len(question_tension.strip()) <= 1200
    ):
        raise CoverDesignError(
            "Cover question tension must contain 40 to 1200 characters."
        )
    dominant_symbol = proposal.get("dominant_symbol")
    if (
        not isinstance(dominant_symbol, str)
        or not 3 <= len(dominant_symbol.strip()) <= 160
    ):
        raise CoverDesignError(
            "Cover dominant symbol must contain 3 to 160 characters."
        )
    dominant_symbol = dominant_symbol.strip()
    diversity_statement = proposal.get("diversity_statement")
    if (
        not isinstance(diversity_statement, str)
        or not 40 <= len(diversity_statement.strip()) <= 1200
    ):
        raise CoverDesignError(
            "Cover diversity statement must contain 40 to 1200 characters."
        )
    quote = proposal.get("quote")
    if not isinstance(quote, str) or not 20 <= len(quote.strip()) <= 180:
        raise CoverDesignError("Cover quotation must contain 20 to 180 characters.")
    quote = quote.strip()
    quote_location = _quote_location(context["book_text"], quote)
    concept = proposal.get("concept")
    if not isinstance(concept, str) or not 40 <= len(concept.strip()) <= 1200:
        raise CoverDesignError("Cover concept must contain 40 to 1200 characters.")
    motifs = _validated_unique_strings(
        proposal.get("visual_motifs"), "Visual motifs", 3, 8
    )
    if dominant_symbol not in motifs:
        raise CoverDesignError("Cover dominant symbol must be one visual motif.")
    palette = _validated_unique_strings(proposal.get("palette"), "Palette", 4, 8)
    if any(not HEX_COLOR_PATTERN.fullmatch(color) for color in palette):
        raise CoverDesignError("Palette colors must use six-digit hexadecimal values.")
    image_prompt = proposal.get("image_prompt")
    if not isinstance(image_prompt, str) or not 200 <= len(image_prompt.strip()) <= 5000:
        raise CoverDesignError("Cover image prompt must contain 200 to 5000 characters.")
    _graphical_prompt(image_prompt)
    matched_topic_terms = _validate_topic_specificity(
        manifest_question,
        image_prompt,
    )
    fallback_style = proposal.get("fallback_style")
    if fallback_style not in FALLBACK_STYLES:
        raise CoverDesignError("Cover fallback style is invalid.")
    recent_designs = _recent_cover_designs(project_path, book_id)
    recent_symbols = {
        str(
            design.get("dominant_symbol")
            or next(iter(design.get("visual_motifs") or []), "")
        ).strip().casefold()
        for design in recent_designs
    }
    if dominant_symbol.casefold() in recent_symbols:
        raise CoverDesignError(
            "Cover dominant symbol repeats one of the four most recent covers."
        )

    directory = canonical_path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fallback_path = directory / "fallback.svg"
    document = {
        "schema_version": "1.0",
        "book_id": book_id,
        "created_at": current_utc_time(),
        "designer_host": designer_host,
        "assembly_path": str(context["assembly_path"].relative_to(project_path)),
        "assembly_sha256": assembly_hash,
        "book_sha256": context["book_sha256"],
        "core_question": manifest_question,
        "topic_terms": _topic_terms(manifest_question),
        "prompt_topic_terms": matched_topic_terms,
        "question_tension": question_tension.strip(),
        "dominant_symbol": dominant_symbol,
        "diversity_statement": diversity_statement.strip(),
        "quote": quote,
        "quote_location": quote_location,
        "concept": concept.strip(),
        "visual_motifs": motifs,
        "palette": palette,
        "image_prompt": image_prompt.strip(),
        "fallback_style": fallback_style,
        "asset": {
            "status": "fallback",
            "kind": "deterministic_svg",
            "render_profile": GRAPHICAL_FALLBACK_PROFILE,
            "path": str(fallback_path.relative_to(project_path)),
            "mime_type": "image/svg+xml",
        },
    }
    _write_text_atomically(fallback_path, _fallback_svg(document))
    document["asset"]["sha256"] = file_sha256(fallback_path)
    document["design_sha256"] = canonical_json_sha256(document, "design_sha256")
    _write_text_atomically(
        canonical_path,
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
    )
    return document


def create_default_design(
    project_path: Path,
    book_id: str,
    *,
    designer_host: str = "deterministic_fallback",
) -> dict:
    """Create a bounded cover when the provider cannot submit a brief."""
    context = _final_book_context(project_path, book_id)
    paragraphs = [
        block.strip()
        for block in re.split(r"\n\s*\n", context["book_text"])
        if block.strip() and not block.lstrip().startswith("#")
    ]
    quote = None
    for paragraph in paragraphs:
        cleaned = paragraph.removeprefix("> ").strip()
        if "[Evidence:" in cleaned or cleaned.startswith("|"):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
            candidate = sentence.strip()
            if 40 <= len(candidate) <= 180 and "\n" not in candidate:
                quote = candidate
                break
        if quote is not None:
            break
    if quote is None:
        for line in context["book_text"].splitlines():
            candidate = line.lstrip("#>- ").strip()
            if 20 <= len(candidate) <= 180 and candidate in context["book_text"]:
                quote = candidate
                break
    if quote is None:
        raise CoverDesignError("No bounded exact quotation is available for fallback cover design.")
    assembly_hash = file_sha256(context["assembly_path"])
    title = str(context["manifest"].get("working_title", "this book"))
    question = str(
        context["manifest"].get("central_question") or "its central inquiry"
    )
    recent_symbols = {
        str(
            design.get("dominant_symbol")
            or next(iter(design.get("visual_motifs") or []), "")
        ).strip().casefold()
        for design in _recent_cover_designs(project_path, book_id)
    }
    dominant_symbol = next(
        symbol
        for symbol in (
            "open central aperture",
            "split evidence prism",
            "unresolved threshold",
        )
        if symbol.casefold() not in recent_symbols
    )
    return submit_design(
        project_path,
        {
            "book_id": book_id,
            "assembly_sha256": assembly_hash,
            "core_question": question,
            "question_tension": (
                "The cover must keep the question open by showing evidence paths "
                "meeting a visible boundary without collapsing them into one answer."
            ),
            "dominant_symbol": dominant_symbol,
            "diversity_statement": (
                "The fallback derives its aperture from this book's question and "
                "uses intersecting evidence paths instead of repeating a recent "
                "cover's first-listed object or composition."
            ),
            "quote": quote,
            "concept": (
                f"A restrained archive diagram turns the central tension of {title} "
                "into intersecting evidence paths that remain visibly distinct instead "
                f"of implying a conclusion beyond this question: {question}"
            )[:1200],
            "visual_motifs": [
                "intersecting evidence paths",
                "archival frame",
                dominant_symbol,
                "provenance constellations",
            ],
            "palette": ["#070A12", "#D4B06A", "#2F78C4", "#E8DDC4", "#8F3340"],
            "image_prompt": (
                "Create original title-free portrait cover artwork for a finalized "
                "Alexandrian Library book as a dense graphical editorial collage, "
                f"not line art. The core question is: {question} Express this "
                "book's central intellectual tension "
                "through one large filled symbolic object, layered torn-paper fields, "
                "archival geometry, stained-glass color masses, and materially rich "
                "provenance threads. Use carbon black, brass gold, cobalt, parchment, "
                "and restrained crimson with strong value grouping at thumbnail size. "
                "Keep quiet regions for live HTML title, verified quotation, and "
                "edition. Do not include words, logos, watermarks, copied artwork, "
                "the Archive Sentinel eye, or hands. Treat the visual metaphor as "
                "presentation, not evidence."
            ),
            "fallback_style": "archive_geometry",
        },
        designer_host,
    )


def load_and_verify(project_path: Path, book_id: str) -> dict:
    document = _read_json(design_path(project_path, book_id), "Cover design")
    context = _final_book_context(project_path, book_id)
    if (
        document.get("schema_version") != "1.0"
        or document.get("book_id") != book_id
        or document.get("assembly_sha256") != file_sha256(context["assembly_path"])
        or document.get("book_sha256") != context["book_sha256"]
        or document.get("design_sha256")
        != canonical_json_sha256(document, "design_sha256")
    ):
        raise CoverDesignError("Cover design provenance is invalid.")
    quote = document.get("quote")
    if not isinstance(quote, str):
        raise CoverDesignError("Cover design quotation is invalid.")
    _quote_location(context["book_text"], quote)
    asset = document.get("asset")
    if not isinstance(asset, dict):
        raise CoverDesignError("Cover design has no asset record.")
    asset_path = (project_path / str(asset.get("path", ""))).resolve()
    try:
        asset_path.relative_to(cover_directory(project_path, book_id).resolve())
    except ValueError as error:
        raise CoverDesignError("Cover asset is outside its book.") from error
    if (
        not asset_path.is_file()
        or asset.get("sha256") != file_sha256(asset_path)
        or asset.get("mime_type")
        not in {"image/svg+xml", *RASTER_MIME_TYPES.values()}
    ):
        raise CoverDesignError("Cover asset is missing or changed.")
    return document


def bind_generated_asset(
    project_path: Path,
    book_id: str,
    source_asset: Path,
    *,
    generated_by: str,
    prompt_path: Path | None = None,
) -> dict:
    document = load_and_verify(project_path, book_id)
    suffix = source_asset.suffix.lower()
    mime_type = RASTER_MIME_TYPES.get(suffix)
    if mime_type is None or not source_asset.is_file():
        raise CoverDesignError("Generated cover asset type is unsupported.")
    destination = cover_directory(project_path, book_id) / f"generated{suffix}"
    try:
        content = source_asset.read_bytes()
    except OSError as error:
        raise CoverDesignError("Generated cover asset could not be read.") from error
    _write_bytes_atomically(destination, content)
    asset = {
        "status": "generated",
        "kind": "generated_raster",
        "path": str(destination.relative_to(project_path)),
        "mime_type": mime_type,
        "sha256": file_sha256(destination),
        "generated_by": generated_by,
    }
    if prompt_path is not None:
        prompt_destination = cover_directory(project_path, book_id) / "image-prompt.txt"
        try:
            shutil.copyfile(prompt_path, prompt_destination)
        except OSError as error:
            raise CoverDesignError("Cover prompt could not be preserved.") from error
        asset["prompt_path"] = str(prompt_destination.relative_to(project_path))
        asset["prompt_sha256"] = file_sha256(prompt_destination)
    document["asset"] = asset
    document["updated_at"] = current_utc_time()
    document["design_sha256"] = canonical_json_sha256(document, "design_sha256")
    _write_text_atomically(
        design_path(project_path, book_id),
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
    )
    return document


def refresh_fallback_asset(project_path: Path, book_id: str) -> dict:
    """Upgrade one verified SVG fallback without changing its cover brief."""
    document = load_and_verify(project_path, book_id)
    asset = document["asset"]
    if (
        asset.get("status") != "fallback"
        or asset.get("kind") != "deterministic_svg"
        or asset.get("mime_type") != "image/svg+xml"
    ):
        raise CoverDesignError(
            "Only a verified deterministic fallback cover can be refreshed."
        )
    fallback_path = cover_directory(project_path, book_id) / "fallback.svg"
    if (project_path / asset["path"]).resolve() != fallback_path.resolve():
        raise CoverDesignError("Fallback cover path is not canonical.")
    _write_text_atomically(fallback_path, _fallback_svg(document))
    asset["sha256"] = file_sha256(fallback_path)
    asset["render_profile"] = GRAPHICAL_FALLBACK_PROFILE
    document["updated_at"] = current_utc_time()
    document["design_sha256"] = canonical_json_sha256(
        document,
        "design_sha256",
    )
    _write_text_atomically(
        design_path(project_path, book_id),
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
    )
    return document
