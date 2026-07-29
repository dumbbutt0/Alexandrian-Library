"""Provider-neutral, append-only conversation storage.

This module owns the V1 conversation event contract. It can read legacy
Alexandrian Library transcripts without modifying them, but it only writes V1
records.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any


SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_MAJOR = 1
APPLICATION_NAME = "alexandrian-library"
UNKNOWN_LEGACY_TIME = "1970-01-01T00:00:00Z"
EVENT_TYPES = {
    "conversation_started",
    "backend_selected",
    "message",
    "external_activity",
    "backend_error",
    "conversation_finished",
}
TURN_EVENT_TYPES = {"message", "external_activity", "backend_error"}
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class ConversationStoreError(RuntimeError):
    """Base error for durable conversation storage."""


class ConversationFormatError(ConversationStoreError):
    """Raised when a transcript does not satisfy its declared format."""


class ConversationFinishedError(ConversationStoreError):
    """Raised when a caller tries to extend a finished transcript."""


class ConversationIntegrityError(ConversationStoreError):
    """Raised when a final receipt no longer matches its transcript."""


def utc_now() -> str:
    """Return one RFC 3339 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def new_turn_id() -> str:
    """Return a portable turn identifier."""
    return str(uuid.uuid4())


def new_conversation_id() -> str:
    """Return an identifier compatible with the Library's existing filenames."""
    return (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )


def receipt_path_for(conversation_path: Path) -> Path:
    """Return the final receipt path paired with one transcript."""
    return conversation_path.with_suffix(".receipt.json")


def create_conversation(
    conversations_path: Path,
    research_mode: str | None,
    *,
    conversation_id: str | None = None,
    continuation: dict | None = None,
    instruction_snapshots: list[dict] | None = None,
) -> Path:
    """Create a V1 transcript containing its single start event."""
    identifier = conversation_id or new_conversation_id()
    _validate_conversation_id(identifier)
    conversations_path.mkdir(parents=True, exist_ok=True)
    conversation_path = conversations_path / f"{identifier}.jsonl"

    if conversation_path.exists() or receipt_path_for(conversation_path).exists():
        raise ConversationStoreError(
            f"Conversation already exists: {identifier}"
        )
    if continuation is not None and not isinstance(continuation, dict):
        raise ConversationFormatError("continuation must be an object or null.")

    event = _make_event(
        conversation_id=identifier,
        sequence=1,
        event_type="conversation_started",
        turn_id=None,
        payload={
            "application": APPLICATION_NAME,
            "research_mode": research_mode,
            "continuation": continuation,
            "instruction_snapshots": instruction_snapshots or [],
        },
    )
    _validate_event(event)
    _write_event_line(conversation_path, event)
    return conversation_path


def append_backend_selected(
    conversation_path: Path,
    *,
    agent_host: str,
    agent_host_version: str | None = None,
    model_provider: str | None = None,
    model_name: str | None = None,
    model_version: str | None = None,
    extensions: dict | None = None,
) -> dict:
    """Record a harness selection separately from the underlying model."""
    return _append_event(
        conversation_path,
        event_type="backend_selected",
        turn_id=None,
        payload={
            "agent_host": {
                "name": agent_host,
                "version": agent_host_version,
            },
            "model": {
                "provider": model_provider,
                "name": model_name,
                "version": model_version,
            },
        },
        extensions=extensions,
    )


def append_message(
    conversation_path: Path,
    *,
    role: str,
    content: str | list[dict],
    turn_id: str | None = None,
    agent_host: str | None = None,
    model_provider: str | None = None,
    model: str | None = None,
    context: dict | None = None,
    extensions: dict | None = None,
) -> dict:
    """Append one user or assistant message and return its canonical event."""
    content_blocks = (
        [{"type": "text", "text": content}]
        if isinstance(content, str)
        else content
    )
    payload: dict[str, Any] = {
        "role": role,
        "content": content_blocks,
    }
    if agent_host is not None:
        payload["agent_host"] = agent_host
    if model_provider is not None:
        payload["model_provider"] = model_provider
    if model is not None:
        payload["model"] = model
    if context is not None:
        payload["context"] = context

    return _append_event(
        conversation_path,
        event_type="message",
        turn_id=turn_id or new_turn_id(),
        payload=payload,
        extensions=extensions,
    )


