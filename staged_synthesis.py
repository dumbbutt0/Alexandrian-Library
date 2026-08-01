"""Finite, resumable, chapter-sized whole-book synthesis.

This module keeps the existing terminal synthesis receipt contract while
replacing the oversized provider transaction that used to create the
inventory, plan, complete edition, and claim mapping in one call.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import uuid
from pathlib import Path

import book_synthesis


PIPELINE_VERSION = "staged-v1"
AUDITED_PIPELINE_VERSION = "staged-v2-plan-audit"
SHARDED_PIPELINE_VERSION = "staged-v3-chapter-plan"
SUPPORTED_PIPELINE_VERSIONS = {
    PIPELINE_VERSION,
    AUDITED_PIPELINE_VERSION,
    SHARDED_PIPELINE_VERSION,
}
MAX_UNIT_CHARACTERS = 60_000
INVENTORY_TOOL_CONTRACT_VERSION = "inventory-authority-slots-v2"


class PlanShardCorrectionError(book_synthesis.BookSynthesisError):
    """A deterministic proposal defect that permits one corrected submit."""


class PlanShardArtifactConflictError(book_synthesis.BookSynthesisError):
    """A non-correctable conflict with an already durable shard."""


class InventoryCorrectionError(book_synthesis.BookSynthesisError):
    """A deterministic inventory defect that permits one corrected submit."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "invalid_inventory_contribution",
        field_path: str = "proposal",
        claim_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.field_path = field_path
        self.claim_id = claim_id

    def safe_diagnostic(self) -> dict:
        """Return bounded correction evidence without rejected field values."""
        return {
            "code": self.code,
            "claim_id": self.claim_id,
            "field_path": self.field_path,
            "correctable": True,
        }


def staging_directory(project_path: Path, book_id: str) -> Path:
    active_root = book_synthesis.synthesis_directory(project_path, book_id)
    return active_root.with_name(
        f"{active_root.name}_staging"
    )


def staged_directory(project_path: Path, book_id: str) -> Path:
    return book_synthesis.synthesis_directory(project_path, book_id) / "staged"


def provider_stage_hold_path(
    project_path: Path,
    book_id: str,
    stage: str,
) -> Path:
    """Return the immutable hold path for one exhausted provider stage."""
    if not re.fullmatch(
        r"inventory-CH[1-9][0-9]*|"
        r"global-plan-audit-A1|"
        r"(?:edition|audit)-S[12]-[A-Za-z0-9._-]+|global-audit-S[12]",
        stage,
    ):
        raise book_synthesis.BookSynthesisError(
            "Provider-stage hold identity is invalid."
        )
    root = (
        staging_directory(project_path, book_id)
        if stage.startswith(("inventory-", "global-plan-audit-"))
        else staged_directory(project_path, book_id)
    )
    return (
        root
        / "provider_holds"
        / f"{stage}.json"
    )


def _provider_hold_status(stage: str) -> str:
    if stage.startswith("inventory-"):
        return "inventory_provider_held"
    if stage.startswith("edition-"):
        return "edition_provider_held"
    if stage.startswith("audit-"):
        return "audit_provider_held"
    if stage.startswith("global-audit-"):
        return "global_audit_provider_held"
    if stage.startswith("global-plan-audit-"):
        return "plan_audit_provider_held"
    raise book_synthesis.BookSynthesisError(
        "Provider-stage hold has an unsupported stage."
    )


