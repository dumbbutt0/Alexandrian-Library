import hashlib
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

import cover_design
import frontend_server


class FrontendServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.frontend_path = self.project_path / "frontend"
        self.frontend_path.mkdir(parents=True)
        for name in ("index.html", "styles.css", "app.js"):
            (self.frontend_path / name).write_text(name, encoding="utf-8")
        asset_path = (
            self.frontend_path
            / "assets"
            / "branches-cover-holy-cyber-v3.png"
        )
        asset_path.parent.mkdir(parents=True)
        asset_path.write_bytes(b"test-png")
        readable_asset_path = (
            self.frontend_path
            / "assets"
            / "readable-cover-forbidden-archive-v1.png"
        )
        readable_asset_path.write_bytes(b"test-readable-png")
        branches_display_path = (
            self.frontend_path
            / "assets"
            / "branches-cover-holy-cyber-v3-display.jpg"
        )
        branches_display_path.write_bytes(b"test-branches-jpg")
        readable_display_path = (
            self.frontend_path
            / "assets"
            / "readable-cover-forbidden-archive-v1-display.jpg"
        )
        readable_display_path.write_bytes(b"test-readable-jpg")
        inquiry_background_path = (
            self.frontend_path
            / "assets"
            / "inquiry-worktable-v1.png"
        )
        inquiry_background_path.write_bytes(b"test-inquiry-png")
        self.book_id = "book-test"
        self.book_path = (
            self.project_path / "data" / "books" / self.book_id / "book.md"
        )
        self.book_path.parent.mkdir(parents=True)
        self.book_path.write_text(
            (
                "# Verified\n\n"
                "The final text remains exact and available for cover verification.\n"
            ),
            encoding="utf-8",
        )
        (self.book_path.parent / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "book_id": self.book_id,
                    "working_title": "A Verified Book",
                    "central_question": "Can the reader verify its source?",
                    "book_mode": "design_oriented",
                }
            ) + "\n",
            encoding="utf-8",
        )
        self.assembly_path = self.book_path.parent / "assembly.json"
        self.write_assembly()
        self.write_catalog()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_local_bind_hosts_adds_only_the_routed_private_interface(self) -> None:
        probe = Mock()
        probe.getsockname.return_value = ("172.20.30.241", 51234)
        with patch("frontend_server.socket.socket", return_value=probe):
            hosts = frontend_server.local_bind_hosts()

        self.assertEqual(
            hosts,
            frontend_server.LOOPBACK_HOSTS | {"172.20.30.241"},
        )
        probe.connect.assert_called_once_with(("192.0.2.1", 9))
        probe.close.assert_called_once_with()

    def write_catalog(self, **book_changes: object) -> None:
        shutil.rmtree(
            cover_design.cover_directory(self.project_path, self.book_id),
            ignore_errors=True,
        )
        design = cover_design.create_default_design(
            self.project_path,
            self.book_id,
            designer_host="frontend-test",
        )
        asset = design["asset"]
        book = {
            "work_id": self.book_id,
            "book_id": self.book_id,
            "edition": 1,
            "assembly_id": "assembly-test",
            "artifact_status": "final",
            "shelf_status": "internal_smoke_test",
            "title": "A Verified Book",
            "central_question": "Can the reader verify its source?",
            "book_mode": "design_oriented",
            "book_path": self.book_path.relative_to(self.project_path).as_posix(),
            "book_sha256": hashlib.sha256(self.book_path.read_bytes()).hexdigest(),
            "assembly_path": f"data/books/{self.book_id}/assembly.json",
            "assembly_sha256": hashlib.sha256(
                self.assembly_path.read_bytes()
            ).hexdigest(),
            "finalized_at": "2026-01-01T00:00:00+00:00",
            "chapter_count": 1,
            "chapters": [],
            "source_count": 0,
            "review_ids": ["review-test"],
            "knowledge_node_ids": [],
            "cover": {
                "design_path": (
                    cover_design.design_path(self.project_path, self.book_id)
                    .relative_to(self.project_path)
                    .as_posix()
                ),
                "design_sha256": design["design_sha256"],
                "quote": design["quote"],
                "quote_location": design["quote_location"],
                "concept": design["concept"],
                "visual_motifs": design["visual_motifs"],
                "palette": design["palette"],
                "asset_status": asset["status"],
                "asset_path": asset["path"],
                "asset_sha256": asset["sha256"],
                "asset_mime_type": asset["mime_type"],
                "asset_url": f"/api/v1/books/{self.book_id}/cover",
            },
        }
        book.update(book_changes)
        catalog = {
            "schema_version": "1.0",
            "generated_at": "2026-01-01T00:00:00+00:00",
            "book_count": 1,
            "books": [book],
        }
        catalog_path = self.project_path / "data" / "bookshelf" / "catalog.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_assembly(self, source_receipt_ids: list[str] | None = None) -> None:
        source_receipt_ids = source_receipt_ids or []
        self.assembly_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "assembly_id": "assembly-test",
                    "book_id": self.book_id,
                    "assembled_at": "2026-01-01T00:00:00+00:00",
                    "lifecycle_status": "final",
                    "book_sha256": hashlib.sha256(
                        self.book_path.read_bytes()
                    ).hexdigest(),
                    "source_count": len(source_receipt_ids),
                    "source_receipt_ids": source_receipt_ids,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def test_load_catalog_accepts_one_finalized_book(self) -> None:
        catalog = frontend_server.load_catalog(self.project_path)
        self.assertEqual(catalog["book_count"], 1)
        self.assertEqual(catalog["books"][0]["book_id"], self.book_id)

    def test_load_catalog_rejects_count_mismatch(self) -> None:
        catalog_path = self.project_path / "data" / "bookshelf" / "catalog.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog["book_count"] = 2
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
        with self.assertRaisesRegex(
            frontend_server.FrontendDataError,
            "book count",
        ):
            frontend_server.load_catalog(self.project_path)

    def test_load_book_rejects_hash_drift(self) -> None:
        self.book_path.write_text("# Changed\n", encoding="utf-8")
        with self.assertRaisesRegex(
            frontend_server.FrontendDataError,
            "hash does not match",
        ):
            frontend_server.load_book(self.project_path, self.book_id)

    def test_load_book_rejects_path_escape(self) -> None:
        outside_path = self.project_path / "outside.md"
        outside_path.write_text("# Outside\n", encoding="utf-8")
        self.write_catalog(
            book_path="outside.md",
            book_sha256=hashlib.sha256(outside_path.read_bytes()).hexdigest(),
        )
        with self.assertRaisesRegex(
            frontend_server.FrontendDataError,
            "escapes",
        ):
            frontend_server.load_book(self.project_path, self.book_id)

    def test_empty_clean_clone_has_a_valid_empty_bookshelf(self) -> None:
        empty_project = self.project_path / "empty"
        empty_project.mkdir()
        catalog = frontend_server.load_catalog(empty_project)
        self.assertEqual(catalog["book_count"], 0)
        self.assertEqual(catalog["projection_status"], "empty_bookshelf")

    def test_catalog_missing_with_final_assembly_is_verification_failure(self) -> None:
        catalog_path = self.project_path / "data" / "bookshelf" / "catalog.json"
        catalog_path.unlink()
        with self.assertRaisesRegex(
            frontend_server.FrontendDataError,
            "catalog is missing",
        ):
            frontend_server.load_catalog(self.project_path)

    def test_book_provenance_resolves_exact_source_receipts(self) -> None:
        receipt_id = "chapter-source-test"
        self.write_assembly([receipt_id])
        self.write_catalog()
        receipt_path = (
            self.book_path.parent
            / "research"
            / "CH1"
            / "source_receipts"
            / "A1"
            / f"{receipt_id}.json"
        )
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "receipt": {
                        "receipt_id": receipt_id,
                        "title": "Primary source",
                        "domain": "example.org",
                        "final_url": "https://example.org/source",
                        "content_sha256": "b" * 64,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

        provenance = frontend_server.load_book_provenance(
            self.project_path,
            self.book_id,
        )

        self.assertEqual(provenance["source_count"], 1)
        self.assertEqual(provenance["sources"][0]["receipt_id"], receipt_id)
        self.assertNotIn("content", provenance["sources"][0])

    def test_book_provenance_supports_immutable_legacy_packet_source(self) -> None:
        receipt_id = "chapter-source-legacy"
        self.write_assembly([receipt_id])
        self.write_catalog()
        packet_path = self.book_path.parent / "research" / "CH1" / "R1.json"
        packet_path.parent.mkdir(parents=True)
        packet_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "research": {},
                    "sources": [
                        {
                            "receipt_id": receipt_id,
                            "title": "Legacy packet source",
                            "domain": "example.org",
                            "final_url": "https://example.org/legacy",
                            "source_content_sha256": "c" * 64,
                            "source_document_sha256": "d" * 64,
                            "evidence_excerpt": "A checked legacy passage.",
                            "evidence_locator": {
                                "locator_id": "locator-" + "e" * 24,
                                "page_number": 4,
                                "block_id": "B000004",
                            },
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        provenance = frontend_server.load_book_provenance(
            self.project_path,
            self.book_id,
        )

        self.assertEqual(provenance["sources"][0]["receipt_id"], receipt_id)
        self.assertEqual(
            provenance["sources"][0]["title"],
            "Legacy packet source",
        )
        self.assertEqual(
            provenance["sources"][0]["evidence_locator"]["page_number"],
            4,
        )
        self.assertEqual(
            provenance["sources"][0]["evidence_excerpt"],
            "A checked legacy passage.",
        )

    def test_book_provenance_merges_fetch_receipt_with_evidence_packet(self) -> None:
        receipt_id = "chapter-source-merged"
        self.write_assembly([receipt_id])
        self.write_catalog()
        receipt_path = (
            self.book_path.parent
            / "research"
            / "CH1"
            / "source_receipts"
            / "A1"
            / f"{receipt_id}.json"
        )
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(
            json.dumps(
                {
                    "receipt": {
                        "receipt_id": receipt_id,
                        "title": "Bare fetch title",
                        "domain": "example.org",
                        "final_url": "https://example.org/source",
                        "content_sha256": "b" * 64,
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        packet_path = self.book_path.parent / "research" / "CH1" / "R1.json"
        packet_path.write_text(
            json.dumps(
                {
                    "sources": [
                        {
                            "receipt_id": receipt_id,
                            "domain": "example.org",
                            "final_url": "https://example.org/source",
                            "source_content_sha256": "b" * 64,
                            "evidence_excerpt": "The preserved exact passage.",
                            "evidence_location": "Section 2",
                            "limitations": "One bounded source.",
                        }
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )

        provenance = frontend_server.load_book_provenance(
            self.project_path,
            self.book_id,
        )

        self.assertEqual(provenance["source_count"], 1)
        self.assertEqual(
            provenance["sources"][0]["evidence_excerpt"],
            "The preserved exact passage.",
        )
        self.assertEqual(
            provenance["sources"][0]["evidence_location"],
            "Section 2",
        )

    def test_book_provenance_publishes_hash_bound_unverified_evidence(self) -> None:
        review_path = self.project_path / "data" / "reviews" / "test.json"
        review_path.parent.mkdir(parents=True)
        review_path.write_text(
            json.dumps(
                {
                    "review_id": "review-test",
                    "conversation_id": "test",
                    "review": {
                        "research_trail": [
                            {
                                "query_or_question": "Does the source establish the claim?",
                                "candidate_source_url": "https://example.org/blocked",
                                "decision": "unavailable",
                                "reason": "The authorized fetch returned HTTP 403.",
                                "transcript_references": ["T1"],
                            }
                        ],
                        "evidence_gaps": [
                            {
                                "text": "The claim remains unchecked.",
                                "disposition": "bounded_unresolved",
                                "book_blocking": False,
                                "closure_reason": "The edition does not rely on it.",
                                "destination": None,
                                "transcript_references": ["T1"],
                            }
                        ],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        candidate_path = self.book_path.parent / "candidate_assembly.json"
        candidate_path.write_text(
            json.dumps(
                {
                    "inputs": [
                        {
                            "role": "research_review",
                            "path": review_path.relative_to(self.project_path).as_posix(),
                            "sha256": hashlib.sha256(review_path.read_bytes()).hexdigest(),
                        }
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assembly = json.loads(self.assembly_path.read_text(encoding="utf-8"))
        assembly.update(
            {
                "candidate_assembly_path": candidate_path.relative_to(
                    self.project_path
                ).as_posix(),
                "candidate_assembly_sha256": hashlib.sha256(
                    candidate_path.read_bytes()
                ).hexdigest(),
            }
        )
        self.assembly_path.write_text(
            json.dumps(assembly, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.write_catalog()

        provenance = frontend_server.load_book_provenance(
            self.project_path,
            self.book_id,
        )

        self.assertEqual(provenance["source_count"], 0)
        self.assertEqual(provenance["evidence_status_count"], 1)
        self.assertEqual(
            provenance["evidence_statuses"][0]["status"],
            "unavailable",
        )
        self.assertFalse(provenance["evidence_statuses"][0]["verified"])
        self.assertEqual(provenance["evidence_gap_count"], 1)

    def test_http_endpoints_serve_only_versioned_verified_data(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        try:
            with urllib.request.urlopen(f"{base_url}/api/v1/health") as response:
                health = json.loads(response.read())
            self.assertEqual(health["status"], "available")
            self.assertEqual(len(health["build_sha256"]), 64)
            self.assertEqual(health["active_job_count"], 0)

            with urllib.request.urlopen(f"{base_url}/api/v1/catalog") as response:
                catalog = json.loads(response.read())
            self.assertEqual(catalog["book_count"], 1)

            with urllib.request.urlopen(
                f"{base_url}/api/v1/books/{self.book_id}"
            ) as response:
                book = json.loads(response.read())
            self.assertIn("The final text remains exact", book["markdown"])

            with urllib.request.urlopen(
                f"{base_url}/api/v1/books/{self.book_id}/exports/markdown"
            ) as response:
                markdown_export = response.read()
                markdown_disposition = response.headers["Content-Disposition"]
            self.assertEqual(markdown_export, self.book_path.read_bytes())
            self.assertIn("attachment;", markdown_disposition)

            with urllib.request.urlopen(
                f"{base_url}/api/v1/books/{self.book_id}/exports/evidence-manifest"
            ) as response:
                evidence_export = json.loads(response.read())
            self.assertEqual(
                evidence_export["schema_version"],
                "alexandrian-portable-evidence.v1",
            )
            self.assertEqual(evidence_export["book"]["book_id"], self.book_id)

            with urllib.request.urlopen(
                f"{base_url}/api/v1/books/{self.book_id}/exports/epub"
            ) as response:
                epub_export = response.read()
                epub_type = response.headers["Content-Type"]
            self.assertTrue(epub_export.startswith(b"PK"))
            self.assertEqual(epub_type, "application/epub+zip")

            with urllib.request.urlopen(
                f"{base_url}/api/v1/books/{self.book_id}/cover"
            ) as response:
                generated_cover = response.read()
                generated_cover_type = response.headers["Content-Type"]
            self.assertIn(b"<svg", generated_cover)
            self.assertEqual(generated_cover_type, "image/svg+xml")

            with urllib.request.urlopen(
                f"{base_url}/assets/branches-cover-holy-cyber-v3.png"
            ) as response:
                cover = response.read()
                content_type = response.headers["Content-Type"]
            self.assertEqual(cover, b"test-png")
            self.assertEqual(content_type, "image/png")

            with urllib.request.urlopen(
                f"{base_url}/assets/readable-cover-forbidden-archive-v1.png"
            ) as response:
                readable_cover = response.read()
                readable_content_type = response.headers["Content-Type"]
            self.assertEqual(readable_cover, b"test-readable-png")
            self.assertEqual(readable_content_type, "image/png")

            with urllib.request.urlopen(
                f"{base_url}/assets/branches-cover-holy-cyber-v3-display.jpg"
            ) as response:
                branches_display = response.read()
                branches_display_type = response.headers["Content-Type"]
            self.assertEqual(branches_display, b"test-branches-jpg")
            self.assertEqual(branches_display_type, "image/jpeg")

            with urllib.request.urlopen(
                f"{base_url}/assets/readable-cover-forbidden-archive-v1-display.jpg"
            ) as response:
                readable_display = response.read()
                readable_display_type = response.headers["Content-Type"]
            self.assertEqual(readable_display, b"test-readable-jpg")
            self.assertEqual(readable_display_type, "image/jpeg")

            with urllib.request.urlopen(
                f"{base_url}/assets/inquiry-worktable-v1.png"
            ) as response:
                inquiry_background = response.read()
                inquiry_background_type = response.headers["Content-Type"]
            self.assertEqual(inquiry_background, b"test-inquiry-png")
            self.assertEqual(inquiry_background_type, "image/png")

            request = urllib.request.Request(
                f"{base_url}/api/inquiries",
                data=b'{"inquiry":"Why?"}',
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
            self.assertEqual(raised.exception.code, 404)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_workflow_api_is_versioned_and_rejects_stale_actions(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        server.RequestHandlerClass.readiness["providers"]["codex"] = {
            "installed": True,
            "full_workflow_ready": True,
            "detail": "Test provider ready.",
        }
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        created = {
            "schema_version": "1.0",
            "conversation_id": "conversation-test",
            "stage": "conversation_active",
            "revision": "a" * 64,
        }
        try:
            with patch.object(
                frontend_server.workflow_service,
                "list_resumable_workflows",
                return_value={
                    "schema_version": "1.0",
                    "workflow_count": 0,
                    "workflows": [],
                },
            ), patch.object(
                frontend_server.workflow_service,
                "create_conversation",
                return_value=created,
            ) as create, patch.object(
                frontend_server.workflow_service,
                "submit_conversation_turn",
                side_effect=frontend_server.workflow_service.WorkflowConflictError(
                    "stale_workflow_revision",
                    "Canonical workflow artifacts changed.",
                ),
            ) as submit:
                with urllib.request.urlopen(
                    f"{base_url}/api/v1/workflows"
                ) as response:
                    workflows = json.loads(response.read())
                self.assertEqual(workflows["workflow_count"], 0)

                request = urllib.request.Request(
                    f"{base_url}/api/v1/conversations",
                    data=json.dumps(
                        {"provider": "codex", "research_mode": "safe"}
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request) as response:
                    create_result = json.loads(response.read())
                    create_status = response.status
                self.assertEqual(create_status, 201)
                self.assertEqual(
                    create_result["conversation_id"],
                    "conversation-test",
                )
                create.assert_called_once_with("codex", "safe")

                request = urllib.request.Request(
                    f"{base_url}/api/v1/conversations/conversation-test/turns",
                    data=json.dumps(
                        {
                            "content": "Why?",
                            "expected_stage": "conversation_active",
                            "expected_revision": "a" * 64,
                        }
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request)
                self.assertEqual(raised.exception.code, 409)
                conflict = json.loads(raised.exception.read())
                self.assertEqual(
                    conflict["error"],
                    "stale_workflow_revision",
                )
                self.assertEqual(submit.call_count, 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_workflow_api_enforces_loopback_origin_and_exact_json(self) -> None:
        with self.assertRaisesRegex(
            frontend_server.FrontendDataError,
            "loopback",
        ):
            frontend_server.make_server(
                self.project_path,
                host="0.0.0.0",
                port=0,
            )

        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        try:
            hostile_origin = urllib.request.Request(
                f"{base_url}/api/v1/conversations",
                data=b'{}',
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://evil.example",
                },
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(hostile_origin)
            self.assertEqual(raised.exception.code, 403)
            self.assertEqual(
                json.loads(raised.exception.read())["error"],
                "invalid_origin",
            )

            wrong_type = urllib.request.Request(
                f"{base_url}/api/v1/conversations",
                data=b'{}',
                method="POST",
                headers={"Content-Type": "text/plain"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(wrong_type)
            self.assertEqual(raised.exception.code, 415)

            unknown_field = urllib.request.Request(
                f"{base_url}/api/v1/conversations",
                data=json.dumps(
                    {
                        "provider": "codex",
                        "research_mode": "safe",
                        "program_path": "/tmp/not-allowed",
                    }
                ).encode(),
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(unknown_field)
            self.assertEqual(raised.exception.code, 400)
            self.assertEqual(
                json.loads(raised.exception.read())["error"],
                "invalid_request_fields",
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_source_mode_route_binds_expected_workflow_revision(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        expected = {
            "schema_version": "1.0",
            "status": "research_mode_updated",
            "conversation_id": "conversation-test",
            "research_mode": "auto_approve",
            "pending_sources_resolved": 1,
            "workflow": {"conversation_id": "conversation-test"},
        }
        try:
            with patch.object(
                frontend_server.workflow_service,
                "set_workflow_research_mode",
                return_value=expected,
            ) as set_mode:
                request = urllib.request.Request(
                    f"http://{host}:{port}/api/v1/workflows/"
                    "conversation-test/source-mode",
                    data=json.dumps(
                        {
                            "research_mode": "auto_approve",
                            "expected_stage": "research_review",
                            "expected_revision": "a" * 64,
                        }
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request) as response:
                    result = json.loads(response.read())

            self.assertEqual(result, expected)
            set_mode.assert_called_once_with(
                "conversation-test",
                "auto_approve",
                expected_stage="research_review",
                expected_revision="a" * 64,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_question_mode_route_binds_expected_workflow_revision(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        expected = {
            "schema_version": "1.0",
            "status": "question_mode_updated",
            "conversation_id": "conversation-test",
            "question_mode": "auto_defer",
            "pending_questions_resolved": 2,
            "workflow": {"conversation_id": "conversation-test"},
        }
        try:
            with patch.object(
                frontend_server.workflow_service,
                "set_workflow_question_mode",
                return_value=expected,
            ) as set_mode:
                request = urllib.request.Request(
                    f"http://{host}:{port}/api/v1/workflows/"
                    "conversation-test/question-mode",
                    data=json.dumps(
                        {
                            "question_mode": "auto_defer",
                            "expected_stage": "chapter_development",
                            "expected_revision": "a" * 64,
                        }
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request) as response:
                    result = json.loads(response.read())

            self.assertEqual(result, expected)
            set_mode.assert_called_once_with(
                "conversation-test",
                "auto_defer",
                expected_stage="chapter_development",
                expected_revision="a" * 64,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_finish_route_forwards_guardrails_authority(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        expected = {
            "schema_version": "1.0",
            "conversation_id": "conversation-test",
            "stage": "research_review",
        }
        try:
            with patch.object(
                frontend_server.workflow_service,
                "finish_conversation",
                return_value=expected,
            ) as finish:
                request = urllib.request.Request(
                    f"http://{host}:{port}/api/v1/conversations/"
                    "conversation-test/finish",
                    data=json.dumps(
                        {
                            "finish_mode": "guardrails",
                            "expected_stage": "conversation_active",
                            "expected_revision": "a" * 64,
                        }
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(request) as response:
                    result = json.loads(response.read())

            self.assertEqual(result, expected)
            finish.assert_called_once_with(
                "conversation-test",
                None,
                finish_mode="guardrails",
                expected_stage="conversation_active",
                expected_revision="a" * 64,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_conversation_rejects_provider_without_workflow_readiness(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        request = urllib.request.Request(
            f"http://{host}:{port}/api/v1/conversations",
            data=json.dumps(
                {"provider": "codex", "research_mode": "safe"}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with patch.object(
                frontend_server.workflow_service,
                "create_conversation",
            ) as create:
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request)
                self.assertEqual(raised.exception.code, 409)
                self.assertEqual(
                    json.loads(raised.exception.read())["error"],
                    "provider_not_ready",
                )
                create.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_workflow_api_serializes_only_the_same_inquiry(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        held_lock = server.RequestHandlerClass.mutation_lanes.lock_for(
            "conversation-test"
        )
        held_lock.acquire()
        try:
            with patch.object(
                frontend_server.workflow_service,
                "finish_conversation",
            ) as finish:
                request = urllib.request.Request(
                    f"{base_url}/api/v1/conversations/conversation-test/finish",
                    data=json.dumps(
                        {
                            "expected_stage": "conversation_active",
                            "expected_revision": "a" * 64,
                        }
                    ).encode(),
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as raised:
                    urllib.request.urlopen(request)
                self.assertEqual(raised.exception.code, 409)
                self.assertEqual(
                    json.loads(raised.exception.read())["error"],
                    "mutation_in_progress",
                )
                finish.assert_not_called()
        finally:
            held_lock.release()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_background_jobs_allow_separate_inquiries_to_keep_moving(self) -> None:
        store = frontend_server.WorkflowJobStore(
            frontend_server.WorkflowMutationLanes()
        )
        first_started = threading.Event()
        release_first = threading.Event()

        def slow_operation(report):
            report(24, "Provider running")
            first_started.set()
            release_first.wait(timeout=2)
            return {"conversation_id": "conversation-one"}

        first = store.start(
            "conversation-one",
            "conversation_turn",
            "claude",
            slow_operation,
            exclusive=True,
        )
        self.assertTrue(first_started.wait(timeout=1))
        second = store.start(
            "conversation-two",
            "conversation_turn",
            "codex",
            lambda report: {"conversation_id": "conversation-two"},
            exclusive=True,
        )

        for _ in range(100):
            if store.get(second["job_id"])["status"] == "completed":
                break
            threading.Event().wait(0.01)
        self.assertEqual(store.get(second["job_id"])["status"], "completed")
        self.assertIn(store.get(first["job_id"])["status"], {"queued", "running"})

        with self.assertRaises(
            frontend_server.workflow_service.WorkflowConflictError
        ) as caught:
            store.start(
                "conversation-one",
                "conversation_turn",
                "codex",
                lambda report: {},
                exclusive=False,
            )
        self.assertEqual(caught.exception.code, "conversation_job_active")
        release_first.set()


class FrontendPresentationContractTests(unittest.TestCase):
    def test_worktable_uses_the_versioned_workflow_api_and_backend_actions(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        app = (project_path / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (project_path / "frontend" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )

        for element_id in (
            "workflow-list",
            "new-inquiry-panel",
            "active-workflow-panel",
            "conversation-transcript",
            "conversation-composer",
            "finish-guardrails-note",
            "workflow-action-zone",
            "workflow-progress",
            "workflow-transition-fill",
            "run-receipt-toggle",
            "run-receipt-panel",
            "agent-switcher",
            "workflow-filter-published",
            "workflow-filter-active",
            "finish-readiness",
            "workflow-timeline-panel",
            "reader-export-menu",
            "sample-inquiry-button",
        ):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("Build my book", html)
        self.assertIn("Continue inquiry", html)
        self.assertIn('aria-describedby="finish-guardrails-note"', html)
        self.assertIn("each unknown source URL once for this inquiry", html)
        for endpoint in (
            '"/api/v1/readiness"',
            '"/api/v1/catalog"',
            '"/api/v1/workflows"',
            '"/api/v1/conversations"',
            "/decisions`",
            "/turns/async`",
            "/build/async`",
            "/finish/async`",
            "/advance/async`",
            "/provider`",
            "/source-mode`",
            "/question-mode`",
            '"/api/v1/jobs"',
            "/run-receipt`",
        ):
            self.assertIn(endpoint, app)
        self.assertNotIn('fetchJson("/api/inquiries"', app)
        self.assertNotIn("workflowHold", app)
        self.assertIn("state.workflow.pending_action || {}", app)
        self.assertIn("DEMO_VISUAL_REFERENCES", app)
        self.assertIn("full_workflow_ready", app)
        self.assertIn("expected_stage: state.workflow.stage", app)
        self.assertIn("expected_revision: state.workflow.revision", app)
        self.assertIn("function renderReviewCheckpoint(action)", app)
        self.assertIn("function renderSourceCheckpoint(action)", app)
        self.assertIn("function renderChapterCheckpoint(action)", app)
        self.assertIn("function chapterQuickChoices(question)", app)
        self.assertIn('normalizedQuestion.includes("technical fortress")', app)
        self.assertIn('normalizedQuestion.includes("layered resilience")', app)
        self.assertIn("Suggested answers — choose one, then edit if needed", app)
        self.assertIn("Suggested decision basis — review before submitting", app)
        self.assertIn("Use bounded-repair authorization", app)
        self.assertIn("exactly one correction of the audited", app)
        self.assertIn('button.setAttribute("aria-pressed", "false")', app)
        self.assertIn(".chapter-quick-choices-label", styles)
        self.assertIn("function renderEditorialCheckpoint(action)", app)
        self.assertIn('finish_mode: "guardrails"', app)
        self.assertIn("Finish guardrails paused", app)
        self.assertIn("Build my book", html)
        self.assertIn("function renderFinishReadiness(workflow)", app)
        self.assertIn("function renderWorkflowTimeline(workflow)", app)
        self.assertIn("GUIDED_INQUIRY_EXAMPLE", app)
        self.assertIn("exportEpub.href = `${exportBase}/epub`;", app)
        self.assertIn('id="export-cover-card"', html)
        self.assertIn('id="export-quote-card"', html)
        self.assertIn("function exportShareCard(kind)", app)
        self.assertIn('canvas.toDataURL("image/png")', app)
        self.assertIn("Why trust this?", html)
        self.assertIn("Build record", html)
        self.assertIn(".finish-readiness", styles)
        self.assertIn("container-type: inline-size", styles)
        self.assertIn("@container (max-width: 680px)", styles)
        self.assertIn(
            ".finish-check:last-child:nth-child(odd)",
            styles,
        )
        self.assertIn(".workflow-timeline", styles)
        self.assertIn(".workflow-list-item.is-selected", styles)
        self.assertIn(".workflow-list-item.is-building-book", styles)
        self.assertIn("workflow.book_creation", app)
        self.assertIn("Progress safely preserved", app)
        self.assertIn(".workflow-action-card", styles)
        self.assertIn(".workflow-source-mode-toggle", styles)
        self.assertIn(
            ".worktable-grid {\n    grid-template-columns: minmax(0, 1fr);",
            styles,
        )
        self.assertIn("Auto-approve sources", app)
        self.assertIn('button.setAttribute("aria-pressed"', app)
        self.assertIn("function toggleWorkflowSourceMode()", app)
        self.assertIn("Auto-defer questions", app)
        self.assertIn("function toggleWorkflowQuestionMode()", app)
        self.assertIn(".workflow-question-mode-toggle", styles)
        self.assertIn(".workflow-progress-track", styles)
        self.assertIn(".source-choice.is-reject", styles)
        self.assertIn("function estimatedJobProgress(", app)
        self.assertIn("function normalizedWorkflowProgress(workflow)", app)
        self.assertIn("workflow?.progress?.percent", app)
        self.assertIn("function showRunReceipt()", app)
        self.assertIn("job.transition_progress", app)
        self.assertIn("function syncSelectedWorkflowDuringJob", app)
        self.assertIn("canonical.stage !== state.workflow.stage", app)
        self.assertIn('name="inquiry-run-mode"', html)
        self.assertIn("background_book", app)
        self.assertIn(".workflow-ready-dot", styles)
        self.assertNotIn('shelfViewport.addEventListener("wheel"', app)
        self.assertIn("Turn a question into a book.", html)
        self.assertIn("Begin my book", html)
        self.assertIn('data-inquiry-starter="understand"', html)
        self.assertIn('data-inquiry-starter="belief"', html)
        self.assertIn('data-inquiry-starter="material"', html)
        self.assertIn('class="advanced-inquiry-controls"', html)
        self.assertIn('id="book-seed"', html)
        self.assertIn('id="first-visit-example"', html)
        self.assertIn("function provisionalBookTitle(question)", app)
        self.assertIn("function renderBookSeed(workflow)", app)
        self.assertIn("firstVisitExample.hidden", app)
        self.assertIn("isRunning || bookCreationActive", app)
        self.assertIn(".inquiry-paths", styles)
        self.assertIn(".book-seed", styles)
        self.assertIn(".first-visit-example", styles)

    def test_each_shelf_book_is_one_complete_persistent_3d_object(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )
        app = (project_path / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (project_path / "frontend" / "index.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<template id="shelf-book-template">', html)
        self.assertNotIn('id="display-volume"', html)
        self.assertNotIn('id="book-spine-template"', html)
        for face in (
            "book-cover-face",
            "book-back-board",
            "book-spine-panel",
            "book-page-block",
            "book-head-edge",
            "book-tail-edge",
        ):
            self.assertIn(f'class="{face}"', html)
        self.assertIn("const shelfBookTemplate", app)
        self.assertIn(
            "shelfBookTemplate.content.firstElementChild.cloneNode(true)",
            app,
        )
        self.assertNotIn("displayVolume", app)
        self.assertNotIn("BROWSE_MOTION_PHASES", app)
        self.assertNotIn("exchangePresentedBook", app)
        self.assertIn("--shelf-yaw: 88deg;", styles)
        self.assertIn("--focus-yaw: 7deg;", styles)
        self.assertIn(
            "transform: translateZ(var(--book-back-depth)) rotateY(-90deg);",
            styles,
        )
        self.assertIn(
            ".shelf-book.is-focused .book-rotor {",
            styles,
        )
        self.assertIn(
            "rotateY(calc(var(--shelf-yaw) + var(--spin-y)))",
            styles,
        )
        self.assertIn(
            "rotateY(calc(var(--focus-yaw) + var(--spin-y)))",
            styles,
        )
        self.assertIn('<span class="book-spine-lip"></span>', html)
        self.assertIn(
            '<span class="display-spine-imprint">INQUIRION LABS</span>',
            html,
        )
        self.assertIn(
            'bookObject.style.setProperty("--book-depth", `${bookDepth}px`);',
            app,
        )
        self.assertIn('bookObject.classList.add("is-focused");', app)
        self.assertIn('bookObject.classList.remove("is-focused");', app)

    def test_arrows_only_browse_and_click_focuses_the_same_book_object(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )
        app = (project_path / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )

        move_start = app.index("function moveShelf(direction)")
        move_end = app.index("function renderShelf(catalog)", move_start)
        move_body = app[move_start:move_end]
        self.assertIn("selectVolumeAt(currentIndex + direction);", move_body)
        self.assertNotIn("setInspectionMode(true)", move_body)
        self.assertIn(
            "selectVolumeAt(index, { focusAfter: true });",
            app,
        )
        self.assertIn(
            "positionBookForFocus(bookObject);",
            app,
        )
        self.assertIn(
            'centerBookInShelf(bookObject, focusAfter ? "auto" : "smooth");',
            app,
        )
        self.assertIn(
            "translate3d(var(--focus-x), var(--focus-y), 150px)",
            styles,
        )
        self.assertIn(
            ".shelf-room.has-focused-book .shelf-book:not(.is-focused)",
            styles,
        )
        self.assertIn("Arrows browse · click a spine to focus", (project_path / "frontend" / "index.html").read_text(encoding="utf-8"))

    def test_shelf_centers_books_until_their_measured_width_overflows(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )
        app = (project_path / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("function updateShelfGeometry()", app)
        self.assertIn(
            'shelf.classList.toggle("books-overflow", overflows);',
            app,
        )
        self.assertIn("contentWidth + edgeSpace * 2 > viewportWidth", app)
        self.assertIn(".bookshelf.books-overflow {", styles)
        self.assertIn("justify-content: center;", styles)
        self.assertIn(
            "padding-right: var(--shelf-edge-space",
            styles,
        )

    def test_all_exposed_page_surfaces_share_layered_paper_materials(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )

        self.assertIn(".book-rotor::after {", styles)
        self.assertIn(".book-head-edge,\n.book-tail-edge {", styles)
        self.assertIn("background-color: #d8cfbb;", styles)
        self.assertIn("background-blend-mode: multiply", styles)
        self.assertIn("radial-gradient(circle at 17% 13%", styles)
        self.assertIn("radial-gradient(circle at 13% 31%", styles)
        self.assertIn(
            "transform: translateZ(calc(var(--book-half-depth) - 3px));",
            styles,
        )

    def test_reader_preserves_evidence_tables_and_presentation_state(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        app = (project_path / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )
        html = (project_path / "frontend" / "index.html").read_text(
            encoding="utf-8"
        )
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("[Evidence record]", app)
        self.assertIn("function appendEvidenceText", app)
        self.assertIn("function evidenceNumber", app)
        self.assertIn('replace(/^`+|`+$/g, "")', app)
        self.assertIn('`[${number}]`', app)
        self.assertIn("Exact supporting passage", app)
        self.assertIn("Unverified evidence trail", app)
        self.assertIn("These records are public provenance, not verified citations.", app)
        self.assertIn("Define the first bounded study", app)
        self.assertIn("Set the human decision boundary", app)
        self.assertIn("Keep all open and continue", app)
        self.assertIn("explicitly deferred all chapter-framing decisions", app)
        self.assertIn('data-active-route="library"', html)
        self.assertIn("CONTRADICTION", app)
        self.assertIn('type: "table"', app)
        self.assertIn("/provenance`", app)
        self.assertIn("alexandrian-reader:", app)
        self.assertIn("alexandrian-selected-book", app)
        self.assertIn('id="provenance-panel"', html)
        self.assertIn('id="evidence-toggle"', html)
        self.assertIn(".reader-table-wrap", styles)
        self.assertIn(".evidence-marker", styles)
        self.assertIn(".contradiction-callout", styles)
        self.assertIn(".evidence-passage", styles)
        self.assertNotIn(".reader-tools {\n    display: none;", styles)
        self.assertIn("@media print", styles)

    def test_primary_pages_share_centered_responsive_geometry(self) -> None:
        project_path = Path(frontend_server.__file__).resolve().parent
        styles = (project_path / "frontend" / "styles.css").read_text(
            encoding="utf-8"
        )
        app = (project_path / "frontend" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("--app-frame-max: 1680px;", styles)
        self.assertIn(".library-view,\n.worktable-view {", styles)
        self.assertIn("/* Full-bleed primary atmospheres", styles)
        self.assertIn("min-height: calc(100vh - 74px);", styles)
        self.assertIn("url(\"/assets/inquiry-worktable-v1.png\")", styles)
        self.assertIn('body[data-active-route="library"]', styles)
        self.assertIn("height: calc(100dvh - 74px);", styles)
        self.assertIn("scrollbar-gutter: stable;", styles)
        self.assertIn(
            'url("/assets/archive-sentinel-v1-display.jpg") center / cover no-repeat',
            styles,
        )
        self.assertIn(
            "/* Focusing a cover must not trade the artwork viewport for a large synopsis. */",
            styles,
        )
        self.assertIn('grid-template-areas: "title actions";', styles)
        self.assertIn("body[data-active-route=\"reader\"]", styles)
        self.assertIn(".open-book:not(.is-plain) {", styles)
        self.assertIn("height: 100%;", styles)
        self.assertIn(".chapter-decision-checkpoint .chapter-question-response > label", styles)
        self.assertIn("font-size: 15px;", styles)
        self.assertIn("width: min(100%, var(--app-frame-max));", styles)
        self.assertIn(".reader-view {", styles)
        self.assertIn("margin-inline: auto;", styles)
        self.assertIn("aspect-ratio: 4 / 3;", styles)
        self.assertIn("aspect-ratio: 2 / 3;", styles)
        self.assertIn("perspective: 1800px;", styles)
        self.assertIn("transform-style: preserve-3d;", styles)
        self.assertIn("translateZ(-8px)", styles)
        self.assertIn(".open-book:not(.is-plain) .book-binding", styles)
        self.assertIn(
            "font-size: clamp(14px, 1.12vw, 17px);",
            styles,
        )
        self.assertIn(".transcript-message p,", styles)
        self.assertIn(".reader-identity strong {", styles)
        self.assertIn(
            "Math.round(bookHeight * (2 / 3))",
            app,
        )


if __name__ == "__main__":
    unittest.main()
