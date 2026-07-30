"""Durable, bounded, claim-preserving whole-book synthesis."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CLAIM_STATUSES = {
    "preserved",
    "clarified",
    "narrowed",
    "strengthened",
    "omitted",
    "contradicted",
    "meaning_changed",
}
BLOCKING_CLAIM_STATUSES = {
    "strengthened",
    "omitted",
    "contradicted",
    "meaning_changed",
}
PROFILE_NAME = "Proposed Alexandrian accessibility profile"
PROFILE_VERSION = "unversioned"
MAX_ATTEMPTS = 2
AUDITED_PLAN_PROTOCOL = "audited-plan-v2"
SHARDED_PLAN_PROTOCOL = "chapter-sharded-plan-v1"
DEFAULT_SYNTHESIS_PROTOCOL = SHARDED_PLAN_PROTOCOL
AUDITED_PLAN_PROTOCOLS = {
    AUDITED_PLAN_PROTOCOL,
    SHARDED_PLAN_PROTOCOL,
}
INVENTORY_AUTHORITY_TYPES = {
    "source_evidence",
    "user_statement",
    "human_decision",
    "project_record",
    "candidate_prose",
    "unknown",
}
INVENTORY_RESOLUTION_STATUSES = {
    "established",
    "qualified",
    "project_choice",
    "open",
    "routed",
    "unresolved",
    "contradicted",
}
INVENTORY_CLAIM_ROLES = {
    "research_claim",
    "observation",
    "hypothesis",
    "prediction",
    "project_choice",
    "definition",
    "question",
    "other",
}
INVENTORY_NORMATIVE_FORCES = {
    "none",
    "permission",
    "recommendation",
    "requirement",
}
INVENTORY_NORMATIVE_POLARITIES = {
    "not_applicable",
    "positive",
    "negative",
}
AUDIT_CHANGED_DIMENSIONS = {
    "normative_force",
    "certainty",
    "scope",
    "conditions",
    "attribution",
    "evidence_relationship",
    "resolution_status",
    "presence",
    "contradiction",
    "meaning",
}
PLAN_AUTHORITY_TYPES = {
    "candidate",
    "human_decision",
    "editorial_mechanics",
}
PLAN_EFFECT_TYPES = {
    "preserve",
    "clarify",
    "compress",
    "move",
    "merge",
    "rename",
    "resolve",
    "change_evidence_relationship",
}
PLAN_RESOLUTION_EFFECTS = {
    "unchanged",
    "remains_open",
    "resolved",
}


class BookSynthesisError(RuntimeError):
    """Reject stale, incomplete, unbounded, or meaning-changing synthesis."""


def is_audited_plan_protocol(value: object) -> bool:
    """Return whether a protocol requires an independent pre-edition audit."""
    return value in AUDITED_PLAN_PROTOCOLS


def has_authority_contract(inventory: dict) -> bool:
    """Return whether an inventory uses the protected-authority schema."""
    return inventory.get("schema_version") == "4.0"


def validate_inventory_claim_authority(
    claim: dict,
    canonical_claims: dict[str, dict],
    *,
    required: bool,
) -> None:
    """Reject a derived claim representation that conflicts with its source."""
    contract_fields = (
        "authority_source",
        "authority_ref",
        "authoritative_text",
        "normalized_summary",
        "protected_semantics",
    )
    if not required and not any(field in claim for field in contract_fields):
        return
    if any(field not in claim for field in contract_fields):
        raise BookSynthesisError(
            "Inventory claim authority contract is incomplete."
        )

    source_kind = claim.get("source_kind")
    canonical_id = (
        claim.get("canonical_claim_id")
        if source_kind == "canonical_occurrence"
        else claim.get("claim_id")
    )
    if source_kind in {"canonical", "canonical_occurrence"}:
        canonical = canonical_claims.get(canonical_id)
        expected_source = "canonical_claim"
        expected_ref = canonical_id
        expected_text = None if canonical is None else canonical.get("claim")
    elif source_kind == "candidate_only":
        expected_source = "candidate_excerpt"
        expected_ref = "candidate_excerpt"
        expected_text = claim.get("candidate_excerpt")
    else:
        raise BookSynthesisError(
            "Inventory claim has no supported authority source."
        )

    if (
        expected_text is None
        or claim.get("authority_source") != expected_source
        or claim.get("authority_ref") != expected_ref
        or claim.get("authoritative_text") != expected_text
        or claim.get("statement") != expected_text
        or not isinstance(claim.get("normalized_summary"), str)
        or not claim["normalized_summary"].strip()
    ):
        raise BookSynthesisError(
            "Inventory claim conflicts with its authoritative text."
        )

    protected = claim.get("protected_semantics")
    if not isinstance(protected, dict):
        raise BookSynthesisError(
            "Inventory claim protected semantics are invalid."
        )
    expected_fields = {
        "claim_role",
        "normative_force",
        "normative_polarity",
        "normative_surface_form",
        "certainty",
        "scope_and_conditions",
        "attribution",
        "evidence_relationship",
        "resolution_status",
    }
    if set(protected) != expected_fields:
        raise BookSynthesisError(
            "Inventory claim protected semantics are incomplete."
        )
    if (
        protected.get("claim_role") not in INVENTORY_CLAIM_ROLES
        or protected.get("normative_force") not in INVENTORY_NORMATIVE_FORCES
        or protected.get("normative_polarity")
        not in INVENTORY_NORMATIVE_POLARITIES
        or protected.get("certainty") != claim.get("certainty")
        or protected.get("scope_and_conditions")
        != claim.get("scope_and_conditions")
        or protected.get("attribution") != claim.get("attribution")
        or protected.get("evidence_relationship")
        != claim.get("evidence_relationship")
        or protected.get("resolution_status")
        != claim.get("resolution_status")
    ):
        raise BookSynthesisError(
            "Inventory claim protected semantics conflict with its frame."
        )

    force = protected["normative_force"]
    polarity = protected["normative_polarity"]
    surface = protected["normative_surface_form"]
    if force == "none":
        if polarity != "not_applicable" or surface is not None:
            raise BookSynthesisError(
                "A non-normative claim cannot carry normative wording."
            )
    elif (
        polarity not in {"positive", "negative"}
        or not isinstance(surface, str)
        or not surface.strip()
        or surface not in expected_text
    ):
        raise BookSynthesisError(
            "Normative force must cite exact authoritative wording."
        )


def validate_audit_changed_dimensions(
    result: dict,
    *,
    required: bool,
) -> None:
    """Validate the dimensions used to construct an exact correction target."""
    dimensions = result.get("changed_dimensions")
    if dimensions is None and not required:
        return
    if (
        not isinstance(dimensions, list)
        or len(dimensions) != len(set(dimensions))
        or not set(dimensions).issubset(AUDIT_CHANGED_DIMENSIONS)
    ):
        raise BookSynthesisError(
            "Audit changed dimensions are invalid."
        )
    status = result.get("status")
    if status in {"preserved", "clarified"} and dimensions:
        raise BookSynthesisError(
            "A preserved or clarified claim cannot record changed dimensions."
        )
    if status in BLOCKING_CLAIM_STATUSES and not dimensions:
        raise BookSynthesisError(
            "A blocking claim must identify its changed dimension."
        )
    if result.get("certainty_changed") and not set(dimensions).intersection(
        {"normative_force", "certainty", "resolution_status"}
    ):
        raise BookSynthesisError(
            "A certainty change must identify its protected dimension."
        )
    if result.get("scope_changed") and not set(dimensions).intersection(
        {"scope", "conditions"}
    ):
        raise BookSynthesisError(
            "A scope change must identify scope or conditions."
        )
    if (
        result.get("evidence_relationship_changed")
        and not set(dimensions).intersection(
            {"attribution", "evidence_relationship"}
        )
    ):
        raise BookSynthesisError(
            "An evidence change must identify its protected dimension."
        )


def current_utc_time() -> str:
    """Return an ISO 8601 timestamp in UTC."""
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    """Hash one immutable synthesis input or artifact."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise BookSynthesisError(f"Could not hash required file: {path}") from error