def save_provider_stage_hold(
    project_path: Path,
    book_id: str,
    cycle_id: str,
    stage: str,
    target_path: Path,
    input_hash: str,
    maximum_provider_calls: int,
    consumed_backends: list[str],
) -> dict:
    """Save a terminal hold after one stage exhausts its provider calls."""
    project_path = project_path.resolve()
    target_path = target_path.resolve()
    if (
        maximum_provider_calls != 2
        or len(consumed_backends) != maximum_provider_calls
        or any(
            backend not in {"codex", "claude"}
            for backend in consumed_backends
        )
        or not isinstance(input_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", input_hash)
        or target_path.exists()
    ):
        raise book_synthesis.BookSynthesisError(
            "Provider-stage hold foundation is invalid."
        )
    active_cycle = book_synthesis.active_synthesis_cycle(
        project_path,
        book_id,
    )
    if (
        (active_cycle is None and cycle_id != "cycle-001")
        or (
            active_cycle is not None
            and active_cycle.get("cycle_id") != cycle_id
        )
    ):
        raise book_synthesis.BookSynthesisError(
            "Provider-stage hold targets an inactive cycle."
        )
    try:
        relative_target = str(target_path.relative_to(project_path))
    except ValueError as error:
        raise book_synthesis.BookSynthesisError(
            "Provider-stage hold target escapes the project."
        ) from error

    controller_path = (
        project_path
        / "data"
        / "books"
        / book_id
        / "runs"
        / "synthesis.controller.jsonl"
    )
    try:
        events = [
            json.loads(line)
            for line in controller_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise book_synthesis.BookSynthesisError(
            "Provider-stage controller is unreadable."
        ) from error
    stage_events = [
        event
        for event in events
        if event.get("stage") == stage
        and event.get("cycle_id") == cycle_id
        and event.get("event") != "provider_hold_created"
    ]
    failed = [
        event
        for event in stage_events
        if event.get("event") == "contract_failed"
    ]
    if (
        len(failed) < maximum_provider_calls
        or [event.get("backend") for event in failed[-2:]]
        != consumed_backends
    ):
        raise book_synthesis.BookSynthesisError(
            "Provider-stage hold does not match consumed calls."
        )
    attempts = []
    for event in failed[-2:]:
        correction_reservation_granted = False
        correction_validation_completed = False
        budget_path = event.get("tool_budget_path")
        if isinstance(budget_path, str):
            summary_path = book_synthesis.resolve_project_path(
                project_path,
                budget_path,
                "Synthesis tool-budget summary",
            )
            summary = book_synthesis.read_json_object(
                summary_path,
                "Synthesis tool-budget summary",
            )
            if (
                summary.get("budget_sha256")
                != event.get("tool_budget_sha256")
                or summary.get("budget_sha256")
                != book_synthesis.canonical_json_sha256(
                    summary,
                    "budget_sha256",
                )
            ):
                raise book_synthesis.BookSynthesisError(
                    "Provider-stage tool budget is stale."
                )
            correction_reservation_granted = any(
                receipt.get("record_type") == "reservation"
                and receipt.get("attempt_number") == 2
                for receipt in summary.get("attempt_receipts", [])
                if isinstance(receipt, dict)
            )
            correction_validation_completed = any(
                receipt.get("record_type") == "result"
                and receipt.get("attempt_number") == 2
                for receipt in summary.get("attempt_receipts", [])
                if isinstance(receipt, dict)
            )
        attempts.append(
            {
                "action_id": event.get("action_id"),
                "backend": event.get("backend"),
                "failure_classification": event.get(
                    "failure_classification"
                ),
                "diagnostic_sha256": event.get("diagnostic_sha256"),
                "correction_proposed_by_provider": (
                    True if correction_reservation_granted else None
                ),
                "correction_reservation_granted": (
                    correction_reservation_granted
                ),
                "correction_validation_completed": (
                    correction_validation_completed
                ),
            }
        )
    if any(
        not isinstance(attempt["action_id"], str)
        or not isinstance(attempt["failure_classification"], str)
        or not isinstance(attempt["diagnostic_sha256"], str)
        for attempt in attempts
    ):
        raise book_synthesis.BookSynthesisError(
            "Provider-stage failure evidence is incomplete."
        )
    stage_event_sha256 = hashlib.sha256(
        json.dumps(
            stage_events,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    hold = {
        "schema_version": "1.1",
        "book_id": book_id,
        "cycle_id": cycle_id,
        "stage": stage,
        "status": _provider_hold_status(stage),
        "target_path": relative_target,
        "target_created": False,
        "accepted_artifact": False,
        "input_hash": input_hash,
        "maximum_provider_calls": maximum_provider_calls,
        "same_provider_retry_allowed": True,
        "consumed_backends": consumed_backends,
        "provider_attempts": attempts,
        "required_recovery_authority_type": (
            "human_synthesis_recovery_authorization"
        ),
        "controller_path": str(controller_path.relative_to(project_path)),
        "stage_event_sha256": stage_event_sha256,
        "recorded_at": book_synthesis.current_utc_time(),
    }
    hold["hold_sha256"] = book_synthesis.canonical_json_sha256(
        hold,
        "hold_sha256",
    )
    path = provider_stage_hold_path(
        project_path,
        book_id,
        stage,
    )
    if path.is_file():
        existing = book_synthesis.read_json_object(
            path,
            "Provider-stage hold",
        )
        if existing.get("hold_sha256") != (
            book_synthesis.canonical_json_sha256(
                existing,
                "hold_sha256",
            )
        ):
            raise book_synthesis.BookSynthesisError(
                "Provider-stage hold is stale."
            )
        return existing
    book_synthesis.atomic_write_json(path, hold)
    return hold


def load_provider_stage_hold(path: Path) -> dict:
    """Load and verify one provider-stage hold."""
    hold = book_synthesis.read_json_object(path, "Provider-stage hold")
    schema_version = hold.get("schema_version")

    def valid_attempt(attempt: object) -> bool:
        if not isinstance(attempt, dict):
            return False
        if schema_version == "1.0":
            return isinstance(
                attempt.get("correction_submission_used"),
                bool,
            )
        if schema_version == "1.1":
            return (
                attempt.get("correction_proposed_by_provider")
                in {True, None}
                and isinstance(
                    attempt.get("correction_reservation_granted"),
                    bool,
                )
                and isinstance(
                    attempt.get("correction_validation_completed"),
                    bool,
                )
                and (
                    not attempt["correction_validation_completed"]
                    or attempt["correction_reservation_granted"]
                )
            )
        return False

    if (
        hold.get("status") != _provider_hold_status(hold.get("stage", ""))
        or hold.get("target_created") is not False
        or hold.get("accepted_artifact") is not False
        or hold.get("required_recovery_authority_type")
        != "human_synthesis_recovery_authorization"
        or not isinstance(hold.get("provider_attempts"), list)
        or any(
            not valid_attempt(attempt)
            for attempt in hold["provider_attempts"]
        )
        or hold.get("hold_sha256")
        != book_synthesis.canonical_json_sha256(hold, "hold_sha256")
    ):
        raise book_synthesis.BookSynthesisError(
            "Provider-stage hold is invalid."
        )
    return hold


def _write_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _copy_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _same_json(path: Path, document: dict) -> bool:
    if not path.is_file():
        return False
    try:
        return book_synthesis.read_json_object(path, path.name) == document
    except book_synthesis.BookSynthesisError:
        return False


def _required_chapters(manifest: dict) -> list[dict]:
    chapters = [
        chapter
        for chapter in manifest.get("chapter_plan", [])
        if isinstance(chapter, dict)
        and chapter.get("material_disposition") in {"include", "appendix"}
    ]
    if not chapters or any(
        not isinstance(chapter.get("chapter_id"), str)
        for chapter in chapters
    ):
        raise book_synthesis.BookSynthesisError(
            "Staged synthesis requires ordered included chapters."
        )
    return chapters


def _chapter_draft_record(candidate: dict, chapter_id: str) -> dict:
    matches = [
        record
        for record in candidate.get("inputs", [])
        if isinstance(record, dict)
        and record.get("role") == "chapter_draft"
        and record.get("chapter_id") == chapter_id
    ]
    if len(matches) != 1:
        raise book_synthesis.BookSynthesisError(
            f"{chapter_id} must have exactly one candidate draft."
        )
    return matches[0]


def _load_index(directory: Path) -> dict:
    index = book_synthesis.read_json_object(
        directory / "index.json",
        "Staged synthesis index",
    )
    if (
        index.get("pipeline_version") not in SUPPORTED_PIPELINE_VERSIONS
        or index.get("index_sha256")
        != book_synthesis.canonical_json_sha256(index, "index_sha256")
    ):
        raise book_synthesis.BookSynthesisError(
            "Staged synthesis index hash does not match."
        )
    return index


def _load_staging(project_path: Path, book_id: str) -> tuple[dict, dict]:
    directory = staging_directory(project_path, book_id)
    index = _load_index(directory)
    context = book_synthesis.read_json_object(
        directory / "context.json",
        "Staged synthesis context",
    )
    if (
        context.get("book_id") != book_id
        or context.get("context_sha256")
        != book_synthesis.canonical_json_sha256(context, "context_sha256")
        or index.get("context_sha256") != context["context_sha256"]
    ):
        raise book_synthesis.BookSynthesisError(
            "Staged synthesis context hash does not match."
        )
    return context, index


def _load_promoted(
    project_path: Path,
    book_id: str,
) -> tuple[dict, dict, dict, dict]:
    context, inventory, plan = book_synthesis.load_initialized_synthesis(
        project_path,
        book_id,
    )
    directory = staged_directory(project_path, book_id)
    index = _load_index(directory)
    if (
        context.get("pipeline_version") not in SUPPORTED_PIPELINE_VERSIONS
        or index.get("pipeline_version") != context.get("pipeline_version")
        or index.get("context_sha256") != context["context_sha256"]
        or index.get("inventory_sha256") != inventory["inventory_sha256"]
        or index.get("plan_sha256") != plan["plan_sha256"]
    ):
        raise book_synthesis.BookSynthesisError(
            "Promoted staged synthesis foundation is stale."
        )
    return context, inventory, plan, index


def prepare(
    project_path: Path,
    book_id: str,
    candidate: dict,
    profile_path: Path,
    *,
    protocol_version: str | None = None,
) -> dict:
    """Create immutable chapter packets and locked candidate segments."""
    project_path = project_path.resolve()
    directory = staging_directory(project_path, book_id)
    context = book_synthesis.build_context(
        project_path,
        book_id,
        candidate,
        profile_path,
        protocol_version=protocol_version,
    )
    if (
        context.get("protocol_version")
        == book_synthesis.SHARDED_PLAN_PROTOCOL
    ):
        context["pipeline_version"] = SHARDED_PIPELINE_VERSION
    elif book_synthesis.is_audited_plan_protocol(
        context.get("protocol_version")
    ):
        context["pipeline_version"] = AUDITED_PIPELINE_VERSION
    else:
        context["pipeline_version"] = PIPELINE_VERSION
    context["context_sha256"] = book_synthesis.canonical_json_sha256(
        context,
        "context_sha256",
    )
    if directory.exists():
        existing_context, index = _load_staging(project_path, book_id)
        if existing_context != context:
            raise book_synthesis.BookSynthesisError(
                "Existing staged synthesis targets a different candidate."
            )
        return index
    if book_synthesis.synthesis_directory(project_path, book_id).exists():
        raise book_synthesis.BookSynthesisError(
            "Cannot stage a second synthesis foundation."
        )

    manifest_path = book_synthesis.resolve_project_path(
        project_path,
        context["manifest_path"],
        "Book manifest",
    )
    manifest = book_synthesis.read_json_object(manifest_path, "Book manifest")
    chapters = _required_chapters(manifest)
    candidate_path = book_synthesis.resolve_project_path(
        project_path,
        context["candidate_path"],
        "Candidate book",
    )
    candidate_text = candidate_path.read_text(encoding="utf-8")
    claims_by_id = {
        claim["claim_id"]: claim for claim in context["required_claims"]
    }

    units = []
    locked_segments = []
    assembly_sequence = []
    cursor = 0
    packet_documents: list[tuple[dict, str]] = []
    locked_texts: list[tuple[dict, str]] = []
    for chapter in chapters:
        chapter_id = chapter["chapter_id"]
        draft_record = _chapter_draft_record(candidate, chapter_id)
        draft_path = book_synthesis.resolve_project_path(
            project_path,
            draft_record.get("path"),
            f"{chapter_id} draft",
        )
        if book_synthesis.file_sha256(draft_path) != draft_record.get("sha256"):
            raise book_synthesis.BookSynthesisError(
                f"{chapter_id} candidate draft hash changed."
            )
        draft_text = draft_path.read_text(encoding="utf-8")
        if len(draft_text) > MAX_UNIT_CHARACTERS:
            raise book_synthesis.BookSynthesisError(
                f"{chapter_id} exceeds the staged unit envelope; "
                "section packetization is required."
            )
        start = candidate_text.find(draft_text, cursor)
        if start < 0:
            raise book_synthesis.BookSynthesisError(
                f"{chapter_id} draft is not an exact candidate segment."
            )
        locked_text = candidate_text[cursor:start]
        locked_id = f"L{len(locked_segments) + 1}"
        locked_record = {
            "segment_id": locked_id,
            "path": f"locked/{locked_id}.md",
            "sha256": book_synthesis.hashlib.sha256(
                locked_text.encode("utf-8")
            ).hexdigest(),
        }
        locked_segments.append(locked_record)
        locked_texts.append((locked_record, locked_text))
        assembly_sequence.append(
            {"kind": "locked", "segment_id": locked_id}
        )

        canonical_claim_ids = chapter.get("claim_ids", [])
        if not isinstance(canonical_claim_ids, list) or any(
            claim_id not in claims_by_id for claim_id in canonical_claim_ids
        ):
            raise book_synthesis.BookSynthesisError(
                f"{chapter_id} has invalid synthesis claims."
            )
        packet_sha256 = book_synthesis.hashlib.sha256(
            draft_text.encode("utf-8")
        ).hexdigest()
        packet = {
            "schema_version": "1.0",
            "book_id": book_id,
            "unit_id": chapter_id,
            "working_title": chapter.get("working_title", chapter_id),
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "packet_path": f"packets/{chapter_id}.md",
            "packet_sha256": packet_sha256,
            "canonical_claims": [
                claims_by_id[claim_id] for claim_id in canonical_claim_ids
            ],
            "central_question": context["central_question"],
            "curiosity_signature": context["curiosity_signature"],
            "prohibited_overinterpretations": context[
                "prohibited_overinterpretations"
            ],
            "contradictions": context["contradictions"],
        }
        packet["packet_metadata_sha256"] = (
            book_synthesis.canonical_json_sha256(
                packet,
                "packet_metadata_sha256",
            )
        )
        unit = {
            "unit_id": chapter_id,
            "working_title": packet["working_title"],
            "packet_path": packet["packet_path"],
            "packet_sha256": packet_sha256,
            "packet_metadata_path": f"packets/{chapter_id}.json",
            "packet_metadata_sha256": packet["packet_metadata_sha256"],
            "canonical_claim_ids": canonical_claim_ids,
        }
        units.append(unit)
        packet_documents.append((packet, draft_text))
        assembly_sequence.append({"kind": "unit", "unit_id": chapter_id})
        cursor = start + len(draft_text)

    locked_text = candidate_text[cursor:]
    locked_id = f"L{len(locked_segments) + 1}"
    locked_record = {
        "segment_id": locked_id,
        "path": f"locked/{locked_id}.md",
        "sha256": book_synthesis.hashlib.sha256(
            locked_text.encode("utf-8")
        ).hexdigest(),
    }
    locked_segments.append(locked_record)
    locked_texts.append((locked_record, locked_text))
    assembly_sequence.append({"kind": "locked", "segment_id": locked_id})

    index = {
        "schema_version": "1.0",
        "pipeline_version": context["pipeline_version"],
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "candidate_sha256": context["candidate_sha256"],
        "units": units,
        "locked_segments": locked_segments,
        "assembly_sequence": assembly_sequence,
        "primary_call_budget": len(units) * 3 + 2,
        "maximum_attempts": 2,
    }
    index["index_sha256"] = book_synthesis.canonical_json_sha256(
        index,
        "index_sha256",
    )

    temporary = directory.with_name(
        f"{directory.name}.tmp-{uuid.uuid4().hex}"
    )
    temporary.mkdir(parents=True)
    try:
        _write_json(temporary / "context.json", context)
        for packet, text in packet_documents:
            _copy_text(temporary / packet["packet_path"], text)
            _write_json(
                temporary / f"packets/{packet['unit_id']}.json",
                packet,
            )
        for record, text in locked_texts:
            _copy_text(temporary / record["path"], text)
        _write_json(temporary / "index.json", index)
        os.replace(temporary, directory)
    except OSError as error:
        shutil.rmtree(temporary, ignore_errors=True)
        raise book_synthesis.BookSynthesisError(
            "Staged synthesis packets could not be installed."
        ) from error
    return index


def next_inventory_unit(project_path: Path, book_id: str) -> dict | None:
    _, index = _load_staging(project_path, book_id)
    directory = staging_directory(project_path, book_id)
    for unit in index["units"]:
        if not (directory / "inventory_units" / f"{unit['unit_id']}.json").is_file():
            return unit
    return None


def inventory_task(project_path: Path, book_id: str, unit_id: str) -> dict:
    context, index = _load_staging(project_path, book_id)
    unit = next(
        (item for item in index["units"] if item["unit_id"] == unit_id),
        None,
    )
    if unit is None:
        raise book_synthesis.BookSynthesisError("Unknown inventory unit.")
    root = staging_directory(project_path, book_id)
    claims_by_id = {
        claim["claim_id"]: claim for claim in context["required_claims"]
    }
    canonical_slots = []
    for order, claim_id in enumerate(unit["canonical_claim_ids"], start=1):
        claim = claims_by_id[claim_id]
        receipts = claim.get("source_receipt_ids", [])
        authority_type = (
            "source_evidence"
            if receipts
            else (
                "user_statement"
                if claim.get("origin") == "user"
                else "unknown"
            )
        )
        canonical_slots.append(
            {
                "claim_id": claim_id,
                "canonical_order": order,
                "authoritative_text": claim["claim"],
                "authoritative_text_sha256": hashlib.sha256(
                    claim["claim"].encode("utf-8")
                ).hexdigest(),
                "authority_type": authority_type,
                "authority_source": "canonical_claim",
                "authority_ref": claim_id,
                "evidence_receipt_ids": receipts,
                "source_origin": claim.get("origin"),
                "source_status": claim.get("status"),
            }
        )
    document = {
        "schema_version": "1.0",
        "tool_contract_version": INVENTORY_TOOL_CONTRACT_VERSION,
        "inventory_schema_version": "4.0",
        "protocol_version": context["protocol_version"],
        "book_id": book_id,
        "unit_id": unit_id,
        "context_sha256": context["context_sha256"],
        "candidate_sha256": context["candidate_sha256"],
        "candidate_assembly_sha256": context[
            "candidate_assembly_sha256"
        ],
        "manifest_sha256": context["manifest_sha256"],
        "profile_skill_sha256": context["profile"]["skill_sha256"],
        "packet_path": str(
            (root / unit["packet_path"]).relative_to(project_path.resolve())
        ),
        "packet_metadata_path": str(
            (root / unit["packet_metadata_path"]).relative_to(
                project_path.resolve()
            )
        ),
        "packet_metadata_sha256": unit["packet_metadata_sha256"],
        "packet_sha256": unit["packet_sha256"],
        "canonical_claim_ids": list(unit["canonical_claim_ids"]),
        "canonical_claim_slots": canonical_slots,
        "interpretation_contract": {
            "candidate_excerpt": {
                "match_mode": "case_sensitive_contiguous_substring",
                "source_field": "chapter_markdown",
            },
            "normative_surface_form": {
                "when": "protected_semantics.normative_force != none",
                "match_mode": "case_sensitive_contiguous_substring",
                "source_field": "canonical_claim_slots.authoritative_text",
            },
            "non_normative_surface_form": {
                "when": "protected_semantics.normative_force == none",
                "required_value": None,
            },
        },
    }
    document["input_hash"] = book_synthesis.canonical_json_sha256(
        document,
        "input_hash",
    )
    return document


def read_inventory_input(
    project_path: Path,
    book_id: str,
    unit_id: str,
    input_hash: str,
) -> dict:
    """Return one hash-bound inventory input and its exact chapter text."""
    project_path = project_path.resolve()
    task = inventory_task(project_path, book_id, unit_id)
    if input_hash != task["input_hash"]:
        raise book_synthesis.BookSynthesisError(
            "Requested inventory input hash is stale."
        )
    packet_path = book_synthesis.resolve_project_path(
        project_path,
        task["packet_path"],
        "Inventory chapter packet",
    )
    metadata_path = book_synthesis.resolve_project_path(
        project_path,
        task["packet_metadata_path"],
        "Inventory packet metadata",
    )
    if (
        book_synthesis.file_sha256(packet_path) != task["packet_sha256"]
        or not metadata_path.is_file()
    ):
        raise book_synthesis.BookSynthesisError(
            "Inventory input packet changed."
        )
    metadata = book_synthesis.read_json_object(
        metadata_path,
        "Inventory packet metadata",
    )
    if (
        metadata.get("packet_metadata_sha256")
        != task["packet_metadata_sha256"]
        or metadata.get("packet_metadata_sha256")
        != book_synthesis.canonical_json_sha256(
            metadata,
            "packet_metadata_sha256",
        )
        or metadata.get("packet_sha256") != task["packet_sha256"]
    ):
        raise book_synthesis.BookSynthesisError(
            "Inventory packet metadata changed."
        )
    return {
        "schema_version": "1.0",
        "book_id": book_id,
        "unit_id": unit_id,
        "input_hash": input_hash,
        "inventory_input": task,
        "packet_metadata": metadata,
        "chapter_markdown": packet_path.read_text(encoding="utf-8"),
    }


def save_inventory_contribution(
    project_path: Path,
    proposal: dict,
) -> dict:
    """Join model interpretation to deterministic canonical authority slots."""
    project_path = project_path.resolve()
    book_id = proposal.get("book_id")
    unit_id = proposal.get("unit_id")
    input_hash = proposal.get("input_hash")
    if not all(
        isinstance(value, str) and value
        for value in (book_id, unit_id, input_hash)
    ):
        raise book_synthesis.BookSynthesisError(
            "Inventory contribution identity is invalid."
        )
    task = inventory_task(project_path, book_id, unit_id)
    if input_hash != task["input_hash"]:
        raise book_synthesis.BookSynthesisError(
            "Inventory contribution input hash is stale."
        )

    canonical = proposal.get("canonical_claims")
    candidate = proposal.get("candidate_claims")
    if not isinstance(canonical, list) or not isinstance(candidate, list):
        raise InventoryCorrectionError(
            "Inventory contribution claim groups are invalid.",
            code="claim_group_list_required",
            field_path="canonical_claims_or_candidate_claims",
        )
    contribution_fields = (
        "claim_id",
        "candidate_excerpt",
        "candidate_location",
        "certainty",
        "scope_and_conditions",
        "attribution",
        "evidence_relationship",
        "resolution_status",
        "normalized_summary",
        "protected_semantics",
        "importance_reason",
    )
    for group_name, contributions in (
        ("canonical_claims", canonical),
        ("candidate_claims", candidate),
    ):
        for index, contribution in enumerate(contributions):
            if not isinstance(contribution, dict):
                raise InventoryCorrectionError(
                    "Inventory contribution claim is invalid.",
                    code="claim_object_required",
                    field_path=f"{group_name}[{index}]",
                )
            missing = next(
                (
                    field
                    for field in contribution_fields
                    if field not in contribution
                ),
                None,
            )
            if missing is not None:
                raise InventoryCorrectionError(
                    "Inventory contribution claim is incomplete.",
                    code="required_field_missing",
                    field_path=f"{group_name}[{index}].{missing}",
                    claim_id=(
                        contribution.get("claim_id")
                        if isinstance(contribution.get("claim_id"), str)
                        else None
                    ),
                )
    canonical_by_id = {
        item.get("claim_id"): item
        for item in canonical
        if isinstance(item, dict)
        and isinstance(item.get("claim_id"), str)
    }
    expected_ids = [
        slot["claim_id"] for slot in task["canonical_claim_slots"]
    ]
    submitted_ids = [
        item.get("claim_id") for item in canonical if isinstance(item, dict)
    ]
    if (
        len(canonical_by_id) != len(canonical)
        or set(canonical_by_id) != set(expected_ids)
    ):
        raise InventoryCorrectionError(
            "Canonical inventory contributions must match the supplied slots.",
            code="canonical_slot_set_mismatch",
            field_path="canonical_claims.claim_id",
        )
    candidate_ids = [
        item.get("claim_id") for item in candidate if isinstance(item, dict)
    ]
    if (
        len(candidate_ids) != len(candidate)
        or candidate_ids
        != [f"LOCAL{index}" for index in range(1, len(candidate) + 1)]
    ):
        raise InventoryCorrectionError(
            "Candidate-only contributions must be LOCAL1, LOCAL2, and so on.",
            code="candidate_claim_numbering_invalid",
            field_path="candidate_claims.claim_id",
        )

    claims = []
    for slot in task["canonical_claim_slots"]:
        contribution = canonical_by_id[slot["claim_id"]]
        claims.append(
            {
                "claim_id": slot["claim_id"],
                "source_kind": "canonical",
                "statement": slot["authoritative_text"],
                "candidate_excerpt": contribution["candidate_excerpt"],
                "candidate_location": contribution["candidate_location"],
                "certainty": contribution["certainty"],
                "scope_and_conditions": contribution[
                    "scope_and_conditions"
                ],
                "evidence_receipt_ids": slot["evidence_receipt_ids"],
                "attribution": contribution["attribution"],
                "evidence_relationship": contribution[
                    "evidence_relationship"
                ],
                "authority_type": slot["authority_type"],
                "resolution_status": contribution["resolution_status"],
                "authority_source": slot["authority_source"],
                "authority_ref": slot["authority_ref"],
                "authoritative_text": slot["authoritative_text"],
                "normalized_summary": contribution["normalized_summary"],
                "protected_semantics": contribution[
                    "protected_semantics"
                ],
                "required_in_edition": True,
                "importance_reason": contribution["importance_reason"],
            }
        )
    for contribution in candidate:
        claims.append(
            {
                "claim_id": contribution["claim_id"],
                "source_kind": "candidate_only",
                "statement": contribution["candidate_excerpt"],
                "candidate_excerpt": contribution["candidate_excerpt"],
                "candidate_location": contribution["candidate_location"],
                "certainty": contribution["certainty"],
                "scope_and_conditions": contribution[
                    "scope_and_conditions"
                ],
                "evidence_receipt_ids": [],
                "attribution": contribution["attribution"],
                "evidence_relationship": contribution[
                    "evidence_relationship"
                ],
                "authority_type": "candidate_prose",
                "resolution_status": contribution["resolution_status"],
                "authority_source": "candidate_excerpt",
                "authority_ref": "candidate_excerpt",
                "authoritative_text": contribution["candidate_excerpt"],
                "normalized_summary": contribution["normalized_summary"],
                "protected_semantics": contribution[
                    "protected_semantics"
                ],
                "required_in_edition": True,
                "importance_reason": contribution["importance_reason"],
            }
        )
    return save_inventory_unit(
        project_path,
        {
            "book_id": book_id,
            "unit_id": unit_id,
            "input_hash": input_hash,
            "context_sha256": task["context_sha256"],
            "candidate_sha256": task["candidate_sha256"],
            "packet_sha256": task["packet_sha256"],
            "claims": claims,
            "definitions": proposal.get("definitions", []),
            "limitations": proposal.get("limitations", []),
            "unresolved_questions": proposal.get(
                "unresolved_questions",
                [],
            ),
        },
        correction_boundary=True,
    )


def save_inventory_unit(
    project_path: Path,
    proposal: dict,
    *,
    correction_boundary: bool = False,
) -> dict:
    """Save one exact chapter inventory shard, idempotently."""
    project_path = project_path.resolve()
    book_id = proposal.get("book_id")
    unit_id = proposal.get("unit_id")
    if not isinstance(book_id, str) or not isinstance(unit_id, str):
        raise book_synthesis.BookSynthesisError(
            "Inventory unit identity is invalid."
        )
    context, index = _load_staging(project_path, book_id)
    unit = next(
        (item for item in index["units"] if item["unit_id"] == unit_id),
        None,
    )
    if (
        unit is None
        or proposal.get("context_sha256") != context["context_sha256"]
        or proposal.get("candidate_sha256") != context["candidate_sha256"]
        or proposal.get("packet_sha256") != unit["packet_sha256"]
    ):
        raise book_synthesis.BookSynthesisError(
            "Inventory unit foundation is stale."
        )
    claims = proposal.get("claims")
    if not isinstance(claims, list):
        raise book_synthesis.BookSynthesisError(
            "Inventory unit claims are invalid."
        )
    canonical = [
        claim for claim in claims if claim.get("source_kind") == "canonical"
    ]
    if [claim.get("claim_id") for claim in canonical] != unit[
        "canonical_claim_ids"
    ]:
        raise book_synthesis.BookSynthesisError(
            "Inventory unit must include its canonical claims in order."
        )
    candidate_only = [
        claim
        for claim in claims
        if claim.get("source_kind") == "candidate_only"
    ]
    if [claim.get("claim_id") for claim in candidate_only] != [
        f"LOCAL{index}" for index in range(1, len(candidate_only) + 1)
    ]:
        raise book_synthesis.BookSynthesisError(
            "Candidate-only unit claims must be LOCAL1, LOCAL2, and so on."
        )
    packet_path = staging_directory(project_path, book_id) / unit["packet_path"]
    packet_text = packet_path.read_text(encoding="utf-8")
    canonical_by_id = {
        claim["claim_id"]: claim for claim in context["required_claims"]
    }
    allowed_receipts = set(context["source_receipt_ids"])
    required_text_fields = (
        "statement",
        "candidate_excerpt",
        "candidate_location",
        "certainty",
        "scope_and_conditions",
        "importance_reason",
    )
    audited_protocol = book_synthesis.is_audited_plan_protocol(
        context.get("protocol_version")
    )
    for claim in claims:
        claim_id = (
            claim.get("claim_id")
            if isinstance(claim.get("claim_id"), str)
            else None
        )

        def reject_model_field(
            message: str,
            code: str,
            field_path: str,
        ) -> None:
            if correction_boundary:
                raise InventoryCorrectionError(
                    message,
                    code=code,
                    field_path=field_path,
                    claim_id=claim_id,
                )
            raise book_synthesis.BookSynthesisError(message)

        if claim.get("source_kind") not in {"canonical", "candidate_only"}:
            raise book_synthesis.BookSynthesisError(
                "Inventory unit claim content is invalid."
            )
        for field in required_text_fields:
            if (
                not isinstance(claim.get(field), str)
                or not claim[field].strip()
            ):
                if field == "statement":
                    raise book_synthesis.BookSynthesisError(
                        "Inventory unit claim content is invalid."
                    )
                reject_model_field(
                    "Inventory unit claim content is invalid.",
                    "nonempty_text_required",
                    field,
                )
        if claim["candidate_excerpt"] not in packet_text:
            reject_model_field(
                "Inventory candidate excerpt was not found exactly.",
                "exact_candidate_substring_required",
                "candidate_excerpt",
            )
        if (
            claim.get("required_in_edition") is not True
            or not isinstance(claim.get("evidence_receipt_ids"), list)
            or len(claim["evidence_receipt_ids"])
            != len(set(claim["evidence_receipt_ids"]))
            or not set(claim["evidence_receipt_ids"]).issubset(
                allowed_receipts
            )
        ):
            raise book_synthesis.BookSynthesisError(
                "Inventory unit claim content is invalid."
            )
        if audited_protocol and (
            not isinstance(claim.get("attribution"), str)
            or not claim["attribution"].strip()
            or not isinstance(claim.get("evidence_relationship"), str)
            or not claim["evidence_relationship"].strip()
            or claim.get("resolution_status")
            not in book_synthesis.INVENTORY_RESOLUTION_STATUSES
        ):
            invalid_field = next(
                field
                for field in (
                    "attribution",
                    "evidence_relationship",
                    "resolution_status",
                )
                if (
                    not isinstance(claim.get(field), str)
                    or not claim[field].strip()
                    or (
                        field == "resolution_status"
                        and claim.get(field)
                        not in book_synthesis.INVENTORY_RESOLUTION_STATUSES
                    )
                )
            )
            reject_model_field(
                "Audited-plan inventory claim frame is incomplete.",
                "claim_frame_invalid",
                invalid_field,
            )
        if (
            audited_protocol
            and claim.get("authority_type")
            not in book_synthesis.INVENTORY_AUTHORITY_TYPES
        ):
            raise book_synthesis.BookSynthesisError(
                "Audited-plan inventory authority frame is invalid."
            )
        book_synthesis.validate_inventory_claim_authority(
            claim,
            canonical_by_id,
            required=audited_protocol,
            correction_factory=(
                (
                    lambda message, code, field_path: (
                        InventoryCorrectionError(
                            message,
                            code=code,
                            field_path=field_path,
                            claim_id=claim_id,
                        )
                    )
                )
                if correction_boundary
                else None
            ),
        )
        if claim["source_kind"] == "canonical":
            source = canonical_by_id.get(claim["claim_id"])
            if (
                source is None
                or claim["statement"] != source["claim"]
                or claim["evidence_receipt_ids"]
                != source.get("source_receipt_ids", [])
            ):
                raise book_synthesis.BookSynthesisError(
                    "Inventory unit changed a canonical claim."
                )
    for field in ("definitions", "limitations", "unresolved_questions"):
        values = proposal.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            if correction_boundary:
                raise InventoryCorrectionError(
                    f"Inventory unit {field} are invalid.",
                    code="nonempty_text_list_required",
                    field_path=field,
                )
            raise book_synthesis.BookSynthesisError(
                f"Inventory unit {field} are invalid."
            )
    document = {
        **proposal,
        "schema_version": "2.0" if audited_protocol else "1.0",
        "saved_at": book_synthesis.current_utc_time(),
    }
    document["inventory_unit_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "inventory_unit_sha256",
        )
    )
    path = (
        staging_directory(project_path, book_id)
        / "inventory_units"
        / f"{unit_id}.json"
    )
    if path.exists():
        existing = book_synthesis.read_json_object(path, "Inventory unit")
        comparable = {
            key: value
            for key, value in existing.items()
            if key not in {"saved_at", "inventory_unit_sha256"}
        }
        requested = {
            key: value
            for key, value in document.items()
            if key not in {"saved_at", "inventory_unit_sha256"}
        }
        if comparable != requested:
            raise book_synthesis.BookSynthesisError(
                "Inventory unit already exists with different content."
            )
        return existing
    book_synthesis.atomic_write_json(path, document)
    return document


def _unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def finalize_inventory(project_path: Path, book_id: str) -> dict:
    """Merge every verified inventory shard into one deterministic inventory."""
    context, index = _load_staging(project_path, book_id)
    directory = staging_directory(project_path, book_id)
    path = directory / "inventory.json"
    if path.exists():
        return book_synthesis.read_json_object(path, "Merged inventory")
    if next_inventory_unit(project_path, book_id) is not None:
        raise book_synthesis.BookSynthesisError(
            "Inventory cannot merge before every unit is saved."
        )
    occurrence_claims: list[dict] = []
    unit_claim_ids: dict[str, list[str]] = {}
    definitions: list[str] = []
    limitations: list[str] = []
    unresolved: list[str] = []
    next_occurrence_id = 1
    next_candidate_id = 1
    for unit in index["units"]:
        unit_id = unit["unit_id"]
        shard = book_synthesis.read_json_object(
            directory / "inventory_units" / f"{unit_id}.json",
            f"{unit_id} inventory",
        )
        assigned: list[str] = []
        for claim in shard["claims"]:
            item = dict(claim)
            if item["source_kind"] == "canonical":
                canonical_claim_id = item["claim_id"]
                item["claim_id"] = f"CO{next_occurrence_id}"
                next_occurrence_id += 1
                item["source_kind"] = "canonical_occurrence"
                item["canonical_claim_id"] = canonical_claim_id
                item["origin_unit_id"] = unit_id
                occurrence_claims.append(item)
                assigned.append(item["claim_id"])
            else:
                item["claim_id"] = f"SC{next_candidate_id}"
                next_candidate_id += 1
                item["origin_unit_id"] = unit_id
                occurrence_claims.append(item)
                assigned.append(item["claim_id"])
        unit_claim_ids[unit_id] = assigned
        definitions.extend(shard["definitions"])
        limitations.extend(shard["limitations"])
        unresolved.extend(shard["unresolved_questions"])
    inventory = {
        "schema_version": (
            "4.0"
            if book_synthesis.is_audited_plan_protocol(
                context.get("protocol_version")
            )
            else "2.0"
        ),
        "identity_model": "canonical-claim-occurrence-v1",
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "candidate_sha256": context["candidate_sha256"],
        "central_question": context["central_question"],
        "curiosity_signature": context["curiosity_signature"],
        "canonical_claims": context["required_claims"],
        "claims": occurrence_claims,
        "definitions": _unique_strings(definitions),
        "contradictions": context["contradictions"],
        "limitations": _unique_strings(limitations),
        "unresolved_questions": _unique_strings(unresolved),
        "unit_claim_ids": unit_claim_ids,
    }
    candidate_text = book_synthesis.resolve_project_path(
        project_path,
        context["candidate_path"],
        "Candidate book",
    ).read_text(encoding="utf-8")
    book_synthesis._validate_inventory(inventory, context, candidate_text)
    inventory["staging_inventory_sha256"] = (
        book_synthesis.canonical_json_sha256(
            inventory,
            "staging_inventory_sha256",
        )
    )
    book_synthesis.atomic_write_json(path, inventory)
    return inventory


def reuse_prior_verified_inventory(
    project_path: Path,
    book_id: str,
) -> dict:
    """Rebind cycle-002 inventory shards to cycle 003 without a provider call."""
    project_path = project_path.resolve()
    context, index = _load_staging(project_path, book_id)
    if context.get("protocol_version") != book_synthesis.SHARDED_PLAN_PROTOCOL:
        raise book_synthesis.BookSynthesisError(
            "Inventory reuse is only valid for the sharded cycle."
        )
    authorization = book_synthesis.verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    if authorization is None:
        raise book_synthesis.BookSynthesisError(
            "Sharded inventory reuse has no authorization."
        )
    if authorization.get("inventory_policy") == "regenerate_required":
        raise book_synthesis.BookSynthesisError(
            "This successor requires a newly generated inventory."
        )
    prior_inventory_path = book_synthesis.resolve_project_path(
        project_path,
        authorization.get("prior_inventory_path"),
        "Prior inventory",
    )
    prior_root = prior_inventory_path.parent
    prior_index = _load_index(prior_root)
    if (
        [unit["unit_id"] for unit in prior_index["units"]]
        != [unit["unit_id"] for unit in index["units"]]
        or [
            (unit["unit_id"], unit["packet_sha256"])
            for unit in prior_index["units"]
        ]
        != [
            (unit["unit_id"], unit["packet_sha256"])
            for unit in index["units"]
        ]
    ):
        raise book_synthesis.BookSynthesisError(
            "Prior inventory packets are incompatible with cycle 003."
        )
    for unit in index["units"]:
        prior = book_synthesis.read_json_object(
            prior_root / "inventory_units" / f"{unit['unit_id']}.json",
            f"Prior {unit['unit_id']} inventory",
        )
        if (
            prior.get("inventory_unit_sha256")
            != book_synthesis.canonical_json_sha256(
                prior,
                "inventory_unit_sha256",
            )
            or prior.get("packet_sha256") != unit["packet_sha256"]
        ):
            raise book_synthesis.BookSynthesisError(
                f"Prior {unit['unit_id']} inventory is stale."
            )
        proposal = {
            key: value
            for key, value in prior.items()
            if key
            not in {"schema_version", "saved_at", "inventory_unit_sha256"}
        }
        proposal["context_sha256"] = context["context_sha256"]
        proposal["candidate_sha256"] = context["candidate_sha256"]
        save_inventory_unit(project_path, proposal)
    return finalize_inventory(project_path, book_id)


SHARD_DISPOSITIONS = {
    "preserved_unchanged",
    "clarified",
    "moved_within_chapter",
    "merged_with_named_occurrences",
    "removed_with_explicit_authority",
    "preserved_as_unresolved",
}
GLOBAL_PLAN_AUDIT_QUESTIONS = (
    "central_question_preserved",
    "cross_chapter_operations_compatible",
    "unresolved_global_decisions_preserved",
    "terminology_consistent",
    "authority_and_evidence_preserved",
    "curiosity_and_answer_boundary_preserved",
    "all_operations_supported",
)


def plan_shard_input_path(staging: Path, chapter_id: str) -> Path:
    return staging / "plan_inputs" / f"{chapter_id}.json"


def plan_shard_path(staging: Path, chapter_id: str) -> Path:
    return staging / "plan_shards" / f"{chapter_id}.json"


def prepare_plan_shard_inputs(project_path: Path, book_id: str) -> dict:
    """Create six immutable chapter-scoped planning inputs."""
    project_path = project_path.resolve()
    context, index = _load_staging(project_path, book_id)
    if context.get("protocol_version") != book_synthesis.SHARDED_PLAN_PROTOCOL:
        raise book_synthesis.BookSynthesisError(
            "Plan shards require the chapter-sharded protocol."
        )
    inventory = finalize_inventory(project_path, book_id)
    root = staging_directory(project_path, book_id)
    output_index = root / "plan_inputs" / "index.json"
    if output_index.is_file():
        document = book_synthesis.read_json_object(
            output_index,
            "Plan-shard input index",
        )
        if document.get("plan_input_index_sha256") != (
            book_synthesis.canonical_json_sha256(
                document,
                "plan_input_index_sha256",
            )
        ):
            raise book_synthesis.BookSynthesisError(
                "Plan-shard input index is stale."
            )
        return document
    claims_by_id = {
        claim["claim_id"]: claim for claim in inventory["claims"]
    }
    canonical_by_id = {
        claim["claim_id"]: claim for claim in inventory["canonical_claims"]
    }
    records = []
    for unit in index["units"]:
        chapter_id = unit["unit_id"]
        claim_ids = inventory["unit_claim_ids"][chapter_id]
        claims = [claims_by_id[claim_id] for claim_id in claim_ids]
        canonical_ids = _unique_strings(
            [
                claim["canonical_claim_id"]
                for claim in claims
                if claim.get("canonical_claim_id")
            ]
        )
        document = {
            "schema_version": "1.0",
            "protocol_version": book_synthesis.SHARDED_PLAN_PROTOCOL,
            "book_id": book_id,
            "shard_id": chapter_id,
            "chapter_id": chapter_id,
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "inventory_sha256": inventory["staging_inventory_sha256"],
            "chapter_path": str(
                (root / unit["packet_path"]).relative_to(project_path)
            ),
            "chapter_sha256": unit["packet_sha256"],
            "chapter_claim_ids": claim_ids,
            "chapter_claims": claims,
            "canonical_claim_definitions": [
                canonical_by_id[claim_id] for claim_id in canonical_ids
            ],
            "human_decision_artifacts": context.get(
                "human_decision_artifacts",
                [],
            ),
            "unresolved_claim_ids": [
                claim["claim_id"]
                for claim in claims
                if claim.get("resolution_status")
                in {"open", "routed", "unresolved", "contradicted"}
            ],
            "global_rules": {
                "central_question": context["central_question"],
                "provisional_thesis": context.get("provisional_thesis"),
                "curiosity_signature": context["curiosity_signature"],
                "prohibited_overinterpretations": context[
                    "prohibited_overinterpretations"
                ],
                "profile": context["profile"],
                "allowed_chapter_ids": [
                    item["unit_id"] for item in index["units"]
                ],
                "public_profile_rename_allowed": any(
                    decision.get("decision_id") == "profile-public-name"
                    for decision in context.get(
                        "human_decision_artifacts",
                        [],
                    )
                ),
            },
        }
        document["input_hash"] = book_synthesis.canonical_json_sha256(
            document,
            "input_hash",
        )
        book_synthesis.atomic_write_json(
            plan_shard_input_path(root, chapter_id),
            document,
        )
        records.append(
            {
                "shard_id": chapter_id,
                "chapter_id": chapter_id,
                "input_path": str(
                    plan_shard_input_path(root, chapter_id).relative_to(
                        project_path
                    )
                ),
                "input_hash": document["input_hash"],
            }
        )
    result = {
        "schema_version": "1.0",
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["staging_inventory_sha256"],
        "shards": records,
        "maximum_provider_calls_per_shard": 2,
    }
    result["plan_input_index_sha256"] = (
        book_synthesis.canonical_json_sha256(
            result,
            "plan_input_index_sha256",
        )
    )
    book_synthesis.atomic_write_json(output_index, result)
    return result


def load_plan_shard_input(
    project_path: Path,
    book_id: str,
    chapter_id: str,
) -> dict:
    root = staging_directory(project_path, book_id)
    document = book_synthesis.read_json_object(
        plan_shard_input_path(root, chapter_id),
        f"{chapter_id} plan input",
    )
    if (
        document.get("book_id") != book_id
        or document.get("chapter_id") != chapter_id
        or document.get("input_hash")
        != book_synthesis.canonical_json_sha256(document, "input_hash")
    ):
        raise book_synthesis.BookSynthesisError(
            f"{chapter_id} plan input is stale."
        )
    return document


def next_plan_shard(project_path: Path, book_id: str) -> str | None:
    _, index = _load_staging(project_path, book_id)
    prepare_plan_shard_inputs(project_path, book_id)
    root = staging_directory(project_path, book_id)
    for unit in index["units"]:
        if not plan_shard_path(root, unit["unit_id"]).is_file():
            return unit["unit_id"]
    return None


def plan_shard_task(
    project_path: Path,
    book_id: str,
    chapter_id: str,
) -> dict:
    input_document = load_plan_shard_input(
        project_path,
        book_id,
        chapter_id,
    )
    return {
        "book_id": book_id,
        "shard_id": chapter_id,
        "chapter_id": chapter_id,
        "input_path": str(
            plan_shard_input_path(
                staging_directory(project_path, book_id),
                chapter_id,
            ).relative_to(project_path.resolve())
        ),
        "input_hash": input_document["input_hash"],
        "maximum_provider_calls": 2,
        "retry_rule": (
            "one fresh-process retry; the same provider is allowed"
        ),
    }


def read_plan_shard_input(
    project_path: Path,
    book_id: str,
    chapter_id: str,
    input_hash: str,
) -> dict:
    """Return one verified chapter-scoped planning input without mutation."""
    project_path = project_path.resolve()
    document = load_plan_shard_input(
        project_path,
        book_id,
        chapter_id,
    )
    if input_hash != document["input_hash"]:
        raise book_synthesis.BookSynthesisError(
            "Requested plan-shard input hash is stale."
        )
    chapter_path = book_synthesis.resolve_project_path(
        project_path,
        document["chapter_path"],
        "Plan-shard chapter",
    )
    if book_synthesis.file_sha256(chapter_path) != document["chapter_sha256"]:
        raise book_synthesis.BookSynthesisError(
            "Plan-shard chapter hash changed."
        )
    try:
        chapter_markdown = chapter_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise book_synthesis.BookSynthesisError(
            "Plan-shard chapter text could not be read."
        ) from error
    return {
        "schema_version": "1.0",
        "book_id": book_id,
        "shard_id": document["shard_id"],
        "chapter_id": chapter_id,
        "input_hash": document["input_hash"],
        "chapter_sha256": document["chapter_sha256"],
        "plan_input": document,
        "chapter_markdown": chapter_markdown,
    }


def _validate_shard_authority(
    item: dict,
    claim_ids: set[str],
    decision_ids: set[str],
) -> None:
    authority_type = item.get("authority_type")
    authority_refs = item.get("authority_refs")
    if (
        authority_type not in book_synthesis.PLAN_AUTHORITY_TYPES
        or not isinstance(authority_refs, list)
        or len(authority_refs) != len(set(authority_refs))
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard authority is invalid."
        )
    if authority_type == "candidate" and (
        not authority_refs or not set(authority_refs).issubset(claim_ids)
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan shard cites foreign candidate authority."
        )
    if authority_type == "human_decision" and (
        not authority_refs or not set(authority_refs).issubset(decision_ids)
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan shard cites an unknown human decision."
        )
    if authority_type == "editorial_mechanics" and authority_refs:
        raise book_synthesis.BookSynthesisError(
            "Editorial mechanics cannot cite authority references."
        )


def save_plan_shard(project_path: Path, proposal: dict) -> dict:
    """Validate and save one shard, exposing only proposal defects as retryable."""
    project_path = project_path.resolve()
    book_id = proposal.get("book_id")
    chapter_id = proposal.get("chapter_id")
    if (
        not isinstance(book_id, str)
        or not isinstance(chapter_id, str)
        or proposal.get("shard_id") != chapter_id
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard identity is invalid."
        )
    input_document = load_plan_shard_input(
        project_path,
        book_id,
        chapter_id,
    )
    if proposal.get("input_hash") != input_document["input_hash"]:
        raise book_synthesis.BookSynthesisError(
            "Plan-shard input hash is stale."
        )
    chapter_path = book_synthesis.resolve_project_path(
        project_path,
        input_document["chapter_path"],
        "Plan-shard chapter",
    )
    if book_synthesis.file_sha256(chapter_path) != (
        input_document["chapter_sha256"]
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard chapter hash changed."
        )
    try:
        chapter_text = chapter_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise book_synthesis.BookSynthesisError(
            "Plan-shard chapter text could not be read."
        ) from error
    try:
        return _save_validated_plan_shard(
            project_path,
            proposal,
            input_document,
            chapter_text,
        )
    except PlanShardArtifactConflictError as error:
        raise book_synthesis.BookSynthesisError(str(error)) from error
    except book_synthesis.BookSynthesisError as error:
        raise PlanShardCorrectionError(str(error)) from error


def _save_validated_plan_shard(
    project_path: Path,
    proposal: dict,
    input_document: dict,
    chapter_text: str,
) -> dict:
    """Apply proposal-level rules after identity and source integrity checks."""
    book_id = proposal["book_id"]
    chapter_id = proposal["chapter_id"]
    claim_ids = input_document["chapter_claim_ids"]
    claim_id_set = set(claim_ids)
    decisions = {
        decision["decision_id"]
        for decision in input_document["human_decision_artifacts"]
    }
    if (
        not isinstance(proposal.get("chapter_job"), str)
        or not proposal["chapter_job"].strip()
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan shard has no chapter job."
        )
    dispositions = proposal.get("claim_dispositions")
    if (
        not isinstance(dispositions, list)
        or [item.get("claim_id") for item in dispositions] != claim_ids
    ):
        raise book_synthesis.BookSynthesisError(
            "Every chapter claim needs exactly one ordered disposition."
        )
    unresolved_ids = set(input_document["unresolved_claim_ids"])
    for disposition in dispositions:
        if (
            disposition.get("disposition") not in SHARD_DISPOSITIONS
            or not isinstance(disposition.get("explanation"), str)
            or not disposition["explanation"].strip()
            or not isinstance(disposition.get("related_claim_ids"), list)
            or not set(disposition["related_claim_ids"]).issubset(claim_id_set)
            or disposition.get("resolution_effect")
            not in book_synthesis.PLAN_RESOLUTION_EFFECTS
        ):
            raise book_synthesis.BookSynthesisError(
                "Plan-shard claim disposition is invalid."
            )
        _validate_shard_authority(disposition, claim_id_set, decisions)
        if (
            disposition["disposition"]
            == "merged_with_named_occurrences"
            and not disposition["related_claim_ids"]
        ):
            raise book_synthesis.BookSynthesisError(
                "Merged disposition must name its local occurrences."
            )
        if (
            disposition["disposition"]
            == "removed_with_explicit_authority"
            and disposition["authority_type"] != "human_decision"
        ):
            raise book_synthesis.BookSynthesisError(
                "Removing a protected claim requires a human decision."
            )
        if (
            disposition["claim_id"] in unresolved_ids
            and disposition.get("resolution_effect") == "resolved"
            and disposition["authority_type"] != "human_decision"
        ):
            raise book_synthesis.BookSynthesisError(
                "A local shard cannot settle an unresolved claim."
            )

    operations = proposal.get("operations")
    if not isinstance(operations, list):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard operations are invalid."
        )
    operation_ids = []
    valid_kinds = {
        "merge",
        "move",
        "compress",
        "transition",
        "terminology",
        "appendix",
        "conclusion",
    }
    forbidden_effects = {
        "rename",
        "resolve",
        "change_evidence_relationship",
    }
    for operation_number, operation in enumerate(operations, start=1):
        label = f"Plan-shard operation {operation_number}"
        if not isinstance(operation, dict):
            raise book_synthesis.BookSynthesisError(
                f"{label} is not an object."
            )
        operation_id = operation.get("operation_id")
        operation_ids.append(operation_id)
        if (
            not isinstance(operation_id, str)
            or not re.fullmatch(r"OP[1-9][0-9]*", operation_id)
        ):
            raise book_synthesis.BookSynthesisError(
                f"{label} has an invalid operation_id."
            )
        if operation.get("kind") not in valid_kinds:
            raise book_synthesis.BookSynthesisError(
                f"{label} has an unsupported kind."
            )
        if operation.get("target") != chapter_id:
            raise book_synthesis.BookSynthesisError(
                f"{label} must target only {chapter_id}."
            )
        if (
            not isinstance(operation.get("description"), str)
            or not operation["description"].strip()
        ):
            raise book_synthesis.BookSynthesisError(
                f"{label} has no description."
            )
        protected_claim_ids = operation.get("protected_claim_ids")
        if not isinstance(protected_claim_ids, list):
            raise book_synthesis.BookSynthesisError(
                f"{label} protected_claim_ids must be a list."
            )
        foreign_claim_ids = sorted(
            set(protected_claim_ids) - claim_id_set
        )
        if foreign_claim_ids:
            raise book_synthesis.BookSynthesisError(
                f"{label} cites claims outside {chapter_id}: "
                + ", ".join(foreign_claim_ids)
                + "."
            )
        effect_type = operation.get("effect_type")
        if effect_type in forbidden_effects:
            raise book_synthesis.BookSynthesisError(
                f"{label} uses forbidden effect_type {effect_type}."
            )
        if effect_type not in book_synthesis.PLAN_EFFECT_TYPES:
            raise book_synthesis.BookSynthesisError(
                f"{label} has an invalid effect_type."
            )
        if (
            operation.get("resolution_effect")
            not in book_synthesis.PLAN_RESOLUTION_EFFECTS
        ):
            raise book_synthesis.BookSynthesisError(
                f"{label} has an invalid resolution_effect."
            )
        _validate_shard_authority(operation, claim_id_set, decisions)
        if (
            operation.get("resolution_effect") == "resolved"
            and set(operation["protected_claim_ids"]) & unresolved_ids
            and operation["authority_type"] != "human_decision"
        ):
            raise book_synthesis.BookSynthesisError(
                "A local operation cannot settle an unresolved claim."
            )
    if len(operation_ids) != len(set(operation_ids)):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard operation identities repeat."
        )

    terminology = proposal.get("terminology")
    if not isinstance(terminology, list):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard terminology is invalid."
        )
    for entry in terminology:
        if (
            not isinstance(entry.get("term"), str)
            or not entry["term"].strip()
            or not isinstance(entry.get("canonical_form"), str)
            or not entry["canonical_form"].strip()
            or entry.get("operation_id") not in operation_ids
            or next(
                operation
                for operation in operations
                if operation["operation_id"] == entry["operation_id"]
            ).get("kind")
            != "terminology"
            or entry.get("applies_to") != [chapter_id]
        ):
            raise book_synthesis.BookSynthesisError(
                "Plan-shard terminology is not locally scoped."
            )
        _validate_shard_authority(entry, claim_id_set, decisions)
        if (
            entry["authority_type"] == "editorial_mechanics"
            and entry["term"] not in chapter_text
            and entry["canonical_form"] not in chapter_text
        ):
            raise book_synthesis.BookSynthesisError(
                "Editorial terminology is unsupported by the chapter."
            )
        if (
            entry["authority_type"] == "candidate"
            and not any(
                entry["term"] in claim["candidate_excerpt"]
                or entry["canonical_form"] in claim["candidate_excerpt"]
                for claim in input_document["chapter_claims"]
                if claim["claim_id"] in entry["authority_refs"]
            )
        ):
            raise book_synthesis.BookSynthesisError(
                "Candidate terminology has no cited textual support."
            )

    unresolved = proposal.get("unresolved_decisions")
    if not isinstance(unresolved, list) or any(
        not isinstance(item, dict)
        or item.get("status") != "unresolved"
        or not isinstance(item.get("question"), str)
        or not item["question"].strip()
        or not isinstance(item.get("claim_ids"), list)
        or not set(item["claim_ids"]).issubset(claim_id_set)
        for item in unresolved
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan-shard unresolved decisions are invalid."
        )
    allowed_chapters = set(
        input_document["global_rules"]["allowed_chapter_ids"]
    )
    dependencies = proposal.get("cross_chapter_dependencies")
    if not isinstance(dependencies, list) or any(
        not isinstance(item, dict)
        or item.get("from_chapter_id") != chapter_id
        or item.get("to_chapter_id") not in allowed_chapters - {chapter_id}
        or not isinstance(item.get("description"), str)
        or not item["description"].strip()
        or not isinstance(item.get("claim_ids"), list)
        or not set(item["claim_ids"]).issubset(claim_id_set)
        for item in dependencies
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan shard contains an invalid cross-chapter dependency."
        )
    document = {
        **proposal,
        "schema_version": "1.0",
        "saved_at": book_synthesis.current_utc_time(),
    }
    document["plan_shard_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "plan_shard_sha256",
        )
    )
    path = plan_shard_path(
        staging_directory(project_path, book_id),
        chapter_id,
    )
    if path.is_file():
        existing = book_synthesis.read_json_object(path, "Plan shard")
        comparable = {
            key: value
            for key, value in existing.items()
            if key not in {"saved_at", "plan_shard_sha256"}
        }
        requested = {
            key: value
            for key, value in document.items()
            if key not in {"saved_at", "plan_shard_sha256"}
        }
        if comparable != requested:
            raise PlanShardArtifactConflictError(
                "Plan shard already exists with different content."
            )
        return existing
    book_synthesis.atomic_write_json(path, document)
    return document


