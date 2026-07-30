import json
import hashlib
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

    def write_ready_completed_book_state(self) -> Path:
        self.write_review()
        self.write_ready_decision()
        (
            self.knowledge_path / "nodes" / self.review_id
        ).mkdir(parents=True)
        self.write_manifest()
        book_directory = self.books_path / self.book_id
        self.write_json(
            book_directory / "approval.json",
            {
                "book_id": self.book_id,
                "decision": "approved_for_drafting",
                "chapter_drafting_gate": "ready",
            },
        )
        self.write_json(
            book_directory / "runs" / "CH1.json",
            {
                "book_id": self.book_id,
                "chapter_id": "CH1",
                "status": "chapter_complete",
            },
        )
        return book_directory

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
                    "decision": "needs_plan_revision",
                    "chapter_drafting_gate": "blocked",
                },
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_plan_revision",
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
                "candidate_assembly",
            )

    def test_approved_plan_can_be_reopened_without_mutation(self) -> None:
        self.write_review()
        self.write_ready_decision()
        (self.knowledge_path / "nodes" / self.review_id).mkdir(
            parents=True
        )
        self.write_manifest()
        book_directory = self.books_path / self.book_id
        self.write_json(
            book_directory / "approval.json",
            {
                "book_id": self.book_id,
                "decision": "approved_for_drafting",
                "chapter_drafting_gate": "ready",
                "notes": [],
            },
        )

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            request_path = (
                backend_selector.record_book_plan_reopen_request(
                    book_directory.relative_to(self.project_path),
                    ["Separate draft readiness from research closure."],
                )
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_plan_revision",
            )
            history = backend_selector.archive_book_plan_for_revision(
                book_directory
            )

            prior_manifest = json.loads(
                (history / "manifest.json").read_text(encoding="utf-8")
            )
            revised_manifest = json.loads(json.dumps(prior_manifest))
            revised_manifest["schema_version"] = "1.2"
            revised_manifest["chapter_plan"][0]["draft_gate"] = "ready"
            revised_manifest["chapter_plan"][0]["open_requirements"] = []
            self.write_json(
                book_directory / "manifest.json",
                revised_manifest,
            )
            (book_directory / "outline.md").write_text(
                "# Revised outline\n",
                encoding="utf-8",
            )
            carried = (
                backend_selector.carry_forward_reopened_plan_approval(
                    book_directory.relative_to(self.project_path)
                )
            )

        self.assertEqual(request_path.name, "reopen_request.json")
        self.assertTrue((history / "reopen_request.json").is_file())
        self.assertTrue((history / "approval.json").is_file())
        self.assertIsNotNone(carried)
        approval = json.loads(carried.read_text(encoding="utf-8"))
        self.assertEqual(
            approval["approval_basis"],
            "carried_forward_after_readiness_only_revision",
        )

    def test_reopen_request_detects_changed_approved_input(self) -> None:
        self.write_review()
        self.write_ready_decision()
        (self.knowledge_path / "nodes" / self.review_id).mkdir(
            parents=True
        )
        self.write_manifest()
        book_directory = self.books_path / self.book_id
        self.write_json(
            book_directory / "approval.json",
            {
                "book_id": self.book_id,
                "decision": "approved_for_drafting",
                "chapter_drafting_gate": "ready",
                "notes": [],
            },
        )

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            backend_selector.record_book_plan_reopen_request(
                book_directory,
                ["Separate draft readiness from research closure."],
            )
            (book_directory / "outline.md").write_text(
                "# Changed after request\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "no longer matches outline",
            ):
                backend_selector.workflow_stage(self.conversation_path)

    def test_independent_ready_chapter_can_progress_past_blocked_one(
        self,
    ) -> None:
        self.write_review()
        self.write_ready_decision()
        (self.knowledge_path / "nodes" / self.review_id).mkdir(
            parents=True
        )
        book_directory = self.books_path / self.book_id
        self.write_json(
            book_directory / "manifest.json",
            {
                "book_id": self.book_id,
                "chapter_plan": [
                    {
                        "chapter_id": "CH1",
                        "readiness": "research_required",
                        "draft_gate": "blocked",
                        "open_requirements": [
                            {
                                "kind": "research",
                                "description": "Verify a required claim.",
                                "disposition": "bounded_unresolved",
                                "required_before": "drafting",
                            }
                        ],
                        "dependencies": [],
                        "material_disposition": "include",
                    },
                    {
                        "chapter_id": "CH2",
                        "readiness": "experiment_required",
                        "draft_gate": "ready",
                        "open_requirements": [
                            {
                                "kind": "experiment",
                                "description": "Run the test later.",
                                "disposition": "routed_to_later",
                                "required_before": "later_work",
                            }
                        ],
                        "dependencies": [],
                        "material_disposition": "include",
                    },
                ],
            },
        )
        (book_directory / "outline.md").write_text(
            "# Outline\n",
            encoding="utf-8",
        )
        self.write_json(
            book_directory / "approval.json",
            {
                "book_id": self.book_id,
                "decision": "approved_for_drafting",
                "chapter_drafting_gate": "ready",
            },
        )

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "chapter_development",
            )
            with patch.object(
                backend_selector,
                "run_chapter_completion_loop",
                return_value={
                    "chapter_id": "CH2",
                    "status": "chapter_complete",
                },
            ) as run:
                results = backend_selector.run_ready_book_chapters(
                    "codex",
                    "/usr/bin/codex",
                    json.loads(
                        (book_directory / "manifest.json").read_text(
                            encoding="utf-8"
                        )
                    ),
                )

        self.assertEqual(results[0]["chapter_id"], "CH2")
        run.assert_called_once_with(
            "codex",
            "/usr/bin/codex",
            self.book_id,
            "CH2",
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

    def test_review_checkpoint_can_pause_without_writing_a_decision(self) -> None:
        self.write_review()

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "record_human_review_checkpoint",
                    return_value=None,
                )
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        self.assertEqual(result["status"], "awaiting_review_checkpoint")
        self.assertFalse(
            (
                self.decisions_path / f"{self.conversation_id}.json"
            ).exists()
        )

    def test_candidate_requires_editorial_approval_before_finalization(
        self,
    ) -> None:
        book_directory = self.write_ready_completed_book_state()
        candidate_body = "# Candidate\n"
        candidate_path = book_directory / "candidate.md"
        candidate_path.write_text(candidate_body, encoding="utf-8")
        candidate_sha256 = hashlib.sha256(
            candidate_body.encode("utf-8")
        ).hexdigest()
        candidate_receipt_path = (
            book_directory / "candidate_assembly.json"
        )
        candidate = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "lifecycle_status": "candidate",
            "candidate_path": str(
                candidate_path.relative_to(self.project_path)
            ),
            "candidate_sha256": candidate_sha256,
            "chapters": [{"chapter_id": "CH1"}],
        }
        self.write_json(candidate_receipt_path, candidate)

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector.book_assembler,
                    "verify_existing_candidate",
                    return_value=candidate,
                )
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_synthesis",
            )
            synthesis_receipt = {
                "book_id": self.book_id,
                "lifecycle_status": "ready_for_editorial",
                "candidate_sha256": candidate_sha256,
                "book_path": (
                    f"data/books/{self.book_id}/synthesis/attempts/S1/book.md"
                ),
                "book_sha256": "b" * 64,
                "selected_attempt": 1,
            }
            stack.enter_context(
                patch.object(
                    backend_selector.book_synthesis,
                    "workflow_state",
                    return_value="ready_for_editorial",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector.book_synthesis,
                    "verify_receipt",
                    return_value=synthesis_receipt,
                )
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "editorial_checkpoint",
            )
            self.write_json(
                book_directory / "editorial_review.json",
                {
                    "schema_version": "1.0",
                    "book_id": self.book_id,
                    "candidate_sha256": candidate_sha256,
                    "candidate_assembly_sha256": hashlib.sha256(
                        candidate_receipt_path.read_bytes()
                    ).hexdigest(),
                    "decision": "approved_for_finalization",
                    "notes": [],
                    "chapter_revisions": [],
                    "decided_at": "2026-01-01T00:00:00+00:00",
                },
            )
            stack.enter_context(
                patch.object(
                    backend_selector.book_assembler,
                    "load_editorial_decision",
                    return_value=(
                        book_directory / "editorial_review.json",
                        {
                            "decision": "approved_for_finalization",
                        },
                    ),
                )
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "book_finalization",
            )

    def test_inventory_provider_hold_is_terminal_at_public_workflow(
        self,
    ) -> None:
        book_directory = self.write_ready_completed_book_state()
        candidate_path = book_directory / "candidate.md"
        candidate_path.write_text("# Candidate\n", encoding="utf-8")
        candidate = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "lifecycle_status": "candidate",
            "candidate_path": str(
                candidate_path.relative_to(self.project_path)
            ),
            "candidate_sha256": hashlib.sha256(
                candidate_path.read_bytes()
            ).hexdigest(),
        }
        self.write_json(
            book_directory / "candidate_assembly.json",
            candidate,
        )
        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector.book_assembler,
                    "verify_existing_candidate",
                    return_value=candidate,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector.book_synthesis,
                    "workflow_state",
                    return_value="inventory_provider_held",
                )
            )
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "synthesis_held",
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )
        self.assertEqual(result["status"], "synthesis_held")
        self.assertEqual(
            result["synthesis_state"],
            "inventory_provider_held",
        )
        self.assertIn("No further provider call", result["reason"])

    def test_noninteractive_editorial_checkpoint_pauses_cleanly(
        self,
    ) -> None:
        book_directory = self.write_ready_completed_book_state()
        candidate_body = "# Candidate\n"
        candidate_path = book_directory / "candidate.md"
        candidate_path.write_text(candidate_body, encoding="utf-8")
        candidate_sha256 = hashlib.sha256(
            candidate_body.encode("utf-8")
        ).hexdigest()
        candidate_receipt_path = (
            book_directory / "candidate_assembly.json"
        )
        candidate = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "lifecycle_status": "candidate",
            "candidate_path": str(
                candidate_path.relative_to(self.project_path)
            ),
            "candidate_sha256": candidate_sha256,
            "chapters": [{"chapter_id": "CH1"}],
        }
        self.write_json(candidate_receipt_path, candidate)
        edition_path = (
            book_directory / "synthesis" / "attempts" / "S1" / "book.md"
        )
        edition_path.parent.mkdir(parents=True)
        edition_path.write_text("# Synthesized\n", encoding="utf-8")
        synthesis_receipt_path = (
            book_directory / "synthesis" / "receipt.json"
        )
        synthesis_receipt = {
            "book_id": self.book_id,
            "lifecycle_status": "ready_for_editorial",
            "candidate_sha256": candidate_sha256,
            "book_path": str(edition_path.relative_to(self.project_path)),
            "book_sha256": hashlib.sha256(
                edition_path.read_bytes()
            ).hexdigest(),
            "selected_attempt": 1,
        }
        self.write_json(synthesis_receipt_path, synthesis_receipt)

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector.book_assembler,
                    "verify_existing_candidate",
                    return_value=candidate,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector.book_synthesis,
                    "workflow_state",
                    return_value="ready_for_editorial",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector.book_synthesis,
                    "verify_receipt",
                    return_value=synthesis_receipt,
                )
            )
            stack.enter_context(
                patch("builtins.input", side_effect=EOFError)
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        self.assertEqual(
            result["status"],
            "awaiting_editorial_review",
        )
        self.assertEqual(
            Path(result["edition_path"]),
            edition_path,
        )
        self.assertFalse(
            (book_directory / "editorial_review.json").exists()
        )

    def test_editorial_revision_routing_reopens_only_selected_chapter(
        self,
    ) -> None:
        book_directory = self.write_ready_completed_book_state()
        archived = (
            book_directory / "editorial_history" / "v001"
        )
        self.write_json(
            archived / "editorial_review.json",
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "candidate_sha256": "a" * 64,
                "decision": "needs_chapter_revision",
                "notes": ["Keep the full-book transition coherent."],
                "chapter_revisions": [
                    {
                        "chapter_id": "CH1",
                        "instructions": ["Clarify the restart boundary."],
                    }
                ],
            },
        )

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            (book_directory / "runs" / "CH1.json").unlink()
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                "CH1",
            )
            run["status"] = "chapter_complete"
            backend_selector.save_chapter_run(run)
            routed = backend_selector.route_editorial_chapter_revisions(
                self.book_id,
                archived,
                json.loads(
                    (archived / "editorial_review.json").read_text(
                        encoding="utf-8"
                    )
                ),
            )
            reopened = backend_selector.load_or_create_chapter_run(
                self.book_id,
                "CH1",
            )

        self.assertEqual(routed, ["CH1"])
        self.assertEqual(
            reopened["status"],
            "editorial_revision_requested",
        )
        self.assertEqual(
            reopened["editorial_candidate_sha256"],
            "a" * 64,
        )

    def test_editorial_checkpoint_records_targeted_human_changes(
        self,
    ) -> None:
        book_directory = self.write_ready_completed_book_state()
        candidate_receipt_path = (
            book_directory / "candidate_assembly.json"
        )
        self.write_json(
            candidate_receipt_path,
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "lifecycle_status": "candidate",
                "candidate_sha256": "a" * 64,
            },
        )
        edition_path = (
            book_directory / "synthesis" / "attempts" / "S1" / "book.md"
        )
        edition_path.parent.mkdir(parents=True)
        edition_path.write_text("# Synthesized\n", encoding="utf-8")
        synthesis_receipt_path = (
            book_directory / "synthesis" / "receipt.json"
        )
        synthesis_receipt = {
            "book_id": self.book_id,
            "lifecycle_status": "ready_for_editorial",
            "candidate_sha256": "a" * 64,
            "book_path": str(edition_path.relative_to(self.project_path)),
            "book_sha256": hashlib.sha256(
                edition_path.read_bytes()
            ).hexdigest(),
            "selected_attempt": 1,
        }
        self.write_json(synthesis_receipt_path, synthesis_receipt)

        with (
            patch(
                "builtins.input",
                side_effect=[
                    "2",
                    "Improve the transition into the conclusion.",
                    "CH1",
                    "Define the restart boundary;preserve the open limitation",
                ],
            ),
            patch.object(
                backend_selector.book_synthesis,
                "verify_receipt",
                return_value=synthesis_receipt,
            ),
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
        ):
            review_path = backend_selector.record_editorial_checkpoint(
                synthesis_receipt_path,
                book_directory / "manifest.json",
            )
        review = json.loads(review_path.read_text(encoding="utf-8"))

        self.assertEqual(review["decision"], "needs_chapter_revision")
        self.assertEqual(
            review["chapter_revisions"],
            [
                {
                    "chapter_id": "CH1",
                    "instructions": [
                        "Define the restart boundary",
                        "preserve the open limitation",
                    ],
                }
            ],
        )
        self.assertEqual(review["accepted_finalization_requirements"], [])

    def test_final_approval_accepts_named_finalization_requirements(
        self,
    ) -> None:
        book_directory = self.write_ready_completed_book_state()
        manifest_path = book_directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["chapter_plan"][0]["open_requirements"] = [
            {
                "kind": "human_decision",
                "description": "Approve the final public profile name.",
                "disposition": "requires_user_decision",
                "required_before": "finalization",
            }
        ]
        self.write_json(manifest_path, manifest)
        candidate_receipt_path = (
            book_directory / "candidate_assembly.json"
        )
        self.write_json(
            candidate_receipt_path,
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "lifecycle_status": "candidate",
                "candidate_sha256": "a" * 64,
            },
        )
        edition_path = (
            book_directory / "synthesis" / "attempts" / "S1" / "book.md"
        )
        edition_path.parent.mkdir(parents=True)
        edition_path.write_text("# Synthesized\n", encoding="utf-8")
        synthesis_receipt_path = (
            book_directory / "synthesis" / "receipt.json"
        )
        synthesis_receipt = {
            "book_id": self.book_id,
            "lifecycle_status": "ready_for_editorial",
            "candidate_sha256": "a" * 64,
            "book_path": str(edition_path.relative_to(self.project_path)),
            "book_sha256": hashlib.sha256(
                edition_path.read_bytes()
            ).hexdigest(),
            "selected_attempt": 1,
        }
        self.write_json(synthesis_receipt_path, synthesis_receipt)

        with (
            patch("builtins.input", side_effect=["1", ""]),
            patch.object(
                backend_selector.book_synthesis,
                "verify_receipt",
                return_value=synthesis_receipt,
            ),
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
        ):
            review_path = backend_selector.record_editorial_checkpoint(
                synthesis_receipt_path,
                manifest_path,
            )
        review = json.loads(review_path.read_text(encoding="utf-8"))

        self.assertEqual(review["decision"], "approved_for_finalization")
        self.assertEqual(
            review["accepted_finalization_requirements"],
            [
                {
                    "chapter_id": "CH1",
                    "kind": "human_decision",
                    "description": "Approve the final public profile name.",
                    "disposition": "requires_user_decision",
                }
            ],
        )

    def test_research_revision_creates_one_hash_bound_continuation(self) -> None:
        self.write_review()
        decision_path = (
            self.decisions_path / f"{self.conversation_id}.json"
        )
        self.write_json(
            decision_path,
            {
                "review_id": self.review_id,
                "conversation_id": self.conversation_id,
                "decision": "needs_research_or_revision",
                "book_planning_gate": "blocked",
                "confirmed_core_question_or_thesis": (
                    "Can a workflow resume from durable state?"
                ),
                "accepted_belief_revisions": [
                    "Test single-session fidelity and multi-session durability."
                ],
                "disputed_points": [],
                "excluded_material": [],
            },
        )
        original_conversation = self.conversation_path.read_bytes()
        review_path = self.reviews_path / f"{self.conversation_id}.json"
        original_review = review_path.read_bytes()
        original_decision = decision_path.read_bytes()

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)

            first_result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )
            second_result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )
            continuation_path = Path(first_result["conversation_path"])
            context = backend_selector.load_research_continuation_context(
                continuation_path
            )

            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "research_continuation",
            )
            backend_selector.finish_conversation(continuation_path)
            self.assertEqual(
                backend_selector.workflow_stage(self.conversation_path),
                "research_continued",
            )

        self.assertEqual(first_result["status"], "research_continuation")
        self.assertEqual(
            second_result["conversation_path"],
            first_result["conversation_path"],
        )
        self.assertEqual(
            context["lineage"]["parent_review_id"],
            self.review_id,
        )
        self.assertEqual(
            context["human_checkpoint"]["accepted_belief_revisions"],
            [
                "Test single-session fidelity and multi-session durability."
            ],
        )
        self.assertEqual(
            self.conversation_path.read_bytes(),
            original_conversation,
        )
        self.assertEqual(review_path.read_bytes(), original_review)
        self.assertEqual(decision_path.read_bytes(), original_decision)

    def test_continuation_context_is_supplied_as_data_to_prompts(self) -> None:
        context = {
            "lineage": {
                "parent_review_id": self.review_id,
            },
            "human_checkpoint": {
                "accepted_belief_revisions": ["Use two phases."],
            },
        }

        librarian_prompt = backend_selector.build_librarian_prompt(
            "Librarian rules",
            [],
            "Continue the research.",
            context,
        )
        reviewer_prompt = backend_selector.build_reviewer_prompt(
            "Reviewer rules",
            "continued-research",
            [{"reference": "T1", "role": "user", "content": "Continue."}],
            [],
            context,
        )

        self.assertIn('"research_continuation_context"', librarian_prompt)
        self.assertIn('"parent_review_id": "review-resume-test"', librarian_prompt)
        self.assertIn('"research_continuation_context"', reviewer_prompt)
        self.assertIn('"Use two phases."', reviewer_prompt)
        self.assertIn(
            "do not submit its transcript references or source receipt IDs",
            reviewer_prompt,
        )
        self.assertIn(
            "Give every evidence gap a terminal disposition",
            reviewer_prompt,
        )

    def test_resume_routes_call_limit_to_separate_recovery_checkpoint(
        self,
    ) -> None:
        recovery_path = (
            self.books_path
            / self.book_id
            / "runs"
            / "CH2.recovery.json"
        )
        manifest = {
            "book_id": self.book_id,
            "chapter_plan": [
                {
                    "chapter_id": "CH2",
                    "material_disposition": "include",
                }
            ],
        }
        stopped_run = {
            "chapter_id": "CH2",
            "status": "agent_call_limit_reached",
            "stop_reason": "The bounded normal run stopped.",
            "human_input_requests": [],
        }

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "workflow_stage",
                    return_value="chapter_development",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_workflow_json",
                    return_value=manifest,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "run_ready_book_chapters",
                    return_value=[stopped_run],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=None,
                )
            )
            checkpoint = stack.enter_context(
                patch.object(
                    backend_selector,
                    "record_chapter_recovery_checkpoint",
                    return_value=recovery_path,
                )
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        checkpoint.assert_called_once_with(self.book_id, "CH2")
        self.assertEqual(result["status"], "recovery_authorized")
        self.assertEqual(result["max_additional_provider_calls"], 4)

    def test_resume_routes_evidence_block_to_recovery_checkpoint(
        self,
    ) -> None:
        recovery_path = (
            self.books_path
            / self.book_id
            / "runs"
            / "CH5.recovery.json"
        )
        manifest = {
            "book_id": self.book_id,
            "chapter_plan": [
                {
                    "chapter_id": "CH5",
                    "material_disposition": "include",
                }
            ],
        }
        stopped_run = {
            "chapter_id": "CH5",
            "status": "chapter_blocked",
            "last_assessment_outcome": "chapter_blocked",
            "last_research_packet_id": "R1",
            "stop_reason": "The admitted sources did not establish the claim.",
            "human_input_requests": [],
        }

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "workflow_stage",
                    return_value="chapter_development",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_workflow_json",
                    return_value=manifest,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "run_ready_book_chapters",
                    return_value=[stopped_run],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=None,
                )
            )
            checkpoint = stack.enter_context(
                patch.object(
                    backend_selector,
                    "record_research_access_recovery_checkpoint",
                    return_value=recovery_path,
                )
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        checkpoint.assert_called_once_with(self.book_id, "CH5")
        self.assertEqual(
            result["status"],
            "research_access_recovery_authorized",
        )
        self.assertEqual(result["max_additional_provider_calls"], 10)
        self.assertEqual(result["max_new_source_plans"], 1)
        self.assertEqual(result["max_new_research_packets"], 1)

    def test_resume_routes_recorded_decision_to_closeout_checkpoint(
        self,
    ) -> None:
        closeout_path = (
            self.books_path
            / self.book_id
            / "runs"
            / "CH5.A5.human-closeout.json"
        )
        manifest = {
            "book_id": self.book_id,
            "chapter_plan": [
                {
                    "chapter_id": "CH5",
                    "material_disposition": "include",
                }
            ],
        }
        stopped_run = {
            "chapter_id": "CH5",
            "status": "human_input_closeout_required",
            "last_assessment_id": "A5",
            "stop_reason": "The recorded decision needs bounded closeout.",
            "human_input_requests": ["Approve the V1 observations?"],
        }

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "workflow_stage",
                    return_value="chapter_development",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_workflow_json",
                    return_value=manifest,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "run_ready_book_chapters",
                    return_value=[stopped_run],
                )
            )
            checkpoint = stack.enter_context(
                patch.object(
                    backend_selector,
                    "record_chapter_human_input_closeout_checkpoint",
                    return_value=closeout_path,
                )
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        checkpoint.assert_called_once_with(self.book_id, "CH5")
        self.assertEqual(
            result["status"],
            "human_input_closeout_authorized",
        )
        self.assertEqual(result["max_additional_provider_calls"], 6)

    def test_resume_routes_human_stop_to_assessment_bound_checkpoint(
        self,
    ) -> None:
        human_input_path = (
            self.books_path
            / self.book_id
            / "runs"
            / "CH3.A1.human-input.json"
        )
        manifest = {
            "book_id": self.book_id,
            "chapter_plan": [
                {
                    "chapter_id": "CH3",
                    "material_disposition": "include",
                }
            ],
        }
        stopped_run = {
            "chapter_id": "CH3",
            "status": "human_input_required",
            "last_assessment_id": "A1",
            "stop_reason": "The user's interpretation is required.",
            "human_input_requests": ["Which interpretation should remain?"],
        }

        with ExitStack() as stack:
            for active_patch in self.patches():
                stack.enter_context(active_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "workflow_stage",
                    return_value="chapter_development",
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_workflow_json",
                    return_value=manifest,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "run_ready_book_chapters",
                    return_value=[stopped_run],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_human_input",
                    return_value=None,
                )
            )
            checkpoint = stack.enter_context(
                patch.object(
                    backend_selector,
                    "record_chapter_human_input_checkpoint",
                    return_value=human_input_path,
                )
            )
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                self.conversation_path,
            )

        checkpoint.assert_called_once_with(self.book_id, "CH3")
        self.assertEqual(result["status"], "human_input_recorded")
        self.assertEqual(
            result["human_input_path"],
            str(human_input_path),
        )

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
