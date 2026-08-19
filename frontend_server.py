"""Local-only frontend server for the Alexandrian Library.

The backend remains authoritative. This adapter serves verified bookshelf data
and a narrow versioned API over the UI-neutral workflow service.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import mimetypes
import os
import re
import socket
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import environment_check
import cover_design
import portable_export
import workflow_service


FRONTEND_VERSION = "1.1.0"
API_VERSION = "v1"
MAX_JSON_REQUEST_BYTES = 1_048_576
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
BOOK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
CONVERSATION_ID = r"([A-Za-z0-9._-]+)"
WORKFLOW_ROUTE = re.compile(rf"^/api/v1/workflows/{CONVERSATION_ID}$")
TURN_ROUTE = re.compile(rf"^/api/v1/conversations/{CONVERSATION_ID}/turns$")
TURN_ASYNC_ROUTE = re.compile(
    rf"^/api/v1/conversations/{CONVERSATION_ID}/turns/async$"
)
BACKGROUND_BOOK_ROUTE = re.compile(
    rf"^/api/v1/conversations/{CONVERSATION_ID}/build/async$"
)
FINISH_ROUTE = re.compile(rf"^/api/v1/conversations/{CONVERSATION_ID}/finish$")
FINISH_ASYNC_ROUTE = re.compile(
    rf"^/api/v1/conversations/{CONVERSATION_ID}/finish/async$"
)
PROVIDER_ROUTE = re.compile(
    rf"^/api/v1/conversations/{CONVERSATION_ID}/provider$"
)
DECISION_ROUTE = re.compile(rf"^/api/v1/workflows/{CONVERSATION_ID}/decisions$")
SOURCE_MODE_ROUTE = re.compile(
    rf"^/api/v1/workflows/{CONVERSATION_ID}/source-mode$"
)
QUESTION_MODE_ROUTE = re.compile(
    rf"^/api/v1/workflows/{CONVERSATION_ID}/question-mode$"
)
ADVANCE_ROUTE = re.compile(rf"^/api/v1/workflows/{CONVERSATION_ID}/advance$")
ADVANCE_ASYNC_ROUTE = re.compile(
    rf"^/api/v1/workflows/{CONVERSATION_ID}/advance/async$"
)
JOB_ID = r"([0-9a-f]{32})"
JOB_ROUTE = re.compile(rf"^/api/v1/jobs/{JOB_ID}$")
TRANSITION_EVENTS_ROUTE = re.compile(
    rf"^/api/v1/workflows/{CONVERSATION_ID}/events$"
)
BOOK_ROUTE = re.compile(r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})$")
PROVENANCE_ROUTE = re.compile(
    r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})/provenance$"
)
RUN_RECEIPT_ROUTE = re.compile(
    r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})/run-receipt$"
)
COVER_ROUTE = re.compile(
    r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})/cover$"
)
BOOK_EXPORT_ROUTE = re.compile(
    r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})/exports/(markdown|epub|evidence-manifest)$"
)
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/assets/branches-cover-holy-cyber-v3.png": (
        "assets/branches-cover-holy-cyber-v3.png"
    ),
    "/assets/branches-cover-holy-cyber-v3-display.jpg": (
        "assets/branches-cover-holy-cyber-v3-display.jpg"
    ),
    "/assets/readable-cover-forbidden-archive-v1.png": (
        "assets/readable-cover-forbidden-archive-v1.png"
    ),
    "/assets/readable-cover-forbidden-archive-v1-display.jpg": (
        "assets/readable-cover-forbidden-archive-v1-display.jpg"
    ),
    "/assets/archive-sentinel-v1-display.jpg": (
        "assets/archive-sentinel-v1-display.jpg"
    ),
    "/assets/inquiry-worktable-v1.png": (
        "assets/inquiry-worktable-v1.png"
    ),
}
SERVER_STARTED_AT = time.time()
TRANSITION_PHASE_LABELS = {
    "transition_preflight_passed": "Verified the durable transition",
    "provider_started": "Provider is working on the current artifact",
    "mechanical_retry_scheduled": "Correcting the provider handoff",
    "artifact_reconciled_after_provider_error": "Recovered the saved artifact",
    "durable_artifact_saved": "Durable artifact saved",
    "provider_failed": "Provider stopped before saving",
    "provider_returned_without_transition": "Checking an incomplete provider response",
}


def local_bind_hosts() -> set[str]:
    """Return loopback plus this process's exact private WSL interface."""
    hosts = set(LOOPBACK_HOSTS)
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        routed_candidate = probe.getsockname()[0]
        routed_address = ipaddress.ip_address(routed_candidate)
        if routed_address.is_private and not routed_address.is_unspecified:
            hosts.add(routed_candidate)
    except OSError:
        pass
    finally:
        probe.close()
    return hosts


