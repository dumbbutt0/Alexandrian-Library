import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import book_assembler
import book_synthesis
import bookshelf


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
        self.synthesizer_skill_path = (
            self.project_path
            / "skills"
            / "alexandrian-book-synthesizer"
            / "SKILL.md"
        )
        self.synthesizer_skill_path.parent.mkdir(parents=True)
        self.synthesizer_skill_path.write_text(
            "# Test synthesis profile\n",
            encoding="utf-8",
        )
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
                "claim_index": [
                    {
                        "claim_id": f"{self.review_id}:C1",
                        "claim": "Exact first chapter evidence is preserved.",
                        "status": "supported_by_checked_source",
                        "source_receipt_ids": ["SRC1"],
                    }
                ],
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

    def test_explicit_draft_gate_overrides_open_research_state(self) -> None:
        chapter = self.chapter_plan("CH1", "First Chapter")
        chapter["readiness"] = "experiment_required"
        chapter["draft_gate"] = "ready"

        self.assertTrue(book_assembler.chapter_is_draft_ready(chapter))

    def chapter_plan(self, chapter_id: str, title: str) -> dict:
        return {
            "chapter_id": chapter_id,
            "working_title": title,
            "readiness": "ready_to_draft",
            "material_disposition": "include",
            "claim_ids": (
                [f"{self.review_id}:C1"] if chapter_id == "CH1" else []
            ),
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
        receipt = book_assembler.assemble_candidate_book(
            self.project_path,
            self.book_id,
        )

        book_path = self.project_path / receipt["candidate_path"]
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
        self.assertFalse((self.book_directory / "book.md").exists())

    def test_assembles_exact_human_accepted_draft(self) -> None:
        assessment_path = (
            self.book_directory / "assessments" / "CH1" / "A2.json"
        )
        assessment_document = json.loads(
            assessment_path.read_text(encoding="utf-8")
        )
        assessment_document["assessment"]["outcome"] = "human_input_required"
        assessment_document["assessment"]["human_input_requests"] = [
            "Choose whether to accept the exact assessed draft."
        ]
        assessment_document["assessment_sha256"] = (
            book_assembler.canonical_json_sha256(
                assessment_document,
                "assessment_sha256",
            )
        )
        self.write_json(assessment_path, assessment_document)

        human_input_path = (
            self.book_directory / "runs" / "CH1.A2.human-input.json"
        )
        human_input = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "chapter_id": "CH1",
            "assessment_id": "A2",
            "assessment_sha256": assessment_document["assessment_sha256"],
            "answers": [
                {
                    "question": (
                        "Choose whether to accept the exact assessed draft."
                    ),
                    "answer": "Accept the exact assessed draft.",
                }
            ],
        }
        human_input["human_input_sha256"] = (
            book_assembler.canonical_json_sha256(
                human_input,
                "human_input_sha256",
            )
        )
        self.write_json(human_input_path, human_input)

        workspace_path = (
            self.book_directory
            / "workspaces"
            / "CH1"
            / "versions"
            / "v002.json"
        )
        workspace_document = json.loads(
            workspace_path.read_text(encoding="utf-8")
        )
        final_draft = workspace_document["workspace"]["draft_versions"][-1]
        acceptance_path = (
            self.book_directory / "runs" / "CH1.A2.draft-acceptance.json"
        )
        acceptance = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "chapter_id": "CH1",
            "assessment_id": "A2",
            "assessment_path": str(assessment_path.relative_to(self.project_path)),
            "assessment_sha256": assessment_document["assessment_sha256"],
            "human_input_path": str(
                human_input_path.relative_to(self.project_path)
            ),
            "human_input_sha256": human_input["human_input_sha256"],
            "workspace_path": str(workspace_path.relative_to(self.project_path)),
            "workspace_sha256": workspace_document["workspace_sha256"],
            "workspace_file_sha256": book_assembler.file_sha256(
                workspace_path
            ),
            "accepted_draft_id": final_draft["draft_id"],
            "accepted_draft_path": final_draft["relative_path"],
            "accepted_draft_sha256": final_draft["sha256"],
            "provider_calls_added": 0,
            "new_research_allowed": False,
        }
        acceptance["draft_acceptance_sha256"] = (
            book_assembler.canonical_json_sha256(
                acceptance,
                "draft_acceptance_sha256",
            )
        )
        self.write_json(acceptance_path, acceptance)

        run_path = self.book_directory / "runs" / "CH1.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run.update(
            {
                "final_pass_requested": False,
                "final_pass_source_draft_id": None,
                "last_assessment_sha256": assessment_document[
                    "assessment_sha256"
                ],
                "completion_authority_kind": "human_draft_acceptance",
                "completion_authority_path": str(
                    acceptance_path.relative_to(self.project_path)
                ),
                "completion_authority_sha256": acceptance[
                    "draft_acceptance_sha256"
                ],
                "accepted_final_draft_id": final_draft["draft_id"],
                "accepted_final_draft_sha256": final_draft["sha256"],
            }
        )
        self.write_json(run_path, run)

        receipt = book_assembler.assemble_candidate_book(
            self.project_path,
            self.book_id,
        )

        chapter_one_inputs = [
            record
            for record in receipt["inputs"]
            if record.get("chapter_id") == "CH1"
        ]
        authority_input = next(
            record
            for record in chapter_one_inputs
            if record["role"] == "chapter_completion_authority"
        )
        human_input_record = next(
            record
            for record in chapter_one_inputs
            if record["role"] == "chapter_human_input"
        )
        self.assertEqual(
            authority_input["sha256"],
            book_assembler.file_sha256(acceptance_path),
        )
        self.assertEqual(
            human_input_record["sha256"],
            book_assembler.file_sha256(human_input_path),
        )

    def test_rejects_incomplete_chapter(self) -> None:
        run_path = self.book_directory / "runs" / "CH2.json"
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["status"] = "active"
        self.write_json(run_path, run)

        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.assemble_candidate_book(
                self.project_path,
                self.book_id,
            )

        self.assertFalse((self.book_directory / "candidate.md").exists())

    def test_rejects_tampered_final_draft(self) -> None:
        (
            self.book_directory / "drafts" / "CH1" / "D2.md"
        ).write_text("Changed after evaluation.\n", encoding="utf-8")

        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.assemble_candidate_book(
                self.project_path,
                self.book_id,
            )

    def test_existing_assembly_is_idempotent_and_detects_drift(self) -> None:
        first = book_assembler.assemble_candidate_book(
            self.project_path,
            self.book_id,
        )
        second = book_assembler.assemble_candidate_book(
            self.project_path,
            self.book_id,
        )
        self.assertEqual(first, second)

        manifest_path = self.book_directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["working_title"] = "Changed after assembly"
        self.write_json(manifest_path, manifest)

        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.assemble_candidate_book(
                self.project_path,
                self.book_id,
            )

    def write_editorial_review(
        self,
        candidate: dict,
        decision: str,
        chapter_revisions: list[dict] | None = None,
    ) -> Path:
        path = self.book_directory / "editorial_review.json"
        synthesis_receipt_path = (
            self.book_directory / "synthesis" / "receipt.json"
        )
        synthesis_receipt = json.loads(
            synthesis_receipt_path.read_text(encoding="utf-8")
        )
        self.write_json(
            path,
            {
                "schema_version": "1.0",
                "book_id": self.book_id,
                "candidate_sha256": candidate["candidate_sha256"],
                "candidate_assembly_sha256": hashlib.sha256(
                    (
                        self.book_directory / "candidate_assembly.json"
                    ).read_bytes()
                ).hexdigest(),
                "synthesis_receipt_path": str(
                    synthesis_receipt_path.relative_to(self.project_path)
                ),
                "synthesis_receipt_sha256": hashlib.sha256(
                    synthesis_receipt_path.read_bytes()
                ).hexdigest(),
                "edition_path": synthesis_receipt["book_path"],
                "edition_sha256": synthesis_receipt["book_sha256"],
                "synthesis_attempt": synthesis_receipt["selected_attempt"],
                "decision": decision,
                "notes": ["Preserve the evidence boundaries."],
                "chapter_revisions": chapter_revisions or [],
                "decided_at": "2026-01-02T00:00:00+00:00",
            },
        )
        return path

    def create_passing_synthesis(
        self,
        candidate: dict,
        edition: str | None = None,
    ) -> dict:
        context = book_synthesis.build_context(
            self.project_path,
            self.book_id,
            candidate,
            self.synthesizer_skill_path,
            protocol_version="staged-v1",
        )
        candidate_body = (
            self.book_directory / "candidate.md"
        ).read_text(encoding="utf-8")
        canonical_excerpt = "Exact first chapter."
        inventory = {
            "book_id": self.book_id,
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "central_question": context["central_question"],
            "curiosity_signature": context["curiosity_signature"],
            "claims": [
                {
                    "claim_id": f"{self.review_id}:C1",
                    "source_kind": "canonical",
                    "statement": "Exact first chapter evidence is preserved.",
                    "candidate_excerpt": canonical_excerpt,
                    "candidate_location": "First chapter",
                    "certainty": "supported",
                    "scope_and_conditions": "This assembled book only.",
                    "evidence_receipt_ids": ["SRC1"],
                    "required_in_edition": True,
                    "importance_reason": "The manifest requires this claim.",
                }
            ],
            "definitions": [],
            "contradictions": [],
            "limitations": [],
            "unresolved_questions": [],
        }
        plan = {
            "book_id": self.book_id,
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "profile": context["profile"],
            "central_answer": "Hashes preserve assembly lineage.",
            "chapter_jobs": [],
            "operations": [],
            "terminology": [],
            "protected_claim_ids": [f"{self.review_id}:C1"],
        }
        book_synthesis.initialize_synthesis(
            self.project_path,
            context,
            inventory,
            plan,
        )
        context, inventory, plan = book_synthesis.load_initialized_synthesis(
            self.project_path,
            self.book_id,
        )
        edition = edition or candidate_body
        submission = book_synthesis.save_attempt(
            self.project_path,
            {
                "book_id": self.book_id,
                "attempt": 1,
                "context_sha256": context["context_sha256"],
                "inventory_sha256": inventory["inventory_sha256"],
                "plan_sha256": plan["plan_sha256"],
                "correction_audit_sha256": None,
                "claim_mappings": [
                    {
                        "claim_id": f"{self.review_id}:C1",
                        "edition_excerpt": canonical_excerpt,
                        "edition_location": "First chapter",
                    }
                ],
                "change_summary": ["Improved whole-book flow."],
            },
            edition,
        )
        return book_synthesis.submit_audit(
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
                        "claim_id": f"{self.review_id}:C1",
                        "status": "preserved",
                        "candidate_excerpt": canonical_excerpt,
                        "edition_excerpt": canonical_excerpt,
                        "explanation": "Meaning and evidence remain unchanged.",
                        "justification": None,
                        "certainty_changed": False,
                        "scope_changed": False,
                        "evidence_relationship_changed": False,
                    }
                ],
                "unmapped_candidate_claims": [],
                "novel_edition_claims": [],
                "lost_limitations": [],
                "terminology_drift": [],
                "central_question_preserved": True,
                "curiosity_preservation": {
                    "status": "preserved",
                    "explanation": "The curiosity signature remains.",
                },
                "overall_status": "pass",
                "blocking_reasons": [],
            },
        )

    def test_finalization_requires_hash_bound_human_approval(self) -> None:
        candidate = book_assembler.assemble_candidate_book(
            self.project_path,
            self.book_id,
        )
        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.finalize_book(
                self.project_path,
                self.book_id,
            )

        synthesized_body = (
            self.book_directory / "candidate.md"
        ).read_text(encoding="utf-8") + "\nSynthesized transition.\n"
        synthesis_receipt = self.create_passing_synthesis(
            candidate,
            synthesized_body,
        )
        with self.assertRaises(book_assembler.BookAssemblyError):
            book_assembler.finalize_book(
                self.project_path,
                self.book_id,
            )
        self.write_editorial_review(
            candidate,
            "approved_for_finalization",
        )
        final = book_assembler.finalize_book(
            self.project_path,
            self.book_id,
        )

        self.assertEqual(final["lifecycle_status"], "final")
        self.assertEqual(
            (self.book_directory / "book.md").read_bytes(),
            (
                self.project_path / synthesis_receipt["book_path"]
            ).read_bytes(),
        )
        self.assertNotEqual(
            (self.book_directory / "book.md").read_bytes(),
            (self.book_directory / "candidate.md").read_bytes(),
        )
        self.assertEqual(
            book_assembler.verify_existing_assembly(
                self.project_path,
                self.book_directory,
                self.book_id,
            ),
            final,
        )

        knowledge_path = self.project_path / "data" / "knowledge"
        self.write_json(
            knowledge_path / "graph.json",
            {
                "schema_version": "1.0",
                "generated_at": "2026-01-02T00:00:00+00:00",
                "nodes": [
                    {
                        "id": "ki-assembly-test-question",
                        "type": "core_question",
                        "title": "Can completed research remain verifiable?",
                        "summary": "Preserve the final synthesis lineage.",
                        "status": "active",
                        "path": (
                            "nodes/review-assembly-test/"
                            "ki-assembly-test-question.md"
                        ),
                        "source_review_ids": [self.review_id],
                        "claim_ids": [],
                        "source_receipt_ids": ["SRC1"],
                        "transcript_references": [],
                    }
                ],
                "edges": [],
            },
        )
        shelf = bookshelf.build_bookshelf(
            self.project_path,
            knowledge_path=knowledge_path,
        )
        catalog = json.loads(
            shelf["catalog_path"].read_text(encoding="utf-8")
        )
        self.assertEqual(catalog["book_count"], 1)
        self.assertEqual(
            catalog["books"][0]["book_sha256"],
            synthesis_receipt["book_sha256"],
        )
        self.assertNotEqual(
            catalog["books"][0]["book_sha256"],
            candidate["candidate_sha256"],
        )

        assembly_path = self.book_directory / "assembly.json"
        editorial_path = self.book_directory / "editorial_review.json"
        approved_assembly = assembly_path.read_bytes()
        approved_editorial = editorial_path.read_bytes()
        editorial = json.loads(approved_editorial)
        editorial["edition_sha256"] = candidate["candidate_sha256"]
        self.write_json(editorial_path, editorial)
        assembly = json.loads(approved_assembly)
        assembly["editorial_review_sha256"] = hashlib.sha256(
            editorial_path.read_bytes()
        ).hexdigest()
        self.write_json(assembly_path, assembly)
        with self.assertRaisesRegex(
            bookshelf.BookshelfError,
            "Human approval does not match the final edition",
        ):
            bookshelf.build_bookshelf(
                self.project_path,
                knowledge_path=knowledge_path,
            )
        editorial_path.write_bytes(approved_editorial)
        assembly_path.write_bytes(approved_assembly)

        assembly = json.loads(approved_assembly)
        assembly["synthesis_receipt_sha256"] = "0" * 64
        self.write_json(assembly_path, assembly)
        with self.assertRaisesRegex(
            bookshelf.BookshelfError,
            "Final assembly does not match its audited synthesis",
        ):
            bookshelf.build_bookshelf(
                self.project_path,
                knowledge_path=knowledge_path,
            )
        assembly_path.write_bytes(approved_assembly)

        final_book_path = self.book_directory / "book.md"
        approved_final = final_book_path.read_bytes()
        final_book_path.write_text("# Tampered final edition\n", encoding="utf-8")
        with self.assertRaisesRegex(
            bookshelf.BookshelfError,
            "Final book hash does not match",
        ):
            bookshelf.build_bookshelf(
                self.project_path,
                knowledge_path=knowledge_path,
            )
        final_book_path.write_bytes(approved_final)

        candidate_path = self.book_directory / "candidate.md"
        candidate_path.write_text("# Tampered candidate\n", encoding="utf-8")
        with self.assertRaisesRegex(
            bookshelf.BookshelfError,
            "Candidate book hash does not match",
        ):
            bookshelf.build_bookshelf(
                self.project_path,
                knowledge_path=knowledge_path,
            )

    def test_revision_archives_candidate_and_human_decision(self) -> None:
        candidate = book_assembler.assemble_candidate_book(
            self.project_path,
            self.book_id,
        )
        self.create_passing_synthesis(candidate)
        self.write_editorial_review(
            candidate,
            "needs_chapter_revision",
            [
                {
                    "chapter_id": "CH1",
                    "instructions": ["Clarify the first mechanism."],
                }
            ],
        )

        archived, decision = (
            book_assembler.archive_candidate_for_revision(
                self.project_path,
                self.book_id,
            )
        )

        self.assertEqual(decision["decision"], "needs_chapter_revision")
        self.assertTrue((archived / "candidate.md").is_file())
        self.assertTrue((archived / "candidate_assembly.json").is_file())
        self.assertTrue((archived / "synthesis").is_dir())
        self.assertTrue((archived / "editorial_review.json").is_file())
        self.assertFalse((self.book_directory / "candidate.md").exists())
        self.assertFalse(
            (self.book_directory / "editorial_review.json").exists()
        )


if __name__ == "__main__":
    unittest.main()
