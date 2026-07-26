import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import backend_selector
import mcp_server
from mcp.server.fastmcp.exceptions import ToolError


class BookPlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name)
        self.conversations_path = self.project_path / "data" / "conversations"
        self.reviews_path = self.project_path / "data" / "reviews"
        self.review_sessions_path = (
            self.project_path / "data" / "review_sessions"
        )
        self.review_decisions_path = (
            self.project_path / "data" / "review_decisions"
        )
        self.books_path = self.project_path / "data" / "books"
        self.source_policy_path = (
            self.project_path / "config" / "source_policy.json"
        )
        self.conversations_path.mkdir(parents=True)
        self.source_policy_path.parent.mkdir(parents=True)
        self.source_policy_path.write_text(
            json.dumps(
                {
                    "approved_domains": [],
                    "allow_subdomains": False,
                    "max_response_bytes": 10000,
                    "max_text_characters": 5000,
                    "timeout_seconds": 1,
                }
            ),
            encoding="utf-8",
        )

        mcp_server.PROJECT_PATH = self.project_path
        mcp_server.CONVERSATIONS_PATH = self.conversations_path
        mcp_server.REVIEWS_PATH = self.reviews_path
        mcp_server.REVIEW_SESSIONS_PATH = self.review_sessions_path
        mcp_server.REVIEW_DECISIONS_PATH = self.review_decisions_path
        mcp_server.BOOKS_PATH = self.books_path
        mcp_server.SOURCE_POLICY_PATH = self.source_policy_path
        mcp_server.SOURCE_RECEIPTS.clear()
        os.environ["ALEXANDRIAN_AGENT_HOST"] = "test-planner"

    def tearDown(self) -> None:
        os.environ.pop("ALEXANDRIAN_AGENT_HOST", None)
        mcp_server.SOURCE_RECEIPTS.clear()
        self.temporary_directory.cleanup()

    def write_finished_conversation(self, conversation_id: str) -> None:
        events = [
            {
                "type": "conversation_started",
                "conversation_id": conversation_id,
            },
            {
                "type": "message",
                "role": "user",
                "provider": "codex",
                "content": "How can evidence become a trustworthy book?",
            },
            {
                "type": "message",
                "role": "assistant",
                "provider": "codex",
                "content": "Preserve provenance before drafting prose.",
            },
            {
                "type": "conversation_finished",
                "status": "ready_for_review",
            },
        ]
        (self.conversations_path / f"{conversation_id}.jsonl").write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def make_ready_review(
        self,
        conversation_id: str,
        claim_status: str = "user_belief",
        evidence_gap: bool = False,
    ) -> mcp_server.ResearchReview:
        core_question = {
            "text": "How can evidence become a trustworthy book?",
            "transcript_references": ["T1"],
        }
        return mcp_server.ResearchReview.model_validate(
            {
                "conversation_id": conversation_id,
                "core_question": core_question,
                "scope": {
                    "included_topics": [core_question],
                    "excluded_topics": [],
                    "key_definitions": [],
                },
                "starting_model": {
                    "beliefs": [core_question],
                    "demonstrated_understanding": [],
                    "misunderstandings": [],
                },
                "belief_revisions": [],
                "claims": [
                    {
                        "claim": "Provenance should be preserved before prose.",
                        "origin": "joint",
                        "status": claim_status,
                        "transcript_references": ["T1", "T2"],
                        "source_receipt_ids": [],
                        "next_verification": "Test the planner contract.",
                    }
                ],
                "mentioned_sources": [],
                "checked_sources": [],
                "research_trail": [],
                "evidence_gaps": [core_question] if evidence_gap else [],
                "connected_ideas": [],
                "separate_branches": [],
                "thought_questions": [core_question],
                "experiments": [],
                "use_case_candidates": [],
                "research_route": "ready_for_book_consideration",
                "route_reason": core_question,
                "recommended_next_step": "Create a book plan.",
            }
        )

    def create_eligible_review(
        self,
        conversation_id: str,
        claim_status: str = "user_belief",
        evidence_gap: bool = False,
    ) -> str:
        self.write_finished_conversation(conversation_id)
        receipt = mcp_server.submit_research_review(
            self.make_ready_review(
                conversation_id,
                claim_status,
                evidence_gap,
            )
        )
        self.review_decisions_path.mkdir(parents=True)
        (self.review_decisions_path / f"{conversation_id}.json").write_text(
            json.dumps(
                {
                    "review_id": receipt.review_id,
                    "conversation_id": conversation_id,
                    "decided_at": "2026-01-01T00:00:00+00:00",
                    "decision": "approved_for_book_planning",
                    "review_research_route": "ready_for_book_consideration",
                    "book_planning_gate": "ready",
                    "confirmed_core_question_or_thesis": (
                        "How can evidence become a trustworthy book?"
                    ),
                    "accepted_belief_revisions": [],
                    "disputed_points": [],
                    "excluded_material": [],
                }
            ),
            encoding="utf-8",
        )
        return receipt.review_id

    def make_proposal(
        self,
        review_id: str,
        claim_ids: list[str] | None = None,
        readiness: str = "ready_to_draft",
        missing_work: list[str] | None = None,
        contradictions: list[dict] | None = None,
        research_gaps: list[dict] | None = None,
    ) -> mcp_server.BookManifestProposal:
        human_contribution = (
            ["Explain why this question matters personally."]
            if readiness == "user_reflection_required"
            else []
        )
        return mcp_server.BookManifestProposal.model_validate(
            {
                "working_title": "From Evidence to Book",
                "book_mode": "exploratory",
                "central_question": (
                    "How can evidence become a trustworthy book?"
                ),
                "provisional_thesis": None,
                "input_review_ids": [review_id],
                "intended_reader": "A learner building research tools",
                "assumed_knowledge": ["Basic command-line use"],
                "learning_outcomes": [
                    "Explain why provenance must precede drafting."
                ],
                "prohibited_overinterpretations": [],
                "scope": ["Evidence-grounded book planning"],
                "exclusions": [],
                "narrative_arc": (
                    "Move from the initial question to a testable planning model."
                ),
                "contradictions": contradictions or [],
                "research_gaps": research_gaps or [],
                "separate_book_candidates": [],
                "human_authorship_requirements": [
                    "The user writes the personal motivation."
                ],
                "chapter_plan": [
                    {
                        "chapter_id": "CH1",
                        "working_title": "Why Provenance Comes First",
                        "purpose": "Establish the planning boundary.",
                        "reader_question": (
                            "Why should a book plan precede prose?"
                        ),
                        "transition_reason": "This opens the book.",
                        "dependencies": [],
                        "claim_ids": claim_ids or [],
                        "evidence_requirements": [],
                        "evidence_source_receipt_ids": [],
                        "belief_revision_transcript_references": [],
                        "human_contribution_required": human_contribution,
                        "readiness": readiness,
                        "missing_work": missing_work or [],
                        "material_disposition": "include",
                    }
                ],
            }
        )

    def test_review_receives_deterministic_claim_identity(self) -> None:
        conversation_id = "claim-identity"
        review_id = self.create_eligible_review(conversation_id)
        review_document = json.loads(
            (self.reviews_path / f"{conversation_id}.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            review_document["claim_index"][0]["claim_id"],
            f"{review_id}:C1",
        )

    def test_book_tool_writes_manifest_and_outline_without_prose(self) -> None:
        conversation_id = "successful-book-plan"
        review_id = self.create_eligible_review(conversation_id)
        proposal = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
        )

        receipt = mcp_server.submit_book_manifest(proposal)
        manifest_path = self.project_path / receipt.manifest_path
        outline_path = self.project_path / receipt.outline_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        outline = outline_path.read_text(encoding="utf-8")

        self.assertEqual(manifest["schema_version"], "1.0")
        self.assertEqual(manifest["lifecycle_status"], "planning")
        self.assertEqual(
            manifest["approval_status"],
            "awaiting_human_approval",
        )
        self.assertIn(f"{review_id}:C1", outline)
        self.assertIn(
            "This artifact plans a book. It does not contain chapter prose.",
            outline,
        )
        self.assertFalse((manifest_path.parent / "chapter-1.md").exists())

    def test_wrapper_runs_planner_with_read_only_host_call(self) -> None:
        conversation_id = "wrapper-book-plan"
        review_id = self.create_eligible_review(conversation_id)
        review_path = self.reviews_path / f"{conversation_id}.json"
        decision_path = (
            self.review_decisions_path / f"{conversation_id}.json"
        )
        proposal = self.make_proposal(review_id, [f"{review_id}:C1"])
        captured_call_purposes = []

        def capture_side_effect(
            _backend: str,
            _program_path: str,
            prompt: str,
            call_purpose: str,
        ) -> tuple[list[dict], str]:
            captured_call_purposes.append(call_purpose)
            self.assertIn(f"{review_id}:C1", prompt)
            mcp_server.submit_book_manifest(proposal)
            return [], "Book plan saved."

        with (
            patch.object(backend_selector, "BOOKS_PATH", self.books_path),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=capture_side_effect,
            ),
        ):
            manifest_path, outline_path, outline = (
                backend_selector.plan_book_from_review(
                    "codex",
                    "/usr/bin/codex",
                    review_path,
                    decision_path,
                )
            )

        self.assertTrue(manifest_path.is_file())
        self.assertTrue(outline_path.is_file())
        self.assertIn("# Alexandrian Book Plan", outline)
        self.assertEqual(
            captured_call_purposes,
            [backend_selector.BOOK_PLAN_CALL],
        )

    def test_unknown_claim_prevents_any_book_files(self) -> None:
        conversation_id = "unknown-claim"
        review_id = self.create_eligible_review(conversation_id)
        proposal = self.make_proposal(review_id, [f"{review_id}:C99"])

        with self.assertRaises(ToolError):
            mcp_server.submit_book_manifest(proposal)

        self.assertFalse(
            (self.books_path / f"book-{conversation_id}").exists()
        )

    def test_contradicted_claim_must_be_mapped(self) -> None:
        conversation_id = "contradicted-claim"
        review_id = self.create_eligible_review(
            conversation_id,
            "contradicted_in_conversation",
        )
        proposal = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
        )

        with self.assertRaises(ToolError):
            mcp_server.submit_book_manifest(proposal)

    def test_known_research_gap_must_be_preserved(self) -> None:
        conversation_id = "research-gap"
        review_id = self.create_eligible_review(
            conversation_id,
            evidence_gap=True,
        )
        proposal = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
        )

        with self.assertRaises(ToolError):
            mcp_server.submit_book_manifest(proposal)

    def test_known_research_gap_can_be_mapped_without_hiding_it(self) -> None:
        conversation_id = "mapped-research-gap"
        review_id = self.create_eligible_review(
            conversation_id,
            evidence_gap=True,
        )
        proposal_data = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
            readiness="research_required",
            missing_work=["Resolve the transcript evidence gap."],
            research_gaps=[
                {
                    "description": (
                        "How can evidence become a trustworthy book?"
                    ),
                    "source_review_id": review_id,
                    "transcript_references": ["T1"],
                    "blocking_chapter_ids": ["CH1"],
                }
            ],
        ).model_dump(mode="json")
        proposal_data["prohibited_overinterpretations"] = [
            "Do not treat the current planning model as validated."
        ]

        receipt = mcp_server.submit_book_manifest(
            mcp_server.BookManifestProposal.model_validate(proposal_data)
        )

        self.assertTrue((self.project_path / receipt.manifest_path).is_file())

    def test_ready_chapter_cannot_hide_missing_work(self) -> None:
        conversation_id = "hidden-work"
        review_id = self.create_eligible_review(conversation_id)
        proposal = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
            missing_work=["Verify another source."],
        )

        with self.assertRaises(ToolError):
            mcp_server.submit_book_manifest(proposal)

    def test_blocked_human_gate_rejects_book_planning(self) -> None:
        conversation_id = "blocked-gate"
        review_id = self.create_eligible_review(conversation_id)
        decision_path = self.review_decisions_path / f"{conversation_id}.json"
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        decision["book_planning_gate"] = "blocked"
        decision_path.write_text(json.dumps(decision), encoding="utf-8")

        with self.assertRaises(ToolError):
            mcp_server.submit_book_manifest(
                self.make_proposal(review_id, [f"{review_id}:C1"])
            )

    def test_book_plan_checkpoint_is_separate_from_manifest(self) -> None:
        conversation_id = "book-checkpoint"
        review_id = self.create_eligible_review(conversation_id)
        receipt = mcp_server.submit_book_manifest(
            self.make_proposal(review_id, [f"{review_id}:C1"])
        )
        manifest_path = self.project_path / receipt.manifest_path
        original_manifest = manifest_path.read_text(encoding="utf-8")
        responses = iter(["1", "Keep the uncertainty visible"])

        with patch("builtins.input", side_effect=lambda _prompt: next(responses)):
            approval_path = backend_selector.record_book_plan_checkpoint(
                manifest_path
            )

        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        self.assertEqual(approval["chapter_drafting_gate"], "ready")
        self.assertEqual(
            manifest_path.read_text(encoding="utf-8"),
            original_manifest,
        )


if __name__ == "__main__":
    unittest.main()