def append_external_activity(
    conversation_path: Path,
    *,
    turn_id: str,
    activity_type: str,
    status: str,
    purpose: str,
    result_summary: str,
    target: str | None = None,
    artifact_refs: list[dict] | None = None,
    extensions: dict | None = None,
) -> dict:
    """Preserve material research activity without logging every tool call."""
    return _append_event(
        conversation_path,
        event_type="external_activity",
        turn_id=turn_id,
        payload={
            "activity_type": activity_type,
            "status": status,
            "purpose": purpose,
            "target": target,
            "result_summary": result_summary,
            "artifact_refs": artifact_refs or [],
        },
        extensions=extensions,
    )


def append_backend_error(
    conversation_path: Path,
    *,
    turn_id: str,
    agent_host: str,
    category: str,
    message: str,
    retryable: bool,
    raw_details_ref: dict | None = None,
    extensions: dict | None = None,
) -> dict:
    """Append a bounded error summary rather than raw provider stderr."""
    safe_message = " ".join(message.split())[:1000]
    if not safe_message:
        raise ConversationFormatError("Backend error message cannot be empty.")

    return _append_event(
        conversation_path,
        event_type="backend_error",
        turn_id=turn_id,
        payload={
            "agent_host": agent_host,
            "category": category,
            "message": safe_message,
            "retryable": retryable,
            "raw_details_ref": raw_details_ref,
        },
        extensions=extensions,
    )


def finish_conversation(
    conversation_path: Path,
    *,
    project_root: Path,
    research_mode: str,
    reason: str = "user_finish_command",
) -> dict:
    """Finish once, then atomically write or recover the final receipt."""
    events = _load_v1_events_for_write(conversation_path)
    receipt_path = receipt_path_for(conversation_path)
    finished_events = [
        event
        for event in events
        if event["type"] == "conversation_finished"
    ]

    if finished_events:
        if len(finished_events) != 1 or events[-1] is not finished_events[0]:
            raise ConversationFormatError(
                "Conversation has an invalid finish boundary."
            )
        if receipt_path.exists():
            return verify_receipt(conversation_path, project_root=project_root)
        return _write_final_receipt(
            conversation_path,
            project_root=project_root,
            events=events,
        )

    if receipt_path.exists():
        raise ConversationIntegrityError(
            "A receipt exists for an unfinished conversation."
        )

    message_count = sum(event["type"] == "message" for event in events)
    _append_event(
        conversation_path,
        event_type="conversation_finished",
        turn_id=None,
        payload={
            "status": "ready_for_review",
            "reason": reason,
            "research_mode": research_mode,
            "message_count": message_count,
        },
    )
    final_events = _load_v1_events_for_write(conversation_path)
    return _write_final_receipt(
        conversation_path,
        project_root=project_root,
        events=final_events,
    )


def verify_receipt(
    conversation_path: Path,
    *,
    project_root: Path,
) -> dict:
    """Verify final identity, event boundaries, and exact transcript bytes."""
    receipt_path = receipt_path_for(conversation_path)
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConversationIntegrityError(
            f"Conversation receipt does not exist: {receipt_path.name}"
        ) from error
    except (json.JSONDecodeError, OSError) as error:
        raise ConversationIntegrityError(
            f"Conversation receipt could not be read: {receipt_path.name}"
        ) from error

    _validate_receipt(receipt)
    expected_path = _project_relative_path(conversation_path, project_root)
    actual_hash = _file_sha256(conversation_path)

    if receipt["conversation_id"] != conversation_path.stem:
        raise ConversationIntegrityError("Receipt conversation identity differs.")
    if receipt["transcript_path"] != expected_path:
        raise ConversationIntegrityError("Receipt transcript path differs.")
    if receipt["transcript_sha256"] != actual_hash:
        raise ConversationIntegrityError(
            "Finished transcript no longer matches its receipt."
        )

    events = _load_v1_events_for_write(conversation_path)
    if not events or events[-1]["type"] != "conversation_finished":
        raise ConversationIntegrityError(
            "Receipt transcript has no terminal finish event."
        )
    if (
        receipt["event_count"] != len(events)
        or receipt["final_sequence"] != events[-1]["sequence"]
        or receipt["finished_at"] != events[-1]["created_at"]
    ):
        raise ConversationIntegrityError(
            "Receipt event boundary differs from the transcript."
        )
    return receipt


