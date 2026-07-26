import json
import os
import tempfile
import unittest
from pathlib import Path

import knowledge_index
import mcp_server
from mcp.server.fastmcp.exceptions import ToolError


class KnowledgeIndexTests(unittest.TestCase):
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
        mcp_server.REVIEW_SESSIONS_PATH = self.review_sessions_path
        mcp_server.REVIEW_DECISIONS_PATH = self.review_decisions_path
        mcp_server.BOOKS_PATH = self.books_path
        mcp_server.KNOWLEDGE_PATH = self.knowledge_path
        mcp_server.SOURCE_POLICY_PATH = self.source_policy_path
        mcp_server.SOURCE_RECEIPTS.clear()
        os.environ["ALEXANDRIAN_AGENT_HOST"] = "test-indexer"

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
                "content": "How should research become reusable knowledge?",
            },
            {
                "type": "message",
                "role": "assistant",
                "provider": "codex",
                "content": "Preserve provenance and link reusable concepts.",
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

    def make_review(
        self,
        conversation_id: str,
        receipt_id: str,
    ) -> mcp_server.ResearchReview:
        core_question = {
            "text": "How should research become reusable knowledge?",
            "transcript_references": ["T1"],
        }
        return mcp_server.ResearchReview.model_validate(
            {
                "conversation_id": conversation_id,
                "core_question": core_question,
                "curiosity_signature": {
                    "why_this_question_matters": [
                        {
                            "text": (
                                "The user wants research to remain reusable."
                            ),
                            "transcript_references": ["T1"],
                        }
                    ],
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
                    "beliefs": [
                        {
                            "text": "A folder of notes may be enough.",
                            "transcript_references": ["T1"],
                        }
                    ],
                    "demonstrated_understanding": [],
                    "misunderstandings": [],
                },
                "belief_revisions": [
                    {
                        "original_belief": {
                            "text": "A folder of notes may be enough.",
                            "transcript_references": ["T1"],
                        },
                        "assistant_challenge": {
                            "text": "Folders do not encode why ideas connect.",
                            "transcript_references": ["T2"],
                        },
                        "user_response": {
                            "text": "The storage layer should behave like an index.",
                            "transcript_references": ["T1"],
                        },
                        "status": "accepted",
                    }
                ],
                "claims": [
                    {
                        "claim": (
                            "Stable provenance lets knowledge be reused safely."
                        ),
                        "origin": "joint",
                        "status": "supported_by_checked_source",
                        "transcript_references": ["T1", "T2"],
                        "source_receipt_ids": [receipt_id],
                        "next_verification": (
                            "Promote the same review and inspect every reference."
                        ),
                    }
                ],
                "mentioned_sources": [],
                "checked_sources": [
                    {
                        "receipt_id": receipt_id,
                        "claim_checked": (
                            "Stable provenance lets knowledge be reused safely."
                        ),
                        "finding": "supports",
                        "assessment": "The source supports the bounded claim.",
                        "author_or_organization": "Example Research Group",
                        "publication_date": "2026-01-01",
                        "publisher_or_journal": "Example Journal",
                        "doi": None,
                        "evidence_location": "Section 2",
                        "evidence_summary": (
                            "Stable identifiers preserved source lineage."
                        ),
                        "limitations": "The example covers one local index.",
                        "counterevidence_or_alternative_explanation": (
                            "A small unchanging collection may need less structure."
                        ),
                        "transcript_references": ["T2"],
                    }
                ],
                "research_trail": [],
                "evidence_gaps": [
                    {
                        "text": "Cross-review deduplication remains untested.",
                        "transcript_references": ["T1"],
                    }
                ],
                "connected_ideas": [
                    {
                        "idea": "Knowledge graphs can expose reusable paths.",
                        "relationship": "application",
                        "transcript_references": ["T2"],
                    }
                ],
                "separate_branches": [
                    {
                        "idea": "Semantic retrieval deserves separate research.",
                        "relationship": "separate_research_question",
                        "transcript_references": ["T1"],
                    }
                ],
                "thought_questions": [
                    {
                        "text": "When should two nodes be merged?",
                        "transcript_references": ["T1"],
                    }
                ],
                "experiments": [
                    {
                        "hypothesis": (
                            "Typed links retrieve cleaner chapter context."
                        ),
                        "observable_outcome": (
                            "A context bundle contains only relevant nodes."
                        ),
                        "comparison_or_baseline": "Compare with folder search.",
                        "evidence_that_would_weaken_the_claim": (
                            "Folder search returns equally precise context."
                        ),
                        "major_unresolved_dependency": (
                            "The context-bundle contract is not built."
                        ),
                        "transcript_references": ["T2"],
                    }
                ],
                "use_case_candidates": [
                    {
                        "text": "Assemble one claim into multiple books.",
                        "transcript_references": ["T1"],
                    }
                ],
                "research_route": "ready_for_book_consideration",
                "route_reason": core_question,
                "recommended_next_step": "Create the reusable index.",
            }
        )

    def create_eligible_review(self, conversation_id: str) -> str:
        self.write_finished_conversation(conversation_id)
        receipt_id = "source-index-test"
        claim = "Stable provenance lets knowledge be reused safely."
        mcp_server.SOURCE_RECEIPTS[receipt_id] = mcp_server.SourceReceipt(
            receipt_id=receipt_id,
            conversation_id=conversation_id,
            requested_url="https://example.com/research",
            final_url="https://example.com/research",
            domain="example.com",
            title="An Example Source",
            content_type="text/plain",
            retrieved_at="2026-01-01T00:00:00+00:00",
            content_sha256="abc123",
            claim_under_review=claim,
            content="RAW SOURCE BODY MUST NOT ENTER THE INDEX",
            content_truncated=False,
        )
        receipt = mcp_server.submit_research_review(
            self.make_review(conversation_id, receipt_id)
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
                        "How should research become reusable knowledge?"
                    ),
                    "curiosity_signature_decision": "accepted",
                    "approved_curiosity_signature": (
                        self.approved_curiosity_signature()
                    ),
                    "accepted_belief_revisions": [
                        "A maintained index is more useful than loose notes."
                    ],
                    "disputed_points": [],
                    "excluded_material": [],
                }
            ),
            encoding="utf-8",
        )
        return receipt.review_id

    def approved_curiosity_signature(self) -> dict:
        return {
            "why_this_question_matters": [
                {
                    "text": "The user wants research to remain reusable.",
                    "provenance": "review_extraction",
                    "transcript_references": ["T1"],
                }
            ],
            "original_intuition": [
                {
                    "text": "How should research become reusable knowledge?",
                    "provenance": "review_extraction",
                    "transcript_references": ["T1"],
                }
            ],
            "surprising_connections": [],
            "central_tensions": [],
            "desired_transformation": [],
            "questions_that_must_remain_open": [
                {
                    "text": "How should research become reusable knowledge?",
                    "provenance": "review_extraction",
                    "transcript_references": ["T1"],
                }
            ],
        }

    def test_promotion_writes_canonical_markdown_and_derived_graph(self) -> None:
        review_id = self.create_eligible_review("successful-index")

        receipt = mcp_server.promote_review_to_knowledge_index(review_id)
        graph_path = self.project_path / receipt.graph_path
        index_path = self.project_path / receipt.index_path
        graph = json.loads(graph_path.read_text(encoding="utf-8"))
        node_types = {node["type"] for node in graph["nodes"]}
        edge_types = {edge["type"] for edge in graph["edges"]}
        claim_node = next(
            node for node in graph["nodes"] if node["type"] == "claim"
        )

        self.assertEqual(graph["schema_version"], "1.0")
        self.assertEqual(claim_node["claim_ids"], [f"{review_id}:C1"])
        self.assertEqual(
            claim_node["source_receipt_ids"],
            ["source-index-test"],
        )
        self.assertTrue(
            {
                "core_question",
                "belief",
                "belief_revision",
                "claim",
                "source",
                "concept",
                "book_branch",
                "reflection_question",
                "experiment",
                "use_case",
                "evidence_gap",
            }.issubset(node_types)
        )
        self.assertTrue(
            {
                "supported_by",
                "branches_into",
                "tested_by",
                "applies_to",
            }.issubset(edge_types)
        )
        self.assertIn("# Alexandrian Knowledge Index", index_path.read_text())

        node_files = list(
            (self.project_path / receipt.review_nodes_path).glob("*.md")
        )
        combined_output = "\n".join(
            [
                index_path.read_text(encoding="utf-8"),
                graph_path.read_text(encoding="utf-8"),
                *[
                    path.read_text(encoding="utf-8")
                    for path in node_files
                ],
            ]
        )
        self.assertIn("source-index-test", combined_output)
        self.assertIn("[[ki-successful-index-", combined_output)
        self.assertIn("## Why This Question Matters", combined_output)
        self.assertIn(
            "The user wants research to remain reusable.",
            combined_output,
        )
        self.assertNotIn(
            "RAW SOURCE BODY MUST NOT ENTER THE INDEX",
            combined_output,
        )

    def test_unapproved_review_cannot_be_promoted(self) -> None:
        review_id = self.create_eligible_review("blocked-index")
        decision_path = self.review_decisions_path / "blocked-index.json"
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        decision["book_planning_gate"] = "blocked"
        decision_path.write_text(json.dumps(decision), encoding="utf-8")

        with self.assertRaises(ToolError):
            mcp_server.promote_review_to_knowledge_index(review_id)

        self.assertFalse(self.knowledge_path.exists())

    def test_review_cannot_be_promoted_twice(self) -> None:
        review_id = self.create_eligible_review("duplicate-index")
        mcp_server.promote_review_to_knowledge_index(review_id)

        with self.assertRaises(ToolError):
            mcp_server.promote_review_to_knowledge_index(review_id)

        events = (self.knowledge_path / "events.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertEqual(len(events), 1)

    def test_human_curiosity_correction_is_preserved_in_core_node(self) -> None:
        review_id = self.create_eligible_review("corrected-index")
        decision_path = self.review_decisions_path / "corrected-index.json"
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        decision["curiosity_signature_decision"] = "corrected"
        decision["approved_curiosity_signature"][
            "why_this_question_matters"
        ] = [
            {
                "text": "This matters because I want a reusable research method.",
                "provenance": "human_checkpoint",
                "transcript_references": [],
            }
        ]
        decision_path.write_text(json.dumps(decision), encoding="utf-8")

        receipt = mcp_server.promote_review_to_knowledge_index(review_id)
        core_path = (
            self.project_path
            / receipt.review_nodes_path
            / "ki-corrected-index-core-question.md"
        )
        core_node = core_path.read_text(encoding="utf-8")

        self.assertIn(
            "This matters because I want a reusable research method.",
            core_node,
        )
        self.assertIn("`human checkpoint correction`", core_node)

    def test_changed_claim_identity_cannot_enter_index(self) -> None:
        review_id = self.create_eligible_review("changed-claim-index")
        review_path = self.reviews_path / "changed-claim-index.json"
        review_document = json.loads(review_path.read_text(encoding="utf-8"))
        review_document["claim_index"][0]["claim_id"] = f"{review_id}:C99"
        review_path.write_text(json.dumps(review_document), encoding="utf-8")

        with self.assertRaises(ToolError):
            mcp_server.promote_review_to_knowledge_index(review_id)

        self.assertFalse(self.knowledge_path.exists())

    def test_dangling_relationship_is_rejected(self) -> None:
        node = knowledge_index.KnowledgeNode(
            node_id="ki-test-node",
            node_type="claim",
            title="A claim",
            summary="A claim",
            status="open",
            source_review_ids=("review-test",),
            relations=(
                knowledge_index.KnowledgeRelation(
                    "supported_by",
                    "ki-test-missing",
                ),
            ),
        )

        with self.assertRaises(knowledge_index.KnowledgeIndexError):
            knowledge_index.validate_nodes([node])


if __name__ == "__main__":
    unittest.main()
