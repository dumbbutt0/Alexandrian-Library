import tempfile
import unittest
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
        self.state["version"] += 1
        if self.state["version"] == 2:
            self.state["drafts"] = [self.draft("D1")]
        if final_pass_requested:
            self.state["drafts"].append(self.draft("D2"))
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
                        "branch_ids": [],
                        "requirement_ids": [],
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
        )

    def test_alternates_until_new_final_pass_draft_is_complete(self) -> None:
        outcomes = iter(
            [
                self.assessment_document(
                    "continue_research",
                    "The lesson needs a first bounded draft.",
                    draft_id=None,
                    actions=["Create a grounded rough draft."],
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
        self.assertEqual(result["researcher_passes_total"], 1)
        self.assertEqual(
            self.developer_final_flags,
            [False, False, True],
        )
        self.assertIsNone(self.developer_research_paths[0])
        self.assertRegex(
            self.developer_research_paths[1],
            r"research/CH1/R1\.json$",
        )
        self.assertEqual(result["final_pass_source_draft_id"], "D1")

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


if __name__ == "__main__":
    unittest.main()