def build_server_identity(project_path: Path) -> dict[str, Any]:
    """Bind health reporting to the exact code loaded by this server."""
    digest = hashlib.sha256()
    for relative in (
        "frontend_server.py",
        "workflow_service.py",
        "backend_selector.py",
        "mcp_server.py",
        "transition_runtime.py",
    ):
        path = project_path / relative
        digest.update(relative.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
    return {
        "schema_version": "1.0",
        "status": "available",
        "api_version": API_VERSION,
        "frontend_version": FRONTEND_VERSION,
        "process_id": os.getpid(),
        "started_at": SERVER_STARTED_AT,
        "build_sha256": digest.hexdigest(),
    }


class FrontendDataError(RuntimeError):
    """Reject an unsafe or inconsistent frontend projection."""


class WorkflowMutationLanes:
    """Serialize one inquiry without blocking unrelated inquiries."""

    CATALOG_LANE = "__catalog__"

    def __init__(self) -> None:
        self._registry_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def lock_for(self, conversation_id: str | None) -> threading.Lock:
        key = conversation_id or self.CATALOG_LANE
        with self._registry_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


class WorkflowJobStore:
    """Run provider work off-request while keeping visible bounded status."""

    def __init__(
        self,
        mutation_lanes: WorkflowMutationLanes,
        project_path: Path | None = None,
        server_identity: dict[str, Any] | None = None,
    ) -> None:
        self._mutation_lanes = mutation_lanes
        self._project_path = project_path
        self._server_identity = dict(server_identity or {})
        self._runtime_session_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_by_conversation: dict[str, str] = {}

    def _public(self, job: dict[str, Any]) -> dict[str, Any]:
        public = {
            key: value
            for key, value in job.items()
            if not key.startswith("_")
        }
        started = job.get("_started_monotonic", job["_created_monotonic"])
        ended = job.get("_ended_monotonic", time.monotonic())
        public["elapsed_seconds"] = max(0, round(ended - started, 1))
        return public

    def list(self) -> dict[str, Any]:
        with self._lock:
            jobs = [self._public(job) for job in self._jobs.values()]
        jobs.sort(key=lambda job: job["created_at"], reverse=True)
        return {
            "schema_version": "1.0",
            "job_count": len(jobs),
            "jobs": jobs[:100],
        }

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return self._public(job)

    def start(
        self,
        conversation_id: str,
        kind: str,
        provider: str | None,
        operation: Any,
        *,
        exclusive: bool,
    ) -> dict[str, Any]:
        try:
            initial_workflow = workflow_service.inspect_workflow(
                conversation_id,
                include_messages=False,
            )
            initial_progress = int(initial_workflow.get("overall_progress", 1))
            initial_phase = initial_workflow.get("stage_label", "Workflow queued")
        except workflow_service.WorkflowServiceError:
            initial_progress = 1
            initial_phase = "Workflow queued"
        with self._lock:
            active_id = self._active_by_conversation.get(conversation_id)
            if active_id is not None:
                active = self._jobs.get(active_id)
                if active and active["status"] in {"queued", "running"}:
                    raise workflow_service.WorkflowConflictError(
                        "conversation_job_active",
                        "This inquiry already has provider work in progress.",
                    )
            job_id = uuid.uuid4().hex
            now = time.time()
            job = {
                "schema_version": "1.0",
                "job_id": job_id,
                "conversation_id": conversation_id,
                "kind": kind,
                "provider": provider,
                "status": "queued",
                "progress": initial_progress,
                "overall_progress": initial_progress,
                "transition_progress": 1,
                "phase": "Queued",
                "overall_phase": initial_phase,
                "transition_phase": "Queued",
                "created_at": now,
                "result": None,
                "error": None,
                "_created_monotonic": time.monotonic(),
            }
            self._jobs[job_id] = job
            self._active_by_conversation[conversation_id] = job_id

        try:
            workflow_service.append_runtime_event(
                conversation_id,
                {
                "schema_version": "1.0",
                "runtime_session_id": self._runtime_session_id,
                "build_sha256": self._server_identity.get("build_sha256"),
                "process_id": self._server_identity.get("process_id"),
                "server_started_at": self._server_identity.get("started_at"),
                "event": "job_started",
                "job_id": job_id,
                "kind": kind,
                "provider": provider,
                "status": "running",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        except (workflow_service.WorkflowServiceError, OSError):
            pass

        def report(percent: int, phase: str) -> None:
            bounded = max(1, min(99, percent))
            try:
                canonical = workflow_service.inspect_workflow(
                    conversation_id,
                    include_messages=False,
                )
            except workflow_service.WorkflowServiceError:
                canonical = None
            with self._lock:
                job["transition_progress"] = bounded
                job["transition_phase"] = phase
                job["phase"] = phase
                if canonical is not None:
                    job["overall_progress"] = int(
                        canonical.get("overall_progress", initial_progress)
                    )
                    job["progress"] = job["overall_progress"]
                    job["overall_phase"] = canonical.get(
                        "stage_label", job["overall_phase"]
                    )

        def run() -> None:
            acquired = False
            watcher_stop = threading.Event()

            def watch_transition_events() -> None:
                last_event_id = None
                while not watcher_stop.wait(0.5):
                    try:
                        payload = workflow_service.transition_events(
                            conversation_id,
                            limit=1,
                        )
                    except workflow_service.WorkflowServiceError:
                        continue
                    events = payload.get("events", [])
                    if not events:
                        continue
                    event = events[-1]
                    if event.get("event_id") == last_event_id:
                        continue
                    last_event_id = event.get("event_id")
                    with self._lock:
                        if job.get("status") != "running":
                            continue
                        job["latest_transition_event"] = event
                        transition_phase = TRANSITION_PHASE_LABELS.get(
                            event.get("phase"),
                            str(event.get("phase", "Provider work is progressing")),
                        )
                        job["phase"] = transition_phase
                        job["transition_phase"] = transition_phase
                        job["transition_progress"] = min(
                            100, max(0, int(event.get("percent", 0)))
                        )
                        try:
                            canonical = workflow_service.inspect_workflow(
                                conversation_id,
                                include_messages=False,
                            )
                        except workflow_service.WorkflowServiceError:
                            canonical = None
                        if canonical is not None:
                            job["overall_progress"] = int(
                                canonical.get("overall_progress", initial_progress)
                            )
                            job["progress"] = job["overall_progress"]
                            job["overall_phase"] = canonical.get(
                                "stage_label", job["overall_phase"]
                            )

            try:
                with self._lock:
                    job["status"] = "running"
                    job["_started_monotonic"] = time.monotonic()
                    job["phase"] = (
                        "Waiting for the mutation lane"
                        if exclusive
                        else "Loading the durable inquiry"
                    )
                    job["progress"] = job["overall_progress"]
                    job["transition_progress"] = 1
                # Every provider-backed mutation belongs to its inquiry. This
                # prevents same-inquiry races while allowing other books to
                # keep moving in parallel.
                self._mutation_lanes.lock_for(conversation_id).acquire()
                acquired = True
                threading.Thread(
                    target=watch_transition_events,
                    name=f"alexandrian-events-{job_id[:8]}",
                    daemon=True,
                ).start()
                result = operation(report)
            except workflow_service.WorkflowServiceError as error:
                with self._lock:
                    job["status"] = "failed"
                    job["phase"] = "Stopped with a durable error"
                    job["error"] = {
                        "code": error.code,
                        "message": str(error),
                    }
            except Exception:
                with self._lock:
                    job["status"] = "failed"
                    job["phase"] = "Stopped unexpectedly"
                    job["error"] = {
                        "code": "background_job_failed",
                        "message": "The background workflow job stopped unexpectedly.",
                    }
            else:
                result_workflow = None
                if isinstance(result, dict):
                    candidate = result.get("workflow", result)
                    if isinstance(candidate, dict):
                        result_workflow = candidate
                try:
                    canonical = workflow_service.inspect_workflow(
                        conversation_id,
                        include_messages=False,
                    )
                except workflow_service.WorkflowServiceError:
                    canonical = None
                if (
                    result_workflow is not None
                    and result_workflow.get("conversation_id")
                    != conversation_id
                ):
                    canonical = result_workflow
                elif canonical is None:
                    canonical = result_workflow
                with self._lock:
                    job["status"] = "completed"
                    job["progress"] = 100
                    job["transition_progress"] = 100
                    job["phase"] = "Canonical state saved"
                    job["transition_phase"] = "Canonical state saved"
                    if canonical is not None and isinstance(
                        canonical.get("overall_progress"), int
                    ):
                        job["overall_progress"] = canonical["overall_progress"]
                        job["overall_phase"] = canonical.get(
                            "stage_label", job["overall_phase"]
                        )
                    job["result"] = result
            finally:
                watcher_stop.set()
                if acquired:
                    self._mutation_lanes.lock_for(conversation_id).release()
                with self._lock:
                    job["_ended_monotonic"] = time.monotonic()
                    elapsed = max(
                        0,
                        round(
                            job["_ended_monotonic"]
                            - job.get("_started_monotonic", job["_created_monotonic"]),
                            1,
                        ),
                    )
                    if self._active_by_conversation.get(conversation_id) == job_id:
                        del self._active_by_conversation[conversation_id]
                try:
                    workflow_service.append_runtime_event(
                        conversation_id,
                        {
                        "schema_version": "1.0",
                        "runtime_session_id": self._runtime_session_id,
                        "build_sha256": self._server_identity.get("build_sha256"),
                        "process_id": self._server_identity.get("process_id"),
                        "server_started_at": self._server_identity.get("started_at"),
                        "event": "job_finished",
                        "job_id": job_id,
                        "kind": kind,
                        "provider": provider,
                        "status": job["status"],
                        "elapsed_seconds": elapsed,
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
                except (workflow_service.WorkflowServiceError, OSError):
                    pass
                try:
                    canonical = workflow_service.inspect_workflow(
                        conversation_id,
                        include_messages=False,
                    )
                    if canonical.get("stage") == "complete":
                        receipt = workflow_service.write_e2e_run_receipt(
                            conversation_id
                        )
                        with self._lock:
                            job["run_receipt"] = receipt
                except workflow_service.WorkflowServiceError:
                    pass

        threading.Thread(
            target=run,
            name=f"alexandrian-{kind}-{job_id[:8]}",
            daemon=True,
        ).start()
        return self.get(job_id)


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FrontendDataError(f"{label} could not be read.") from error
    if not isinstance(value, dict):
        raise FrontendDataError(f"{label} must be a JSON object.")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FrontendDataError(f"{label} must be a non-empty string.")
    return value.strip()


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise FrontendDataError(f"Required artifact could not be hashed: {path}") from error


def load_catalog(project_path: Path) -> dict[str, Any]:
    """Load the existing deterministic bookshelf projection without rebuilding it."""
    catalog_path = project_path / "data" / "bookshelf" / "catalog.json"
    assembly_paths = sorted(
        (project_path / "data" / "books").glob("*/assembly.json")
    )
    if not catalog_path.is_file():
        if assembly_paths:
            raise FrontendDataError(
                "Final assemblies exist, but the bookshelf catalog is missing."
            )
        return {
            "schema_version": "1.0",
            "generated_at": None,
            "book_count": 0,
            "books": [],
            "projection_status": "empty_bookshelf",
        }
    catalog = _json_object(catalog_path, "Bookshelf catalog")
    if catalog.get("schema_version") != "1.0":
        raise FrontendDataError("Unsupported bookshelf schema.")
    books = catalog.get("books")
    if not isinstance(books, list) or any(not isinstance(book, dict) for book in books):
        raise FrontendDataError("Bookshelf catalog has no valid book list.")
    if catalog.get("book_count") != len(books):
        raise FrontendDataError("Bookshelf book count does not match its entries.")

    seen_ids: set[str] = set()
    for book in books:
        book_id = _required_text(book.get("book_id"), "Book ID")
        if not BOOK_ID_PATTERN.fullmatch(book_id):
            raise FrontendDataError(f"Book ID is unsafe: {book_id}")
        if book_id in seen_ids:
            raise FrontendDataError(f"Duplicate book ID: {book_id}")
        if book.get("artifact_status") != "final":
            raise FrontendDataError(f"Bookshelf contains a non-final book: {book_id}")
        _required_text(book.get("title"), f"Title for {book_id}")
        cover = book.get("cover")
        if not isinstance(cover, dict):
            raise FrontendDataError(f"Bookshelf contains no cover for {book_id}")
        _required_text(cover.get("asset_url"), f"Cover URL for {book_id}")
        _required_text(cover.get("asset_sha256"), f"Cover hash for {book_id}")
        _required_text(cover.get("quote"), f"Cover quotation for {book_id}")
        seen_ids.add(book_id)
    assembly_ids = {path.parent.name for path in assembly_paths}
    if seen_ids != assembly_ids:
        raise FrontendDataError(
            "Bookshelf entries do not exactly match finalized assemblies."
        )
    return catalog


def load_cover_asset(project_path: Path, book_id: str) -> tuple[bytes, str]:
    """Return one catalog-bound cover only after its hashes verify."""
    catalog = load_catalog(project_path)
    entry = next(
        (book for book in catalog["books"] if book.get("book_id") == book_id),
        None,
    )
    if entry is None:
        raise KeyError(book_id)
    try:
        design = cover_design.load_and_verify(project_path, book_id)
    except cover_design.CoverDesignError as error:
        raise FrontendDataError(str(error)) from error
    cover = entry.get("cover")
    asset = design["asset"]
    if (
        not isinstance(cover, dict)
        or cover.get("design_sha256") != design.get("design_sha256")
        or cover.get("asset_sha256") != asset.get("sha256")
        or cover.get("asset_path") != asset.get("path")
    ):
        raise FrontendDataError("Bookshelf cover metadata is stale.")
    asset_path = (project_path / asset["path"]).resolve()
    try:
        asset_path.relative_to(
            (project_path / "data" / "books" / book_id / "cover").resolve()
        )
    except ValueError as error:
        raise FrontendDataError("Cover asset is outside its finalized book.") from error
    return asset_path.read_bytes(), asset["mime_type"]


def _resolve_book_path(project_path: Path, recorded_path: Any) -> Path:
    relative = _required_text(recorded_path, "Book path")
    books_root = (project_path / "data" / "books").resolve()
    resolved = (project_path / relative).resolve()
    try:
        resolved.relative_to(books_root)
    except ValueError as error:
        raise FrontendDataError("Book path escapes the finalized books directory.") from error
    if not resolved.is_file():
        raise FrontendDataError("Final book artifact does not exist.")
    return resolved


def load_book(project_path: Path, book_id: str) -> dict[str, Any]:
    """Return one finalized book only after its catalog path and hash verify."""
    if not BOOK_ID_PATTERN.fullmatch(book_id):
        raise FrontendDataError("Book ID has an invalid format.")
    catalog = load_catalog(project_path)
    entry = next(
        (book for book in catalog["books"] if book.get("book_id") == book_id),
        None,
    )
    if entry is None:
        raise KeyError(book_id)

    book_path = _resolve_book_path(project_path, entry.get("book_path"))
    expected_hash = _required_text(entry.get("book_sha256"), "Book SHA-256")
    if len(expected_hash) != 64 or _file_hash(book_path) != expected_hash:
        raise FrontendDataError("Final book hash does not match the bookshelf.")
    assembly_path = _resolve_book_path(
        project_path,
        entry.get("assembly_path"),
    )
    expected_assembly_hash = _required_text(
        entry.get("assembly_sha256"),
        "Assembly SHA-256",
    )
    if (
        len(expected_assembly_hash) != 64
        or _file_hash(assembly_path) != expected_assembly_hash
    ):
        raise FrontendDataError(
            "Final assembly hash does not match the bookshelf."
        )
    assembly = _json_object(assembly_path, "Final assembly")
    if (
        assembly.get("book_id") != book_id
        or assembly.get("lifecycle_status") != "final"
        or assembly.get("book_sha256") != expected_hash
    ):
        raise FrontendDataError(
            "Final assembly identity does not match the bookshelf."
        )
    try:
        markdown = book_path.read_text(encoding="utf-8")
    except OSError as error:
        raise FrontendDataError("Final book text could not be read.") from error

    return {
        "schema_version": "1.0",
        "book": entry,
        "markdown": markdown,
        "verified_sha256": expected_hash,
        "assembly": {
            "assembly_id": assembly.get("assembly_id"),
            "assembled_at": assembly.get("assembled_at"),
            "source_count": assembly.get("source_count"),
            "source_receipt_ids": assembly.get("source_receipt_ids", []),
            "verified_sha256": expected_assembly_hash,
            "finalization_authority": assembly.get("finalization_authority"),
            "workflow_policy_version": assembly.get("workflow_policy_version"),
            "review_status": (
                "generated_for_review"
                if assembly.get("finalization_authority") == "automated_audit_policy"
                else "human_approved_final"
            ),
        },
    }


def _source_receipt_identity(document: dict[str, Any]) -> str | None:
    receipt = document.get("receipt")
    if isinstance(receipt, dict):
        value = receipt.get("receipt_id")
    else:
        value = document.get("receipt_id")
    return value if isinstance(value, str) and value else None


def _source_receipt_summary(document: dict[str, Any]) -> dict[str, Any]:
    nested = document.get("receipt")
    source = nested if isinstance(nested, dict) else document
    return {
        "receipt_id": _source_receipt_identity(document),
        "title": source.get("title"),
        "domain": source.get("domain"),
        "requested_url": source.get("requested_url"),
        "final_url": source.get("final_url"),
        "retrieved_at": source.get("retrieved_at"),
        "content_type": source.get("content_type"),
        "content_sha256": source.get("content_sha256")
        or source.get("source_content_sha256"),
        "content_truncated": source.get("content_truncated")
        if "content_truncated" in source
        else source.get("source_content_truncated"),
        "source_document_path": source.get("source_document_path"),
        "source_document_sha256": source.get("source_document_sha256"),
        "claim_checked": source.get("claim_checked"),
        "finding": source.get("finding"),
        "assessment": source.get("assessment"),
        "evidence_location": source.get("evidence_location"),
        "evidence_locator": source.get("evidence_locator"),
        "evidence_excerpt": source.get("evidence_excerpt"),
        "evidence_excerpt_sha256": source.get("evidence_excerpt_sha256"),
        "limitations": source.get("limitations"),
        "research_question": source.get("research_question"),
    }


def _merge_source_receipt_records(
    receipt_id: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge one receipt's fetch record with its richer evidence packet."""
    summaries = [_source_receipt_summary(document) for document in documents]
    identity_fields = (
        "domain",
        "requested_url",
        "final_url",
        "content_sha256",
        "source_document_sha256",
    )
    for field_name in identity_fields:
        values = {
            summary.get(field_name)
            for summary in summaries
            if summary.get(field_name) is not None
        }
        if len(values) > 1:
            raise FrontendDataError(
                f"Source receipt {receipt_id} has conflicting {field_name}."
            )

    merged = {"receipt_id": receipt_id}
    for field_name in summaries[0]:
        if field_name == "receipt_id":
            continue
        values = [
            summary.get(field_name)
            for summary in summaries
            if summary.get(field_name) is not None
        ]
        merged[field_name] = values[-1] if values else None
    return merged


def _load_bound_review_evidence_status(
    project_path: Path,
    book_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Expose non-admitted evidence only when its review is hash-bound."""
    book_directory = (project_path / "data" / "books" / book_id).resolve()
    assembly = _json_object(book_directory / "assembly.json", "Final assembly")
    candidate_relative = assembly.get("candidate_assembly_path")
    candidate_hash = assembly.get("candidate_assembly_sha256")
    if not isinstance(candidate_relative, str) or not isinstance(candidate_hash, str):
        return [], []
    candidate_path = (project_path / candidate_relative).resolve()
    try:
        candidate_path.relative_to(book_directory)
    except ValueError as error:
        raise FrontendDataError(
            "Candidate assembly escapes its finalized book."
        ) from error
    if not candidate_path.is_file() or _file_hash(candidate_path) != candidate_hash:
        raise FrontendDataError("Candidate assembly hash does not match the final book.")
    candidate = _json_object(candidate_path, "Candidate assembly")
    inputs = candidate.get("inputs")
    if not isinstance(inputs, list):
        raise FrontendDataError("Candidate assembly has no valid input ledger.")
    review_inputs = [
        item
        for item in inputs
        if isinstance(item, dict) and item.get("role") == "research_review"
    ]
    if len(review_inputs) != 1:
        raise FrontendDataError(
            "Candidate assembly has no unique research-review input."
        )
    review_input = review_inputs[0]
    relative_review_path = review_input.get("path")
    review_hash = review_input.get("sha256")
    if not isinstance(relative_review_path, str) or not isinstance(review_hash, str):
        raise FrontendDataError("Research-review input provenance is invalid.")
    review_path = (project_path / relative_review_path).resolve()
    reviews_root = (project_path / "data" / "reviews").resolve()
    try:
        review_path.relative_to(reviews_root)
    except ValueError as error:
        raise FrontendDataError(
            "Research-review input escapes the review archive."
        ) from error
    if not review_path.is_file() or _file_hash(review_path) != review_hash:
        raise FrontendDataError("Research-review hash does not match the book input.")
    document = _json_object(review_path, "Canonical research review")
    review = document.get("review")
    if (
        not isinstance(review, dict)
        or document.get("review_id") != f"review-{review_path.stem}"
        or document.get("conversation_id") != review_path.stem
    ):
        raise FrontendDataError("Research-review identity is invalid.")

    statuses = []
    for item in review.get("research_trail", []):
        if not isinstance(item, dict) or item.get("decision") == "included":
            continue
        question = item.get("query_or_question")
        reason = item.get("reason")
        decision = item.get("decision")
        if not all(isinstance(value, str) and value for value in (question, reason, decision)):
            continue
        statuses.append(
            {
                "status": decision,
                "question": question,
                "candidate_source_url": item.get("candidate_source_url"),
                "reason": reason,
                "transcript_references": item.get("transcript_references", []),
                "verified": False,
            }
        )

    gaps = []
    for item in review.get("evidence_gaps", []):
        if not isinstance(item, dict) or item.get("disposition") == "resolved":
            continue
        text = item.get("text")
        closure_reason = item.get("closure_reason")
        if not isinstance(text, str) or not text:
            continue
        gaps.append(
            {
                "text": text,
                "disposition": item.get("disposition"),
                "book_blocking": item.get("book_blocking") is True,
                "closure_reason": closure_reason if isinstance(closure_reason, str) else None,
                "destination": item.get("destination"),
                "transcript_references": item.get("transcript_references", []),
            }
        )
    return statuses, gaps


def load_book_provenance(project_path: Path, book_id: str) -> dict[str, Any]:
    """Resolve exact source IDs for one already verified final publication."""
    book = load_book(project_path, book_id)
    source_ids = book["assembly"]["source_receipt_ids"]
    if not isinstance(source_ids, list) or any(
        not isinstance(receipt_id, str) or not receipt_id
        for receipt_id in source_ids
    ):
        raise FrontendDataError("Final assembly source IDs are invalid.")
    if book["assembly"]["source_count"] != len(source_ids):
        raise FrontendDataError("Final assembly source count is invalid.")

    candidate_paths = list(
        (project_path / "data" / "evidence").glob("**/*.json")
    ) + list(
        (project_path / "data" / "books" / book_id / "research").glob(
            "**/source_receipts/**/*.json"
        )
    )
    by_id: dict[str, list[dict[str, Any]]] = {}
    for path in candidate_paths:
        document = _json_object(path, "Source receipt")
        receipt_id = _source_receipt_identity(document)
        if receipt_id in source_ids:
            by_id.setdefault(receipt_id, []).append(document)

    # A chapter may have a bare canonical fetch receipt plus a richer immutable
    # research-packet record. Resolve both by the final assembly's exact ID,
    # reject conflicting lineage, and preserve the richer evidence fields.
    packet_paths = sorted(
        (project_path / "data" / "books" / book_id / "research").glob(
            "*/R*.json"
        )
    )
    for path in packet_paths:
        packet = _json_object(path, "Chapter research packet")
        sources = packet.get("sources")
        if not isinstance(sources, list):
            continue
        for source in sources:
            if not isinstance(source, dict):
                continue
            receipt_id = _source_receipt_identity(source)
            if receipt_id in source_ids:
                by_id.setdefault(receipt_id, []).append(source)

    records = []
    for receipt_id in source_ids:
        matches = by_id.get(receipt_id, [])
        if not matches:
            raise FrontendDataError(
                f"Source receipt {receipt_id} did not resolve."
            )
        records.append(_merge_source_receipt_records(receipt_id, matches))
    evidence_statuses, evidence_gaps = _load_bound_review_evidence_status(
        project_path,
        book_id,
    )
    return {
        "schema_version": "1.0",
        "book_id": book_id,
        "book_sha256": book["verified_sha256"],
        "assembly": book["assembly"],
        "source_count": len(records),
        "sources": records,
        "evidence_status_count": len(evidence_statuses),
        "evidence_statuses": evidence_statuses,
        "evidence_gap_count": len(evidence_gaps),
        "evidence_gaps": evidence_gaps,
    }


def build_frontend_readiness(
    project_path: Path,
    *,
    check_runtime: bool,
) -> dict[str, Any]:
    """Return provider and full-workflow readiness without changing state."""
    results = environment_check.collect_environment_results(
        project_path,
        check_runtime=check_runtime,
    )
    result_by_label = {result.label: result for result in results}
    runnable = result_by_label.get("Runnable full-workflow host")
    configured = set()
    if runnable is not None and runnable.status == "available":
        prefix = "Configured: "
        if runnable.detail.startswith(prefix):
            configured = {
                value.strip()
                for value in runnable.detail.removeprefix(prefix).split(",")
                if value.strip()
            }
    providers = {}
    for provider in environment_check.SUPPORTED_BACKENDS:
        result = result_by_label.get(f"Backend: {provider}")
        installed = result is not None and result.status == "available"
        providers[provider] = {
            "installed": installed,
            "full_workflow_ready": provider in configured,
            "detail": result.detail if result is not None else "Not checked.",
        }
    required_failures = [
        result.label
        for result in results
        if result.required and result.status == "missing"
    ]
    return {
        "schema_version": "1.0",
        "status": "ready" if configured and not required_failures else "degraded",
        "frontend_version": FRONTEND_VERSION,
        "api_version": API_VERSION,
        "providers": providers,
        "runnable_providers": sorted(configured),
        "required_failures": required_failures,
        "runtime_checked": check_runtime,
    }


class FrontendRequestHandler(BaseHTTPRequestHandler):
    """Serve static files plus the local-only V1 workflow API."""

    project_path: Path
    frontend_path: Path
    mutation_lanes: WorkflowMutationLanes
    job_store: WorkflowJobStore
    allowed_hosts: set[str]
    readiness: dict[str, Any]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        filename: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if filename is not None:
            self.send_header(
                "Content-Disposition",
                f'attachment; filename="{filename}"',
            )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        value: dict[str, Any],
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._send_json({"error": code, "message": message}, status)

    def _request_context_is_valid(self) -> bool:
        host_header = self.headers.get("Host")
        if not host_header:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_host", "Host is required.")
            return False
        parsed_host = urlsplit(f"//{host_header}")
        if parsed_host.hostname not in self.allowed_hosts:
            self._error(
                HTTPStatus.FORBIDDEN,
                "invalid_host",
                "The workflow API accepts only this local Library host.",
            )
            return False

        origin = self.headers.get("Origin")
        if origin:
            parsed_origin = urlsplit(origin)
            server_port = int(self.server.server_address[1])
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname not in self.allowed_hosts
                or parsed_origin.port != server_port
            ):
                self._error(
                    HTTPStatus.FORBIDDEN,
                    "invalid_origin",
                    "Origin must match this local frontend server.",
                )
                return False
        return True

    def _read_json_object(self) -> dict[str, Any] | None:
        content_type = self.headers.get("Content-Type", "")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            self._error(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "json_required",
                "Mutating workflow requests must use application/json.",
            )
            return None
        length_text = self.headers.get("Content-Length")
        try:
            length = int(length_text) if length_text is not None else -1
        except ValueError:
            length = -1
        if length < 0:
            self._error(
                HTTPStatus.LENGTH_REQUIRED,
                "content_length_required",
                "A valid Content-Length is required.",
            )
            return None
        if length > MAX_JSON_REQUEST_BYTES:
            self._error(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "request_too_large",
                f"JSON requests are limited to {MAX_JSON_REQUEST_BYTES} bytes.",
            )
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                "Request body must be one valid JSON object.",
            )
            return None
        if not isinstance(payload, dict):
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_json",
                "Request body must be one JSON object.",
            )
            return None
        return payload

    def _require_fields(
        self,
        payload: dict[str, Any],
        required: set[str],
        optional: set[str] | None = None,
    ) -> bool:
        allowed = required | (optional or set())
        missing = sorted(required - payload.keys())
        unknown = sorted(payload.keys() - allowed)
        if missing or unknown:
            details = []
            if missing:
                details.append("missing: " + ", ".join(missing))
            if unknown:
                details.append("unknown: " + ", ".join(unknown))
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_request_fields",
                "Request fields are invalid (" + "; ".join(details) + ").",
            )
            return False
        return True

    def _service_error(self, error: workflow_service.WorkflowServiceError) -> None:
        if isinstance(error, workflow_service.WorkflowNotFoundError):
            status = HTTPStatus.NOT_FOUND
        elif isinstance(error, workflow_service.WorkflowConflictError):
            status = HTTPStatus.CONFLICT
        elif isinstance(error, workflow_service.WorkflowOperationError):
            status = HTTPStatus.BAD_GATEWAY
        else:
            status = HTTPStatus.BAD_REQUEST
        self._error(status, error.code, str(error))

    def _run_mutation(
        self,
        operation: Any,
        *,
        conversation_id: str | None = None,
        success_status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        lane = self.mutation_lanes.lock_for(conversation_id)
        if not lane.acquire(blocking=False):
            self._error(
                HTTPStatus.CONFLICT,
                "mutation_in_progress",
                "This inquiry already has a mutation in progress.",
            )
            return
        try:
            result = operation()
        except workflow_service.WorkflowServiceError as error:
            self._service_error(error)
        else:
            self._send_json(result, success_status)
        finally:
            lane.release()

    def _start_background_job(
        self,
        conversation_id: str,
        kind: str,
        provider: str | None,
        operation: Any,
        *,
        exclusive: bool,
    ) -> None:
        try:
            job = self.job_store.start(
                conversation_id,
                kind,
                provider,
                operation,
                exclusive=exclusive,
            )
        except workflow_service.WorkflowServiceError as error:
            self._service_error(error)
            return
        self._send_json(job, HTTPStatus.ACCEPTED)

    def do_GET(self) -> None:
        if not self._request_context_is_valid():
            return
        path = unquote(urlsplit(self.path).path)
        try:
            if path in {"/api/health", "/api/v1/readiness"}:
                self._send_json(self.readiness)
                return
            if path == "/api/v1/health":
                health = dict(self.server_identity)
                health["active_job_count"] = sum(
                    1
                    for job in self.job_store.list()["jobs"]
                    if job["status"] in {"queued", "running"}
                )
                self._send_json(health)
                return
            if path == "/api/v1/workflows":
                self._send_json(workflow_service.list_resumable_workflows())
                return
            if path == "/api/v1/jobs":
                self._send_json(self.job_store.list())
                return
            job_match = JOB_ROUTE.fullmatch(path)
            if job_match:
                try:
                    job = self.job_store.get(job_match.group(1))
                except KeyError:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "job_not_found",
                        "That workflow activity is no longer available.",
                    )
                    return
                self._send_json(job)
                return
            workflow_match = WORKFLOW_ROUTE.fullmatch(path)
            if workflow_match:
                try:
                    workflow = workflow_service.inspect_workflow(
                        workflow_match.group(1)
                    )
                except workflow_service.WorkflowServiceError as error:
                    self._service_error(error)
                    return
                self._send_json(workflow)
                return
            events_match = TRANSITION_EVENTS_ROUTE.fullmatch(path)
            if events_match:
                self._send_json(
                    workflow_service.transition_events(events_match.group(1))
                )
                return
            receipt_match = RUN_RECEIPT_ROUTE.fullmatch(path)
            if receipt_match:
                book_id = receipt_match.group(1)
                if not book_id.startswith("book-"):
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "run_receipt_not_found",
                        "That book has no workflow run receipt.",
                    )
                    return
                self._send_json(
                    workflow_service.build_e2e_run_receipt(
                        book_id.removeprefix("book-")
                    )
                )
                return
            if path == "/api/v1/catalog":
                self._send_json(load_catalog(self.project_path))
                return
            provenance_match = PROVENANCE_ROUTE.fullmatch(path)
            if provenance_match:
                book_id = provenance_match.group(1)
                try:
                    payload = load_book_provenance(self.project_path, book_id)
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "book_not_found", "That finalized book is not on the shelf.")
                    return
                self._send_json(payload)
                return
            cover_match = COVER_ROUTE.fullmatch(path)
            if cover_match:
                book_id = cover_match.group(1)
                try:
                    body, content_type = load_cover_asset(
                        self.project_path,
                        book_id,
                    )
                except KeyError:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "book_not_found",
                        "That finalized book is not on the shelf.",
                    )
                    return
                self._send_bytes(body, content_type)
                return
            export_match = BOOK_EXPORT_ROUTE.fullmatch(path)
            if export_match:
                book_id, export_kind = export_match.groups()
                try:
                    payload = load_book(self.project_path, book_id)
                    provenance = load_book_provenance(self.project_path, book_id)
                except KeyError:
                    self._error(
                        HTTPStatus.NOT_FOUND,
                        "book_not_found",
                        "That finalized book is not on the shelf.",
                    )
                    return
                try:
                    receipt = workflow_service.build_e2e_run_receipt(
                        book_id.removeprefix("book-")
                    )
                except workflow_service.WorkflowServiceError:
                    receipt = {
                        "status": "unavailable",
                        "review_state": payload["assembly"].get("review_status"),
                        "workflow_policy_version": payload["assembly"].get(
                            "workflow_policy_version"
                        ),
                    }
                book_record = {
                    **payload["book"],
                    "verified_sha256": payload["verified_sha256"],
                    "assembly": payload["assembly"],
                }
                manifest = portable_export.evidence_manifest(
                    book=book_record,
                    provenance=provenance,
                    run_receipt=receipt,
                )
                title = str(payload["book"].get("title") or book_id)
                if export_kind == "markdown":
                    self._send_bytes(
                        payload["markdown"].encode("utf-8"),
                        "text/markdown; charset=utf-8",
                        filename=portable_export.safe_export_name(title, "md"),
                    )
                elif export_kind == "evidence-manifest":
                    self._send_bytes(
                        (
                            json.dumps(manifest, ensure_ascii=False, indent=2)
                            + "\n"
                        ).encode("utf-8"),
                        "application/json; charset=utf-8",
                        filename=portable_export.safe_export_name(
                            title,
                            "evidence.json",
                        ),
                    )
                else:
                    self._send_bytes(
                        portable_export.epub_bytes(
                            book_id=book_id,
                            title=title,
                            markdown=payload["markdown"],
                            evidence=manifest,
                        ),
                        "application/epub+zip",
                        filename=portable_export.safe_export_name(title, "epub"),
                    )
                return
            book_match = BOOK_ROUTE.fullmatch(path)
            if book_match:
                book_id = book_match.group(1)
                try:
                    payload = load_book(self.project_path, book_id)
                except KeyError:
                    self._error(HTTPStatus.NOT_FOUND, "book_not_found", "That finalized book is not on the shelf.")
                    return
                self._send_json(payload)
                return
            static_name = STATIC_FILES.get(path)
            if static_name:
                static_path = self.frontend_path / static_name
                body = static_path.read_bytes()
                content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
                if content_type.startswith("text/") or content_type == "application/javascript":
                    content_type += "; charset=utf-8"
                self._send_bytes(body, content_type)
                return
            self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested page does not exist.")
        except FrontendDataError as error:
            self._error(HTTPStatus.CONFLICT, "artifact_verification_failed", str(error))
        except OSError:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "frontend_file_unavailable", "A frontend file could not be read.")

    def do_POST(self) -> None:
        if not self._request_context_is_valid():
            return
        path = unquote(urlsplit(self.path).path)
        if path == "/api/v1/conversations":
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"provider", "research_mode"},
            ):
                return
            provider = payload["provider"]
            provider_state = self.readiness.get("providers", {}).get(provider)
            if not isinstance(provider_state, dict) or not provider_state.get(
                "full_workflow_ready"
            ):
                self._error(
                    HTTPStatus.CONFLICT,
                    "provider_not_ready",
                    "The selected provider has no verified full-workflow MCP configuration.",
                )
                return
            self._run_mutation(
                lambda: workflow_service.create_conversation(
                    provider,
                    payload["research_mode"],
                ),
                conversation_id=None,
                success_status=HTTPStatus.CREATED,
            )
            return

        provider_match = PROVIDER_ROUTE.fullmatch(path)
        if provider_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"provider", "expected_stage", "expected_revision"},
            ):
                return
            provider = payload["provider"]
            provider_state = self.readiness.get("providers", {}).get(provider)
            if not isinstance(provider_state, dict) or not provider_state.get(
                "full_workflow_ready"
            ):
                self._error(
                    HTTPStatus.CONFLICT,
                    "provider_not_ready",
                    "The selected provider has no verified full-workflow MCP configuration.",
                )
                return
            conversation_id = provider_match.group(1)
            self._run_mutation(
                lambda: workflow_service.switch_conversation_provider(
                    conversation_id,
                    provider,
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        async_turn_match = TURN_ASYNC_ROUTE.fullmatch(path)
        if async_turn_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"content", "expected_stage", "expected_revision"},
            ):
                return
            conversation_id = async_turn_match.group(1)
            try:
                provider = workflow_service.inspect_workflow(
                    conversation_id,
                    include_messages=False,
                ).get("provider")
            except workflow_service.WorkflowServiceError as error:
                self._service_error(error)
                return
            self._start_background_job(
                conversation_id,
                "conversation_turn",
                provider,
                lambda report: workflow_service.submit_conversation_turn(
                    conversation_id,
                    payload["content"],
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                    progress=report,
                ),
                exclusive=False,
            )
            return

        background_book_match = BACKGROUND_BOOK_ROUTE.fullmatch(path)
        if background_book_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"content", "expected_stage", "expected_revision"},
            ):
                return
            conversation_id = background_book_match.group(1)
            try:
                provider = workflow_service.inspect_workflow(
                    conversation_id,
                    include_messages=False,
                ).get("provider")
            except workflow_service.WorkflowServiceError as error:
                self._service_error(error)
                return
            self._start_background_job(
                conversation_id,
                "background_book",
                provider,
                lambda report: workflow_service.build_book_to_review(
                    conversation_id,
                    payload["content"],
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                    progress=report,
                ),
                exclusive=True,
            )
            return

        turn_match = TURN_ROUTE.fullmatch(path)
        if turn_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"content", "expected_stage", "expected_revision"},
            ):
                return
            conversation_id = turn_match.group(1)
            self._run_mutation(
                lambda: workflow_service.submit_conversation_turn(
                    conversation_id,
                    payload["content"],
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        finish_match = FINISH_ROUTE.fullmatch(path)
        if finish_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"expected_stage", "expected_revision"},
                {"research_mode", "finish_mode"},
            ):
                return
            conversation_id = finish_match.group(1)
            self._run_mutation(
                lambda: workflow_service.finish_conversation(
                    conversation_id,
                    payload.get("research_mode"),
                    finish_mode=payload.get("finish_mode", "standard"),
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        async_finish_match = FINISH_ASYNC_ROUTE.fullmatch(path)
        if async_finish_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"expected_stage", "expected_revision"},
                {"research_mode", "finish_mode"},
            ):
                return
            conversation_id = async_finish_match.group(1)
            try:
                provider = workflow_service.inspect_workflow(
                    conversation_id,
                    include_messages=False,
                ).get("provider")
            except workflow_service.WorkflowServiceError as error:
                self._service_error(error)
                return
            self._start_background_job(
                conversation_id,
                "finish_and_advance",
                provider,
                lambda report: workflow_service.finish_and_advance_workflow(
                    conversation_id,
                    payload.get("research_mode"),
                    finish_mode=payload.get("finish_mode", "standard"),
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                    progress=report,
                ),
                exclusive=True,
            )
            return

        decision_match = DECISION_ROUTE.fullmatch(path)
        if decision_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"decision", "expected_stage", "expected_revision"},
            ):
                return
            conversation_id = decision_match.group(1)
            self._run_mutation(
                lambda: workflow_service.submit_checkpoint_decision(
                    conversation_id,
                    payload["decision"],
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        source_mode_match = SOURCE_MODE_ROUTE.fullmatch(path)
        if source_mode_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"research_mode", "expected_stage", "expected_revision"},
            ):
                return
            conversation_id = source_mode_match.group(1)
            self._run_mutation(
                lambda: workflow_service.set_workflow_research_mode(
                    conversation_id,
                    payload["research_mode"],
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        question_mode_match = QUESTION_MODE_ROUTE.fullmatch(path)
        if question_mode_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"question_mode", "expected_stage", "expected_revision"},
            ):
                return
            conversation_id = question_mode_match.group(1)
            self._run_mutation(
                lambda: workflow_service.set_workflow_question_mode(
                    conversation_id,
                    payload["question_mode"],
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        advance_match = ADVANCE_ROUTE.fullmatch(path)
        if advance_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"expected_stage", "expected_revision"},
            ):
                return
            conversation_id = advance_match.group(1)
            self._run_mutation(
                lambda: workflow_service.advance_workflow_with_guardrails(
                    conversation_id,
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
                conversation_id=conversation_id,
            )
            return

        async_advance_match = ADVANCE_ASYNC_ROUTE.fullmatch(path)
        if async_advance_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"expected_stage", "expected_revision"},
            ):
                return
            conversation_id = async_advance_match.group(1)
            try:
                provider = workflow_service.inspect_workflow(
                    conversation_id,
                    include_messages=False,
                ).get("provider")
            except workflow_service.WorkflowServiceError as error:
                self._service_error(error)
                return
            self._start_background_job(
                conversation_id,
                "advance_workflow",
                provider,
                lambda report: workflow_service.advance_workflow_with_guardrails(
                    conversation_id,
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                    progress=report,
                ),
                exclusive=True,
            )
            return

        self._error(HTTPStatus.NOT_FOUND, "not_found", "The requested action does not exist.")


def make_server(
    project_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    check_runtime: bool = False,
) -> ThreadingHTTPServer:
    """Create a local-only server rooted at one repository."""
    allowed_hosts = local_bind_hosts()
    if host not in allowed_hosts:
        raise FrontendDataError(
            "The frontend workflow server must bind to loopback or this "
            "process's exact private WSL interface."
        )
    resolved_project = project_path.resolve()
    frontend_path = resolved_project / "frontend"
    if not frontend_path.is_dir():
        raise FrontendDataError("Frontend directory does not exist.")

    mutation_lanes = WorkflowMutationLanes()
    server_identity = build_server_identity(resolved_project)
    job_store = WorkflowJobStore(
        mutation_lanes,
        resolved_project,
        server_identity,
    )
    handler = type(
        "ConfiguredFrontendRequestHandler",
        (FrontendRequestHandler,),
        {
            "project_path": resolved_project,
            "frontend_path": frontend_path,
            "mutation_lanes": mutation_lanes,
            "job_store": job_store,
            "allowed_hosts": allowed_hosts,
            "readiness": build_frontend_readiness(
                resolved_project,
                check_runtime=check_runtime,
            ),
            "server_identity": server_identity,
        },
    )
    return ThreadingHTTPServer((host, port), handler)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local Alexandrian Library frontend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", dest="open_browser")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    project_path = Path(__file__).resolve().parent
    server = make_server(
        project_path,
        arguments.host,
        arguments.port,
        check_runtime=True,
    )
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}"
    print(f"Inquirion Library frontend: {url}", flush=True)
    if arguments.open_browser:
        threading.Timer(0.15, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
