"""Deterministic inline-citation and contradiction grounding checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


CITATION_CONTRACT_VERSION = "1.0"
EVIDENCE_MARKER_PATTERN = re.compile(
    r"\[Evidence:\s*([^\]]+)\]",
    re.IGNORECASE,
)
CONTRADICTION_CALLOUT_PATTERN = re.compile(
    r"^\s*>\s*(?:\[!CONTRADICTION\]|\*\*Contradiction:\*\*)",
    re.IGNORECASE | re.MULTILINE,
)
SAFE_RECEIPT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class CitationGroundingError(RuntimeError):
    """Reject prose whose reader-facing evidence markers are not grounded."""


@dataclass(frozen=True)
class EvidenceMarker:
    """One parsed marker and its exact location in the manuscript."""

    receipt_ids: tuple[str, ...]
    start: int
    end: int
    raw: str


def parse_evidence_markers(markdown: str) -> list[EvidenceMarker]:
    """Parse every strict ``[Evidence: receipt-id]`` marker."""
    markers = []
    for match in EVIDENCE_MARKER_PATTERN.finditer(markdown):
        receipt_ids = tuple(
            item.strip().strip("`").strip()
            for item in re.split(r"[,;]", match.group(1))
            if item.strip()
        )
        if (
            not receipt_ids
            or len(receipt_ids) != len(set(receipt_ids))
            or any(
                SAFE_RECEIPT_ID_PATTERN.fullmatch(receipt_id) is None
                for receipt_id in receipt_ids
            )
        ):
            raise CitationGroundingError(
                f"Invalid evidence marker: {match.group(0)}"
            )
        markers.append(
            EvidenceMarker(
                receipt_ids=receipt_ids,
                start=match.start(),
                end=match.end(),
                raw=match.group(0),
            )
        )
    if markdown.lower().count("[evidence:") != len(markers):
        raise CitationGroundingError(
            "Every evidence marker must close with ] and contain receipt IDs."
        )
    return markers


def _receipt_id(record: dict[str, Any]) -> str | None:
    value = record.get("receipt_id")
    return value if isinstance(value, str) and value else None


def _claim_receipts(claim: dict[str, Any]) -> tuple[str, ...]:
    values = claim.get("source_receipt_ids")
    if values is None:
        values = claim.get("evidence_receipt_ids", [])
    if not isinstance(values, list):
        return ()
    return tuple(
        value for value in values if isinstance(value, str) and value
    )


def _validate_allowed_markers(
    markers: list[EvidenceMarker],
    allowed_receipt_ids: set[str],
) -> None:
    cited = {
        receipt_id
        for marker in markers
        for receipt_id in marker.receipt_ids
    }
    unknown = sorted(cited - allowed_receipt_ids)
    if unknown:
        raise CitationGroundingError(
            "Evidence markers reference receipts outside the approved "
            "context: " + ", ".join(unknown)
        )


def _match_claims_to_distinct_markers(
    claims: list[dict[str, Any]],
    markers: list[EvidenceMarker],
) -> None:
    sourced_claims = [claim for claim in claims if _claim_receipts(claim)]
    marker_owner: dict[int, int] = {}

    def assign(claim_index: int, visited: set[int]) -> bool:
        claim_receipts = set(_claim_receipts(sourced_claims[claim_index]))
        for marker_index, marker in enumerate(markers):
            if marker_index in visited or not (
                claim_receipts & set(marker.receipt_ids)
            ):
                continue
            visited.add(marker_index)
            current_owner = marker_owner.get(marker_index)
            if current_owner is None or assign(current_owner, visited):
                marker_owner[marker_index] = claim_index
                return True
        return False

    unmatched = []
    for claim_index, claim in enumerate(sourced_claims):
        if not assign(claim_index, set()):
            unmatched.append(str(claim.get("claim_id") or "unnamed claim"))
    if unmatched:
        raise CitationGroundingError(
            "Every evidence-backed claim needs its own inline evidence "
            "marker. Missing: " + ", ".join(unmatched)
        )


def _paragraph_bounds(markdown: str, start: int, end: int) -> tuple[int, int]:
    paragraph_start = markdown.rfind("\n\n", 0, start)
    paragraph_end = markdown.find("\n\n", end)
    return (
        0 if paragraph_start < 0 else paragraph_start + 2,
        len(markdown) if paragraph_end < 0 else paragraph_end,
    )


def _summary(
    markers: list[EvidenceMarker],
    sourced_claim_count: int,
    contradiction_count: int,
) -> dict[str, Any]:
    cited_receipts = []
    for marker in markers:
        for receipt_id in marker.receipt_ids:
            if receipt_id not in cited_receipts:
                cited_receipts.append(receipt_id)
    return {
        "version": CITATION_CONTRACT_VERSION,
        "marker_count": len(markers),
        "cited_receipt_ids": cited_receipts,
        "grounded_claim_count": sourced_claim_count,
        "contradiction_callout_count": contradiction_count,
    }


def validate_chapter_markdown(
    markdown: str,
    claims: list[dict[str, Any]],
    evidence_packets: list[dict[str, Any]],
    contradictions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate a chapter against its approved evidence and contradiction set."""
    markers = parse_evidence_markers(markdown)
    allowed_receipts = {
        receipt_id
        for packet in evidence_packets
        if isinstance(packet, dict)
        for receipt_id in [_receipt_id(packet)]
        if receipt_id is not None
    }
    _validate_allowed_markers(markers, allowed_receipts)
    _match_claims_to_distinct_markers(claims, markers)

    contradiction_count = len(CONTRADICTION_CALLOUT_PATTERN.findall(markdown))
    if contradiction_count < len(contradictions):
        raise CitationGroundingError(
            "Every approved contradiction must remain visibly marked with "
            "> [!CONTRADICTION]."
        )
    return _summary(
        markers,
        sum(1 for claim in claims if _claim_receipts(claim)),
        contradiction_count,
    )


