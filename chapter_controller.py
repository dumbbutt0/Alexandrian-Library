"""Finite, provider-neutral control records for chapter development.

This module does not write chapter prose or call a model.  It decides which
bounded workflow action is authorized, records that action before execution,
and preserves an append-only account of what happened.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"


class ChapterState(str, Enum):
    WORKSPACE_DEVELOPMENT = "workspace_development"
    AWAITING_EVALUATION = "awaiting_evaluation"
    AWAITING_HUMAN_INPUT = "awaiting_human_input"
    AWAITING_CLOSEOUT_AUTHORIZATION = (
        "awaiting_closeout_authorization"
    )
    AWAITING_SOURCE_APPROVAL = "awaiting_source_approval"
    RESEARCH_REQUIRED = "research_required"
    FINAL_PASS_REQUIRED = "final_pass_required"
    RECOVERY_REQUIRED = "recovery_required"
    INTERRUPTED = "interrupted"
    COMPLETE = "complete"
    FAILED = "failed"


class ActionKind(str, Enum):
    RUN_CHAPTER_PHASE = "run_chapter_phase"
    RUN_FINAL_PASS_CLOSEOUT = "run_final_pass_closeout"
    RUN_FINAL_PASS_REPAIR = "run_final_pass_repair"
    RUN_RESEARCH_ACCESS_RECOVERY = "run_research_access_recovery"
    APPLY_HUMAN_INPUT = "apply_human_input"
    RUN_HUMAN_INPUT_CLOSEOUT = "run_human_input_closeout"
    REQUEST_HUMAN_INPUT = "request_human_input"
    REQUEST_CLOSEOUT_AUTHORIZATION = (
        "request_closeout_authorization"
    )
    REQUEST_SOURCE_APPROVAL = "request_source_approval"
    REQUEST_RECOVERY_AUTHORIZATION = (
        "request_recovery_authorization"
    )
    RECONCILE_INTERRUPTED_ACTION = "reconcile_interrupted_action"
    RETURN_COMPLETE = "return_complete"
    RETURN_FAILURE = "return_failure"


EXECUTABLE_ACTIONS = {
    ActionKind.RUN_CHAPTER_PHASE,
    ActionKind.RUN_FINAL_PASS_CLOSEOUT,
    ActionKind.RUN_FINAL_PASS_REPAIR,
    ActionKind.RUN_RESEARCH_ACCESS_RECOVERY,
    ActionKind.APPLY_HUMAN_INPUT,
    ActionKind.RUN_HUMAN_INPUT_CLOSEOUT,
}

TERMINAL_LEGACY_STATUSES = {
    "assessment_extension_consumed",
    "chapter_blocked",
    "controller_no_progress",
    "final_pass_failed",
    "final_pass_repair_failed",
    "human_input_closeout_failed",
    "insufficient_closeout_budget",
    "recovery_call_limit_reached",
    "research_access_recovery_failed",
    "research_incomplete",
    "research_limit_reached",
    "stalled",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ChapterSnapshot:
    book_id: str
    chapter_id: str
    legacy_status: str
    developer_calls: int
    evaluator_calls: int
    researcher_calls: int
    call_limit: int
    workspace_exists: bool
    matching_assessment_exists: bool
    assessment_outcome: str | None
    human_input_exists: bool
    closeout_authorized: bool
    recovery_kind: str | None
    final_closeout_pending: bool
    source_requests_pending: bool
    interrupted_contract_id: str | None = None

    @property
    def calls_used(self) -> int:
        return (
            self.developer_calls
            + self.evaluator_calls
            + self.researcher_calls
        )

    @property
    def calls_remaining(self) -> int:
        return max(0, self.call_limit - self.calls_used)

    @property
    def digest(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True)
class NextAction:
    state: ChapterState
    action: ActionKind
    reason: str
    max_provider_calls: int

    @property
    def executable(self) -> bool:
        return self.action in EXECUTABLE_ACTIONS


def decide_next_action(snapshot: ChapterSnapshot) -> NextAction:
    """Return one deterministic action from an immutable chapter snapshot."""
    if snapshot.interrupted_contract_id is not None:
        return NextAction(
            ChapterState.INTERRUPTED,
            ActionKind.RECONCILE_INTERRUPTED_ACTION,
            "A prior action started but has no terminal ledger event.",
            0,
        )

    if snapshot.legacy_status == "chapter_complete":
        return NextAction(
            ChapterState.COMPLETE,
            ActionKind.RETURN_COMPLETE,
            "The durable chapter run is complete.",
            0,
        )

    if (
        snapshot.legacy_status == "research_access_recovery_failed"
        and snapshot.recovery_kind == "research_access_retry"
        and snapshot.human_input_exists
        and not snapshot.closeout_authorized
    ):
        return NextAction(
            ChapterState.AWAITING_CLOSEOUT_AUTHORIZATION,
            ActionKind.REQUEST_CLOSEOUT_AUTHORIZATION,
            "A recovery-bound human decision needs separate closeout "
            "authorization.",
            0,
        )

    if snapshot.legacy_status in TERMINAL_LEGACY_STATUSES:
        return NextAction(
            ChapterState.FAILED,
            ActionKind.RETURN_FAILURE,
            "The durable chapter run reached a terminal failure state.",
            0,
        )

    if (
        snapshot.legacy_status == "agent_call_limit_reached"
        and snapshot.recovery_kind is None
    ):
        return NextAction(
            ChapterState.RECOVERY_REQUIRED,
            ActionKind.REQUEST_RECOVERY_AUTHORIZATION,
            "The base call budget ended without recovery authorization.",
            0,
        )

    if (
        snapshot.legacy_status == "source_approval_required"
        and snapshot.source_requests_pending
    ):
        return NextAction(
            ChapterState.AWAITING_SOURCE_APPROVAL,
            ActionKind.REQUEST_SOURCE_APPROVAL,
            "Research is waiting for an explicit source decision.",
            0,
        )

    if snapshot.legacy_status == "human_input_closeout_required":
        if not snapshot.closeout_authorized:
            return NextAction(
                ChapterState.AWAITING_CLOSEOUT_AUTHORIZATION,
                ActionKind.REQUEST_CLOSEOUT_AUTHORIZATION,
                "Recorded human input needs a separate closeout authority.",
                0,
            )
        return NextAction(
            ChapterState.WORKSPACE_DEVELOPMENT,
            ActionKind.RUN_HUMAN_INPUT_CLOSEOUT,
            "The bounded no-research human closeout is authorized.",
            min(1, snapshot.calls_remaining),
        )

    if snapshot.legacy_status == "human_input_required":
        if not snapshot.human_input_exists:
            return NextAction(
                ChapterState.AWAITING_HUMAN_INPUT,
                ActionKind.REQUEST_HUMAN_INPUT,
                "The current assessment requires a human decision.",
                0,
            )
        if (
            snapshot.recovery_kind == "research_access_retry"
            and not snapshot.closeout_authorized
        ):
            return NextAction(
                ChapterState.AWAITING_CLOSEOUT_AUTHORIZATION,
                ActionKind.REQUEST_CLOSEOUT_AUTHORIZATION,
                "Recovery-bound human input needs closeout authorization.",
                0,
            )
        return NextAction(
            ChapterState.WORKSPACE_DEVELOPMENT,
            ActionKind.APPLY_HUMAN_INPUT,
            "The assessment-bound human decision is ready to apply.",
            min(1, snapshot.calls_remaining),
        )

    if snapshot.recovery_kind == "final_pass_revision":
        return NextAction(
            ChapterState.FINAL_PASS_REQUIRED,
            ActionKind.RUN_FINAL_PASS_REPAIR,
            "A bounded final-pass repair is authorized.",
            min(1, snapshot.calls_remaining),
        )

    if snapshot.final_closeout_pending:
        return NextAction(
            ChapterState.FINAL_PASS_REQUIRED,
            ActionKind.RUN_FINAL_PASS_CLOSEOUT,
            "A final-pass closeout has been durably requested.",
            min(1, snapshot.calls_remaining),
        )

    if snapshot.closeout_authorized:
        return NextAction(
            ChapterState.WORKSPACE_DEVELOPMENT,
            ActionKind.RUN_HUMAN_INPUT_CLOSEOUT,
            "The assessment-bound no-research closeout remains active.",
            min(1, snapshot.calls_remaining),
        )

    if snapshot.recovery_kind == "research_access_retry":
        return NextAction(
            ChapterState.RESEARCH_REQUIRED,
            ActionKind.RUN_RESEARCH_ACCESS_RECOVERY,
            "The one-time research-access recovery is authorized.",
            min(1, snapshot.calls_remaining),
        )

    if snapshot.calls_remaining == 0:
        return NextAction(
            ChapterState.FAILED,
            ActionKind.RETURN_FAILURE,
            "No authorized provider call remains.",
            0,
        )

    if not snapshot.workspace_exists:
        state = ChapterState.WORKSPACE_DEVELOPMENT
        reason = "No chapter workspace exists."
    elif not snapshot.matching_assessment_exists:
        state = ChapterState.AWAITING_EVALUATION
        reason = "The current workspace has no matching assessment."
    elif snapshot.assessment_outcome == "continue_research":
        state = ChapterState.RESEARCH_REQUIRED
        reason = "The current assessment requests bounded research."
    elif snapshot.assessment_outcome == "ready_for_final_pass":
        state = ChapterState.FINAL_PASS_REQUIRED
        reason = "The current assessment requests the final pass."
    else:
        state = ChapterState.WORKSPACE_DEVELOPMENT
        reason = "The current assessment permits another development phase."

    return NextAction(
        state,
        ActionKind.RUN_CHAPTER_PHASE,
        reason,
        min(1, snapshot.calls_remaining),
    )


class ChapterEventLedger:
    """Validate and append hash-chained controller events."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        except OSError as error:
            raise RuntimeError(
                f"Chapter controller ledger could not be read at {self.path}."
            ) from error

        events: list[dict] = []
        previous_hash = None
        for expected_sequence, line in enumerate(lines, start=1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(
                    "Chapter controller ledger contains invalid JSON."
                ) from error
            if (
                not isinstance(event, dict)
                or event.get("schema_version") != SCHEMA_VERSION
                or event.get("sequence") != expected_sequence
                or event.get("previous_event_sha256") != previous_hash
                or event.get("event_sha256")
                != canonical_sha256(
                    {
                        key: value
                        for key, value in event.items()
                        if key != "event_sha256"
                    }
                )
            ):
                raise RuntimeError(
                    "Chapter controller ledger failed integrity validation."
                )
            events.append(event)
            previous_hash = event["event_sha256"]
        return events

    def append(self, event_type: str, payload: dict) -> dict:
        events = self.read()
        sequence = len(events) + 1
        previous_hash = (
            events[-1]["event_sha256"] if events else None
        )
        event = {
            "schema_version": SCHEMA_VERSION,
            "sequence": sequence,
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "recorded_at": utc_now(),
            "previous_event_sha256": previous_hash,
            "payload": payload,
        }
        event["event_sha256"] = canonical_sha256(event)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = (
            json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8")
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return event

    def initialize(self, snapshot: ChapterSnapshot) -> dict | None:
        events = self.read()
        if events:
            first = events[0]
            payload = first.get("payload", {})
            if (
                first.get("event_type") != "legacy_state_imported"
                or payload.get("book_id") != snapshot.book_id
                or payload.get("chapter_id") != snapshot.chapter_id
            ):
                raise RuntimeError(
                    "Chapter ledger is not bound to this chapter."
                )
            return None
        return self.append(
            "legacy_state_imported",
            {
                "book_id": snapshot.book_id,
                "chapter_id": snapshot.chapter_id,
                "snapshot": asdict(snapshot),
                "snapshot_sha256": snapshot.digest,
            },
        )

    def open_contract(
        self,
        snapshot: ChapterSnapshot,
        decision: NextAction,
        run_baseline: dict | None = None,
    ) -> dict:
        if not decision.executable:
            raise RuntimeError(
                "A non-executable controller action cannot open a contract."
            )
        self.initialize(snapshot)
        events = self.read()
        active = self.active_contract(events)
        if active is not None:
            if (
                active["payload"]["snapshot_sha256"] == snapshot.digest
                and active["payload"]["action"] == decision.action.value
                and not self.contract_started(
                    active["payload"]["contract_id"],
                    events,
                )
            ):
                return active["payload"]
            raise RuntimeError(
                "An earlier chapter action requires reconciliation."
            )

        failed_for_action = [
            contract
            for contract in self.failed_contracts(events)
            if contract["action"] == decision.action.value
        ]
        retry_authorizations = {
            event["payload"]["failed_contract_id"]
            for event in events
            if event.get("event_type") == "retry_authorized"
        }
        if len(failed_for_action) >= 2:
            raise RuntimeError(
                "The action exhausted its one changed-input retry."
            )
        for contract in failed_for_action:
            if contract["contract_id"] not in retry_authorizations:
                raise RuntimeError(
                    "A failed action cannot retry without explicit "
                    "changed-condition authorization."
                )
        step_number = (
            1
            + sum(
                event["event_type"] == "contract_opened"
                for event in events
            )
        )
        contract_basis = {
            "book_id": snapshot.book_id,
            "chapter_id": snapshot.chapter_id,
            "step_number": step_number,
            "attempt_number": len(failed_for_action) + 1,
            "snapshot_sha256": snapshot.digest,
            "state": decision.state.value,
            "action": decision.action.value,
            "reason": decision.reason,
            "max_provider_calls": decision.max_provider_calls,
            "snapshot": asdict(snapshot),
            "run_baseline": run_baseline,
        }
        contract = {
            **contract_basis,
            "contract_id": (
                f"CC-{step_number}-"
                f"{canonical_sha256(contract_basis)[:16]}"
            ),
        }
        self.append("contract_opened", contract)
        return contract

    def mark_started(self, contract_id: str) -> dict:
        events = self.read()
        self._require_open_unstarted(contract_id, events)
        return self.append(
            "contract_started",
            {"contract_id": contract_id},
        )

    def acknowledge_provider_call(
        self,
        contract_id: str,
        call_kind: str,
    ) -> dict:
        events = self.read()
        contract = self._require_active_started(contract_id, events)
        used = sum(
            event["event_type"] == "provider_call_acknowledged"
            and event["payload"].get("contract_id") == contract_id
            for event in events
        )
        if used >= contract["max_provider_calls"]:
            raise RuntimeError(
                "Provider acknowledgement exceeds the action contract."
            )
        return self.append(
            "provider_call_acknowledged",
            {
                "contract_id": contract_id,
                "call_number": used + 1,
                "call_kind": call_kind,
            },
        )

    def complete(
        self,
        contract_id: str,
        result_snapshot: ChapterSnapshot,
    ) -> dict:
        events = self.read()
        self._require_active_started(contract_id, events)
        return self.append(
            "contract_completed",
            {
                "contract_id": contract_id,
                "result_snapshot": asdict(result_snapshot),
                "result_snapshot_sha256": result_snapshot.digest,
            },
        )

    def fail(
        self,
        contract_id: str,
        failure_kind: str,
        message: str,
    ) -> dict:
        events = self.read()
        self._require_active_started(contract_id, events)
        return self.append(
            "contract_failed",
            {
                "contract_id": contract_id,
                "failure_kind": failure_kind,
                "message": message,
            },
        )

    def reconcile(
        self,
        contract_id: str,
        disposition: str,
        result_snapshot: ChapterSnapshot,
        note: str,
    ) -> dict:
        """Close one interrupted contract through an explicit audit action."""
        if disposition not in {
            "no_provider_output",
            "provider_output_accepted",
            "provider_output_rejected",
        }:
            raise ValueError("Unsupported interruption disposition.")
        if not note.strip():
            raise ValueError("Interruption reconciliation needs a note.")
        events = self.read()
        self._require_active_started(contract_id, events)
        return self.append(
            "contract_reconciled",
            {
                "contract_id": contract_id,
                "disposition": disposition,
                "result_snapshot": asdict(result_snapshot),
                "result_snapshot_sha256": result_snapshot.digest,
                "note": note.strip(),
            },
        )

    def authorize_retry(
        self,
        failed_contract_id: str,
        changed_condition: str,
    ) -> dict:
        """Authorize the sole retry after an operational condition changes."""
        if not changed_condition.strip():
            raise ValueError(
                "Retry authorization needs the changed condition."
            )
        events = self.read()
        failed_contracts = [
            contract
            for contract in self.failed_contracts(events)
            if contract["contract_id"] == failed_contract_id
        ]
        if len(failed_contracts) != 1:
            raise RuntimeError(
                "Retry authorization requires one failed contract."
            )
        if any(
            event.get("event_type") == "retry_authorized"
            and event.get("payload", {}).get("failed_contract_id")
            == failed_contract_id
            for event in events
        ):
            raise RuntimeError("Retry was already authorized.")
        failed_contract = failed_contracts[0]
        if (
            sum(
                contract["action"] == failed_contract["action"]
                for contract in self.failed_contracts(events)
            )
            >= 2
        ):
            raise RuntimeError("The action has no retry remaining.")
        return self.append(
            "retry_authorized",
            {
                "failed_contract_id": failed_contract_id,
                "action": failed_contract["action"],
                "changed_condition": changed_condition.strip(),
            },
        )

    @staticmethod
    def active_contract(events: list[dict]) -> dict | None:
        opened: dict[str, dict] = {}
        closed: set[str] = set()
        for event in events:
            contract_id = event.get("payload", {}).get("contract_id")
            if event.get("event_type") == "contract_opened":
                opened[contract_id] = event
            elif event.get("event_type") in {
                "contract_completed",
                "contract_failed",
                "contract_reconciled",
            }:
                closed.add(contract_id)
        active = [
            event
            for contract_id, event in opened.items()
            if contract_id not in closed
        ]
        if len(active) > 1:
            raise RuntimeError(
                "Chapter controller ledger has multiple active contracts."
            )
        return active[0] if active else None

    @staticmethod
    def contract_started(
        contract_id: str,
        events: list[dict],
    ) -> bool:
        return any(
            event.get("event_type") == "contract_started"
            and event.get("payload", {}).get("contract_id") == contract_id
            for event in events
        )

    @staticmethod
    def failed_contracts(events: list[dict]) -> list[dict]:
        opened = {
            event["payload"]["contract_id"]: event["payload"]
            for event in events
            if event.get("event_type") == "contract_opened"
        }
        failed = []
        for event in events:
            event_type = event.get("event_type")
            payload = event.get("payload", {})
            is_failed_reconciliation = (
                event_type == "contract_reconciled"
                and payload.get("disposition")
                in {"no_provider_output", "provider_output_rejected"}
            )
            if event_type != "contract_failed" and not (
                is_failed_reconciliation
            ):
                continue
            contract_id = payload.get("contract_id")
            if contract_id in opened:
                failed.append(opened[contract_id])
        return failed

    def retry_requirement(
        self,
        snapshot: ChapterSnapshot,
        decision: NextAction,
    ) -> dict | None:
        """Return the retry gate for the proposed action, if one exists."""
        events = self.read()
        failed_for_action = [
            contract
            for contract in self.failed_contracts(events)
            if contract["action"] == decision.action.value
        ]
        if not failed_for_action:
            return None
        if len(failed_for_action) >= 2:
            return {
                "status": "exhausted",
                "failed_contract_id": failed_for_action[-1][
                    "contract_id"
                ],
            }
        failed = failed_for_action[-1]
        authorized = any(
            event.get("event_type") == "retry_authorized"
            and event.get("payload", {}).get("failed_contract_id")
            == failed["contract_id"]
            for event in events
        )
        if authorized:
            return None
        return {
            "status": "authorization_required",
            "failed_contract_id": failed["contract_id"],
        }

    def contract_record(self, contract_id: str) -> dict:
        """Return one immutable opened-contract payload."""
        records = [
            event["payload"]
            for event in self.read()
            if event.get("event_type") == "contract_opened"
            and event.get("payload", {}).get("contract_id") == contract_id
        ]
        if len(records) != 1:
            raise RuntimeError("Chapter action contract is unavailable.")
        return records[0]

    def interrupted_contract_id(self) -> str | None:
        events = self.read()
        active = self.active_contract(events)
        if active is None:
            return None
        contract_id = active["payload"]["contract_id"]
        if self.contract_started(contract_id, events):
            return contract_id
        return None

    def provider_call_counts(self, contract_id: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.read():
            if (
                event.get("event_type")
                != "provider_call_acknowledged"
                or event.get("payload", {}).get("contract_id")
                != contract_id
            ):
                continue
            call_kind = event["payload"]["call_kind"]
            counts[call_kind] = counts.get(call_kind, 0) + 1
        return counts

    def _require_open_unstarted(
        self,
        contract_id: str,
        events: list[dict],
    ) -> dict:
        active = self.active_contract(events)
        if (
            active is None
            or active["payload"].get("contract_id") != contract_id
        ):
            raise RuntimeError("Chapter action contract is not active.")
        if self.contract_started(contract_id, events):
            raise RuntimeError("Chapter action contract already started.")
        return active["payload"]

    def _require_active_started(
        self,
        contract_id: str,
        events: list[dict],
    ) -> dict:
        active = self.active_contract(events)
        if (
            active is None
            or active["payload"].get("contract_id") != contract_id
            or not self.contract_started(contract_id, events)
        ):
            raise RuntimeError(
                "Chapter action contract is not active and started."
            )
        return active["payload"]
