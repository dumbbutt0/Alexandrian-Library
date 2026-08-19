"""UI-neutral commands for the Alexandrian Library's durable workflow.

This module is an interface boundary, not another workflow controller.  It
delegates every canonical read and write to ``backend_selector`` and returns
structured values instead of presenting terminal prompts.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from datetime import datetime
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

import backend_selector as backend
import conversation_store
import transition_runtime


SCHEMA_VERSION = "1.0"
MAX_TURN_CHARACTERS = 100_000
MAX_BACKGROUND_BOOK_TRANSITIONS = 64
MAX_GUARDRAIL_RESEARCH_CONTINUATIONS = 1
GUARDRAIL_RESEARCH_CONTINUATION_PROMPT = (
    "Continue the bounded research requested by the parent review. Resolve "
    "only its documented evidence gaps, preserve unresolved human choices, "
    "and do not broaden the inquiry."
)
AUTO_DEFER_QUESTION_RESPONSE = (
    "Keep this decision explicitly unresolved. Do not attribute a settled "
    "position to me, and narrow the chapter so it does not depend on an "
    "invented answer."
)
AUTO_DEFER_AUTHORIZATION_NOTE = (
    "Preauthorized by the inquiry-level auto_defer question mode. The "
    "inquiry author explicitly deferred every named chapter-framing "
    "decision; preserve each as unresolved and narrow the chapter rather "
    "than inferring an answer."
)
AUTO_QUESTION_RETRY_CONSTRAINT = (
    "Preauthorized by the inquiry-level auto_defer question mode. Retry the "
    "same bounded contract once, perform exactly one canonical workspace "
    "transition, preserve every unresolved human question, and do not invent "
    "new authorial intent."
)
FINISH_MODES = {"standard", "guardrails"}
FINISH_GUARDRAILS_POLICY_VERSION = "finish-with-guardrails-v1"
GUARDRAIL_AUTOMATIC_CHECKPOINTS = (
    "research_source_permissions",
    "chapter_source_permissions",
    "chapter_human_input_closeout",
    "chapter_recovery",
    "research_access_recovery",
    "final_pass_repair",
)
GUARDRAIL_NONDELEGABLE_CHECKPOINTS = (
    "chapter_human_input",
    "chapter_draft_acceptance",
    "chapter_retry",
    "chapter_artifact_review",
)
ProgressCallback = Callable[[int, str], None]

WORKFLOW_STAGE_PROGRESS = {
    "conversation_active": 1,
    "research_review": 10,
    "review_checkpoint": 18,
    "review_held": 18,
    "research_continuation": 20,
    "research_continued": 20,
    "knowledge_promotion": 24,
    "book_plan": 30,
    "book_plan_checkpoint": 36,
    "book_plan_revision": 36,
    "book_plan_held": 36,
    "chapter_plan_blocked": 38,
    "chapter_development": 38,
    "candidate_assembly": 72,
    "book_synthesis": 78,
    "synthesis_audit": 86,
    "synthesis_correction": 88,
    "synthesis_receipt_recovery": 90,
    "synthesis_held": 90,
    "editorial_checkpoint": 94,
    "editorial_revision": 94,
    "editorial_revision_recovery": 94,
    "editorial_held": 94,
    "book_finalization": 96,
    "cover_design": 98,
    "bookshelf_projection": 99,
    "complete": 100,
    "workflow_error": 1,
}

EXPERIENCE_STAGE_COPY = {
    "conversation_active": (
        "conversation",
        "Shape the question",
        "Add context, challenge the answer, or ask the Library to begin the book.",
    ),
    "research_review": (
        "researching",
        "Find and test the evidence",
        "The Library is comparing sources and looking for meaningful disagreement.",
    ),
    "review_checkpoint": (
        "researching",
        "Check the research direction",
        "The first evidence review is ready for its next durable decision.",
    ),
    "review_held": (
        "paused_recoverably",
        "Research is safely paused",
        "Completed evidence remains preserved while the next step is decided.",
    ),
    "research_continuation": (
        "researching",
        "Resolve the remaining evidence gaps",
        "The Library is following the bounded questions identified by the review.",
    ),
    "research_continued": (
        "paused_recoverably",
        "Continued research is preserved",
        "The additional evidence is saved and ready for the next durable step.",
    ),
    "knowledge_promotion": (
        "planning",
        "Organize the useful evidence",
        "Verified research is being prepared for book planning.",
    ),
    "book_plan": (
        "planning",
        "Design the investigation",
        "The Library is turning the inquiry into a coherent book structure.",
    ),
    "book_plan_checkpoint": (
        "planning",
        "Check the book plan",
        "The proposed structure is ready for its next durable decision.",
    ),
    "book_plan_revision": (
        "planning",
        "Strengthen the book plan",
        "The structure is being revised without discarding accepted research.",
    ),
    "book_plan_held": (
        "paused_recoverably",
        "The book plan is safely paused",
        "Accepted planning work remains preserved for recovery.",
    ),
    "chapter_plan_blocked": (
        "paused_recoverably",
        "Chapter planning needs attention",
        "The accepted research remains safe while the blocked chapter is resolved.",
    ),
    "chapter_development": (
        "writing",
        "Write the book",
        "Chapters are being researched, challenged, and written into durable artifacts.",
    ),
    "candidate_assembly": (
        "writing",
        "Assemble the complete draft",
        "Accepted chapters are being joined into one reviewable manuscript.",
    ),
    "book_synthesis": (
        "synthesizing",
        "Connect the chapters",
        "The Library is shaping the draft into one continuous argument.",
    ),
    "synthesis_audit": (
        "verifying",
        "Test the complete book",
        "The assembled argument and its evidence are being audited together.",
    ),
    "synthesis_correction": (
        "verifying",
        "Correct the complete book",
        "A bounded correction is preserving accepted work while repairing the draft.",
    ),
    "synthesis_receipt_recovery": (
        "verifying",
        "Recover the verification record",
        "The manuscript is preserved while its durable audit receipt is repaired.",
    ),
    "synthesis_held": (
        "paused_recoverably",
        "The complete draft is safely paused",
        "Accepted chapters and evidence remain preserved for recovery.",
    ),
    "editorial_checkpoint": (
        "ready_for_review",
        "Your review edition is ready",
        "Read the complete draft and decide whether it is ready to become a final edition.",
    ),
    "editorial_revision": (
        "verifying",
        "Revise the review edition",
        "The requested editorial changes are being applied to the preserved draft.",
    ),
    "editorial_revision_recovery": (
        "verifying",
        "Recover the editorial revision",
        "The review edition remains safe while the bounded revision is repaired.",
    ),
    "editorial_held": (
        "paused_recoverably",
        "The review edition is safely paused",
        "The manuscript remains available while the editorial decision is resolved.",
    ),
    "book_finalization": (
        "publishing",
        "Prepare the final edition",
        "The accepted manuscript is being finalized without changing its argument.",
    ),
    "cover_design": (
        "publishing",
        "Create the finished volume",
        "The verified edition is receiving its final cover and presentation.",
    ),
    "bookshelf_projection": (
        "publishing",
        "Place the book on the shelf",
        "The final edition is being verified for the enduring catalog.",
    ),
    "complete": (
        "complete",
        "Verified edition ready",
        "The book, evidence, and acceptance records are complete.",
    ),
    "workflow_error": (
        "terminal_error",
        "The Library needs help",
        "A durable inconsistency must be resolved before work can continue.",
    ),
}


def _report_progress(
    callback: ProgressCallback | None,
    percent: int,
    phase: str,
) -> None:
    if callback is not None:
        callback(max(0, min(100, percent)), phase)


class WorkflowServiceError(RuntimeError):
    """Base error carrying one stable machine-readable service code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkflowNotFoundError(WorkflowServiceError):
    """Raised when a requested canonical conversation does not exist."""


class WorkflowConflictError(WorkflowServiceError):
    """Raised when an action is illegal for the derived current state."""


class WorkflowOperationError(WorkflowServiceError):
    """Raised when an allowed backend operation fails durably."""


HUMAN_CHECKPOINT_STAGES: set[str] = set()
CHECKPOINT_KINDS = {
    "review_checkpoint": "research_review",
    "book_plan_checkpoint": "book_plan",
    "editorial_checkpoint": "editorial",
}
TERMINAL_HOLD_STAGES = {
    "review_held",
    "research_continued",
    "book_plan_held",
    "chapter_plan_blocked",
    "synthesis_held",
    "editorial_held",
}

CHAPTER_TERMINAL_STATUSES = {
    "assessment_extension_consumed",
    "controller_no_progress",
    "controller_retry_exhausted",
    "final_pass_repair_failed",
    "insufficient_closeout_budget",
    "recovery_call_limit_reached",
    "research_access_recovery_failed",
    "research_incomplete",
    "research_limit_reached",
    "stalled",
}


def _conversation_path(conversation_id: str) -> Path:
    if (
        not isinstance(conversation_id, str)
        or not conversation_store.CONVERSATION_ID_PATTERN.fullmatch(
            conversation_id
        )
    ):
        raise WorkflowNotFoundError(
            "conversation_not_found",
            "Conversation ID has an invalid format.",
        )
    path = backend.CONVERSATIONS_PATH / f"{conversation_id}.jsonl"
    if not path.is_file():
        raise WorkflowNotFoundError(
            "conversation_not_found",
            f"Conversation does not exist: {conversation_id}",
        )
    return path


def finish_guardrails_policy_path(conversation_id: str) -> Path:
    """Return the inquiry-local durable Finish authority record."""
    _conversation_path(conversation_id)
    return backend.CONVERSATIONS_PATH / f"{conversation_id}.finish-policy.json"


def load_finish_guardrails_policy(
    conversation_id: str,
) -> dict[str, Any] | None:
    """Load and strictly validate one inquiry-scoped Finish policy."""
    path = finish_guardrails_policy_path(conversation_id)
    authorized_conversation_id = conversation_id
    if not path.is_file():
        lineage_paths = _conversation_lineage_paths(
            _conversation_path(conversation_id)
        )
        authorized_conversation_id = lineage_paths[0].stem
        path = backend.CONVERSATIONS_PATH / (
            f"{authorized_conversation_id}.finish-policy.json"
        )
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as error:
        raise WorkflowOperationError(
            "finish_policy_unreadable",
            "Finish guardrails policy could not be read.",
        ) from error
    expected_limits = {
        "max_final_pass_repair_calls_per_chapter": (
            backend.MAX_FINAL_PASS_REPAIR_CALLS_TOTAL
        ),
        "max_pending_draft_recovery_calls_per_chapter": (
            backend.MAX_PENDING_DRAFT_RECOVERY_CALLS_TOTAL
        ),
        "max_research_access_recovery_calls_per_chapter": (
            backend.MAX_RESEARCH_ACCESS_RECOVERY_CALLS_TOTAL
        ),
        "max_new_source_plans_per_research_recovery": 1,
        "max_new_research_packets_per_research_recovery": 1,
    }
    if (
        not isinstance(policy, dict)
        or policy.get("schema_version") != SCHEMA_VERSION
        or policy.get("policy_version")
        != FINISH_GUARDRAILS_POLICY_VERSION
        or policy.get("conversation_id") != authorized_conversation_id
        or policy.get("scope") != "inquiry"
        or policy.get("authorized_by") != "human_finish_command"
        or policy.get("authorized_from_stage") != "conversation_active"
        or policy.get("source_authority")
        != "exact_url_allow_once"
        or policy.get("automatic_checkpoint_kinds")
        != list(GUARDRAIL_AUTOMATIC_CHECKPOINTS)
        or policy.get("nondelegable_checkpoint_kinds")
        != list(GUARDRAIL_NONDELEGABLE_CHECKPOINTS)
        or policy.get("limits") != expected_limits
        or not isinstance(policy.get("authorized_at"), str)
        or not isinstance(policy.get("authorized_from_revision"), str)
        or len(policy["authorized_from_revision"]) != 64
        or any(
            character not in "0123456789abcdef"
            for character in policy["authorized_from_revision"]
        )
        or policy.get("finish_policy_sha256")
        != backend.canonical_json_sha256(
            policy,
            "finish_policy_sha256",
        )
    ):
        raise WorkflowOperationError(
            "finish_policy_invalid",
            "Finish guardrails policy failed integrity validation.",
        )
    return policy