def _load_plan_shard(
    project_path: Path,
    book_id: str,
    chapter_id: str,
) -> dict:
    shard = book_synthesis.read_json_object(
        plan_shard_path(
            staging_directory(project_path, book_id),
            chapter_id,
        ),
        f"{chapter_id} plan shard",
    )
    if shard.get("plan_shard_sha256") != (
        book_synthesis.canonical_json_sha256(
            shard,
            "plan_shard_sha256",
        )
    ):
        raise book_synthesis.BookSynthesisError(
            f"{chapter_id} plan shard is stale."
        )
    plan_input = load_plan_shard_input(
        project_path,
        book_id,
        chapter_id,
    )
    if shard.get("input_hash") != plan_input["input_hash"]:
        raise book_synthesis.BookSynthesisError(
            f"{chapter_id} plan shard targets an obsolete input."
        )
    return shard


def merge_plan_shards(project_path: Path, book_id: str) -> dict:
    """Deterministically merge validated shards without model authorship."""
    project_path = project_path.resolve()
    context, index = _load_staging(project_path, book_id)
    if next_plan_shard(project_path, book_id) is not None:
        raise book_synthesis.BookSynthesisError(
            "Plan shards cannot merge before all chapters complete."
        )
    inventory = finalize_inventory(project_path, book_id)
    claim_order = {
        claim["claim_id"]: position
        for position, claim in enumerate(inventory["claims"])
    }
    shards = [
        _load_plan_shard(project_path, book_id, unit["unit_id"])
        for unit in index["units"]
    ]
    terminology_by_term: dict[str, str] = {}
    for shard in shards:
        for term in shard["terminology"]:
            existing = terminology_by_term.get(term["term"])
            if existing is not None and existing != term["canonical_form"]:
                raise book_synthesis.BookSynthesisError(
                    "Conflicting shard terminology blocks the merge."
                )
            terminology_by_term[term["term"]] = term["canonical_form"]
    operation_records = []
    for chapter_position, shard in enumerate(shards):
        for operation in shard["operations"]:
            protected = operation["protected_claim_ids"]
            occurrence_position = min(
                (claim_order[claim_id] for claim_id in protected),
                default=len(claim_order),
            )
            operation_records.append(
                (
                    chapter_position,
                    occurrence_position,
                    operation.get("kind", ""),
                    operation["operation_id"],
                    shard,
                    operation,
                )
            )
    operation_records.sort(key=lambda item: item[:4])
    merged_operations = []
    operation_map: dict[tuple[str, str], str] = {}
    for position, record in enumerate(operation_records, start=1):
        _, _, _, source_operation_id, shard, operation = record
        operation_id = f"OP{position}"
        operation_map[(shard["chapter_id"], source_operation_id)] = (
            operation_id
        )
        merged_operations.append(
            {
                **operation,
                "operation_id": operation_id,
                "source_shard_id": shard["shard_id"],
                "source_operation_id": source_operation_id,
                "source_shard_sha256": shard["plan_shard_sha256"],
            }
        )
    merged_terminology = []
    for shard in shards:
        for term in shard["terminology"]:
            merged_terminology.append(
                {
                    **term,
                    "operation_id": operation_map[
                        (shard["chapter_id"], term["operation_id"])
                    ],
                    "source_shard_id": shard["shard_id"],
                    "source_shard_sha256": shard["plan_shard_sha256"],
                }
            )
    thesis = context.get("provisional_thesis")
    plan = {
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "candidate_sha256": context["candidate_sha256"],
        "profile": context["profile"],
        "central_answer": (
            thesis.strip()
            if isinstance(thesis, str) and thesis.strip()
            else (
                "The available candidate preserves the question without "
                f"settling it: {context['central_question']}"
            )
        ),
        "central_answer_claim_ids": [
            claim["claim_id"] for claim in inventory["claims"]
        ],
        "central_answer_resolution_effect": (
            "unchanged"
            if isinstance(thesis, str) and thesis.strip()
            else "remains_open"
        ),
        "chapter_jobs": [
            {
                "chapter_id": shard["chapter_id"],
                "job": shard["chapter_job"],
                "source_shard_id": shard["shard_id"],
                "source_shard_sha256": shard["plan_shard_sha256"],
            }
            for shard in shards
        ],
        "operations": merged_operations,
        "terminology": merged_terminology,
        "protected_claim_ids": [
            claim["claim_id"] for claim in inventory["claims"]
        ],
        "plan_attempt": 1,
        "correction_audit_sha256": None,
        "claim_dispositions": [
            {
                **disposition,
                "source_shard_id": shard["shard_id"],
                "source_shard_sha256": shard["plan_shard_sha256"],
            }
            for shard in shards
            for disposition in shard["claim_dispositions"]
        ],
        "unresolved_decisions": [
            {
                **item,
                "source_shard_id": shard["shard_id"],
            }
            for shard in shards
            for item in shard["unresolved_decisions"]
        ],
        "cross_chapter_dependencies": [
            {
                **item,
                "source_shard_id": shard["shard_id"],
            }
            for shard in shards
            for item in shard["cross_chapter_dependencies"]
        ],
        "coverage_report": {
            "required_claim_count": len(inventory["claims"]),
            "disposed_claim_count": sum(
                len(shard["claim_dispositions"]) for shard in shards
            ),
            "missing_claim_ids": [],
            "duplicate_claim_ids": [],
        },
        "planning_protocol_version": book_synthesis.SHARDED_PLAN_PROTOCOL,
        "source_shards": [
            {
                "shard_id": shard["shard_id"],
                "input_hash": shard["input_hash"],
                "plan_shard_sha256": shard["plan_shard_sha256"],
            }
            for shard in shards
        ],
    }
    return save_plan_draft(project_path, plan)