def validate_synthesis_markdown(
    markdown: str,
    claims: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require each sourced edition claim to cite evidence in its paragraph."""
    markers = parse_evidence_markers(markdown)
    allowed_receipts = {
        receipt_id
        for claim in claims
        for receipt_id in _claim_receipts(claim)
    }
    _validate_allowed_markers(markers, allowed_receipts)
    mapping_by_id = {
        mapping.get("claim_id"): mapping
        for mapping in mappings
        if isinstance(mapping, dict)
    }
    sourced_claims = [claim for claim in claims if _claim_receipts(claim)]
    for claim in sourced_claims:
        claim_id = claim.get("claim_id")
        mapping = mapping_by_id.get(claim_id)
        excerpt = mapping.get("edition_excerpt") if mapping else None
        if not isinstance(excerpt, str) or not excerpt:
            raise CitationGroundingError(
                f"Evidence-backed claim {claim_id} has no exact edition mapping."
            )
        start = markdown.find(excerpt)
        if start < 0:
            raise CitationGroundingError(
                f"Evidence-backed claim {claim_id} is absent from the edition."
            )
        paragraph_start, paragraph_end = _paragraph_bounds(
            markdown,
            start,
            start + len(excerpt),
        )
        claim_receipts = set(_claim_receipts(claim))
        nearby = [
            marker
            for marker in markers
            if marker.start >= paragraph_start and marker.end <= paragraph_end
        ]
        if not any(claim_receipts & set(marker.receipt_ids) for marker in nearby):
            raise CitationGroundingError(
                f"Evidence-backed claim {claim_id} needs an inline marker in "
                "the same paragraph as its mapped edition excerpt."
            )
        if claim.get("resolution_status") == "contradicted":
            paragraph = markdown[paragraph_start:paragraph_end]
            if CONTRADICTION_CALLOUT_PATTERN.search(paragraph) is None:
                raise CitationGroundingError(
                    f"Contradicted claim {claim_id} must remain visibly marked."
                )
    contradiction_count = len(CONTRADICTION_CALLOUT_PATTERN.findall(markdown))
    return _summary(markers, len(sourced_claims), contradiction_count)