def record_finish_guardrails_policy(
    conversation_id: str,
    *,
    authorized_from_stage: str,
    authorized_from_revision: str,
) -> dict[str, Any]:
    """Persist the bounded authority granted by the explicit Finish action."""
    existing = load_finish_guardrails_policy(conversation_id)
    if existing is not None:
        return existing
    policy = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": FINISH_GUARDRAILS_POLICY_VERSION,
        "conversation_id": conversation_id,
        "scope": "inquiry",
        "authorized_at": backend.current_utc_time(),
        "authorized_by": "human_finish_command",
        "authorized_from_stage": authorized_from_stage,
        "authorized_from_revision": authorized_from_revision,
        "source_authority": "exact_url_allow_once",
        "automatic_checkpoint_kinds": list(
            GUARDRAIL_AUTOMATIC_CHECKPOINTS
        ),
        "nondelegable_checkpoint_kinds": list(
            GUARDRAIL_NONDELEGABLE_CHECKPOINTS
        ),
        "limits": {
            "max_final_pass_repair_calls_per_chapter": (
                backend.MAX_FINAL_PASS_REPAIR_CALLS_TOTAL
            ),
            "max_pending_draft_recovery_calls_per_chapter": (
                backend.MAX_PENDING_DRAFT_RECOVERY_CALLS_TOTAL
            ),
            "max_research_access_recovery_calls_per_chapter": (
                backend.MAX_RESEARCH_ACCESS_RECOVERY_CALLS_TOTAL
            ),
            "max_new_source_plans_per_research_recovery": 1,
            "max_new_research_packets_per_research_recovery": 1,
        },
    }
    policy["finish_policy_sha256"] = backend.canonical_json_sha256(
        policy,
        "finish_policy_sha256",
    )
    backend.atomic_write_json(
        finish_guardrails_policy_path(conversation_id),
        policy,
    )
    return policy


def transition_events(
    conversation_id: str,
    *,
    limit: int = 100,
) -> dict[str, Any]:
    """Return append-only canonical transition events for one inquiry."""
    _conversation_path(conversation_id)
    events = transition_runtime.read_events(
        backend.BOOKS_PATH
        / f"book-{conversation_id}"
        / "runs"
        / "transition.events.jsonl",
        limit=limit,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation_id,
        "event_count": len(events),
        "events": events,
    }


def workflow_overall_progress(stage: str) -> int:
    """Return a stable milestone percentage for the complete book workflow."""
    return WORKFLOW_STAGE_PROGRESS.get(stage, 1)


def canonical_workflow_progress(
    conversation_id: str,
    stage: str,
    lineage_paths: list[Path],
    reviewed_conversation_ids: list[str],
) -> dict[str, Any]:
    """Measure 1-100 completion from durable artifacts, never provider guesses."""
    percent = workflow_overall_progress(stage)
    basis = "durable_stage"
    completed_units = None
    total_units = None

    if stage == "chapter_development":
        book_id = f"book-{conversation_id}"
        manifest = backend.load_workflow_json(
            backend.BOOKS_PATH / book_id / "manifest.json",
            "Book manifest",
        )
        included = [
            chapter
            for chapter in manifest.get("chapter_plan", [])
            if isinstance(chapter, dict)
            and chapter.get("material_disposition") in {"include", "appendix"}
        ]
        completed_units = 0
        total_units = len(included)
        for chapter in included:
            chapter_id = chapter.get("chapter_id")
            if not isinstance(chapter_id, str):
                continue
            run_path = backend.chapter_run_path(book_id, chapter_id)
            if not run_path.is_file():
                continue
            run = backend.load_workflow_json(run_path, f"{chapter_id} run state")
            if run.get("status") == "chapter_complete":
                completed_units += 1
        if total_units:
            percent = min(
                71,
                38 + round(33 * completed_units / total_units),
            )
            basis = "completed_chapters"

    if reviewed_conversation_ids and len(lineage_paths) > 1:
        lineage_floor = WORKFLOW_STAGE_PROGRESS["research_continuation"]
        if percent < lineage_floor:
            percent = lineage_floor
            basis = "durable_lineage"

    percent = max(1, min(100, percent))
    return {
        "percent": percent,
        "scale_min": 1,
        "scale_max": 100,
        "basis": basis,
        "stage": stage,
        "completed_units": completed_units,
        "total_units": total_units,
        "final": stage == "complete",
    }


def workflow_experience_projection(
    stage: str,
    pending_action: dict[str, Any],
    *,
    book_creation_active: bool,
) -> dict[str, Any]:
    """Translate canonical state into calm user-facing copy without authority."""
    state, headline, detail = EXPERIENCE_STAGE_COPY.get(
        stage,
        (
            "starting",
            "Prepare the next durable step",
            "The inquiry is preserved while the Library determines what comes next.",
        ),
    )
    action_kind = pending_action.get("kind")
    decision_kind = pending_action.get("decision_kind")
    attention_required = False
    primary_action = None

    if stage == "editorial_checkpoint":
        primary_action = "review_edition"
        attention_required = True
    elif action_kind == "human_checkpoint" and decision_kind in {
        "research_source_permissions",
        "chapter_source_permissions",
    }:
        state = "needs_source_decision"
        headline = "A source needs your permission"
        detail = (
            "Choose whether this exact source may be used. The book remains safely paused."
        )
        primary_action = "review_source"
        attention_required = True
    elif action_kind == "human_checkpoint":
        state = "needs_human_judgment"
        headline = "The Library needs your judgment"
        detail = (
            "The next step would change authorship or meaning, so the Library will not decide for you."
        )
        primary_action = "record_decision"
        attention_required = True
    elif action_kind == "terminal_hold":
        state = "paused_recoverably"
        headline = "The book is safely paused"
        detail = (
            "Completed work remains preserved. Review the recovery details before continuing."
        )
        primary_action = "review_recovery"
        attention_required = True
    elif action_kind == "workflow_error":
        state = "terminal_error"
        headline = "The Library needs help"
        detail = "A durable inconsistency must be resolved before work can continue."
        primary_action = "review_recovery"
        attention_required = True
    elif action_kind == "conversation_input":
        primary_action = "continue_inquiry"
    elif action_kind == "backend_transition" and book_creation_active:
        primary_action = "resume_book"
    elif action_kind == "complete":
        primary_action = "open_book"

    return {
        "schema_version": "workflow-experience.v1",
        "state": state,
        "headline": headline,
        "detail": detail,
        "primary_action": primary_action,
        "attention_required": attention_required,
        "can_leave": stage != "workflow_error",
        "technical_detail_available": True,
    }


def _workflow_timeline(conversation_id: str, stage: str) -> list[dict[str, Any]]:
    """Project a compact, read-only history from canonical transition events."""
    records = transition_events(conversation_id, limit=500).get("events", [])
    meaningful_phases = {
        "durable_artifact_saved",
        "mechanical_retry_scheduled",
        "provider_failed",
        "validation_failed",
    }
    timeline = []
    for event in records:
        phase = event.get("phase")
        if phase not in meaningful_phases:
            continue
        kind = str(event.get("kind") or "workflow transition")
        timeline.append(
            {
                "kind": "transition",
                "label": kind.replace("_", " ").strip().title(),
                "detail": str(phase).replace("_", " ").strip().capitalize(),
                "status": (
                    "attention"
                    if phase in {"provider_failed", "validation_failed"}
                    else "retry"
                    if phase == "mechanical_retry_scheduled"
                    else "complete"
                ),
                "recorded_at": event.get("recorded_at"),
                "provider": event.get("provider"),
                "attempt": event.get("mechanical_attempt"),
            }
        )
    timeline = timeline[-7:]
    timeline.append(
        {
            "kind": "current",
            "label": backend.WORKFLOW_STAGE_LABELS.get(
                stage,
                "Active conversation" if stage == "conversation_active" else "Workflow hold",
            ),
            "detail": "Current durable state",
            "status": "current" if stage != "complete" else "complete",
            "recorded_at": None,
        }
    )
    return timeline


def _finish_readiness(
    *,
    stage: str,
    pending_action: dict[str, Any],
    research_mode: str,
    question_mode: str,
    finish_policy: dict[str, Any] | None,
    message_count: int,
) -> dict[str, Any]:
    """Explain what one guarded Finish can and cannot complete."""
    action_kind = pending_action.get("kind")
    decision_kind = pending_action.get("decision_kind")
    auto_deferred_question = (
        action_kind == "human_checkpoint"
        and decision_kind == "chapter_human_input"
        and question_mode == "auto_defer"
    )
    nondelegable = (
        action_kind == "human_checkpoint"
        and decision_kind in GUARDRAIL_NONDELEGABLE_CHECKPOINTS
        and not auto_deferred_question
    )
    terminal = action_kind in {"terminal_hold", "workflow_error"}
    if stage == "complete":
        status = "complete"
        summary = "The verified edition and its acceptance records are complete."
    elif stage == "conversation_active":
        status = "ready" if message_count >= 2 else "needs_context"
        summary = (
            "One guarded Finish can seal the inquiry and continue through bounded mechanical work."
            if status == "ready"
            else "Add enough inquiry context for the research workflow, then use guarded Finish."
        )
    elif nondelegable:
        status = "decision_required"
        summary = "The workflow is healthy but requires a human authorship decision before it can continue."
    elif terminal:
        status = "recovery_required"
        summary = "The workflow reached a durable hold that needs an explicit recovery choice."
    else:
        status = "advancing"
        summary = "The book workflow is underway and can continue from its current durable stage."

    pending_sources = 0
    context = pending_action.get("context")
    if isinstance(context, dict):
        requests = context.get("pending_requests")
        if isinstance(requests, list):
            pending_sources = len(requests)
    guardrails_enabled = finish_policy is not None
    return {
        "schema_version": "finish-readiness.v1",
        "status": status,
        "summary": summary,
        "finish_available": stage == "conversation_active" and message_count >= 2,
        "guardrails_enabled": guardrails_enabled,
        "checks": [
            {
                "id": "conversation_context",
                "label": "Inquiry context",
                "status": "pass" if message_count >= 2 else "attention",
                "detail": f"{message_count} durable messages recorded",
            },
            {
                "id": "source_authority",
                "label": "Source authority",
                "status": "pass",
                "detail": (
                    "Exact requested URLs may be allowed once for this inquiry"
                    if research_mode == "auto_approve" or guardrails_enabled
                    else "Unknown sources pause for review"
                ),
            },
            {
                "id": "pending_sources",
                "label": "Pending source decisions",
                "status": "attention" if pending_sources else "pass",
                "detail": f"{pending_sources} currently waiting",
            },
            {
                "id": "human_authority",
                "label": "Human-only decisions",
                "status": "attention" if nondelegable else "pass",
                "detail": (
                    f"{str(decision_kind).replace('_', ' ')} requires your decision"
                    if nondelegable
                    else "No human-only decision is currently waiting"
                ),
            },
            {
                "id": "durable_recovery",
                "label": "Recovery boundary",
                "status": "attention" if terminal else "pass",
                "detail": (
                    "An explicit recovery choice is required"
                    if terminal
                    else "Bounded retries and resumable artifacts are available"
                ),
            },
        ],
        "will_continue_automatically": [
            "research and planning transitions",
            "inquiry-scoped exact-URL source approvals",
            *(
                ["explicit deferral of new chapter-framing questions"]
                if question_mode == "auto_defer"
                else []
            ),
            "bounded mechanical validation and recovery",
            "assembly, audit, cover design, and shelf publication",
        ],
        "will_pause_for": [
            "new authorship claims",
            "acceptance of an assessed chapter draft",
            "changed-condition retries or artifact disputes",
            "safety decisions outside the recorded policy",
        ],
    }


def _review_status(conversation_id: str, stage: str) -> dict[str, Any]:
    """Separate an assembled review edition from explicit human approval."""
    if stage != "complete":
        return {
            "status": "not_ready",
            "label": "Book generation in progress",
            "human_approval_recorded": False,
        }
    assembly_path = (
        backend.BOOKS_PATH / f"book-{conversation_id}" / "assembly.json"
    )
    try:
        assembly = backend.load_workflow_json(assembly_path, "Final assembly")
    except RuntimeError:
        return {
            "status": "verification_error",
            "label": "Review edition needs verification",
            "human_approval_recorded": False,
        }
    automatic = assembly.get("finalization_authority") == "automated_audit_policy"
    return {
        "status": "generated_for_review" if automatic else "human_approved_final",
        "label": "Review edition ready" if automatic else "Human-approved final edition",
        "human_approval_recorded": not automatic,
        "finalization_authority": assembly.get("finalization_authority"),
        "workflow_policy_version": assembly.get("workflow_policy_version"),
    }