def sharded_plan_audit_task(project_path: Path, book_id: str) -> dict:
    """Return the narrow global questions; the auditor cannot edit the plan."""
    context, _ = _load_staging(project_path, book_id)
    inventory = finalize_inventory(project_path, book_id)
    plan = load_plan_draft(project_path, book_id, 1)
    root = staging_directory(project_path, book_id)
    return {
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["staging_inventory_sha256"],
        "merged_plan_path": str(
            plan_draft_path(root, 1).relative_to(project_path.resolve())
        ),
        "merged_plan_sha256": plan["plan_draft_sha256"],
        "candidate_path": context["candidate_path"],
        "plan_input_index_path": str(
            (root / "plan_inputs" / "index.json").relative_to(
                project_path.resolve()
            )
        ),
        "questions": list(GLOBAL_PLAN_AUDIT_QUESTIONS),
        "allowed_verdicts": ["approve", "reject"],
        "editing_allowed": False,
    }


def read_sharded_plan_audit_input(
    project_path: Path,
    book_id: str,
    merged_plan_sha256: str,
) -> dict:
    """Return the exact hash-verified inputs for one global plan audit."""
    project_path = project_path.resolve()
    context, index = _load_staging(project_path, book_id)
    inventory = finalize_inventory(project_path, book_id)
    plan = load_plan_draft(project_path, book_id, 1)
    if (
        merged_plan_sha256 != plan["plan_draft_sha256"]
        or plan.get("context_sha256") != context["context_sha256"]
        or plan.get("inventory_staging_sha256")
        != inventory["staging_inventory_sha256"]
    ):
        raise book_synthesis.BookSynthesisError(
            "Requested global plan-audit input is stale."
        )

    candidate_path = book_synthesis.resolve_project_path(
        project_path,
        context["candidate_path"],
        "Synthesis candidate",
    )
    if book_synthesis.file_sha256(candidate_path) != context[
        "candidate_sha256"
    ]:
        raise book_synthesis.BookSynthesisError(
            "Global plan-audit candidate hash changed."
        )
    try:
        candidate_markdown = candidate_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise book_synthesis.BookSynthesisError(
            "Global plan-audit candidate could not be read."
        ) from error

    plan_inputs = [
        load_plan_shard_input(project_path, book_id, unit["unit_id"])
        for unit in index["units"]
    ]
    expected_inputs = {
        source["shard_id"]: source["input_hash"]
        for source in plan["source_shards"]
    }
    actual_inputs = {
        plan_input["shard_id"]: plan_input["input_hash"]
        for plan_input in plan_inputs
    }
    if expected_inputs != actual_inputs:
        raise book_synthesis.BookSynthesisError(
            "Global plan-audit shard inputs do not match the merged plan."
        )

    return {
        "schema_version": "1.0",
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["staging_inventory_sha256"],
        "merged_plan_sha256": plan["plan_draft_sha256"],
        "candidate_sha256": context["candidate_sha256"],
        "candidate_markdown": candidate_markdown,
        "merged_plan": plan,
        "plan_shard_inputs": plan_inputs,
        "questions": list(GLOBAL_PLAN_AUDIT_QUESTIONS),
    }


