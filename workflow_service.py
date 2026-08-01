"""UI-neutral commands for the Alexandrian Library's durable workflow.

This module is an interface boundary, not another workflow controller.  It
delegates every canonical read and write to ``backend_selector`` and returns
structured values instead of presenting terminal prompts.
"""

from __future__ import annotations

import hashlib
import io
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import backend_selector as backend
import conversation_store


SCHEMA_VERSION = "1.0"
MAX_TURN_CHARACTERS = 100_000


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
    return action


def _workflow_files(conversation_id: str) -> list[Path]:
    """Return the bounded canonical files that define one workflow revision."""
    direct_paths = [
        backend.CONVERSATIONS_PATH / f"{conversation_id}.jsonl",
        backend.CONVERSATIONS_PATH / f"{conversation_id}.receipt.json",
        backend.REVIEWS_PATH / f"{conversation_id}.json",
        backend.REVIEWS_PATH / f"{conversation_id}.md",
        backend.REVIEW_SESSIONS_PATH / f"{conversation_id}.json",
        backend.REVIEW_DECISIONS_PATH / f"{conversation_id}.json",
    ]
    files = [path for path in direct_paths if path.is_file()]
    book_directory = backend.BOOKS_PATH / f"book-{conversation_id}"
    if book_directory.is_dir():
        files.extend(
            path
            for path in book_directory.rglob("*")
            if path.is_file()
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

    result = {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation_id,
        "conversation_path": str(path),
        "provider": _selected_backend(events),
        "research_mode": _research_mode(events),
        "finished": has_finish_event and receipt_valid,
        "stage": stage,
        "stage_label": backend.WORKFLOW_STAGE_LABELS.get(
            stage,
            "active conversation" if stage == "conversation_active" else "workflow error",
        ),
        "pending_action": _checkpoint_pending_action(
            conversation_id,
            stage,
        ),
    }
    messages = backend.load_conversation_messages(path)
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
    try:
        path = backend.create_conversation(research_mode=research_mode)
        backend.ensure_backend_selection(path, provider)
    except RuntimeError as error:
        raise WorkflowOperationError(
            "conversation_create_failed",
            str(error),
        ) from error
    return inspect_workflow(path.stem)


def submit_conversation_turn(
    conversation_id: str,
    content: str,
    *,
    expected_stage: str | None = None,
    expected_revision: str | None = None,
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

    _assert_expected_state(
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

    previous_messages = backend.load_conversation_messages(path)
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
    prompt = backend.build_librarian_prompt(
        librarian_instructions,
        previous_messages,
        content,
        backend.load_research_continuation_context(path),
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

    backend.append_conversation_message(
        path,
        "assistant",
        provider,
        assistant_response,
        turn_id=user_event["turn_id"],
    )
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
    expected_stage: str | None = None,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    """Finish one active conversation and expose its derived next hold."""
    _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    path = _conversation_path(conversation_id)
    events = backend.load_conversation_events(path)
    if conversation_store.is_finished(events):
        raise WorkflowConflictError(
            "conversation_finished",
            "Conversation is already finished.",
        )
    selected_mode = research_mode or _research_mode(events)
    if selected_mode not in backend.RESEARCH_MODES:
        raise WorkflowServiceError(
            "unsupported_research_mode",
            f"Unsupported research mode: {selected_mode}",
        )
    try:
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


def list_resumable_workflows() -> dict[str, Any]:
    """List active and unfinished canonical workflows without changing them."""
    workflows = []
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
        if workflow["stage"] != "complete":
            workflows.append(workflow)
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


def advance_workflow(
    conversation_id: str,
    *,
    expected_stage: str,
    expected_revision: str,
) -> dict[str, Any]:
    """Advance through the authoritative controller until its next hold."""
    current = _assert_expected_state(
        conversation_id,
        expected_stage,
        expected_revision,
    )
    if current["stage"] in (
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
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "workflow_advanced",
        "conversation_id": conversation_id,
        "result": result,
        "workflow": inspect_workflow(conversation_id),
    }
