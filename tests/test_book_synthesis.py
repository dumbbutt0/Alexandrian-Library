import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import book_synthesis


class BookSynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.book_id = "book-synthesis-test"
        self.book_directory = (
            self.project_path / "data" / "books" / self.book_id
        )
        self.book_directory.mkdir(parents=True)
        self.candidate_text = (
            "# Candidate\n\n"
            "The canonical claim remains bounded.\n\n"
            "A local implementation remains untested.\n"
        )
        self.candidate_path = self.book_directory / "candidate.md"
        self.candidate_path.write_text(
            self.candidate_text,
            encoding="utf-8",
        )
        self.manifest_path = self.book_directory / "manifest.json"
        self.review_path = (
            self.project_path / "data" / "reviews" / "synthesis-test.json"
        )
        self.profile_path = (
            self.project_path
            / "skills"
            / "alexandrian-book-synthesizer"
            / "SKILL.md"
        )
        self.profile_path.parent.mkdir(parents=True)
        self.profile_path.write_text("# Test profile\n", encoding="utf-8")
        self.write_json(
            self.manifest_path,
            {
                "book_id": self.book_id,
                "central_question": "Can editing preserve meaning?",
                "curiosity_signature": {"original_intuition": ["Preserve it."]},
                "prohibited_overinterpretations": ["Do not generalize."],
                "contradictions": [],
                "chapter_plan": [
                    {
                        "chapter_id": "CH1",
                        "material_disposition": "include",
                        "claim_ids": ["review-synthesis-test:C1"],
                    }
                ],
            },
        )
        self.write_json(
            self.review_path,
            {
                "claim_index": [
                    {
                        "claim_id": "review-synthesis-test:C1",
                        "claim": "The canonical claim remains bounded.",
                        "status": "supported_by_checked_source",
                        "source_receipt_ids": ["SRC1"],
                    }
                ]
            },
        )
        self.candidate = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "lifecycle_status": "candidate",
            "candidate_path": str(
                self.candidate_path.relative_to(self.project_path)
            ),
            "candidate_sha256": self.sha256(self.candidate_path),
            "source_receipt_ids": ["SRC1"],
            "inputs": [
                self.input_record("manifest", self.manifest_path),
                self.input_record("research_review", self.review_path),
            ],
        }
        self.write_json(
            self.book_directory / "candidate_assembly.json",
            self.candidate,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def input_record(self, role: str, path: Path) -> dict:
        return {
            "role": role,
            "path": str(path.relative_to(self.project_path)),
            "sha256": self.sha256(path),
        }

    def initialize(self) -> tuple[dict, dict, dict]:
        context = book_synthesis.build_context(
            self.project_path,
            self.book_id,
            self.candidate,
            self.profile_path,
        )
        inventory = {
            "book_id": self.book_id,
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "central_question": context["central_question"],
            "curiosity_signature": context["curiosity_signature"],
            "claims": [
                {
                    "claim_id": "review-synthesis-test:C1",
                    "source_kind": "canonical",
                    "statement": "The canonical claim remains bounded.",
                    "candidate_excerpt": (
                        "The canonical claim remains bounded."
                    ),
                    "candidate_location": "Candidate paragraph 1",
                    "certainty": "bounded",
                    "scope_and_conditions": "Only the described context.",
                    "evidence_receipt_ids": ["SRC1"],
                    "required_in_edition": True,
                    "importance_reason": "It is the planned chapter claim.",
                },
                {
                    "claim_id": "SC1",
                    "source_kind": "candidate_only",
                    "statement": "A local implementation remains untested.",
                    "candidate_excerpt": (
                        "A local implementation remains untested."
                    ),
                    "candidate_location": "Candidate paragraph 2",
                    "certainty": "unresolved",
                    "scope_and_conditions": "Local implementation only.",
                    "evidence_receipt_ids": [],
                    "required_in_edition": True,
                    "importance_reason": "It preserves an implementation limit.",
                },
            ],
            "definitions": [],
            "contradictions": [],
            "limitations": ["A local implementation remains untested."],
            "unresolved_questions": ["Will implementation validate it?"],
        }
        plan = {
            "book_id": self.book_id,
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "profile": context["profile"],
            "central_answer": "Editing can preserve meaning when audited.",
            "chapter_jobs": [{"chapter_id": "CH1", "job": "Explain the claim."}],
            "operations": [
                {
                    "operation_id": "OP1",
                    "kind": "transition",
                    "target": "CH1",
                    "description": "Add a clearer transition.",
                    "protected_claim_ids": [
                        "review-synthesis-test:C1",
                        "SC1",
                    ],
                }
            ],
            "terminology": [],
            "protected_claim_ids": [
                "review-synthesis-test:C1",
                "SC1",
            ],
        }
        book_synthesis.initialize_synthesis(
            self.project_path,
            context,
            inventory,
            plan,
        )
        return book_synthesis.load_initialized_synthesis(
            self.project_path,
            self.book_id,
        )

    def save_attempt(
        self,
        attempt: int,
        correction_audit_sha256: str | None = None,
    ) -> tuple[str, dict]:
        context, inventory, plan = book_synthesis.load_initialized_synthesis(
            self.project_path,
            self.book_id,
        )
        markdown = (
            "# Synthesized\n\n"
            "The canonical claim remains bounded.\n\n"
            "A local implementation remains untested.\n"
        )
        submission = book_synthesis.save_attempt(
            self.project_path,
            {
                "book_id": self.book_id,
                "attempt": attempt,
                "context_sha256": context["context_sha256"],
                "inventory_sha256": inventory["inventory_sha256"],
                "plan_sha256": plan["plan_sha256"],
                "correction_audit_sha256": correction_audit_sha256,
                "claim_mappings": [
                    {
                        "claim_id": "review-synthesis-test:C1",
                        "edition_excerpt": (
                            "The canonical claim remains bounded."
                        ),
                        "edition_location": "Synthesized paragraph 1",
                    },
                    {
                        "claim_id": "SC1",
                        "edition_excerpt": (
                            "A local implementation remains untested."
                        ),
                        "edition_location": "Synthesized paragraph 2",
                    },
                ],
                "change_summary": ["Added one transition."],
            },
            markdown,
        )
        return markdown, submission

    def audit(
        self,
        attempt: int,
        submission: dict,
        first_status: str = "preserved",
    ) -> dict:
        context, inventory, plan = book_synthesis.load_initialized_synthesis(
            self.project_path,
            self.book_id,
        )
        blocking = first_status in book_synthesis.BLOCKING_CLAIM_STATUSES
        return book_synthesis.submit_audit(
            self.project_path,
            {
                "book_id": self.book_id,
                "attempt": attempt,
                "context_sha256": context["context_sha256"],
                "inventory_sha256": inventory["inventory_sha256"],
                "plan_sha256": plan["plan_sha256"],
                "submission_sha256": submission["submission_sha256"],
                "book_sha256": submission["book_sha256"],
                "claim_results": [
                    {
                        "claim_id": "review-synthesis-test:C1",
                        "status": first_status,
                        "candidate_excerpt": (
                            "The canonical claim remains bounded."
                        ),
                        "edition_excerpt": (
                            None
                            if first_status == "omitted"
                            else "The canonical claim remains bounded."
                        ),
                        "explanation": "Compared scope and certainty.",
                        "justification": None,
                        "certainty_changed": blocking,
                        "scope_changed": blocking,
                        "evidence_relationship_changed": False,
                    },
                    {
                        "claim_id": "SC1",
                        "status": "preserved",
                        "candidate_excerpt": (
                            "A local implementation remains untested."
                        ),
                        "edition_excerpt": (
                            "A local implementation remains untested."
                        ),
                        "explanation": "The limitation remains visible.",
                        "justification": None,
                        "certainty_changed": False,
                        "scope_changed": False,
                        "evidence_relationship_changed": False,
                    },
                ],
                "unmapped_candidate_claims": [],
                "novel_edition_claims": [],
                "lost_limitations": [],
                "terminology_drift": [],
                "central_question_preserved": True,
                "curiosity_preservation": {
                    "status": "preserved",
                    "explanation": "The original intuition remains.",
                },
                "overall_status": "blocking" if blocking else "pass",
                "blocking_reasons": (
                    ["The canonical claim changed."] if blocking else []
                ),
            },
        )

    def test_passing_first_attempt_becomes_ready_for_editorial(self) -> None:
        self.initialize()
        _, submission = self.save_attempt(1)
        receipt = self.audit(1, submission)

        self.assertEqual(receipt["lifecycle_status"], "ready_for_editorial")
        self.assertEqual(receipt["selected_attempt"], 1)
        self.assertEqual(
            book_synthesis.workflow_state(self.project_path, self.book_id),
            "ready_for_editorial",
        )

    def test_blocking_first_audit_allows_exactly_one_correction(self) -> None:
        self.initialize()
        _, first = self.save_attempt(1)
        first_result = self.audit(1, first, "strengthened")
        self.assertEqual(first_result["status"], "correction_required")

        first_audit_path = (
            self.book_directory
            / "synthesis"
            / "attempts"
            / "S1"
            / "audit.json"
        )
        first_audit = json.loads(first_audit_path.read_text(encoding="utf-8"))
        _, second = self.save_attempt(2, first_audit["audit_sha256"])
        receipt = self.audit(2, second)

        self.assertEqual(receipt["selected_attempt"], 2)
        with self.assertRaises(book_synthesis.BookSynthesisError):
            book_synthesis.save_attempt(
                self.project_path,
                {**second, "attempt": 3},
                "Third attempt",
            )

    def test_second_blocking_audit_holds_the_cycle(self) -> None:
        self.initialize()
        _, first = self.save_attempt(1)
        self.audit(1, first, "meaning_changed")
        first_audit_path = (
            self.book_directory
            / "synthesis"
            / "attempts"
            / "S1"
            / "audit.json"
        )
        first_audit = json.loads(first_audit_path.read_text(encoding="utf-8"))
        _, second = self.save_attempt(2, first_audit["audit_sha256"])
        receipt = self.audit(2, second, "omitted")

        self.assertEqual(receipt["lifecycle_status"], "synthesis_held")
        self.assertEqual(
            book_synthesis.workflow_state(self.project_path, self.book_id),
            "synthesis_held",
        )

    def test_stale_candidate_prevents_context_creation(self) -> None:
        self.candidate_path.write_text("Changed.\n", encoding="utf-8")

        with self.assertRaises(book_synthesis.BookSynthesisError):
            book_synthesis.build_context(
                self.project_path,
                self.book_id,
                self.candidate,
                self.profile_path,
            )

    def test_every_contract_blocker_is_enforced(self) -> None:
        _, inventory, _ = self.initialize()
        base_audit = {
            "claim_results": [
                {
                    "claim_id": "review-synthesis-test:C1",
                    "status": "preserved",
                    "candidate_excerpt": (
                        "The canonical claim remains bounded."
                    ),
                    "edition_excerpt": (
                        "The canonical claim remains bounded."
                    ),
                    "explanation": "Meaning remains stable.",
                    "justification": None,
                    "certainty_changed": False,
                    "scope_changed": False,
                    "evidence_relationship_changed": False,
                },
                {
                    "claim_id": "SC1",
                    "status": "preserved",
                    "candidate_excerpt": (
                        "A local implementation remains untested."
                    ),
                    "edition_excerpt": (
                        "A local implementation remains untested."
                    ),
                    "explanation": "The limitation remains visible.",
                    "justification": None,
                    "certainty_changed": False,
                    "scope_changed": False,
                    "evidence_relationship_changed": False,
                },
            ],
            "unmapped_candidate_claims": [],
            "novel_edition_claims": [],
            "lost_limitations": [],
            "terminology_drift": [],
            "central_question_preserved": True,
            "curiosity_preservation": {
                "status": "preserved",
                "explanation": "The original intuition remains.",
            },
        }
        cases = [
            ("strengthened", "review-synthesis-test:C1:strengthened"),
            ("omitted", "review-synthesis-test:C1:omitted"),
            ("contradicted", "review-synthesis-test:C1:contradicted"),
            ("meaning_changed", "review-synthesis-test:C1:meaning_changed"),
            ("certainty_flag", "review-synthesis-test:C1:unclassified_change"),
            ("scope_flag", "review-synthesis-test:C1:unclassified_change"),
            ("narrowed_evidence", "review-synthesis-test:C1:evidence_changed"),
            ("unmapped_candidate_claims", "unmapped_candidate_claims"),
            ("novel_edition_claims", "novel_edition_claims"),
            ("lost_limitations", "lost_limitations"),
            ("terminology_drift", "terminology_drift"),
            ("central_question", "central_question_changed"),
            ("curiosity", "curiosity_distorted"),
        ]
        for case, expected in cases:
            with self.subTest(case=case):
                audit = deepcopy(base_audit)
                result = audit["claim_results"][0]
                if case in book_synthesis.BLOCKING_CLAIM_STATUSES:
                    result["status"] = case
                    if case == "omitted":
                        result["edition_excerpt"] = None
                elif case == "certainty_flag":
                    result["certainty_changed"] = True
                elif case == "scope_flag":
                    result["status"] = "clarified"
                    result["scope_changed"] = True
                elif case == "narrowed_evidence":
                    result["status"] = "narrowed"
                    result["justification"] = "The evidence supports less."
                    result["evidence_relationship_changed"] = True
                elif case in {
                    "unmapped_candidate_claims",
                    "novel_edition_claims",
                    "lost_limitations",
                    "terminology_drift",
                }:
                    audit[case] = ["Detected material."]
                elif case == "central_question":
                    audit["central_question_preserved"] = False
                elif case == "curiosity":
                    audit["curiosity_preservation"]["status"] = "distorted"

                blockers = book_synthesis._audit_blockers(
                    audit,
                    inventory,
                )
                self.assertIn(expected, blockers)

        allowed_narrowing = deepcopy(base_audit)
        allowed_result = allowed_narrowing["claim_results"][0]
        allowed_result["status"] = "narrowed"
        allowed_result["justification"] = (
            "The edition narrows certainty without changing its evidence."
        )
        allowed_result["certainty_changed"] = True
        allowed_result["scope_changed"] = True
        self.assertEqual(
            book_synthesis._audit_blockers(allowed_narrowing, inventory),
            [],
        )

    def test_audit_requires_explicit_boolean_change_flags(self) -> None:
        self.initialize()
        _, submission = self.save_attempt(1)
        context, inventory, plan = book_synthesis.load_initialized_synthesis(
            self.project_path,
            self.book_id,
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            book_synthesis.submit_audit(
                self.project_path,
                {
                    "book_id": self.book_id,
                    "attempt": 1,
                    "context_sha256": context["context_sha256"],
                    "inventory_sha256": inventory["inventory_sha256"],
                    "plan_sha256": plan["plan_sha256"],
                    "submission_sha256": submission["submission_sha256"],
                    "book_sha256": submission["book_sha256"],
                    "claim_results": [
                        {
                            "claim_id": claim["claim_id"],
                            "status": "preserved",
                            "candidate_excerpt": claim["candidate_excerpt"],
                            "edition_excerpt": claim["candidate_excerpt"],
                            "explanation": "Compared directly.",
                            "justification": None,
                            "certainty_changed": None,
                            "scope_changed": False,
                            "evidence_relationship_changed": False,
                        }
                        for claim in inventory["claims"]
                    ],
                    "unmapped_candidate_claims": [],
                    "novel_edition_claims": [],
                    "lost_limitations": [],
                    "terminology_drift": [],
                    "central_question_preserved": True,
                    "curiosity_preservation": {
                        "status": "preserved",
                        "explanation": "The original intuition remains.",
                    },
                    "overall_status": "pass",
                    "blocking_reasons": [],
                },
            )

    def test_terminal_receipt_can_be_recovered_without_an_agent_call(self) -> None:
        self.initialize()
        _, submission = self.save_attempt(1)
        self.audit(1, submission)
        receipt_path = self.book_directory / "synthesis" / "receipt.json"
        receipt_path.unlink()

        self.assertEqual(
            book_synthesis.workflow_state(self.project_path, self.book_id),
            "receipt_recovery",
        )
        receipt = book_synthesis.recover_terminal_receipt(
            self.project_path,
            self.book_id,
        )
        self.assertEqual(receipt["lifecycle_status"], "ready_for_editorial")
        self.assertEqual(
            book_synthesis.workflow_state(self.project_path, self.book_id),
            "ready_for_editorial",
        )

    def test_tampered_selected_edition_invalidates_receipt(self) -> None:
        self.initialize()
        _, submission = self.save_attempt(1)
        receipt = self.audit(1, submission)
        selected_book = self.project_path / receipt["book_path"]
        selected_book.write_text("Tampered.\n", encoding="utf-8")

        with self.assertRaises(book_synthesis.BookSynthesisError):
            book_synthesis.verify_receipt(
                self.project_path,
                self.book_id,
                self.candidate["candidate_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