def load_events(conversation_path: Path) -> list[dict]:
    """Load V1 events or normalize an unversioned legacy transcript in memory."""
    raw_events = _read_json_lines(conversation_path)
    transcript_kind = _detect_transcript_format(raw_events)
    if transcript_kind == "v1":
        _validate_event_stream(
            raw_events,
            expected_conversation_id=conversation_path.stem,
        )
        return raw_events

    normalized = _normalize_legacy_events(
        raw_events,
        conversation_id=conversation_path.stem,
    )
    _validate_event_stream(
        normalized,
        expected_conversation_id=conversation_path.stem,
    )
    return normalized


def transcript_format(conversation_path: Path) -> str:
    """Return ``v1`` or ``legacy`` after rejecting a mixed transcript."""
    return _detect_transcript_format(_read_json_lines(conversation_path))


def visible_messages(conversation_path: Path) -> list[dict]:
    """Return provider-neutral text messages for prompts and review numbering."""
    return visible_messages_from_events(load_events(conversation_path))


def visible_messages_from_events(events: list[dict]) -> list[dict]:
    """Project canonical events into the text-message view used by consumers."""
    messages = []
    for event in events:
        _validate_event(event)
        if event["type"] != "message":
            continue
        payload = event["payload"]
        text = "\n".join(
            block["text"]
            for block in payload["content"]
            if block.get("type") == "text"
        )
        messages.append(
            {
                "role": payload["role"],
                "agent_host": payload.get("agent_host"),
                "content": text,
                "turn_id": event["turn_id"],
            }
        )
    return messages


def is_finished(events: list[dict]) -> bool:
    """Return whether canonical events end at the review-ready boundary."""
    if not events:
        return False
    for event in events:
        _validate_event(event)
    return (
        events[-1]["type"] == "conversation_finished"
        and events[-1]["payload"]["status"] == "ready_for_review"
    )


def group_turns(events: list[dict]) -> list[dict]:
    """Group canonical turn events while preserving first-seen turn order."""
    grouped: dict[str, list[dict]] = {}
    for event in events:
        _validate_event(event)
        turn_id = event["turn_id"]
        if turn_id is None:
            continue
        grouped.setdefault(turn_id, []).append(event)
    return [
        {"turn_id": turn_id, "events": turn_events}
        for turn_id, turn_events in grouped.items()
    ]


def _append_event(
    conversation_path: Path,
    *,
    event_type: str,
    turn_id: str | None,
    payload: dict,
    extensions: dict | None = None,
) -> dict:
    events = _load_v1_events_for_write(conversation_path)
    if events[-1]["type"] == "conversation_finished":
        raise ConversationFinishedError(
            "No events can be appended after conversation_finished."
        )
    if receipt_path_for(conversation_path).exists():
        raise ConversationIntegrityError(
            "An unfinished transcript unexpectedly has a final receipt."
        )

    event = _make_event(
        conversation_id=conversation_path.stem,
        sequence=events[-1]["sequence"] + 1,
        event_type=event_type,
        turn_id=turn_id,
        payload=payload,
        extensions=extensions,
    )
    _validate_event(event)
    _write_event_line(conversation_path, event)
    return event