def save_sharded_plan_audit(project_path: Path, audit: dict) -> dict:
    """Save one terminal approve/reject global audit without a repair path."""
    project_path = project_path.resolve()
    book_id = audit.get("book_id")
    if not isinstance(book_id, str):
        raise book_synthesis.BookSynthesisError(
            "Sharded plan audit has no book."
        )
    allowed_fields = {
        "book_id",
        "context_sha256",
        "inventory_sha256",
        "merged_plan_sha256",
        "findings",
        "verdict",
        "auditor_host",
        "auditor_skill_sha256",
    }
    if set(audit) - allowed_fields:
        raise book_synthesis.BookSynthesisError(
            "Global plan audit cannot contain edits or replacement content."
        )
    task = sharded_plan_audit_task(project_path, book_id)
    findings = audit.get("findings")
    if (
        audit.get("context_sha256") != task["context_sha256"]
        or audit.get("inventory_sha256") != task["inventory_sha256"]
        or audit.get("merged_plan_sha256") != task["merged_plan_sha256"]
        or not isinstance(findings, list)
        or [item.get("question_id") for item in findings]
        != task["questions"]
        or any(
            item.get("status") not in {"pass", "fail"}
            or not isinstance(item.get("explanation"), str)
            or not item["explanation"].strip()
            for item in findings
        )
    ):
        raise book_synthesis.BookSynthesisError(
            "Sharded plan audit is stale or incomplete."
        )
    expected_verdict = (
        "reject"
        if any(item["status"] == "fail" for item in findings)
        else "approve"
    )
    if audit.get("verdict") != expected_verdict:
        raise book_synthesis.BookSynthesisError(
            "Sharded plan verdict does not match its findings."
        )
    document = {
        **audit,
        "schema_version": "2.0",
        "plan_attempt": 1,
        "plan_draft_sha256": task["merged_plan_sha256"],
        "overall_status": (
            "pass" if expected_verdict == "approve" else "blocking"
        ),
        "blocking_reasons": [
            item["question_id"]
            for item in findings
            if item["status"] == "fail"
        ],
        "audited_at": book_synthesis.current_utc_time(),
    }
    document["plan_audit_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "plan_audit_sha256",
        )
    )
    path = plan_audit_path(staging_directory(project_path, book_id), 1)
    if path.exists():
        existing = load_plan_audit(project_path, book_id, 1)
        comparable = {
            key: value
            for key, value in existing.items()
            if key not in {"audited_at", "plan_audit_sha256"}
        }
        requested = {
            key: value
            for key, value in document.items()
            if key not in {"audited_at", "plan_audit_sha256"}
        }
        if comparable != requested:
            raise book_synthesis.BookSynthesisError(
                "Global plan audit already exists with different content."
            )
        return existing
    book_synthesis.atomic_write_json(path, document)
    return document


def plan_task(project_path: Path, book_id: str) -> dict:
    context, index = _load_staging(project_path, book_id)
    inventory = finalize_inventory(project_path, book_id)
    root = staging_directory(project_path, book_id)
    attempt = next_plan_attempt(project_path, book_id)
    correction_audit = (
        load_plan_audit(project_path, book_id, 1)
        if attempt == 2
        else None
    )
    return {
        "book_id": book_id,
        "plan_attempt": attempt,
        "context_sha256": context["context_sha256"],
        "candidate_sha256": context["candidate_sha256"],
        "inventory_path": str(
            (root / "inventory.json").relative_to(project_path.resolve())
        ),
        "inventory_sha256": inventory["staging_inventory_sha256"],
        "index_path": str(
            (root / "index.json").relative_to(project_path.resolve())
        ),
        "unit_ids": [unit["unit_id"] for unit in index["units"]],
        "profile": context["profile"],
        "human_decision_artifacts": context.get(
            "human_decision_artifacts",
            [],
        ),
        "correction_audit_path": (
            None
            if correction_audit is None
            else str(
                plan_audit_path(root, 1).relative_to(
                    project_path.resolve()
                )
            )
        ),
        "correction_audit_sha256": (
            None
            if correction_audit is None
            else correction_audit["plan_audit_sha256"]
        ),
    }


