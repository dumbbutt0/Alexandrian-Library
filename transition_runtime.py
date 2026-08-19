"""Shared durable transition hooks for provider-backed workflow mutations."""

from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "1.0"
CONTRACT_VERSION = "transition-contract.v1"
_EVENT_LOCK = threading.RLock()
RUNTIME_OBSERVATION_RELATIVE_PATH = Path("runs/runtime.events.jsonl")


def book_directory_is_observation_only(book_directory: Path) -> bool:
    """Return whether a precreated book directory contains only job telemetry."""
    if not book_directory.is_dir():
        return False
    allowed_entries = {
        RUNTIME_OBSERVATION_RELATIVE_PATH.parent,
        RUNTIME_OBSERVATION_RELATIVE_PATH,
    }
    return all(
        path.relative_to(book_directory) in allowed_entries
        for path in book_directory.rglob("*")
    )


@dataclass(frozen=True)
class TransitionSpec:
    """Declare one bounded state transition and its mechanical retry policy."""

    transition_id: str
    kind: str
    expected_states: tuple[str, ...]
    success_states: tuple[str, ...]
    provider: str
    # Provider calls can be expensive and may already have written their
    # durable artifact before the transport fails. Reconcile once; never
    # replay an entire transition unless the caller explicitly opts in.
    maximum_mechanical_attempts: int = 1


def current_utc_time() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_event(
    path: Path,
    spec: TransitionSpec,
    phase: str,
    percent: int,
    *,
    mechanical_attempt: int = 0,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one fsynced canonical event without rewriting prior events."""
    event = {
        "schema_version": SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "transition_id": spec.transition_id,
        "kind": spec.kind,
        "provider": spec.provider,
        "phase": phase,
        "percent": max(0, min(100, int(percent))),
        "mechanical_attempt": mechanical_attempt,
        "recorded_at": current_utc_time(),
        "details": details or {},
    }
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with _EVENT_LOCK:
        descriptor = os.open(
            path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return event


def read_events(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    """Read the newest valid transition events without trusting partial lines."""
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines[-max(1, limit) :]:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("schema_version") == SCHEMA_VERSION:
            events.append(event)
    return events


def provider_failure_text(events: list[dict], response: str) -> str:
    """Extract bounded provider/tool errors for retry classification."""
    values = [response]
    for event in events:
        item = event.get("item", {}) if isinstance(event, dict) else {}
        if not isinstance(item, dict):
            continue
        for field in ("error", "result", "output"):
            value = item.get(field)
            if value is not None:
                values.append(str(value))
    return "\n".join(values)[-20_000:]


def is_mechanical_failure(events: list[dict], response: str) -> bool:
    """Distinguish transport/schema failures from accepted semantic artifacts."""
    text = provider_failure_text(events, response).lower()
    markers = (
        "mechanical_contract_error",
        "validation error",
        "field required",
        "extra inputs are not permitted",
        "input_type=",
        "tool not found",
        "unknown tool",
        "schema validation",
        "timed out",
        "timeout",
        "connection reset",
        "provider output was unavailable",
    )
    return any(marker in text for marker in markers)


def run_provider_transition(
    spec: TransitionSpec,
    *,
    event_path: Path,
    state_reader: Callable[[], str],
    provider_call: Callable[[], tuple[list[dict], str]],
    progress: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    """Execute, reconcile, and mechanically retry one provider transition."""
    before = state_reader()
    if before not in spec.expected_states:
        raise RuntimeError(
            f"{spec.kind} cannot start from canonical state {before}."
        )

    def report(percent: int, phase: str, attempt: int, details=None) -> None:
        append_event(
            event_path,
            spec,
            phase,
            percent,
            mechanical_attempt=attempt,
            details=details,
        )
        if progress is not None:
            progress(percent, phase)

    report(10, "transition_preflight_passed", 0, {"state": before})
    last_events: list[dict] = []
    last_response = ""
    last_state = before
    for attempt in range(1, spec.maximum_mechanical_attempts + 1):
        report(25, "provider_started", attempt)
        try:
            last_events, last_response = provider_call()
        except Exception as error:
            last_events = []
            last_response = str(error)
            last_state = state_reader()
            if last_state in spec.success_states:
                report(
                    90,
                    "artifact_reconciled_after_provider_error",
                    attempt,
                    {"state": last_state},
                )
                break
            if attempt >= spec.maximum_mechanical_attempts or not is_mechanical_failure(
                last_events,
                last_response,
            ):
                report(90, "provider_failed", attempt, {"state": last_state})
                raise
            report(35, "mechanical_retry_scheduled", attempt)
            continue

        last_state = state_reader()
        if last_state in spec.success_states:
            report(90, "durable_artifact_saved", attempt, {"state": last_state})
            break
        mechanical = is_mechanical_failure(last_events, last_response)
        if mechanical and attempt < spec.maximum_mechanical_attempts:
            report(35, "mechanical_retry_scheduled", attempt)
            continue
        report(
            90,
            "provider_returned_without_transition",
            attempt,
            {"state": last_state, "mechanical": mechanical},
        )
        break

    return {
        "before_state": before,
        "after_state": last_state,
        "events": last_events,
        "response": last_response,
        "mechanical_attempts": attempt,
        "completed": last_state in spec.success_states,
    }


def contract_envelope(
    *,
    transition_id: str,
    kind: str,
    required_tool: str,
    expected_resulting_states: list[str],
    submission_template: dict[str, Any],
) -> dict[str, Any]:
    """Return the exact machine-readable contract supplied to a provider."""
    return {
        "contract_version": CONTRACT_VERSION,
        "transition_id": transition_id,
        "kind": kind,
        "required_tool": required_tool,
        "expected_resulting_states": expected_resulting_states,
        "submission_template": submission_template,
        "mechanical_errors_consume_semantic_attempt": False,
    }