def _make_event(
    *,
    conversation_id: str,
    sequence: int,
    event_type: str,
    turn_id: str | None,
    payload: dict,
    extensions: dict | None = None,
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    event = {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation_id,
        "event_id": event_id or str(uuid.uuid4()),
        "sequence": sequence,
        "type": event_type,
        "created_at": created_at or utc_now(),
        "turn_id": turn_id,
        "payload": payload,
    }
    if extensions:
        event["extensions"] = extensions
    return event


def _write_event_line(conversation_path: Path, event: dict) -> None:
    conversation_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        event,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        with conversation_path.open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as transcript:
            transcript.write(serialized + "\n")
            transcript.flush()
            os.fsync(transcript.fileno())
    except OSError as error:
        raise ConversationStoreError(
            f"Conversation event could not be written: {conversation_path}"
        ) from error


def _read_json_lines(conversation_path: Path) -> list[dict]:
    try:
        raw_text = conversation_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ConversationStoreError(
            f"Conversation could not be read: {conversation_path}"
        ) from error

    if not raw_text:
        raise ConversationFormatError("Conversation transcript is empty.")
    events = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        if not line.strip():
            raise ConversationFormatError(
                f"Conversation contains a blank event on line {line_number}."
            )
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ConversationFormatError(
                f"Conversation contains invalid JSON on line {line_number}."
            ) from error
        if not isinstance(event, dict):
            raise ConversationFormatError(
                f"Conversation event on line {line_number} is not an object."
            )
        events.append(event)
    return events


def _detect_transcript_format(events: list[dict]) -> str:
    versioned = ["schema_version" in event for event in events]
    if any(versioned) and not all(versioned):
        raise ConversationFormatError(
            "Conversation mixes legacy and versioned events."
        )
    return "v1" if all(versioned) else "legacy"


def _load_v1_events_for_write(conversation_path: Path) -> list[dict]:
    raw_events = _read_json_lines(conversation_path)
    if _detect_transcript_format(raw_events) != "v1":
        raise ConversationFormatError(
            "Legacy conversations are read-only; start a V1 continuation."
        )
    _validate_event_stream(
        raw_events,
        expected_conversation_id=conversation_path.stem,
    )
    return raw_events


def _validate_event_stream(
    events: list[dict],
    *,
    expected_conversation_id: str,
) -> None:
    if not events:
        raise ConversationFormatError("Conversation transcript is empty.")
    event_ids = set()
    finished = False

    for expected_sequence, event in enumerate(events, start=1):
        _validate_event(event)
        if event["conversation_id"] != expected_conversation_id:
            raise ConversationFormatError(
                "Conversation ID does not match its transcript filename."
            )
        if event["sequence"] != expected_sequence:
            raise ConversationFormatError(
                "Conversation sequences must be contiguous and begin at 1."
            )
        if event["event_id"] in event_ids:
            raise ConversationFormatError("Conversation event IDs must be unique.")
        if finished:
            raise ConversationFormatError(
                "Conversation contains an event after conversation_finished."
            )
        if event["type"] == "conversation_finished":
            finished = True
        event_ids.add(event["event_id"])

    if events[0]["type"] != "conversation_started":
        raise ConversationFormatError(
            "The first event must be conversation_started."
        )
    if any(
        event["type"] == "conversation_started" for event in events[1:]
    ):
        raise ConversationFormatError(
            "Conversation may contain only one start event."
        )


def _validate_event(event: dict) -> None:
    required = {
        "schema_version",
        "conversation_id",
        "event_id",
        "sequence",
        "type",
        "created_at",
        "turn_id",
        "payload",
    }
    _require_fields(event, required, "Conversation event")
    _validate_schema_version(event["schema_version"])
    _validate_conversation_id(event["conversation_id"])
    _validate_uuid(event["event_id"], "event_id")
    if not isinstance(event["sequence"], int) or isinstance(
        event["sequence"],
        bool,
    ) or event["sequence"] < 1:
        raise ConversationFormatError("sequence must be a positive integer.")
    if event["type"] not in EVENT_TYPES:
        raise ConversationFormatError(
            f"Unsupported conversation event type: {event['type']}"
        )
    _validate_timestamp(event["created_at"], "created_at")
    if event["type"] in TURN_EVENT_TYPES:
        _validate_uuid(event["turn_id"], "turn_id")
    elif event["turn_id"] is not None:
        raise ConversationFormatError(
            f"{event['type']} must have a null turn_id."
        )
    if not isinstance(event["payload"], dict):
        raise ConversationFormatError("payload must be an object.")
    if "extensions" in event and not isinstance(event["extensions"], dict):
        raise ConversationFormatError("extensions must be an object.")

    validators = {
        "conversation_started": _validate_started_payload,
        "backend_selected": _validate_backend_payload,
        "message": _validate_message_payload,
        "external_activity": _validate_activity_payload,
        "backend_error": _validate_error_payload,
        "conversation_finished": _validate_finished_payload,
    }
    validators[event["type"]](event["payload"])


def _validate_started_payload(payload: dict) -> None:
    _require_fields(
        payload,
        {
            "application",
            "research_mode",
            "continuation",
            "instruction_snapshots",
        },
        "conversation_started payload",
    )
    _require_nonempty_string(payload["application"], "application")
    _require_nullable_string(payload["research_mode"], "research_mode")
    if payload["continuation"] is not None and not isinstance(
        payload["continuation"],
        dict,
    ):
        raise ConversationFormatError("continuation must be an object or null.")
    if not isinstance(payload["instruction_snapshots"], list):
        raise ConversationFormatError("instruction_snapshots must be a list.")
    for snapshot in payload["instruction_snapshots"]:
        if not isinstance(snapshot, dict):
            raise ConversationFormatError(
                "Each instruction snapshot must be an object."
            )
        _require_fields(
            snapshot,
            {"name", "path", "sha256"},
            "instruction snapshot",
        )
        _require_nonempty_string(snapshot["name"], "snapshot name")
        _validate_portable_path(snapshot["path"])
        _validate_sha256(snapshot["sha256"])


def _validate_backend_payload(payload: dict) -> None:
    _require_fields(payload, {"agent_host", "model"}, "backend payload")
    for field, required_fields in (
        ("agent_host", {"name", "version"}),
        ("model", {"provider", "name", "version"}),
    ):
        identity = payload[field]
        if not isinstance(identity, dict):
            raise ConversationFormatError(f"{field} must be an object.")
        _require_fields(identity, required_fields, field)
        for identity_field in required_fields:
            _require_nullable_string(
                identity[identity_field],
                f"{field}.{identity_field}",
            )
    if payload["agent_host"]["name"] is None:
        raise ConversationFormatError("agent_host.name cannot be null.")


def _validate_message_payload(payload: dict) -> None:
    _require_fields(payload, {"role", "content"}, "message payload")
    if payload["role"] not in {"user", "assistant"}:
        raise ConversationFormatError("Message role must be user or assistant.")
    for optional_identity in ("agent_host", "model_provider", "model"):
        if optional_identity in payload:
            _require_nullable_string(
                payload[optional_identity],
                optional_identity,
            )
    content = payload["content"]
    if not isinstance(content, list) or not content:
        raise ConversationFormatError("Message content must be a nonempty list.")
    for block in content:
        _validate_content_block(block)
    if "context" in payload:
        context = payload["context"]
        if not isinstance(context, dict):
            raise ConversationFormatError("Message context must be an object.")
        _require_fields(
            context,
            {"through_sequence", "context_sha256"},
            "message context",
        )
        if not isinstance(context["through_sequence"], int) or isinstance(
            context["through_sequence"],
            bool,
        ) or context["through_sequence"] < 0:
            raise ConversationFormatError(
                "through_sequence must be a nonnegative integer."
            )
        _validate_sha256(context["context_sha256"])


def _validate_content_block(block: Any) -> None:
    if not isinstance(block, dict):
        raise ConversationFormatError("Each content block must be an object.")
    if block.get("type") == "text":
        if not isinstance(block.get("text"), str):
            raise ConversationFormatError("Text content requires a text string.")
        return
    if block.get("type") == "artifact_ref":
        _require_fields(
            block,
            {"path", "media_type", "sha256"},
            "artifact content block",
        )
        _validate_portable_path(block["path"])
        _require_nonempty_string(block["media_type"], "media_type")
        _validate_sha256(block["sha256"])
        return
    raise ConversationFormatError("Unsupported message content block type.")


def _validate_activity_payload(payload: dict) -> None:
    _require_fields(
        payload,
        {
            "activity_type",
            "status",
            "purpose",
            "target",
            "result_summary",
            "artifact_refs",
        },
        "external_activity payload",
    )
    _require_nonempty_string(payload["activity_type"], "activity_type")
    _require_nonempty_string(payload["status"], "status")
    if not isinstance(payload["purpose"], str):
        raise ConversationFormatError("purpose must be a string.")
    _require_nullable_string(payload["target"], "target")
    if not isinstance(payload["result_summary"], str):
        raise ConversationFormatError("result_summary must be a string.")
    if not isinstance(payload["artifact_refs"], list):
        raise ConversationFormatError("artifact_refs must be a list.")
    for artifact_ref in payload["artifact_refs"]:
        _validate_artifact_ref(artifact_ref)


def _validate_error_payload(payload: dict) -> None:
    _require_fields(
        payload,
        {
            "agent_host",
            "category",
            "message",
            "retryable",
            "raw_details_ref",
        },
        "backend_error payload",
    )
    for field in ("agent_host", "category", "message"):
        _require_nonempty_string(payload[field], field)
    if not isinstance(payload["retryable"], bool):
        raise ConversationFormatError("retryable must be a boolean.")
    if payload["raw_details_ref"] is not None:
        _validate_artifact_ref(payload["raw_details_ref"])


def _validate_finished_payload(payload: dict) -> None:
    _require_fields(
        payload,
        {"status", "reason", "research_mode", "message_count"},
        "conversation_finished payload",
    )
    if payload["status"] != "ready_for_review":
        raise ConversationFormatError("Unsupported finish status.")
    _require_nonempty_string(payload["reason"], "finish reason")
    _require_nonempty_string(payload["research_mode"], "research_mode")
    if not isinstance(payload["message_count"], int) or isinstance(
        payload["message_count"],
        bool,
    ) or payload["message_count"] < 0:
        raise ConversationFormatError(
            "message_count must be a nonnegative integer."
        )


def _validate_artifact_ref(artifact_ref: Any) -> None:
    if not isinstance(artifact_ref, dict):
        raise ConversationFormatError("Artifact reference must be an object.")
    _require_fields(artifact_ref, {"path", "sha256"}, "artifact reference")
    _validate_portable_path(artifact_ref["path"])
    _validate_sha256(artifact_ref["sha256"])
    if "media_type" in artifact_ref:
        _require_nonempty_string(artifact_ref["media_type"], "media_type")


def _normalize_legacy_events(
    events: list[dict],
    *,
    conversation_id: str,
) -> list[dict]:
    _validate_conversation_id(conversation_id)
    normalized = []
    active_turn_id: str | None = None

    for sequence, legacy_event in enumerate(events, start=1):
        legacy_type = legacy_event.get("type")
        timestamp, timestamp_missing = _legacy_timestamp(legacy_event)
        extensions: dict[str, Any] = {
            "org.alexandrian.legacy": {
                "original_type": (
                    legacy_type if isinstance(legacy_type, str) else None
                ),
                "timestamp_missing": timestamp_missing,
            }
        }

        if legacy_type == "conversation_started":
            event_type = legacy_type
            turn_id = None
            payload = {
                "application": APPLICATION_NAME,
                "research_mode": _legacy_nullable_string(
                    legacy_event.get("research_mode")
                ),
                "continuation": legacy_event.get("research_continuation"),
                "instruction_snapshots": [],
            }
        elif legacy_type == "backend_selected":
            event_type = legacy_type
            turn_id = None
            host = _legacy_nullable_string(
                legacy_event.get("agent_host")
                or legacy_event.get("provider")
            )
            payload = {
                "agent_host": {"name": host or "unknown", "version": None},
                "model": {
                    "provider": _legacy_nullable_string(
                        legacy_event.get("model_provider")
                    ),
                    "name": _legacy_nullable_string(legacy_event.get("model")),
                    "version": None,
                },
            }
        elif legacy_type == "message":
            event_type = legacy_type
            role = legacy_event.get("role")
            if role == "user" or active_turn_id is None:
                active_turn_id = _legacy_uuid(
                    conversation_id,
                    f"turn:{sequence}",
                )
            turn_id = active_turn_id
            content = legacy_event.get("content")
            payload = {
                "role": role,
                "content": [
                    {
                        "type": "text",
                        "text": content if isinstance(content, str) else "",
                    }
                ],
            }
            if role == "assistant":
                payload["agent_host"] = _legacy_nullable_string(
                    legacy_event.get("provider")
                )
                active_turn_id = None
        elif legacy_type == "backend_error":
            event_type = legacy_type
            turn_id = active_turn_id or _legacy_uuid(
                conversation_id,
                f"turn:{sequence}",
            )
            payload = {
                "agent_host": _legacy_nullable_string(
                    legacy_event.get("provider")
                )
                or "unknown",
                "category": _legacy_nullable_string(
                    legacy_event.get("category")
                )
                or "legacy_error",
                "message": _legacy_nullable_string(
                    legacy_event.get("message")
                )
                or "Legacy backend error.",
                "retryable": bool(legacy_event.get("retryable", False)),
                "raw_details_ref": None,
            }
            active_turn_id = None
        elif legacy_type == "conversation_finished":
            event_type = legacy_type
            turn_id = None
            payload = {
                "status": "ready_for_review",
                "reason": _legacy_nullable_string(legacy_event.get("reason"))
                or "legacy_finish",
                "research_mode": _legacy_nullable_string(
                    legacy_event.get("research_mode")
                )
                or "safe",
                "message_count": sum(
                    event["type"] == "message" for event in normalized
                ),
            }
        else:
            event_type = "external_activity"
            turn_id = active_turn_id or _legacy_uuid(
                conversation_id,
                f"turn:{sequence}",
            )
            content = legacy_event.get("content")
            payload = {
                "activity_type": (
                    legacy_type if isinstance(legacy_type, str) else "unknown"
                ),
                "status": "recorded",
                "purpose": "Preserve a legacy material event.",
                "target": None,
                "result_summary": (
                    content[:4000] if isinstance(content, str) else ""
                ),
                "artifact_refs": [],
            }

        normalized.append(
            _make_event(
                conversation_id=conversation_id,
                sequence=sequence,
                event_type=event_type,
                turn_id=turn_id,
                payload=payload,
                extensions=extensions,
                event_id=_legacy_uuid(
                    conversation_id,
                    f"event:{sequence}",
                ),
                created_at=timestamp,
            )
        )
    return normalized


def _legacy_timestamp(event: dict) -> tuple[str, bool]:
    for field in ("created_at", "started_at", "finished_at"):
        value = event.get(field)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            continue
        normalized = parsed.astimezone(timezone.utc).isoformat().replace(
            "+00:00",
            "Z",
        )
        return normalized, False
    return UNKNOWN_LEGACY_TIME, True


def _legacy_uuid(conversation_id: str, value: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"alexandrian-library:{conversation_id}:{value}",
        )
    )