def plan_draft_path(staging: Path, attempt: int) -> Path:
    return staging / "plans" / f"P{attempt}.json"


def plan_audit_path(staging: Path, attempt: int) -> Path:
    return staging / "plan_audits" / f"A{attempt}.json"


def load_plan_draft(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> dict:
    draft = book_synthesis.read_json_object(
        plan_draft_path(staging_directory(project_path, book_id), attempt),
        f"Plan draft P{attempt}",
    )
    if (
        draft.get("plan_attempt") != attempt
        or draft.get("plan_draft_sha256")
        != book_synthesis.canonical_json_sha256(
            draft,
            "plan_draft_sha256",
        )
    ):
        raise book_synthesis.BookSynthesisError(
            f"Plan draft P{attempt} hash does not match."
        )
    return draft


def load_plan_audit(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> dict:
    audit = book_synthesis.read_json_object(
        plan_audit_path(staging_directory(project_path, book_id), attempt),
        f"Plan audit A{attempt}",
    )
    if (
        audit.get("plan_attempt") != attempt
        or audit.get("plan_audit_sha256")
        != book_synthesis.canonical_json_sha256(
            audit,
            "plan_audit_sha256",
        )
    ):
        raise book_synthesis.BookSynthesisError(
            f"Plan audit A{attempt} hash does not match."
        )
    return audit


def next_plan_attempt(project_path: Path, book_id: str) -> int:
    root = staging_directory(project_path, book_id)
    if not plan_draft_path(root, 1).is_file():
        return 1
    if not plan_audit_path(root, 1).is_file():
        return 1
    first_audit = load_plan_audit(project_path, book_id, 1)
    if first_audit["overall_status"] == "pass":
        return 1
    if not plan_draft_path(root, 2).is_file():
        return 2
    return 2


def plan_audit_targets(plan: dict) -> list[dict]:
    targets = [
        {
            "target_id": "central_answer",
            "kind": "central_answer",
            "text": plan["central_answer"],
            "claim_ids": plan.get("central_answer_claim_ids", []),
            "resolution_effect": plan.get(
                "central_answer_resolution_effect"
            ),
        }
    ]
    for operation in plan["operations"]:
        targets.append(
            {
                "target_id": operation["operation_id"],
                "kind": "operation",
                "operation": operation,
            }
        )
    for index, entry in enumerate(plan["terminology"], start=1):
        targets.append(
            {
                "target_id": f"TERM{index}",
                "kind": "terminology",
                "terminology": entry,
            }
        )
    return targets


def save_plan_draft(project_path: Path, plan: dict) -> dict:
    """Save P1 or the sole P2 correction without promoting it."""
    project_path = project_path.resolve()
    book_id = plan.get("book_id")
    if not isinstance(book_id, str):
        raise book_synthesis.BookSynthesisError("Synthesis plan has no book.")
    context, _ = _load_staging(project_path, book_id)
    if not book_synthesis.is_audited_plan_protocol(
        context.get("protocol_version")
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan drafts require the audited-plan protocol."
        )
    attempt = next_plan_attempt(project_path, book_id)
    if plan.get("plan_attempt") != attempt:
        raise book_synthesis.BookSynthesisError("Plan attempt is out of order.")
    inventory = finalize_inventory(project_path, book_id)
    inventory_for_validation = {
        key: value
        for key, value in inventory.items()
        if key != "staging_inventory_sha256"
    }
    book_synthesis._validate_plan(
        plan,
        context,
        inventory_for_validation,
    )
    if attempt == 1 and plan.get("correction_audit_sha256") is not None:
        raise book_synthesis.BookSynthesisError(
            "First plan draft cannot cite a correction audit."
        )
    if attempt == 2:
        first_audit = load_plan_audit(project_path, book_id, 1)
        if (
            first_audit.get("overall_status") != "blocking"
            or plan.get("correction_audit_sha256")
            != first_audit.get("plan_audit_sha256")
        ):
            raise book_synthesis.BookSynthesisError(
                "Corrected plan is not bound to blocking audit A1."
            )
    document = {
        **plan,
        "schema_version": "2.0",
        "inventory_staging_sha256": inventory[
            "staging_inventory_sha256"
        ],
        "saved_at": book_synthesis.current_utc_time(),
    }
    document["plan_draft_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "plan_draft_sha256",
        )
    )
    path = plan_draft_path(staging_directory(project_path, book_id), attempt)
    if path.is_file():
        existing = load_plan_draft(project_path, book_id, attempt)
        comparable = {
            key: value
            for key, value in existing.items()
            if key not in {"saved_at", "plan_draft_sha256"}
        }
        requested = {
            key: value
            for key, value in document.items()
            if key not in {"saved_at", "plan_draft_sha256"}
        }
        if comparable != requested:
            raise book_synthesis.BookSynthesisError(
                f"Plan draft P{attempt} already differs."
            )
        return existing
    book_synthesis.atomic_write_json(path, document)
    return document


def plan_audit_task(project_path: Path, book_id: str) -> dict:
    context, _ = _load_staging(project_path, book_id)
    inventory = finalize_inventory(project_path, book_id)
    attempt = next_plan_attempt(project_path, book_id)
    plan = load_plan_draft(project_path, book_id, attempt)
    root = staging_directory(project_path, book_id)
    return {
        "book_id": book_id,
        "plan_attempt": attempt,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["staging_inventory_sha256"],
        "plan_draft_path": str(
            plan_draft_path(root, attempt).relative_to(
                project_path.resolve()
            )
        ),
        "plan_draft_sha256": plan["plan_draft_sha256"],
        "inventory_path": str(
            (root / "inventory.json").relative_to(project_path.resolve())
        ),
        "candidate_path": context["candidate_path"],
        "targets": plan_audit_targets(plan),
        "unresolved_questions": inventory["unresolved_questions"],
        "human_decision_artifacts": context.get(
            "human_decision_artifacts",
            [],
        ),
    }


def save_plan_audit(project_path: Path, audit: dict) -> dict:
    """Save one independent semantic audit for P1 or corrected P2."""
    project_path = project_path.resolve()
    book_id = audit.get("book_id")
    attempt = audit.get("plan_attempt")
    if not isinstance(book_id, str) or attempt not in {1, 2}:
        raise book_synthesis.BookSynthesisError(
            "Plan audit identity is invalid."
        )
    task = plan_audit_task(project_path, book_id)
    if (
        attempt != task["plan_attempt"]
        or audit.get("context_sha256") != task["context_sha256"]
        or audit.get("inventory_sha256") != task["inventory_sha256"]
        or audit.get("plan_draft_sha256") != task["plan_draft_sha256"]
    ):
        raise book_synthesis.BookSynthesisError("Plan audit is stale.")
    expected_targets = [item["target_id"] for item in task["targets"]]
    findings = audit.get("findings")
    if (
        not isinstance(findings, list)
        or [finding.get("target_id") for finding in findings]
        != expected_targets
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan audit must assess every target in order."
        )
    valid_classifications = {
        "candidate_supported",
        "human_authorized",
        "neutral_editorial",
        "preserves_unresolved",
        "unsupported",
    }
    for finding in findings:
        if (
            finding.get("classification") not in valid_classifications
            or not isinstance(finding.get("explanation"), str)
            or not finding["explanation"].strip()
        ):
            raise book_synthesis.BookSynthesisError(
                "Plan audit finding is invalid."
            )
    blockers = [
        finding["target_id"]
        for finding in findings
        if finding["classification"] == "unsupported"
    ]
    expected_status = "blocking" if blockers else "pass"
    if (
        audit.get("overall_status") != expected_status
        or audit.get("blocking_reasons") != blockers
    ):
        raise book_synthesis.BookSynthesisError(
            "Plan audit outcome does not match its findings."
        )
    document = {
        **audit,
        "schema_version": "1.0",
        "audited_at": book_synthesis.current_utc_time(),
    }
    document["plan_audit_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "plan_audit_sha256",
        )
    )
    path = plan_audit_path(
        staging_directory(project_path, book_id),
        attempt,
    )
    if path.is_file():
        existing = load_plan_audit(project_path, book_id, attempt)
        comparable = {
            key: value
            for key, value in existing.items()
            if key not in {"audited_at", "plan_audit_sha256"}
        }
        requested = {
            key: value
            for key, value in document.items()
            if key not in {"audited_at", "plan_audit_sha256"}
        }
        if comparable != requested:
            raise book_synthesis.BookSynthesisError(
                f"Plan audit A{attempt} already differs."
            )
        return existing
    book_synthesis.atomic_write_json(path, document)
    return document


def _promote_verified_plan(
    project_path: Path,
    plan: dict,
    plan_audit: dict | None = None,
) -> dict:
    """Validate one compact plan and atomically install the final foundation."""
    project_path = project_path.resolve()
    book_id = plan.get("book_id")
    if not isinstance(book_id, str):
        raise book_synthesis.BookSynthesisError("Synthesis plan has no book.")
    context, index = _load_staging(project_path, book_id)
    directory = staging_directory(project_path, book_id)
    inventory = finalize_inventory(project_path, book_id)
    inventory_for_foundation = {
        key: value
        for key, value in inventory.items()
        if key != "staging_inventory_sha256"
    }
    book_synthesis._validate_plan(plan, context, inventory_for_foundation)
    candidate_text = book_synthesis.resolve_project_path(
        project_path,
        context["candidate_path"],
        "Candidate book",
    ).read_text(encoding="utf-8")
    book_synthesis._validate_inventory(
        inventory_for_foundation,
        context,
        candidate_text,
    )
    if book_synthesis.is_audited_plan_protocol(
        context.get("protocol_version")
    ):
        if (
            plan_audit is None
            or plan_audit.get("overall_status") != "pass"
            or plan_audit.get("plan_draft_sha256")
            != plan.get("plan_draft_sha256")
        ):
            raise book_synthesis.BookSynthesisError(
                "Audited-plan foundation requires a passing plan audit."
            )
    final_directory = book_synthesis.synthesis_directory(
        project_path,
        book_id,
    )
    if final_directory.exists():
        promoted = _load_promoted(project_path, book_id)
        return {
            "book_id": book_id,
            "context_sha256": promoted[0]["context_sha256"],
            "inventory_sha256": promoted[1]["inventory_sha256"],
            "plan_sha256": promoted[2]["plan_sha256"],
        }

    inventory_for_foundation["inventory_sha256"] = (
        book_synthesis.canonical_json_sha256(
            inventory_for_foundation,
            "inventory_sha256",
        )
    )
    plan_document = dict(plan)
    plan_document["approved_plan_draft_sha256"] = plan_document.pop(
        "plan_draft_sha256",
        None,
    )
    plan_document["plan_audit_path"] = (
        None
        if plan_audit is None
        else str(
            (
                final_directory
                / "staged"
                / "plan_history"
                / f"A{plan['plan_attempt']}.json"
            ).relative_to(project_path)
        )
    )
    plan_document["plan_audit_sha256"] = (
        None
        if plan_audit is None
        else plan_audit["plan_audit_sha256"]
    )
    plan_document["inventory_sha256"] = inventory_for_foundation[
        "inventory_sha256"
    ]
    plan_document["plan_sha256"] = book_synthesis.canonical_json_sha256(
        plan_document,
        "plan_sha256",
    )
    index_document = dict(index)
    index_document["inventory_sha256"] = inventory_for_foundation[
        "inventory_sha256"
    ]
    index_document["plan_sha256"] = plan_document["plan_sha256"]
    index_document["index_sha256"] = book_synthesis.canonical_json_sha256(
        index_document,
        "index_sha256",
    )

    temporary = final_directory.with_name(
        f".synthesis-{uuid.uuid4().hex}"
    )
    temporary.mkdir(parents=True)
    try:
        _write_json(temporary / "context.json", context)
        _write_json(temporary / "inventory.json", inventory_for_foundation)
        _write_json(temporary / "plan.json", plan_document)
        shutil.copytree(directory / "packets", temporary / "staged" / "packets")
        shutil.copytree(directory / "locked", temporary / "staged" / "locked")
        shutil.copytree(
            directory / "inventory_units",
            temporary / "staged" / "inventory_units",
        )
        if (directory / "plans").is_dir():
            shutil.copytree(
                directory / "plans",
                temporary / "staged" / "plan_history" / "plans",
            )
        if (directory / "plan_audits").is_dir():
            shutil.copytree(
                directory / "plan_audits",
                temporary / "staged" / "plan_history",
                dirs_exist_ok=True,
            )
        _write_json(temporary / "staged" / "index.json", index_document)
        os.replace(temporary, final_directory)
    except OSError as error:
        shutil.rmtree(temporary, ignore_errors=True)
        raise book_synthesis.BookSynthesisError(
            "Staged synthesis foundation could not be promoted."
        ) from error
    return {
        "book_id": book_id,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory_for_foundation["inventory_sha256"],
        "plan_sha256": plan_document["plan_sha256"],
    }


def save_plan_and_promote(project_path: Path, plan: dict) -> dict:
    """Preserve the legacy direct-promotion behavior for cycle 001."""
    context, _ = _load_staging(project_path, plan.get("book_id"))
    if book_synthesis.is_audited_plan_protocol(
        context.get("protocol_version")
    ):
        raise book_synthesis.BookSynthesisError(
            "Audited-plan protocol must save and audit a plan draft first."
        )
    return _promote_verified_plan(project_path, plan)


def promote_approved_plan(project_path: Path, book_id: str) -> dict:
    """Promote only the latest independently approved plan draft."""
    attempt = next_plan_attempt(project_path, book_id)
    plan = load_plan_draft(project_path, book_id, attempt)
    audit = load_plan_audit(project_path, book_id, attempt)
    if audit.get("overall_status") != "pass":
        raise book_synthesis.BookSynthesisError(
            "Blocking plan audit cannot be promoted."
        )
    return _promote_verified_plan(project_path, plan, audit)