def runtime_event_path(conversation_id: str) -> Path:
    return (
        backend.BOOKS_PATH
        / f"book-{conversation_id}"
        / "runs"
        / "runtime.events.jsonl"
    )


def append_runtime_event(conversation_id: str, event: dict[str, Any]) -> None:
    """Append one fsynced UI-runtime observation without rewriting history."""
    _conversation_path(conversation_id)
    path = runtime_event_path(conversation_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _seconds_between(start: Any, end: Any) -> float | None:
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    try:
        return max(0.0, round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds(), 1))
    except ValueError:
        return None


def build_e2e_run_receipt(conversation_id: str) -> dict[str, Any]:
    """Build the human-readable proof record for one complete workflow run."""
    conversation_path = _conversation_path(conversation_id)
    lineage_paths = _conversation_lineage_paths(conversation_path)
    conversation_events = []
    provider_sequence = []
    for lineage_path in lineage_paths:
        events = backend.load_conversation_events(lineage_path)
        conversation_events.extend(events)
        for event in events:
            if event.get("type") != "backend_selected":
                continue
            host = event.get("payload", {}).get("agent_host", {}).get("name")
            if isinstance(host, str) and (not provider_sequence or provider_sequence[-1] != host):
                provider_sequence.append(host)

    book_id = f"book-{conversation_id}"
    book_directory = backend.BOOKS_PATH / book_id
    runtime_events = _read_jsonl(runtime_event_path(conversation_id))
    transition_records = transition_runtime.read_events(
        book_directory / "runs" / "transition.events.jsonl",
        limit=10_000,
    )
    source_decisions = []
    session_paths = [backend.REVIEW_SESSIONS_PATH / f"{conversation_id}.json"]
    session_paths.extend(sorted((book_directory / "research").glob("*/sessions/*.json")))
    for session_path in session_paths:
        if not session_path.is_file():
            continue
        try:
            session = backend.load_workflow_json(session_path, "Source session")
        except RuntimeError:
            continue
        for decision in session.get("decisions", []):
            if isinstance(decision, dict):
                source_decisions.append({
                    key: decision.get(key)
                    for key in ("request_id", "domain", "url", "decision", "decided_at")
                })

    transition_groups: dict[str, list[dict[str, Any]]] = {}
    for event in transition_records:
        transition_id = event.get("transition_id")
        if isinstance(transition_id, str):
            transition_groups.setdefault(transition_id, []).append(event)
    transitions = []
    for transition_id, events in transition_groups.items():
        transitions.append({
            "transition_id": transition_id,
            "kind": events[0].get("kind"),
            "provider": events[0].get("provider"),
            "started_at": events[0].get("recorded_at"),
            "ended_at": events[-1].get("recorded_at"),
            "elapsed_seconds": _seconds_between(events[0].get("recorded_at"), events[-1].get("recorded_at")),
            "final_phase": events[-1].get("phase"),
            "mechanical_attempts": max(int(event.get("mechanical_attempt", 0)) for event in events),
        })

    assembly_path = book_directory / "assembly.json"
    assembly = backend.load_workflow_json(assembly_path, "Final assembly") if assembly_path.is_file() else {}
    runtime_sessions = []
    seen_sessions = set()
    for event in runtime_events:
        session_id = event.get("runtime_session_id")
        if not isinstance(session_id, str) or session_id in seen_sessions:
            continue
        seen_sessions.add(session_id)
        runtime_sessions.append({
            "runtime_session_id": session_id,
            "build_sha256": event.get("build_sha256"),
            "process_id": event.get("process_id"),
            "server_started_at": event.get("server_started_at"),
        })
    job_summaries = [
        {key: event.get(key) for key in (
            "job_id", "kind", "provider", "event", "recorded_at", "elapsed_seconds", "status"
        )}
        for event in runtime_events
    ]
    first_created = conversation_events[0].get("created_at") if conversation_events else None
    finished_events = [event for event in conversation_events if event.get("type") == "conversation_finished"]
    finished_at = finished_events[-1].get("created_at") if finished_events else None
    artifacts = {
        "book_path": assembly.get("book_path"),
        "book_sha256": assembly.get("book_sha256"),
        "assembly_path": str(assembly_path.relative_to(backend.PROJECT_PATH)) if assembly else None,
        "assembly_sha256": backend.file_sha256(assembly_path) if assembly else None,
        "synthesis_receipt_path": assembly.get("synthesis_receipt_path"),
        "synthesis_receipt_sha256": assembly.get("synthesis_receipt_file_sha256"),
        "synthesis_audit_sha256": assembly.get("synthesis_audit_sha256"),
    }
    finish_policy = load_finish_guardrails_policy(conversation_id)
    return {
        "schema_version": "e2e-run-receipt.v1",
        "conversation_id": conversation_id,
        "book_id": book_id,
        "status": "complete" if assembly else "in_progress",
        "review_state": _review_status(conversation_id, "complete" if assembly else "workflow_error"),
        "workflow_policy_version": assembly.get("workflow_policy_version"),
        "finish_guardrails": (
            {
                "enabled": True,
                "policy_version": finish_policy["policy_version"],
                "policy_sha256": finish_policy["finish_policy_sha256"],
                "scope": finish_policy["scope"],
                "source_authority": finish_policy["source_authority"],
                "automatic_checkpoint_kinds": finish_policy[
                    "automatic_checkpoint_kinds"
                ],
                "nondelegable_checkpoint_kinds": finish_policy[
                    "nondelegable_checkpoint_kinds"
                ],
                "limits": finish_policy["limits"],
            }
            if finish_policy is not None
            else {"enabled": False}
        ),
        "provider_sequence": provider_sequence,
        "provider_switch_count": max(0, len(provider_sequence) - 1),
        "source_decisions": source_decisions,
        "source_decision_counts": {
            decision: sum(1 for item in source_decisions if item.get("decision") == decision)
            for decision in ("reject", "allow_once", "allow_domain")
        },
        "runtime_sessions": runtime_sessions,
        "observed_restart_count": max(0, len(runtime_sessions) - 1),
        "jobs": job_summaries,
        "transitions": transitions,
        "mechanical_retry_count": sum(1 for event in transition_records if event.get("phase") == "mechanical_retry_scheduled"),
        "timings": {
            "conversation_seconds": _seconds_between(first_created, finished_at),
            "background_runtime_seconds": round(sum(float(event.get("elapsed_seconds", 0) or 0) for event in runtime_events if event.get("event") == "job_finished"), 1),
            "transition_seconds": round(sum(float(item.get("elapsed_seconds", 0) or 0) for item in transitions), 1),
        },
        "artifacts": artifacts,
        "generated_at": backend.current_utc_time(),
    }


def write_e2e_run_receipt(conversation_id: str) -> dict[str, Any]:
    receipt = build_e2e_run_receipt(conversation_id)
    if receipt["status"] != "complete":
        return receipt
    backend.atomic_write_json(
        backend.BOOKS_PATH / f"book-{conversation_id}" / "run_receipt.json",
        receipt,
    )
    return receipt


def _conversation_lineage_paths(path: Path) -> list[Path]:
    """Return root-to-current conversation paths for one durable lineage."""
    reverse_paths = []
    visited = set()
    current = path
    while True:
        if current.stem in visited:
            raise WorkflowOperationError(
                "conversation_lineage_cycle",
                "Conversation continuation lineage contains a cycle.",
            )
        visited.add(current.stem)
        reverse_paths.append(current)
        try:
            lineage = backend.continuation_lineage(current)
        except RuntimeError as error:
            raise WorkflowOperationError(
                "conversation_lineage_unreadable",
                str(error),
            ) from error
        if lineage is None:
            break
        current = _conversation_path(lineage["parent_conversation_id"])
    return list(reversed(reverse_paths))


