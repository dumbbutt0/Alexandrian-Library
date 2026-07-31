"""Durable per-provider tool budgets for staged book synthesis."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


READ_TOOL = "read_staged_synthesis_plan_shard_input"
SUBMIT_TOOL = "submit_staged_synthesis_plan_shard"
INVENTORY_READ_TOOL = "read_staged_synthesis_inventory_input"
INVENTORY_SUBMIT_TOOL = "submit_staged_synthesis_inventory_unit"
PLAN_AUDIT_READ_TOOL = (
    "read_staged_synthesis_global_plan_audit_input"
)
PLAN_AUDIT_SUBMIT_TOOL = (
    "submit_staged_synthesis_global_plan_audit"
)
EDITION_TOOL = "save_staged_synthesis_edition_unit"
AUDIT_UNIT_TOOL = "submit_staged_synthesis_audit_unit"
GLOBAL_AUDIT_TOOL = "submit_staged_synthesis_global_audit"
CONTROLLED_TOOLS = {
    READ_TOOL,
    SUBMIT_TOOL,
    INVENTORY_READ_TOOL,
    INVENTORY_SUBMIT_TOOL,
    PLAN_AUDIT_READ_TOOL,
    PLAN_AUDIT_SUBMIT_TOOL,
    EDITION_TOOL,
    AUDIT_UNIT_TOOL,
    GLOBAL_AUDIT_TOOL,
}
TOOL_KINDS = {
    READ_TOOL: "read",
    SUBMIT_TOOL: "submit",
    INVENTORY_READ_TOOL: "read",
    INVENTORY_SUBMIT_TOOL: "submit",
    PLAN_AUDIT_READ_TOOL: "read",
    PLAN_AUDIT_SUBMIT_TOOL: "plan-audit-submit",
    EDITION_TOOL: "edition-submit",
    AUDIT_UNIT_TOOL: "audit-submit",
    GLOBAL_AUDIT_TOOL: "global-audit-submit",
}
CORRECTABLE_ERROR_PREFIX = "CORRECTABLE_PLAN_SHARD_VALIDATION:"
CORRECTABLE_INVENTORY_ERROR_PREFIX = (
    "CORRECTABLE_INVENTORY_VALIDATION:"
)

CONTEXT_ENV = {
    "book_id": "ALEXANDRIAN_SYNTHESIS_BOOK_ID",
    "cycle_id": "ALEXANDRIAN_SYNTHESIS_CYCLE_ID",
    "action_id": "ALEXANDRIAN_SYNTHESIS_ACTION_ID",
    "stage": "ALEXANDRIAN_SYNTHESIS_STAGE",
    "backend": "ALEXANDRIAN_SYNTHESIS_BACKEND",
    "input_hash": "ALEXANDRIAN_SYNTHESIS_INPUT_HASH",
}


class SynthesisToolBudgetError(RuntimeError):
    """Reject a call that is outside its durable provider-action budget."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_sha256(document: dict, hash_field: str) -> str:
    payload = {
        key: value for key, value in document.items() if key != hash_field
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _argument_sha256(arguments: Any) -> str:
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        encoded = repr(arguments).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_create_json(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        payload = (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_json(path: Path, label: str) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SynthesisToolBudgetError(f"{label} is unreadable.") from error
    if not isinstance(document, dict):
        raise SynthesisToolBudgetError(f"{label} is not a JSON object.")
    return document


def _environment_context(
    environment: Mapping[str, str],
    tool_name: str,
) -> dict[str, str] | None:
    context = {
        field: environment.get(variable, "")
        for field, variable in CONTEXT_ENV.items()
    }
    if any(not value for value in context.values()):
        return None
    scope_id = _stage_scope_id(context["stage"], tool_name)
    if scope_id is None:
        raise SynthesisToolBudgetError(
            "The controlled tool is outside the active stage contract."
        )
    context["scope_id"] = scope_id
    context["chapter_id"] = scope_id
    return context


def _stage_scope_id(stage: str, tool_name: str) -> str | None:
    if tool_name in {READ_TOOL, SUBMIT_TOOL}:
        if not stage.startswith("plan-shard-"):
            return None
        return stage.removeprefix("plan-shard-") or None
    if tool_name in {INVENTORY_READ_TOOL, INVENTORY_SUBMIT_TOOL}:
        if not stage.startswith("inventory-"):
            return None
        return stage.removeprefix("inventory-") or None
    if tool_name in {PLAN_AUDIT_READ_TOOL, PLAN_AUDIT_SUBMIT_TOOL}:
        return "GLOBAL" if stage == "global-plan-audit-A1" else None
    if tool_name == EDITION_TOOL:
        if not stage.startswith("edition-S"):
            return None
        return stage.rsplit("-", 1)[-1]
    if tool_name == AUDIT_UNIT_TOOL:
        if not stage.startswith("audit-S"):
            return None
        return stage.rsplit("-", 1)[-1]
    if tool_name == GLOBAL_AUDIT_TOOL:
        return "GLOBAL" if stage.startswith("global-audit-S") else None
    return None


def _argument_identity(arguments: Any) -> dict[str, str]:
    if not isinstance(arguments, dict):
        return {}
    source = arguments
    for key in ("proposal", "submission", "audit"):
        nested = arguments.get(key)
        if isinstance(nested, dict):
            source = nested
            break
    return {
        field: source[field]
        for field in ("book_id", "chapter_id", "unit_id", "input_hash")
        if isinstance(source.get(field), str) and source[field]
    }


def _durable_context(
    project_path: Path,
    arguments: Any,
    tool_name: str,
) -> dict[str, str]:
    identity = _argument_identity(arguments)
    books_root = project_path / "data" / "books"
    if "book_id" in identity:
        ledgers = [
            books_root
            / identity["book_id"]
            / "runs"
            / "synthesis.controller.jsonl"
        ]
    else:
        ledgers = sorted(books_root.glob("*/runs/synthesis.controller.jsonl"))

    candidates = []
    for ledger_path in ledgers:
        if not ledger_path.is_file():
            continue
        events = []
        for line in ledger_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise SynthesisToolBudgetError(
                    "A synthesis controller ledger is invalid."
                ) from error
        terminal_actions = {
            event.get("action_id")
            for event in events
            if event.get("event") in {"contract_completed", "contract_failed"}
        }
        for event in events:
            stage = event.get("stage")
            tool_budget = event.get("tool_budget")
            declared_tools = (
                tool_budget.get("controlled_tools", [])
                if isinstance(tool_budget, dict)
                else []
            )
            legacy_plan_tool = (
                stage.startswith("plan-shard-")
                and tool_name in {READ_TOOL, SUBMIT_TOOL}
            )
            if (
                event.get("event") != "contract_opened"
                or event.get("action_id") in terminal_actions
                or not isinstance(stage, str)
                or (
                    tool_name not in declared_tools
                    and not legacy_plan_tool
                )
            ):
                continue
            scope_id = _stage_scope_id(stage, tool_name)
            if scope_id is None:
                continue
            supplied_scope = (
                identity.get("chapter_id") or identity.get("unit_id")
            )
            if (
                supplied_scope is not None
                and supplied_scope != scope_id
            ):
                continue
            input_hash = event.get("input_hash") or identity.get("input_hash")
            required = {
                "book_id": event.get("book_id"),
                "cycle_id": event.get("cycle_id"),
                "action_id": event.get("action_id"),
                "stage": stage,
                "backend": event.get("backend"),
                "input_hash": input_hash,
                "scope_id": scope_id,
                "chapter_id": scope_id,
            }
            if all(isinstance(value, str) and value for value in required.values()):
                candidates.append(required)
    if len(candidates) != 1:
        raise SynthesisToolBudgetError(
            "No single open staged-synthesis action can be resolved."
        )
    return candidates[0]


def _verify_open_action(project_path: Path, context: dict[str, str]) -> None:
    ledger_path = (
        project_path
        / "data"
        / "books"
        / context["book_id"]
        / "runs"
        / "synthesis.controller.jsonl"
    )
    try:
        lines = ledger_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise SynthesisToolBudgetError(
            "The active synthesis controller ledger is unavailable."
        ) from error

    action_events = []
    for line in lines:
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise SynthesisToolBudgetError(
                "The synthesis controller ledger is invalid."
            ) from error
        if event.get("action_id") == context["action_id"]:
            action_events.append(event)
    opened = next(
        (
            event
            for event in action_events
            if event.get("event") == "contract_opened"
        ),
        None,
    )
    if opened is None:
        raise SynthesisToolBudgetError(
            "The active synthesis action was not opened durably."
        )
    for field in ("book_id", "cycle_id", "stage", "backend"):
        if opened.get(field) != context[field]:
            raise SynthesisToolBudgetError(
                "The active synthesis action does not match its controller."
            )
    if any(
        event.get("event") in {"contract_completed", "contract_failed"}
        for event in action_events
    ):
        raise SynthesisToolBudgetError(
            "The active synthesis action is already terminal."
        )


def _budget_root(project_path: Path, context: dict[str, str]) -> Path:
    return (
        project_path
        / "data"
        / "books"
        / context["book_id"]
        / "runs"
        / "synthesis_tool_budgets"
        / context["action_id"]
    )


def _receipt_paths(root: Path, kind: str, number: int) -> tuple[Path, Path]:
    stem = f"{kind}-{number:02d}"
    return root / f"{stem}.reservation.json", root / f"{stem}.result.json"


def _verified_result(path: Path) -> dict:
    result = _read_json(path, "Synthesis tool result")
    if result.get("attempt_sha256") != _canonical_sha256(
        result,
        "attempt_sha256",
    ):
        raise SynthesisToolBudgetError(
            "A synthesis tool result receipt is stale."
        )
    return result


def reserve_tool_attempt(
    project_path: Path,
    tool_name: str,
    arguments: Any,
    environment: Mapping[str, str] | None = None,
) -> dict:
    """Atomically consume one raw call slot before argument validation."""
    if tool_name not in CONTROLLED_TOOLS:
        raise ValueError(f"Tool is not budget-controlled: {tool_name}.")
    project_path = project_path.resolve()
    selected_environment = os.environ if environment is None else environment
    context = _environment_context(selected_environment, tool_name)
    if context is None:
        context = _durable_context(project_path, arguments, tool_name)
    _verify_open_action(project_path, context)
    root = _budget_root(project_path, context)
    kind = TOOL_KINDS[tool_name]

    if kind == "read":
        attempt_number = 1
        reservation_path, _ = _receipt_paths(root, kind, attempt_number)
        if reservation_path.exists():
            raise SynthesisToolBudgetError(
                "The staged input read budget is exhausted."
            )
    elif tool_name == PLAN_AUDIT_SUBMIT_TOOL:
        read_reservation, read_result = _receipt_paths(root, "read", 1)
        if (
            not read_reservation.is_file()
            or not read_result.is_file()
            or _verified_result(read_result).get("outcome") != "success"
        ):
            raise SynthesisToolBudgetError(
                "The global plan-audit input must be read successfully "
                "before submission."
            )
        attempt_number = 1
        reservation_path, _ = _receipt_paths(
            root,
            TOOL_KINDS[tool_name],
            attempt_number,
        )
        if reservation_path.exists():
            raise SynthesisToolBudgetError(
                "The staged write submission budget is exhausted."
            )
    elif tool_name in {SUBMIT_TOOL, INVENTORY_SUBMIT_TOOL}:
        first_reservation, first_result = _receipt_paths(root, kind, 1)
        second_reservation, _ = _receipt_paths(root, kind, 2)
        if not first_reservation.exists():
            attempt_number = 1
            reservation_path = first_reservation
        else:
            if second_reservation.exists():
                raise SynthesisToolBudgetError(
                    "The staged submission budget is exhausted."
                )
            if not first_result.is_file():
                raise SynthesisToolBudgetError(
                    "The first submission slot was consumed without a "
                    "completed result; recovery cannot retry it."
                )
            result = _verified_result(first_result)
            if result.get("outcome") != "correctable_validation_rejected":
                raise SynthesisToolBudgetError(
                    "A second submission is allowed only after a correctable "
                    "deterministic validation rejection."
                )
            attempt_number = 2
            reservation_path = second_reservation
    else:
        attempt_number = 1
        reservation_path, _ = _receipt_paths(root, kind, attempt_number)
        if reservation_path.exists():
            raise SynthesisToolBudgetError(
                "The staged write submission budget is exhausted."
            )

    document = {
        "schema_version": "1.0",
        "record_type": "reservation",
        **context,
        "tool_name": tool_name,
        "attempt_number": attempt_number,
        "raw_argument_sha256": _argument_sha256(arguments),
        "reserved_at": _now(),
    }
    document["attempt_sha256"] = _canonical_sha256(
        document,
        "attempt_sha256",
    )
    try:
        _atomic_create_json(reservation_path, document)
    except FileExistsError as error:
        raise SynthesisToolBudgetError(
            f"The {kind} attempt slot was already consumed."
        ) from error
    return {
        **document,
        "reservation_path": str(reservation_path.relative_to(project_path)),
    }


def record_tool_result(
    project_path: Path,
    reservation: dict,
    outcome: str,
    error_message: str | None = None,
    *,
    safe_diagnostic: dict | None = None,
) -> dict:
    """Append the immutable outcome for one already-consumed call slot."""
    project_path = project_path.resolve()
    context = {
        field: reservation[field]
        for field in (
            "book_id",
            "cycle_id",
            "action_id",
            "stage",
            "backend",
            "input_hash",
            "chapter_id",
            "scope_id",
        )
    }
    kind = TOOL_KINDS[reservation["tool_name"]]
    _, result_path = _receipt_paths(
        _budget_root(project_path, context),
        kind,
        int(reservation["attempt_number"]),
    )
    validation_diagnostic = None
    if safe_diagnostic is not None:
        if (
            set(safe_diagnostic)
            != {"code", "claim_id", "field_path", "correctable"}
            or not isinstance(safe_diagnostic.get("code"), str)
            or not re.fullmatch(
                r"[a-z0-9_]{1,100}",
                safe_diagnostic["code"],
            )
            or (
                safe_diagnostic.get("claim_id") is not None
                and (
                    not isinstance(safe_diagnostic["claim_id"], str)
                    or not re.fullmatch(
                        r"[A-Za-z0-9:._-]{1,200}",
                        safe_diagnostic["claim_id"],
                    )
                )
            )
            or not isinstance(safe_diagnostic.get("field_path"), str)
            or not re.fullmatch(
                r"[A-Za-z0-9_.\[\]-]{1,300}",
                safe_diagnostic["field_path"],
            )
            or safe_diagnostic.get("correctable") is not True
            or outcome != "correctable_validation_rejected"
        ):
            raise SynthesisToolBudgetError(
                "Safe validation diagnostic is invalid."
            )
        validation_diagnostic = {
            **safe_diagnostic,
            "submission_attempt": reservation["attempt_number"],
        }
    document = {
        "schema_version": "1.0",
        "record_type": "result",
        **context,
        "tool_name": reservation["tool_name"],
        "attempt_number": reservation["attempt_number"],
        "reservation_sha256": reservation["attempt_sha256"],
        "outcome": outcome,
        "error_sha256": (
            None
            if error_message is None
            else hashlib.sha256(error_message.encode("utf-8")).hexdigest()
        ),
        "validation_diagnostic": validation_diagnostic,
        "completed_at": _now(),
    }
    document["attempt_sha256"] = _canonical_sha256(
        document,
        "attempt_sha256",
    )
    try:
        _atomic_create_json(result_path, document)
    except FileExistsError as error:
        raise SynthesisToolBudgetError(
            "The synthesis tool result was already recorded."
        ) from error
    return {
        **document,
        "result_path": str(result_path.relative_to(project_path)),
    }


def finalize_action_budget(
    project_path: Path,
    book_id: str,
    action_id: str,
) -> dict | None:
    """Create one hash-bound summary after the provider process exits."""
    project_path = project_path.resolve()
    root = (
        project_path
        / "data"
        / "books"
        / book_id
        / "runs"
        / "synthesis_tool_budgets"
        / action_id
    )
    if not root.is_dir():
        return None
    summary_path = root / "summary.json"
    receipts = []
    for path in sorted(root.glob("*.json")):
        if path.name == summary_path.name:
            continue
        document = _read_json(path, "Synthesis tool attempt receipt")
        if document.get("attempt_sha256") != _canonical_sha256(
            document,
            "attempt_sha256",
        ):
            raise SynthesisToolBudgetError(
                "A synthesis tool attempt receipt is stale."
            )
        receipts.append(
            {
                "path": str(path.relative_to(project_path)),
                "attempt_sha256": document["attempt_sha256"],
                "record_type": document.get("record_type"),
                "tool_name": document.get("tool_name"),
                "attempt_number": document.get("attempt_number"),
                "outcome": document.get("outcome"),
                "validation_diagnostic": document.get(
                    "validation_diagnostic"
                ),
            }
        )
    tools = {receipt["tool_name"] for receipt in receipts}
    correction_budget = bool(
        tools
        & {
            READ_TOOL,
            SUBMIT_TOOL,
            INVENTORY_READ_TOOL,
            INVENTORY_SUBMIT_TOOL,
        }
    )
    global_plan_audit_budget = bool(
        tools & {PLAN_AUDIT_READ_TOOL, PLAN_AUDIT_SUBMIT_TOOL}
    )
    summary = {
        "schema_version": "1.0",
        "book_id": book_id,
        "action_id": action_id,
        "maximum_reads": (
            1 if correction_budget or global_plan_audit_budget else 0
        ),
        "maximum_submissions": 2 if correction_budget else 1,
        "maximum_successful_writes": 1,
        "second_submission_rule": (
            (
                "correctable_validation_rejected_only"
                if correction_budget
                else "none"
            )
        ),
        "attempt_receipts": receipts,
        "finalized_at": _now(),
    }
    summary["budget_sha256"] = _canonical_sha256(
        summary,
        "budget_sha256",
    )
    if summary_path.is_file():
        existing = _read_json(summary_path, "Synthesis tool budget summary")
        comparable_existing = {
            key: value
            for key, value in existing.items()
            if key not in {"finalized_at", "budget_sha256"}
        }
        comparable_requested = {
            key: value
            for key, value in summary.items()
            if key not in {"finalized_at", "budget_sha256"}
        }
        if comparable_existing != comparable_requested:
            raise SynthesisToolBudgetError(
                "The synthesis tool budget changed after finalization."
            )
        summary = existing
    else:
        _atomic_create_json(summary_path, summary)
    return {
        **summary,
        "budget_path": str(summary_path.relative_to(project_path)),
    }