def _legacy_nullable_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _write_final_receipt(
    conversation_path: Path,
    *,
    project_root: Path,
    events: list[dict],
) -> dict:
    final_event = events[-1]
    if final_event["type"] != "conversation_finished":
        raise ConversationFormatError(
            "A receipt requires a terminal conversation_finished event."
        )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "conversation_id": conversation_path.stem,
        "status": "final",
        "event_count": len(events),
        "final_sequence": final_event["sequence"],
        "transcript_path": _project_relative_path(
            conversation_path,
            project_root,
        ),
        "transcript_sha256": _file_sha256(conversation_path),
        "finished_at": final_event["created_at"],
    }
    _validate_receipt(receipt)
    _atomic_write_json(receipt_path_for(conversation_path), receipt)
    return receipt


def _validate_receipt(receipt: Any) -> None:
    if not isinstance(receipt, dict):
        raise ConversationIntegrityError("Conversation receipt must be an object.")
    required = {
        "schema_version",
        "conversation_id",
        "status",
        "event_count",
        "final_sequence",
        "transcript_path",
        "transcript_sha256",
        "finished_at",
    }
    try:
        _require_fields(receipt, required, "Conversation receipt")
        _validate_schema_version(receipt["schema_version"])
        _validate_conversation_id(receipt["conversation_id"])
        if receipt["status"] != "final":
            raise ConversationFormatError("Receipt status must be final.")
        for field in ("event_count", "final_sequence"):
            if not isinstance(receipt[field], int) or isinstance(
                receipt[field],
                bool,
            ) or receipt[field] < 1:
                raise ConversationFormatError(
                    f"{field} must be a positive integer."
                )
        _validate_portable_path(receipt["transcript_path"])
        _validate_sha256(receipt["transcript_sha256"])
        _validate_timestamp(receipt["finished_at"], "finished_at")
    except ConversationFormatError as error:
        raise ConversationIntegrityError(str(error)) from error


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f"{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except OSError as error:
        temporary_path.unlink(missing_ok=True)
        raise ConversationStoreError(
            f"Conversation receipt could not be written: {path}"
        ) from error


