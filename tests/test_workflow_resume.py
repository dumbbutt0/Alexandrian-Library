import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import backend_selector


class WorkflowResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.conversations_path = (
            self.project_path / "data" / "conversations"
        )
        self.reviews_path = self.project_path / "data" / "reviews"
        self.decisions_path = (
            self.project_path / "data" / "review_decisions"
        )
        self.books_path = self.project_path / "data" / "books"
        self.knowledge_path = self.project_path / "data" / "knowledge"
        self.conversation_id = "resume-test"
        self.review_id = f"review-{self.conversation_id}"
        self.book_id = f"book-{self.conversation_id}"
        self.conversation_path = (
            self.conversations_path / f"{self.conversation_id}.jsonl"
        )
        self.write_finished_conversation()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def patches(self) -> tuple:
        return (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
            patch.object(
                backend_selector,
                "REVIEWS_PATH",
                self.reviews_path,
            ),
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.decisions_path,
            ),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.books_path,
            ),
            patch.object(
                backend_selector,
                "KNOWLEDGE_PATH",
                self.knowledge_path,
            ),
        )

    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def write_finished_conversation(self) -> None:
        self.conversations_path.mkdir(parents=True)
        events = [
            {
                "type": "conversation_started",
                "conversation_id": self.conversation_id,
            },
            {
                "type": "message",
                "role": "user",
                "provider": "codex",
                "content": "Can a workflow resume from durable state?",
            },
            {
                "type": "conversation_finished",
                "status": "ready_for_review",
            },
        ]
        self.conversation_path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def write_review(self) -> None:
        self.write_json(
            self.reviews_path / f"{self.conversation_id}.json",
            {
                "review_id": self.review_id,
                "conversation_id": self.conversation_id,
                "review": {},
            },
        )
        (
            self.reviews_path / f"{self.conversation_id}.md"
        ).write_text("# Review\n", encoding="utf-8")

    def write_ready_decision(self) -> None:
        self.write_json(
            self.decisions_path / f"{self.conversation_id}.json",
            {
                "review_id": self.review_id,
                "conversation_id": self.conversation_id,
                "book_planning_gate": "ready",
            },
        )

    def write_manifest(self) -> None:
        book_directory = self.books_path / self.book_id
        self.write_json(
            book_directory / "manifest.json",
            {
                "book_id": self.book_id,
                "chapter_plan": [
                    {
                        "chapter_id": "CH1",
                        "readiness": "ready_to_draft",
                        "material_disposition": "include",
                    }
                ],
            },
        )
        (book_directory / "outline.md").write_text(
            "# Outline\n",
            encoding="utf-8",
        )

    def test_stage_is_derived_from_existing_artifacts(self) -> None:
        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)

            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "research_review",
            )
            self.write_review()
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "review_checkpoint",
            )
            self.write_ready_decision()
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "knowledge_promotion",
            )
            (
                self.knowledge_path / "nodes" / self.review_id
            ).mkdir(parents=True)
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_plan",
            )
            self.write_manifest()
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_plan_checkpoint",
            )
            self.write_json(
                self.books_path / self.book_id / "approval.json",
                {
                    "book_id": self.book_id,
                    "chapter_drafting_gate": "ready",
                },
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "chapter_development",
            )
            self.write_json(
                self.books_path / self.book_id / "runs" / "CH1.json",
                {
                    "book_id": self.book_id,
                    "chapter_id": "CH1",
                    "status": "chapter_complete",
                },
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_assembly",
            )

    def test_resume_does_not_repeat_an_existing_review(self) -> None:
        self.write_review()
        decision_path = (
            self.decisions_path / f"{self.conversation_id}.json"
        )

        def record_held_checkpoint(_review_path: Path) -> Path:
            self.write_json(
                decision_path,
                {
                    "review_id": self.review_id,
                    "conversation_id": self.conversation_id,
                    "book_planning_gate": "blocked",
                },
            )
            return decision_path

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            review_call = stack.enter_context(
                patch.object(
                    backend_selector,
                    "review_finished_conversation",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "record_human_review_checkpoint",
                    side_effect=record_held_checkpoint,
                )
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        review_call.assert_not_called()
        self.assertEqual(result["status"], "review_held")

    def test_discovery_ignores_unfinished_conversations(self) -> None:
        unfinished_path = self.conversations_path / "unfinished.jsonl"
        unfinished_path.write_text(
            json.dumps(
                {
                    "type": "conversation_started",
                    "conversation_id": "unfinished",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            workflows = backend_selector.discover_resumable_workflows()

        self.assertEqual(
            workflows,
            [(self.conversation_path, "research_review")],
        )


if __name__ == "__main__":
    unittest.main()
