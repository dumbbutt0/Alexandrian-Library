import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import book_assembler


class BookAssemblyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.book_id = "book-assembly-test"
        self.conversation_id = "assembly-test"
        self.review_id = f"review-{self.conversation_id}"
        self.book_directory = (
            self.project_path / "data" / "books" / self.book_id
        )
        self.book_directory.mkdir(parents=True)
        self.write_json(
            self.book_directory / "manifest.json",
            {
                "book_id": self.book_id,
                "working_title": "A Durable Book",
                "book_mode": "exploratory",
                "central_question": "Can completed research remain verifiable?",
                "curiosity_signature": {
                    "why_this_question_matters": [
                        {"text": "The result should remain trustworthy."}
                    ],
                    "original_intuition": [
                        {"text": "Preserve evidence before prose."}
                    ],
                    "surprising_connections": [],
                    "central_tensions": [],
                    "desired_transformation": [],
                    "questions_that_must_remain_open": [],
                },
                "input_review_ids": [self.review_id],
                "chapter_plan": [
                    self.chapter_plan("CH1", "First Chapter"),
                    self.chapter_plan("CH2", "Second Chapter"),
                ],
            },
        )
        self.write_json(
            self.book_directory / "approval.json",
            {
                "book_id": self.book_id,
                "decision": "approved_for_drafting",
                "chapter_drafting_gate": "ready",
            },
        )
        review_path = (
            self.project_path
            / "data"
            / "reviews"
            / f"{self.conversation_id}.json"
        )
        self.write_json(
            review_path,
            {
                "review_id": self.review_id,
                "conversation_id": self.conversation_id,
                "source_receipts": [
                    {
                        "receipt_id": "SRC1",
                        "title": "Primary Source",
                        "final_url": "https://example.com/study",
                    }
                ],
                "review": {
                    "checked_sources": [
                        {
                            "receipt_id": "SRC1",
                            "finding": "supports",
                            "author_or_organization": "Research Group",
                            "publication_date": "2026-01-01",
                            "publisher_or_journal": "Example Journal",
                            "doi": "10.0000/example",
                            "limitations": "One bounded test.",
                        }
                    ],
                    "experiments": [
                        {
                            "hypothesis": "Hashes expose changed inputs.",
                            "observable_outcome": "Changed input is rejected.",
                            "comparison_or_baseline": "Assembly without hashes.",
                            "evidence_that_would_weaken_the_claim": (
                                "Changed input is accepted."
                            ),
                            "major_unresolved_dependency": (
                                "Filesystem integrity."
                            ),
                        }
                    ],
                    "use_case_candidates": [
                        {"text": "Build a verifiable research book."}
                    ],
                },
            },
        )
        self.write_completed_chapter(
            "CH1",
            "# First Chapter\n\nExact first chapter.",
        )
        self.write_completed_chapter(
            "CH2",
            "# Second Chapter\n\nExact second chapter.",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def chapter_plan(self, chapter_id: str, title: str) -> dict:
        return {
            "chapter_id": chapter_id,
            "working_title": title,
            "readiness": "ready_to_draft",
            "material_disposition": "include",
        }

    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_completed_chapter(
        self,
        chapter_id: str,
        markdown: str,
    ) -> None:
        draft_directory = (
            self.book_directory / "drafts" / chapter_id
        )
        draft_directory.mkdir(parents=True)
        draft_path = draft_directory / "D2.md"
        draft_content = markdown.rstrip() + "\n"
        draft_path.write_text(draft_content, encoding="utf-8")
        draft_sha256 = hashlib.sha256(
            draft_content.encode("utf-8")
        ).hexdigest()
        relative_path = str(draft_path.relative_to(self.project_path))
        context_sha256 = "c" * 64
        created_at = "2026-01-01T00:00:00+00:00"
        final_draft = {
            "draft_id": "D2",
            "relative_path": relative_path,
            "sha256": draft_sha256,
            "context_sha256": context_sha256,
            "provider": "test",
            "created_at": created_at,
        }
        self.write_json(
            draft_directory / "D2.json",
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "chapter_id": chapter_id,
                **final_draft,
                "source_workspace_version": 1,
                "source_workspace_sha256": "1" * 64,
            },
        )

        workspace_directory = (
            self.book_directory / "workspaces" / chapter_id
        )
        workspace_document = {
            "schema_version": "1.0",
            "workspace_version": 2,
            "workspace": {
                "draft_versions": [
                    {
                        "draft_id": "D1",
                        "relative_path": "unused",
                        "sha256": "1" * 64,
                        "context_sha256": context_sha256,
                        "provider": "test",
                        "created_at": created_at,
                    },
                    final_draft,
                ]
            },
        }
        workspace_document["workspace_sha256"] = (
            book_assembler.canonical_json_sha256(
                workspace_document,
                "workspace_sha256",
            )
        )
        self.write_json(
            workspace_directory / "versions" / "v002.json",
            workspace_document,
        )
        self.write_json(
            workspace_directory / "index.json",
            {
                "book_id": self.book_id,
                "chapter_id": chapter_id,
                "current_version": 2,
                "current_json_path": "versions/v002.json",
            },
        )

        assessment_document = {
            "assessment_id": "A2",
            "assessment": {
                "book_id": self.book_id,
                "chapter_id": chapter_id,
                "workspace_sha256": workspace_document["workspace_sha256"],
                "draft_id": "D2",
                "outcome": "chapter_complete",
            },
        }
        assessment_document["assessment_sha256"] = (
            book_assembler.canonical_json_sha256(
                assessment_document,
                "assessment_sha256",
            )
        )
        self.write_json(
            self.book_directory
            / "assessments"
            / chapter_id
            / "A2.json",
            assessment_document,
        )
        self.write_json(
            self.book_directory / "runs" / f"{chapter_id}.json",
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "chapter_id": chapter_id,
                "status": "chapter_complete",
                "final_pass_requested": True,
                "final_pass_source_draft_id": "D1",
                "last_assessment_id": "A2",
                "last_assessment_sha256": assessment_document[
                    "assessment_sha256"
                ],
            },
        )

    def test_assembles_exact_chapters_and_end_material(self) -> None:
        receipt = book_assembler.assemble_book(
            self.project_path,
            self.book_id,
        )

        book_path = self.project_path / receipt["book_path"]
        body = book_path.read_text(encoding="utf-8")
        self.assertLess(
            body.index("# First Chapter"),
            body.index("# Second Chapter"),
        )
        self.assertIn("Exact first chapter.", body)
        self.assertIn("# Experiments and Use Cases", body)
        self.assertIn("Hashes expose changed inputs.", body)
        self.assertIn("Build a verifiable research book.", body)
        self.assertIn("# Evidence and Source Index", body)
        self.assertIn("https://example.com/study", body)
        self.assertEqual(receipt["chapter_count"], 2)
        self.assertEqual(receipt["source_receipt_ids"], ["SRC1"])

    def test_rejects_incomplete_chapter(self) -> None:
        run_path = self.book_directory / "runs" / "CH2.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["status"] = "active"
        self.write_json(run_path, run)

        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.assemble_book(
                self.project_path,
                self.book_id,
            )

        self.assertFalse((self.book_directory / "book.md").exists())

    def test_rejects_tampered_final_draft(self) -> None:
        (
            self.book_directory / "drafts" / "CH1" / "D2.md"
        ).write_text("Changed after evaluation.\n", encoding="utf-8")

        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.assemble_book(
                self.project_path,
                self.book_id,
            )

    def test_existing_assembly_is_idempotent_and_detects_drift(self) -> None:
        first = book_assembler.assemble_book(
            self.project_path,
            self.book_id,
        )
        second = book_assembler.assemble_book(
            self.project_path,
            self.book_id,
        )
        self.assertEqual(first, second)

        manifest_path = self.book_directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["working_title"] = "Changed after assembly"
        self.write_json(manifest_path, manifest)

        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.assemble_book(
                self.project_path,
                self.book_id,
            )


if __name__ == "__main__":
    unittest.main()