def _attempt_units_directory(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> Path:
    return (
        staged_directory(project_path, book_id)
        / "attempts"
        / f"S{attempt}"
        / "units"
    )


def load_correction(project_path: Path, book_id: str) -> dict | None:
    """Load and verify the sole finite S2 correction contract."""
    path = staged_directory(project_path, book_id) / "correction.json"
    if not path.is_file():
        return None
    document = book_synthesis.read_json_object(path, "Correction routing")
    unit_ids = document.get("unit_ids")
    if (
        document.get("book_id") != book_id
        or document.get("maximum_additional_attempts") != 1
        or not isinstance(unit_ids, list)
        or not unit_ids
        or len(unit_ids) != len(set(unit_ids))
        or any(
            not isinstance(unit_id, str)
            or not re.fullmatch(r"CH[1-9][0-9]*", unit_id)
            for unit_id in unit_ids
        )
        or document.get("correction_sha256")
        != book_synthesis.canonical_json_sha256(
            document,
            "correction_sha256",
        )
    ):
        raise book_synthesis.BookSynthesisError(
            "Correction contract is invalid."
        )
    if document.get("schema_version") == "2.0":
        targets = document.get("claim_targets")
        if not isinstance(targets, list):
            raise book_synthesis.BookSynthesisError(
                "Correction claim targets are invalid."
            )
        for target in targets:
            if (
                not isinstance(target, dict)
                or target.get("unit_id") not in unit_ids
                or target.get("allowed_unit_ids") != [target.get("unit_id")]
                or not isinstance(target.get("claim_id"), str)
                or not isinstance(target.get("failed_dimensions"), list)
                or not target["failed_dimensions"]
                or not isinstance(
                    target.get("authoritative_excerpt"),
                    str,
                )
                or not target["authoritative_excerpt"].strip()
                or not isinstance(target.get("offending_excerpt"), str)
                or not target["offending_excerpt"].strip()
                or target.get("required_outcome")
                != "restore_authoritative_claim_frame"
                or target.get("required_exact_excerpt") is not True
            ):
                raise book_synthesis.BookSynthesisError(
                    "Correction claim target is incomplete."
                )
    return document


def correction_unit_ids(project_path: Path, book_id: str) -> list[str]:
    document = load_correction(project_path, book_id)
    return [] if document is None else document["unit_ids"]


def required_edition_units(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> list[str]:
    _, _, _, index = _load_promoted(project_path, book_id)
    if attempt == 1:
        return [unit["unit_id"] for unit in index["units"]]
    units = correction_unit_ids(project_path, book_id)
    if not units:
        raise book_synthesis.BookSynthesisError(
            "Second synthesis attempt has no routed correction units."
        )
    return units


def next_edition_unit(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> str | None:
    directory = _attempt_units_directory(
        project_path,
        book_id,
        attempt,
    )
    for unit_id in required_edition_units(
        project_path,
        book_id,
        attempt,
    ):
        if not (directory / f"{unit_id}.json").is_file():
            return unit_id
    return None


def edition_task(
    project_path: Path,
    book_id: str,
    attempt: int,
    unit_id: str,
) -> dict:
    context, inventory, plan, index = _load_promoted(
        project_path,
        book_id,
    )
    unit = next(
        (item for item in index["units"] if item["unit_id"] == unit_id),
        None,
    )
    if unit is None or unit_id not in required_edition_units(
        project_path,
        book_id,
        attempt,
    ):
        raise book_synthesis.BookSynthesisError("Unknown edition unit.")
    root = staged_directory(project_path, book_id)
    claim_by_id = {
        claim["claim_id"]: claim for claim in inventory["claims"]
    }
    task = {
        "book_id": book_id,
        "attempt": attempt,
        "unit_id": unit_id,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["inventory_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "packet_path": str(
            (root / unit["packet_path"]).relative_to(project_path.resolve())
        ),
        "packet_sha256": unit["packet_sha256"],
        "assigned_claims": [
            claim_by_id[claim_id]
            for claim_id in inventory["unit_claim_ids"][unit_id]
        ],
        "operations": [
            operation
            for operation in plan["operations"]
            if operation["target"] == unit_id
            or set(operation["protected_claim_ids"]).intersection(
                inventory["unit_claim_ids"][unit_id]
            )
        ],
        "chapter_job": next(
            (
                job
                for job in plan["chapter_jobs"]
                if job.get("chapter_id") == unit_id
            ),
            {"chapter_id": unit_id, "job": unit["working_title"]},
        ),
        "terminology": plan["terminology"],
        "central_answer": plan["central_answer"],
        "correction_audit_path": (
            str(
                (
                    book_synthesis.attempt_directory(
                        project_path,
                        book_id,
                        1,
                    )
                    / "audit.json"
                ).relative_to(project_path.resolve())
            )
            if attempt == 2
            else None
        ),
        "correction_targets": [],
    }
    if attempt == 2:
        correction = load_correction(project_path, book_id)
        if correction is None:
            raise book_synthesis.BookSynthesisError(
                "Second synthesis attempt has no correction contract."
            )
        task["correction_sha256"] = correction["correction_sha256"]
        task["correction_targets"] = [
            target
            for target in correction.get("claim_targets", [])
            if target["unit_id"] == unit_id
        ]
    return task


def save_edition_unit(
    project_path: Path,
    submission: dict,
    markdown: str,
) -> dict:
    """Save one edition chapter and exact claim mappings, idempotently."""
    project_path = project_path.resolve()
    book_id = submission.get("book_id")
    attempt = submission.get("attempt")
    unit_id = submission.get("unit_id")
    if (
        not isinstance(book_id, str)
        or attempt not in {1, 2}
        or not isinstance(unit_id, str)
        or not isinstance(markdown, str)
        or not markdown.strip()
    ):
        raise book_synthesis.BookSynthesisError(
            "Edition unit submission is invalid."
        )
    task = edition_task(project_path, book_id, attempt, unit_id)
    for field in (
        "context_sha256",
        "inventory_sha256",
        "plan_sha256",
        "packet_sha256",
    ):
        if submission.get(field) != task[field]:
            raise book_synthesis.BookSynthesisError(
                "Edition unit foundation is stale."
            )
    expected_ids = [claim["claim_id"] for claim in task["assigned_claims"]]
    mappings = submission.get("claim_mappings")
    if (
        not isinstance(mappings, list)
        or [mapping.get("claim_id") for mapping in mappings] != expected_ids
    ):
        raise book_synthesis.BookSynthesisError(
            "Edition unit must map every assigned claim in order."
        )
    for mapping in mappings:
        if (
            not isinstance(mapping.get("edition_excerpt"), str)
            or not mapping["edition_excerpt"].strip()
            or mapping["edition_excerpt"] not in markdown
            or not isinstance(mapping.get("edition_location"), str)
            or not mapping["edition_location"].strip()
        ):
            raise book_synthesis.BookSynthesisError(
                "Edition unit claim mapping is invalid."
            )
    if attempt == 2:
        mappings_by_id = {
            mapping["claim_id"]: mapping for mapping in mappings
        }
        for target in task["correction_targets"]:
            mapping = mappings_by_id.get(target["claim_id"])
            if (
                mapping is None
                or mapping["edition_excerpt"]
                != target["authoritative_excerpt"]
            ):
                raise book_synthesis.BookSynthesisError(
                    "Edition correction did not restore the exact "
                    "authoritative claim."
                )
    summaries = submission.get("change_summary")
    if not isinstance(summaries, list) or any(
        not isinstance(value, str) or not value.strip() for value in summaries
    ):
        raise book_synthesis.BookSynthesisError(
            "Edition unit change summary is invalid."
        )
    directory = _attempt_units_directory(
        project_path,
        book_id,
        attempt,
    )
    metadata_path = directory / f"{unit_id}.json"
    markdown_path = directory / f"{unit_id}.md"
    document = {
        **submission,
        "schema_version": "1.0",
        "markdown_path": str(markdown_path.relative_to(project_path)),
        "markdown_sha256": book_synthesis.hashlib.sha256(
            markdown.encode("utf-8")
        ).hexdigest(),
        "saved_at": book_synthesis.current_utc_time(),
    }
    document["edition_unit_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "edition_unit_sha256",
        )
    )
    if metadata_path.exists() or markdown_path.exists():
        if not (metadata_path.is_file() and markdown_path.is_file()):
            raise book_synthesis.BookSynthesisError(
                "Edition unit is partially present."
            )
        existing = book_synthesis.read_json_object(
            metadata_path,
            "Edition unit",
        )
        if (
            existing.get("markdown_sha256")
            != book_synthesis.hashlib.sha256(
                markdown.encode("utf-8")
            ).hexdigest()
        ):
            raise book_synthesis.BookSynthesisError(
                "Edition unit already exists with different content."
            )
        return existing
    directory.mkdir(parents=True, exist_ok=True)
    book_synthesis.atomic_write_text(markdown_path, markdown)
    try:
        book_synthesis.atomic_write_json(metadata_path, document)
    except Exception:
        markdown_path.unlink(missing_ok=True)
        raise
    return document


def _selected_unit_paths(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> dict[str, tuple[Path, dict]]:
    _, _, _, index = _load_promoted(project_path, book_id)
    selected = {}
    correction = set(
        correction_unit_ids(project_path, book_id) if attempt == 2 else []
    )
    for unit in index["units"]:
        unit_id = unit["unit_id"]
        source_attempt = 2 if attempt == 2 and unit_id in correction else 1
        directory = _attempt_units_directory(
            project_path,
            book_id,
            source_attempt,
        )
        metadata = book_synthesis.read_json_object(
            directory / f"{unit_id}.json",
            f"{unit_id} edition unit",
        )
        markdown_path = directory / f"{unit_id}.md"
        if metadata.get("markdown_sha256") != book_synthesis.file_sha256(
            markdown_path
        ):
            raise book_synthesis.BookSynthesisError(
                f"{unit_id} edition unit hash does not match."
            )
        selected[unit_id] = (markdown_path, metadata)
    return selected


def assemble_attempt(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> dict:
    """Assemble saved units and install a legacy-compatible immutable attempt."""
    project_path = project_path.resolve()
    context, inventory, plan, index = _load_promoted(project_path, book_id)
    if next_edition_unit(project_path, book_id, attempt) is not None:
        raise book_synthesis.BookSynthesisError(
            "Cannot assemble before every required edition unit is saved."
        )
    selected = _selected_unit_paths(project_path, book_id, attempt)
    root = staged_directory(project_path, book_id)
    locked_by_id = {
        item["segment_id"]: item for item in index["locked_segments"]
    }
    parts: list[str] = []
    for item in index["assembly_sequence"]:
        if item["kind"] == "locked":
            record = locked_by_id[item["segment_id"]]
            path = root / record["path"]
            if book_synthesis.file_sha256(path) != record["sha256"]:
                raise book_synthesis.BookSynthesisError(
                    "Locked synthesis segment hash changed."
                )
            parts.append(path.read_text(encoding="utf-8"))
        else:
            path, _ = selected[item["unit_id"]]
            parts.append(path.read_text(encoding="utf-8"))
    markdown = "".join(parts)
    mapping_by_id = {}
    change_summary = []
    for unit_id in [unit["unit_id"] for unit in index["units"]]:
        _, metadata = selected[unit_id]
        for mapping in metadata["claim_mappings"]:
            mapping_by_id[mapping["claim_id"]] = mapping
        change_summary.extend(metadata["change_summary"])
    required_ids = [
        claim["claim_id"]
        for claim in inventory["claims"]
        if claim["required_in_edition"]
    ]
    if set(mapping_by_id) != set(required_ids):
        raise book_synthesis.BookSynthesisError(
            "Assembled edition does not map the complete inventory."
        )
    correction_hash = None
    if attempt == 2:
        first_audit = book_synthesis.read_json_object(
            book_synthesis.attempt_directory(project_path, book_id, 1)
            / "audit.json",
            "First synthesis audit",
        )
        correction_hash = first_audit["audit_sha256"]
    return book_synthesis.save_attempt(
        project_path,
        {
            "book_id": book_id,
            "attempt": attempt,
            "context_sha256": context["context_sha256"],
            "inventory_sha256": inventory["inventory_sha256"],
            "plan_sha256": plan["plan_sha256"],
            "correction_audit_sha256": correction_hash,
            "claim_mappings": [
                mapping_by_id[claim_id] for claim_id in required_ids
            ],
            "change_summary": _unique_strings(change_summary),
            "pipeline_version": PIPELINE_VERSION,
        },
        markdown,
    )


def audit_task(
    project_path: Path,
    book_id: str,
    attempt: int,
    unit_id: str,
) -> dict:
    context, inventory, plan, index = _load_promoted(
        project_path,
        book_id,
    )
    if unit_id not in [unit["unit_id"] for unit in index["units"]]:
        raise book_synthesis.BookSynthesisError("Unknown audit unit.")
    selected = _selected_unit_paths(project_path, book_id, attempt)
    edition_path, metadata = selected[unit_id]
    claim_by_id = {
        claim["claim_id"]: claim for claim in inventory["claims"]
    }
    return {
        "book_id": book_id,
        "attempt": attempt,
        "unit_id": unit_id,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["inventory_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "submission_path": str(
            (
                book_synthesis.attempt_directory(
                    project_path,
                    book_id,
                    attempt,
                )
                / "submission.json"
            ).relative_to(project_path.resolve())
        ),
        "candidate_packet_path": str(
            (
                staged_directory(project_path, book_id)
                / next(
                    unit["packet_path"]
                    for unit in index["units"]
                    if unit["unit_id"] == unit_id
                )
            ).relative_to(project_path.resolve())
        ),
        "edition_unit_path": str(
            edition_path.relative_to(project_path.resolve())
        ),
        "claims": [
            claim_by_id[claim_id]
            for claim_id in inventory["unit_claim_ids"][unit_id]
        ],
        "claim_mappings": metadata["claim_mappings"],
    }


def _audit_units_directory(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> Path:
    return (
        staged_directory(project_path, book_id)
        / "attempts"
        / f"S{attempt}"
        / "audits"
    )


def next_audit_unit(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> str | None:
    _, _, _, index = _load_promoted(project_path, book_id)
    directory = _audit_units_directory(
        project_path,
        book_id,
        attempt,
    )
    for unit in index["units"]:
        if not (directory / f"{unit['unit_id']}.json").is_file():
            return unit["unit_id"]
    return None


def save_audit_unit(project_path: Path, audit: dict) -> dict:
    """Save an independent claim audit for one edition unit."""
    project_path = project_path.resolve()
    book_id = audit.get("book_id")
    attempt = audit.get("attempt")
    unit_id = audit.get("unit_id")
    if (
        not isinstance(book_id, str)
        or attempt not in {1, 2}
        or not isinstance(unit_id, str)
    ):
        raise book_synthesis.BookSynthesisError("Audit unit identity is invalid.")
    _, inventory, _, _ = _load_promoted(project_path, book_id)
    task = audit_task(project_path, book_id, attempt, unit_id)
    for field in ("context_sha256", "inventory_sha256", "plan_sha256"):
        if audit.get(field) != task[field]:
            raise book_synthesis.BookSynthesisError(
                "Audit unit foundation is stale."
            )
    results = audit.get("claim_results")
    if (
        not isinstance(results, list)
        or [result.get("claim_id") for result in results]
        != [claim["claim_id"] for claim in task["claims"]]
    ):
        raise book_synthesis.BookSynthesisError(
            "Audit unit must assess every assigned claim in order."
        )
    candidate_text = (
        project_path / task["candidate_packet_path"]
    ).read_text(encoding="utf-8")
    edition_text = (
        project_path / task["edition_unit_path"]
    ).read_text(encoding="utf-8")
    claims = {claim["claim_id"]: claim for claim in task["claims"]}
    for result in results:
        claim = claims[result["claim_id"]]
        if (
            result.get("status") not in book_synthesis.CLAIM_STATUSES
            or result.get("candidate_excerpt") != claim["candidate_excerpt"]
            or result["candidate_excerpt"] not in candidate_text
            or not isinstance(result.get("certainty_changed"), bool)
            or not isinstance(result.get("scope_changed"), bool)
            or not isinstance(
                result.get("evidence_relationship_changed"),
                bool,
            )
            or not isinstance(result.get("explanation"), str)
            or not result["explanation"].strip()
        ):
            raise book_synthesis.BookSynthesisError(
                "Audit unit claim finding is invalid."
            )
        book_synthesis.validate_audit_changed_dimensions(
            result,
            required=book_synthesis.has_authority_contract(inventory),
        )
        if result["status"] == "omitted":
            if result.get("edition_excerpt") is not None:
                raise book_synthesis.BookSynthesisError(
                    "Omitted audit finding has an edition excerpt."
                )
        elif (
            not isinstance(result.get("edition_excerpt"), str)
            or result["edition_excerpt"] not in edition_text
        ):
            raise book_synthesis.BookSynthesisError(
                "Audit unit edition excerpt is invalid."
            )
    document = {
        **audit,
        "schema_version": "1.0",
        "audited_at": book_synthesis.current_utc_time(),
    }
    document["audit_unit_sha256"] = book_synthesis.canonical_json_sha256(
        document,
        "audit_unit_sha256",
    )
    path = (
        _audit_units_directory(project_path, book_id, attempt)
        / f"{unit_id}.json"
    )
    if path.exists():
        return book_synthesis.read_json_object(path, "Audit unit")
    book_synthesis.atomic_write_json(path, document)
    return document


def global_audit_task(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> dict:
    context, inventory, plan, index = _load_promoted(
        project_path,
        book_id,
    )
    _, submission = book_synthesis.load_attempt(
        project_path,
        book_id,
        attempt,
    )
    return {
        "book_id": book_id,
        "attempt": attempt,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["inventory_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "submission_sha256": submission["submission_sha256"],
        "book_sha256": submission["book_sha256"],
        "edition_path": submission["book_path"],
        "plan_path": str(
            (
                book_synthesis.synthesis_directory(project_path, book_id)
                / "plan.json"
            ).relative_to(project_path.resolve())
        ),
        "context_path": str(
            (
                book_synthesis.synthesis_directory(project_path, book_id)
                / "context.json"
            ).relative_to(project_path.resolve())
        ),
        "unit_ids": [unit["unit_id"] for unit in index["units"]],
    }


def save_global_audit(project_path: Path, audit: dict) -> dict:
    project_path = project_path.resolve()
    book_id = audit.get("book_id")
    attempt = audit.get("attempt")
    if not isinstance(book_id, str) or attempt not in {1, 2}:
        raise book_synthesis.BookSynthesisError(
            "Global audit identity is invalid."
        )
    task = global_audit_task(project_path, book_id, attempt)
    for field in (
        "context_sha256",
        "inventory_sha256",
        "plan_sha256",
        "submission_sha256",
        "book_sha256",
    ):
        if audit.get(field) != task[field]:
            raise book_synthesis.BookSynthesisError(
                "Global audit foundation is stale."
            )
    for field in (
        "unmapped_candidate_claims",
        "novel_edition_claims",
        "lost_limitations",
        "terminology_drift",
        "affected_unit_ids",
    ):
        values = audit.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise book_synthesis.BookSynthesisError(
                f"Global audit {field} are invalid."
            )
    if not set(audit["affected_unit_ids"]).issubset(set(task["unit_ids"])):
        raise book_synthesis.BookSynthesisError(
            "Global audit routes an unknown correction unit."
        )
    if not isinstance(audit.get("central_question_preserved"), bool):
        raise book_synthesis.BookSynthesisError(
            "Global audit central-question result is invalid."
        )
    curiosity = audit.get("curiosity_preservation")
    if (
        not isinstance(curiosity, dict)
        or curiosity.get("status") not in {"preserved", "distorted"}
        or not isinstance(curiosity.get("explanation"), str)
        or not curiosity["explanation"].strip()
    ):
        raise book_synthesis.BookSynthesisError(
            "Global audit curiosity finding is invalid."
        )
    document = {
        **audit,
        "schema_version": "1.0",
        "audited_at": book_synthesis.current_utc_time(),
    }
    document["global_audit_sha256"] = (
        book_synthesis.canonical_json_sha256(
            document,
            "global_audit_sha256",
        )
    )
    path = (
        staged_directory(project_path, book_id)
        / "attempts"
        / f"S{attempt}"
        / "global_audit.json"
    )
    if path.exists():
        return book_synthesis.read_json_object(path, "Global audit")
    book_synthesis.atomic_write_json(path, document)
    return document


def finalize_audit(project_path: Path, book_id: str, attempt: int) -> dict:
    """Merge unit and structural audits into the terminal legacy gate."""
    project_path = project_path.resolve()
    context, inventory, plan, index = _load_promoted(project_path, book_id)
    if next_audit_unit(project_path, book_id, attempt) is not None:
        raise book_synthesis.BookSynthesisError(
            "Cannot finalize before every audit unit is saved."
        )
    global_path = (
        staged_directory(project_path, book_id)
        / "attempts"
        / f"S{attempt}"
        / "global_audit.json"
    )
    global_audit = book_synthesis.read_json_object(
        global_path,
        "Global audit",
    )
    _, submission = book_synthesis.load_attempt(
        project_path,
        book_id,
        attempt,
    )
    results_by_id = {}
    claim_unit = {}
    for unit in index["units"]:
        unit_id = unit["unit_id"]
        document = book_synthesis.read_json_object(
            _audit_units_directory(project_path, book_id, attempt)
            / f"{unit_id}.json",
            f"{unit_id} audit",
        )
        for result in document["claim_results"]:
            results_by_id[result["claim_id"]] = result
            claim_unit[result["claim_id"]] = unit_id
    ordered_ids = [claim["claim_id"] for claim in inventory["claims"]]
    if set(results_by_id) != set(ordered_ids):
        raise book_synthesis.BookSynthesisError(
            "Merged audit does not cover the complete inventory."
        )
    audit = {
        "book_id": book_id,
        "attempt": attempt,
        "context_sha256": context["context_sha256"],
        "inventory_sha256": inventory["inventory_sha256"],
        "plan_sha256": plan["plan_sha256"],
        "submission_sha256": submission["submission_sha256"],
        "book_sha256": submission["book_sha256"],
        "claim_results": [results_by_id[claim_id] for claim_id in ordered_ids],
        "unmapped_candidate_claims": global_audit[
            "unmapped_candidate_claims"
        ],
        "novel_edition_claims": global_audit["novel_edition_claims"],
        "lost_limitations": global_audit["lost_limitations"],
        "terminology_drift": global_audit["terminology_drift"],
        "central_question_preserved": global_audit[
            "central_question_preserved"
        ],
        "curiosity_preservation": global_audit[
            "curiosity_preservation"
        ],
    }
    blockers = book_synthesis._audit_blockers(audit, inventory)
    audit["overall_status"] = "blocking" if blockers else "pass"
    audit["blocking_reasons"] = blockers
    result = book_synthesis.submit_audit(project_path, audit)
    if attempt == 1 and audit["overall_status"] == "blocking":
        affected = set(global_audit["affected_unit_ids"])
        for claim_id, finding in results_by_id.items():
            if (
                finding["status"] in book_synthesis.BLOCKING_CLAIM_STATUSES
                or finding["certainty_changed"]
                or finding["scope_changed"]
                or finding["evidence_relationship_changed"]
            ):
                affected.add(claim_unit[claim_id])
        if not affected:
            raise book_synthesis.BookSynthesisError(
                "Blocking first audit has no correction units."
            )
        ordered_units = [
            unit["unit_id"]
            for unit in index["units"]
            if unit["unit_id"] in affected
        ]
        claim_targets = []
        for claim_id in ordered_ids:
            finding = results_by_id[claim_id]
            if (
                finding["status"]
                not in book_synthesis.BLOCKING_CLAIM_STATUSES
                and not finding["certainty_changed"]
                and not finding["scope_changed"]
                and not finding["evidence_relationship_changed"]
            ):
                continue
            claim = next(
                item
                for item in inventory["claims"]
                if item["claim_id"] == claim_id
            )
            failed_dimensions = finding.get("changed_dimensions")
            if not failed_dimensions:
                failed_dimensions = []
                if finding["certainty_changed"]:
                    failed_dimensions.append("certainty")
                if finding["scope_changed"]:
                    failed_dimensions.append("scope")
                if finding["evidence_relationship_changed"]:
                    failed_dimensions.append("evidence_relationship")
                if not failed_dimensions:
                    failed_dimensions.append(
                        {
                            "omitted": "presence",
                            "contradicted": "contradiction",
                        }.get(finding["status"], "meaning")
                    )
            unit_id = claim_unit[claim_id]
            claim_targets.append(
                {
                    "target_id": (
                        f"{claim_id}:{finding['status']}:"
                        + ",".join(failed_dimensions)
                    ),
                    "claim_id": claim_id,
                    "unit_id": unit_id,
                    "status": finding["status"],
                    "failed_dimensions": failed_dimensions,
                    "authority_source": claim.get(
                        "authority_source",
                        (
                            "canonical_claim"
                            if claim["source_kind"]
                            == "canonical_occurrence"
                            else "candidate_excerpt"
                        ),
                    ),
                    "authority_ref": claim.get(
                        "authority_ref",
                        claim.get("canonical_claim_id")
                        or "candidate_excerpt",
                    ),
                    "authoritative_excerpt": claim[
                        "candidate_excerpt"
                    ],
                    "offending_excerpt": (
                        finding.get("edition_excerpt")
                        or "<omitted>"
                    ),
                    "required_outcome": (
                        "restore_authoritative_claim_frame"
                    ),
                    "required_exact_excerpt": True,
                    "allowed_unit_ids": [unit_id],
                    "protected_semantics": claim.get(
                        "protected_semantics"
                    ),
                }
            )
        correction = {
            "schema_version": "2.0",
            "book_id": book_id,
            "first_audit_sha256": result["audit_sha256"],
            "unit_ids": ordered_units,
            "claim_targets": claim_targets,
            "global_targets": {
                "unmapped_candidate_claims": global_audit[
                    "unmapped_candidate_claims"
                ],
                "novel_edition_claims": global_audit[
                    "novel_edition_claims"
                ],
                "lost_limitations": global_audit[
                    "lost_limitations"
                ],
                "terminology_drift": global_audit[
                    "terminology_drift"
                ],
                "central_question_preserved": global_audit[
                    "central_question_preserved"
                ],
                "curiosity_preservation": global_audit[
                    "curiosity_preservation"
                ],
                "affected_unit_ids": global_audit[
                    "affected_unit_ids"
                ],
            },
            "maximum_additional_attempts": 1,
        }
        correction["correction_sha256"] = (
            book_synthesis.canonical_json_sha256(
                correction,
                "correction_sha256",
            )
        )
        book_synthesis.atomic_write_json(
            staged_directory(project_path, book_id) / "correction.json",
            correction,
        )
    return result


def state(project_path: Path, book_id: str) -> str | None:
    """Derive a staged transition, or return None for a legacy synthesis."""
    project_path = project_path.resolve()
    final_directory = book_synthesis.synthesis_directory(
        project_path,
        book_id,
    )
    staging = staging_directory(project_path, book_id)
    if not final_directory.exists():
        if not staging.exists():
            return "not_started"
        _load_staging(project_path, book_id)
        provider_holds = sorted(
            (staging / "provider_holds").glob("*.json")
        )
        if provider_holds:
            if len(provider_holds) != 1:
                raise book_synthesis.BookSynthesisError(
                    "Synthesis has multiple terminal provider-stage holds."
                )
            return load_provider_stage_hold(provider_holds[0])["status"]
        if next_inventory_unit(project_path, book_id) is not None:
            return "inventory_required"
        if not (staging / "inventory.json").is_file():
            return "inventory_merge_required"
        context, _ = _load_staging(project_path, book_id)
        if (
            context.get("protocol_version")
            == book_synthesis.SHARDED_PLAN_PROTOCOL
        ):
            _, staged_index = _load_staging(project_path, book_id)
            if not (staging / "plan_inputs" / "index.json").is_file():
                return "plan_shard_inputs_required"
            if any(
                (
                    staging
                    / "plan_shard_holds"
                    / f"{unit['unit_id']}.json"
                ).is_file()
                for unit in staged_index["units"]
            ):
                return "plan_shard_held"
            if any(
                not plan_shard_path(
                    staging,
                    unit["unit_id"],
                ).is_file()
                for unit in staged_index["units"]
            ):
                return "plan_shards_required"
            if not plan_draft_path(staging, 1).is_file():
                return "plan_merge_required"
            if not plan_audit_path(staging, 1).is_file():
                return "plan_global_audit_required"
            audit = load_plan_audit(project_path, book_id, 1)
            if audit.get("verdict") == "approve":
                return "plan_promote_required"
            return "plan_held"
        if (
            book_synthesis.is_audited_plan_protocol(
                context.get("protocol_version")
            )
        ):
            first_plan = plan_draft_path(staging, 1)
            first_audit = plan_audit_path(staging, 1)
            second_plan = plan_draft_path(staging, 2)
            second_audit = plan_audit_path(staging, 2)
            if not first_plan.is_file():
                return "plan_required"
            if not first_audit.is_file():
                return "plan_audit_required"
            audit_one = load_plan_audit(project_path, book_id, 1)
            if audit_one["overall_status"] == "pass":
                return "plan_promote_required"
            if not second_plan.is_file():
                return "plan_correction_required"
            if not second_audit.is_file():
                return "plan_audit_required"
            audit_two = load_plan_audit(project_path, book_id, 2)
            if audit_two["overall_status"] == "pass":
                return "plan_promote_required"
            return "plan_held"
        return "plan_required"
    if not (final_directory / "staged" / "index.json").is_file():
        return None

    provider_holds = sorted(
        (
            staged_directory(project_path, book_id)
            / "provider_holds"
        ).glob("*.json")
    )
    if provider_holds:
        if len(provider_holds) != 1:
            raise book_synthesis.BookSynthesisError(
                "Synthesis has multiple terminal provider-stage holds."
            )
        return load_provider_stage_hold(provider_holds[0])["status"]

    legacy = book_synthesis.workflow_state_legacy(project_path, book_id)
    if legacy in {"ready_for_editorial", "synthesis_held", "receipt_recovery"}:
        return legacy
    if legacy in {"correction_required", "audit_S2"}:
        attempt = 2
    else:
        attempt = 1
    if next_edition_unit(project_path, book_id, attempt) is not None:
        return f"edition_units_S{attempt}"
    attempt_dir = book_synthesis.attempt_directory(
        project_path,
        book_id,
        attempt,
    )
    if not (attempt_dir / "book.md").is_file():
        return f"assembly_S{attempt}"
    if next_audit_unit(project_path, book_id, attempt) is not None:
        return f"audit_units_S{attempt}"
    global_path = (
        staged_directory(project_path, book_id)
        / "attempts"
        / f"S{attempt}"
        / "global_audit.json"
    )
    if not global_path.is_file():
        return f"global_audit_S{attempt}"
    if not (attempt_dir / "audit.json").is_file():
        return f"audit_finalize_S{attempt}"
    return book_synthesis.workflow_state_legacy(project_path, book_id)