def canonical_json_sha256(document: dict, hash_field: str) -> str:
    """Hash one JSON object without its recorded digest."""
    value = {
        key: item for key, item in document.items() if key != hash_field
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_json_object(path: Path, label: str) -> dict:
    """Read one required JSON object."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise BookSynthesisError(f"{label} could not be read: {path}") from error
    if not isinstance(value, dict):
        raise BookSynthesisError(f"{label} must contain a JSON object.")
    return value


def resolve_project_path(
    project_path: Path,
    relative_path: str,
    label: str,
) -> Path:
    """Resolve one recorded project path without permitting escape."""
    if not isinstance(relative_path, str) or not relative_path:
        raise BookSynthesisError(f"{label} has no valid path.")
    project_path = project_path.resolve()
    path = (project_path / relative_path).resolve()
    if not path.is_relative_to(project_path):
        raise BookSynthesisError(f"{label} escapes the project directory.")
    return path


def atomic_write_json(path: Path, document: dict) -> None:
    """Atomically create one JSON artifact without overwriting it."""
    if path.exists():
        raise BookSynthesisError(f"Synthesis artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.link(temporary, path)
    except OSError as error:
        raise BookSynthesisError(
            f"Synthesis artifact could not be saved: {path}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically create one text artifact without overwriting it."""
    if path.exists():
        raise BookSynthesisError(f"Synthesis artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.link(temporary, path)
    except OSError as error:
        raise BookSynthesisError(
            f"Synthesis artifact could not be saved: {path}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)


def book_directory(project_path: Path, book_id: str) -> Path:
    """Return one validated book directory."""
    if not re.fullmatch(r"book-[A-Za-z0-9._-]+", book_id):
        raise BookSynthesisError("Book identity is invalid.")
    return project_path.resolve() / "data" / "books" / book_id


def legacy_synthesis_directory(project_path: Path, book_id: str) -> Path:
    """Return the original, cycle-001 synthesis directory."""
    return book_directory(project_path, book_id) / "synthesis"


def synthesis_cycle_directory(project_path: Path, book_id: str) -> Path:
    """Return the directory holding cycle records and later cycle roots."""
    return book_directory(project_path, book_id) / "synthesis_cycles"


def synthesis_cycle_index_path(project_path: Path, book_id: str) -> Path:
    return synthesis_cycle_directory(project_path, book_id) / "index.json"


def synthesis_cycle_successor_index_path(
    project_path: Path,
    book_id: str,
) -> Path:
    """Return the first append-only successor index for compatibility."""
    return synthesis_cycle_index_path_for_count(project_path, book_id, 3)


def synthesis_cycle_index_path_for_count(
    project_path: Path,
    book_id: str,
    cycle_count: int,
) -> Path:
    """Return the immutable index path that first records a cycle count."""
    if cycle_count < 2:
        raise BookSynthesisError("A cycle index requires at least two cycles.")
    root = synthesis_cycle_directory(project_path, book_id)
    if cycle_count == 2:
        return root / "index.json"
    return root / f"index-v{cycle_count - 1}.json"


def synthesis_cycle_index_chain(
    project_path: Path,
    book_id: str,
) -> list[Path]:
    """Discover one contiguous append-only index chain."""
    root = synthesis_cycle_directory(project_path, book_id)
    base = synthesis_cycle_index_path(project_path, book_id)
    if not base.is_file():
        unexpected = list(root.glob("index-v*.json")) if root.is_dir() else []
        if unexpected:
            raise BookSynthesisError(
                "Synthesis successor index exists without its base index."
            )
        return []

    versioned: dict[int, Path] = {}
    for path in root.glob("index-v*.json"):
        match = re.fullmatch(r"index-v([1-9][0-9]*)\.json", path.name)
        if match is None:
            raise BookSynthesisError(
                f"Unrecognized synthesis index filename: {path.name}."
            )
        version = int(match.group(1))
        if version < 2 or version in versioned:
            raise BookSynthesisError("Synthesis successor index is invalid.")
        versioned[version] = path
    if versioned:
        expected = list(range(2, max(versioned) + 1))
        if sorted(versioned) != expected:
            raise BookSynthesisError(
                "Synthesis successor index chain has a gap."
            )
    return [base, *(versioned[number] for number in sorted(versioned))]


def load_synthesis_cycle_index(
    project_path: Path,
    book_id: str,
) -> dict | None:
    """Load and verify the latest member of an append-only index chain."""
    project_path = project_path.resolve()
    chain = synthesis_cycle_index_chain(project_path, book_id)
    if not chain:
        return None
    prior_index = None
    prior_path = None
    for position, path in enumerate(chain, start=1):
        index = read_json_object(path, "Synthesis cycle index")
        cycles = index.get("cycles")
        expected_cycle_count = position + 1
        if (
            index.get("schema_version") != f"{position}.0"
            or index.get("book_id") != book_id
            or not isinstance(cycles, list)
            or len(cycles) != expected_cycle_count
            or [cycle.get("cycle_id") for cycle in cycles]
            != [
                f"cycle-{number:03d}"
                for number in range(1, expected_cycle_count + 1)
            ]
            or index.get("active_cycle_id") != cycles[-1].get("cycle_id")
            or index.get("cycle_index_sha256")
            != canonical_json_sha256(index, "cycle_index_sha256")
        ):
            raise BookSynthesisError("Synthesis cycle index is invalid.")
        if prior_index is None:
            if (
                index.get("prior_index_path") is not None
                or index.get("prior_index_sha256") is not None
            ):
                raise BookSynthesisError(
                    "Base cycle index cannot name a predecessor."
                )
        else:
            if (
                index.get("prior_index_path")
                != str(prior_path.relative_to(project_path))
                or index.get("prior_index_sha256") != file_sha256(prior_path)
                or cycles[:-2] != prior_index["cycles"][:-1]
            ):
                raise BookSynthesisError(
                    "Synthesis cycle index does not preserve its predecessor."
                )
            prior_active = prior_index["cycles"][-1]
            terminal_prior = cycles[-2]
            if (
                any(
                    terminal_prior.get(key) != value
                    for key, value in prior_active.items()
                )
                or not isinstance(
                    terminal_prior.get("terminal_status"),
                    str,
                )
            ):
                raise BookSynthesisError(
                    "Successor index changed its prior active cycle."
                )
        for cycle in cycles:
            root = resolve_project_path(
                project_path,
                cycle.get("root_path"),
                "Synthesis cycle root",
            )
            if not root.is_relative_to(
                book_directory(project_path, book_id)
            ):
                raise BookSynthesisError(
                    "Synthesis cycle root escapes its book."
                )
        prior_index = index
        prior_path = path
    return prior_index


def active_synthesis_cycle(
    project_path: Path,
    book_id: str,
) -> dict | None:
    index = load_synthesis_cycle_index(project_path, book_id)
    if index is None:
        return None
    return next(
        cycle
        for cycle in index["cycles"]
        if cycle["cycle_id"] == index["active_cycle_id"]
    )


def synthesis_directory(project_path: Path, book_id: str) -> Path:
    """Return the active synthesis directory without relocating cycle 001."""
    active = active_synthesis_cycle(project_path, book_id)
    if active is None:
        return legacy_synthesis_directory(project_path, book_id)
    return resolve_project_path(
        project_path,
        active["root_path"],
        "Active synthesis cycle",
    )


def authorize_fresh_synthesis_cycle(
    project_path: Path,
    book_id: str,
    reason: str,
    human_decision_artifacts: list[dict] | None = None,
) -> dict:
    """Authorize cycle 002 while preserving the terminal held cycle 001."""
    project_path = project_path.resolve()
    if not isinstance(reason, str) or not reason.strip():
        raise BookSynthesisError("Fresh-cycle authorization needs a reason.")
    if load_synthesis_cycle_index(project_path, book_id) is not None:
        raise BookSynthesisError("A fresh synthesis cycle is already authorized.")
    legacy_root = legacy_synthesis_directory(project_path, book_id)
    receipt_path = legacy_root / "receipt.json"
    receipt = read_json_object(receipt_path, "Prior synthesis receipt")
    if (
        receipt.get("book_id") != book_id
        or receipt.get("lifecycle_status") != "synthesis_held"
        or receipt.get("receipt_sha256")
        != canonical_json_sha256(receipt, "receipt_sha256")
    ):
        raise BookSynthesisError(
            "Fresh-cycle authorization requires a verified terminal hold."
        )
    receipt = verify_receipt(
        project_path,
        book_id,
        receipt["candidate_sha256"],
    )
    candidate_path = resolve_project_path(
        project_path,
        receipt.get("candidate_path"),
        "Held-cycle candidate",
    )
    if file_sha256(candidate_path) != receipt.get("candidate_sha256"):
        raise BookSynthesisError("Held-cycle candidate changed.")

    decisions = []
    for decision in human_decision_artifacts or []:
        if (
            not isinstance(decision, dict)
            or not isinstance(decision.get("decision_id"), str)
            or not decision["decision_id"].strip()
        ):
            raise BookSynthesisError("Human decision identity is invalid.")
        path = resolve_project_path(
            project_path,
            decision.get("path"),
            "Human synthesis decision",
        )
        if file_sha256(path) != decision.get("sha256"):
            raise BookSynthesisError("Human synthesis decision hash changed.")
        decisions.append(
            {
                "decision_id": decision["decision_id"],
                "path": str(path.relative_to(project_path)),
                "sha256": decision["sha256"],
            }
        )
    if len({item["decision_id"] for item in decisions}) != len(decisions):
        raise BookSynthesisError("Human synthesis decision identities repeat.")

    cycles_root = synthesis_cycle_directory(project_path, book_id)
    authorization_path = cycles_root / "authorizations" / "cycle-002.json"
    authorization = {
        "schema_version": "1.0",
        "book_id": book_id,
        "cycle_id": "cycle-002",
        "protocol_version": AUDITED_PLAN_PROTOCOL,
        "candidate_path": str(candidate_path.relative_to(project_path)),
        "candidate_sha256": receipt["candidate_sha256"],
        "prior_cycle_id": "cycle-001",
        "prior_cycle_root_path": str(legacy_root.relative_to(project_path)),
        "prior_terminal_receipt_path": str(
            receipt_path.relative_to(project_path)
        ),
        "prior_terminal_receipt_sha256": file_sha256(receipt_path),
        "reason": reason.strip(),
        "human_decision_artifacts": decisions,
        "maximum_synthesis_attempts": 2,
        "authorized_at": current_utc_time(),
    }
    authorization["authorization_sha256"] = canonical_json_sha256(
        authorization,
        "authorization_sha256",
    )
    atomic_write_json(authorization_path, authorization)

    cycle_two_root = cycles_root / "cycle-002"
    index = {
        "schema_version": "1.0",
        "book_id": book_id,
        "active_cycle_id": "cycle-002",
        "cycles": [
            {
                "cycle_id": "cycle-001",
                "protocol_version": "staged-v1",
                "root_path": str(legacy_root.relative_to(project_path)),
                "terminal_status": "synthesis_held",
                "terminal_receipt_path": str(
                    receipt_path.relative_to(project_path)
                ),
                "terminal_receipt_sha256": file_sha256(receipt_path),
            },
            {
                "cycle_id": "cycle-002",
                "protocol_version": AUDITED_PLAN_PROTOCOL,
                "root_path": str(cycle_two_root.relative_to(project_path)),
                "authorization_path": str(
                    authorization_path.relative_to(project_path)
                ),
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
            },
        ],
    }
    index["cycle_index_sha256"] = canonical_json_sha256(
        index,
        "cycle_index_sha256",
    )
    atomic_write_json(synthesis_cycle_index_path(project_path, book_id), index)
    return authorization


def authorize_sharded_synthesis_cycle(
    project_path: Path,
    book_id: str,
    reason: str,
) -> dict:
    """Authorize cycle 003 after cycle 002 exhausts monolithic planning."""
    project_path = project_path.resolve()
    if not isinstance(reason, str) or not reason.strip():
        raise BookSynthesisError("Sharded-cycle authorization needs a reason.")
    if synthesis_cycle_successor_index_path(project_path, book_id).exists():
        raise BookSynthesisError("Cycle 003 is already authorized.")
    index = load_synthesis_cycle_index(project_path, book_id)
    if (
        index is None
        or index.get("active_cycle_id") != "cycle-002"
        or len(index.get("cycles", [])) != 2
    ):
        raise BookSynthesisError(
            "Cycle 003 requires the immutable cycle-002 index."
        )
    cycle_two = index["cycles"][1]
    cycle_two_root = resolve_project_path(
        project_path,
        cycle_two.get("root_path"),
        "Cycle-002 root",
    )
    if cycle_two_root.exists():
        raise BookSynthesisError(
            "Cycle 003 cannot replace a promoted cycle-002 foundation."
        )
    cycle_two_staging = cycle_two_root.with_name(
        f"{cycle_two_root.name}_staging"
    )
    context_path = cycle_two_staging / "context.json"
    staged_index_path = cycle_two_staging / "index.json"
    inventory_path = cycle_two_staging / "inventory.json"
    context = read_json_object(context_path, "Cycle-002 context")
    staged_index = read_json_object(
        staged_index_path,
        "Cycle-002 staged index",
    )
    inventory = read_json_object(inventory_path, "Cycle-002 inventory")
    if (
        context.get("cycle_id") != "cycle-002"
        or context.get("protocol_version") != AUDITED_PLAN_PROTOCOL
        or context.get("context_sha256")
        != canonical_json_sha256(context, "context_sha256")
        or staged_index.get("context_sha256") != context["context_sha256"]
        or staged_index.get("index_sha256")
        != canonical_json_sha256(staged_index, "index_sha256")
        or inventory.get("context_sha256") != context["context_sha256"]
        or inventory.get("staging_inventory_sha256")
        != canonical_json_sha256(inventory, "staging_inventory_sha256")
    ):
        raise BookSynthesisError("Cycle-002 staged foundation is invalid.")
    if (
        (cycle_two_staging / "plans").exists()
        or (cycle_two_staging / "plan_audits").exists()
    ):
        raise BookSynthesisError(
            "Cycle 002 is not the expected no-P1 terminal boundary."
        )
    controller_path = (
        book_directory(project_path, book_id)
        / "runs"
        / "synthesis.controller.jsonl"
    )
    try:
        events = [
            json.loads(line)
            for line in controller_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise BookSynthesisError(
            "Cycle-002 provider-attempt ledger is unreadable."
        ) from error
    failures = [
        {
            "action_id": event.get("action_id"),
            "backend": event.get("backend"),
            "reason": event.get("reason"),
        }
        for event in events
        if event.get("event") == "contract_failed"
        and event.get("stage") == "whole-book-plan-P1"
    ]
    if (
        len(failures) != 2
        or len({failure["backend"] for failure in failures}) != 2
        or any(
            not isinstance(failure["action_id"], str)
            or not isinstance(failure["backend"], str)
            or not isinstance(failure["reason"], str)
            for failure in failures
        )
    ):
        raise BookSynthesisError(
            "Cycle 002 has not exhausted its two-provider P1 budget."
        )
    controller_snapshot_path = (
        synthesis_cycle_directory(project_path, book_id)
        / "terminal_records"
        / "cycle-002-controller.jsonl"
    )
    atomic_write_text(
        controller_snapshot_path,
        controller_path.read_text(encoding="utf-8"),
    )

    cycle_two_authorization = resolve_project_path(
        project_path,
        cycle_two.get("authorization_path"),
        "Cycle-002 authorization",
    )
    authorization_path = (
        synthesis_cycle_directory(project_path, book_id)
        / "authorizations"
        / "cycle-003.json"
    )
    authorization = {
        "schema_version": "1.0",
        "book_id": book_id,
        "cycle_id": "cycle-003",
        "protocol_version": SHARDED_PLAN_PROTOCOL,
        "candidate_path": context["candidate_path"],
        "candidate_sha256": context["candidate_sha256"],
        "prior_cycle_id": "cycle-002",
        "prior_cycle_authorization_path": str(
            cycle_two_authorization.relative_to(project_path)
        ),
        "prior_cycle_authorization_sha256": file_sha256(
            cycle_two_authorization
        ),
        "prior_context_path": str(context_path.relative_to(project_path)),
        "prior_context_sha256": file_sha256(context_path),
        "prior_staged_index_path": str(
            staged_index_path.relative_to(project_path)
        ),
        "prior_staged_index_sha256": file_sha256(staged_index_path),
        "prior_inventory_path": str(inventory_path.relative_to(project_path)),
        "prior_inventory_sha256": file_sha256(inventory_path),
        "prior_inventory_content_sha256": inventory[
            "staging_inventory_sha256"
        ],
        "prior_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "prior_controller_sha256": file_sha256(controller_snapshot_path),
        "exhausted_plan_failures": failures,
        "reason": reason.strip(),
        "human_decision_artifacts": context.get(
            "human_decision_artifacts",
            [],
        ),
        "planning_shard_count": len(staged_index["units"]),
        "maximum_provider_calls_per_shard": 2,
        "maximum_global_audit_calls": 1,
        "rejected_global_audit_policy": "terminal_hold",
        "authorized_at": current_utc_time(),
    }
    authorization["authorization_sha256"] = canonical_json_sha256(
        authorization,
        "authorization_sha256",
    )
    atomic_write_json(authorization_path, authorization)

    prior_index_path = synthesis_cycle_index_path(project_path, book_id)
    cycle_three_root = (
        synthesis_cycle_directory(project_path, book_id) / "cycle-003"
    )
    successor = {
        "schema_version": "2.0",
        "book_id": book_id,
        "prior_index_path": str(prior_index_path.relative_to(project_path)),
        "prior_index_sha256": file_sha256(prior_index_path),
        "active_cycle_id": "cycle-003",
        "cycles": [
            dict(index["cycles"][0]),
            {
                **index["cycles"][1],
                "terminal_status": "planning_transaction_failed",
                "terminal_context_path": str(
                    context_path.relative_to(project_path)
                ),
                "terminal_context_sha256": file_sha256(context_path),
                "terminal_inventory_path": str(
                    inventory_path.relative_to(project_path)
                ),
                "terminal_inventory_sha256": file_sha256(inventory_path),
                "terminal_controller_path": str(
                    controller_snapshot_path.relative_to(project_path)
                ),
                "terminal_controller_sha256": file_sha256(
                    controller_snapshot_path
                ),
                "terminal_plan_status": "absent",
                "terminal_edition_status": "absent",
            },
            {
                "cycle_id": "cycle-003",
                "protocol_version": SHARDED_PLAN_PROTOCOL,
                "root_path": str(cycle_three_root.relative_to(project_path)),
                "authorization_path": str(
                    authorization_path.relative_to(project_path)
                ),
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
            },
        ],
    }
    successor["cycle_index_sha256"] = canonical_json_sha256(
        successor,
        "cycle_index_sha256",
    )
    atomic_write_json(
        synthesis_cycle_successor_index_path(project_path, book_id),
        successor,
    )
    return authorization


def authorize_sharded_recovery_cycle(
    project_path: Path,
    book_id: str,
    reason: str,
) -> dict:
    """Authorize one append-only sharded successor after a shard hold."""
    project_path = project_path.resolve()
    if not isinstance(reason, str) or not reason.strip():
        raise BookSynthesisError(
            "Sharded recovery authorization needs a reason."
        )
    index = load_synthesis_cycle_index(project_path, book_id)
    if index is None or len(index.get("cycles", [])) < 3:
        raise BookSynthesisError(
            "A sharded recovery requires an existing sharded cycle."
        )
    prior_cycle = index["cycles"][-1]
    prior_cycle_id = prior_cycle["cycle_id"]
    if prior_cycle.get("protocol_version") != SHARDED_PLAN_PROTOCOL:
        raise BookSynthesisError(
            "Only a sharded cycle can create a sharded recovery."
        )
    verify_active_cycle_authorization(project_path, book_id)
    prior_root = resolve_project_path(
        project_path,
        prior_cycle.get("root_path"),
        "Prior sharded cycle",
    )
    if prior_root.exists():
        raise BookSynthesisError(
            "A promoted sharded cycle cannot be replaced by recovery."
        )
    prior_staging = prior_root.with_name(f"{prior_root.name}_staging")
    context_path = prior_staging / "context.json"
    staged_index_path = prior_staging / "index.json"
    inventory_path = prior_staging / "inventory.json"
    context = read_json_object(context_path, "Prior sharded context")
    staged_index = read_json_object(
        staged_index_path,
        "Prior sharded staged index",
    )
    inventory = read_json_object(
        inventory_path,
        "Prior sharded inventory",
    )
    if (
        context.get("cycle_id") != prior_cycle_id
        or context.get("protocol_version") != SHARDED_PLAN_PROTOCOL
        or context.get("context_sha256")
        != canonical_json_sha256(context, "context_sha256")
        or staged_index.get("context_sha256") != context["context_sha256"]
        or staged_index.get("index_sha256")
        != canonical_json_sha256(staged_index, "index_sha256")
        or inventory.get("context_sha256") != context["context_sha256"]
        or inventory.get("staging_inventory_sha256")
        != canonical_json_sha256(inventory, "staging_inventory_sha256")
    ):
        raise BookSynthesisError(
            "Prior sharded foundation is invalid."
        )

    hold_paths = sorted(
        (prior_staging / "plan_shard_holds").glob("*.json")
    )
    if len(hold_paths) != 1:
        raise BookSynthesisError(
            "Sharded recovery requires exactly one terminal shard hold."
        )
    hold_path = hold_paths[0]
    hold = read_json_object(hold_path, "Prior plan-shard hold")
    chapter_id = hold_path.stem
    consumed_backends = hold.get("consumed_backends")
    recorded_hold_cycle = hold.get("cycle_id")
    if (
        hold.get("book_id") != book_id
        or hold.get("chapter_id") != chapter_id
        or hold.get("stage") != f"plan-shard-{chapter_id}"
        or (
            recorded_hold_cycle != prior_cycle_id
            and not (
                prior_cycle_id == "cycle-003"
                and recorded_hold_cycle is None
            )
        )
        or hold.get("status") != "provider_budget_exhausted"
        or hold.get("maximum_provider_calls") != 2
        or hold.get("hold_sha256")
        != canonical_json_sha256(hold, "hold_sha256")
        or not isinstance(consumed_backends, list)
        or len(consumed_backends) != 2
        or any(
            not isinstance(backend, str) or not backend
            for backend in consumed_backends
        )
        or len(set(consumed_backends)) != 2
        or (prior_staging / "plan_shards" / f"{chapter_id}.json").exists()
    ):
        raise BookSynthesisError("Prior plan-shard hold is invalid.")
    plan_input = read_json_object(
        prior_staging / "plan_inputs" / f"{chapter_id}.json",
        "Held plan-shard input",
    )
    if (
        plan_input.get("book_id") != book_id
        or plan_input.get("chapter_id") != chapter_id
        or plan_input.get("input_hash") != hold.get("input_hash")
        or plan_input.get("input_hash")
        != canonical_json_sha256(plan_input, "input_hash")
    ):
        raise BookSynthesisError(
            "Prior plan-shard hold does not match its immutable input."
        )

    controller_path = (
        book_directory(project_path, book_id)
        / "runs"
        / "synthesis.controller.jsonl"
    )
    try:
        controller_text = controller_path.read_text(encoding="utf-8")
        events = [
            json.loads(line)
            for line in controller_text.splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise BookSynthesisError(
            "Prior sharded controller ledger is unreadable."
        ) from error
    stage_events = [
        event
        for event in events
        if event.get("stage") == hold.get("stage")
        and (
            event.get("cycle_id") == prior_cycle_id
            or (
                prior_cycle_id == "cycle-003"
                and event.get("cycle_id") is None
            )
        )
    ]
    stage_digest = hashlib.sha256(
        json.dumps(
            stage_events,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        not stage_events
        or hold.get("stage_event_sha256") != stage_digest
    ):
        raise BookSynthesisError(
            "Prior plan-shard hold is not bound to its controller events."
        )

    chain = synthesis_cycle_index_chain(project_path, book_id)
    prior_index_path = chain[-1]
    next_number = len(index["cycles"]) + 1
    next_cycle_id = f"cycle-{next_number:03d}"
    next_index_path = synthesis_cycle_index_path_for_count(
        project_path,
        book_id,
        next_number,
    )
    authorization_path = (
        synthesis_cycle_directory(project_path, book_id)
        / "authorizations"
        / f"{next_cycle_id}.json"
    )
    if next_index_path.exists() or authorization_path.exists():
        raise BookSynthesisError(
            f"{next_cycle_id} is already authorized."
        )
    controller_snapshot_path = (
        synthesis_cycle_directory(project_path, book_id)
        / "terminal_records"
        / f"{prior_cycle_id}-controller.jsonl"
    )
    if controller_snapshot_path.exists():
        raise BookSynthesisError(
            "Prior sharded controller snapshot already exists."
        )
    atomic_write_text(controller_snapshot_path, controller_text)

    prior_authorization_path = resolve_project_path(
        project_path,
        prior_cycle.get("authorization_path"),
        "Prior cycle authorization",
    )
    authorization = {
        "schema_version": "2.0",
        "book_id": book_id,
        "cycle_id": next_cycle_id,
        "protocol_version": SHARDED_PLAN_PROTOCOL,
        "transition_kind": "retry_after_plan_shard_hold",
        "candidate_path": context["candidate_path"],
        "candidate_sha256": context["candidate_sha256"],
        "prior_cycle_id": prior_cycle_id,
        "prior_cycle_authorization_path": str(
            prior_authorization_path.relative_to(project_path)
        ),
        "prior_cycle_authorization_sha256": file_sha256(
            prior_authorization_path
        ),
        "prior_index_path": str(
            prior_index_path.relative_to(project_path)
        ),
        "prior_index_sha256": file_sha256(prior_index_path),
        "prior_context_path": str(context_path.relative_to(project_path)),
        "prior_context_sha256": file_sha256(context_path),
        "prior_staged_index_path": str(
            staged_index_path.relative_to(project_path)
        ),
        "prior_staged_index_sha256": file_sha256(staged_index_path),
        "prior_inventory_path": str(
            inventory_path.relative_to(project_path)
        ),
        "prior_inventory_sha256": file_sha256(inventory_path),
        "prior_inventory_content_sha256": inventory[
            "staging_inventory_sha256"
        ],
        "prior_hold_path": str(hold_path.relative_to(project_path)),
        "prior_hold_sha256": file_sha256(hold_path),
        "prior_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "prior_controller_sha256": file_sha256(
            controller_snapshot_path
        ),
        "held_chapter_id": chapter_id,
        "exhausted_plan_failures": [
            {
                "backend": backend,
                "stage": hold["stage"],
            }
            for backend in consumed_backends
        ],
        "reason": reason.strip(),
        "human_decision_artifacts": context.get(
            "human_decision_artifacts",
            [],
        ),
        "planning_shard_count": len(staged_index["units"]),
        "maximum_provider_calls_per_shard": 2,
        "maximum_global_audit_calls": 1,
        "rejected_global_audit_policy": "terminal_hold",
        "authorized_at": current_utc_time(),
    }
    authorization["authorization_sha256"] = canonical_json_sha256(
        authorization,
        "authorization_sha256",
    )
    atomic_write_json(authorization_path, authorization)

    next_root = (
        synthesis_cycle_directory(project_path, book_id) / next_cycle_id
    )
    terminal_prior = {
        **prior_cycle,
        "terminal_status": "plan_shard_held",
        "terminal_context_path": str(
            context_path.relative_to(project_path)
        ),
        "terminal_context_sha256": file_sha256(context_path),
        "terminal_inventory_path": str(
            inventory_path.relative_to(project_path)
        ),
        "terminal_inventory_sha256": file_sha256(inventory_path),
        "terminal_hold_path": str(hold_path.relative_to(project_path)),
        "terminal_hold_sha256": file_sha256(hold_path),
        "terminal_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "terminal_controller_sha256": file_sha256(
            controller_snapshot_path
        ),
        "terminal_plan_status": "absent",
        "terminal_edition_status": "absent",
    }
    successor = {
        "schema_version": f"{next_number - 1}.0",
        "book_id": book_id,
        "prior_index_path": str(
            prior_index_path.relative_to(project_path)
        ),
        "prior_index_sha256": file_sha256(prior_index_path),
        "active_cycle_id": next_cycle_id,
        "cycles": [
            *index["cycles"][:-1],
            terminal_prior,
            {
                "cycle_id": next_cycle_id,
                "protocol_version": SHARDED_PLAN_PROTOCOL,
                "root_path": str(next_root.relative_to(project_path)),
                "authorization_path": str(
                    authorization_path.relative_to(project_path)
                ),
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
            },
        ],
    }
    successor["cycle_index_sha256"] = canonical_json_sha256(
        successor,
        "cycle_index_sha256",
    )
    atomic_write_json(next_index_path, successor)
    return authorization


def authorize_audit_recovery_cycle(
    project_path: Path,
    book_id: str,
    reason: str,
) -> dict:
    """Authorize one append-only successor for a held S1 unit audit."""
    import staged_synthesis

    project_path = project_path.resolve()
    if not isinstance(reason, str) or not reason.strip():
        raise BookSynthesisError(
            "Audit recovery authorization needs a reason."
        )
    index = load_synthesis_cycle_index(project_path, book_id)
    if index is None or len(index.get("cycles", [])) < 3:
        raise BookSynthesisError(
            "Audit recovery requires an existing sharded cycle."
        )
    prior_cycle = index["cycles"][-1]
    prior_cycle_id = prior_cycle["cycle_id"]
    if prior_cycle.get("protocol_version") != SHARDED_PLAN_PROTOCOL:
        raise BookSynthesisError(
            "Audit recovery requires a sharded synthesis cycle."
        )
    prior_authorization = verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    if prior_authorization is None:
        raise BookSynthesisError(
            "Audit recovery has no verified prior authorization."
        )
    if (
        prior_authorization.get("transition_kind")
        == "resume_after_audit_provider_hold"
    ):
        raise BookSynthesisError(
            "The single audit-recovery cycle is already consumed."
        )
    prior_root = resolve_project_path(
        project_path,
        prior_cycle.get("root_path"),
        "Prior audit-held cycle",
    )
    if not prior_root.is_dir():
        raise BookSynthesisError(
            "Audit recovery requires a promoted prior cycle."
        )
    receipt_path = prior_root / "receipt.json"
    if receipt_path.exists():
        raise BookSynthesisError(
            "A terminal synthesis receipt cannot be audit-recovered."
        )

    hold_paths = sorted(
        (prior_root / "staged" / "provider_holds").glob("*.json")
    )
    if len(hold_paths) != 1:
        raise BookSynthesisError(
            "Audit recovery requires exactly one provider-stage hold."
        )
    hold_path = hold_paths[0]
    hold = staged_synthesis.load_provider_stage_hold(hold_path)
    match = re.fullmatch(
        r"audit-S1-([A-Za-z0-9._-]+)",
        str(hold.get("stage")),
    )
    if (
        match is None
        or hold.get("book_id") != book_id
        or hold.get("cycle_id") != prior_cycle_id
        or hold.get("status") != "audit_provider_held"
        or hold.get("maximum_provider_calls") != 2
        or hold.get("target_created") is not False
    ):
        raise BookSynthesisError(
            "Only a verified held S1 unit audit can be recovered."
        )
    held_unit_id = match.group(1)
    target_path = resolve_project_path(
        project_path,
        hold.get("target_path"),
        "Held audit target",
    )
    expected_target = (
        prior_root
        / "staged"
        / "attempts"
        / "S1"
        / "audits"
        / f"{held_unit_id}.json"
    )
    if target_path != expected_target or target_path.exists():
        raise BookSynthesisError(
            "Held audit target does not match the prior cycle."
        )

    staged_index_path = prior_root / "staged" / "index.json"
    staged_index = read_json_object(
        staged_index_path,
        "Prior staged synthesis index",
    )
    if (
        staged_index.get("index_sha256")
        != canonical_json_sha256(staged_index, "index_sha256")
    ):
        raise BookSynthesisError(
            "Prior staged synthesis index is invalid."
        )
    unit_ids = [
        unit.get("unit_id")
        for unit in staged_index.get("units", [])
        if isinstance(unit, dict)
    ]
    if held_unit_id not in unit_ids or len(set(unit_ids)) != len(unit_ids):
        raise BookSynthesisError(
            "Held audit unit is not in the staged synthesis index."
        )
    held_position = unit_ids.index(held_unit_id)
    audit_directory = (
        prior_root / "staged" / "attempts" / "S1" / "audits"
    )
    for position, unit_id in enumerate(unit_ids):
        audit_exists = (audit_directory / f"{unit_id}.json").is_file()
        if audit_exists != (position < held_position):
            raise BookSynthesisError(
                "Prior unit-audit sequence is not a resumable prefix."
            )

    context_path = prior_root / "context.json"
    inventory_path = prior_root / "inventory.json"
    plan_path = prior_root / "plan.json"
    attempt_book_path = prior_root / "attempts" / "S1" / "book.md"
    submission_path = (
        prior_root / "attempts" / "S1" / "submission.json"
    )
    required_files = [
        context_path,
        inventory_path,
        plan_path,
        staged_index_path,
        attempt_book_path,
        submission_path,
    ]
    if any(not path.is_file() for path in required_files):
        raise BookSynthesisError(
            "Audit recovery foundation is incomplete."
        )
    context = read_json_object(context_path, "Prior synthesis context")
    inventory = read_json_object(
        inventory_path,
        "Prior synthesis inventory",
    )
    plan = read_json_object(plan_path, "Prior synthesis plan")
    submission = read_json_object(
        submission_path,
        "Prior S1 submission",
    )
    if (
        context.get("cycle_id") != prior_cycle_id
        or context.get("cycle_authorization_sha256")
        != prior_authorization["authorization_sha256"]
        or context.get("context_sha256")
        != canonical_json_sha256(context, "context_sha256")
        or inventory.get("context_sha256") != context["context_sha256"]
        or inventory.get("inventory_sha256")
        != canonical_json_sha256(inventory, "inventory_sha256")
        or plan.get("context_sha256") != context["context_sha256"]
        or plan.get("inventory_sha256") != inventory["inventory_sha256"]
        or plan.get("plan_sha256")
        != canonical_json_sha256(plan, "plan_sha256")
        or submission.get("book_sha256") != file_sha256(attempt_book_path)
        or submission.get("submission_sha256")
        != canonical_json_sha256(submission, "submission_sha256")
    ):
        raise BookSynthesisError(
            "Prior audit recovery foundation is stale."
        )

    controller_path = (
        book_directory(project_path, book_id)
        / "runs"
        / "synthesis.controller.jsonl"
    )
    try:
        controller_text = controller_path.read_text(encoding="utf-8")
        controller_events = [
            json.loads(line)
            for line in controller_text.splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError) as error:
        raise BookSynthesisError(
            "Audit recovery controller ledger is unreadable."
        ) from error
    stage_events = [
        event
        for event in controller_events
        if event.get("cycle_id") == prior_cycle_id
        and event.get("stage") == hold["stage"]
        and event.get("event") != "provider_hold_created"
    ]
    stage_digest = hashlib.sha256(
        json.dumps(
            stage_events,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if (
        len(hold.get("provider_attempts", [])) != 2
        or hold.get("stage_event_sha256") != stage_digest
    ):
        raise BookSynthesisError(
            "Audit hold is not bound to its provider attempts."
        )

    chain = synthesis_cycle_index_chain(project_path, book_id)
    prior_index_path = chain[-1]
    next_number = len(index["cycles"]) + 1
    next_cycle_id = f"cycle-{next_number:03d}"
    next_index_path = synthesis_cycle_index_path_for_count(
        project_path,
        book_id,
        next_number,
    )
    cycles_root = synthesis_cycle_directory(project_path, book_id)
    authorization_path = (
        cycles_root / "authorizations" / f"{next_cycle_id}.json"
    )
    next_root = cycles_root / next_cycle_id
    if (
        next_index_path.exists()
        or authorization_path.exists()
        or next_root.exists()
    ):
        raise BookSynthesisError(
            f"{next_cycle_id} audit recovery is already present."
        )
    controller_snapshot_path = (
        cycles_root
        / "terminal_records"
        / f"{prior_cycle_id}-controller.jsonl"
    )
    if controller_snapshot_path.exists():
        raise BookSynthesisError(
            "Prior audit-held controller snapshot already exists."
        )

    temporary = cycles_root / f".{next_cycle_id}-{uuid.uuid4().hex}"
    try:
        shutil.copytree(prior_root, temporary)
        shutil.rmtree(
            temporary / "staged" / "provider_holds",
            ignore_errors=True,
        )
        reused_artifacts = []
        for recovery_path in sorted(
            path for path in temporary.rglob("*") if path.is_file()
        ):
            relative = recovery_path.relative_to(temporary)
            prior_path = prior_root / relative
            digest = file_sha256(recovery_path)
            if not prior_path.is_file() or file_sha256(prior_path) != digest:
                raise BookSynthesisError(
                    "Audit recovery copy changed a reused artifact."
                )
            reused_artifacts.append(
                {
                    "relative_path": str(relative),
                    "sha256": digest,
                }
            )
        if not reused_artifacts:
            raise BookSynthesisError(
                "Audit recovery copied no immutable artifacts."
            )
        os.replace(temporary, next_root)
    except (OSError, BookSynthesisError) as error:
        shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(error, BookSynthesisError):
            raise
        raise BookSynthesisError(
            "Audit recovery cycle could not be installed."
        ) from error

    atomic_write_text(controller_snapshot_path, controller_text)
    prior_authorization_path = resolve_project_path(
        project_path,
        prior_cycle.get("authorization_path"),
        "Prior cycle authorization",
    )
    authorization = {
        "schema_version": "3.0",
        "book_id": book_id,
        "cycle_id": next_cycle_id,
        "protocol_version": SHARDED_PLAN_PROTOCOL,
        "transition_kind": "resume_after_audit_provider_hold",
        "candidate_path": context["candidate_path"],
        "candidate_sha256": context["candidate_sha256"],
        "prior_cycle_id": prior_cycle_id,
        "prior_cycle_root_path": str(
            prior_root.relative_to(project_path)
        ),
        "prior_cycle_authorization_path": str(
            prior_authorization_path.relative_to(project_path)
        ),
        "prior_cycle_authorization_sha256": file_sha256(
            prior_authorization_path
        ),
        "foundation_cycle_authorization_sha256": prior_authorization[
            "authorization_sha256"
        ],
        "prior_index_path": str(prior_index_path.relative_to(project_path)),
        "prior_index_sha256": file_sha256(prior_index_path),
        "prior_context_path": str(context_path.relative_to(project_path)),
        "prior_context_sha256": file_sha256(context_path),
        "prior_staged_index_path": str(
            staged_index_path.relative_to(project_path)
        ),
        "prior_staged_index_sha256": file_sha256(staged_index_path),
        "prior_inventory_path": str(
            inventory_path.relative_to(project_path)
        ),
        "prior_inventory_sha256": file_sha256(inventory_path),
        "prior_plan_path": str(plan_path.relative_to(project_path)),
        "prior_plan_sha256": file_sha256(plan_path),
        "prior_attempt_book_path": str(
            attempt_book_path.relative_to(project_path)
        ),
        "prior_attempt_book_sha256": file_sha256(attempt_book_path),
        "prior_submission_path": str(
            submission_path.relative_to(project_path)
        ),
        "prior_submission_sha256": file_sha256(submission_path),
        "prior_hold_path": str(hold_path.relative_to(project_path)),
        "prior_hold_sha256": file_sha256(hold_path),
        "prior_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "prior_controller_sha256": file_sha256(
            controller_snapshot_path
        ),
        "held_stage": hold["stage"],
        "held_unit_id": held_unit_id,
        "resumed_attempt": 1,
        "preserved_audit_unit_ids": unit_ids[:held_position],
        "reused_artifacts": reused_artifacts,
        "reason": reason.strip(),
        "human_decision_artifacts": context.get(
            "human_decision_artifacts",
            [],
        ),
        "maximum_provider_calls_for_held_stage": 2,
        "maximum_synthesis_attempts": 2,
        "authorized_at": current_utc_time(),
    }
    authorization["authorization_sha256"] = canonical_json_sha256(
        authorization,
        "authorization_sha256",
    )
    atomic_write_json(authorization_path, authorization)

    terminal_prior = {
        **prior_cycle,
        "terminal_status": "audit_provider_held",
        "terminal_context_path": str(context_path.relative_to(project_path)),
        "terminal_context_sha256": file_sha256(context_path),
        "terminal_inventory_path": str(
            inventory_path.relative_to(project_path)
        ),
        "terminal_inventory_sha256": file_sha256(inventory_path),
        "terminal_plan_path": str(plan_path.relative_to(project_path)),
        "terminal_plan_sha256": file_sha256(plan_path),
        "terminal_attempt_book_path": str(
            attempt_book_path.relative_to(project_path)
        ),
        "terminal_attempt_book_sha256": file_sha256(attempt_book_path),
        "terminal_submission_path": str(
            submission_path.relative_to(project_path)
        ),
        "terminal_submission_sha256": file_sha256(submission_path),
        "terminal_hold_path": str(hold_path.relative_to(project_path)),
        "terminal_hold_sha256": file_sha256(hold_path),
        "terminal_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "terminal_controller_sha256": file_sha256(
            controller_snapshot_path
        ),
        "terminal_plan_status": "approved",
        "terminal_edition_status": "S1_assembled",
    }
    successor = {
        "schema_version": f"{next_number - 1}.0",
        "book_id": book_id,
        "prior_index_path": str(prior_index_path.relative_to(project_path)),
        "prior_index_sha256": file_sha256(prior_index_path),
        "active_cycle_id": next_cycle_id,
        "cycles": [
            *index["cycles"][:-1],
            terminal_prior,
            {
                "cycle_id": next_cycle_id,
                "protocol_version": SHARDED_PLAN_PROTOCOL,
                "root_path": str(next_root.relative_to(project_path)),
                "authorization_path": str(
                    authorization_path.relative_to(project_path)
                ),
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
            },
        ],
    }
    successor["cycle_index_sha256"] = canonical_json_sha256(
        successor,
        "cycle_index_sha256",
    )
    atomic_write_json(next_index_path, successor)
    return authorization


def authorize_terminal_synthesis_successor_cycle(
    project_path: Path,
    book_id: str,
    reason: str,
) -> dict:
    """Authorize one fresh cycle after an immutable terminal synthesis hold."""
    project_path = project_path.resolve()
    if not isinstance(reason, str) or not reason.strip():
        raise BookSynthesisError(
            "Terminal-successor authorization needs a reason."
        )
    index = load_synthesis_cycle_index(project_path, book_id)
    if index is None or len(index.get("cycles", [])) < 3:
        raise BookSynthesisError(
            "A terminal successor requires an existing sharded cycle."
        )
    prior_cycle = index["cycles"][-1]
    prior_cycle_id = prior_cycle["cycle_id"]
    if prior_cycle.get("protocol_version") != SHARDED_PLAN_PROTOCOL:
        raise BookSynthesisError(
            "A terminal successor requires a sharded prior cycle."
        )
    prior_authorization = verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    if prior_authorization is None:
        raise BookSynthesisError(
            "The prior cycle has no verified authorization."
        )
    prior_root = resolve_project_path(
        project_path,
        prior_cycle.get("root_path"),
        "Prior terminal cycle",
    )
    receipt_path = prior_root / "receipt.json"
    if not receipt_path.is_file():
        raise BookSynthesisError(
            "A terminal successor requires a synthesis receipt."
        )
    raw_receipt = read_json_object(receipt_path, "Prior synthesis receipt")
    if raw_receipt.get("lifecycle_status") != "synthesis_held":
        raise BookSynthesisError(
            "Only a held synthesis can authorize a fresh successor."
        )
    receipt = verify_receipt(
        project_path,
        book_id,
        raw_receipt.get("candidate_sha256"),
    )
    if (
        receipt.get("cycle_id") != prior_cycle_id
        or receipt.get("receipt_sha256")
        != canonical_json_sha256(receipt, "receipt_sha256")
    ):
        raise BookSynthesisError(
            "Prior terminal receipt does not match the active cycle."
        )
    context_path = prior_root / "context.json"
    inventory_path = prior_root / "inventory.json"
    staged_index_path = prior_root / "staged" / "index.json"
    context = read_json_object(context_path, "Prior terminal context")
    inventory = read_json_object(inventory_path, "Prior terminal inventory")
    staged_index = read_json_object(
        staged_index_path,
        "Prior terminal staged index",
    )
    if (
        context.get("candidate_sha256") != receipt["candidate_sha256"]
        or context.get("context_sha256")
        != canonical_json_sha256(context, "context_sha256")
        or inventory.get("context_sha256") != context["context_sha256"]
        or inventory.get("inventory_sha256")
        != canonical_json_sha256(inventory, "inventory_sha256")
        or staged_index.get("context_sha256") != context["context_sha256"]
        or staged_index.get("index_sha256")
        != canonical_json_sha256(staged_index, "index_sha256")
    ):
        raise BookSynthesisError(
            "Prior terminal foundation is invalid."
        )

    chain = synthesis_cycle_index_chain(project_path, book_id)
    prior_index_path = chain[-1]
    next_number = len(index["cycles"]) + 1
    next_cycle_id = f"cycle-{next_number:03d}"
    next_index_path = synthesis_cycle_index_path_for_count(
        project_path,
        book_id,
        next_number,
    )
    authorization_path = (
        synthesis_cycle_directory(project_path, book_id)
        / "authorizations"
        / f"{next_cycle_id}.json"
    )
    if next_index_path.exists() or authorization_path.exists():
        raise BookSynthesisError(
            f"{next_cycle_id} is already authorized."
        )
    controller_path = (
        book_directory(project_path, book_id)
        / "runs"
        / "synthesis.controller.jsonl"
    )
    try:
        controller_text = controller_path.read_text(encoding="utf-8")
    except OSError as error:
        raise BookSynthesisError(
            "Prior terminal controller ledger is unreadable."
        ) from error
    controller_snapshot_path = (
        synthesis_cycle_directory(project_path, book_id)
        / "terminal_records"
        / f"{prior_cycle_id}-controller.jsonl"
    )
    if controller_snapshot_path.exists():
        raise BookSynthesisError(
            "Prior terminal controller snapshot already exists."
        )
    atomic_write_text(controller_snapshot_path, controller_text)

    prior_authorization_path = resolve_project_path(
        project_path,
        prior_cycle.get("authorization_path"),
        "Prior cycle authorization",
    )
    authorization = {
        "schema_version": "4.0",
        "book_id": book_id,
        "cycle_id": next_cycle_id,
        "protocol_version": SHARDED_PLAN_PROTOCOL,
        "transition_kind": "fresh_after_terminal_hold",
        "inventory_policy": "regenerate_required",
        "candidate_path": receipt["candidate_path"],
        "candidate_sha256": receipt["candidate_sha256"],
        "prior_cycle_id": prior_cycle_id,
        "prior_cycle_authorization_path": str(
            prior_authorization_path.relative_to(project_path)
        ),
        "prior_cycle_authorization_sha256": file_sha256(
            prior_authorization_path
        ),
        "prior_index_path": str(prior_index_path.relative_to(project_path)),
        "prior_index_sha256": file_sha256(prior_index_path),
        "prior_terminal_receipt_path": str(
            receipt_path.relative_to(project_path)
        ),
        "prior_terminal_receipt_sha256": file_sha256(receipt_path),
        "prior_context_path": str(context_path.relative_to(project_path)),
        "prior_context_sha256": file_sha256(context_path),
        "prior_staged_index_path": str(
            staged_index_path.relative_to(project_path)
        ),
        "prior_staged_index_sha256": file_sha256(staged_index_path),
        "prior_inventory_path": str(
            inventory_path.relative_to(project_path)
        ),
        "prior_inventory_sha256": file_sha256(inventory_path),
        "prior_inventory_content_sha256": inventory["inventory_sha256"],
        "prior_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "prior_controller_sha256": file_sha256(
            controller_snapshot_path
        ),
        "reason": reason.strip(),
        "human_decision_artifacts": context.get(
            "human_decision_artifacts",
            [],
        ),
        "planning_shard_count": len(staged_index["units"]),
        "maximum_provider_calls_per_stage": 2,
        "maximum_synthesis_attempts": 2,
        "authorized_at": current_utc_time(),
    }
    authorization["authorization_sha256"] = canonical_json_sha256(
        authorization,
        "authorization_sha256",
    )
    atomic_write_json(authorization_path, authorization)

    next_root = synthesis_cycle_directory(
        project_path,
        book_id,
    ) / next_cycle_id
    terminal_prior = {
        **prior_cycle,
        "terminal_status": "synthesis_held",
        "terminal_receipt_path": str(receipt_path.relative_to(project_path)),
        "terminal_receipt_sha256": file_sha256(receipt_path),
        "terminal_context_path": str(context_path.relative_to(project_path)),
        "terminal_context_sha256": file_sha256(context_path),
        "terminal_inventory_path": str(
            inventory_path.relative_to(project_path)
        ),
        "terminal_inventory_sha256": file_sha256(inventory_path),
        "terminal_controller_path": str(
            controller_snapshot_path.relative_to(project_path)
        ),
        "terminal_controller_sha256": file_sha256(
            controller_snapshot_path
        ),
    }
    successor = {
        "schema_version": f"{next_number - 1}.0",
        "book_id": book_id,
        "prior_index_path": str(prior_index_path.relative_to(project_path)),
        "prior_index_sha256": file_sha256(prior_index_path),
        "active_cycle_id": next_cycle_id,
        "cycles": [
            *index["cycles"][:-1],
            terminal_prior,
            {
                "cycle_id": next_cycle_id,
                "protocol_version": SHARDED_PLAN_PROTOCOL,
                "root_path": str(next_root.relative_to(project_path)),
                "authorization_path": str(
                    authorization_path.relative_to(project_path)
                ),
                "authorization_sha256": authorization[
                    "authorization_sha256"
                ],
            },
        ],
    }
    successor["cycle_index_sha256"] = canonical_json_sha256(
        successor,
        "cycle_index_sha256",
    )
    atomic_write_json(next_index_path, successor)
    return authorization


def verify_active_cycle_authorization(
    project_path: Path,
    book_id: str,
) -> dict | None:
    """Verify the active cycle and the immutable held-cycle boundary."""
    active = active_synthesis_cycle(project_path, book_id)
    if active is None:
        return None
    path = resolve_project_path(
        project_path,
        active.get("authorization_path"),
        "Cycle authorization",
    )
    authorization = read_json_object(path, "Cycle authorization")
    candidate = resolve_project_path(
        project_path,
        authorization.get("candidate_path"),
        "Cycle candidate",
    )
    if (
        authorization.get("book_id") != book_id
        or authorization.get("cycle_id") != active.get("cycle_id")
        or authorization.get("protocol_version")
        != active.get("protocol_version")
        or authorization.get("authorization_sha256")
        != canonical_json_sha256(authorization, "authorization_sha256")
        or authorization.get("authorization_sha256")
        != active.get("authorization_sha256")
        or file_sha256(candidate) != authorization.get("candidate_sha256")
    ):
        raise BookSynthesisError("Synthesis-cycle authorization is stale.")
    if authorization.get("cycle_id") == "cycle-002":
        prior_receipt = resolve_project_path(
            project_path,
            authorization.get("prior_terminal_receipt_path"),
            "Prior terminal receipt",
        )
        if file_sha256(prior_receipt) != authorization.get(
            "prior_terminal_receipt_sha256"
        ):
            raise BookSynthesisError(
                "Cycle-002 terminal receipt changed."
            )
    elif re.fullmatch(
        r"cycle-[0-9]{3,}",
        str(authorization.get("cycle_id")),
    ) and int(authorization["cycle_id"].split("-")[1]) >= 3:
        required_prior_artifacts = [
            (
                "prior_cycle_authorization_path",
                "prior_cycle_authorization_sha256",
                "Prior cycle authorization",
            ),
            ("prior_context_path", "prior_context_sha256", "Prior context"),
            (
                "prior_staged_index_path",
                "prior_staged_index_sha256",
                "Prior staged index",
            ),
            (
                "prior_inventory_path",
                "prior_inventory_sha256",
                "Prior inventory",
            ),
            (
                "prior_controller_path",
                "prior_controller_sha256",
                "Prior controller ledger",
            ),
        ]
        if authorization.get("schema_version") == "2.0":
            if (
                authorization.get("transition_kind")
                != "retry_after_plan_shard_hold"
                or authorization.get("prior_cycle_id")
                != f"cycle-{int(authorization['cycle_id'].split('-')[1]) - 1:03d}"
            ):
                raise BookSynthesisError(
                    "Sharded recovery authorization is invalid."
                )
            required_prior_artifacts.extend(
                [
                    (
                        "prior_index_path",
                        "prior_index_sha256",
                        "Prior cycle index",
                    ),
                    (
                        "prior_hold_path",
                        "prior_hold_sha256",
                        "Prior plan-shard hold",
                    ),
                ]
            )
        elif authorization.get("schema_version") == "3.0":
            if (
                authorization.get("transition_kind")
                != "resume_after_audit_provider_hold"
                or authorization.get("prior_cycle_id")
                != f"cycle-{int(authorization['cycle_id'].split('-')[1]) - 1:03d}"
                or authorization.get("resumed_attempt") != 1
                or authorization.get(
                    "maximum_provider_calls_for_held_stage"
                )
                != 2
                or not re.fullmatch(
                    r"audit-S1-[A-Za-z0-9._-]+",
                    str(authorization.get("held_stage")),
                )
            ):
                raise BookSynthesisError(
                    "Audit recovery authorization is invalid."
                )
            required_prior_artifacts.extend(
                [
                    (
                        "prior_index_path",
                        "prior_index_sha256",
                        "Prior cycle index",
                    ),
                    (
                        "prior_plan_path",
                        "prior_plan_sha256",
                        "Prior synthesis plan",
                    ),
                    (
                        "prior_attempt_book_path",
                        "prior_attempt_book_sha256",
                        "Prior S1 book",
                    ),
                    (
                        "prior_submission_path",
                        "prior_submission_sha256",
                        "Prior S1 submission",
                    ),
                    (
                        "prior_hold_path",
                        "prior_hold_sha256",
                        "Prior audit-stage hold",
                    ),
                ]
            )
        elif authorization.get("schema_version") == "4.0":
            if (
                authorization.get("transition_kind")
                != "fresh_after_terminal_hold"
                or authorization.get("inventory_policy")
                != "regenerate_required"
                or authorization.get("prior_cycle_id")
                != f"cycle-{int(authorization['cycle_id'].split('-')[1]) - 1:03d}"
                or authorization.get("maximum_provider_calls_per_stage") != 2
                or authorization.get("maximum_synthesis_attempts") != 2
            ):
                raise BookSynthesisError(
                    "Terminal-successor authorization is invalid."
                )
            required_prior_artifacts.extend(
                [
                    (
                        "prior_index_path",
                        "prior_index_sha256",
                        "Prior cycle index",
                    ),
                    (
                        "prior_terminal_receipt_path",
                        "prior_terminal_receipt_sha256",
                        "Prior terminal receipt",
                    ),
                ]
            )
        elif authorization.get("cycle_id") != "cycle-003":
            raise BookSynthesisError(
                "Sharded successor uses an unknown authorization version."
            )
        for path_field, hash_field, label in required_prior_artifacts:
            prior_path = resolve_project_path(
                project_path,
                authorization.get(path_field),
                label,
            )
            if file_sha256(prior_path) != authorization.get(hash_field):
                raise BookSynthesisError(f"{label} changed.")
        if authorization.get("schema_version") == "3.0":
            prior_authorization_path = resolve_project_path(
                project_path,
                authorization.get("prior_cycle_authorization_path"),
                "Prior cycle authorization",
            )
            prior_authorization = read_json_object(
                prior_authorization_path,
                "Prior cycle authorization",
            )
            if (
                prior_authorization.get("authorization_sha256")
                != authorization.get(
                    "foundation_cycle_authorization_sha256"
                )
            ):
                raise BookSynthesisError(
                    "Audit recovery foundation authorization changed."
                )
            prior_root = resolve_project_path(
                project_path,
                authorization.get("prior_cycle_root_path"),
                "Prior audit-held cycle",
            )
            active_root = resolve_project_path(
                project_path,
                active.get("root_path"),
                "Active audit-recovery cycle",
            )
            reused = authorization.get("reused_artifacts")
            if not isinstance(reused, list) or not reused:
                raise BookSynthesisError(
                    "Audit recovery has no reused-artifact manifest."
                )
            seen = set()
            for artifact in reused:
                if (
                    not isinstance(artifact, dict)
                    or not isinstance(artifact.get("relative_path"), str)
                    or not artifact["relative_path"]
                    or artifact["relative_path"] in seen
                    or not re.fullmatch(
                        r"[0-9a-f]{64}",
                        str(artifact.get("sha256")),
                    )
                ):
                    raise BookSynthesisError(
                        "Audit recovery reused-artifact manifest is invalid."
                    )
                relative = Path(artifact["relative_path"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise BookSynthesisError(
                        "Audit recovery artifact path is unsafe."
                    )
                seen.add(artifact["relative_path"])
                for root, label in (
                    (prior_root, "Prior reused artifact"),
                    (active_root, "Recovered artifact"),
                ):
                    artifact_path = (root / relative).resolve()
                    if (
                        not artifact_path.is_relative_to(root)
                        or file_sha256(artifact_path)
                        != artifact["sha256"]
                    ):
                        raise BookSynthesisError(f"{label} changed.")
        if authorization.get("schema_version") == "4.0":
            prior_receipt_path = resolve_project_path(
                project_path,
                authorization["prior_terminal_receipt_path"],
                "Prior terminal receipt",
            )
            prior_receipt = read_json_object(
                prior_receipt_path,
                "Prior terminal receipt",
            )
            if (
                prior_receipt.get("lifecycle_status") != "synthesis_held"
                or prior_receipt.get("candidate_sha256")
                != authorization.get("candidate_sha256")
                or prior_receipt.get("receipt_sha256")
                != canonical_json_sha256(
                    prior_receipt,
                    "receipt_sha256",
                )
            ):
                raise BookSynthesisError(
                    "Prior terminal receipt is invalid."
                )
    else:
        raise BookSynthesisError("Unknown synthesis-cycle authorization.")
    for decision in authorization.get("human_decision_artifacts", []):
        decision_path = resolve_project_path(
            project_path,
            decision.get("path"),
            "Human synthesis decision",
        )
        if file_sha256(decision_path) != decision.get("sha256"):
            raise BookSynthesisError("Human synthesis decision changed.")
    return authorization


def attempt_directory(project_path: Path, book_id: str, attempt: int) -> Path:
    """Return one immutable synthesis-attempt directory."""
    if attempt not in {1, 2}:
        raise BookSynthesisError("Synthesis attempt must be S1 or S2.")
    return synthesis_directory(project_path, book_id) / "attempts" / f"S{attempt}"


def _candidate_input(candidate: dict, role: str) -> dict:
    matches = [
        record
        for record in candidate.get("inputs", [])
        if isinstance(record, dict) and record.get("role") == role
    ]
    if len(matches) != 1:
        raise BookSynthesisError(
            f"Candidate must contain exactly one {role} input."
        )
    return matches[0]


def build_context(
    project_path: Path,
    book_id: str,
    candidate: dict,
    profile_path: Path,
    *,
    protocol_version: str | None = None,
) -> dict:
    """Build one deterministic, hash-bound synthesis context."""
    project_path = project_path.resolve()
    if (
        candidate.get("book_id") != book_id
        or candidate.get("lifecycle_status") != "candidate"
        or not isinstance(candidate.get("candidate_sha256"), str)
    ):
        raise BookSynthesisError("Candidate identity is invalid.")

    candidate_path = resolve_project_path(
        project_path,
        candidate.get("candidate_path"),
        "Candidate book",
    )
    if file_sha256(candidate_path) != candidate["candidate_sha256"]:
        raise BookSynthesisError("Candidate book hash does not match.")

    manifest_record = _candidate_input(candidate, "manifest")
    review_record = _candidate_input(candidate, "research_review")
    manifest_path = resolve_project_path(
        project_path,
        manifest_record.get("path"),
        "Book manifest",
    )
    review_path = resolve_project_path(
        project_path,
        review_record.get("path"),
        "Research review",
    )
    if (
        file_sha256(manifest_path) != manifest_record.get("sha256")
        or file_sha256(review_path) != review_record.get("sha256")
    ):
        raise BookSynthesisError("Candidate synthesis input hash changed.")

    manifest = read_json_object(manifest_path, "Book manifest")
    review_document = read_json_object(review_path, "Research review")
    if manifest.get("book_id") != book_id:
        raise BookSynthesisError("Manifest identity does not match the book.")
    claim_index = review_document.get("claim_index")
    if not isinstance(claim_index, list) or any(
        not isinstance(claim, dict)
        or not isinstance(claim.get("claim_id"), str)
        or not isinstance(claim.get("claim"), str)
        for claim in claim_index
    ):
        raise BookSynthesisError("Research review has no valid claim index.")
    claims_by_id = {claim["claim_id"]: claim for claim in claim_index}
    if len(claims_by_id) != len(claim_index):
        raise BookSynthesisError("Research review repeats a claim identity.")

    included_chapters = [
        chapter
        for chapter in manifest.get("chapter_plan", [])
        if isinstance(chapter, dict)
        and chapter.get("material_disposition") in {"include", "appendix"}
    ]
    required_claim_ids = []
    for chapter in included_chapters:
        for claim_id in chapter.get("claim_ids", []):
            if claim_id not in required_claim_ids:
                required_claim_ids.append(claim_id)
    unknown_claim_ids = sorted(set(required_claim_ids) - set(claims_by_id))
    if unknown_claim_ids:
        raise BookSynthesisError(
            "Manifest references unknown claims: " + ", ".join(unknown_claim_ids)
        )

    profile_path = profile_path.resolve()
    if not profile_path.is_file():
        raise BookSynthesisError("Book synthesizer skill is missing.")

    cycle_authorization = verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    context = {
        "schema_version": "1.0",
        "synthesis_id": (
            f"synthesis-{book_id}"
            if cycle_authorization is None
            else f"synthesis-{book_id}-{cycle_authorization['cycle_id']}"
        ),
        "book_id": book_id,
        "cycle_id": (
            "cycle-001"
            if cycle_authorization is None
            else cycle_authorization["cycle_id"]
        ),
        "protocol_version": (
            (
                DEFAULT_SYNTHESIS_PROTOCOL
                if protocol_version is None
                else protocol_version
            )
            if cycle_authorization is None
            else cycle_authorization["protocol_version"]
        ),
        "cycle_authorization_path": (
            None
            if cycle_authorization is None
            else str(
                (
                    synthesis_cycle_directory(project_path, book_id)
                    / "authorizations"
                    / f"{cycle_authorization['cycle_id']}.json"
                ).relative_to(project_path)
            )
        ),
        "cycle_authorization_sha256": (
            None
            if cycle_authorization is None
            else cycle_authorization["authorization_sha256"]
        ),
        "human_decision_artifacts": (
            []
            if cycle_authorization is None
            else cycle_authorization["human_decision_artifacts"]
        ),
        "candidate_path": str(candidate_path.relative_to(project_path)),
        "candidate_sha256": candidate["candidate_sha256"],
        "candidate_assembly_path": str(
            (
                project_path
                / "data"
                / "books"
                / book_id
                / "candidate_assembly.json"
            ).relative_to(project_path)
        ),
        "candidate_assembly_sha256": file_sha256(
            project_path
            / "data"
            / "books"
            / book_id
            / "candidate_assembly.json"
        ),
        "manifest_path": str(manifest_path.relative_to(project_path)),
        "manifest_sha256": manifest_record["sha256"],
        "review_path": str(review_path.relative_to(project_path)),
        "review_sha256": review_record["sha256"],
        "central_question": manifest.get("central_question"),
        "provisional_thesis": manifest.get("provisional_thesis"),
        "curiosity_signature": manifest.get("curiosity_signature"),
        "prohibited_overinterpretations": manifest.get(
            "prohibited_overinterpretations",
            [],
        ),
        "contradictions": manifest.get("contradictions", []),
        "required_claims": [
            claims_by_id[claim_id] for claim_id in required_claim_ids
        ],
        "source_receipt_ids": candidate.get("source_receipt_ids", []),
        "profile": {
            "name": PROFILE_NAME,
            "version": PROFILE_VERSION,
            "skill_path": str(profile_path.relative_to(project_path)),
            "skill_sha256": file_sha256(profile_path),
        },
    }
    context["context_sha256"] = canonical_json_sha256(
        context,
        "context_sha256",
    )
    return context


def _validate_inventory(
    inventory: dict,
    context: dict,
    candidate_text: str,
) -> None:
    if (
        inventory.get("book_id") != context["book_id"]
        or inventory.get("context_sha256") != context["context_sha256"]
        or inventory.get("candidate_sha256") != context["candidate_sha256"]
    ):
        raise BookSynthesisError("Preservation inventory identity is stale.")
    if inventory.get("central_question") != context["central_question"]:
        raise BookSynthesisError("Inventory changed the central question.")
    if inventory.get("curiosity_signature") != context["curiosity_signature"]:
        raise BookSynthesisError("Inventory changed the curiosity signature.")

    claims = inventory.get("claims")
    if not isinstance(claims, list):
        raise BookSynthesisError("Preservation inventory claims are invalid.")
    claim_ids = [
        claim.get("claim_id") for claim in claims if isinstance(claim, dict)
    ]
    if len(claim_ids) != len(claims) or len(claim_ids) != len(set(claim_ids)):
        raise BookSynthesisError("Inventory claim identities must be unique.")

    canonical_claims = {
        claim["claim_id"]: claim for claim in context["required_claims"]
    }
    occurrence_identity = (
        inventory.get("identity_model") == "canonical-claim-occurrence-v1"
    )
    if occurrence_identity:
        if inventory.get("canonical_claims") != context["required_claims"]:
            raise BookSynthesisError(
                "Inventory canonical registry changed the research claims."
            )
        occurrence_ids = [
            claim["claim_id"]
            for claim in claims
            if claim.get("source_kind") == "canonical_occurrence"
        ]
        if occurrence_ids != [
            f"CO{index}" for index in range(1, len(occurrence_ids) + 1)
        ]:
            raise BookSynthesisError(
                "Canonical occurrences must be sequential from CO1."
            )
        occurrence_parent_ids = {
            claim.get("canonical_claim_id")
            for claim in claims
            if claim.get("source_kind") == "canonical_occurrence"
        }
        if occurrence_parent_ids != set(canonical_claims):
            raise BookSynthesisError(
                "Inventory must represent every required canonical claim."
            )
    else:
        submitted_canonical_ids = {
            claim["claim_id"]
            for claim in claims
            if claim.get("source_kind") == "canonical"
        }
        if submitted_canonical_ids != set(canonical_claims):
            raise BookSynthesisError(
                "Inventory must account for every book-required canonical "
                "claim."
            )

    candidate_only_ids = [
        claim["claim_id"]
        for claim in claims
        if claim.get("source_kind") == "candidate_only"
    ]
    if candidate_only_ids != [
        f"SC{index}" for index in range(1, len(candidate_only_ids) + 1)
    ]:
        raise BookSynthesisError(
            "Candidate-only claims must be sequential from SC1."
        )

    allowed_receipts = set(context["source_receipt_ids"])
    audited_protocol = is_audited_plan_protocol(
        context.get("protocol_version")
    )
    for claim in claims:
        required_fields = (
            "claim_id",
            "source_kind",
            "statement",
            "candidate_excerpt",
            "candidate_location",
            "certainty",
            "scope_and_conditions",
            "evidence_receipt_ids",
            "required_in_edition",
            "importance_reason",
        )
        if any(field not in claim for field in required_fields):
            raise BookSynthesisError("Inventory claim is missing required fields.")
        if (
            not all(
                isinstance(claim[field], str) and claim[field].strip()
                for field in (
                    "statement",
                    "candidate_excerpt",
                    "candidate_location",
                    "certainty",
                    "scope_and_conditions",
                    "importance_reason",
                )
            )
            or claim["candidate_excerpt"] not in candidate_text
            or claim["required_in_edition"] is not True
            or not isinstance(claim["evidence_receipt_ids"], list)
            or len(claim["evidence_receipt_ids"])
            != len(set(claim["evidence_receipt_ids"]))
            or not set(claim["evidence_receipt_ids"]).issubset(allowed_receipts)
        ):
            raise BookSynthesisError("Inventory claim content is invalid.")
        if claim["source_kind"] in {"canonical", "canonical_occurrence"}:
            canonical_id = (
                claim.get("canonical_claim_id")
                if claim["source_kind"] == "canonical_occurrence"
                else claim["claim_id"]
            )
            canonical = canonical_claims.get(canonical_id)
            if (
                canonical is None
                or claim["statement"] != canonical["claim"]
                or claim["evidence_receipt_ids"]
                != canonical.get("source_receipt_ids", [])
            ):
                raise BookSynthesisError(
                    "Canonical claim wording or evidence was changed."
                )
            if (
                claim["source_kind"] == "canonical_occurrence"
                and not occurrence_identity
            ):
                raise BookSynthesisError(
                    "Canonical occurrence requires the occurrence identity "
                    "model."
                )
        elif claim["source_kind"] != "candidate_only":
            raise BookSynthesisError("Inventory claim source kind is invalid.")
        if audited_protocol and (
            not isinstance(claim.get("attribution"), str)
            or not claim["attribution"].strip()
            or not isinstance(claim.get("evidence_relationship"), str)
            or not claim["evidence_relationship"].strip()
            or claim.get("authority_type") not in INVENTORY_AUTHORITY_TYPES
            or claim.get("resolution_status")
            not in INVENTORY_RESOLUTION_STATUSES
        ):
            raise BookSynthesisError(
                "Audited-plan inventory claim frame is incomplete."
            )
        validate_inventory_claim_authority(
            claim,
            canonical_claims,
            required=has_authority_contract(inventory),
        )

    for field in (
        "definitions",
        "limitations",
        "unresolved_questions",
    ):
        values = inventory.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise BookSynthesisError(f"Inventory {field} are invalid.")
    if inventory.get("contradictions") != context["contradictions"]:
        raise BookSynthesisError("Inventory changed approved contradictions.")


def _validate_plan(plan: dict, context: dict, inventory: dict) -> None:
    if (
        plan.get("book_id") != context["book_id"]
        or plan.get("context_sha256") != context["context_sha256"]
        or plan.get("candidate_sha256") != context["candidate_sha256"]
        or plan.get("profile") != context["profile"]
    ):
        raise BookSynthesisError("Synthesis plan identity or profile is stale.")
    inventory_claim_ids = [
        claim["claim_id"] for claim in inventory["claims"]
    ]
    if plan.get("protected_claim_ids") != inventory_claim_ids:
        raise BookSynthesisError(
            "Synthesis plan must preserve the complete inventory in order."
        )
    if not isinstance(plan.get("central_answer"), str) or not plan[
        "central_answer"
    ].strip():
        raise BookSynthesisError("Synthesis plan has no central answer.")
    for field in ("chapter_jobs", "operations", "terminology"):
        if not isinstance(plan.get(field), list):
            raise BookSynthesisError(f"Synthesis plan {field} are invalid.")
    valid_operation_kinds = {
        "merge",
        "move",
        "compress",
        "transition",
        "terminology",
        "appendix",
        "conclusion",
    }
    operation_ids = []
    operations_by_id = {}
    for operation in plan["operations"]:
        if (
            not isinstance(operation, dict)
            or not isinstance(operation.get("operation_id"), str)
            or operation.get("kind") not in valid_operation_kinds
            or not isinstance(operation.get("target"), str)
            or not isinstance(operation.get("description"), str)
            or not isinstance(operation.get("protected_claim_ids"), list)
            or not set(operation["protected_claim_ids"]).issubset(
                set(inventory_claim_ids)
            )
        ):
            raise BookSynthesisError("Synthesis plan operation is invalid.")
        operation_ids.append(operation["operation_id"])
        operations_by_id[operation["operation_id"]] = operation
    if len(operation_ids) != len(set(operation_ids)):
        raise BookSynthesisError("Synthesis operation identities repeat.")

    if not is_audited_plan_protocol(context.get("protocol_version")):
        return

    inventory_by_id = {
        claim["claim_id"]: claim for claim in inventory["claims"]
    }
    claim_ids = set(inventory_by_id)
    central_claim_ids = plan.get("central_answer_claim_ids")
    if (
        plan.get("plan_attempt") not in {1, 2}
        or not isinstance(central_claim_ids, list)
        or not central_claim_ids
        or len(central_claim_ids) != len(set(central_claim_ids))
        or not set(central_claim_ids).issubset(claim_ids)
        or plan.get("central_answer_resolution_effect")
        not in PLAN_RESOLUTION_EFFECTS
    ):
        raise BookSynthesisError(
            "Audited synthesis plan central-answer authority is invalid."
        )

    decision_ids = {
        decision["decision_id"]
        for decision in context.get("human_decision_artifacts", [])
    }
    unresolved = {"open", "routed", "unresolved", "contradicted"}
    for operation in plan["operations"]:
        authority_type = operation.get("authority_type")
        authority_refs = operation.get("authority_refs")
        effect_type = operation.get("effect_type")
        resolution_effect = operation.get("resolution_effect")
        affected = operation["protected_claim_ids"]
        if (
            authority_type not in PLAN_AUTHORITY_TYPES
            or not isinstance(authority_refs, list)
            or len(authority_refs) != len(set(authority_refs))
            or effect_type not in PLAN_EFFECT_TYPES
            or resolution_effect not in PLAN_RESOLUTION_EFFECTS
        ):
            raise BookSynthesisError(
                "Audited synthesis operation has incomplete authority."
            )
        if authority_type == "candidate" and (
            not authority_refs or not set(authority_refs).issubset(claim_ids)
        ):
            raise BookSynthesisError(
                "Candidate-authorized operation has false authority refs."
            )
        if authority_type == "human_decision" and (
            not authority_refs
            or not set(authority_refs).issubset(decision_ids)
        ):
            raise BookSynthesisError(
                "Human-authorized operation has false authority refs."
            )
        if authority_type == "editorial_mechanics" and authority_refs:
            raise BookSynthesisError(
                "Editorial mechanics cannot claim external authority refs."
            )
        if (
            effect_type in {"rename", "resolve"}
            or resolution_effect == "resolved"
        ) and any(
            inventory_by_id[claim_id].get("resolution_status") in unresolved
            for claim_id in affected
        ) and authority_type != "human_decision":
            raise BookSynthesisError(
                "Unresolved claim cannot be renamed or resolved without a "
                "human decision."
            )
        if (
            authority_type == "editorial_mechanics"
            and effect_type
            in {"rename", "resolve", "change_evidence_relationship"}
        ):
            raise BookSynthesisError(
                "Editorial mechanics cannot change authority or resolution."
            )

    terminology = plan["terminology"]
    for entry in terminology:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("term"), str)
            or not entry["term"].strip()
            or not isinstance(entry.get("canonical_form"), str)
            or not entry["canonical_form"].strip()
            or entry.get("operation_id") not in operations_by_id
            or operations_by_id[entry["operation_id"]].get("kind")
            != "terminology"
        ):
            raise BookSynthesisError(
                "Audited-plan terminology lacks an authorized operation."
            )


def initialize_synthesis(
    project_path: Path,
    context: dict,
    inventory: dict,
    plan: dict,
) -> dict:
    """Create the immutable context, inventory, and plan for one candidate."""
    project_path = project_path.resolve()
    book_id = context["book_id"]
    directory = synthesis_directory(project_path, book_id)
    if directory.exists():
        raise BookSynthesisError("A synthesis cycle already exists.")
    candidate_path = resolve_project_path(
        project_path,
        context["candidate_path"],
        "Candidate book",
    )
    candidate_text = candidate_path.read_text(encoding="utf-8")
    _validate_inventory(inventory, context, candidate_text)
    _validate_plan(plan, context, inventory)

    context_document = dict(context)
    inventory_document = dict(inventory)
    inventory_document["inventory_sha256"] = canonical_json_sha256(
        inventory_document,
        "inventory_sha256",
    )
    plan_document = dict(plan)
    plan_document["inventory_sha256"] = inventory_document["inventory_sha256"]
    plan_document["plan_sha256"] = canonical_json_sha256(
        plan_document,
        "plan_sha256",
    )

    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = directory.parent / f".synthesis-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        atomic_write_json(staging / "context.json", context_document)
        atomic_write_json(staging / "inventory.json", inventory_document)
        atomic_write_json(staging / "plan.json", plan_document)
        os.replace(staging, directory)
    except (BookSynthesisError, OSError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(error, BookSynthesisError):
            raise
        raise BookSynthesisError(
            "Synthesis foundation could not be installed."
        ) from error
    return {
        "book_id": book_id,
        "context_path": str(
            (directory / "context.json").relative_to(project_path)
        ),
        "context_sha256": context_document["context_sha256"],
        "inventory_path": str(
            (directory / "inventory.json").relative_to(project_path)
        ),
        "inventory_sha256": inventory_document["inventory_sha256"],
        "plan_path": str((directory / "plan.json").relative_to(project_path)),
        "plan_sha256": plan_document["plan_sha256"],
    }


def load_initialized_synthesis(
    project_path: Path,
    book_id: str,
) -> tuple[dict, dict, dict]:
    """Load and verify the immutable synthesis foundation."""
    directory = synthesis_directory(project_path, book_id)
    context = read_json_object(directory / "context.json", "Synthesis context")
    inventory = read_json_object(
        directory / "inventory.json",
        "Preservation inventory",
    )
    plan = read_json_object(directory / "plan.json", "Synthesis plan")
    if (
        context.get("book_id") != book_id
        or context.get("context_sha256")
        != canonical_json_sha256(context, "context_sha256")
        or inventory.get("book_id") != book_id
        or inventory.get("context_sha256") != context["context_sha256"]
        or inventory.get("inventory_sha256")
        != canonical_json_sha256(inventory, "inventory_sha256")
        or plan.get("book_id") != book_id
        or plan.get("context_sha256") != context["context_sha256"]
        or plan.get("inventory_sha256") != inventory["inventory_sha256"]
        or plan.get("plan_sha256")
        != canonical_json_sha256(plan, "plan_sha256")
    ):
        raise BookSynthesisError("Synthesis foundation hash does not match.")
    if is_audited_plan_protocol(context.get("protocol_version")):
        authorization = verify_active_cycle_authorization(
            project_path,
            book_id,
        )
        cycle_root = synthesis_cycle_directory(project_path, book_id)
        initial_cycle = (
            authorization is None
            and context.get("cycle_id") == "cycle-001"
            and context.get("synthesis_id") == f"synthesis-{book_id}"
            and context.get("cycle_authorization_path") is None
            and context.get("cycle_authorization_sha256") is None
            and not cycle_root.exists()
        )
        plan_audit_path = resolve_project_path(
            project_path,
            plan.get("plan_audit_path"),
            "Plan audit",
        )
        expected_foundation_authorization = (
            authorization.get("foundation_cycle_authorization_sha256")
            if authorization is not None
            and authorization.get("transition_kind")
            == "resume_after_audit_provider_hold"
            else (
                None
                if authorization is None
                else authorization.get("authorization_sha256")
            )
        )
        plan_audit = read_json_object(plan_audit_path, "Plan audit")
        if (
            not initial_cycle
            and authorization is None
            or context.get("cycle_authorization_sha256")
            != expected_foundation_authorization
            or plan_audit.get("overall_status") != "pass"
            or plan_audit.get("plan_draft_sha256")
            != plan.get("approved_plan_draft_sha256")
            or plan_audit.get("plan_audit_sha256")
            != canonical_json_sha256(
                plan_audit,
                "plan_audit_sha256",
            )
            or plan.get("plan_audit_sha256")
            != plan_audit.get("plan_audit_sha256")
        ):
            raise BookSynthesisError(
                "Audited synthesis foundation has no valid plan audit."
            )
    return context, inventory, plan


def save_attempt(
    project_path: Path,
    submission: dict,
    markdown: str,
) -> dict:
    """Save one of at most two immutable synthesis attempts."""
    project_path = project_path.resolve()
    book_id = submission.get("book_id")
    attempt = submission.get("attempt")
    if not isinstance(book_id, str) or attempt not in {1, 2}:
        raise BookSynthesisError("Synthesis attempt identity is invalid.")
    context, inventory, plan = load_initialized_synthesis(
        project_path,
        book_id,
    )
    if (
        submission.get("context_sha256") != context["context_sha256"]
        or submission.get("inventory_sha256") != inventory["inventory_sha256"]
        or submission.get("plan_sha256") != plan["plan_sha256"]
    ):
        raise BookSynthesisError("Synthesis attempt foundation is stale.")
    if not isinstance(markdown, str) or not markdown.strip():
        raise BookSynthesisError("Synthesized book is empty.")
    if (synthesis_directory(project_path, book_id) / "receipt.json").exists():
        raise BookSynthesisError("Synthesis cycle is already terminal.")

    if attempt == 2:
        first_audit_path = attempt_directory(
            project_path,
            book_id,
            1,
        ) / "audit.json"
        first_audit = read_json_object(first_audit_path, "First synthesis audit")
        if first_audit.get("overall_status") != "blocking":
            raise BookSynthesisError(
                "A second attempt requires a blocking first audit."
            )
        if submission.get("correction_audit_sha256") != first_audit.get(
            "audit_sha256"
        ):
            raise BookSynthesisError("Correction is not bound to the first audit.")
    elif submission.get("correction_audit_sha256") is not None:
        raise BookSynthesisError("First synthesis attempt cannot be a correction.")

    mappings = submission.get("claim_mappings")
    if not isinstance(mappings, list):
        raise BookSynthesisError("Synthesis claim mappings are invalid.")
    required_ids = [
        claim["claim_id"]
        for claim in inventory["claims"]
        if claim["required_in_edition"]
    ]
    mapped_ids = [
        mapping.get("claim_id")
        for mapping in mappings
        if isinstance(mapping, dict)
    ]
    if mapped_ids != required_ids:
        raise BookSynthesisError(
            "Synthesis attempt must map every required claim in inventory order."
        )
    for mapping in mappings:
        if (
            not isinstance(mapping.get("edition_excerpt"), str)
            or not mapping["edition_excerpt"].strip()
            or mapping["edition_excerpt"] not in markdown
            or not isinstance(mapping.get("edition_location"), str)
            or not mapping["edition_location"].strip()
        ):
            raise BookSynthesisError("Synthesis claim mapping is invalid.")

    directory = attempt_directory(project_path, book_id, attempt)
    if directory.exists():
        raise BookSynthesisError("Synthesis attempt already exists.")
    book_path = directory / "book.md"
    directory.parent.mkdir(parents=True, exist_ok=True)
    staging = directory.parent / f".S{attempt}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        staging_book = staging / "book.md"
        atomic_write_text(staging_book, markdown)
        document = {
            **submission,
            "schema_version": "1.0",
            "submitted_at": current_utc_time(),
            "book_path": str(book_path.relative_to(project_path)),
            "book_sha256": file_sha256(staging_book),
        }
        document["submission_sha256"] = canonical_json_sha256(
            document,
            "submission_sha256",
        )
        atomic_write_json(staging / "submission.json", document)
        os.replace(staging, directory)
    except (BookSynthesisError, OSError) as error:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(error, BookSynthesisError):
            raise
        raise BookSynthesisError(
            "Synthesis attempt could not be installed."
        ) from error
    return document


def load_attempt(
    project_path: Path,
    book_id: str,
    attempt: int,
) -> tuple[Path, dict]:
    """Load and verify one immutable synthesis attempt."""
    directory = attempt_directory(project_path, book_id, attempt)
    book_path = directory / "book.md"
    submission = read_json_object(
        directory / "submission.json",
        "Synthesis submission",
    )
    expected_book_paths = {
        str(book_path.relative_to(project_path.resolve()))
    }
    authorization = verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    if (
        attempt == 1
        and authorization is not None
        and authorization.get("transition_kind")
        == "resume_after_audit_provider_hold"
    ):
        expected_book_paths.add(
            str(authorization.get("prior_attempt_book_path"))
        )
    if (
        submission.get("book_id") != book_id
        or submission.get("attempt") != attempt
        or submission.get("book_path") not in expected_book_paths
        or submission.get("book_sha256") != file_sha256(book_path)
        or submission.get("submission_sha256")
        != canonical_json_sha256(submission, "submission_sha256")
    ):
        raise BookSynthesisError("Synthesis attempt hash does not match.")
    return book_path, submission


def _audit_blockers(audit: dict, inventory: dict) -> list[str]:
    blockers = []
    results = audit.get("claim_results", [])
    inventory_ids = [claim["claim_id"] for claim in inventory["claims"]]
    result_ids = [
        result.get("claim_id")
        for result in results
        if isinstance(result, dict)
    ]
    if result_ids != inventory_ids:
        raise BookSynthesisError(
            "Audit must assess every inventory claim in order."
        )
    for result in results:
        status = result.get("status")
        if status not in CLAIM_STATUSES:
            raise BookSynthesisError("Audit claim status is invalid.")
        for field in ("explanation", "candidate_excerpt"):
            if not isinstance(result.get(field), str) or not result[field].strip():
                raise BookSynthesisError("Audit claim finding is incomplete.")
        if status == "omitted":
            if result.get("edition_excerpt") is not None:
                raise BookSynthesisError("An omitted claim has an edition excerpt.")
        elif (
            not isinstance(result.get("edition_excerpt"), str)
            or not result["edition_excerpt"].strip()
        ):
            raise BookSynthesisError("Audited claim has no edition excerpt.")
        if status == "narrowed" and (
            not isinstance(result.get("justification"), str)
            or not result["justification"].strip()
        ):
            raise BookSynthesisError("A narrowed claim requires justification.")
        if status in BLOCKING_CLAIM_STATUSES:
            blockers.append(f"{result['claim_id']}:{status}")
        semantic_change_flags = (
            result.get("certainty_changed"),
            result.get("scope_changed"),
            result.get("evidence_relationship_changed"),
        )
        if any(type(flag) is not bool for flag in semantic_change_flags):
            raise BookSynthesisError(
                "Audit semantic-change flags must be true or false."
            )
        validate_audit_changed_dimensions(
            result,
            required=has_authority_contract(inventory),
        )
        if status in {"preserved", "clarified"} and any(
            flag is True for flag in semantic_change_flags
        ):
            blockers.append(f"{result['claim_id']}:unclassified_change")
        if (
            status == "narrowed"
            and result.get("evidence_relationship_changed") is True
        ):
            blockers.append(f"{result['claim_id']}:evidence_changed")

    for field in (
        "unmapped_candidate_claims",
        "novel_edition_claims",
        "lost_limitations",
        "terminology_drift",
    ):
        values = audit.get(field)
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            raise BookSynthesisError(f"Audit {field} are invalid.")
        if values:
            blockers.append(field)
    if type(audit.get("central_question_preserved")) is not bool:
        raise BookSynthesisError(
            "Central-question preservation must be true or false."
        )
    if audit["central_question_preserved"] is not True:
        blockers.append("central_question_changed")
    curiosity = audit.get("curiosity_preservation")
    if (
        not isinstance(curiosity, dict)
        or curiosity.get("status") not in {"preserved", "distorted"}
        or not isinstance(curiosity.get("explanation"), str)
        or not curiosity["explanation"].strip()
    ):
        raise BookSynthesisError("Curiosity-preservation finding is invalid.")
    if curiosity["status"] == "distorted":
        blockers.append("curiosity_distorted")
    return blockers


def _write_terminal_receipt(
    project_path: Path,
    book_id: str,
    attempt: int,
    context: dict,
    inventory: dict,
    plan: dict,
    book_path: Path,
    submission: dict,
    audit_path: Path,
    audit: dict,
) -> dict:
    """Write the terminal receipt for a passing or final blocking audit."""
    if audit.get("overall_status") == "blocking" and attempt != 2:
        raise BookSynthesisError("First blocking audit is not terminal.")
    lifecycle_status = (
        "ready_for_editorial"
        if audit.get("overall_status") == "pass"
        else "synthesis_held"
    )
    authorization = verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    is_audit_recovery = (
        authorization is not None
        and authorization.get("transition_kind")
        == "resume_after_audit_provider_hold"
    )
    completion_cycle_id = (
        authorization["cycle_id"]
        if is_audit_recovery
        else context.get("cycle_id", "cycle-001")
    )
    completion_authorization_path = (
        str(
            (
                synthesis_cycle_directory(project_path, book_id)
                / "authorizations"
                / f"{authorization['cycle_id']}.json"
            ).relative_to(project_path)
        )
        if is_audit_recovery
        else context.get("cycle_authorization_path")
    )
    completion_authorization_sha256 = (
        authorization["authorization_sha256"]
        if is_audit_recovery
        else context.get("cycle_authorization_sha256")
    )
    receipt = {
        "schema_version": "1.0",
        "synthesis_id": (
            f"synthesis-{book_id}-{completion_cycle_id}"
            if is_audit_recovery
            else context["synthesis_id"]
        ),
        "book_id": book_id,
        "cycle_id": completion_cycle_id,
        "foundation_cycle_id": (
            context.get("cycle_id") if is_audit_recovery else None
        ),
        "foundation_cycle_authorization_sha256": (
            context.get("cycle_authorization_sha256")
            if is_audit_recovery
            else None
        ),
        "protocol_version": context.get("protocol_version", "staged-v1"),
        "cycle_authorization_path": completion_authorization_path,
        "cycle_authorization_sha256": completion_authorization_sha256,
        "lifecycle_status": lifecycle_status,
        "completed_at": current_utc_time(),
        "candidate_path": context["candidate_path"],
        "candidate_sha256": context["candidate_sha256"],
        "context_path": str(
            (
                synthesis_directory(project_path, book_id) / "context.json"
            ).relative_to(project_path)
        ),
        "context_sha256": context["context_sha256"],
        "inventory_path": str(
            (
                synthesis_directory(project_path, book_id) / "inventory.json"
            ).relative_to(project_path)
        ),
        "inventory_sha256": inventory["inventory_sha256"],
        "plan_path": str(
            (
                synthesis_directory(project_path, book_id) / "plan.json"
            ).relative_to(project_path)
        ),
        "plan_sha256": plan["plan_sha256"],
        "plan_audit_path": plan.get("plan_audit_path"),
        "plan_audit_sha256": plan.get("plan_audit_sha256"),
        "selected_attempt": attempt,
        "book_path": str(book_path.relative_to(project_path)),
        "book_sha256": submission["book_sha256"],
        "submission_path": str(
            (
                attempt_directory(project_path, book_id, attempt)
                / "submission.json"
            ).relative_to(project_path)
        ),
        "submission_sha256": submission["submission_sha256"],
        "audit_path": str(audit_path.relative_to(project_path)),
        "audit_sha256": audit["audit_sha256"],
        "profile": context["profile"],
    }
    receipt["receipt_sha256"] = canonical_json_sha256(
        receipt,
        "receipt_sha256",
    )
    atomic_write_json(
        synthesis_directory(project_path, book_id) / "receipt.json",
        receipt,
    )
    return receipt


def submit_audit(project_path: Path, audit: dict) -> dict:
    """Save one independent audit and close or advance the bounded cycle."""
    project_path = project_path.resolve()
    book_id = audit.get("book_id")
    attempt = audit.get("attempt")
    if not isinstance(book_id, str) or attempt not in {1, 2}:
        raise BookSynthesisError("Synthesis audit identity is invalid.")
    context, inventory, plan = load_initialized_synthesis(
        project_path,
        book_id,
    )
    book_path, submission = load_attempt(project_path, book_id, attempt)
    if (
        audit.get("context_sha256") != context["context_sha256"]
        or audit.get("inventory_sha256") != inventory["inventory_sha256"]
        or audit.get("plan_sha256") != plan["plan_sha256"]
        or audit.get("submission_sha256") != submission["submission_sha256"]
        or audit.get("book_sha256") != submission["book_sha256"]
    ):
        raise BookSynthesisError("Synthesis audit foundation is stale.")
    candidate_path = resolve_project_path(
        project_path,
        context["candidate_path"],
        "Candidate book",
    )
    candidate_text = candidate_path.read_text(encoding="utf-8")
    edition_text = book_path.read_text(encoding="utf-8")
    inventory_by_id = {
        claim["claim_id"]: claim for claim in inventory["claims"]
    }
    for result in audit.get("claim_results", []):
        if not isinstance(result, dict):
            raise BookSynthesisError("Audit claim finding is invalid.")
        inventory_claim = inventory_by_id.get(result.get("claim_id"))
        if (
            inventory_claim is None
            or result.get("candidate_excerpt")
            != inventory_claim["candidate_excerpt"]
            or result["candidate_excerpt"] not in candidate_text
            or (
                result.get("edition_excerpt") is not None
                and result["edition_excerpt"] not in edition_text
            )
        ):
            raise BookSynthesisError(
                "Audit claim excerpts do not match immutable artifacts."
            )
    blockers = _audit_blockers(audit, inventory)
    computed_status = "blocking" if blockers else "pass"
    if audit.get("overall_status") != computed_status:
        raise BookSynthesisError(
            "Audit outcome does not match its structured findings."
        )
    recorded_reasons = audit.get("blocking_reasons")
    if not isinstance(recorded_reasons, list) or any(
        not isinstance(reason, str) or not reason.strip()
        for reason in recorded_reasons
    ):
        raise BookSynthesisError("Audit blocking reasons are invalid.")
    if computed_status == "blocking" and not recorded_reasons:
        raise BookSynthesisError("A blocking audit requires reasons.")
    if computed_status == "pass" and recorded_reasons:
        raise BookSynthesisError("A passing audit cannot record blockers.")

    audit_document = {
        **audit,
        "schema_version": "1.0",
        "audited_at": current_utc_time(),
        "computed_blockers": blockers,
    }
    audit_document["audit_sha256"] = canonical_json_sha256(
        audit_document,
        "audit_sha256",
    )
    audit_path = attempt_directory(
        project_path,
        book_id,
        attempt,
    ) / "audit.json"
    atomic_write_json(audit_path, audit_document)

    if computed_status == "blocking" and attempt == 1:
        return {
            "book_id": book_id,
            "attempt": attempt,
            "status": "correction_required",
            "audit_path": str(audit_path.relative_to(project_path)),
            "audit_sha256": audit_document["audit_sha256"],
        }

    return _write_terminal_receipt(
        project_path,
        book_id,
        attempt,
        context,
        inventory,
        plan,
        book_path,
        submission,
        audit_path,
        audit_document,
    )


def recover_terminal_receipt(project_path: Path, book_id: str) -> dict:
    """Recover only the receipt after a completed terminal audit."""
    project_path = project_path.resolve()
    receipt_path = synthesis_directory(project_path, book_id) / "receipt.json"
    if receipt_path.exists():
        context = read_json_object(
            synthesis_directory(project_path, book_id) / "context.json",
            "Synthesis context",
        )
        return verify_receipt(
            project_path,
            book_id,
            context["candidate_sha256"],
        )
    context, inventory, plan = load_initialized_synthesis(
        project_path,
        book_id,
    )
    for attempt in (1, 2):
        audit_path = attempt_directory(
            project_path,
            book_id,
            attempt,
        ) / "audit.json"
        if not audit_path.is_file():
            continue
        audit = read_json_object(audit_path, "Synthesis audit")
        terminal = (
            audit.get("overall_status") == "pass"
            or (
                attempt == 2
                and audit.get("overall_status") == "blocking"
            )
        )
        if not terminal:
            continue
        if audit.get("audit_sha256") != canonical_json_sha256(
            audit,
            "audit_sha256",
        ):
            raise BookSynthesisError("Terminal audit hash does not match.")
        book_path, submission = load_attempt(
            project_path,
            book_id,
            attempt,
        )
        return _write_terminal_receipt(
            project_path,
            book_id,
            attempt,
            context,
            inventory,
            plan,
            book_path,
            submission,
            audit_path,
            audit,
        )
    raise BookSynthesisError("No terminal audit is available for recovery.")


def verify_receipt(
    project_path: Path,
    book_id: str,
    expected_candidate_sha256: str,
) -> dict:
    """Verify one terminal synthesis receipt and every selected artifact."""
    project_path = project_path.resolve()
    directory = synthesis_directory(project_path, book_id)
    receipt = read_json_object(directory / "receipt.json", "Synthesis receipt")
    context, inventory, plan = load_initialized_synthesis(project_path, book_id)
    attempt = receipt.get("selected_attempt")
    if attempt not in {1, 2}:
        raise BookSynthesisError("Synthesis receipt attempt is invalid.")
    book_path, submission = load_attempt(project_path, book_id, attempt)
    audit_path = attempt_directory(project_path, book_id, attempt) / "audit.json"
    audit = read_json_object(audit_path, "Synthesis audit")
    plan_audit = None
    authorization = verify_active_cycle_authorization(
        project_path,
        book_id,
    )
    is_audit_recovery = (
        authorization is not None
        and authorization.get("transition_kind")
        == "resume_after_audit_provider_hold"
    )
    if is_audited_plan_protocol(receipt.get("protocol_version")):
        plan_audit_path = resolve_project_path(
            project_path,
            receipt.get("plan_audit_path"),
            "Plan audit",
        )
        plan_audit = read_json_object(plan_audit_path, "Plan audit")
    if (
        receipt.get("book_id") != book_id
        or receipt.get("lifecycle_status")
        not in {"ready_for_editorial", "synthesis_held"}
        or receipt.get("candidate_sha256") != expected_candidate_sha256
        or context.get("candidate_sha256") != expected_candidate_sha256
        or receipt.get("context_sha256") != context["context_sha256"]
        or receipt.get("inventory_sha256") != inventory["inventory_sha256"]
        or receipt.get("plan_sha256") != plan["plan_sha256"]
        or (
            is_audited_plan_protocol(receipt.get("protocol_version"))
            and (
                receipt.get("cycle_id")
                != (
                    authorization.get("cycle_id")
                    if is_audit_recovery
                    else context.get("cycle_id")
                )
                or receipt.get("cycle_authorization_sha256")
                != (
                    authorization.get("authorization_sha256")
                    if is_audit_recovery
                    else context.get("cycle_authorization_sha256")
                )
                or (
                    is_audit_recovery
                    and (
                        receipt.get("foundation_cycle_id")
                        != context.get("cycle_id")
                        or receipt.get(
                            "foundation_cycle_authorization_sha256"
                        )
                        != context.get("cycle_authorization_sha256")
                    )
                )
                or receipt.get("plan_audit_sha256")
                != plan.get("plan_audit_sha256")
                or plan_audit.get("plan_audit_sha256")
                != canonical_json_sha256(
                    plan_audit,
                    "plan_audit_sha256",
                )
                or plan_audit.get("plan_audit_sha256")
                != receipt.get("plan_audit_sha256")
            )
        )
        or receipt.get("book_path") != str(book_path.relative_to(project_path))
        or receipt.get("book_sha256") != submission["book_sha256"]
        or receipt.get("submission_sha256") != submission["submission_sha256"]
        or receipt.get("audit_path") != str(audit_path.relative_to(project_path))
        or receipt.get("audit_sha256") != audit.get("audit_sha256")
        or receipt.get("audit_sha256")
        != canonical_json_sha256(audit, "audit_sha256")
        or receipt.get("receipt_sha256")
        != canonical_json_sha256(receipt, "receipt_sha256")
    ):
        raise BookSynthesisError("Synthesis receipt hash does not match.")
    expected_lifecycle = (
        "ready_for_editorial"
        if audit.get("overall_status") == "pass"
        else "synthesis_held"
    )
    if receipt["lifecycle_status"] != expected_lifecycle:
        raise BookSynthesisError("Synthesis lifecycle does not match its audit.")
    return receipt


def workflow_state_legacy(project_path: Path, book_id: str) -> str:
    """Derive the legacy terminal synthesis transition from durable artifacts."""
    directory = synthesis_directory(project_path, book_id)
    if not directory.exists():
        return "not_started"
    foundation = [
        directory / "context.json",
        directory / "inventory.json",
        directory / "plan.json",
    ]
    if not all(path.is_file() for path in foundation):
        raise BookSynthesisError("Synthesis foundation is partially present.")
    load_initialized_synthesis(project_path, book_id)
    receipt_path = directory / "receipt.json"
    if receipt_path.exists():
        context = read_json_object(directory / "context.json", "Synthesis context")
        receipt = verify_receipt(
            project_path,
            book_id,
            context["candidate_sha256"],
        )
        return receipt["lifecycle_status"]

    for attempt in (1, 2):
        attempt_dir = attempt_directory(project_path, book_id, attempt)
        book_exists = (attempt_dir / "book.md").exists()
        submission_exists = (attempt_dir / "submission.json").exists()
        audit_exists = (attempt_dir / "audit.json").exists()
        if book_exists != submission_exists:
            raise BookSynthesisError("Synthesis attempt is partially present.")
        if audit_exists and not book_exists:
            raise BookSynthesisError("Synthesis audit has no attempt.")
        if not book_exists:
            if attempt == 1:
                return "synthesis_required"
            first_audit = read_json_object(
                attempt_directory(project_path, book_id, 1) / "audit.json",
                "First synthesis audit",
            )
            if first_audit.get("overall_status") != "blocking":
                raise BookSynthesisError("Unexpected second synthesis transition.")
            return "correction_required"
        load_attempt(project_path, book_id, attempt)
        if not audit_exists:
            return f"audit_S{attempt}"
        audit = read_json_object(attempt_dir / "audit.json", "Synthesis audit")
        if audit.get("audit_sha256") != canonical_json_sha256(
            audit,
            "audit_sha256",
        ):
            raise BookSynthesisError("Synthesis audit hash does not match.")
        if audit.get("overall_status") == "pass":
            return "receipt_recovery"
        if attempt == 2:
            return "receipt_recovery"
    raise BookSynthesisError("Synthesis state cannot be derived.")


def workflow_state(project_path: Path, book_id: str) -> str:
    """Derive the next legacy or staged synthesis transition."""
    import staged_synthesis

    staged_state = staged_synthesis.state(project_path, book_id)
    if staged_state is not None:
        return staged_state
    return workflow_state_legacy(project_path, book_id)