def _selected_backend(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        if event.get("type") != "backend_selected":
            continue
        payload = event.get("payload")
        agent_host = payload.get("agent_host") if isinstance(payload, dict) else None
        selected = agent_host.get("name") if isinstance(agent_host, dict) else None
        return selected if selected in backend.BACKENDS.values() else None
    return None


def _research_mode(events: list[dict[str, Any]]) -> str:
    for event in events:
        if event.get("type") != "conversation_started":
            continue
        payload = event.get("payload")
        mode = payload.get("research_mode") if isinstance(payload, dict) else None
        if mode in backend.RESEARCH_MODES:
            return mode
        break
    return backend.DEFAULT_RESEARCH_MODE


def _effective_research_mode(
    conversation_id: str,
    events: list[dict[str, Any]],
) -> str:
    """Return the durable inquiry override when one has been recorded."""
    session_path = backend.review_session_path(conversation_id)
    if session_path.is_file():
        session = backend.load_review_session(conversation_id)
        mode = session.get("research_mode")
        if mode in backend.RESEARCH_MODES:
            return mode
    for event in events:
        if event.get("type") != "conversation_started":
            continue
        payload = event.get("payload")
        continuation = (
            payload.get("continuation")
            if isinstance(payload, dict)
            else None
        )
        parent_id = (
            continuation.get("parent_conversation_id")
            if isinstance(continuation, dict)
            else None
        )
        if not isinstance(parent_id, str):
            break
        try:
            _conversation_path(parent_id)
        except WorkflowServiceError:
            break
        parent_session_path = backend.review_session_path(parent_id)
        if parent_session_path.is_file():
            parent_session = backend.load_review_session(parent_id)
            inherited_mode = parent_session.get("research_mode")
            if inherited_mode in backend.RESEARCH_MODES:
                return inherited_mode
        break
    return _research_mode(events)


def _effective_question_mode(
    conversation_id: str,
    events: list[dict[str, Any]],
) -> str:
    """Return the nearest durable question-mode choice in the lineage."""
    current_id = conversation_id
    current_events = events
    visited: set[str] = set()
    while current_id not in visited:
        visited.add(current_id)
        session_path = backend.review_session_path(current_id)
        if session_path.is_file():
            session = backend.load_review_session(current_id)
            mode = session.get(
                "question_mode",
                backend.DEFAULT_QUESTION_MODE,
            )
            if mode in backend.QUESTION_MODES:
                if (
                    mode != backend.DEFAULT_QUESTION_MODE
                    or "question_mode" in session
                ):
                    return mode
        parent_id = None
        for event in current_events:
            if event.get("type") != "conversation_started":
                continue
            payload = event.get("payload")
            continuation = (
                payload.get("continuation")
                if isinstance(payload, dict)
                else None
            )
            candidate = (
                continuation.get("parent_conversation_id")
                if isinstance(continuation, dict)
                else None
            )
            if isinstance(candidate, str):
                parent_id = candidate
            break
        if parent_id is None:
            break
        try:
            parent_path = _conversation_path(parent_id)
        except WorkflowServiceError:
            break
        current_id = parent_id
        current_events = backend.load_conversation_events(parent_path)
    return backend.DEFAULT_QUESTION_MODE


def _pending_action(stage: str) -> dict[str, Any]:
    if stage == "conversation_active":
        return {
            "kind": "conversation_input",
            "commands": ["submit_turn", "finish_conversation"],
        }
    if stage in TERMINAL_HOLD_STAGES:
        return {
            "kind": "terminal_hold",
            "stage": stage,
            "command_available": False,
        }
    if stage == "complete":
        return {"kind": "complete", "command_available": False}
    if stage == "workflow_error":
        return {"kind": "workflow_error", "command_available": False}
    return {
        "kind": "backend_transition",
        "stage": stage,
        "command": "advance_workflow",
        "command_available": True,
    }


def _checkpoint_pending_action(
    conversation_id: str,
    stage: str,
) -> dict[str, Any]:
    """Project canonical checkpoint context without granting new authority."""
    action = _pending_action(stage)
    if stage == "research_review":
        session = backend.load_review_session(conversation_id)
        pending_requests = session.get("pending_requests", [])
        if pending_requests:
            return {
                "kind": "human_checkpoint",
                "stage": stage,
                "decision_kind": "research_source_permissions",
                "command_available": True,
                "context": {
                    "pending_requests": pending_requests,
                    "allowed_decisions": [
                        "allow_once",
                        "allow_domain",
                        "reject",
                    ],
                },
            }
    if stage == "review_checkpoint":
        document = backend.load_workflow_json(
            backend.REVIEWS_PATH / f"{conversation_id}.json",
            "Canonical research review",
        )
        review = document.get("review", {})
        route = review.get("research_route")
        eligible = route == "ready_for_book_consideration"
        action["context"] = {
            "core_question": review.get("core_question", {}).get("text"),
            "research_route": route,
            "book_planning_eligible": eligible,
            "curiosity_signature": review.get("curiosity_signature"),
            "decision_mode": "automatic",
            "workflow_policy_version": backend.WORKFLOW_POLICY_VERSION,
        }
    elif stage == "book_plan_checkpoint":
        manifest = backend.load_workflow_json(
            backend.BOOKS_PATH
            / f"book-{conversation_id}"
            / "manifest.json",
            "Book manifest",
        )
        action["context"] = {
            "working_title": manifest.get("working_title"),
            "central_question": manifest.get("central_question"),
            "book_mode": manifest.get("book_mode"),
            "chapters": [
                {
                    "chapter_id": chapter.get("chapter_id"),
                    "title": chapter.get("title"),
                    "objective": chapter.get("objective"),
                    "material_disposition": chapter.get(
                        "material_disposition"
                    ),
                    "draft_gate": chapter.get(
                        "draft_gate",
                        chapter.get("readiness"),
                    ),
                }
                for chapter in manifest.get("chapter_plan", [])
                if isinstance(chapter, dict)
            ],
            "decision_mode": "automatic",
            "workflow_policy_version": backend.WORKFLOW_POLICY_VERSION,
        }
    elif stage == "editorial_checkpoint":
        book_directory = backend.BOOKS_PATH / f"book-{conversation_id}"
        (
            receipt,
            _,
            included_chapter_ids,
            outstanding_requirements,
        ) = backend._editorial_checkpoint_context(
            backend.book_synthesis.synthesis_receipt_path(
                backend.PROJECT_PATH,
                f"book-{conversation_id}",
            ),
            book_directory / "manifest.json",
        )
        manifest = backend.load_workflow_json(
            book_directory / "manifest.json",
            "Book manifest",
        )
        action["context"] = {
            "working_title": manifest.get("working_title"),
            "edition_sha256": receipt.get("book_sha256"),
            "synthesis_attempt": receipt.get("selected_attempt"),
            "included_chapter_ids": included_chapter_ids,
            "outstanding_finalization_requirements": (
                outstanding_requirements
            ),
            "decision_mode": "automatic",
            "workflow_policy_version": backend.WORKFLOW_POLICY_VERSION,
        }
    elif stage == "chapter_development":
        chapter_action = _chapter_pending_action(conversation_id)
        if chapter_action is not None:
            return chapter_action
    return action


def _active_chapter_run(
    conversation_id: str,
) -> tuple[str, str, dict[str, Any]] | None:
    """Find the first dependency-ready, incomplete chapter with durable state."""
    book_id = f"book-{conversation_id}"
    manifest = backend.load_workflow_json(
        backend.BOOKS_PATH / book_id / "manifest.json",
        "Book manifest",
    )
    completed: set[str] = set()
    for chapter in manifest.get("chapter_plan", []):
        if (
            not isinstance(chapter, dict)
            or chapter.get("material_disposition")
            not in {"include", "appendix"}
        ):
            continue
        chapter_id = chapter.get("chapter_id")
        if not isinstance(chapter_id, str):
            raise RuntimeError("Approved book plan has an invalid chapter.")
        run_path = backend.chapter_run_path(book_id, chapter_id)
        if run_path.is_file():
            run = backend.load_workflow_json(
                run_path,
                f"{chapter_id} run state",
            )
            if run.get("status") == "chapter_complete":
                completed.add(chapter_id)
                continue
        else:
            run = None
        if not backend.manifest_chapter_is_draft_ready(chapter):
            continue
        dependencies = chapter.get("dependencies", [])
        if (
            not isinstance(dependencies, list)
            or any(not isinstance(item, str) for item in dependencies)
        ):
            raise RuntimeError(
                f"{chapter_id} has invalid chapter dependencies."
            )
        if not set(dependencies).issubset(completed):
            continue
        if run is None:
            return None
        return book_id, chapter_id, run
    return None


def _human_chapter_action(
    decision_kind: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "human_checkpoint",
        "stage": "chapter_development",
        "decision_kind": decision_kind,
        "command_available": True,
        "context": context,
    }


def _chapter_retry_action(
    book_id: str,
    chapter_id: str,
) -> dict[str, Any] | None:
    """Project a durable retry or artifact-review hold from the ledger."""
    snapshot = backend.build_chapter_controller_snapshot(
        book_id,
        chapter_id,
    )
    decision = backend.chapter_controller.decide_next_action(snapshot)
    ledger = backend.chapter_controller.ChapterEventLedger(
        backend.chapter_controller_ledger_path(book_id, chapter_id)
    )
    events = ledger.read() if ledger.path.is_file() else []
    active = ledger.active_contract(events)
    if active is not None:
        contract_id = active["payload"]["contract_id"]
        review = ledger.artifact_review_requirement(contract_id)
        if review is not None:
            if (
                review.get("failure_kind") == "InterruptedProviderCall"
                and review.get("artifact_changes", []) == []
            ):
                return None
            return _human_chapter_action(
                "chapter_artifact_review",
                {
                    "book_id": book_id,
                    "chapter_id": chapter_id,
                    "failed_contract_id": contract_id,
                    "call_kind": review.get("call_kind"),
                    "reason": review.get("message"),
                    "artifact_changes": review.get(
                        "artifact_changes",
                        [],
                    ),
                },
            )
    if backend.can_accept_auto_deferred_chapter_draft(
        book_id,
        chapter_id,
    ):
        return {
            "kind": "backend_transition",
            "stage": "chapter_development",
            "command": "advance_workflow",
            "command_available": True,
            "operation": "accept_auto_deferred_chapter_draft",
            "context": {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "reason": (
                    "The evaluator's only remaining blockers are choices "
                    "that Auto questions already preserved as explicitly "
                    "unresolved. The current assessed draft can close "
                    "without another provider call or inferred answer."
                ),
            },
        }
    requirement = ledger.retry_requirement(snapshot, decision)
    if requirement is None:
        return None
    if backend.can_apply_deferred_chapter_human_input(book_id, chapter_id):
        return {
            "kind": "backend_transition",
            "stage": "chapter_development",
            "command": "advance_workflow",
            "command_available": True,
            "operation": "apply_deferred_chapter_human_input",
            "context": {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "reason": (
                    "Auto questions already recorded an exact defer-all answer. "
                    "The Library can apply that answer deterministically without "
                    "another provider call or human retry checkpoint."
                ),
            },
        }
    if (
        requirement.get("status") == "exhausted"
        and backend.stale_graph_retry_repair_evidence(book_id, chapter_id)
        is not None
    ):
        return {
            "kind": "backend_transition",
            "stage": "chapter_development",
            "command": "advance_workflow",
            "command_available": True,
            "operation": "repair_stale_graph_retry_exhaustion",
            "context": {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "reason": (
                    "The chapter attempts were consumed by an obsolete "
                    "global-graph snapshot validator. The Library can record "
                    "that proven system repair and reopen one fresh contract."
                ),
            },
        }
    if (
        requirement.get("status") == "exhausted"
        and backend.planned_revision_draft_misroute_repair_evidence(
            book_id,
            chapter_id,
        )
        is not None
    ):
        return {
            "kind": "backend_transition",
            "stage": "chapter_development",
            "command": "advance_workflow",
            "command_available": True,
            "operation": "repair_planned_revision_draft_misroute",
            "context": {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "reason": (
                    "The old recovery instruction recorded its authority in "
                    "the workspace instead of producing the required draft. "
                    "The Library can preserve that version, record the proven "
                    "routing repair, and reopen one exact draft contract."
                ),
            },
        }
    if requirement.get("status") == "authorization_required":
        failed_contract_id = requirement["failed_contract_id"]
        failure = next(
            (
                event
                for event in reversed(events)
                if event.get("event_type")
                in {"contract_failed", "contract_reconciled"}
                and event.get("payload", {}).get("contract_id")
                == failed_contract_id
            ),
            None,
        )
        payload = failure.get("payload", {}) if failure else {}
        return _human_chapter_action(
            "chapter_retry",
            {
                "book_id": book_id,
                "chapter_id": chapter_id,
                "failed_contract_id": failed_contract_id,
                "reason": payload.get("message")
                or payload.get("note")
                or "The prior chapter action failed.",
            },
        )
    return {
        "kind": "terminal_hold",
        "stage": "chapter_development",
        "status": "controller_retry_exhausted",
        "reason": "The chapter action exhausted its one authorized retry.",
        "command_available": False,
    }


def _auto_question_retry_transition(
    question_mode: str,
    action: dict[str, Any],
) -> dict[str, Any]:
    """Project a bounded retry as automatic when question deferral owns it."""
    if not (
        question_mode == "auto_defer"
        and action.get("kind") == "human_checkpoint"
        and action.get("decision_kind") == "chapter_retry"
    ):
        return action
    context = dict(action.get("context") or {})
    context["changed_condition"] = AUTO_QUESTION_RETRY_CONSTRAINT
    context["reason"] = (
        "Auto questions already authorizes the one bounded retry under an "
        "explicit no-invention constraint."
    )
    return {
        "kind": "backend_transition",
        "stage": "chapter_development",
        "command": "advance_workflow",
        "command_available": True,
        "operation": "authorize_auto_question_chapter_retry",
        "context": context,
    }


def _chapter_pending_action(
    conversation_id: str,
) -> dict[str, Any] | None:
    """Reconstruct one chapter hold from canonical files without mutation."""
    active = _active_chapter_run(conversation_id)
    if active is None:
        return None
    book_id, chapter_id, run = active
    retry_action = _chapter_retry_action(book_id, chapter_id)
    if retry_action is not None:
        return retry_action

    status = run.get("status")
    reason = run.get("stop_reason")
    base = {
        "book_id": book_id,
        "chapter_id": chapter_id,
        "reason": reason,
    }
    assessment_id = run.get("last_assessment_id")

    if status == "source_approval_required":
        if not isinstance(assessment_id, str):
            raise RuntimeError("Source approval has no bound assessment.")
        session = backend.load_workflow_json(
            backend.chapter_research_session_path(
                book_id,
                chapter_id,
                assessment_id,
            ),
            "Chapter research approval state",
        )
        pending_requests = list(session["pending_requests"])
        if not pending_requests:
            return None
        return _human_chapter_action(
            "chapter_source_permissions",
            {
                **base,
                "assessment_id": assessment_id,
                "research_mode": session["research_mode"],
                "pending_requests": pending_requests,
            },
        )
    if (
        status == "human_input_required"
        and isinstance(assessment_id, str)
        and backend.load_chapter_human_input(
            book_id,
            chapter_id,
            assessment_id,
        )
        is None
    ):
        return _human_chapter_action(
            "chapter_human_input",
            {
                **base,
                "questions": run.get("human_input_requests", []),
            },
        )
    if (
        status == "human_input_closeout_required"
        and backend.load_chapter_human_input_closeout(
            book_id,
            chapter_id,
        )
        is None
    ):
        return _human_chapter_action(
            "chapter_human_input_closeout",
            base,
        )

    human_input_exists = (
        isinstance(assessment_id, str)
        and backend.load_chapter_human_input(
            book_id,
            chapter_id,
            assessment_id,
        )
        is not None
    )
    if (
        status == "human_input_closeout_failed"
        and run.get("last_assessment_outcome") == "human_input_required"
        and human_input_exists
    ) or (
        status == "agent_call_limit_reached"
        and run.get("last_assessment_outcome") == "human_input_required"
        and human_input_exists
    ):
        return _human_chapter_action(
            "chapter_draft_acceptance",
            base,
        )
    recovery = backend.load_chapter_recovery_authorization(
        book_id,
        chapter_id,
    )
    if (
        status == "controller_no_progress"
        and recovery is not None
        and recovery.get("recovery_kind") == "final_pass_revision"
    ):
        # The terminal-looking legacy status is the immutable basis for this
        # narrowly authorized repair. Once that authority exists, the
        # controller can resume under its exact two-call contract.
        return None
    if (
        status == "controller_no_progress"
        and recovery is not None
        and recovery.get("recovery_kind") == "planned_draft_revision"
        and backend.chapter_controller_dry_run(book_id, chapter_id).action
        in backend.chapter_controller.EXECUTABLE_ACTIONS
    ):
        # An auto-authorized retry supersedes the immutable no-progress
        # observation. Project the controller's executable recovery action
        # instead of replaying the stale terminal presentation.
        return None
    if (
        backend.performance_repair_review_required(
            book_id,
            chapter_id,
            run,
        )
        and recovery is None
    ):
        return _human_chapter_action(
            "final_pass_repair",
            {
                **base,
                "reason": (
                    "The bounded performance path used its targeted repair; "
                    "the remaining audited defect can receive exactly one "
                    "terminal correction and one independent verdict."
                ),
            },
        )
    if status == "agent_call_limit_reached" and recovery is None:
        return _human_chapter_action("chapter_recovery", base)
    if (
        status == "chapter_blocked"
        and run.get("last_assessment_outcome") == "chapter_blocked"
        and isinstance(run.get("last_research_packet_id"), str)
        and recovery is None
    ):
        return _human_chapter_action("research_access_recovery", base)
    if (
        status == "final_pass_failed"
        and run.get("last_assessment_outcome") == "revise_draft"
        and recovery is None
    ):
        return _human_chapter_action("final_pass_repair", base)
    if (
        status == "human_input_closeout_failed"
        and run.get("last_assessment_outcome") == "ready_for_final_pass"
        and run.get("final_pass_requested") is True
        and isinstance(run.get("final_closeout_source_draft_id"), str)
        and run.get("final_closeout_developer_call_started_at") is None
        and run.get("final_closeout_evaluator_call_started_at") is None
        and recovery is None
    ):
        return _human_chapter_action("final_pass_repair", base)
    if status in CHAPTER_TERMINAL_STATUSES:
        return {
            "kind": "terminal_hold",
            "stage": "chapter_development",
            "status": status,
            "reason": reason,
            "command_available": False,
        }
    return None


def _workflow_files(conversation_id: str) -> list[Path]:
    """Return the bounded canonical files that define one workflow revision."""
    direct_paths = [
        backend.CONVERSATIONS_PATH / f"{conversation_id}.jsonl",
        backend.CONVERSATIONS_PATH / f"{conversation_id}.receipt.json",
        backend.CONVERSATIONS_PATH / f"{conversation_id}.finish-policy.json",
        backend.REVIEWS_PATH / f"{conversation_id}.json",
        backend.REVIEWS_PATH / f"{conversation_id}.md",
        backend.REVIEW_SESSIONS_PATH / f"{conversation_id}.json",
        backend.REVIEW_DECISIONS_PATH / f"{conversation_id}.json",
    ]
    files = [path for path in direct_paths if path.is_file()]
    book_directory = backend.BOOKS_PATH / f"book-{conversation_id}"
    runtime_observation_path = runtime_event_path(conversation_id)
    if book_directory.is_dir():
        files.extend(
            path
            for path in book_directory.rglob("*")
            if path.is_file() and path != runtime_observation_path
        )

    review_path = backend.REVIEWS_PATH / f"{conversation_id}.json"
    if review_path.is_file():
        try:
            review = backend.load_workflow_json(
                review_path,
                "Canonical research review",
            )
        except RuntimeError:
            review = {}
        review_id = review.get("review_id")
        if isinstance(review_id, str):
            node_directory = backend.KNOWLEDGE_PATH / "nodes" / review_id
            if node_directory.is_dir():
                files.extend(
                    path
                    for path in node_directory.rglob("*")
                    if path.is_file()
                )
    return sorted(set(files), key=lambda path: path.as_posix())


def workflow_revision(conversation_id: str) -> str:
    """Hash one workflow's canonical artifact names and exact bytes."""
    _conversation_path(conversation_id)
    digest = hashlib.sha256()
    for path in _workflow_files(conversation_id):
        try:
            relative = path.resolve().relative_to(backend.PROJECT_PATH.resolve())
            payload = path.read_bytes()
        except (OSError, ValueError) as error:
            raise WorkflowOperationError(
                "workflow_revision_failed",
                "Canonical workflow state could not be hashed.",
            ) from error
        encoded_name = relative.as_posix().encode("utf-8")
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _assert_expected_state(
    conversation_id: str,
    expected_stage: str | None,
    expected_revision: str | None,
) -> dict[str, Any]:
    current = inspect_workflow(conversation_id, include_messages=False)
    if expected_stage is not None and expected_stage != current["stage"]:
        raise WorkflowConflictError(
            "stale_workflow_stage",
            (
                f"Expected stage {expected_stage}, but canonical state is "
                f"{current['stage']}."
            ),
        )
    if (
        expected_revision is not None
        and expected_revision != current["revision"]
    ):
        raise WorkflowConflictError(
            "stale_workflow_revision",
            "Canonical workflow artifacts changed after this action was prepared.",
        )
    return current


def inspect_workflow(
    conversation_id: str,
    *,
    include_messages: bool = True,
) -> dict[str, Any]:
    """Derive one workflow view from canonical artifacts without mutation."""
    path = _conversation_path(conversation_id)
    try:
        events = backend.load_conversation_events(path)
    except RuntimeError as error:
        raise WorkflowOperationError(
            "conversation_unreadable",
            str(error),
        ) from error

    has_finish_event = conversation_store.is_finished(events)
    receipt_valid = backend.conversation_is_finished(path)
    if has_finish_event and not receipt_valid:
        stage = "workflow_error"
    elif not has_finish_event:
        stage = "conversation_active"
    else:
        try:
            stage = backend.workflow_stage(path)
        except RuntimeError:
            stage = "workflow_error"

    lineage_paths = _conversation_lineage_paths(path)
    lineage = backend.continuation_lineage(path)
    reviewed_conversation_ids = [
        lineage_path.stem
        for lineage_path in lineage_paths
        if (
            backend.REVIEWS_PATH / f"{lineage_path.stem}.json"
        ).is_file()
        and (
            backend.REVIEWS_PATH / f"{lineage_path.stem}.md"
        ).is_file()
    ]
    book_creation_active = (
        stage != "complete"
        and (has_finish_event or bool(reviewed_conversation_ids))
    )
    progress_state = canonical_workflow_progress(
        conversation_id,
        stage,
        lineage_paths,
        reviewed_conversation_ids,
    )
    pending_action = _checkpoint_pending_action(
        conversation_id,
        stage,
    )
    question_mode = _effective_question_mode(conversation_id, events)
    pending_action = _auto_question_retry_transition(
        question_mode,
        pending_action,
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation_id,
        "conversation_path": str(path),
        "provider": _selected_backend(events),
        "research_mode": _effective_research_mode(conversation_id, events),
        "question_mode": question_mode,
        "finish_policy": (
            {
                "enabled": True,
                "policy_version": finish_policy["policy_version"],
                "scope": finish_policy["scope"],
                "source_authority": finish_policy["source_authority"],
                "automatic_checkpoint_kinds": finish_policy[
                    "automatic_checkpoint_kinds"
                ],
                "nondelegable_checkpoint_kinds": finish_policy[
                    "nondelegable_checkpoint_kinds"
                ],
                "finish_policy_sha256": finish_policy[
                    "finish_policy_sha256"
                ],
            }
            if (
                finish_policy := load_finish_guardrails_policy(
                    conversation_id
                )
            )
            is not None
            else {"enabled": False}
        ),
        "finished": has_finish_event and receipt_valid,
        "stage": stage,
        "stage_label": backend.WORKFLOW_STAGE_LABELS.get(
            stage,
            "active conversation" if stage == "conversation_active" else "workflow error",
        ),
        "pending_action": pending_action,
        "overall_progress": progress_state["percent"],
        "progress": progress_state,
        "review_state": _review_status(conversation_id, stage),
        "lineage_root_conversation_id": lineage_paths[0].stem,
        "lineage_depth": len(lineage_paths) - 1,
        "book_creation": {
            "active": book_creation_active,
            "status": (
                "building"
                if book_creation_active
                else "ready" if stage == "complete" else "not_started"
            ),
            "label": (
                "Book generation in progress"
                if book_creation_active
                else "Review edition ready"
                if stage == "complete"
                else "Conversation in progress"
            ),
            "reviewed_checkpoint_count": len(reviewed_conversation_ids),
        },
        "experience": workflow_experience_projection(
            stage,
            pending_action,
            book_creation_active=book_creation_active,
        ),
    }
    if lineage is not None:
        result["lineage"] = {
            "kind": lineage["kind"],
            "parent_conversation_id": lineage["parent_conversation_id"],
            "parent_review_id": lineage["parent_review_id"],
        }
    message_segments = [
        backend.load_conversation_messages(lineage_path)
        for lineage_path in lineage_paths
    ]
    local_messages = message_segments[-1]
    messages = [
        message
        for segment in message_segments
        for message in segment
    ]
    result["local_message_count"] = len(local_messages)
    result["message_count"] = len(messages)
    result["opening_question"] = next(
        (
            message.get("content")
            for message in messages
            if message.get("role") == "user"
            and isinstance(message.get("content"), str)
        ),
        None,
    )
    if include_messages:
        result["messages"] = messages
    result["finish_readiness"] = _finish_readiness(
        stage=stage,
        pending_action=result["pending_action"],
        research_mode=result["research_mode"],
        question_mode=result["question_mode"],
        finish_policy=finish_policy,
        message_count=len(messages),
    )
    result["timeline"] = _workflow_timeline(conversation_id, stage)
    result["revision"] = workflow_revision(conversation_id)
    return result


def create_conversation(
    provider: str,
    research_mode: str = backend.DEFAULT_RESEARCH_MODE,
) -> dict[str, Any]:
    """Create one canonical conversation and bind its selected provider."""
    if provider not in backend.BACKENDS.values():
        raise WorkflowServiceError(
            "unsupported_provider",
            f"Unsupported provider: {provider}",
        )
    if research_mode not in backend.RESEARCH_MODES:
        raise WorkflowServiceError(
            "unsupported_research_mode",
            f"Unsupported research mode: {research_mode}",
        )
    if backend.find_backend_program(provider) is None:
        raise WorkflowOperationError(
            "provider_unavailable",
            f"{provider} was not found on PATH; no conversation was created.",
        )
    try:
        path = backend.create_conversation(research_mode=research_mode)
        backend.ensure_backend_selection(path, provider)
    except RuntimeError as error:
        raise WorkflowOperationError(
            "conversation_create_failed",
            str(error),
        ) from error
    return inspect_workflow(path.stem)


def switch_conversation_provider(
    conversation_id: str,
    provider: str,
    *,
    expected_stage: str,
    expected_revision: str,
) -> dict[str, Any]:
    """Switch the active harness while preserving the canonical transcript."""
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    if current["stage"] != "conversation_active":
        raise WorkflowConflictError(
            "provider_switch_not_allowed",
            "The conversation provider can change only before /finish.",
        )
    if provider not in backend.BACKENDS.values():
        raise WorkflowServiceError(
            "unsupported_provider",
            f"Unsupported provider: {provider}",
        )
    if backend.find_backend_program(provider) is None:
        raise WorkflowOperationError(
            "provider_unavailable",
            f"{provider} was not found on PATH.",
        )
    path = _conversation_path(conversation_id)
    try:
        backend.ensure_backend_selection(path, provider)
    except RuntimeError as error:
        raise WorkflowOperationError(
            "provider_switch_failed",
            str(error),
        ) from error
    return inspect_workflow(conversation_id)


def submit_conversation_turn(
    conversation_id: str,
    content: str,
    *,
    expected_stage: str | None = None,
    expected_revision: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run one turn with the provider already bound in canonical history."""
    if not isinstance(content, str) or not content.strip():
        raise WorkflowServiceError(
            "invalid_turn",
            "Conversation turn must contain non-empty text.",
        )
    if len(content) > MAX_TURN_CHARACTERS:
        raise WorkflowServiceError(
            "turn_too_large",
            f"Conversation turn exceeds {MAX_TURN_CHARACTERS} characters.",
        )

    _report_progress(progress, 8, "Checking the durable conversation")
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    path = _conversation_path(conversation_id)
    events = backend.load_conversation_events(path)
    if conversation_store.is_finished(events):
        raise WorkflowConflictError(
            "conversation_finished",
            "A finished conversation cannot accept another turn.",
        )
    provider = _selected_backend(events)
    if provider is None:
        raise WorkflowConflictError(
            "provider_not_selected",
            "Conversation has no valid selected provider.",
        )
    program_path = backend.find_backend_program(provider)
    if program_path is None:
        raise WorkflowOperationError(
            "provider_unavailable",
            f"{provider} was not found on PATH.",
        )

    previous_messages = backend.load_conversation_lineage_messages(path)
    librarian_instructions = backend.load_skill_instructions(
        backend.LIBRARIAN_SKILL_PATH,
        "Alexandrian Librarian",
    )
    user_event = backend.append_conversation_message(
        path,
        "user",
        provider,
        content,
    )
    _report_progress(progress, 24, f"Starting {provider}")
    prompt = backend.build_librarian_prompt(
        librarian_instructions,
        previous_messages,
        content,
        backend.load_research_continuation_context(path),
        {
            key: current.get(key)
            for key in (
                "schema_version",
                "conversation_id",
                "provider",
                "research_mode",
                "stage",
                "stage_label",
                "pending_action",
                "lineage",
                "revision",
            )
            if key in current
        },
    )
    try:
        _, assistant_response = backend.capture_backend_response(
            provider,
            program_path,
            prompt,
        )
    except RuntimeError as error:
        try:
            conversation_store.append_backend_error(
                path,
                turn_id=user_event["turn_id"],
                agent_host=provider,
                category="backend_call_failed",
                message=(
                    "The selected backend did not return a usable response."
                ),
                retryable=True,
            )
        except conversation_store.ConversationStoreError as storage_error:
            raise WorkflowOperationError(
                "backend_failure_unrecorded",
                "Provider failure and its durable error event could not both be saved.",
            ) from storage_error
        raise WorkflowOperationError(
            "backend_call_failed",
            str(error),
        ) from error

    _report_progress(progress, 88, "Provider response received")
    backend.append_conversation_message(
        path,
        "assistant",
        provider,
        assistant_response,
        turn_id=user_event["turn_id"],
    )
    _report_progress(progress, 96, "Saving the canonical turn")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "turn_completed",
        "conversation_id": conversation_id,
        "provider": provider,
        "turn_id": user_event["turn_id"],
        "assistant_response": assistant_response,
        "workflow": inspect_workflow(conversation_id),
    }


def finish_conversation(
    conversation_id: str,
    research_mode: str | None = None,
    *,
    finish_mode: str = "standard",
    expected_stage: str | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Finish one active conversation and expose its derived next hold."""
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    if finish_mode not in FINISH_MODES:
        raise WorkflowServiceError(
            "unsupported_finish_mode",
            f"Unsupported Finish mode: {finish_mode}",
        )
    path = _conversation_path(conversation_id)
    events = backend.load_conversation_events(path)
    if conversation_store.is_finished(events):
        raise WorkflowConflictError(
            "conversation_finished",
            "Conversation is already finished.",
        )
    selected_mode = (
        "auto_approve"
        if finish_mode == "guardrails"
        else research_mode
        or _effective_research_mode(conversation_id, events)
    )
    if selected_mode not in backend.RESEARCH_MODES:
        raise WorkflowServiceError(
            "unsupported_research_mode",
            f"Unsupported research mode: {selected_mode}",
        )
    try:
        if finish_mode == "guardrails":
            record_finish_guardrails_policy(
                conversation_id,
                authorized_from_stage=current["stage"],
                authorized_from_revision=current["revision"],
            )
        snapshotted_mode = backend.snapshot_review_research_mode(
            conversation_id,
            selected_mode,
        )
        backend.finish_conversation(path, snapshotted_mode)
    except RuntimeError as error:
        raise WorkflowOperationError(
            "conversation_finish_failed",
            str(error),
        ) from error
    return inspect_workflow(conversation_id)


def _guardrail_authorization_note(
    policy: dict[str, Any],
    checkpoint_kind: str,
) -> str:
    return (
        "Preauthorized by the inquiry-scoped "
        f"{policy['policy_version']} Finish policy "
        f"{policy['finish_policy_sha256']} for {checkpoint_kind}. "
        "This authority is limited to the exact bounded contract and does "
        "not authorize new authorship claims or broader trust."
    )


def _apply_guardrail_checkpoint(
    current: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any] | None:
    """Apply one policy-listed mechanical checkpoint, never authorship."""
    action = current.get("pending_action", {})
    if action.get("kind") != "human_checkpoint":
        return None
    checkpoint_kind = action.get("decision_kind")
    if checkpoint_kind not in policy["automatic_checkpoint_kinds"]:
        return None
    conversation_id = current["conversation_id"]
    if checkpoint_kind in {
        "research_source_permissions",
        "chapter_source_permissions",
    }:
        return set_workflow_research_mode(
            conversation_id,
            "auto_approve",
            expected_stage=current["stage"],
            expected_revision=current["revision"],
            authorized_by="finish_guardrails_policy",
        )["workflow"]

    context = action.get("context", {})
    note = _guardrail_authorization_note(policy, checkpoint_kind)
    decision = {
        "kind": checkpoint_kind,
        "book_id": context.get("book_id"),
        "chapter_id": context.get("chapter_id"),
        "approval_note": note,
    }
    result = submit_checkpoint_decision(
        conversation_id,
        decision,
        expected_stage=current["stage"],
        expected_revision=current["revision"],
    )
    return result["workflow"]


def _drive_workflow_with_guardrails(
    conversation_id: str,
    current: dict[str, Any],
    policy: dict[str, Any],
    *,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Advance until completion or a genuinely nondelegable durable hold."""
    research_continuation_count = 0
    for transition_count in range(MAX_BACKGROUND_BOOK_TRANSITIONS):
        if current.get("stage") == "complete":
            _report_progress(progress, 98, "Verified book completed")
            return current
        current_conversation_id = current.get("conversation_id")
        if not isinstance(current_conversation_id, str):
            raise WorkflowOperationError(
                "finish_guardrails_invalid_workflow",
                "Finish guardrails received a workflow without an identity.",
            )
        if (
            current_conversation_id != conversation_id
            and current.get("stage") == "conversation_active"
        ):
            if (
                research_continuation_count
                >= MAX_GUARDRAIL_RESEARCH_CONTINUATIONS
            ):
                _report_progress(
                    progress,
                    96,
                    "Paused after the bounded research continuation",
                )
                return current
            _report_progress(
                progress,
                min(94, 20 + transition_count * 2),
                "Resolving the parent review's bounded evidence gaps",
            )
            turn = submit_conversation_turn(
                current_conversation_id,
                GUARDRAIL_RESEARCH_CONTINUATION_PROMPT,
                expected_stage=current["stage"],
                expected_revision=current["revision"],
            )
            current = finish_conversation(
                current_conversation_id,
                turn["workflow"].get("research_mode"),
                finish_mode="standard",
                expected_stage=turn["workflow"]["stage"],
                expected_revision=turn["workflow"]["revision"],
            )
            research_continuation_count += 1
            continue
        action = current.get("pending_action", {})
        previous_state = (
            current.get("stage"),
            current.get("revision"),
            action.get("kind"),
            action.get("decision_kind"),
        )
        if (
            action.get("kind") == "backend_transition"
            and action.get("command_available")
        ):
            step_progress = min(92, 20 + transition_count * 2)
            _report_progress(
                progress,
                step_progress,
                f"Advancing {current.get('stage_label', current.get('stage'))}",
            )

            def advance_progress(
                percent: int,
                phase: str,
                *,
                floor: int = step_progress,
            ) -> None:
                _report_progress(
                    progress,
                    min(95, floor + round(percent * 0.02)),
                    phase,
                )

            result = advance_workflow(
                current_conversation_id,
                expected_stage=current["stage"],
                expected_revision=current["revision"],
                progress=advance_progress,
            )
            current = result["workflow"]
        else:
            deferred = _auto_defer_pending_questions(current)
            if deferred is not None:
                current, _ = deferred
                _report_progress(
                    progress,
                    min(95, 22 + transition_count * 2),
                    "Deferring inquiry questions as explicitly unresolved",
                )
            else:
                guarded = _apply_guardrail_checkpoint(current, policy)
                if guarded is None:
                    _report_progress(
                        progress,
                        96,
                        "Paused at a nondelegable durable decision",
                    )
                    return current
                _report_progress(
                    progress,
                    min(95, 22 + transition_count * 2),
                    "Applying inquiry-scoped Finish guardrails",
                )
                current = guarded
        next_action = current.get("pending_action", {})
        next_state = (
            current.get("stage"),
            current.get("revision"),
            next_action.get("kind"),
            next_action.get("decision_kind"),
        )
        if next_state == previous_state:
            raise WorkflowOperationError(
                "finish_guardrails_no_progress",
                "Finish guardrails produced no durable workflow progress.",
            )
    raise WorkflowOperationError(
        "finish_guardrails_transition_limit",
        "Finish guardrails reached their finite transition limit.",
    )


def advance_workflow_with_guardrails(
    conversation_id: str,
    *,
    expected_stage: str,
    expected_revision: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Advance once normally, or drive while an inquiry policy remains valid."""
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    policy = load_finish_guardrails_policy(conversation_id)
    if policy is None:
        return advance_workflow(
            conversation_id,
            expected_stage=expected_stage,
            expected_revision=expected_revision,
            progress=progress,
        )
    workflow = _drive_workflow_with_guardrails(
        conversation_id,
        current,
        policy,
        progress=progress,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "workflow_advanced_with_guardrails",
        "conversation_id": conversation_id,
        "policy_version": policy["policy_version"],
        "workflow": workflow,
    }


def finish_and_advance_workflow(
    conversation_id: str,
    research_mode: str | None = None,
    *,
    finish_mode: str = "standard",
    expected_stage: str,
    expected_revision: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Seal one conversation and run its authorized automatic transitions."""
    _report_progress(progress, 8, "Sealing the conversation")
    finished = finish_conversation(
        conversation_id,
        research_mode,
        finish_mode=finish_mode,
        expected_stage=expected_stage,
        expected_revision=expected_revision,
    )
    if finish_mode == "guardrails":
        _report_progress(progress, 18, "Finish guardrails started")
        policy = load_finish_guardrails_policy(conversation_id)
        if policy is None:
            raise WorkflowOperationError(
                "finish_policy_missing",
                "Finish guardrails were not durably recorded.",
            )
        return _drive_workflow_with_guardrails(
            conversation_id,
            finished,
            policy,
            progress=progress,
        )
    action = finished.get("pending_action", {})
    if not (
        action.get("kind") == "backend_transition"
        and action.get("command_available")
    ):
        _report_progress(progress, 96, "Conversation sealed at a durable hold")
        return finished

    _report_progress(progress, 18, "Independent review started")

    def advance_progress(percent: int, phase: str) -> None:
        mapped = 18 + round(percent * 0.78)
        _report_progress(progress, mapped, phase)

    result = advance_workflow(
        conversation_id,
        expected_stage=finished["stage"],
        expected_revision=finished["revision"],
        progress=advance_progress,
    )
    return result["workflow"]


def build_book_to_review(
    conversation_id: str,
    content: str,
    *,
    expected_stage: str,
    expected_revision: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run one new inquiry through automatic stages to its next human hold.

    The controller remains authoritative for every transition. This bounded
    orchestrator never supplies a human decision: it stops for source
    permission, a genuine blocker, completion, or the editorial checkpoint.
    """

    def opening_progress(percent: int, phase: str) -> None:
        _report_progress(progress, 4 + round(percent * 0.12), phase)

    _report_progress(progress, 4, "Starting the opening inquiry")
    opening = submit_conversation_turn(
        conversation_id,
        content,
        expected_stage=expected_stage,
        expected_revision=expected_revision,
        progress=opening_progress,
    )
    current = opening["workflow"]

    def finish_progress(percent: int, phase: str) -> None:
        _report_progress(progress, 18 + round(percent * 0.18), phase)

    _report_progress(progress, 18, "Sealing the opening exchange")
    current = finish_and_advance_workflow(
        current["conversation_id"],
        current.get("research_mode"),
        finish_mode="guardrails",
        expected_stage=current["stage"],
        expected_revision=current["revision"],
        progress=finish_progress,
    )

    for transition_count in range(MAX_BACKGROUND_BOOK_TRANSITIONS):
        stage = current.get("stage")
        action = current.get("pending_action", {})
        if stage == "editorial_checkpoint":
            _report_progress(progress, 98, "Book ready for editorial review")
            return current
        if stage == "complete":
            _report_progress(progress, 98, "Verified book completed")
            return current
        if not (
            action.get("kind") == "backend_transition"
            and action.get("command_available")
        ):
            _report_progress(
                progress,
                96,
                "Background build paused at a durable decision",
            )
            return current

        step_floor = min(91, 38 + transition_count * 3)

        def advance_progress(
            percent: int,
            phase: str,
            *,
            floor: int = step_floor,
        ) -> None:
            _report_progress(progress, floor + round(percent * 0.02), phase)

        _report_progress(
            progress,
            step_floor,
            f"Advancing {current.get('stage_label', stage)}",
        )
        result = advance_workflow(
            current["conversation_id"],
            expected_stage=current["stage"],
            expected_revision=current["revision"],
            progress=advance_progress,
        )
        current = result["workflow"]

    raise WorkflowOperationError(
        "background_build_limit",
        "The background book build reached its bounded transition limit.",
    )


def list_resumable_workflows() -> dict[str, Any]:
    """List active and unfinished canonical workflows without changing them."""
    all_workflows = []
    for path in sorted(backend.CONVERSATIONS_PATH.glob("*.jsonl")):
        try:
            workflow = inspect_workflow(
                path.stem,
                include_messages=False,
            )
        except WorkflowServiceError as error:
            workflow = {
                "schema_version": SCHEMA_VERSION,
                "conversation_id": path.stem,
                "conversation_path": str(path),
                "finished": False,
                "stage": "workflow_error",
                "stage_label": "workflow error",
                "pending_action": {
                    "kind": "workflow_error",
                    "command_available": False,
                },
                "error": {"code": error.code, "message": str(error)},
            }
        all_workflows.append(workflow)
    continuation_parent_ids = {
        workflow["lineage"]["parent_conversation_id"]
        for workflow in all_workflows
        if isinstance(workflow.get("lineage"), dict)
        and isinstance(
            workflow["lineage"].get("parent_conversation_id"),
            str,
        )
    }
    workflows = [
        workflow
        for workflow in all_workflows
        if workflow["stage"] != "complete"
        and workflow["conversation_id"] not in continuation_parent_ids
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_count": len(workflows),
        "workflows": workflows,
    }


def _require_decision_fields(
    decision: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = set() if optional is None else optional
    missing = required - set(decision)
    unknown = set(decision) - required - optional
    if missing or unknown:
        parts = []
        if missing:
            parts.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            parts.append("unknown " + ", ".join(sorted(unknown)))
        raise WorkflowServiceError(
            "invalid_decision",
            "Decision fields are invalid: " + "; ".join(parts) + ".",
        )


def _bound_book_id(conversation_id: str, supplied: object) -> str:
    expected = f"book-{conversation_id}"
    if supplied != expected:
        raise WorkflowConflictError(
            "decision_book_mismatch",
            "Decision does not target this conversation's canonical book.",
        )
    return expected


def _record_checkpoint_decision(
    conversation_id: str,
    stage: str,
    decision: dict[str, Any],
) -> object:
    kind = decision.get("kind")
    conversation_path = _conversation_path(conversation_id)
    book_id = f"book-{conversation_id}"
    book_directory = backend.BOOKS_PATH / book_id

    if kind == "research_review":
        _require_decision_fields(
            decision,
            required={"kind", "decision"},
            optional={
                "confirmed_core_question_or_thesis",
                "curiosity_signature_decision",
                "approved_curiosity_signature",
                "accepted_belief_revisions",
                "disputed_points",
                "excluded_material",
            },
        )
        if stage != "review_checkpoint":
            raise WorkflowConflictError(
                "checkpoint_stage_mismatch",
                "Research-review authority is not currently pending.",
            )
        return backend.record_human_review_decision(
            backend.REVIEWS_PATH / f"{conversation_id}.json",
            decision=decision["decision"],
            confirmed_core_question_or_thesis=decision.get(
                "confirmed_core_question_or_thesis"
            ),
            curiosity_signature_decision=decision.get(
                "curiosity_signature_decision"
            ),
            approved_curiosity_signature=decision.get(
                "approved_curiosity_signature"
            ),
            accepted_belief_revisions=decision.get(
                "accepted_belief_revisions"
            ),
            disputed_points=decision.get("disputed_points"),
            excluded_material=decision.get("excluded_material"),
        )

    if kind == "book_plan":
        _require_decision_fields(
            decision,
            required={"kind", "decision"},
            optional={"notes"},
        )
        if stage != "book_plan_checkpoint":
            raise WorkflowConflictError(
                "checkpoint_stage_mismatch",
                "Book-plan authority is not currently pending.",
            )
        return backend.record_book_plan_decision(
            book_directory / "manifest.json",
            decision=decision["decision"],
            notes=decision.get("notes"),
        )

    if kind == "editorial":
        _require_decision_fields(
            decision,
            required={"kind", "decision"},
            optional={"notes", "chapter_revisions"},
        )
        if stage != "editorial_checkpoint":
            raise WorkflowConflictError(
                "checkpoint_stage_mismatch",
                "Final editorial authority is not currently pending.",
            )
        return backend.record_editorial_decision(
            backend.book_synthesis.synthesis_receipt_path(
                backend.PROJECT_PATH,
                f"book-{conversation_id}",
            ),
            book_directory / "manifest.json",
            decision=decision["decision"],
            notes=decision.get("notes"),
            chapter_revisions=decision.get("chapter_revisions"),
        )

    if kind == "research_source_permissions":
        _require_decision_fields(
            decision,
            required={"kind", "decisions"},
        )
        if stage != "research_review":
            raise WorkflowConflictError(
                "checkpoint_stage_mismatch",
                "Research source authority is not currently pending.",
            )
        return backend.apply_pending_source_decisions(
            conversation_id,
            decision["decisions"],
        )

    chapter_kinds = {
        "chapter_source_permissions",
        "chapter_artifact_review",
        "chapter_retry",
        "chapter_human_input",
        "chapter_human_input_closeout",
        "chapter_draft_acceptance",
        "chapter_recovery",
        "research_access_recovery",
        "final_pass_repair",
    }
    if kind in chapter_kinds:
        if stage != "chapter_development":
            raise WorkflowConflictError(
                "checkpoint_stage_mismatch",
                "Chapter authority is not currently pending.",
            )
        _bound_book_id(conversation_id, decision.get("book_id"))
        chapter_id = decision.get("chapter_id")
        if not isinstance(chapter_id, str) or not chapter_id:
            raise WorkflowServiceError(
                "invalid_decision",
                "Chapter decision requires a chapter_id.",
            )
        common = {"kind", "book_id", "chapter_id"}
        if kind == "chapter_source_permissions":
            _require_decision_fields(
                decision,
                required=common
                | {"assessment_id", "research_mode", "decisions"},
            )
            return backend.apply_pending_chapter_source_decisions(
                book_id,
                chapter_id,
                decision["assessment_id"],
                decision["research_mode"],
                decision["decisions"],
            )
        if kind == "chapter_artifact_review":
            _require_decision_fields(
                decision,
                required=common | {"note"},
            )
            return backend.reject_incomplete_chapter_transition(
                book_id,
                chapter_id,
                decision["note"],
            )
        if kind == "chapter_retry":
            _require_decision_fields(
                decision,
                required=common | {"changed_condition"},
            )
            return backend.authorize_chapter_retry(
                book_id,
                chapter_id,
                decision["changed_condition"],
            )
        if kind == "chapter_human_input":
            _require_decision_fields(
                decision,
                required=common | {"responses", "authorization_note"},
            )
            return backend.record_chapter_human_input(
                book_id,
                chapter_id,
                decision["responses"],
                decision["authorization_note"],
            )
        _require_decision_fields(
            decision,
            required=common | {"approval_note"},
        )
        authorizers = {
            "chapter_human_input_closeout": (
                backend.authorize_chapter_human_input_closeout
            ),
            "chapter_draft_acceptance": (
                backend.authorize_chapter_human_draft_acceptance
            ),
            "chapter_recovery": backend.authorize_chapter_recovery,
            "research_access_recovery": (
                backend.authorize_research_access_recovery
            ),
            "final_pass_repair": backend.authorize_final_pass_repair,
        }
        return authorizers[kind](
            book_id,
            chapter_id,
            decision["approval_note"],
        )

    raise WorkflowServiceError(
        "invalid_decision_kind",
        "Decision kind is not supported by the canonical workflow.",
    )


def submit_checkpoint_decision(
    conversation_id: str,
    decision: dict[str, Any],
    *,
    expected_stage: str,
    expected_revision: str,
) -> dict[str, Any]:
    """Apply one validated human decision to the canonical pending stage."""
    if not isinstance(decision, dict):
        raise WorkflowServiceError(
            "invalid_decision",
            "Workflow decision must be a JSON object.",
        )
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    pending_action = current["pending_action"]
    if current["stage"] == "chapter_development":
        if (
            pending_action.get("kind") != "human_checkpoint"
            or pending_action.get("decision_kind") != decision.get("kind")
        ):
            raise WorkflowConflictError(
                "checkpoint_not_pending",
                "This exact chapter decision is not pending in canonical state.",
            )
    try:
        result = _record_checkpoint_decision(
            conversation_id,
            current["stage"],
            decision,
        )
    except WorkflowServiceError:
        raise
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        raise WorkflowConflictError(
            "checkpoint_rejected",
            str(error),
        ) from error
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "checkpoint_recorded",
        "conversation_id": conversation_id,
        "result": str(result) if isinstance(result, Path) else result,
        "workflow": inspect_workflow(conversation_id),
    }


def set_workflow_research_mode(
    conversation_id: str,
    research_mode: str,
    *,
    expected_stage: str,
    expected_revision: str,
    authorized_by: str = "human_workflow_ui",
) -> dict[str, Any]:
    """Change source authority for one unfinished inquiry and audit it."""
    if research_mode not in backend.RESEARCH_MODES:
        raise WorkflowServiceError(
            "unsupported_research_mode",
            f"Unsupported research mode: {research_mode}",
        )
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    if current["stage"] in {
        "conversation_active",
        "complete",
        "workflow_error",
    }:
        raise WorkflowConflictError(
            "research_mode_change_not_allowed",
            "Source automation can change only while a book workflow is active.",
        )

    action = current.get("pending_action", {})
    context = action.get("context", {})
    resolved_count = 0
    try:
        backend.set_review_research_mode(
            conversation_id,
            research_mode,
            authorized_by=authorized_by,
        )

        if action.get("decision_kind") == "chapter_source_permissions":
            book_id = context["book_id"]
            chapter_id = context["chapter_id"]
            assessment_id = context["assessment_id"]
            backend.set_chapter_research_mode(
                book_id,
                chapter_id,
                assessment_id,
                research_mode,
                authorized_by=authorized_by,
            )

        if research_mode == "auto_approve":
            pending_requests = list(context.get("pending_requests", []))
            decisions = [
                {
                    "request_id": request["request_id"],
                    "decision": "allow_once",
                }
                for request in pending_requests
            ]
            if decisions and action.get("decision_kind") == (
                "research_source_permissions"
            ):
                backend.apply_pending_source_decisions(
                    conversation_id,
                    decisions,
                    authorized_by="auto_approve_mode",
                )
                resolved_count = len(decisions)
            elif decisions and action.get("decision_kind") == (
                "chapter_source_permissions"
            ):
                backend.apply_pending_chapter_source_decisions(
                    context["book_id"],
                    context["chapter_id"],
                    context["assessment_id"],
                    research_mode,
                    decisions,
                    authorized_by="auto_approve_mode",
                )
                resolved_count = len(decisions)
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        raise WorkflowConflictError(
            "research_mode_change_rejected",
            str(error),
            str(error),
        ) from error

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "research_mode_updated",
        "conversation_id": conversation_id,
        "research_mode": research_mode,
        "pending_sources_resolved": resolved_count,
        "workflow": inspect_workflow(conversation_id),
    }


def _auto_defer_pending_questions(
    current: dict[str, Any],
) -> tuple[dict[str, Any], int] | None:
    """Record one pending question set as explicitly unresolved."""
    if current.get("question_mode") != "auto_defer":
        return None
    action = current.get("pending_action", {})
    if not (
        action.get("kind") == "human_checkpoint"
        and action.get("decision_kind") == "chapter_human_input"
    ):
        return None
    context = action.get("context", {})
    questions = context.get("questions")
    if (
        not isinstance(questions, list)
        or not questions
        or any(
            not isinstance(question, str) or not question.strip()
            for question in questions
        )
    ):
        raise WorkflowConflictError(
            "question_mode_change_rejected",
            "The pending chapter questions are invalid.",
        )
    conversation_id = current["conversation_id"]
    book_id = _bound_book_id(conversation_id, context.get("book_id"))
    chapter_id = context.get("chapter_id")
    if not isinstance(chapter_id, str) or not chapter_id:
        raise WorkflowConflictError(
            "question_mode_change_rejected",
            "The pending chapter question has no chapter identity.",
        )
    backend.record_chapter_human_input(
        book_id,
        chapter_id,
        [AUTO_DEFER_QUESTION_RESPONSE for _ in questions],
        AUTO_DEFER_AUTHORIZATION_NOTE,
    )
    if backend.can_apply_deferred_chapter_human_input(book_id, chapter_id):
        backend.apply_deferred_chapter_human_input(book_id, chapter_id)
    elif backend.can_accept_auto_deferred_chapter_draft(book_id, chapter_id):
        backend.accept_auto_deferred_chapter_draft(book_id, chapter_id)
    return inspect_workflow(conversation_id), len(questions)


def set_workflow_question_mode(
    conversation_id: str,
    question_mode: str,
    *,
    expected_stage: str,
    expected_revision: str,
    authorized_by: str = "human_workflow_ui",
) -> dict[str, Any]:
    """Change automatic question deferral for one unfinished inquiry."""
    if question_mode not in backend.QUESTION_MODES:
        raise WorkflowServiceError(
            "unsupported_question_mode",
            f"Unsupported question mode: {question_mode}",
        )
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    if current["stage"] in {
        "conversation_active",
        "complete",
        "workflow_error",
    }:
        raise WorkflowConflictError(
            "question_mode_change_not_allowed",
            "Question automation can change only while a book workflow is active.",
        )
    try:
        backend.set_review_question_mode(
            conversation_id,
            question_mode,
            authorized_by=authorized_by,
        )
        updated = inspect_workflow(conversation_id)
        resolved_count = 0
        if question_mode == "auto_defer":
            deferred = _auto_defer_pending_questions(updated)
            if deferred is not None:
                updated, resolved_count = deferred
    except WorkflowServiceError:
        raise
    except (RuntimeError, ValueError, KeyError, TypeError) as error:
        raise WorkflowConflictError(
            "question_mode_change_rejected",
            str(error),
        ) from error
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "question_mode_updated",
        "conversation_id": conversation_id,
        "question_mode": question_mode,
        "pending_questions_resolved": resolved_count,
        "workflow": updated,
    }


def advance_workflow(
    conversation_id: str,
    *,
    expected_stage: str,
    expected_revision: str,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Advance through the authoritative controller until its next hold."""
    _report_progress(progress, 8, "Checking canonical workflow state")
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    action = current["pending_action"]
    if action.get("operation") == "accept_auto_deferred_chapter_draft":
        context = action.get("context", {})
        book_id = _bound_book_id(conversation_id, context.get("book_id"))
        chapter_id = context.get("chapter_id")
        if not isinstance(chapter_id, str) or not chapter_id:
            raise WorkflowOperationError(
                "auto_deferred_acceptance_invalid",
                "Auto-deferred acceptance has no chapter identity.",
            )
        _report_progress(
            progress,
            35,
            "Verifying the assessed draft against Auto questions authority",
        )
        try:
            path = backend.accept_auto_deferred_chapter_draft(
                book_id,
                chapter_id,
            )
        except RuntimeError as error:
            raise WorkflowOperationError(
                "auto_deferred_acceptance_failed",
                str(error),
            ) from error
        workflow = inspect_workflow(conversation_id)
        _report_progress(progress, 92, "Auto-deferred chapter closed durably")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "workflow_advanced",
            "conversation_id": conversation_id,
            "result": {
                "status": "auto_deferred_draft_accepted",
                "acceptance_path": str(path),
            },
            "workflow": workflow,
        }
    if action.get("operation") == "repair_stale_graph_retry_exhaustion":
        context = action.get("context", {})
        book_id = _bound_book_id(conversation_id, context.get("book_id"))
        chapter_id = context.get("chapter_id")
        if not isinstance(chapter_id, str) or not chapter_id:
            raise WorkflowOperationError(
                "stale_graph_repair_invalid",
                "Stale-graph repair has no chapter identity.",
            )
        _report_progress(
            progress,
            15,
            "Recording the proven controller invariant repair",
        )
        try:
            backend.authorize_stale_graph_retry_repair(
                book_id,
                chapter_id,
            )
        except RuntimeError as error:
            raise WorkflowOperationError(
                "stale_graph_repair_failed",
                str(error),
            ) from error
        current = inspect_workflow(conversation_id)
        action = current["pending_action"]
        if not (
            action.get("kind") == "backend_transition"
            and action.get("command_available")
        ):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "workflow_advanced",
                "conversation_id": conversation_id,
                "result": {"status": "stale_graph_repair_recorded"},
                "workflow": current,
            }
    if action.get("operation") == "authorize_auto_question_chapter_retry":
        context = action.get("context", {})
        book_id = _bound_book_id(conversation_id, context.get("book_id"))
        chapter_id = context.get("chapter_id")
        changed_condition = context.get("changed_condition")
        if (
            not isinstance(chapter_id, str)
            or not chapter_id
            or not isinstance(changed_condition, str)
            or not changed_condition
        ):
            raise WorkflowOperationError(
                "auto_question_retry_invalid",
                "Automatic question retry has no exact chapter constraint.",
            )
        _report_progress(
            progress,
            18,
            "Applying the inquiry's bounded auto-question retry policy",
        )
        try:
            backend.authorize_chapter_retry(
                book_id,
                chapter_id,
                changed_condition,
            )
        except (RuntimeError, ValueError) as error:
            raise WorkflowOperationError(
                "auto_question_retry_failed",
                str(error),
            ) from error
        current = inspect_workflow(conversation_id)
        action = current["pending_action"]
        if not (
            action.get("kind") == "backend_transition"
            and action.get("command_available")
        ):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "workflow_advanced",
                "conversation_id": conversation_id,
                "result": {"status": "retry_authorized"},
                "workflow": current,
            }
    if action.get("operation") == "repair_planned_revision_draft_misroute":
        context = action.get("context", {})
        book_id = _bound_book_id(conversation_id, context.get("book_id"))
        chapter_id = context.get("chapter_id")
        if not isinstance(chapter_id, str) or not chapter_id:
            raise WorkflowOperationError(
                "planned_revision_repair_invalid",
                "Planned-revision repair has no chapter identity.",
            )
        _report_progress(
            progress,
            18,
            "Recording the proven planned-revision routing repair",
        )
        try:
            backend.authorize_planned_revision_draft_misroute_repair(
                book_id,
                chapter_id,
            )
        except RuntimeError as error:
            raise WorkflowOperationError(
                "planned_revision_repair_failed",
                str(error),
            ) from error
        current = inspect_workflow(conversation_id)
        action = current["pending_action"]
        if not (
            action.get("kind") == "backend_transition"
            and action.get("command_available")
        ):
            return {
                "schema_version": SCHEMA_VERSION,
                "status": "workflow_advanced",
                "conversation_id": conversation_id,
                "result": {"status": "planned_revision_repair_recorded"},
                "workflow": current,
            }
    if action.get("operation") == "apply_deferred_chapter_human_input":
        context = action.get("context", {})
        book_id = _bound_book_id(conversation_id, context.get("book_id"))
        chapter_id = context.get("chapter_id")
        if not isinstance(chapter_id, str) or not chapter_id:
            raise WorkflowOperationError(
                "deferred_input_recovery_invalid",
                "Deferred-input recovery has no chapter identity.",
            )
        _report_progress(
            progress,
            35,
            "Recording the explicitly unresolved chapter boundary",
        )
        try:
            result = backend.apply_deferred_chapter_human_input(
                book_id,
                chapter_id,
            )
        except RuntimeError as error:
            raise WorkflowOperationError(
                "deferred_input_recovery_failed",
                str(error),
            ) from error
        workflow = inspect_workflow(conversation_id)
        _report_progress(progress, 92, "Deferred boundary recorded durably")
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "workflow_advanced",
            "conversation_id": conversation_id,
            "result": result,
            "workflow": workflow,
        }
    if current["pending_action"].get("kind") in {
        "human_checkpoint",
        "terminal_hold",
    } or current["stage"] in (
        {"conversation_active", "complete", "workflow_error"}
        | HUMAN_CHECKPOINT_STAGES
        | TERMINAL_HOLD_STAGES
    ):
        raise WorkflowConflictError(
            "advance_not_allowed",
            "Canonical state requires another command or is terminal.",
        )
    path = _conversation_path(conversation_id)
    events = backend.load_conversation_events(path)
    provider = _selected_backend(events)
    if provider is None:
        raise WorkflowConflictError(
            "provider_not_selected",
            "Workflow has no valid selected provider.",
        )
    program_path = backend.find_backend_program(provider)
    if program_path is None:
        raise WorkflowOperationError(
            "provider_unavailable",
            f"{provider} was not found on PATH.",
        )
    output = io.StringIO()
    _report_progress(progress, 22, f"Starting the {provider} workflow controller")
    try:
        with redirect_stdout(output):
            result = backend.continue_book_workflow(
                provider,
                str(program_path),
                path,
                interactive_checkpoints=False,
            )
    except RuntimeError as error:
        raise WorkflowOperationError(
            "workflow_advance_failed",
            str(error),
        ) from error
    workflow = inspect_workflow(conversation_id)
    if (
        isinstance(result, dict)
        and result.get("status") == "research_continuation"
    ):
        try:
            continuation_path = backend.validate_research_continuation_path(
                result.get("conversation_path")
            )
            backend.ensure_backend_selection(continuation_path, provider)
        except RuntimeError as error:
            raise WorkflowOperationError(
                "workflow_continuation_failed",
                str(error),
            ) from error
        workflow = inspect_workflow(continuation_path.stem)
        _report_progress(progress, 92, "Required conversation context opened")
    else:
        _report_progress(progress, 92, "Controller reached a durable hold")
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "workflow_advanced",
        "conversation_id": conversation_id,
        "result": result,
        "workflow": workflow,
    }


def authorize_streamlined_synthesis_recovery(
    conversation_id: str,
    reason: str,
    *,
    expected_stage: str,
    expected_revision: str,
    authorized_by: str = "human_editor",
) -> dict[str, Any]:
    """Replace a provider-stage synthesis hold with the approved two-call path."""
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    if current["stage"] != "synthesis_held":
        raise WorkflowConflictError(
            "streamlined_recovery_not_allowed",
            "Streamlined recovery requires a canonical synthesis hold.",
        )
    if not isinstance(reason, str) or not reason.strip():
        raise WorkflowServiceError(
            "recovery_reason_required",
            "Streamlined recovery requires the concrete human decision basis.",
        )
    try:
        result = backend.prepare_streamlined_synthesis_foundation(
            f"book-{conversation_id}",
            reason=reason,
            authorized_by=authorized_by,
        )
    except RuntimeError as error:
        raise WorkflowOperationError(
            "streamlined_recovery_failed",
            str(error),
        ) from error
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "streamlined_synthesis_authorized",
        "conversation_id": conversation_id,
        "result": result,
        "workflow": inspect_workflow(conversation_id),
    }
