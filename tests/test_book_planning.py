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
        self.evidence_path = self.project_path / "data" / "evidence"
        self.research_sources_path = (
            self.project_path / "data" / "research_sources"
        )
        self.review_sessions_path = (
            self.project_path / "data" / "review_sessions"
        )
        self.review_decisions_path = (
            self.project_path / "data" / "review_decisions"
        )
        self.books_path = self.project_path / "data" / "books"
        self.knowledge_path = self.project_path / "data" / "knowledge"
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
        mcp_server.EVIDENCE_PATH = self.evidence_path
        mcp_server.RESEARCH_SOURCES_PATH = self.research_sources_path
        mcp_server.REVIEW_SESSIONS_PATH = self.review_sessions_path
        mcp_server.REVIEW_DECISIONS_PATH = self.review_decisions_path
        mcp_server.BOOKS_PATH = self.books_path
        mcp_server.KNOWLEDGE_PATH = self.knowledge_path
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
                "curiosity_signature": {
                    "why_this_question_matters": [],
                    "original_intuition": [core_question],
                    "surprising_connections": [],
                    "central_tensions": [],
                    "desired_transformation": [],
                    "questions_that_must_remain_open": [core_question],
                },
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
                "evidence_gaps": (
                    [
                        {
                            **core_question,
                            "disposition": "routed_to_later",
                            "book_blocking": False,
                            "closure_reason": (
                                "This validation belongs to a later "
                                "implementation study."
                            ),
                            "destination": "future implementation research",
                        }
                    ]
                    if evidence_gap
                    else []
                ),
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
        self.write_review_approval(conversation_id, receipt.review_id)
        return receipt.review_id

    def write_review_approval(
        self,
        conversation_id: str,
        review_id: str,
    ) -> None:
        self.review_decisions_path.mkdir(parents=True)
        (self.review_decisions_path / f"{conversation_id}.json").write_text(
            json.dumps(
                {
                    "review_id": review_id,
                    "conversation_id": conversation_id,
                    "decided_at": "2026-01-01T00:00:00+00:00",
                    "decision": "approved_for_book_planning",
                    "review_research_route": "ready_for_book_consideration",
                    "book_planning_gate": "ready",
                    "confirmed_core_question_or_thesis": (
                        "How can evidence become a trustworthy book?"
                    ),
                    "curiosity_signature_decision": "accepted",
                    "approved_curiosity_signature": (
                        self.approved_curiosity_signature()
                    ),
                    "accepted_belief_revisions": [],
                    "disputed_points": [],
                    "excluded_material": [],
                }
            ),
            encoding="utf-8",
        )

    def create_evidence_book(
        self,
        conversation_id: str,
        readiness: str = "ready_to_draft",
        include_receipt: bool = True,
    ) -> tuple[str, str, str]:
        self.write_finished_conversation(conversation_id)
        review_id = f"review-{conversation_id}"
        claim_id = f"{review_id}:C1"
        receipt_id = f"source-{conversation_id}"
        claim = "Checked provenance supports safe reuse."
        excerpt = "Stable source links preserve the checked result."
        source_receipt = mcp_server.SourceReceipt(
            receipt_id=receipt_id,
            conversation_id=conversation_id,
            requested_url="https://example.com/provenance",
            final_url="https://example.com/provenance",
            domain="example.com",
            title="Provenance Study",
            content_type="text/plain",
            retrieved_at="2026-01-01T00:00:00+00:00",
            content_sha256="source-content-hash",
            claim_under_review=claim,
            content=f"{excerpt} Additional unselected source material.",
            content_truncated=False,
        )
        mcp_server.write_source_receipt(source_receipt)
        review_data = self.make_ready_review(
            conversation_id
        ).model_dump(mode="json")
        review_data["claims"] = [
            {
                "claim": claim,
                "origin": "joint",
                "status": "supported_by_checked_source",
                "transcript_references": ["T1", "T2"],
                "source_receipt_ids": [receipt_id],
                "next_verification": "Rebuild the context from its sources.",
            }
        ]
        review_data["checked_sources"] = [
            {
                "receipt_id": receipt_id,
                "claim_checked": claim,
                "finding": "supports",
                "assessment": "The source supports the bounded claim.",
                "author_or_organization": "Example Research Group",
                "publication_date": "2026-01-01",
                "publisher_or_journal": "Example Journal",
                "doi": None,
                "evidence_location": "Results",
                "evidence_excerpt": excerpt,
                "evidence_summary": "The result preserves source links.",
                "limitations": "One local workflow was evaluated.",
                "counterevidence_or_alternative_explanation": (
                    "A static project may need fewer links."
                ),
                "transcript_references": ["T2"],
            }
        ]
        review_receipt = mcp_server.submit_research_review(
            mcp_server.ResearchReview.model_validate(review_data)
        )
        self.write_review_approval(
            conversation_id,
            review_receipt.review_id,
        )
        mcp_server.promote_review_to_knowledge_index(
            review_receipt.review_id
        )

        missing_work = (
            ["Complete another evidence check."]
            if readiness == "research_required"
            else []
        )
        proposal_data = self.make_proposal(
            review_receipt.review_id,
            [claim_id],
            readiness=readiness,
            missing_work=missing_work,
        ).model_dump(mode="json")
        proposal_data["chapter_plan"][0][
            "evidence_source_receipt_ids"
        ] = [receipt_id] if include_receipt else []
        manifest_receipt = mcp_server.submit_book_manifest(
            mcp_server.BookManifestProposal.model_validate(proposal_data)
        )
        approval_path = (
            self.project_path / manifest_receipt.manifest_path
        ).parent / "approval.json"
        approval_path.write_text(
            json.dumps(
                {
                    "book_id": manifest_receipt.book_id,
                    "decided_at": "2026-01-02T00:00:00+00:00",
                    "decision": "approved_for_drafting",
                    "chapter_drafting_gate": "ready",
                    "notes": [],
                }
            ),
            encoding="utf-8",
        )
        return manifest_receipt.book_id, claim_id, receipt_id

    def approved_curiosity_signature(self) -> dict:
        item = {
            "text": "How can evidence become a trustworthy book?",
            "provenance": "review_extraction",
            "transcript_references": ["T1"],
        }
        return {
            "why_this_question_matters": [],
            "original_intuition": [item],
            "surprising_connections": [],
            "central_tensions": [],
            "desired_transformation": [],
            "questions_that_must_remain_open": [item],
        }

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
                "curiosity_signature": self.approved_curiosity_signature(),
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

        self.assertEqual(manifest["schema_version"], "1.2")
        self.assertEqual(manifest["lifecycle_status"], "planning")
        self.assertEqual(
            manifest["approval_status"],
            "awaiting_human_approval",
        )
        self.assertIn(f"{review_id}:C1", outline)
        self.assertIn("## Curiosity Signature", outline)
        self.assertIn(
            "This artifact plans a book. It does not contain chapter prose.",
            outline,
        )
        self.assertFalse((manifest_path.parent / "chapter-1.md").exists())

    def test_prepares_approved_chapter_context_without_drafting_prose(
        self,
    ) -> None:
        conversation_id = "chapter-context"
        book_id, claim_id, receipt_id = self.create_evidence_book(
            conversation_id
        )

        receipt = mcp_server.prepare_chapter_context(book_id, "CH1")
        context_path = self.project_path / receipt.json_path
        markdown_path = self.project_path / receipt.markdown_path
        context = json.loads(context_path.read_text(encoding="utf-8"))
        markdown = markdown_path.read_text(encoding="utf-8")
        stored_hash = context.pop("context_sha256")
        canonical_context = json.dumps(
            context,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

        self.assertEqual(context["schema_version"], "1.0")
        self.assertEqual(context["claims"][0]["claim_id"], claim_id)
        self.assertEqual(
            context["evidence_packets"][0]["receipt_id"],
            receipt_id,
        )
        self.assertEqual(
            stored_hash,
            mcp_server.hashlib.sha256(
                canonical_context.encode("utf-8")
            ).hexdigest(),
        )
        self.assertGreaterEqual(receipt.knowledge_node_count, 3)
        self.assertIn("This is an approved research context bundle", markdown)
        self.assertIn("Exact excerpt (untrusted source data)", markdown)
        book_directory = self.books_path / book_id
        self.assertFalse((book_directory / "chapter-1.md").exists())
        self.assertFalse((book_directory / "chapters").exists())

    def test_chapter_context_requires_book_plan_approval(self) -> None:
        conversation_id = "context-without-approval"
        book_id, _, _ = self.create_evidence_book(conversation_id)
        (self.books_path / book_id / "approval.json").unlink()

        with self.assertRaises(ToolError):
            mcp_server.prepare_chapter_context(book_id, "CH1")

        self.assertFalse(
            (self.books_path / book_id / "contexts").exists()
        )

    def test_chapter_context_rejects_non_ready_chapter(self) -> None:
        conversation_id = "context-not-ready"
        book_id, _, _ = self.create_evidence_book(
            conversation_id,
            readiness="research_required",
        )

        with self.assertRaises(ToolError):
            mcp_server.prepare_chapter_context(book_id, "CH1")

        self.assertFalse(
            (self.books_path / book_id / "contexts").exists()
        )

    def test_chapter_context_requires_durable_evidence_packet(self) -> None:
        conversation_id = "context-missing-evidence"
        book_id, _, receipt_id = self.create_evidence_book(
            conversation_id
        )
        (
            self.evidence_path / conversation_id / f"{receipt_id}.json"
        ).unlink()

        with self.assertRaises(ToolError):
            mcp_server.prepare_chapter_context(book_id, "CH1")

        self.assertFalse(
            (self.books_path / book_id / "contexts").exists()
        )

    def test_book_preflight_rejects_missing_source_receipt_lineage(
        self,
    ) -> None:
        conversation_id = "missing-source-lineage"
        _, _, receipt_id = self.create_evidence_book(conversation_id)
        packet_path = (
            self.evidence_path / conversation_id / f"{receipt_id}.json"
        )
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        packet["source_receipt_path"] = None
        packet["source_receipt_record_sha256"] = None
        packet_path.write_text(json.dumps(packet), encoding="utf-8")

        with self.assertRaisesRegex(
            ToolError,
            "predates durable source-receipt lineage",
        ):
            mcp_server.resolve_eligible_review(
                f"review-{conversation_id}"
            )

    def test_book_preflight_rejects_tampered_staged_source(self) -> None:
        conversation_id = "tampered-staged-source"
        _, _, receipt_id = self.create_evidence_book(conversation_id)
        source_path = (
            self.research_sources_path
            / conversation_id
            / f"{receipt_id}.json"
        )
        source_document = json.loads(source_path.read_text(encoding="utf-8"))
        source_document["receipt"]["title"] = "Changed after review"
        source_path.write_text(json.dumps(source_document), encoding="utf-8")

        with self.assertRaisesRegex(
            ToolError,
            "source-receipt lineage is invalid",
        ):
            mcp_server.resolve_eligible_review(
                f"review-{conversation_id}"
            )

    def test_ready_chapter_cannot_omit_checked_evidence(self) -> None:
        with self.assertRaises(ToolError):
            self.create_evidence_book(
                "context-omitted-evidence",
                include_receipt=False,
            )

        self.assertFalse(
            (
                self.books_path
                / "book-context-omitted-evidence"
            ).exists()
        )

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

    def test_planner_prompt_bounds_belief_revision_references(self) -> None:
        review_document = {
            "review": {
                "belief_revisions": [
                    {
                        "original_belief": {
                            "text": "An initial belief",
                            "transcript_references": ["T1"],
                        },
                        "assistant_challenge": {
                            "text": "A challenge",
                            "transcript_references": ["T2"],
                        },
                        "user_response": None,
                    }
                ]
            }
        }

        prompt = backend_selector.build_book_planner_prompt(
            "Planner instructions",
            review_document,
            {"book_planning_gate": "ready"},
        )

        self.assertIn(
            '"allowed_belief_revision_transcript_references": [\n'
            '    "T1",\n'
            '    "T2"\n'
            "  ]",
            prompt,
        )
        self.assertIn(
            "is not a general provenance field",
            prompt,
        )

    def test_general_transcript_reference_is_not_a_belief_revision(self) -> None:
        conversation_id = "bounded-belief-revision-reference"
        review_id = self.create_eligible_review(conversation_id)
        proposal_data = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
        ).model_dump(mode="json")
        proposal_data["chapter_plan"][0][
            "belief_revision_transcript_references"
        ] = ["T1"]

        with self.assertRaisesRegex(
            ToolError,
            "unknown belief-revision transcript turns: T1",
        ):
            mcp_server.submit_book_manifest(
                mcp_server.BookManifestProposal.model_validate(proposal_data)
            )

    def test_incomplete_planner_reports_bounded_provider_response(self) -> None:
        conversation_id = "incomplete-wrapper-book-plan"
        self.create_eligible_review(conversation_id)
        review_path = self.reviews_path / f"{conversation_id}.json"
        decision_path = (
            self.review_decisions_path / f"{conversation_id}.json"
        )
        provider_response = "planner schema rejection " + ("x" * 2_000)

        with (
            patch.object(backend_selector, "BOOKS_PATH", self.books_path),
            patch.object(
                backend_selector,
                "capture_backend_response",
                return_value=([], provider_response),
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "Planner response:\\nplanner schema rejection",
            ) as raised,
        ):
            backend_selector.plan_book_from_review(
                "codex",
                "/usr/bin/codex",
                review_path,
                decision_path,
            )

        self.assertLess(len(str(raised.exception)), 1_700)
        self.assertFalse(
            (self.books_path / f"book-{conversation_id}").exists()
        )

    def test_timed_out_planner_remains_resumable_at_book_plan(self) -> None:
        conversation_id = "timed-out-wrapper-book-plan"
        review_id = self.create_eligible_review(conversation_id)
        review_path = self.reviews_path / f"{conversation_id}.json"
        decision_path = (
            self.review_decisions_path / f"{conversation_id}.json"
        )
        (self.knowledge_path / "nodes" / review_id).mkdir(parents=True)
        timeout_error = RuntimeError(
            "codex book_plan timed out after 600 seconds."
        )

        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project_path),
            patch.object(
                backend_selector,
                "REVIEWS_PATH",
                self.reviews_path,
            ),
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.review_decisions_path,
            ),
            patch.object(
                backend_selector,
                "KNOWLEDGE_PATH",
                self.knowledge_path,
            ),
            patch.object(backend_selector, "BOOKS_PATH", self.books_path),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=timeout_error,
            ),
            self.assertRaisesRegex(RuntimeError, "timed out"),
        ):
            backend_selector.plan_book_from_review(
                "codex",
                "/usr/bin/codex",
                review_path,
                decision_path,
            )

        conversation_path = (
            self.conversations_path / f"{conversation_id}.jsonl"
        )
        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project_path),
            patch.object(
                backend_selector,
                "REVIEWS_PATH",
                self.reviews_path,
            ),
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.review_decisions_path,
            ),
            patch.object(
                backend_selector,
                "KNOWLEDGE_PATH",
                self.knowledge_path,
            ),
            patch.object(backend_selector, "BOOKS_PATH", self.books_path),
        ):
            self.assertEqual(
                backend_selector.workflow_stage(conversation_path),
                "book_plan",
            )

        self.assertFalse(
            (self.books_path / f"book-{conversation_id}").exists()
        )

    def test_wrapper_runs_one_new_chapter_development_pass(self) -> None:
        book_id, _, _ = self.create_evidence_book(
            "wrapper-chapter-development"
        )
        manifest_path = self.books_path / book_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["chapter_plan"][0]["readiness"] = "experiment_required"
        manifest["chapter_plan"][0]["draft_gate"] = "ready"
        manifest["chapter_plan"][0]["open_requirements"] = [
            {
                "kind": "experiment",
                "description": "Run the validation after this draft.",
                "disposition": "routed_to_later",
                "required_before": "later_work",
            }
        ]
        manifest_path.write_text(
            json.dumps(manifest),
            encoding="utf-8",
        )
        captured_call_purposes = []

        def capture_side_effect(
            _backend: str,
            _program_path: str,
            prompt: str,
            call_purpose: str,
        ) -> tuple[list[dict], str]:
            captured_call_purposes.append(call_purpose)
            self.assertIn(book_id, prompt)
            self.assertIn('"chapter_id": "CH1"', prompt)
            context_path = (
                self.books_path / book_id / "contexts" / "CH1.json"
            )
            context_path.parent.mkdir(parents=True)
            context_path.write_text("{}\n", encoding="utf-8")
            workspace_directory = (
                self.books_path / book_id / "workspaces" / "CH1"
            )
            versions_directory = workspace_directory / "versions"
            versions_directory.mkdir(parents=True)
            (versions_directory / "v001.json").write_text(
                "{}\n",
                encoding="utf-8",
            )
            (versions_directory / "v001.md").write_text(
                "# Initial Chapter Workspace\n",
                encoding="utf-8",
            )
            (workspace_directory / "index.json").write_text(
                json.dumps(
                    {
                        "book_id": book_id,
                        "chapter_id": "CH1",
                        "context_sha256": "a" * 64,
                        "current_version": 1,
                        "current_json_path": "versions/v001.json",
                        "current_markdown_path": "versions/v001.md",
                    }
                ),
                encoding="utf-8",
            )
            return [], "Chapter workspace initialized."

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
                "capture_backend_response",
                side_effect=capture_side_effect,
            ),
        ):
            json_path, markdown_path, body, version = (
                backend_selector.develop_chapter_once(
                    "codex",
                    "/usr/bin/codex",
                    book_id,
                    "CH1",
                )
            )

        self.assertTrue(json_path.is_file())
        self.assertTrue(markdown_path.is_file())
        self.assertEqual(body, "# Initial Chapter Workspace")
        self.assertEqual(version, 1)
        self.assertEqual(
            captured_call_purposes,
            [backend_selector.CHAPTER_DEVELOPMENT_CALL],
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

    def test_planner_cannot_rewrite_approved_curiosity(self) -> None:
        conversation_id = "changed-curiosity"
        review_id = self.create_eligible_review(conversation_id)
        proposal_data = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
        ).model_dump(mode="json")
        proposal_data["curiosity_signature"]["original_intuition"][0][
            "text"
        ] = "A planner-authored replacement."

        with self.assertRaises(ToolError):
            mcp_server.submit_book_manifest(
                mcp_server.BookManifestProposal.model_validate(proposal_data)
            )

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
            research_gaps=[
                {
                    "description": (
                        "How can evidence become a trustworthy book?"
                    ),
                    "source_review_id": review_id,
                    "transcript_references": ["T1"],
                    "blocking_chapter_ids": [],
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

    def test_two_gate_plan_can_route_experiment_after_drafting(self) -> None:
        conversation_id = "routed-experiment-after-drafting"
        review_id = self.create_eligible_review(conversation_id)
        proposal_data = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
            readiness="experiment_required",
            missing_work=["Run the controlled experiment later."],
        ).model_dump(mode="json")
        chapter = proposal_data["chapter_plan"][0]
        chapter["draft_gate"] = "ready"
        chapter["open_requirements"] = [
            {
                "kind": "experiment",
                "description": "Run the controlled experiment later.",
                "disposition": "routed_to_later",
                "required_before": "later_work",
            }
        ]

        receipt = mcp_server.submit_book_manifest(
            mcp_server.BookManifestProposal.model_validate(proposal_data)
        )
        manifest = json.loads(
            (self.project_path / receipt.manifest_path).read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(
            manifest["chapter_plan"][0]["readiness"],
            "experiment_required",
        )
        self.assertEqual(
            manifest["chapter_plan"][0]["draft_gate"],
            "ready",
        )

    def test_two_gate_plan_blocks_unresolved_drafting_requirement(self) -> None:
        conversation_id = "unresolved-before-drafting"
        review_id = self.create_eligible_review(conversation_id)
        proposal_data = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
            readiness="research_required",
            missing_work=["Verify a required factual claim."],
        ).model_dump(mode="json")
        chapter = proposal_data["chapter_plan"][0]
        chapter["draft_gate"] = "ready"
        chapter["open_requirements"] = [
            {
                "kind": "research",
                "description": "Verify a required factual claim.",
                "disposition": "bounded_unresolved",
                "required_before": "drafting",
            }
        ]

        with self.assertRaisesRegex(
            ToolError,
            "drafting requirements remain unresolved",
        ):
            mcp_server.submit_book_manifest(
                mcp_server.BookManifestProposal.model_validate(proposal_data)
            )

    def test_two_gate_block_requires_named_drafting_requirement(self) -> None:
        conversation_id = "unexplained-draft-block"
        review_id = self.create_eligible_review(conversation_id)
        proposal_data = self.make_proposal(
            review_id,
            [f"{review_id}:C1"],
        ).model_dump(mode="json")
        chapter = proposal_data["chapter_plan"][0]
        chapter["draft_gate"] = "blocked"
        chapter["open_requirements"] = []

        with self.assertRaisesRegex(
            ToolError,
            "draft-blocked without an unresolved drafting requirement",
        ):
            mcp_server.submit_book_manifest(
                mcp_server.BookManifestProposal.model_validate(proposal_data)
            )

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

    def test_returned_plan_is_archived_before_one_revision(self) -> None:
        conversation_id = "book-plan-revision"
        review_id = self.create_eligible_review(conversation_id)
        first_receipt = mcp_server.submit_book_manifest(
            self.make_proposal(review_id, [f"{review_id}:C1"])
        )
        manifest_path = self.project_path / first_receipt.manifest_path
        first_manifest = manifest_path.read_bytes()
        responses = iter(
            [
                "2",
                (
                    "Keep outcome claims excluded while making the "
                    "design explanation draftable."
                ),
            ]
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
            patch("builtins.input", side_effect=lambda _prompt: next(responses)),
        ):
            backend_selector.record_book_plan_checkpoint(manifest_path)
            archived_path = backend_selector.archive_book_plan_for_revision(
                manifest_path.parent
            )
            revision_context = (
                backend_selector.load_latest_book_plan_revision(
                    manifest_path.parent
                )
            )

        self.assertFalse(manifest_path.exists())
        self.assertEqual(
            (archived_path / "manifest.json").read_bytes(),
            first_manifest,
        )
        self.assertEqual(
            revision_context["human_revision_request"]["decision"],
            "needs_plan_revision",
        )
        self.assertIn(
            "making the design explanation draftable",
            revision_context["human_revision_request"]["notes"][0],
        )

        revised_receipt = mcp_server.submit_book_manifest(
            self.make_proposal(review_id, [f"{review_id}:C1"])
        )

        self.assertEqual(revised_receipt.book_id, first_receipt.book_id)
        self.assertTrue(manifest_path.is_file())
        self.assertTrue((archived_path / "outline.md").is_file())


if __name__ == "__main__":
    unittest.main()
