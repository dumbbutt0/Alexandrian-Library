import hashlib
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

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
        self.book_id = "book-test"
        self.book_path = (
            self.project_path / "data" / "books" / self.book_id / "book.md"
        )
        self.book_path.parent.mkdir(parents=True)
        self.book_path.write_text("# Verified\n\nThe final text.\n", encoding="utf-8")
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
            "assembly_sha256": "a" * 64,
            "finalized_at": "2026-01-01T00:00:00+00:00",
            "chapter_count": 1,
            "chapters": [],
            "source_count": 0,
            "review_ids": ["review-test"],
            "knowledge_node_ids": [],
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

    def test_http_endpoints_serve_verified_data_and_hold_inquiries(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        try:
            with urllib.request.urlopen(f"{base_url}/api/catalog") as response:
                catalog = json.loads(response.read())
            self.assertEqual(catalog["book_count"], 1)

            with urllib.request.urlopen(
                f"{base_url}/api/books/{self.book_id}"
            ) as response:
                book = json.loads(response.read())
            self.assertIn("The final text.", book["markdown"])

            with urllib.request.urlopen(
                f"{base_url}/assets/branches-cover-holy-cyber-v3.png"
            ) as response:
                cover = response.read()
                content_type = response.headers["Content-Type"]
            self.assertEqual(cover, b"test-png")
            self.assertEqual(content_type, "image/png")

            request = urllib.request.Request(
                f"{base_url}/api/inquiries",
                data=b'{"inquiry":"Why?"}',
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as raised:
                urllib.request.urlopen(request)
            self.assertEqual(raised.exception.code, 501)
            hold = json.loads(raised.exception.read())
            self.assertEqual(hold["status"], "backend_handoff_not_wired")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_v1_workflow_api_is_versioned_and_rejects_stale_actions(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
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

    def test_v1_workflow_api_allows_only_one_mutation_per_conversation(self) -> None:
        server = frontend_server.make_server(self.project_path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        base_url = f"http://{host}:{port}"
        held_lock = threading.Lock()
        held_lock.acquire()
        server.RequestHandlerClass.mutation_locks["conversation-test"] = held_lock
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
            "workflow-action-zone",
        ):
            self.assertIn(f'id="{element_id}"', html)
        for endpoint in (
            '"/api/v1/workflows"',
            '"/api/v1/conversations"',
            "/turns`",
            "/finish`",
            "/decisions`",
            "/advance`",
        ):
            self.assertIn(endpoint, app)
        self.assertNotIn('fetchJson("/api/inquiries"', app)
        self.assertIn("expected_stage: state.workflow.stage", app)
        self.assertIn("expected_revision: state.workflow.revision", app)
        self.assertIn("function renderReviewCheckpoint(action)", app)
        self.assertIn("function renderSourceCheckpoint(action)", app)
        self.assertIn("function renderChapterCheckpoint(action)", app)
        self.assertIn("function renderEditorialCheckpoint(action)", app)
        self.assertIn(".workflow-list-item.is-selected", styles)
        self.assertIn(".workflow-action-card", styles)

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


if __name__ == "__main__":
    unittest.main()
