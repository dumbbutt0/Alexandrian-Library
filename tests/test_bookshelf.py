import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bookshelf
import cover_design


class BookshelfTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.books_path = self.project_path / "data" / "books"
        self.knowledge_path = self.project_path / "data" / "knowledge"
        self.bookshelf_path = self.project_path / "data" / "bookshelf"
        self.review_id = "review-test"
        self.write_knowledge_graph()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def file_sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def relative_path(self, path: Path) -> str:
        return path.relative_to(self.project_path).as_posix()

    def write_knowledge_graph(self) -> None:
        self.write_json(
            self.knowledge_path / "graph.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-01-01T00:00:00+00:00",
                "nodes": [
                    {
                        "id": "ki-test-core-question",
                        "type": "core_question",
                        "title": "Can a final book become browsable?",
                        "summary": "Test the bookshelf projection.",
                        "status": "active",
                        "path": "nodes/review-test/ki-test-core-question.md",
                        "source_review_ids": [self.review_id],
                        "claim_ids": [],
                        "source_receipt_ids": [],
                        "transcript_references": ["T1"],
                    },
                    {
                        "id": "ki-other-claim",
                        "type": "claim",
                        "title": "An unrelated claim",
                        "summary": "This belongs to another review.",
                        "status": "active",
                        "path": "nodes/review-other/ki-other-claim.md",
                        "source_review_ids": ["review-other"],
                        "claim_ids": [],
                        "source_receipt_ids": [],
                        "transcript_references": [],
                    },
                ],
                "edges": [],
            },
        )

    def create_final_book(
        self,
        book_id: str = "book-test",
        source_receipt_ids: list[str] | None = None,
    ) -> Path:
        if source_receipt_ids is None:
            source_receipt_ids = ["source-test"]
        book_directory = self.books_path / book_id
        book_directory.mkdir(parents=True)
        book_path = book_directory / "book.md"
        book_path.write_text(
            (
                "# A Final Test Book\n\n"
                "The final content remains exact, readable, and available for "
                "cover verification.\n"
            ),
            encoding="utf-8",
        )
        candidate_path = book_directory / "candidate.md"
        candidate_path.write_bytes(book_path.read_bytes())
        manifest_path = book_directory / "manifest.json"
        self.write_json(
            manifest_path,
            {
                "schema_version": "1.1",
                "book_id": book_id,
                "working_title": "A Final Test Book",
                "central_question": (
                    "Can an immutable final book become a rebuildable shelf entry?"
                ),
                "book_mode": "design_oriented",
                "input_review_ids": [self.review_id],
            },
        )
        chapters = [
            {
                "chapter_id": "CH1",
                "working_title": "The First Chapter",
                "material_disposition": "include",
                "draft_id": "D2",
                "draft_sha256": "a" * 64,
                "assessment_id": "A4",
                "assessment_sha256": "b" * 64,
            },
            {
                "chapter_id": "CH2",
                "working_title": "The Second Chapter",
                "material_disposition": "include",
                "draft_id": "D2",
                "draft_sha256": "c" * 64,
                "assessment_id": "A4",
                "assessment_sha256": "d" * 64,
            },
        ]
        candidate_assembly_path = (
            book_directory / "candidate_assembly.json"
        )
        candidate_sha256 = self.file_sha256(candidate_path)
        self.write_json(
            candidate_assembly_path,
            {
                "schema_version": "1.0",
                "assembly_id": f"candidate-{book_id}",
                "book_id": book_id,
                "assembled_at": "2026-01-01T00:00:00+00:00",
                "lifecycle_status": "candidate",
                "candidate_path": self.relative_path(candidate_path),
                "candidate_sha256": candidate_sha256,
                "chapter_count": 2,
                "chapters": chapters,
                "source_count": len(source_receipt_ids),
                "source_receipt_ids": source_receipt_ids,
                "inputs": [
                    {
                        "role": "manifest",
                        "path": self.relative_path(manifest_path),
                        "sha256": self.file_sha256(manifest_path),
                    }
                ],
            },
        )
        editorial_path = book_directory / "editorial_review.json"
        candidate_assembly_sha256 = self.file_sha256(
            candidate_assembly_path
        )
        self.write_json(
            editorial_path,
            {
                "schema_version": "1.0",
                "book_id": book_id,
                "candidate_sha256": candidate_sha256,
                "candidate_assembly_sha256": candidate_assembly_sha256,
                "decision": "approved_for_finalization",
                "notes": [],
                "chapter_revisions": [],
                "decided_at": "2026-01-01T00:00:00+00:00",
            },
        )
        assembly_path = book_directory / "assembly.json"
        self.write_json(
            assembly_path,
            {
                "schema_version": "1.0",
                "assembly_id": f"assembly-{book_id}",
                "book_id": book_id,
                "assembled_at": "2026-01-02T00:00:00+00:00",
                "lifecycle_status": "final",
                "book_path": self.relative_path(book_path),
                "book_sha256": self.file_sha256(book_path),
                "candidate_path": self.relative_path(candidate_path),
                "candidate_sha256": candidate_sha256,
                "candidate_assembly_path": self.relative_path(
                    candidate_assembly_path
                ),
                "candidate_assembly_sha256": candidate_assembly_sha256,
                "editorial_review_path": self.relative_path(editorial_path),
                "editorial_review_sha256": self.file_sha256(editorial_path),
                "chapter_count": 2,
                "chapters": chapters,
                "source_count": len(source_receipt_ids),
                "source_receipt_ids": source_receipt_ids,
            },
        )
        return assembly_path

    def build(self) -> dict:
        return bookshelf.build_bookshelf(
            self.project_path,
            self.books_path,
            self.knowledge_path,
            self.bookshelf_path,
        )

    def test_final_book_becomes_one_frontend_catalog_entry(self) -> None:
        self.create_final_book()

        result = self.build()

        catalog = json.loads(
            result["catalog_path"].read_text(encoding="utf-8")
        )
        self.assertEqual(catalog["book_count"], 1)
        book = catalog["books"][0]
        self.assertEqual(book["book_id"], "book-test")
        self.assertEqual(book["artifact_status"], "final")
        self.assertEqual(book["shelf_status"], "internal_smoke_test")
        self.assertEqual(book["edition"], 1)
        self.assertEqual(book["cover"]["asset_status"], "fallback")
        self.assertEqual(
            book["cover"]["asset_url"],
            "/api/v1/books/book-test/cover",
        )
        self.assertIn(book["cover"]["quote"], (
            self.books_path / "book-test" / "book.md"
        ).read_text(encoding="utf-8"))
        self.assertEqual(
            [chapter["chapter_id"] for chapter in book["chapters"]],
            ["CH1", "CH2"],
        )
        self.assertEqual(
            book["knowledge_node_ids"],
            ["ki-test-core-question"],
        )
        self.assertIn(
            "A Final Test Book",
            result["index_path"].read_text(encoding="utf-8"),
        )

    def test_generated_cover_binding_flows_into_catalog_projection(self) -> None:
        self.create_final_book()
        cover_design.create_default_design(
            self.project_path,
            "book-test",
            designer_host="workflow-test",
        )
        generated = self.project_path / "generated-cover.png"
        generated.write_bytes(b"generated-cover-raster")

        design = cover_design.bind_generated_asset(
            self.project_path,
            "book-test",
            generated,
            generated_by="workflow-test",
        )
        result = self.build()
        catalog = json.loads(
            result["catalog_path"].read_text(encoding="utf-8")
        )
        projected = catalog["books"][0]["cover"]

        self.assertEqual(projected["design_sha256"], design["design_sha256"])
        self.assertEqual(projected["asset_status"], "generated")
        self.assertEqual(projected["asset_mime_type"], "image/png")
        self.assertEqual(projected["asset_sha256"], design["asset"]["sha256"])
        self.assertEqual(
            projected["asset_url"],
            "/api/v1/books/book-test/cover",
        )

    def test_candidate_without_final_assembly_is_not_shelved(self) -> None:
        candidate_directory = self.books_path / "book-candidate"
        self.write_json(
            candidate_directory / "candidate_assembly.json",
            {
                "lifecycle_status": "candidate",
            },
        )

        result = self.build()

        catalog = json.loads(
            result["catalog_path"].read_text(encoding="utf-8")
        )
        self.assertEqual(catalog["books"], [])

    def test_final_book_without_sources_becomes_catalog_entry(self) -> None:
        self.create_final_book(source_receipt_ids=[])

        result = self.build()

        catalog = json.loads(
            result["catalog_path"].read_text(encoding="utf-8")
        )
        self.assertEqual(catalog["book_count"], 1)
        self.assertEqual(catalog["books"][0]["source_count"], 0)

    def test_synthesized_final_accepts_exact_active_cycle_receipt(
        self,
    ) -> None:
        book_id = "book-active-cycle"
        book_directory = self.books_path / book_id
        receipt_path = (
            book_directory
            / "synthesis_cycles"
            / "cycle-009"
            / "receipt.json"
        )
        self.write_json(receipt_path, {"record": "active receipt"})
        receipt = {
            "book_id": book_id,
            "lifecycle_status": "ready_for_editorial",
            "book_sha256": "a" * 64,
            "candidate_sha256": "b" * 64,
            "receipt_sha256": "c" * 64,
            "selected_attempt": 1,
            "context_sha256": "d" * 64,
            "inventory_sha256": "e" * 64,
            "plan_sha256": "f" * 64,
            "audit_sha256": "1" * 64,
            "profile": {"name": "bounded profile"},
        }
        assembly = {
            "book_id": book_id,
            "synthesis_receipt_path": self.relative_path(receipt_path),
            "synthesis_receipt_file_sha256": self.file_sha256(receipt_path),
            "book_sha256": receipt["book_sha256"],
            "candidate_sha256": receipt["candidate_sha256"],
            "synthesis_receipt_sha256": receipt["receipt_sha256"],
            "synthesis_attempt": receipt["selected_attempt"],
            "synthesis_context_sha256": receipt["context_sha256"],
            "synthesis_inventory_sha256": receipt["inventory_sha256"],
            "synthesis_plan_sha256": receipt["plan_sha256"],
            "synthesis_audit_sha256": receipt["audit_sha256"],
            "publication_profile": receipt["profile"],
        }
        candidate = {"candidate_sha256": receipt["candidate_sha256"]}

        with patch.object(
            bookshelf.book_synthesis,
            "synthesis_receipt_path",
            return_value=receipt_path,
        ), patch.object(
            bookshelf.book_synthesis,
            "verify_receipt",
            return_value=receipt,
        ):
            verified = bookshelf.verify_synthesized_final(
                self.project_path,
                book_directory,
                assembly,
                candidate,
            )

        self.assertEqual(verified, receipt)

    def test_rebuild_is_idempotent_and_does_not_duplicate_books(self) -> None:
        self.create_final_book()
        first = self.build()
        first_catalog = first["catalog_path"].read_bytes()
        first_index = first["index_path"].read_bytes()

        second = self.build()

        self.assertEqual(second["catalog_path"].read_bytes(), first_catalog)
        self.assertEqual(second["index_path"].read_bytes(), first_index)
        catalog = json.loads(first_catalog)
        self.assertEqual(len(catalog["books"]), 1)

    def test_corrupt_book_rejects_rebuild_and_preserves_catalog(self) -> None:
        self.create_final_book()
        result = self.build()
        original_catalog = result["catalog_path"].read_bytes()
        (
            self.books_path / "book-test" / "book.md"
        ).write_text("# Changed after finalization\n", encoding="utf-8")

        with self.assertRaises(bookshelf.BookshelfError):
            self.build()

        self.assertEqual(
            result["catalog_path"].read_bytes(),
            original_catalog,
        )

    def test_changed_approved_candidate_breaks_provenance(self) -> None:
        self.create_final_book()
        (
            self.books_path / "book-test" / "candidate.md"
        ).write_text("# Candidate changed after approval\n", encoding="utf-8")

        with self.assertRaisesRegex(
            bookshelf.BookshelfError,
            "Candidate book hash does not match",
        ):
            self.build()

    def test_final_book_requires_matching_promoted_knowledge(self) -> None:
        self.create_final_book()
        graph_path = self.knowledge_path / "graph.json"
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        for node in graph["nodes"]:
            node["source_review_ids"] = ["review-other"]
        self.write_json(graph_path, graph)

        with self.assertRaisesRegex(
            bookshelf.BookshelfError,
            "No promoted knowledge nodes",
        ):
            self.build()


if __name__ == "__main__":
    unittest.main()
