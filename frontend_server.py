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
import re
import socket
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import environment_check
import workflow_service


FRONTEND_VERSION = "1.0.0"
API_VERSION = "v1"
MAX_JSON_REQUEST_BYTES = 1_048_576
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
BOOK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
CONVERSATION_ID = r"([A-Za-z0-9._-]+)"
WORKFLOW_ROUTE = re.compile(rf"^/api/v1/workflows/{CONVERSATION_ID}$")
TURN_ROUTE = re.compile(rf"^/api/v1/conversations/{CONVERSATION_ID}/turns$")
FINISH_ROUTE = re.compile(rf"^/api/v1/conversations/{CONVERSATION_ID}/finish$")
DECISION_ROUTE = re.compile(rf"^/api/v1/workflows/{CONVERSATION_ID}/decisions$")
ADVANCE_ROUTE = re.compile(rf"^/api/v1/workflows/{CONVERSATION_ID}/advance$")
BOOK_ROUTE = re.compile(r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})$")
PROVENANCE_ROUTE = re.compile(
    r"^/api/v1/books/([A-Za-z0-9][A-Za-z0-9._-]{0,199})/provenance$"
)
STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
    "/assets/branches-cover-holy-cyber-v3.png": (
        "assets/branches-cover-holy-cyber-v3.png"
    ),
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


class FrontendDataError(RuntimeError):
    """Reject an unsafe or inconsistent frontend projection."""


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
        seen_ids.add(book_id)
    assembly_ids = {path.parent.name for path in assembly_paths}
    if seen_ids != assembly_ids:
        raise FrontendDataError(
            "Bookshelf entries do not exactly match finalized assemblies."
        )
    return catalog


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
        "claim_checked": source.get("claim_checked"),
        "finding": source.get("finding"),
        "assessment": source.get("assessment"),
        "limitations": source.get("limitations"),
        "research_question": source.get("research_question"),
    }


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

    # Early V1 runs embedded some complete source records in immutable chapter
    # research packets before the dedicated receipt directory was introduced.
    # Use that structured record only when no standalone canonical receipt
    # exists. The final assembly's exact receipt ID remains authoritative.
    missing_ids = set(source_ids) - set(by_id)
    if missing_ids:
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
                if receipt_id in missing_ids:
                    by_id.setdefault(receipt_id, []).append(source)

    records = []
    for receipt_id in source_ids:
        matches = by_id.get(receipt_id, [])
        if len(matches) != 1:
            raise FrontendDataError(
                f"Source receipt {receipt_id} did not resolve exactly once."
            )
        records.append(_source_receipt_summary(matches[0]))
    return {
        "schema_version": "1.0",
        "book_id": book_id,
        "book_sha256": book["verified_sha256"],
        "assembly": book["assembly"],
        "source_count": len(records),
        "sources": records,
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
    mutation_lock: threading.Lock
    allowed_hosts: set[str]
    readiness: dict[str, Any]

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
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
        success_status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        if not self.mutation_lock.acquire(blocking=False):
            self._error(
                HTTPStatus.CONFLICT,
                "mutation_in_progress",
                "Another Library mutation is already active.",
            )
            return
        try:
            result = operation()
        except workflow_service.WorkflowServiceError as error:
            self._service_error(error)
        else:
            self._send_json(result, success_status)
        finally:
            self.mutation_lock.release()

    def do_GET(self) -> None:
        if not self._request_context_is_valid():
            return
        path = unquote(urlsplit(self.path).path)
        try:
            if path in {"/api/health", "/api/v1/readiness"}:
                self._send_json(self.readiness)
                return
            if path == "/api/v1/workflows":
                self._send_json(workflow_service.list_resumable_workflows())
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
                success_status=HTTPStatus.CREATED,
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
            )
            return

        finish_match = FINISH_ROUTE.fullmatch(path)
        if finish_match:
            payload = self._read_json_object()
            if payload is None or not self._require_fields(
                payload,
                {"expected_stage", "expected_revision"},
                {"research_mode"},
            ):
                return
            conversation_id = finish_match.group(1)
            self._run_mutation(
                lambda: workflow_service.finish_conversation(
                    conversation_id,
                    payload.get("research_mode"),
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
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
                lambda: workflow_service.advance_workflow(
                    conversation_id,
                    expected_stage=payload["expected_stage"],
                    expected_revision=payload["expected_revision"],
                ),
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

    handler = type(
        "ConfiguredFrontendRequestHandler",
        (FrontendRequestHandler,),
        {
            "project_path": resolved_project,
            "frontend_path": frontend_path,
            "mutation_lock": threading.Lock(),
            "allowed_hosts": allowed_hosts,
            "readiness": build_frontend_readiness(
                resolved_project,
                check_runtime=check_runtime,
            ),
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