def _project_relative_path(path: Path, project_root: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(project_root.resolve())
    except ValueError as error:
        raise ConversationFormatError(
            "Transcript path must be inside the project root."
        ) from error
    portable_path = relative_path.as_posix()
    _validate_portable_path(portable_path)
    return portable_path


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ConversationStoreError(
            f"Conversation could not be hashed: {path}"
        ) from error


def _validate_schema_version(value: Any) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+", value):
        raise ConversationFormatError("schema_version must be major.minor.")
    major = int(value.split(".", maxsplit=1)[0])
    if major != SUPPORTED_SCHEMA_MAJOR:
        raise ConversationFormatError(
            f"Unsupported conversation schema major version: {major}"
        )


def _validate_conversation_id(value: Any) -> None:
    if not isinstance(value, str) or not CONVERSATION_ID_PATTERN.fullmatch(
        value
    ):
        raise ConversationFormatError(
            "conversation_id contains unsupported characters."
        )


def _validate_uuid(value: Any, field: str) -> None:
    if not isinstance(value, str):
        raise ConversationFormatError(f"{field} must be a UUID string.")
    try:
        uuid.UUID(value)
    except ValueError as error:
        raise ConversationFormatError(
            f"{field} must be a valid UUID."
        ) from error


def _validate_timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ConversationFormatError(f"{field} must be a UTC timestamp.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ConversationFormatError(
            f"{field} must be a valid UTC timestamp."
        ) from error
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ConversationFormatError(f"{field} must use UTC.")


def _validate_portable_path(value: Any) -> None:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value)
    ):
        raise ConversationFormatError(
            "Artifact paths must be project-relative POSIX paths."
        )
    parts = PurePosixPath(value).parts
    if ".." in parts or "." in parts:
        raise ConversationFormatError(
            "Artifact paths cannot contain dot traversal segments."
        )


def _validate_sha256(value: Any) -> None:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise ConversationFormatError(
            "sha256 values must contain 64 lowercase hexadecimal characters."
        )


def _require_fields(value: dict, fields: set[str], label: str) -> None:
    missing = sorted(fields - value.keys())
    if missing:
        raise ConversationFormatError(
            f"{label} is missing required fields: {', '.join(missing)}"
        )


def _require_nonempty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ConversationFormatError(f"{field} must be a nonempty string.")


def _require_nullable_string(value: Any, field: str) -> None:
    if value is not None and not isinstance(value, str):
        raise ConversationFormatError(f"{field} must be a string or null.")
