import tempfile
import unittest
import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import backend_selector


CRITERION_NAMES = (
    "central_lesson",
    "evidence_grounding",
    "curiosity_and_belief",
    "contradictions_and_limits",
    "intellectual_foundations",
    "mechanism_and_falsification",
    "branch_and_requirement_handling",
    "experiments_and_actionability",
)


class ChapterCompletionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.books_path = self.project_path / "data" / "books"
        self.book_id = "book-controller-test"
        self.chapter_id = "CH1"
        self.state = {"version": 0, "drafts": []}
        self.developer_final_flags = []
        self.developer_research_paths = []
        self.developer_editorial_paths = []
        self.developer_human_input_paths = []
        self.developer_recovery_paths = []
        self.evaluation_number = 0
        self.research_number = 0

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def workspace_state(self, book_id: str, chapter_id: str) -> dict | None:
        self.assertEqual(book_id, self.book_id)
        self.assertEqual(chapter_id, self.chapter_id)
        if self.state["version"] == 0:
            return None
        return {
            "index_path": "workspace/index.json",
            "current_version": self.state["version"],
            "context_sha256": "c" * 64,
            "current_json_path": "workspace/current.json",
            "current_markdown_path": "workspace/current.md",
        }

    def workspace_document(self, _workspace_state: dict) -> dict:
        version = self.state["version"]
        return {
            "schema_version": "1.0",
            "workspace_version": version,
            "workspace_sha256": str(version) * 64,
            "workspace": {
                "draft_versions": list(self.state["drafts"]),
            },
        }

    def develop_side_effect(
        self,
        _backend: str,
        _program_path: str,
        book_id: str,
        chapter_id: str,
        latest_assessment_path: str | None = None,
        latest_research_packet_path: str | None = None,
        final_pass_requested: bool = False,
        editorial_review_path: str | None = None,
        human_input_path: str | None = None,
        recovery_authorization_path: str | None = None,
    ) -> tuple[Path, Path, str, int]:
        self.assertEqual(book_id, self.book_id)
        self.assertEqual(chapter_id, self.chapter_id)
        if latest_assessment_path is not None:
            self.assertRegex(
                latest_assessment_path,
                rf"assessments/{self.chapter_id}/A[1-9][0-9]*\.json$",
            )
        self.developer_research_paths.append(
            latest_research_packet_path
        )
        self.developer_final_flags.append(final_pass_requested)
        self.developer_editorial_paths.append(editorial_review_path)
        self.developer_human_input_paths.append(human_input_path)
        self.developer_recovery_paths.append(
            recovery_authorization_path
        )
        self.state["version"] += 1
        if self.state["version"] == 2:
            self.state["drafts"] = [self.draft("D1")]
        if editorial_review_path is not None:
            next_id = f"D{len(self.state['drafts']) + 1}"
            self.state["drafts"].append(self.draft(next_id))
        if final_pass_requested:
            next_id = f"D{len(self.state['drafts']) + 1}"
            self.state["drafts"].append(self.draft(next_id))
        return (
            self.project_path / "workspace.json",
            self.project_path / "workspace.md",
            "Workspace",
            self.state["version"],
        )

    def draft(self, draft_id: str) -> dict:
        return {
            "draft_id": draft_id,
            "relative_path": f"drafts/{draft_id}.md",
            "sha256": draft_id[-1] * 64,
        }

    def assessment_document(
        self,
        outcome: str,
        gap: str,
        *,
        draft_id: str | None,
        statuses: str = "needs_work",
        actions: list[str] | None = None,
        human_requests: list[str] | None = None,
    ) -> tuple[Path, dict, str]:
        self.evaluation_number += 1
        assessment_id = f"A{self.evaluation_number}"
        criteria = {
            name: {
                "status": statuses,
                "explanation": gap,
                "target_spine_sections": [],
                "branch_ids": [],
                "requirement_ids": [],
                "draft_ids": [draft_id] if draft_id else [],
            }
            for name in CRITERION_NAMES
        }
        document = {
            "assessment_id": assessment_id,
            "assessment_sha256": assessment_id.lower() * 32,
            "assessment": {
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "context_sha256": "c" * 64,
                "workspace_version": self.state["version"],
                "workspace_sha256": str(self.state["version"]) * 64,
                "draft_id": draft_id,
                "criteria": criteria,
                "outcome": outcome,
                "most_important_gap": gap,
                "required_next_actions": actions or [],
                "human_input_requests": human_requests or [],
                "research_request": (
                    {
                        "research_question": gap,
                        "why_required": gap,
                        "target_spine_sections": [
                            "evidence_and_reasoning"
                        ],
                        "branch_ids": ["BR1"],
                        "requirement_ids": ["REQ1"],
                        "desired_evidence": ["Primary or official evidence."],
                    }
                    if outcome == "continue_research"
                    else None
                ),
            },
        }
        path = (
            self.project_path
            / "data"
            / "books"
            / self.book_id
            / "assessments"
            / self.chapter_id
            / f"{assessment_id}.json"
        )
        return path, document, "Assessment"

    def research_side_effect(
        self,
        _backend: str,
        _program_path: str,
        _assessment_path: Path,
        assessment_document: dict,
        _research_mode: str,
        recovery_closeout: bool = False,
    ) -> tuple[Path, dict, str]:
        self.research_number += 1
        packet_id = f"R{self.research_number}"
        path = (
            self.project_path
            / "data"
            / "books"
            / self.book_id
            / "research"
            / self.chapter_id
            / f"{packet_id}.json"
        )
        document = {
            "research_packet_id": packet_id,
            "research_packet_sha256": "f" * 64,
            "research": {
                "assessment_id": assessment_document["assessment_id"],
                "assessment_sha256": assessment_document[
                    "assessment_sha256"
                ],
                "workspace_sha256": assessment_document["assessment"][
                    "workspace_sha256"
                ],
                "research_request": assessment_document["assessment"][
                    "research_request"
                ],
            },
        }
        return path, document, "Research"

    def controller_patches(self) -> tuple:
        return (
            patch.object(backend_selector, "PROJECT_PATH", self.project_path),
            patch.object(backend_selector, "BOOKS_PATH", self.books_path),
            patch.object(
                backend_selector,
                "load_chapter_workspace_state",
                side_effect=self.workspace_state,
            ),
            patch.object(
                backend_selector,
                "load_workspace_document",
                side_effect=self.workspace_document,
            ),
            patch.object(
                backend_selector,
                "matching_latest_assessment",
                return_value=None,
            ),
            patch.object(
                backend_selector,
                "develop_chapter_once",
                side_effect=self.develop_side_effect,
            ),
            patch.object(
                backend_selector,
                "research_chapter_once",
                side_effect=self.research_side_effect,
            ),
            patch.object(
                backend_selector,
                "load_research_mode",
                return_value="safe",
            ),
            patch.object(
                backend_selector,
                "load_chapter_research_source_plan",
                return_value=(
                    self.project_path / "source-plan.json",
                    {"source_plan_sha256": "a" * 64},
                ),
            ),
        )

    def test_alternates_until_new_final_pass_draft_is_complete(self) -> None:
        outcomes = iter(
            [
                self.assessment_document(
                    "ready_to_draft",
                    "The finite checklist is closed and needs its first draft.",
                    draft_id=None,
                    actions=["Create a grounded rough draft."],
                    statuses="satisfied",
                ),
                self.assessment_document(
                    "ready_for_final_pass",
                    "Only the final coherence pass remains.",
                    draft_id="D1",
                    statuses="satisfied",
                    actions=["Perform one final pass."],
                ),
                self.assessment_document(
                    "chapter_complete",
                    "No applicable gap remains.",
                    draft_id="D2",
                    statuses="satisfied",
                ),
            ]
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(result["developer_passes_total"], 3)
        self.assertEqual(result["evaluator_passes_total"], 3)
        self.assertEqual(result["researcher_passes_total"], 0)
        self.assertEqual(
            self.developer_final_flags,
            [False, False, True],
        )
        self.assertIsNone(self.developer_research_paths[0])
        self.assertIsNone(self.developer_research_paths[1])
        self.assertEqual(result["final_pass_source_draft_id"], "D1")

    def test_final_pass_human_question_remains_answerable(self) -> None:
        self.state["version"] = 3
        self.state["drafts"] = [self.draft("D1"), self.draft("D2")]
        question = "Approve the narrow artifact-authority interpretation?"
        human_assessment = self.assessment_document(
            "human_input_required",
            "The final interpretation requires human approval.",
            draft_id="D2",
            statuses="satisfied",
            human_requests=[question],
        )
        run = {
            "book_id": self.book_id,
            "chapter_id": self.chapter_id,
            "status": "final_pass_closeout",
            "developer_passes_total": 4,
            "evaluator_passes_total": 3,
            "researcher_passes_total": 0,
            "final_pass_requested": True,
            "final_closeout_source_draft_id": "D1",
            "final_closeout_baseline_agent_calls_total": 6,
            "final_closeout_developer_call_started_at": "started",
            "final_closeout_developer_call_completed_at": "completed",
            "final_closeout_evaluator_call_started_at": None,
            "final_closeout_evaluator_call_completed_at": None,
        }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=human_assessment,
                )
            )
            result = backend_selector.run_final_pass_closeout(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
                run,
            )

        self.assertEqual(result["status"], "human_input_required")
        self.assertEqual(
            result["last_assessment_outcome"],
            "human_input_required",
        )
        self.assertEqual(result["human_input_requests"], [question])

    def test_final_pass_answer_routes_to_no_research_closeout(self) -> None:
        question = "Approve the narrow artifact-authority interpretation?"
        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "human_input_required",
                    "developer_passes_total": 4,
                    "evaluator_passes_total": 4,
                    "final_pass_requested": True,
                    "final_closeout_evaluator_call_completed_at": "completed",
                    "last_assessment_id": "A4",
                    "last_assessment_sha256": "a" * 64,
                    "last_assessment_outcome": "human_input_required",
                    "human_input_requests": [question],
                }
            )
            backend_selector.save_chapter_run(run)
            path = backend_selector.record_chapter_human_input(
                self.book_id,
                self.chapter_id,
                ["Approved within the stated evidence boundary."],
                "Test authorization.",
            )
            routed = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        self.assertTrue(path.is_file())
        self.assertEqual(
            routed["status"],
            "human_input_closeout_required",
        )
        self.assertIn("six-call", routed["stop_reason"])

    def test_chapter_blocked_stops_without_another_developer_pass(
        self,
    ) -> None:
        blocked_assessment = self.assessment_document(
            "chapter_blocked",
            "The contracted mechanism cannot be bounded honestly.",
            draft_id=None,
            statuses="blocked",
            actions=["Record the blocker for human review."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=blocked_assessment,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "chapter_blocked")
        self.assertEqual(result["developer_passes_total"], 1)
        self.assertEqual(result["evaluator_passes_total"], 1)
        self.assertIn("cannot be bounded", result["stop_reason"])

    def test_final_closeout_has_its_own_bounded_two_call_budget(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1")]
        outcomes = iter(
            [
                self.assessment_document(
                    "ready_for_final_pass",
                    "Only the final coherence pass remains.",
                    draft_id="D1",
                    statuses="satisfied",
                    actions=["Perform one final pass."],
                ),
                self.assessment_document(
                    "chapter_complete",
                    "The final draft closes the chapter.",
                    draft_id="D2",
                    statuses="satisfied",
                ),
            ]
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 4
            run["evaluator_passes_total"] = 4
            run["researcher_passes_total"] = 3
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            backend_selector.MAX_CHAPTER_AGENT_CALLS_TOTAL + 2,
        )
        self.assertEqual(self.developer_final_flags, [True])
        self.assertIsNotNone(
            result["final_closeout_developer_call_started_at"]
        )
        self.assertIsNotNone(
            result["final_closeout_developer_call_completed_at"]
        )
        self.assertIsNotNone(
            result["final_closeout_evaluator_call_started_at"]
        )
        self.assertIsNotNone(
            result["final_closeout_evaluator_call_completed_at"]
        )

    def test_failed_final_developer_attempt_is_not_retried(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1")]
        ready = self.assessment_document(
            "ready_for_final_pass",
            "Only the final coherence pass remains.",
            draft_id="D1",
            statuses="satisfied",
            actions=["Perform one final pass."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=ready,
                )
            )
            developer = stack.enter_context(
                patch.object(
                    backend_selector,
                    "develop_chapter_once",
                    side_effect=RuntimeError("Provider failed."),
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "reconcile_pending_draft_receipt",
                    return_value=None,
                )
            )
            first = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            second = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        developer.assert_called_once()
        self.assertEqual(first["status"], "final_pass_failed")
        self.assertEqual(second["status"], "final_pass_failed")
        self.assertIn("consumed", second["stop_reason"])

    def test_authorized_final_pass_repair_is_exactly_two_calls(
        self,
    ) -> None:
        self.state["version"] = 3
        self.state["drafts"] = [self.draft("D1"), self.draft("D2")]
        complete = self.assessment_document(
            "chapter_complete",
            "The bounded correction closes the exact final-pass defect.",
            draft_id="D3",
            statuses="satisfied",
        )
        recovery = {
            "recovery_kind": "final_pass_revision",
            "baseline_agent_calls_total": 10,
            "max_recovery_agent_calls": 2,
            "assessment_path": (
                "data/books/book-controller-test/assessments/CH1/A5.json"
            ),
            "source_draft_id": "D2",
        }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "final_pass_failed",
                    "developer_passes_total": 5,
                    "evaluator_passes_total": 5,
                    "last_assessment_outcome": "revise_draft",
                }
            )
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=recovery,
                )
            )
            evaluator = stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=complete,
                )
            )
            first = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            second = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(first["status"], "chapter_complete")
        self.assertEqual(second["status"], "chapter_complete")
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(first),
            12,
        )
        self.assertEqual(self.developer_final_flags, [True])
        evaluator.assert_called_once()

    def test_final_closeout_reconciles_one_crash_saved_draft_receipt(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1")]
        complete = self.assessment_document(
            "chapter_complete",
            "The crash-saved final draft closes the chapter.",
            draft_id="D2",
            statuses="satisfied",
        )

        def reconcile(_book_id: str, _chapter_id: str) -> dict:
            self.state["version"] = 3
            self.state["drafts"].append(self.draft("D2"))
            return {
                "draft_id": "D2",
                "metadata_path": "drafts/D2.json",
                "metadata_sha256": "a" * 64,
                "draft_sha256": "2" * 64,
                "workspace_version": 3,
                "workspace_sha256": "3" * 64,
            }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "final_pass_closeout",
                    "developer_passes_total": 9,
                    "evaluator_passes_total": 6,
                    "researcher_passes_total": 2,
                    "final_pass_requested": True,
                    "final_pass_source_draft_id": "D1",
                    "final_closeout_baseline_agent_calls_total": 16,
                    "final_closeout_source_draft_id": "D1",
                    "final_closeout_developer_call_started_at": (
                        "2026-07-28T00:00:00+00:00"
                    ),
                }
            )
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value={
                        "baseline_agent_calls_total": 14,
                        "max_recovery_agent_calls": 4,
                    },
                )
            )
            reconciliation = stack.enter_context(
                patch.object(
                    backend_selector,
                    "reconcile_pending_draft_receipt",
                    side_effect=reconcile,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=complete,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "claude",
                "/usr/bin/claude",
                self.book_id,
                self.chapter_id,
            )

        reconciliation.assert_called_once_with(
            self.book_id,
            self.chapter_id,
        )
        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            18,
        )
        self.assertEqual(
            result["accounting_notes"][-1]["kind"],
            "pending_draft_receipt_reconciliation",
        )

    def test_failed_normal_developer_attempt_consumes_budget(
        self,
    ) -> None:
        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            developer = stack.enter_context(
                patch.object(
                    backend_selector,
                    "develop_chapter_once",
                    side_effect=RuntimeError("Incomplete transition."),
                )
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "Incomplete transition",
            ):
                backend_selector.run_chapter_completion_loop(
                    "codex",
                    "/usr/bin/codex",
                    self.book_id,
                    self.chapter_id,
                )
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        developer.assert_called_once()
        self.assertEqual(run["developer_passes_total"], 1)

    def test_revise_workspace_returns_to_developer_without_research(
        self,
    ) -> None:
        def revise_without_drafting(*_args, **_kwargs):
            self.state["version"] += 1
            return (
                self.project_path / "workspace.json",
                self.project_path / "workspace.md",
                "Workspace",
                self.state["version"],
            )

        outcomes = iter(
            [
                self.assessment_document(
                    "revise_workspace",
                    "Two existing requirements need terminal statuses.",
                    draft_id=None,
                    actions=[
                        "Close the requirements from existing evidence."
                    ],
                ),
                self.assessment_document(
                    "human_input_required",
                    "The user's interpretation is now required.",
                    draft_id=None,
                    actions=["Ask for the missing interpretation."],
                    human_requests=["Which interpretation should remain?"],
                ),
            ]
        )
        patches = list(self.controller_patches())
        patches[5] = patch.object(
            backend_selector,
            "develop_chapter_once",
            side_effect=revise_without_drafting,
        )

        with ExitStack() as stack:
            for controller_patch in patches:
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "human_input_required")
        self.assertEqual(result["developer_passes_total"], 2)
        self.assertEqual(result["researcher_passes_total"], 0)

    def test_preapproved_human_input_reopens_chapter_once(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1")]
        outcomes = iter(
            [
                self.assessment_document(
                    "ready_for_final_pass",
                    "The approved choice is now preserved.",
                    draft_id="D1",
                    statuses="satisfied",
                    actions=["Perform one final pass."],
                ),
                self.assessment_document(
                    "chapter_complete",
                    "The final draft closes the chapter.",
                    draft_id="D2",
                    statuses="satisfied",
                ),
            ]
        )
        decision_path = (
            self.books_path
            / self.book_id
            / "runs"
            / "CH1.A1.human-input.json"
        )
        human_document = {
            "assessment_id": "A1",
            "assessment_sha256": "a" * 64,
            "human_input_sha256": "h" * 64,
            "decisions": [
                {
                    "question": "Approve the project-owned name?",
                    "response": "Approved.",
                }
            ],
        }
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(
            json.dumps(human_document) + "\n",
            encoding="utf-8",
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["status"] = "human_input_required"
            run["last_assessment_id"] = "A1"
            run["last_assessment_path"] = (
                "data/books/book-controller-test/assessments/CH1/A1.json"
            )
            run["last_assessment_sha256"] = "a" * 64
            run["human_input_requests"] = [
                "Approve the project-owned name?"
            ]
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_human_input",
                    return_value=(decision_path, human_document),
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(
            self.developer_human_input_paths,
            [
                (
                    "data/books/book-controller-test/runs/"
                    "CH1.A1.human-input.json"
                ),
                None,
            ],
        )

    def test_editorial_revision_reopens_completed_chapter_then_revalidates(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1"), self.draft("D2")]
        run_path = (
            self.books_path
            / self.book_id
            / "runs"
            / f"{self.chapter_id}.json"
        )
        run_path.parent.mkdir(parents=True)
        editorial_path = (
            self.books_path
            / self.book_id
            / "editorial_history"
            / "v001"
            / "editorial_review.json"
        )
        editorial_path.parent.mkdir(parents=True)
        editorial_path.write_text("{}\n", encoding="utf-8")
        backend_selector.atomic_write_json(
            run_path,
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "status": "editorial_revision_requested",
                "started_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "developer_passes_total": 2,
                "evaluator_passes_total": 2,
                "researcher_passes_total": 0,
                "editorial_revision_cycles_total": 0,
                "editorial_review_path": (
                    "data/books/book-controller-test/"
                    "editorial_history/v001/editorial_review.json"
                ),
                "editorial_review_sha256": hashlib.sha256(
                    editorial_path.read_bytes()
                ).hexdigest(),
                "editorial_candidate_sha256": "a" * 64,
                "final_pass_requested": False,
                "final_pass_source_draft_id": None,
                "last_assessment_id": "A2",
                "last_assessment_path": None,
                "last_assessment_sha256": "b" * 64,
                "last_assessment_outcome": "chapter_complete",
                "last_assessment_signature": "c" * 64,
                "repeated_assessment_signature_count": 1,
                "last_research_packet_id": None,
                "last_research_packet_path": None,
                "last_research_packet_sha256": None,
                "stop_reason": None,
                "human_input_requests": [],
            },
        )
        outcomes = iter(
            [
                self.assessment_document(
                    "ready_for_final_pass",
                    "The editorial revision needs one final coherence pass.",
                    draft_id="D3",
                    statuses="satisfied",
                    actions=["Perform the final pass."],
                ),
                self.assessment_document(
                    "chapter_complete",
                    "No applicable gap remains.",
                    draft_id="D4",
                    statuses="satisfied",
                ),
            ]
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(result["editorial_revision_cycles_total"], 1)
        self.assertRegex(
            self.developer_editorial_paths[0],
            r"editorial_history/v001/editorial_review\.json$",
        )
        self.assertEqual(self.developer_final_flags, [False, True])
        self.assertEqual(self.state["drafts"][-1]["draft_id"], "D4")

    def test_recovers_saved_research_before_requesting_another_packet(
        self,
    ) -> None:
        research_assessment = self.assessment_document(
            "continue_research",
            "A saved packet still needs Developer integration.",
            draft_id=None,
            actions=["Integrate the saved evidence."],
        )
        human_assessment = self.assessment_document(
            "human_input_required",
            "The user's interpretation is now required.",
            draft_id="D1",
            actions=["Ask for the user's interpretation."],
            human_requests=["Which interpretation should be preserved?"],
        )
        outcomes = iter([research_assessment, human_assessment])
        research_path = (
            self.project_path
            / "data"
            / "books"
            / self.book_id
            / "research"
            / self.chapter_id
            / "R1.json"
        )
        research_document = {
            "research_packet_id": "R1",
            "research_packet_sha256": "f" * 64,
            "research": {
                "assessment_id": research_assessment[1]["assessment_id"],
                "assessment_sha256": research_assessment[1][
                    "assessment_sha256"
                ],
            },
        }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_latest_assessment",
                    side_effect=[
                        None,
                        research_assessment[:2],
                        None,
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_research_packet_for_assessment",
                    return_value=(research_path, research_document),
                )
            )
            researcher = stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    side_effect=AssertionError(
                        "Saved research should be recovered."
                    ),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        researcher.assert_not_called()
        self.assertEqual(result["status"], "human_input_required")
        self.assertEqual(result["researcher_passes_total"], 0)
        self.assertRegex(
            self.developer_research_paths[1],
            r"research/CH1/R1\.json$",
        )

    def test_no_packet_and_no_source_request_stops_as_incomplete(self) -> None:
        research_assessment = self.assessment_document(
            "continue_research",
            "The research call may need another pass after source approval.",
            draft_id=None,
            actions=["Retry using the saved source decisions."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=research_assessment,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    return_value=None,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "resolve_pending_chapter_source_requests",
                    return_value=False,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "research_incomplete")
        self.assertEqual(result["evaluator_passes_total"], 1)
        self.assertEqual(result["researcher_passes_total"], 1)

    def test_pending_source_decision_stops_without_research_loop(self) -> None:
        research_assessment = self.assessment_document(
            "continue_research",
            "One source permission decision is pending.",
            draft_id=None,
            actions=["Wait for the human source decision."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=research_assessment,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_or_create_chapter_research_session",
                    return_value={
                        "pending_requests": [{"request_id": "SRC1"}],
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "resolve_pending_chapter_source_requests",
                    return_value=False,
                )
            )
            researcher = stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    side_effect=AssertionError(
                        "Research must wait for source permission."
                    ),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        researcher.assert_not_called()
        self.assertEqual(result["status"], "source_approval_required")
        self.assertEqual(result["evaluator_passes_total"], 1)
        self.assertEqual(result["researcher_passes_total"], 0)

    def test_chapter_stops_after_two_research_packets(self) -> None:
        research_assessment = self.assessment_document(
            "continue_research",
            "Another supplemental packet was requested.",
            draft_id=None,
            actions=["Collect another source packet."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=research_assessment,
                )
            )
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["researcher_passes_total"] = 2
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "chapter_research_packet_paths",
                    return_value=[
                        self.project_path / "R1.json",
                        self.project_path / "R2.json",
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_research_packet_for_assessment",
                    return_value=None,
                )
            )
            researcher = stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    side_effect=AssertionError(
                        "Research must stop at the packet limit."
                    ),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        researcher.assert_not_called()
        self.assertEqual(result["status"], "research_limit_reached")
        self.assertEqual(result["researcher_passes_total"], 2)

    def test_research_attempts_do_not_consume_packet_budget(self) -> None:
        research_assessment = self.assessment_document(
            "continue_research",
            "A second durable packet is still needed.",
            draft_id=None,
            actions=["Collect the second source packet."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=research_assessment,
                )
            )
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["researcher_passes_total"] = 2
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "chapter_research_packet_paths",
                    return_value=[self.project_path / "R1.json"],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_research_packet_for_assessment",
                    return_value=None,
                )
            )
            researcher = stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    return_value=None,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "resolve_pending_chapter_source_requests",
                    return_value=False,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        researcher.assert_called_once()
        self.assertEqual(result["status"], "research_incomplete")
        self.assertEqual(result["researcher_passes_total"], 3)

    def test_lifetime_agent_call_limit_survives_resume(self) -> None:
        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 4
            run["evaluator_passes_total"] = 4
            run["researcher_passes_total"] = 4
            backend_selector.save_chapter_run(run)
            developer = stack.enter_context(
                patch.object(
                    backend_selector,
                    "develop_chapter_once",
                    side_effect=AssertionError(
                        "Resume must not reset the lifetime call limit."
                    ),
                )
            )
            evaluator = stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=AssertionError(
                        "Resume must not reset the lifetime call limit."
                    ),
                )
            )
            researcher = stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    side_effect=AssertionError(
                        "Resume must not reset the lifetime call limit."
                    ),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        developer.assert_not_called()
        evaluator.assert_not_called()
        researcher.assert_not_called()
        self.assertEqual(result["status"], "agent_call_limit_reached")
        self.assertIn("lifetime", result["stop_reason"])

    def test_batch_protocol_plans_before_research_packet(self) -> None:
        research_assessment = self.assessment_document(
            "continue_research",
            "One bounded source batch is required.",
            draft_id=None,
            actions=["Plan and check the bounded source batch."],
        )
        human_assessment = self.assessment_document(
            "human_input_required",
            "The user's interpretation is required.",
            draft_id="D1",
            actions=["Ask for the interpretation."],
            human_requests=["Which interpretation should remain?"],
        )
        outcomes = iter([research_assessment, human_assessment])
        source_plan = (
            self.project_path / "source-plan.json",
            {"source_plan_sha256": "a" * 64},
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=lambda *_args: next(outcomes),
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_latest_assessment",
                    side_effect=[
                        None,
                        research_assessment[:2],
                        None,
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_research_source_plan",
                    side_effect=[None, source_plan],
                )
            )
            planner = stack.enter_context(
                patch.object(
                    backend_selector,
                    "plan_chapter_research_sources_once",
                    return_value=source_plan,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        planner.assert_called_once()
        self.assertEqual(result["status"], "human_input_required")
        self.assertEqual(result["researcher_passes_total"], 2)

    def test_source_plan_does_not_start_without_closeout_reserve(self) -> None:
        self.state["version"] = 1
        assessment = self.assessment_document(
            "continue_research",
            "Research would consume the reserved closeout budget.",
            draft_id=None,
            actions=["Do not begin an unfinishable research cycle."],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 3
            run["evaluator_passes_total"] = 2
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=assessment,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_research_source_plan",
                    return_value=None,
                )
            )
            planner = stack.enter_context(
                patch.object(
                    backend_selector,
                    "plan_chapter_research_sources_once",
                    side_effect=AssertionError(
                        "The closeout reserve must be protected."
                    ),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        planner.assert_not_called()
        self.assertEqual(result["status"], "insufficient_closeout_budget")

    def test_recovery_uses_separate_budget_and_forbids_fetch_mode(
        self,
    ) -> None:
        self.state["version"] = 1
        research_assessment = self.assessment_document(
            "continue_research",
            "Close the existing research trail without another fetch.",
            draft_id=None,
            actions=["Submit the bounded recovery packet."],
        )
        human_assessment = self.assessment_document(
            "human_input_required",
            "The user's interpretation is required.",
            draft_id="D1",
            actions=["Ask for the interpretation."],
            human_requests=["Which interpretation should remain?"],
        )
        recovery_flags = []

        def recovery_research(
            _backend,
            _program_path,
            _assessment_path,
            assessment_document,
            _research_mode,
            recovery_closeout=False,
        ):
            recovery_flags.append(recovery_closeout)
            return self.research_side_effect(
                _backend,
                _program_path,
                _assessment_path,
                assessment_document,
                _research_mode,
            )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 4
            run["evaluator_passes_total"] = 4
            run["researcher_passes_total"] = 4
            run["status"] = "agent_call_limit_reached"
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value={
                        "baseline_agent_calls_total": 12,
                        "max_recovery_agent_calls": 7,
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_latest_assessment",
                    side_effect=[
                        research_assessment[:2],
                        None,
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    side_effect=recovery_research,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=human_assessment,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(recovery_flags, [True])
        self.assertEqual(result["status"], "human_input_required")
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            15,
        )

    def test_pending_draft_recovery_includes_final_closeout_in_four_calls(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1")]
        revise = self.assessment_document(
            "revise_draft",
            "Register the already saved revision.",
            draft_id="D1",
            actions=["Register the pending D2 receipt."],
        )
        evaluator_outcomes = iter(
            [
                (
                    "ready_for_final_pass",
                    "Only the bounded final pass remains.",
                    "D2",
                ),
                (
                    "chapter_complete",
                    "The final draft closes the chapter.",
                    "D3",
                ),
            ]
        )

        def recover_pending_draft(*args, **kwargs):
            result = self.develop_side_effect(*args, **kwargs)
            if not kwargs.get("final_pass_requested", False):
                self.state["drafts"].append(self.draft("D2"))
            return result

        def evaluate_recovery(*_args):
            outcome, gap, draft_id = next(evaluator_outcomes)
            return self.assessment_document(
                outcome,
                gap,
                draft_id=draft_id,
                statuses="satisfied",
            )

        patches = list(self.controller_patches())
        patches[5] = patch.object(
            backend_selector,
            "develop_chapter_once",
            side_effect=recover_pending_draft,
        )
        with ExitStack() as stack:
            for controller_patch in patches:
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 7
            run["evaluator_passes_total"] = 5
            run["researcher_passes_total"] = 2
            run["status"] = "agent_call_limit_reached"
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value={
                        "baseline_agent_calls_total": 14,
                        "max_recovery_agent_calls": 4,
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "matching_latest_assessment",
                    side_effect=[
                        revise[:2],
                        None,
                        None,
                    ],
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=evaluate_recovery,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            18,
        )
        self.assertEqual(
            self.developer_final_flags,
            [False, True],
        )

    def test_assessment_extension_allows_exactly_one_final_evaluation(
        self,
    ) -> None:
        self.state["version"] = 2
        self.state["drafts"] = [self.draft("D1"), self.draft("D2")]
        complete_assessment = self.assessment_document(
            "chapter_complete",
            "The newer final-pass draft closes the chapter.",
            draft_id="D2",
            statuses="satisfied",
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 5
            run["evaluator_passes_total"] = 4
            run["researcher_passes_total"] = 12
            run["status"] = "recovery_call_limit_reached"
            run["final_pass_requested"] = True
            run["final_pass_source_draft_id"] = "D1"
            run["last_assessment_outcome"] = "ready_for_final_pass"
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value={
                        "baseline_agent_calls_total": 14,
                        "max_recovery_agent_calls": 7,
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_assessment_extension",
                    return_value={
                        "baseline_agent_calls_total": 21,
                        "max_calls": 1,
                    },
                )
            )
            evaluator = stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=complete_assessment,
                )
            )
            developer = stack.enter_context(
                patch.object(
                    backend_selector,
                    "develop_chapter_once",
                    side_effect=AssertionError(
                        "Assessment extension cannot develop the chapter."
                    ),
                )
            )
            researcher = stack.enter_context(
                patch.object(
                    backend_selector,
                    "research_chapter_once",
                    side_effect=AssertionError(
                        "Assessment extension cannot research."
                    ),
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        evaluator.assert_called_once()
        developer.assert_not_called()
        researcher.assert_not_called()
        self.assertEqual(result["status"], "chapter_complete")
        self.assertEqual(result["evaluator_passes_total"], 5)
        self.assertIsNotNone(
            result["assessment_extension_call_started_at"]
        )
        self.assertIsNotNone(
            result["assessment_extension_call_completed_at"]
        )

    def test_repeated_assessment_stops_as_no_progress(self) -> None:
        def develop_without_draft(*args, **kwargs):
            self.developer_final_flags.append(
                kwargs.get("final_pass_requested", False)
            )
            self.state["version"] += 1
            return (
                self.project_path / "workspace.json",
                self.project_path / "workspace.md",
                "Workspace",
                self.state["version"],
            )

        def same_assessment(*_args):
            return self.assessment_document(
                "continue_research",
                "The same mechanism remains unexplained.",
                draft_id=None,
                actions=["Explain the mechanism."],
            )

        patches = list(self.controller_patches())
        patches[5] = patch.object(
            backend_selector,
            "develop_chapter_once",
            side_effect=develop_without_draft,
        )
        with ExitStack() as stack:
            for controller_patch in patches:
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    side_effect=same_assessment,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "claude",
                "/usr/bin/claude",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "stalled")
        self.assertEqual(
            result["repeated_assessment_signature_count"],
            2,
        )
        self.assertIn("repeated", result["stop_reason"].lower())

    def test_human_input_outcome_stops_without_another_developer_pass(
        self,
    ) -> None:
        human_assessment = self.assessment_document(
            "human_input_required",
            "The user's interpretation is missing.",
            draft_id=None,
            actions=["Ask the user for their interpretation."],
            human_requests=[
                "Which interpretation should the chapter preserve?"
            ],
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=human_assessment,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "human_input_required")
        self.assertEqual(result["developer_passes_total"], 1)
        self.assertEqual(len(result["human_input_requests"]), 1)

    def test_research_access_recovery_activates_once_then_stops(
        self,
    ) -> None:
        self.state["version"] = 2
        self.evaluation_number = 2
        blocked_assessment = self.assessment_document(
            "chapter_blocked",
            "The alternative source batch cannot establish the claim.",
            draft_id=None,
        )
        recovery = {
            "recovery_kind": "research_access_retry",
            "baseline_agent_calls_total": 6,
            "max_recovery_agent_calls": 10,
            "workspace_version": 2,
            "workspace_sha256": "2" * 64,
            "assessment_path": (
                f"data/books/{self.book_id}/assessments/"
                f"{self.chapter_id}/A2.json"
            ),
            "baseline_research_packet_count": 1,
            "baseline_source_plan_count": 1,
            "max_new_research_packets": 1,
            "max_new_source_plans": 1,
        }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 2
            run["evaluator_passes_total"] = 2
            run["researcher_passes_total"] = 2
            run["status"] = "chapter_blocked"
            run["last_assessment_outcome"] = "chapter_blocked"
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=recovery,
                )
            )
            evaluator = stack.enter_context(
                patch.object(
                    backend_selector,
                    "evaluate_chapter_once",
                    return_value=blocked_assessment,
                )
            )
            first = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            second = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        evaluator.assert_called_once()
        self.assertEqual(first["status"], "research_access_recovery_failed")
        self.assertEqual(second["status"], "research_access_recovery_failed")
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(first),
            8,
        )
        self.assertEqual(
            self.developer_recovery_paths,
            [
                (
                    f"data/books/{self.book_id}/runs/"
                    f"{self.chapter_id}.recovery.json"
                )
            ],
        )
        self.assertIsNotNone(
            first["research_access_activation_call_completed_at"]
        )

    def test_recorded_recovery_decision_waits_for_closeout_authorization(
        self,
    ) -> None:
        self.state["version"] = 2
        question = "Approve the bounded V1 observations?"
        recovery = {
            "recovery_kind": "research_access_retry",
            "baseline_agent_calls_total": 6,
            "max_recovery_agent_calls": 10,
            "workspace_version": 2,
            "workspace_sha256": "2" * 64,
            "assessment_path": (
                f"data/books/{self.book_id}/assessments/"
                f"{self.chapter_id}/A5.json"
            ),
            "baseline_research_packet_count": 1,
            "baseline_source_plan_count": 1,
            "max_new_research_packets": 1,
            "max_new_source_plans": 1,
        }
        human_input = (
            self.project_path / "human-input.json",
            {
                "assessment_id": "A5",
                "assessment_sha256": "a" * 64,
                "human_input_sha256": "b" * 64,
                "decisions": [
                    {
                        "question": question,
                        "response": "Approved.",
                    }
                ],
            },
        )

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "developer_passes_total": 5,
                    "evaluator_passes_total": 5,
                    "researcher_passes_total": 4,
                    "status": "human_input_required",
                    "last_assessment_id": "A5",
                    "last_assessment_sha256": "a" * 64,
                    "last_assessment_outcome": "human_input_required",
                    "human_input_requests": [question],
                    "research_access_activation_call_started_at": "started",
                    "research_access_activation_call_completed_at": (
                        "completed"
                    ),
                }
            )
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=recovery,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_human_input",
                    return_value=human_input,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(
            result["status"],
            "human_input_closeout_required",
        )
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            14,
        )
        self.assertEqual(self.developer_recovery_paths, [])

    def test_human_input_closeout_stops_at_its_six_call_limit(
        self,
    ) -> None:
        self.state["version"] = 2
        recovery = {
            "recovery_kind": "research_access_retry",
            "baseline_agent_calls_total": 6,
            "max_recovery_agent_calls": 10,
            "workspace_version": 2,
            "workspace_sha256": "2" * 64,
        }
        closeout = {
            "baseline_agent_calls_total": 14,
            "max_calls": 6,
        }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "developer_passes_total": 7,
                    "evaluator_passes_total": 7,
                    "researcher_passes_total": 6,
                    "status": "active",
                    "research_access_activation_call_started_at": "started",
                    "research_access_activation_call_completed_at": (
                        "completed"
                    ),
                }
            )
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=recovery,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_human_input_closeout",
                    return_value=closeout,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(
            result["status"],
            "human_input_closeout_failed",
        )
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            20,
        )

    def test_closeout_checkpoint_precedes_exhausted_recovery_limit(
        self,
    ) -> None:
        self.state["version"] = 2
        recovery = {
            "recovery_kind": "research_access_retry",
            "baseline_agent_calls_total": 6,
            "max_recovery_agent_calls": 10,
            "workspace_version": 2,
            "workspace_sha256": "2" * 64,
        }

        with ExitStack() as stack:
            for controller_patch in self.controller_patches():
                stack.enter_context(controller_patch)
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "developer_passes_total": 6,
                    "evaluator_passes_total": 6,
                    "researcher_passes_total": 4,
                    "status": "human_input_closeout_required",
                    "last_assessment_outcome": "ready_to_draft",
                    "last_human_input_path": "decision.json",
                    "research_access_activation_call_started_at": "started",
                    "research_access_activation_call_completed_at": (
                        "completed"
                    ),
                }
            )
            backend_selector.save_chapter_run(run)
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_recovery_authorization",
                    return_value=recovery,
                )
            )
            stack.enter_context(
                patch.object(
                    backend_selector,
                    "load_chapter_human_input_closeout",
                    return_value=None,
                )
            )
            result = backend_selector.run_chapter_completion_loop(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(
            result["status"],
            "human_input_closeout_required",
        )
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(result),
            16,
        )


class ChapterRecoveryAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.books_path = self.project_path / "data" / "books"
        self.book_id = "book-recovery-authorization"
        self.chapter_id = "CH2"
        self.book_directory = self.books_path / self.book_id

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_json(self, path: Path, document: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_human_input_closeout_checkpoint_uses_carried_decision(
        self,
    ) -> None:
        expected_path = (
            self.book_directory
            / "runs"
            / f"{self.chapter_id}.A5.human-closeout.json"
        )
        run = {
            "status": "human_input_closeout_required",
            "last_assessment_id": "A6",
        }
        human_input = (
            self.book_directory
            / "runs"
            / f"{self.chapter_id}.A5.human-input.json",
            {
                "assessment_id": "A5",
                "assessment_sha256": "a" * 64,
            },
        )
        with (
            patch.object(
                backend_selector,
                "load_or_create_chapter_run",
                return_value=run,
            ),
            patch.object(
                backend_selector,
                "load_run_chapter_human_input",
                return_value=human_input,
            ),
            patch.object(
                backend_selector,
                "authorize_chapter_human_input_closeout",
                return_value=expected_path,
            ) as authorize,
            patch("builtins.input", return_value="1"),
        ):
            result = (
                backend_selector
                .record_chapter_human_input_closeout_checkpoint(
                    self.book_id,
                    self.chapter_id,
                )
            )

        self.assertEqual(result, expected_path)
        authorize.assert_called_once()

    def test_final_pass_human_closeout_binds_draft_without_recovery(
        self,
    ) -> None:
        assessment_id = "A4"
        question = "Approve the narrow commitment wording?"
        assessment_path = (
            self.book_directory
            / "assessments"
            / self.chapter_id
            / f"{assessment_id}.json"
        )
        workspace_path = (
            self.book_directory
            / "workspaces"
            / self.chapter_id
            / "versions"
            / "v004.json"
        )
        draft_path = (
            self.book_directory
            / "drafts"
            / self.chapter_id
            / "D2.md"
        )
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text("Final draft\n", encoding="utf-8")
        self.write_json(
            assessment_path,
            {
                "assessment_id": assessment_id,
                "assessment_sha256": "a" * 64,
                "assessment": {
                    "outcome": "human_input_required",
                    "draft_id": "D2",
                },
            },
        )
        self.write_json(workspace_path, {"workspace": "bound"})
        workspace_state = {
            "current_version": 4,
            "current_json_path": str(
                workspace_path.relative_to(self.project_path)
            ),
        }
        workspace_document = {
            "workspace_sha256": "w" * 64,
            "workspace": {
                "draft_versions": [
                    {
                        "draft_id": "D2",
                        "relative_path": str(
                            draft_path.relative_to(self.project_path)
                        ),
                        "sha256": backend_selector.file_sha256(
                            draft_path
                        ),
                    }
                ]
            },
        }

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.books_path,
            ),
            patch.object(
                backend_selector,
                "load_chapter_workspace_state",
                return_value=workspace_state,
            ),
            patch.object(
                backend_selector,
                "load_workspace_document",
                return_value=workspace_document,
            ),
            patch.object(
                backend_selector,
                "load_chapter_recovery_authorization",
                return_value=None,
            ),
        ):
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "human_input_required",
                    "developer_passes_total": 4,
                    "evaluator_passes_total": 4,
                    "final_pass_requested": True,
                    "final_closeout_evaluator_call_completed_at": "completed",
                    "last_assessment_id": assessment_id,
                    "last_assessment_path": str(
                        assessment_path.relative_to(self.project_path)
                    ),
                    "last_assessment_sha256": "a" * 64,
                    "last_assessment_outcome": "human_input_required",
                    "human_input_requests": [question],
                }
            )
            backend_selector.save_chapter_run(run)
            backend_selector.record_chapter_human_input(
                self.book_id,
                self.chapter_id,
                ["Approved within the stated evidence boundary."],
                "Test authorization.",
            )
            closeout_path = (
                backend_selector.authorize_chapter_human_input_closeout(
                    self.book_id,
                    self.chapter_id,
                    "Approve one bounded no-research closeout.",
                )
            )
            loaded = backend_selector.load_chapter_human_input_closeout(
                self.book_id,
                self.chapter_id,
            )

        self.assertTrue(closeout_path.is_file())
        self.assertEqual(
            loaded["authority_kind"],
            "final_pass_human_input",
        )
        self.assertEqual(loaded["source_final_draft_id"], "D2")
        self.assertEqual(loaded["baseline_agent_calls_total"], 8)
        self.assertEqual(loaded["max_calls"], 6)

    def test_revision_recovery_binds_pending_draft_without_research_session(
        self,
    ) -> None:
        workspace_path = (
            self.book_directory
            / "workspaces"
            / self.chapter_id
            / "versions"
            / "v005.json"
        )
        workspace_document = {
            "schema_version": "1.0",
            "workspace_version": 5,
            "workspace": {
                "draft_versions": [{"draft_id": "D1"}],
            },
        }
        workspace_document["workspace_sha256"] = (
            backend_selector.canonical_json_sha256(
                workspace_document,
                "workspace_sha256",
            )
        )
        self.write_json(workspace_path, workspace_document)
        workspace_state = {
            "current_version": 5,
            "current_json_path": str(
                workspace_path.relative_to(self.project_path)
            ),
        }

        assessment_path = (
            self.book_directory
            / "assessments"
            / self.chapter_id
            / "A5.json"
        )
        assessment_document = {
            "schema_version": "1.0",
            "assessment_id": "A5",
            "assessment": {
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "outcome": "revise_draft",
            },
        }
        assessment_document["assessment_sha256"] = (
            backend_selector.canonical_json_sha256(
                assessment_document,
                "assessment_sha256",
            )
        )
        self.write_json(assessment_path, assessment_document)

        draft_directory = (
            self.book_directory / "drafts" / self.chapter_id
        )
        draft_directory.mkdir(parents=True)
        draft_path = draft_directory / "D2.md"
        draft_path.write_text("# Recovered draft\n", encoding="utf-8")
        metadata_path = draft_directory / "D2.json"
        self.write_json(
            metadata_path,
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "draft_id": "D2",
                "relative_path": str(
                    draft_path.relative_to(self.project_path)
                ),
                "sha256": backend_selector.file_sha256(draft_path),
                "source_workspace_version": 5,
                "source_workspace_sha256": workspace_document[
                    "workspace_sha256"
                ],
            },
        )

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.books_path,
            ),
            patch.object(
                backend_selector,
                "load_chapter_workspace_state",
                return_value=workspace_state,
            ),
        ):
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "agent_call_limit_reached",
                    "developer_passes_total": 7,
                    "evaluator_passes_total": 5,
                    "researcher_passes_total": 2,
                    "last_assessment_id": "A5",
                    "last_assessment_path": str(
                        assessment_path.relative_to(self.project_path)
                    ),
                    "last_assessment_sha256": assessment_document[
                        "assessment_sha256"
                    ],
                    "last_assessment_outcome": "revise_draft",
                }
            )
            backend_selector.save_chapter_run(run)
            recovery_path = backend_selector.authorize_chapter_recovery(
                self.book_id,
                self.chapter_id,
                "Approve one bounded pending-draft recovery.",
            )
            recovery = json.loads(
                recovery_path.read_text(encoding="utf-8")
            )
            loaded = (
                backend_selector.load_chapter_recovery_authorization(
                    self.book_id,
                    self.chapter_id,
                )
            )
            self.assertEqual(
                loaded["recovery_authorization_sha256"],
                recovery["recovery_authorization_sha256"],
            )
            draft_path.write_text("# Changed draft\n", encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimeError,
                "changed after approval",
            ):
                backend_selector.load_chapter_recovery_authorization(
                    self.book_id,
                    self.chapter_id,
                )

        self.assertEqual(
            recovery["recovery_kind"],
            "pending_draft_revision",
        )
        self.assertEqual(recovery["max_recovery_agent_calls"], 4)
        self.assertFalse(recovery["assessment_extension_allowed"])
        self.assertEqual(recovery["pending_drafts"][0]["draft_id"], "D2")
        self.assertNotIn("source_session_path", recovery)

    def test_research_access_recovery_binds_blocked_evidence_chain(
        self,
    ) -> None:
        workspace_path = (
            self.book_directory
            / "workspaces"
            / self.chapter_id
            / "versions"
            / "v002.json"
        )
        workspace_document = {
            "schema_version": "1.0",
            "workspace_version": 2,
            "workspace": {"draft_versions": []},
        }
        workspace_document["workspace_sha256"] = (
            backend_selector.canonical_json_sha256(
                workspace_document,
                "workspace_sha256",
            )
        )
        self.write_json(workspace_path, workspace_document)
        workspace_state = {
            "current_version": 2,
            "current_json_path": str(
                workspace_path.relative_to(self.project_path)
            ),
        }
        research_request = {
            "research_question": "Which tested formula limits apply?",
            "why_required": "The contracted lesson needs direct evidence.",
            "target_spine_sections": ["evidence_and_reasoning"],
            "branch_ids": ["BR1"],
            "requirement_ids": ["REQ1"],
            "desired_evidence": ["Primary validation evidence."],
        }
        assessment_directory = (
            self.book_directory / "assessments" / self.chapter_id
        )
        source_assessment_path = assessment_directory / "A1.json"
        source_assessment = {
            "schema_version": "1.0",
            "assessment_id": "A1",
            "assessment": {
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "outcome": "continue_research",
                "research_request": research_request,
            },
        }
        source_assessment["assessment_sha256"] = (
            backend_selector.canonical_json_sha256(
                source_assessment,
                "assessment_sha256",
            )
        )
        self.write_json(source_assessment_path, source_assessment)

        packet_path = (
            self.book_directory
            / "research"
            / self.chapter_id
            / "R1.json"
        )
        packet = {
            "schema_version": "1.0",
            "research_packet_id": "R1",
            "research": {
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "assessment_id": "A1",
                "assessment_sha256": source_assessment[
                    "assessment_sha256"
                ],
                "research_request": research_request,
            },
        }
        packet["research_packet_sha256"] = (
            backend_selector.canonical_json_sha256(
                packet,
                "research_packet_sha256",
            )
        )
        self.write_json(packet_path, packet)
        source_plan_path = (
            self.book_directory
            / "research"
            / self.chapter_id
            / "source_plans"
            / "A1.json"
        )
        self.write_json(source_plan_path, {"assessment_id": "A1"})

        blocking_assessment_path = assessment_directory / "A2.json"
        blocking_assessment = {
            "schema_version": "1.0",
            "assessment_id": "A2",
            "assessment": {
                "book_id": self.book_id,
                "chapter_id": self.chapter_id,
                "workspace_version": 2,
                "workspace_sha256": workspace_document[
                    "workspace_sha256"
                ],
                "outcome": "chapter_blocked",
                "research_request": None,
                "human_input_requests": [],
            },
        }
        blocking_assessment["assessment_sha256"] = (
            backend_selector.canonical_json_sha256(
                blocking_assessment,
                "assessment_sha256",
            )
        )
        self.write_json(blocking_assessment_path, blocking_assessment)

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.books_path,
            ),
            patch.object(
                backend_selector,
                "load_chapter_workspace_state",
                return_value=workspace_state,
            ),
        ):
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "chapter_blocked",
                    "developer_passes_total": 2,
                    "evaluator_passes_total": 2,
                    "researcher_passes_total": 2,
                    "last_assessment_id": "A2",
                    "last_assessment_path": str(
                        blocking_assessment_path.relative_to(
                            self.project_path
                        )
                    ),
                    "last_assessment_sha256": blocking_assessment[
                        "assessment_sha256"
                    ],
                    "last_assessment_outcome": "chapter_blocked",
                    "last_research_packet_id": "R1",
                    "last_research_packet_path": str(
                        packet_path.relative_to(self.project_path)
                    ),
                    "last_research_packet_sha256": packet[
                        "research_packet_sha256"
                    ],
                }
            )
            backend_selector.save_chapter_run(run)
            recovery_path = (
                backend_selector.authorize_research_access_recovery(
                    self.book_id,
                    self.chapter_id,
                    "Approve one alternative source batch.",
                )
            )
            recovery = (
                backend_selector.load_chapter_recovery_authorization(
                    self.book_id,
                    self.chapter_id,
                )
            )
            packet_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                RuntimeError,
                "changed after approval",
            ):
                backend_selector.load_chapter_recovery_authorization(
                    self.book_id,
                    self.chapter_id,
                )

        self.assertEqual(
            recovery["recovery_kind"],
            "research_access_retry",
        )
        self.assertEqual(recovery["baseline_agent_calls_total"], 6)
        self.assertEqual(recovery["max_recovery_agent_calls"], 10)
        self.assertEqual(recovery["baseline_research_packet_count"], 1)
        self.assertEqual(recovery["baseline_source_plan_count"], 1)
        self.assertFalse(recovery["no_new_source_fetches"])
        self.assertTrue(recovery_path.is_file())


if __name__ == "__main__":
    unittest.main()
