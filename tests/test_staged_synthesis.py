import asyncio
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import book_synthesis
import backend_selector
import mcp_server
import staged_synthesis
import synthesis_tool_budget


class StagedSynthesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name).resolve()
        self.book_id = "book-staged-test"
        self.book = self.project / "data" / "books" / self.book_id
        self.book.mkdir(parents=True)
        self.profile = (
            self.project
            / "skills"
            / "alexandrian-book-synthesizer"
            / "SKILL.md"
        )
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text("# Profile\n", encoding="utf-8")
        self.chapters = {
            "CH1": "# First\n\nThe first claim stays bounded.\n",
            "CH2": (
                "# Second\n\n"
                "The bounded first claim also applies here.\n\n"
                "The second claim remains uncertain.\n"
            ),
        }
        for chapter_id, text in self.chapters.items():
            path = self.book / "drafts" / chapter_id / "D1.md"
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
        self.candidate_text = (
            "# Test Book\n\n"
            "A locked introduction.\n\n---\n\n"
            + self.chapters["CH1"]
            + "\n---\n\n"
            + self.chapters["CH2"]
            + "\n---\n\n# Sources\n\nA locked source index.\n"
        )
        self.candidate_path = self.book / "candidate.md"
        self.candidate_path.write_text(
            self.candidate_text,
            encoding="utf-8",
        )
        self.manifest_path = self.book / "manifest.json"
        self.review_path = (
            self.project / "data" / "reviews" / "staged-test.json"
        )
        self.write_json(
            self.manifest_path,
            {
                "book_id": self.book_id,
                "central_question": "Can staged editing preserve meaning?",
                "curiosity_signature": {
                    "original_intuition": ["Small calls can remain rigorous."]
                },
                "prohibited_overinterpretations": ["Do not claim validation."],
                "contradictions": [],
                "chapter_plan": [
                    {
                        "chapter_id": "CH1",
                        "working_title": "First",
                        "material_disposition": "include",
                        "claim_ids": ["review-staged:C1"],
                    },
                    {
                        "chapter_id": "CH2",
                        "working_title": "Second",
                        "material_disposition": "include",
                        "claim_ids": [
                            "review-staged:C1",
                            "review-staged:C2",
                        ],
                    },
                ],
            },
        )
        self.write_json(
            self.review_path,
            {
                "claim_index": [
                    {
                        "claim_id": "review-staged:C1",
                        "claim": "The first claim stays bounded.",
                        "source_receipt_ids": ["SRC1"],
                    },
                    {
                        "claim_id": "review-staged:C2",
                        "claim": "The second claim remains uncertain.",
                        "source_receipt_ids": ["SRC2"],
                    },
                ]
            },
        )
        inputs = [
            self.input_record("manifest", self.manifest_path),
            self.input_record("research_review", self.review_path),
        ]
        for chapter_id in self.chapters:
            path = self.book / "drafts" / chapter_id / "D1.md"
            inputs.append(
                {
                    **self.input_record("chapter_draft", path),
                    "chapter_id": chapter_id,
                    "draft_id": "D1",
                }
            )
        self.candidate = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            "lifecycle_status": "candidate",
            "candidate_path": str(
                self.candidate_path.relative_to(self.project)
            ),
            "candidate_sha256": self.sha(self.candidate_path),
            "source_receipt_ids": ["SRC1", "SRC2"],
            "inputs": inputs,
        }
        self.write_json(
            self.book / "candidate_assembly.json",
            self.candidate,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def sha(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def write_json(self, path: Path, document: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def input_record(self, role: str, path: Path) -> dict:
        return {
            "role": role,
            "path": str(path.relative_to(self.project)),
            "sha256": self.sha(path),
        }

    def prepare(self) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
            protocol_version="staged-v1",
        )

    def inventory_proposal(
        self,
        chapter_id: str,
        candidate_only: bool = False,
        audited: bool = False,
    ) -> dict:
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            chapter_id,
        )
        source_claims = {
            "review-staged:C1": {
                "statement": "The first claim stays bounded.",
                "receipt": "SRC1",
            },
            "review-staged:C2": {
                "statement": "The second claim remains uncertain.",
                "receipt": "SRC2",
            },
        }
        claims = []
        for claim_id in task["canonical_claim_ids"]:
            source = source_claims[claim_id]
            excerpt = source["statement"]
            if chapter_id == "CH2" and claim_id == "review-staged:C1":
                excerpt = "The bounded first claim also applies here."
            claim = {
                    "claim_id": claim_id,
                    "source_kind": "canonical",
                    "statement": source["statement"],
                "candidate_excerpt": excerpt,
                "candidate_location": f"{chapter_id} paragraph 1",
                "certainty": "bounded",
                "scope_and_conditions": "This test book only.",
                "evidence_receipt_ids": [source["receipt"]],
                    "required_in_edition": True,
                    "importance_reason": "Canonical chapter claim.",
                }
            if audited:
                claim.update(
                    {
                        "attribution": "The approved research review.",
                        "evidence_relationship": "Supported by the named receipt.",
                        "authority_type": "source_evidence",
                        "resolution_status": "open",
                        "authority_source": "canonical_claim",
                        "authority_ref": claim_id,
                        "authoritative_text": source["statement"],
                        "normalized_summary": source["statement"],
                        "protected_semantics": {
                            "claim_role": "research_claim",
                            "normative_force": "none",
                            "normative_polarity": "not_applicable",
                            "normative_surface_form": None,
                            "certainty": "bounded",
                            "scope_and_conditions": "This test book only.",
                            "attribution": "The approved research review.",
                            "evidence_relationship": (
                                "Supported by the named receipt."
                            ),
                            "resolution_status": "open",
                        },
                    }
                )
            claims.append(claim)
        if candidate_only:
            statement = "The first claim stays bounded."
            claim = {
                    "claim_id": "LOCAL1",
                    "source_kind": "candidate_only",
                    "statement": statement,
                    "candidate_excerpt": statement,
                    "candidate_location": f"{chapter_id} paragraph 1",
                    "certainty": "bounded",
                    "scope_and_conditions": "Local restatement.",
                    "evidence_receipt_ids": [],
                    "required_in_edition": True,
                    "importance_reason": "Preserve the local framing.",
                }
            if audited:
                claim.update(
                    {
                        "attribution": "The candidate chapter.",
                        "evidence_relationship": "Candidate framing only.",
                        "authority_type": "candidate_prose",
                        "resolution_status": "open",
                        "authority_source": "candidate_excerpt",
                        "authority_ref": "candidate_excerpt",
                        "authoritative_text": statement,
                        "normalized_summary": statement,
                        "protected_semantics": {
                            "claim_role": "observation",
                            "normative_force": "none",
                            "normative_polarity": "not_applicable",
                            "normative_surface_form": None,
                            "certainty": "bounded",
                            "scope_and_conditions": "Local restatement.",
                            "attribution": "The candidate chapter.",
                            "evidence_relationship": (
                                "Candidate framing only."
                            ),
                            "resolution_status": "open",
                        },
                    }
                )
            claims.append(claim)
        return {
            "book_id": self.book_id,
            "unit_id": chapter_id,
            "context_sha256": task["context_sha256"],
            "candidate_sha256": task["candidate_sha256"],
            "packet_sha256": task["packet_sha256"],
            "claims": claims,
            "definitions": [],
            "limitations": (
                ["The second claim remains uncertain."]
                if chapter_id == "CH2"
                else []
            ),
            "unresolved_questions": [],
        }

    def inventory_contribution(
        self,
        chapter_id: str,
        *,
        reverse_canonical: bool = False,
    ) -> dict:
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            chapter_id,
        )
        legacy = self.inventory_proposal(
            chapter_id,
            candidate_only=chapter_id == "CH1",
            audited=True,
        )
        fields = (
            "claim_id",
            "candidate_excerpt",
            "candidate_location",
            "certainty",
            "scope_and_conditions",
            "attribution",
            "evidence_relationship",
            "resolution_status",
            "normalized_summary",
            "protected_semantics",
            "importance_reason",
        )
        canonical = [
            {field: claim[field] for field in fields}
            for claim in legacy["claims"]
            if claim["source_kind"] == "canonical"
        ]
        if reverse_canonical:
            canonical.reverse()
        candidate = [
            {field: claim[field] for field in fields}
            for claim in legacy["claims"]
            if claim["source_kind"] == "candidate_only"
        ]
        return {
            "book_id": self.book_id,
            "unit_id": chapter_id,
            "input_hash": task["input_hash"],
            "canonical_claims": canonical,
            "candidate_claims": candidate,
            "definitions": legacy["definitions"],
            "limitations": legacy["limitations"],
            "unresolved_questions": legacy["unresolved_questions"],
        }

    def open_inventory_tool_action(
        self,
        task: dict,
        suffix: str,
    ) -> tuple[str, dict[str, str]]:
        action_id = f"inventory-{task['unit_id']}-{suffix}"
        event = {
            "schema_version": "1.0",
            "event": "contract_opened",
            "book_id": self.book_id,
            "cycle_id": "cycle-001",
            "action_id": action_id,
            "stage": f"inventory-{task['unit_id']}",
            "backend": "codex",
            "input_hash": task["input_hash"],
            "tool_budget": {
                "controlled_tools": [
                    synthesis_tool_budget.INVENTORY_READ_TOOL,
                    synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                ]
            },
        }
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        values = {
            "book_id": self.book_id,
            "cycle_id": "cycle-001",
            "action_id": action_id,
            "stage": event["stage"],
            "backend": "codex",
            "input_hash": task["input_hash"],
        }
        return action_id, {
            synthesis_tool_budget.CONTEXT_ENV[field]: value
            for field, value in values.items()
        }

    def initialize_foundation(self) -> tuple[dict, dict, dict]:
        self.prepare()
        for chapter_id in ("CH1", "CH2"):
            staged_synthesis.save_inventory_unit(
                self.project,
                self.inventory_proposal(
                    chapter_id,
                    candidate_only=chapter_id == "CH1",
                ),
            )
        inventory = staged_synthesis.finalize_inventory(
            self.project,
            self.book_id,
        )
        context = book_synthesis.read_json_object(
            staged_synthesis.staging_directory(
                self.project,
                self.book_id,
            )
            / "context.json",
            "context",
        )
        protected = [claim["claim_id"] for claim in inventory["claims"]]
        staged_synthesis.save_plan_and_promote(
            self.project,
            {
                "book_id": self.book_id,
                "context_sha256": context["context_sha256"],
                "candidate_sha256": context["candidate_sha256"],
                "profile": context["profile"],
                "central_answer": "Staged editing can be checked.",
                "chapter_jobs": [
                    {"chapter_id": "CH1", "job": "State the bounded claim."},
                    {"chapter_id": "CH2", "job": "Preserve uncertainty."},
                ],
                "operations": [
                    {
                        "operation_id": "OP1",
                        "kind": "transition",
                        "target": "CH1",
                        "description": "Clarify the opening.",
                        "protected_claim_ids": protected[:2],
                    },
                    {
                        "operation_id": "OP2",
                        "kind": "conclusion",
                        "target": "CH2",
                        "description": "Close within the evidence.",
                        "protected_claim_ids": protected[2:],
                    },
                ],
                "terminology": [],
                "protected_claim_ids": protected,
            },
        )
        return book_synthesis.load_initialized_synthesis(
            self.project,
            self.book_id,
        )

    def create_terminal_hold(self) -> str:
        self.initialize_foundation()
        self.save_editions()
        self.save_audits(blocking_chapter="CH2")
        self.save_editions(attempt=2)
        receipt = self.save_audits(attempt=2, blocking_chapter="CH2")
        self.assertEqual(receipt["lifecycle_status"], "synthesis_held")
        return self.sha(self.book / "synthesis" / "receipt.json")

    def create_sharded_terminal_synthesis_hold(self) -> dict:
        self.authorize_and_prepare_sharded_cycle()
        self.promote_active_sharded_cycle()
        first = self.save_audits(blocking_chapter="CH2")
        self.assertEqual(first["status"], "correction_required")
        self.save_editions(attempt=2)
        receipt = self.save_audits(
            attempt=2,
            blocking_chapter="CH2",
        )
        self.assertEqual(receipt["lifecycle_status"], "synthesis_held")
        return receipt

    def authorize_and_prepare_audited_cycle(
        self,
        decisions: list[dict] | None = None,
    ) -> tuple[dict, list[str]]:
        authorization = book_synthesis.authorize_fresh_synthesis_cycle(
            self.project,
            self.book_id,
            "The held synthesis exposed an unaudited plan decision.",
            decisions or [],
        )
        self.prepare()
        for chapter_id in ("CH1", "CH2"):
            staged_synthesis.save_inventory_unit(
                self.project,
                self.inventory_proposal(
                    chapter_id,
                    candidate_only=chapter_id == "CH1",
                    audited=True,
                ),
            )
        inventory = staged_synthesis.finalize_inventory(
            self.project,
            self.book_id,
        )
        return authorization, [
            claim["claim_id"] for claim in inventory["claims"]
        ]

    def authorize_and_prepare_sharded_cycle(self) -> dict[str, str]:
        prior_receipt_sha = self.create_terminal_hold()
        self.authorize_and_prepare_audited_cycle()
        cycle_index = self.book / "synthesis_cycles" / "index.json"
        cycle_two_inventory = (
            self.book
            / "synthesis_cycles"
            / "cycle-002_staging"
            / "inventory.json"
        )
        cycle_two_context = cycle_two_inventory.parent / "context.json"
        preserved = {
            "receipt": prior_receipt_sha,
            "index": self.sha(cycle_index),
            "inventory": self.sha(cycle_two_inventory),
            "context": self.sha(cycle_two_context),
        }
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        events = []
        for backend in ("claude", "codex"):
            events.append(
                {
                    "schema_version": "1.0",
                    "event": "contract_failed",
                    "action_id": "whole-book-plan-P1-test",
                    "book_id": self.book_id,
                    "stage": "whole-book-plan-P1",
                    "backend": backend,
                    "reason": f"{backend} produced no P1.",
                }
            )
        ledger.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )
        book_synthesis.authorize_sharded_synthesis_cycle(
            self.project,
            self.book_id,
            "Replace the exhausted monolithic P1 transaction with shards.",
        )
        self.prepare()
        staged_synthesis.reuse_prior_verified_inventory(
            self.project,
            self.book_id,
        )
        staged_synthesis.prepare_plan_shard_inputs(
            self.project,
            self.book_id,
        )
        return preserved

    def create_plan_shard_hold(
        self,
        chapter_id: str = "CH1",
        *,
        legacy_cycle_three: bool = False,
    ) -> dict:
        active = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(active)
        cycle_id = active["cycle_id"]
        stage = f"plan-shard-{chapter_id}"
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        events = []
        for backend in ("codex", "claude"):
            action_id = f"{cycle_id}-{chapter_id}-{backend}"
            events.extend(
                [
                    {
                        "schema_version": "1.0",
                        "event": "contract_opened",
                        "action_id": action_id,
                        "book_id": self.book_id,
                        "stage": stage,
                        "backend": backend,
                        **(
                            {}
                            if legacy_cycle_three
                            else {"cycle_id": cycle_id}
                        ),
                    },
                    {
                        "schema_version": "1.0",
                        "event": "contract_failed",
                        "action_id": action_id,
                        "book_id": self.book_id,
                        "stage": stage,
                        "backend": backend,
                        "reason": "expected_artifact_missing",
                        **(
                            {}
                            if legacy_cycle_three
                            else {"cycle_id": cycle_id}
                        ),
                    },
                ]
            )
        with ledger.open("a", encoding="utf-8") as stream:
            for event in events:
                stream.write(json.dumps(event) + "\n")
        task = staged_synthesis.plan_shard_task(
            self.project,
            self.book_id,
            chapter_id,
        )
        hold = {
            "schema_version": "1.0",
            "book_id": self.book_id,
            **(
                {}
                if legacy_cycle_three
                else {"cycle_id": cycle_id}
            ),
            "chapter_id": chapter_id,
            "stage": stage,
            "input_hash": task["input_hash"],
            "status": "provider_budget_exhausted",
            "maximum_provider_calls": 2,
            "consumed_backends": ["codex", "claude"],
            "stage_event_sha256": hashlib.sha256(
                json.dumps(
                    events,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
            "controller_path": str(ledger.relative_to(self.project)),
            "recorded_at": "2026-07-29T00:00:00+00:00",
        }
        hold["hold_sha256"] = book_synthesis.canonical_json_sha256(
            hold,
            "hold_sha256",
        )
        path = (
            staged_synthesis.staging_directory(
                self.project,
                self.book_id,
            )
            / "plan_shard_holds"
            / f"{chapter_id}.json"
        )
        book_synthesis.atomic_write_json(path, hold)
        return hold

    def prepare_active_sharded_cycle(self) -> None:
        self.prepare()
        staged_synthesis.reuse_prior_verified_inventory(
            self.project,
            self.book_id,
        )
        staged_synthesis.prepare_plan_shard_inputs(
            self.project,
            self.book_id,
        )

    def promote_active_sharded_cycle(self) -> None:
        self.save_all_plan_shards()
        staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        self.sharded_audit()
        staged_synthesis.promote_approved_plan(
            self.project,
            self.book_id,
        )
        self.save_editions()

    def save_one_passing_audit(self, chapter_id: str) -> dict:
        task = staged_synthesis.audit_task(
            self.project,
            self.book_id,
            1,
            chapter_id,
        )
        return staged_synthesis.save_audit_unit(
            self.project,
            {
                "book_id": self.book_id,
                "attempt": 1,
                "unit_id": chapter_id,
                "context_sha256": task["context_sha256"],
                "inventory_sha256": task["inventory_sha256"],
                "plan_sha256": task["plan_sha256"],
                "claim_results": [
                    {
                        "claim_id": claim["claim_id"],
                        "status": "preserved",
                        "candidate_excerpt": claim["candidate_excerpt"],
                        "edition_excerpt": claim["candidate_excerpt"],
                        "explanation": "Compared the exact chapter text.",
                        "justification": None,
                        "certainty_changed": False,
                        "scope_changed": False,
                        "evidence_relationship_changed": False,
                        "changed_dimensions": [],
                    }
                    for claim in task["claims"]
                ],
            },
        )

    def create_audit_provider_hold(self, chapter_id: str) -> dict:
        active = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(active)
        cycle_id = active["cycle_id"]
        stage = f"audit-S1-{chapter_id}"
        task = staged_synthesis.audit_task(
            self.project,
            self.book_id,
            1,
            chapter_id,
        )
        target = (
            staged_synthesis.staged_directory(
                self.project,
                self.book_id,
            )
            / "attempts"
            / "S1"
            / "audits"
            / f"{chapter_id}.json"
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for number, backend in enumerate(("claude", "codex"), start=1):
            action_id = f"{stage}-{number}"
            records.extend(
                [
                    {
                        "schema_version": "1.0",
                        "event": "contract_opened",
                        "action_id": action_id,
                        "book_id": self.book_id,
                        "cycle_id": cycle_id,
                        "stage": stage,
                        "backend": backend,
                    },
                    {
                        "schema_version": "1.0",
                        "event": "contract_failed",
                        "action_id": action_id,
                        "book_id": self.book_id,
                        "cycle_id": cycle_id,
                        "stage": stage,
                        "backend": backend,
                        "reason": "expected_artifact_missing",
                        "failure_classification": "provider_timeout",
                        "diagnostic_sha256": f"{number}" * 64,
                    },
                ]
            )
        with ledger.open("a", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")
        return staged_synthesis.save_provider_stage_hold(
            self.project,
            self.book_id,
            cycle_id,
            stage,
            target,
            backend_selector.synthesis_stage_input_sha256(task),
            2,
            ["claude", "codex"],
        )

    def plan_shard(
        self,
        chapter_id: str,
        *,
        terminology: list[dict] | None = None,
    ) -> dict:
        plan_input = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            chapter_id,
        )
        claim_ids = plan_input["chapter_claim_ids"]
        return {
            "book_id": self.book_id,
            "shard_id": chapter_id,
            "chapter_id": chapter_id,
            "input_hash": plan_input["input_hash"],
            "chapter_job": f"Preserve the bounded work in {chapter_id}.",
            "operations": [
                {
                    "operation_id": "OP1",
                    "kind": "transition",
                    "target": chapter_id,
                    "description": "Keep the local explanation bounded.",
                    "protected_claim_ids": claim_ids,
                    "proposed_text": None,
                    "authority_type": "candidate",
                    "authority_refs": claim_ids,
                    "effect_type": "preserve",
                    "resolution_effect": "remains_open",
                }
            ],
            "claim_dispositions": [
                {
                    "claim_id": claim_id,
                    "disposition": "preserved_as_unresolved",
                    "related_claim_ids": [],
                    "explanation": "Keep the claim and its open status.",
                    "authority_type": "candidate",
                    "authority_refs": [claim_id],
                    "resolution_effect": "remains_open",
                }
                for claim_id in claim_ids
            ],
            "terminology": terminology or [],
            "unresolved_decisions": [],
            "cross_chapter_dependencies": [],
        }

    def save_all_plan_shards(self) -> None:
        for chapter_id in ("CH1", "CH2"):
            staged_synthesis.save_plan_shard(
                self.project,
                self.plan_shard(chapter_id),
            )

    def sharded_audit(self, failing: str | None = None) -> dict:
        task = staged_synthesis.sharded_plan_audit_task(
            self.project,
            self.book_id,
        )
        findings = [
            {
                "question_id": question,
                "status": "fail" if question == failing else "pass",
                "explanation": "Compared the merged plan with its shard inputs.",
            }
            for question in task["questions"]
        ]
        return staged_synthesis.save_sharded_plan_audit(
            self.project,
            {
                "book_id": self.book_id,
                "context_sha256": task["context_sha256"],
                "inventory_sha256": task["inventory_sha256"],
                "merged_plan_sha256": task["merged_plan_sha256"],
                "findings": findings,
                "verdict": "reject" if failing else "approve",
            },
        )

    def audited_plan(
        self,
        protected: list[str],
        *,
        authority_type: str = "candidate",
        authority_refs: list[str] | None = None,
        effect_type: str = "preserve",
        resolution_effect: str = "remains_open",
        terminology: list[dict] | None = None,
        attempt: int = 1,
        correction_audit_sha256: str | None = None,
    ) -> dict:
        context, _ = staged_synthesis._load_staging(
            self.project,
            self.book_id,
        )
        refs = (
            list(authority_refs)
            if authority_refs is not None
            else [protected[0]]
        )
        return {
            "book_id": self.book_id,
            "context_sha256": context["context_sha256"],
            "candidate_sha256": context["candidate_sha256"],
            "profile": context["profile"],
            "central_answer": (
                "The proposed accessibility profile remains open to testing."
            ),
            "central_answer_claim_ids": protected,
            "central_answer_resolution_effect": "remains_open",
            "chapter_jobs": [
                {"chapter_id": "CH1", "job": "Preserve the first claim."},
                {"chapter_id": "CH2", "job": "Preserve uncertainty."},
            ],
            "operations": [
                {
                    "operation_id": "OP1",
                    "kind": (
                        "terminology"
                        if effect_type == "rename"
                        else "transition"
                    ),
                    "target": "CH1 and CH2",
                    "description": "Keep the unresolved profile neutral.",
                    "protected_claim_ids": protected,
                    "proposed_text": "the proposed accessibility profile",
                    "authority_type": authority_type,
                    "authority_refs": refs,
                    "effect_type": effect_type,
                    "resolution_effect": resolution_effect,
                }
            ],
            "terminology": terminology or [],
            "protected_claim_ids": protected,
            "plan_attempt": attempt,
            "correction_audit_sha256": correction_audit_sha256,
        }

    def audit_current_plan(
        self,
        classifications: dict[str, str] | None = None,
    ) -> dict:
        task = staged_synthesis.plan_audit_task(
            self.project,
            self.book_id,
        )
        choices = classifications or {}
        findings = [
            {
                "target_id": target["target_id"],
                "classification": choices.get(
                    target["target_id"],
                    (
                        "preserves_unresolved"
                        if target["target_id"] == "central_answer"
                        else "candidate_supported"
                    ),
                ),
                "explanation": "Compared the plan with its declared authority.",
            }
            for target in task["targets"]
        ]
        blockers = [
            finding["target_id"]
            for finding in findings
            if finding["classification"] == "unsupported"
        ]
        return staged_synthesis.save_plan_audit(
            self.project,
            {
                "book_id": self.book_id,
                "plan_attempt": task["plan_attempt"],
                "context_sha256": task["context_sha256"],
                "inventory_sha256": task["inventory_sha256"],
                "plan_draft_sha256": task["plan_draft_sha256"],
                "findings": findings,
                "overall_status": "blocking" if blockers else "pass",
                "blocking_reasons": blockers,
            },
        )

    def save_editions(self, attempt: int = 1) -> None:
        required = staged_synthesis.required_edition_units(
            self.project,
            self.book_id,
            attempt,
        )
        for chapter_id in required:
            task = staged_synthesis.edition_task(
                self.project,
                self.book_id,
                attempt,
                chapter_id,
            )
            markdown = self.chapters[chapter_id]
            mappings = [
                {
                    "claim_id": claim["claim_id"],
                    "edition_excerpt": claim["candidate_excerpt"],
                    "edition_location": f"{chapter_id} paragraph 1",
                }
                for claim in task["assigned_claims"]
            ]
            staged_synthesis.save_edition_unit(
                self.project,
                {
                    "book_id": self.book_id,
                    "attempt": attempt,
                    "unit_id": chapter_id,
                    "context_sha256": task["context_sha256"],
                    "inventory_sha256": task["inventory_sha256"],
                    "plan_sha256": task["plan_sha256"],
                    "packet_sha256": task["packet_sha256"],
                    "claim_mappings": mappings,
                    "change_summary": ["Preserved the bounded chapter."],
                },
                markdown,
            )
        staged_synthesis.assemble_attempt(
            self.project,
            self.book_id,
            attempt,
        )

    def save_audits(
        self,
        attempt: int = 1,
        blocking_chapter: str | None = None,
    ) -> dict:
        _, _, _, index = staged_synthesis._load_promoted(
            self.project,
            self.book_id,
        )
        for unit in index["units"]:
            chapter_id = unit["unit_id"]
            task = staged_synthesis.audit_task(
                self.project,
                self.book_id,
                attempt,
                chapter_id,
            )
            findings = []
            for claim in task["claims"]:
                blocking = chapter_id == blocking_chapter
                findings.append(
                    {
                        "claim_id": claim["claim_id"],
                        "status": "strengthened" if blocking else "preserved",
                        "candidate_excerpt": claim["candidate_excerpt"],
                        "edition_excerpt": claim["candidate_excerpt"],
                        "explanation": "Compared the exact chapter text.",
                        "justification": None,
                        "certainty_changed": blocking,
                        "scope_changed": False,
                        "evidence_relationship_changed": False,
                        "changed_dimensions": (
                            ["certainty"] if blocking else []
                        ),
                    }
                )
            staged_synthesis.save_audit_unit(
                self.project,
                {
                    "book_id": self.book_id,
                    "attempt": attempt,
                    "unit_id": chapter_id,
                    "context_sha256": task["context_sha256"],
                    "inventory_sha256": task["inventory_sha256"],
                    "plan_sha256": task["plan_sha256"],
                    "claim_results": findings,
                },
            )
        task = staged_synthesis.global_audit_task(
            self.project,
            self.book_id,
            attempt,
        )
        staged_synthesis.save_global_audit(
            self.project,
            {
                "book_id": self.book_id,
                "attempt": attempt,
                "context_sha256": task["context_sha256"],
                "inventory_sha256": task["inventory_sha256"],
                "plan_sha256": task["plan_sha256"],
                "submission_sha256": task["submission_sha256"],
                "book_sha256": task["book_sha256"],
                "unmapped_candidate_claims": [],
                "novel_edition_claims": [],
                "lost_limitations": [],
                "terminology_drift": [],
                "affected_unit_ids": (
                    [blocking_chapter] if blocking_chapter else []
                ),
                "central_question_preserved": True,
                "curiosity_preservation": {
                    "status": "preserved",
                    "explanation": "The original curiosity remains.",
                },
            },
        )
        return staged_synthesis.finalize_audit(
            self.project,
            self.book_id,
            attempt,
        )

    def test_staged_pass_reaches_existing_editorial_contract(self) -> None:
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "not_started",
        )
        _, inventory, _ = self.initialize_foundation()
        occurrences = [
            claim
            for claim in inventory["claims"]
            if claim["source_kind"] == "canonical_occurrence"
        ]
        self.assertEqual(
            [claim["claim_id"] for claim in occurrences],
            ["CO1", "CO2", "CO3"],
        )
        self.assertEqual(
            [
                claim["canonical_claim_id"]
                for claim in occurrences
                if claim["canonical_claim_id"] == "review-staged:C1"
            ],
            ["review-staged:C1", "review-staged:C1"],
        )
        self.assertNotEqual(
            occurrences[0]["candidate_excerpt"],
            occurrences[1]["candidate_excerpt"],
        )
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "edition_units_S1",
        )
        self.save_editions()
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "audit_units_S1",
        )
        result = self.save_audits()
        self.assertEqual(result["lifecycle_status"], "ready_for_editorial")
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "ready_for_editorial",
        )
        final = self.project / result["book_path"]
        self.assertIn("A locked introduction.", final.read_text())
        self.assertIn("A locked source index.", final.read_text())

    def test_inventory_unit_is_idempotent_but_conflicts_are_rejected(self) -> None:
        self.prepare()
        proposal = self.inventory_proposal("CH1")
        first = staged_synthesis.save_inventory_unit(self.project, proposal)
        second = staged_synthesis.save_inventory_unit(self.project, proposal)
        self.assertEqual(
            first["inventory_unit_sha256"],
            second["inventory_unit_sha256"],
        )
        changed = dict(proposal)
        changed["limitations"] = ["Different."]
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_inventory_unit(self.project, changed)

    def test_clean_start_uses_sharded_hash_bound_inventory(self) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        context = book_synthesis.read_json_object(
            staged_synthesis.staging_directory(
                self.project,
                self.book_id,
            )
            / "context.json",
            "context",
        )
        self.assertEqual(
            context["protocol_version"],
            book_synthesis.SHARDED_PLAN_PROTOCOL,
        )
        self.assertEqual(context["cycle_id"], "cycle-001")
        self.assertIsNone(context["cycle_authorization_sha256"])
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH2",
        )
        self.assertEqual(
            [slot["claim_id"] for slot in task["canonical_claim_slots"]],
            ["review-staged:C1", "review-staged:C2"],
        )
        self.assertEqual(
            task["tool_contract_version"],
            "inventory-authority-slots-v2",
        )
        self.assertEqual(
            task["interpretation_contract"]["normative_surface_form"][
                "match_mode"
            ],
            "case_sensitive_contiguous_substring",
        )
        read = staged_synthesis.read_inventory_input(
            self.project,
            self.book_id,
            "CH2",
            task["input_hash"],
        )
        self.assertEqual(read["input_hash"], task["input_hash"])
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "input hash is stale",
        ):
            staged_synthesis.read_inventory_input(
                self.project,
                self.book_id,
                "CH2",
                "0" * 64,
            )
        metadata_path = self.project / task["packet_metadata_path"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["working_title"] = "Changed after hashing"
        self.write_json(metadata_path, metadata)
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "metadata changed",
        ):
            staged_synthesis.read_inventory_input(
                self.project,
                self.book_id,
                "CH2",
                task["input_hash"],
            )

    def test_inventory_order_is_deterministic_and_initial_plan_promotes(
        self,
    ) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        staged_synthesis.save_inventory_contribution(
            self.project,
            self.inventory_contribution("CH1"),
        )
        chapter_two = staged_synthesis.save_inventory_contribution(
            self.project,
            self.inventory_contribution(
                "CH2",
                reverse_canonical=True,
            ),
        )
        self.assertEqual(
            [
                claim["claim_id"]
                for claim in chapter_two["claims"]
                if claim["source_kind"] == "canonical"
            ],
            ["review-staged:C1", "review-staged:C2"],
        )
        staged_synthesis.prepare_plan_shard_inputs(
            self.project,
            self.book_id,
        )
        self.save_all_plan_shards()
        staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        self.sharded_audit()
        staged_synthesis.promote_approved_plan(
            self.project,
            self.book_id,
        )
        context, _, _ = book_synthesis.load_initialized_synthesis(
            self.project,
            self.book_id,
        )
        self.assertEqual(context["cycle_id"], "cycle-001")
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "edition_units_S1",
        )
        self.save_editions()
        receipt = self.save_audits()
        self.assertEqual(
            receipt["lifecycle_status"],
            "ready_for_editorial",
        )
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "ready_for_editorial",
        )
        book_synthesis.synthesis_cycle_directory(
            self.project,
            self.book_id,
        ).mkdir(parents=True)
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "no valid plan audit",
        ):
            book_synthesis.load_initialized_synthesis(
                self.project,
                self.book_id,
            )

    def test_inventory_missing_canonical_slot_is_correctable(self) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        proposal = self.inventory_contribution("CH2")
        proposal["canonical_claims"].pop()
        with self.assertRaisesRegex(
            staged_synthesis.InventoryCorrectionError,
            "match the supplied slots",
        ):
            staged_synthesis.save_inventory_contribution(
                self.project,
                proposal,
            )

    def test_inventory_mcp_correction_survives_restart_and_is_bounded(
        self,
    ) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        action_id, environment = self.open_inventory_tool_action(
            task,
            "mcp-correction",
        )
        invalid = self.inventory_contribution("CH1")
        semantics = invalid["canonical_claims"][0]["protected_semantics"]
        semantics["normative_force"] = "requirement"
        semantics["normative_polarity"] = "positive"
        semantics["normative_surface_form"] = (
            "the first claim stays bounded."
        )

        with (
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.dict(os.environ, environment, clear=False),
        ):
            asyncio.run(
                mcp_server.mcp.call_tool(
                    synthesis_tool_budget.INVENTORY_READ_TOOL,
                    {
                        "book_id": self.book_id,
                        "unit_id": "CH1",
                        "input_hash": task["input_hash"],
                    },
                )
            )
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "exact_authoritative_substring_required",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": invalid},
                    )
                )

        first_result_path = (
            self.book
            / "runs"
            / "synthesis_tool_budgets"
            / action_id
            / "submit-01.result.json"
        )
        first_result = json.loads(
            first_result_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            first_result["outcome"],
            "correctable_validation_rejected",
        )
        self.assertEqual(
            first_result["validation_diagnostic"],
            {
                "code": "exact_authoritative_substring_required",
                "claim_id": "review-staged:C1",
                "field_path": (
                    "protected_semantics.normative_surface_form"
                ),
                "correctable": True,
                "submission_attempt": 1,
            },
        )

        corrected = self.inventory_contribution("CH1")
        semantics = corrected["canonical_claims"][0][
            "protected_semantics"
        ]
        semantics["normative_force"] = "requirement"
        semantics["normative_polarity"] = "positive"
        semantics["normative_surface_form"] = (
            "The first claim stays bounded."
        )
        # A new environment scope represents reloading the remaining slot
        # from durable receipts after a process restart.
        with (
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.dict(os.environ, environment, clear=False),
        ):
            asyncio.run(
                mcp_server.mcp.call_tool(
                    synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                    {"proposal": corrected},
                )
            )
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "submission budget is exhausted",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": corrected},
                    )
                )

        artifact = (
            staged_synthesis.staging_directory(
                self.project,
                self.book_id,
            )
            / "inventory_units"
            / "CH1.json"
        )
        self.assertTrue(artifact.is_file())

    def test_all_model_owned_inventory_fields_use_typed_corrections(
        self,
    ) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        mutations = {
            "candidate_excerpt": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(candidate_excerpt="not in the chapter"),
            "candidate_location": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(candidate_location=""),
            "certainty": lambda proposal: proposal["canonical_claims"][
                0
            ].update(certainty=""),
            "scope_and_conditions": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(scope_and_conditions=""),
            "attribution": lambda proposal: proposal["canonical_claims"][
                0
            ].update(attribution=""),
            "evidence_relationship": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(evidence_relationship=""),
            "resolution_status": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(resolution_status="invalid"),
            "normalized_summary": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(normalized_summary=""),
            "protected_semantics": lambda proposal: proposal[
                "canonical_claims"
            ][0]["protected_semantics"].update(certainty="different"),
            "importance_reason": lambda proposal: proposal[
                "canonical_claims"
            ][0].update(importance_reason=""),
            "definitions": lambda proposal: proposal.update(
                definitions=[""]
            ),
            "limitations": lambda proposal: proposal.update(
                limitations=[""]
            ),
            "unresolved_questions": lambda proposal: proposal.update(
                unresolved_questions=[""]
            ),
        }
        for field, mutate in mutations.items():
            with self.subTest(field=field):
                proposal = self.inventory_contribution("CH1")
                mutate(proposal)
                with self.assertRaises(
                    staged_synthesis.InventoryCorrectionError
                ) as caught:
                    staged_synthesis.save_inventory_contribution(
                        self.project,
                        proposal,
                    )
                diagnostic = caught.exception.safe_diagnostic()
                self.assertTrue(diagnostic["correctable"])
                self.assertIsInstance(diagnostic["code"], str)
                self.assertIsInstance(diagnostic["field_path"], str)

    def test_stale_inventory_hash_is_terminal_without_correction_slot(
        self,
    ) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        action_id, environment = self.open_inventory_tool_action(
            task,
            "stale-hash",
        )
        stale = self.inventory_contribution("CH1")
        stale["input_hash"] = "f" * 64
        with (
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.dict(os.environ, environment, clear=False),
        ):
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "input hash is stale",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": stale},
                    )
                )
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "only after a correctable",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": self.inventory_contribution("CH1")},
                    )
                )
        result = json.loads(
            (
                self.book
                / "runs"
                / "synthesis_tool_budgets"
                / action_id
                / "submit-01.result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(result["outcome"], "tool_execution_rejected")
        self.assertIsNone(result["validation_diagnostic"])

    def test_two_invalid_inventory_submissions_are_terminal(self) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        _, environment = self.open_inventory_tool_action(
            task,
            "two-invalid",
        )
        invalid = self.inventory_contribution("CH1")
        invalid["canonical_claims"][0]["candidate_excerpt"] = "absent"
        with (
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.dict(os.environ, environment, clear=False),
        ):
            for _ in range(2):
                with self.assertRaisesRegex(
                    mcp_server.ToolError,
                    "exact_candidate_substring_required",
                ):
                    asyncio.run(
                        mcp_server.mcp.call_tool(
                            synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                            {"proposal": invalid},
                        )
                    )
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "submission budget is exhausted",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": self.inventory_contribution("CH1")},
                    )
                )

    def test_inventory_schema_rejection_is_structured_and_correctable(
        self,
    ) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        action_id, environment = self.open_inventory_tool_action(
            task,
            "schema-correction",
        )
        invalid = self.inventory_contribution("CH1")
        del invalid["canonical_claims"][0]["candidate_location"]
        with (
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.dict(os.environ, environment, clear=False),
        ):
            with self.assertRaises(mcp_server.ToolError):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": invalid},
                    )
                )
            asyncio.run(
                mcp_server.mcp.call_tool(
                    synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                    {"proposal": self.inventory_contribution("CH1")},
                )
            )
        result = json.loads(
            (
                self.book
                / "runs"
                / "synthesis_tool_budgets"
                / action_id
                / "submit-01.result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            result["validation_diagnostic"],
            {
                "code": "schema_missing",
                "claim_id": "review-staged:C1",
                "field_path": "canonical_claims[0].candidate_location",
                "correctable": True,
                "submission_attempt": 1,
            },
        )

    def test_conflicting_inventory_artifact_is_terminal(self) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        staged_synthesis.save_inventory_contribution(
            self.project,
            self.inventory_contribution("CH1"),
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        action_id, environment = self.open_inventory_tool_action(
            task,
            "artifact-conflict",
        )
        conflicting = self.inventory_contribution("CH1")
        conflicting["definitions"] = ["A conflicting definition."]
        with (
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.dict(os.environ, environment, clear=False),
        ):
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "already exists with different content",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": conflicting},
                    )
                )
            with self.assertRaisesRegex(
                mcp_server.ToolError,
                "only after a correctable",
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": conflicting},
                    )
                )
        result = json.loads(
            (
                self.book
                / "runs"
                / "synthesis_tool_budgets"
                / action_id
                / "submit-01.result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(result["outcome"], "tool_execution_rejected")
        self.assertIsNone(result["validation_diagnostic"])

    def test_corrected_inventory_write_reconciles_missing_provider_output(
        self,
    ) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        invalid = self.inventory_contribution("CH1")
        invalid["canonical_claims"][0]["candidate_excerpt"] = "absent"
        corrected = self.inventory_contribution("CH1")
        target = (
            staged_synthesis.staging_directory(
                self.project,
                self.book_id,
            )
            / "inventory_units"
            / "CH1.json"
        )

        def provider_call(
            *_args: object,
            environment_overrides: dict[str, str],
            **_kwargs: object,
        ) -> tuple[list[dict], str]:
            with patch.dict(
                os.environ,
                environment_overrides,
                clear=False,
            ):
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_READ_TOOL,
                        {
                            "book_id": self.book_id,
                            "unit_id": "CH1",
                            "input_hash": task["input_hash"],
                        },
                    )
                )
                with self.assertRaises(mcp_server.ToolError):
                    asyncio.run(
                        mcp_server.mcp.call_tool(
                            synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                            {"proposal": invalid},
                        )
                    )
                asyncio.run(
                    mcp_server.mcp.call_tool(
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                        {"proposal": corrected},
                    )
                )
            raise RuntimeError("provider output was unavailable")

        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.project / "data" / "books",
            ),
            patch.object(mcp_server, "PROJECT_PATH", self.project),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=provider_call,
            ),
        ):
            result = backend_selector.run_staged_provider_action(
                "codex",
                "/bin/codex",
                self.book_id,
                "inventory-CH1",
                target,
                "bounded inventory prompt",
                "book_synthesis",
                maximum_stage_calls=2,
                capability_preflight={
                    "input_hash": task["input_hash"],
                    "capability_sha256": "a" * 64,
                },
            )

        self.assertTrue(target.is_file())
        self.assertTrue(result["reconciled_after_provider_error"])
        ledger = [
            json.loads(line)
            for line in (
                self.book / "runs" / "synthesis.controller.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [event["event"] for event in ledger][-1],
            "contract_completed",
        )
        self.assertNotIn(
            "contract_failed",
            [event["event"] for event in ledger],
        )

    def test_inventory_failures_have_typed_diagnostics(self) -> None:
        cases = {
            "Inventory contribution input hash is stale.": (
                "noncorrectable_input_hash_mismatch"
            ),
            "Inventory claim conflicts with its authoritative text.": (
                "noncorrectable_authority_conflict"
            ),
            "Canonical inventory contributions must match supplied slots.": (
                "correctable_reference_error"
            ),
            "Proposal has a validation error in a missing classification.": (
                "correctable_schema_error"
            ),
        }
        for error, expected in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    backend_selector.classify_staged_provider_failure(
                        "",
                        None,
                        {
                            "tool_calls": [],
                            "tool_errors": [error],
                        },
                        synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                    ),
                    expected,
                )

    def test_cycle006_sc3_normalization_strengthening_is_rejected(self) -> None:
        fixture_path = (
            Path(__file__).parent
            / "fixtures"
            / "cycle006_sc3_authority_conflict.json"
        )
        claim = json.loads(fixture_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "conflicts with its authoritative text",
        ):
            book_synthesis.validate_inventory_claim_authority(
                claim,
                {},
                required=True,
            )

    def test_blocking_s1_routes_only_affected_unit_and_s2_is_terminal(self) -> None:
        self.initialize_foundation()
        self.save_editions()
        result = self.save_audits(blocking_chapter="CH2")
        self.assertEqual(result["status"], "correction_required")
        self.assertEqual(
            staged_synthesis.required_edition_units(
                self.project,
                self.book_id,
                2,
            ),
            ["CH2"],
        )
        correction = staged_synthesis.load_correction(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(correction)
        self.assertEqual(correction["schema_version"], "2.0")
        self.assertEqual(
            {
                target["unit_id"]
                for target in correction["claim_targets"]
            },
            {"CH2"},
        )
        self.assertTrue(
            all(
                target["required_exact_excerpt"] is True
                for target in correction["claim_targets"]
            )
        )
        task = staged_synthesis.edition_task(
            self.project,
            self.book_id,
            2,
            "CH2",
        )
        self.assertEqual(
            task["correction_sha256"],
            correction["correction_sha256"],
        )
        invalid_mappings = [
            {
                "claim_id": claim["claim_id"],
                "edition_excerpt": (
                    "# Second"
                    if claim["claim_id"]
                    == correction["claim_targets"][0]["claim_id"]
                    else claim["candidate_excerpt"]
                ),
                "edition_location": "CH2 paragraph 1",
            }
            for claim in task["assigned_claims"]
        ]
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "did not restore the exact authoritative claim",
        ):
            staged_synthesis.save_edition_unit(
                self.project,
                {
                    "book_id": self.book_id,
                    "attempt": 2,
                    "unit_id": "CH2",
                    "context_sha256": task["context_sha256"],
                    "inventory_sha256": task["inventory_sha256"],
                    "plan_sha256": task["plan_sha256"],
                    "packet_sha256": task["packet_sha256"],
                    "claim_mappings": invalid_mappings,
                    "change_summary": ["Attempted an invalid correction."],
                },
                self.chapters["CH2"],
            )
        self.save_editions(attempt=2)
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "audit_units_S2",
        )
        receipt = self.save_audits(attempt=2)
        self.assertEqual(receipt["selected_attempt"], 2)
        self.assertEqual(receipt["lifecycle_status"], "ready_for_editorial")

    def test_unresolved_rename_and_false_authority_are_rejected(self) -> None:
        prior_receipt_sha = self.create_terminal_hold()
        _, protected = self.authorize_and_prepare_audited_cycle()
        plan = self.audited_plan(
            protected,
            effect_type="rename",
            resolution_effect="resolved",
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_plan_draft(self.project, plan)
        self.assertFalse(
            staged_synthesis.plan_draft_path(
                staged_synthesis.staging_directory(
                    self.project,
                    self.book_id,
                ),
                1,
            ).exists()
        )
        self.assertEqual(
            self.sha(self.book / "synthesis" / "receipt.json"),
            prior_receipt_sha,
        )
        plan = self.audited_plan(
            protected,
            authority_refs=["SC999"],
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_plan_draft(self.project, plan)

    def test_human_decision_can_authorize_unresolved_public_name(self) -> None:
        self.create_terminal_hold()
        decision_path = self.book / "profile-name-decision.json"
        self.write_json(
            decision_path,
            {
                "decision_id": "profile-public-name",
                "decision": "Use the approved public profile name.",
            },
        )
        _, protected = self.authorize_and_prepare_audited_cycle(
            [
                {
                    "decision_id": "profile-public-name",
                    "path": str(decision_path.relative_to(self.project)),
                    "sha256": self.sha(decision_path),
                }
            ]
        )
        plan = self.audited_plan(
            protected,
            authority_type="human_decision",
            authority_refs=["profile-public-name"],
            effect_type="rename",
            resolution_effect="resolved",
            terminology=[
                {
                    "operation_id": "OP1",
                    "term": "Approved public name",
                    "canonical_form": "Approved public name",
                    "rule": "Use only after the bound human decision.",
                    "applies_to": ["CH1", "CH2"],
                }
            ],
        )
        saved = staged_synthesis.save_plan_draft(self.project, plan)
        self.assertEqual(saved["plan_attempt"], 1)

    def test_evidence_change_cannot_hide_as_editorial_mechanics(self) -> None:
        self.create_terminal_hold()
        _, protected = self.authorize_and_prepare_audited_cycle()
        plan = self.audited_plan(
            protected,
            authority_type="editorial_mechanics",
            authority_refs=[],
            effect_type="change_evidence_relationship",
            resolution_effect="unchanged",
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_plan_draft(self.project, plan)

    def test_two_blocking_plan_audits_hold_without_edition(self) -> None:
        prior_receipt_sha = self.create_terminal_hold()
        _, protected = self.authorize_and_prepare_audited_cycle()
        plan_one = self.audited_plan(protected)
        staged_synthesis.save_plan_draft(self.project, plan_one)
        audit_one = self.audit_current_plan({"OP1": "unsupported"})
        plan_two = self.audited_plan(
            protected,
            attempt=2,
            correction_audit_sha256=audit_one["plan_audit_sha256"],
        )
        staged_synthesis.save_plan_draft(self.project, plan_two)
        self.audit_current_plan({"OP1": "unsupported"})
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "plan_held",
        )
        self.assertFalse(
            book_synthesis.synthesis_directory(
                self.project,
                self.book_id,
            ).exists()
        )
        self.assertEqual(
            self.sha(self.book / "synthesis" / "receipt.json"),
            prior_receipt_sha,
        )

    def test_fresh_cycle_receipt_binds_plan_audit(self) -> None:
        prior_receipt_sha = self.create_terminal_hold()
        authorization, protected = self.authorize_and_prepare_audited_cycle()
        plan = self.audited_plan(protected)
        staged_synthesis.save_plan_draft(self.project, plan)
        plan_audit = self.audit_current_plan()
        staged_synthesis.promote_approved_plan(
            self.project,
            self.book_id,
        )
        self.save_editions()
        receipt = self.save_audits()
        verified = book_synthesis.verify_receipt(
            self.project,
            self.book_id,
            self.candidate["candidate_sha256"],
        )
        self.assertEqual(verified["cycle_id"], "cycle-002")
        self.assertEqual(
            verified["cycle_authorization_sha256"],
            authorization["authorization_sha256"],
        )
        self.assertEqual(
            verified["plan_audit_sha256"],
            plan_audit["plan_audit_sha256"],
        )
        self.assertEqual(receipt["selected_attempt"], 1)
        self.assertEqual(
            book_synthesis.synthesis_receipt_path(
                self.project,
                self.book_id,
            ),
            (
                self.book
                / "synthesis_cycles"
                / "cycle-002"
                / "receipt.json"
            ),
        )
        self.assertEqual(
            self.sha(self.book / "synthesis" / "receipt.json"),
            prior_receipt_sha,
        )
        plan_audit_path = self.project / verified["plan_audit_path"]
        tampered = json.loads(plan_audit_path.read_text(encoding="utf-8"))
        tampered["blocking_reasons"] = ["tampered"]
        self.write_json(plan_audit_path.with_suffix(".replacement"), tampered)
        plan_audit_path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            book_synthesis.verify_receipt(
                self.project,
                self.book_id,
                self.candidate["candidate_sha256"],
            )

    def test_cycle_three_preserves_prior_cycles_and_scopes_inputs(self) -> None:
        preserved = self.authorize_and_prepare_sharded_cycle()
        self.assertEqual(
            self.sha(self.book / "synthesis" / "receipt.json"),
            preserved["receipt"],
        )
        self.assertEqual(
            self.sha(self.book / "synthesis_cycles" / "index.json"),
            preserved["index"],
        )
        self.assertEqual(
            self.sha(
                self.book
                / "synthesis_cycles"
                / "cycle-002_staging"
                / "inventory.json"
            ),
            preserved["inventory"],
        )
        successor = book_synthesis.load_synthesis_cycle_index(
            self.project,
            self.book_id,
        )
        self.assertEqual(successor["active_cycle_id"], "cycle-003")
        self.assertEqual(
            successor["cycles"][1]["terminal_plan_status"],
            "absent",
        )
        ch1 = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            "CH1",
        )
        ch2 = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            "CH2",
        )
        self.assertTrue(
            set(ch1["chapter_claim_ids"]).isdisjoint(
                ch2["chapter_claim_ids"]
            )
        )

    def test_plan_shard_read_contract_is_hash_bound_and_chapter_scoped(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        plan_input = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            "CH1",
        )

        result = staged_synthesis.read_plan_shard_input(
            self.project,
            self.book_id,
            "CH1",
            plan_input["input_hash"],
        )
        task = staged_synthesis.plan_shard_task(
            self.project,
            self.book_id,
            "CH1",
        )

        self.assertEqual(result["chapter_id"], "CH1")
        self.assertEqual(result["input_hash"], plan_input["input_hash"])
        self.assertEqual(result["plan_input"], plan_input)
        self.assertEqual(result["chapter_markdown"], self.chapters["CH1"])
        self.assertEqual(task["maximum_provider_calls"], 2)
        self.assertIn("same provider is allowed", task["retry_rule"])
        self.assertNotIn(self.chapters["CH2"], result["chapter_markdown"])
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "input hash is stale",
        ):
            staged_synthesis.read_plan_shard_input(
                self.project,
                self.book_id,
                "CH1",
                "0" * 64,
            )

    def test_plan_shard_authority_rejects_provenance_identifiers(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        for foreign_ref in (
            "review-staged:C1",
            "SRC1",
            "drafts/CH1/D1.md:1",
        ):
            shard = self.plan_shard("CH1")
            shard["operations"][0]["authority_refs"] = [foreign_ref]
            with self.subTest(foreign_ref=foreign_ref):
                with self.assertRaisesRegex(
                    book_synthesis.BookSynthesisError,
                    "foreign candidate authority",
                ):
                    staged_synthesis.save_plan_shard(
                        self.project,
                        shard,
                    )

    def test_successor_cycle_chain_supports_cycle_four_and_five(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.create_plan_shard_hold(legacy_cycle_three=True)
        cycle_root = self.book / "synthesis_cycles"
        preserved = {
            path.name: self.sha(path)
            for path in (
                cycle_root / "index.json",
                cycle_root / "index-v2.json",
                cycle_root / "cycle-003_staging" / "context.json",
                cycle_root / "cycle-003_staging" / "inventory.json",
            )
        }

        cycle_four = book_synthesis.authorize_sharded_recovery_cycle(
            self.project,
            self.book_id,
            "Retry the held shard through the repaired provider contract.",
        )

        self.assertEqual(cycle_four["cycle_id"], "cycle-004")
        self.assertEqual(
            cycle_four["transition_kind"],
            "retry_after_plan_shard_hold",
        )
        index_four = book_synthesis.load_synthesis_cycle_index(
            self.project,
            self.book_id,
        )
        self.assertEqual(index_four["active_cycle_id"], "cycle-004")
        self.assertEqual(len(index_four["cycles"]), 4)
        self.assertTrue((cycle_root / "index-v3.json").is_file())
        self.assertEqual(
            book_synthesis.verify_active_cycle_authorization(
                self.project,
                self.book_id,
            )["authorization_sha256"],
            cycle_four["authorization_sha256"],
        )
        for path in (
            cycle_root / "index.json",
            cycle_root / "index-v2.json",
            cycle_root / "cycle-003_staging" / "context.json",
            cycle_root / "cycle-003_staging" / "inventory.json",
        ):
            self.assertEqual(self.sha(path), preserved[path.name])

        self.prepare_active_sharded_cycle()
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "plan-shard-CH1",
                "cycle-004",
            ),
            [],
        )
        self.create_plan_shard_hold()
        cycle_five = book_synthesis.authorize_sharded_recovery_cycle(
            self.project,
            self.book_id,
            "Prove that successor authorization is not capped at Cycle 004.",
        )
        index_five = book_synthesis.load_synthesis_cycle_index(
            self.project,
            self.book_id,
        )
        self.assertEqual(cycle_five["cycle_id"], "cycle-005")
        self.assertEqual(index_five["active_cycle_id"], "cycle-005")
        self.assertEqual(len(index_five["cycles"]), 5)
        self.assertTrue((cycle_root / "index-v4.json").is_file())

    def test_successor_index_tampering_breaks_the_chain(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.create_plan_shard_hold(legacy_cycle_three=True)
        book_synthesis.authorize_sharded_recovery_cycle(
            self.project,
            self.book_id,
            "Create a successor whose predecessor hash is testable.",
        )
        path = self.book / "synthesis_cycles" / "index-v2.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["active_cycle_id"] = "cycle-999"
        path.write_text(json.dumps(document), encoding="utf-8")

        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "index is invalid",
        ):
            book_synthesis.load_synthesis_cycle_index(
                self.project,
                self.book_id,
            )

    def test_terminal_hold_successor_regenerates_inventory(self) -> None:
        prior_receipt = self.create_sharded_terminal_synthesis_hold()
        cycle_root = self.book / "synthesis_cycles"
        prior_index = cycle_root / "index-v2.json"
        prior_cycle_root = cycle_root / "cycle-003"
        preserved = {
            "index": self.sha(prior_index),
            "receipt": self.sha(prior_cycle_root / "receipt.json"),
            "candidate": self.sha(self.candidate_path),
        }

        authorization = (
            book_synthesis.authorize_terminal_synthesis_successor_cycle(
                self.project,
                self.book_id,
                "Regenerate the inventory after the terminal claim hold.",
            )
        )

        self.assertEqual(authorization["cycle_id"], "cycle-004")
        self.assertEqual(
            authorization["transition_kind"],
            "fresh_after_terminal_hold",
        )
        self.assertEqual(
            authorization["inventory_policy"],
            "regenerate_required",
        )
        self.assertEqual(
            authorization["candidate_sha256"],
            prior_receipt["candidate_sha256"],
        )
        self.assertEqual(
            book_synthesis.verify_active_cycle_authorization(
                self.project,
                self.book_id,
            )["authorization_sha256"],
            authorization["authorization_sha256"],
        )
        self.assertEqual(self.sha(prior_index), preserved["index"])
        self.assertEqual(
            self.sha(prior_cycle_root / "receipt.json"),
            preserved["receipt"],
        )
        self.assertEqual(self.sha(self.candidate_path), preserved["candidate"])

        self.prepare()
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "inventory_required",
        )
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "newly generated inventory",
        ):
            staged_synthesis.reuse_prior_verified_inventory(
                self.project,
                self.book_id,
            )
        for chapter_id in ("CH1", "CH2"):
            staged_synthesis.save_inventory_unit(
                self.project,
                self.inventory_proposal(
                    chapter_id,
                    candidate_only=chapter_id == "CH1",
                    audited=True,
                ),
            )
        inventory = staged_synthesis.finalize_inventory(
            self.project,
            self.book_id,
        )
        self.assertEqual(inventory["schema_version"], "4.0")
        self.assertTrue(
            all(
                claim["statement"] == claim["authoritative_text"]
                for claim in inventory["claims"]
            )
        )
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "requires a synthesis receipt",
        ):
            book_synthesis.authorize_terminal_synthesis_successor_cycle(
                self.project,
                self.book_id,
                "Do not create a duplicate successor.",
            )

    def test_plan_shard_capability_preflight_is_static_and_hash_bound(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        plan_input = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            "CH1",
        )
        config = self.project / ".codex" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "[mcp_servers.alexandrian_library]\n"
            f'args = ["{self.project / "mcp_server.py"}"]\n',
            encoding="utf-8",
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        prior_ledger = ledger.read_bytes()

        with patch.object(
            backend_selector,
            "PROJECT_PATH",
            self.project,
        ), patch.object(
            backend_selector.environment_check,
            "codex_project_readiness",
            return_value=backend_selector.environment_check.CheckResult(
                "Codex project readiness",
                "available",
                "codex_ready: Effective project MCP verified.",
                required=False,
            ),
        ):
            codex = backend_selector.preflight_staged_capabilities(
                "codex",
                "plan_shard",
                self.book_id,
                "CH1",
                plan_input["input_hash"],
            )
            claude = backend_selector.preflight_staged_capabilities(
                "claude",
                "plan_shard",
                self.book_id,
                "CH1",
                plan_input["input_hash"],
            )

        self.assertTrue(codex["bounded_input_verified"])
        self.assertTrue(claude["provider_permissions_verified"])
        self.assertIn(
            "read_staged_synthesis_plan_shard_input",
            codex["required_tools"],
        )
        self.assertEqual(ledger.read_bytes(), prior_ledger)

    def test_global_plan_audit_preflight_verifies_exact_read_tool(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.save_all_plan_shards()
        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        prior_ledger = ledger.read_bytes()

        with patch.object(
            backend_selector,
            "PROJECT_PATH",
            self.project,
        ), patch.object(
            backend_selector.environment_check,
            "codex_project_readiness",
            return_value=backend_selector.environment_check.CheckResult(
                "Codex project readiness",
                "available",
                "codex_ready: Effective project MCP verified.",
                required=False,
            ),
        ):
            result = backend_selector.preflight_staged_capabilities(
                "codex",
                "global_plan_audit",
                self.book_id,
                input_hash=merged["plan_draft_sha256"],
            )

        self.assertTrue(result["bounded_input_verified"])
        self.assertEqual(
            result["input_hash"],
            merged["plan_draft_sha256"],
        )
        self.assertIn(
            "read_staged_synthesis_global_plan_audit_input",
            result["required_tools"],
        )
        self.assertEqual(ledger.read_bytes(), prior_ledger)

    def test_codex_preflight_rejects_ineffective_project_mcp(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        plan_input = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            "CH1",
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        prior_ledger = ledger.read_bytes()

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project,
            ),
            patch.object(
                backend_selector.environment_check,
                "codex_project_readiness",
                return_value=(
                    backend_selector.environment_check.CheckResult(
                        "Codex project readiness",
                        "missing",
                        (
                            "codex_mcp_not_effective: Codex did not load "
                            "the project server."
                        ),
                        required=False,
                    )
                ),
            ),
            self.assertRaisesRegex(
                backend_selector.CapabilityPreflightError,
                "codex_mcp_not_effective",
            ),
        ):
            backend_selector.preflight_staged_capabilities(
                "codex",
                "plan_shard",
                self.book_id,
                "CH1",
                plan_input["input_hash"],
            )

        self.assertEqual(ledger.read_bytes(), prior_ledger)

    def test_failed_preflight_does_not_open_a_controller_contract(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        plan_input = staged_synthesis.load_plan_shard_input(
            self.project,
            self.book_id,
            "CH1",
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        prior_ledger = ledger.read_bytes()

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project,
            ),
            patch(
                "mcp_server.registered_mcp_tool_names",
                return_value=set(),
            ),
            self.assertRaisesRegex(
                backend_selector.CapabilityPreflightError,
                "missing required tools",
            ),
        ):
            backend_selector.preflight_staged_capabilities(
                "claude",
                "plan_shard",
                self.book_id,
                "CH1",
                plan_input["input_hash"],
            )

        self.assertEqual(ledger.read_bytes(), prior_ledger)

    def test_plan_shard_prompt_defines_exact_operation_target(self) -> None:
        prompt = backend_selector.build_staged_plan_shard_prompt(
            "# Profile",
            {
                "book_id": self.book_id,
                "chapter_id": "CH2",
                "input_hash": "a" * 64,
            },
        )

        self.assertIn(
            "target field must equal the supplied bare chapter_id exactly",
            prompt,
        )
        self.assertIn(
            'for example "CH2"; it is not a section name or prose locator',
            prompt,
        )
        self.assertIn(
            "must be an exact substring of candidate_excerpt",
            prompt,
        )
        self.assertIn(
            "authoritative_text does not satisfy this check",
            prompt,
        )

    def test_shard_rejects_foreign_missing_and_unsupported_content(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        shard = self.plan_shard("CH1")
        shard["operations"][0]["protected_claim_ids"].append("CO999")
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_plan_shard(self.project, shard)

        shard = self.plan_shard("CH1")
        shard["claim_dispositions"] = shard["claim_dispositions"][:-1]
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_plan_shard(self.project, shard)

        shard = self.plan_shard("CH1")
        shard["operations"][0]["kind"] = "terminology"
        shard["terminology"] = [
            {
                "operation_id": "OP1",
                "term": "Invented global label",
                "canonical_form": "Invented global label",
                "rule": "Use an unsupported label.",
                "applies_to": ["CH1"],
                "authority_type": "editorial_mechanics",
                "authority_refs": [],
            }
        ]
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_plan_shard(self.project, shard)

    def test_plan_shard_operation_errors_identify_the_exact_invalid_field(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()

        shard = self.plan_shard("CH1")
        shard["operations"][0]["target"] = "CH2"
        with self.assertRaisesRegex(
            staged_synthesis.PlanShardCorrectionError,
            "operation 1 must target only CH1",
        ):
            staged_synthesis.save_plan_shard(self.project, shard)

        shard = self.plan_shard("CH1")
        shard["operations"][0]["effect_type"] = "rename"
        with self.assertRaisesRegex(
            staged_synthesis.PlanShardCorrectionError,
            "operation 1 uses forbidden effect_type rename",
        ):
            staged_synthesis.save_plan_shard(self.project, shard)

        shard = self.plan_shard("CH1")
        shard["operations"][0]["protected_claim_ids"].append("CO999")
        with self.assertRaisesRegex(
            staged_synthesis.PlanShardCorrectionError,
            "operation 1 cites claims outside CH1: CO999",
        ):
            staged_synthesis.save_plan_shard(self.project, shard)

    def test_shards_resume_and_merge_deterministically(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        first = staged_synthesis.save_plan_shard(
            self.project,
            self.plan_shard("CH1"),
        )
        repeated = staged_synthesis.save_plan_shard(
            self.project,
            self.plan_shard("CH1"),
        )
        self.assertEqual(
            first["plan_shard_sha256"],
            repeated["plan_shard_sha256"],
        )
        self.assertEqual(
            staged_synthesis.next_plan_shard(
                self.project,
                self.book_id,
            ),
            "CH2",
        )
        staged_synthesis.save_plan_shard(
            self.project,
            self.plan_shard("CH2"),
        )
        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        repeated_merge = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        self.assertEqual(
            merged["plan_draft_sha256"],
            repeated_merge["plan_draft_sha256"],
        )
        self.assertEqual(
            [operation["operation_id"] for operation in merged["operations"]],
            ["OP1", "OP2"],
        )
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "plan_global_audit_required",
        )
        self.assertFalse(
            book_synthesis.synthesis_directory(
                self.project,
                self.book_id,
            ).exists()
        )

    def test_conflicting_terminology_blocks_merge(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        for chapter_id, canonical in (
            ("CH1", "first bounded term"),
            ("CH2", "second bounded term"),
        ):
            shard = self.plan_shard(chapter_id)
            shard["operations"][0]["kind"] = "terminology"
            claim_id = shard["claim_dispositions"][0]["claim_id"]
            shard["terminology"] = [
                {
                    "operation_id": "OP1",
                    "term": "claim",
                    "canonical_form": canonical,
                    "rule": "Keep this local expression stable.",
                    "applies_to": [chapter_id],
                    "authority_type": "candidate",
                    "authority_refs": [claim_id],
                }
            ]
            staged_synthesis.save_plan_shard(self.project, shard)
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.merge_plan_shards(
                self.project,
                self.book_id,
            )

    def test_same_terminology_is_preserved_for_each_chapter_scope(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        for chapter_id in ("CH1", "CH2"):
            shard = self.plan_shard(chapter_id)
            shard["operations"][0]["kind"] = "terminology"
            claim_id = shard["claim_dispositions"][0]["claim_id"]
            shard["terminology"] = [
                {
                    "operation_id": "OP1",
                    "term": "claim",
                    "canonical_form": "claim",
                    "rule": "Keep this chapter-scoped term stable.",
                    "applies_to": [chapter_id],
                    "authority_type": "candidate",
                    "authority_refs": [claim_id],
                }
            ]
            staged_synthesis.save_plan_shard(self.project, shard)

        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        terms = [
            entry
            for entry in merged["terminology"]
            if entry["term"] == "claim"
        ]
        self.assertEqual(len(terms), 2)
        self.assertEqual(
            [entry["applies_to"] for entry in terms],
            [["CH1"], ["CH2"]],
        )
        self.assertEqual(
            {entry["source_shard_id"] for entry in terms},
            {"CH1", "CH2"},
        )

    def test_global_audit_is_terminal_and_exact_hash_controls_promotion(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.save_all_plan_shards()
        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.save_sharded_plan_audit(
                self.project,
                {
                    **{
                        "book_id": self.book_id,
                        "context_sha256": merged["context_sha256"],
                        "inventory_sha256": merged[
                            "inventory_staging_sha256"
                        ],
                        "merged_plan_sha256": merged["plan_draft_sha256"],
                        "findings": [],
                        "verdict": "approve",
                    },
                    "replacement_plan": {},
                },
            )
        self.sharded_audit("terminology_consistent")
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "plan_held",
        )
        with self.assertRaises(book_synthesis.BookSynthesisError):
            staged_synthesis.promote_approved_plan(
                self.project,
                self.book_id,
            )
        self.assertFalse(
            book_synthesis.synthesis_directory(
                self.project,
                self.book_id,
            ).exists()
        )

    def test_rejected_plan_audit_authorizes_one_fresh_bound_cycle(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.save_all_plan_shards()
        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        audit = self.sharded_audit("terminology_consistent")
        prior_cycle = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(prior_cycle)
        prior_staging = (
            self.project / prior_cycle["root_path"]
        ).with_name("cycle-003_staging")
        preserved = {
            "plan": self.sha(prior_staging / "plans" / "P1.json"),
            "audit": self.sha(prior_staging / "plan_audits" / "A1.json"),
        }

        authorization = (
            book_synthesis.authorize_plan_audit_recovery_cycle(
                self.project,
                self.book_id,
                "Regenerate after a deterministic merge defect.",
            )
        )

        active = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(active)
        self.assertEqual(active["cycle_id"], "cycle-004")
        self.assertEqual(
            authorization["transition_kind"],
            "fresh_after_plan_audit_hold",
        )
        self.assertEqual(
            authorization["prior_plan_audit_sha256"],
            preserved["audit"],
        )
        self.assertEqual(
            book_synthesis.verify_active_cycle_authorization(
                self.project,
                self.book_id,
            )["authorization_sha256"],
            authorization["authorization_sha256"],
        )
        self.assertEqual(
            self.sha(prior_staging / "plans" / "P1.json"),
            preserved["plan"],
        )
        self.assertEqual(
            self.sha(prior_staging / "plan_audits" / "A1.json"),
            preserved["audit"],
        )
        self.prepare()
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "inventory_required",
        )
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "single plan-audit recovery cycle",
        ):
            book_synthesis.authorize_plan_audit_recovery_cycle(
                self.project,
                self.book_id,
                "A second plan-audit recovery must not be available.",
            )

    def test_global_plan_auditor_reads_exact_hash_bound_inputs(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.save_all_plan_shards()
        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )

        audit_input = staged_synthesis.read_sharded_plan_audit_input(
            self.project,
            self.book_id,
            merged["plan_draft_sha256"],
        )

        self.assertEqual(
            audit_input["merged_plan"],
            merged,
        )
        self.assertEqual(
            audit_input["candidate_markdown"],
            self.candidate_path.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            [item["chapter_id"] for item in audit_input["plan_shard_inputs"]],
            ["CH1", "CH2"],
        )
        self.assertEqual(
            {
                item["shard_id"]: item["input_hash"]
                for item in audit_input["plan_shard_inputs"]
            },
            {
                item["shard_id"]: item["input_hash"]
                for item in merged["source_shards"]
            },
        )
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "stale",
        ):
            staged_synthesis.read_sharded_plan_audit_input(
                self.project,
                self.book_id,
                "0" * 64,
            )

    def test_approved_global_audit_promotes_exact_merged_plan(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.save_all_plan_shards()
        merged = staged_synthesis.merge_plan_shards(
            self.project,
            self.book_id,
        )
        audit = self.sharded_audit()
        result = staged_synthesis.promote_approved_plan(
            self.project,
            self.book_id,
        )
        _, _, plan = book_synthesis.load_initialized_synthesis(
            self.project,
            self.book_id,
        )
        self.assertEqual(
            plan["approved_plan_draft_sha256"],
            merged["plan_draft_sha256"],
        )
        self.assertEqual(
            plan["plan_audit_sha256"],
            audit["plan_audit_sha256"],
        )
        self.assertEqual(result["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "edition_units_S1",
        )

    def test_host_preflight_failure_does_not_consume_provider_budget(
        self,
    ) -> None:
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        records = [
            {
                "event": "contract_opened",
                "stage": "plan-shard-CH1",
                "backend": "codex",
            },
            {
                "event": "contract_failed",
                "stage": "plan-shard-CH1",
                "backend": "codex",
                "reason": "failed to open readonly database",
            },
            {
                "event": "contract_opened",
                "stage": "plan-shard-CH1",
                "backend": "claude",
            },
            {
                "event": "contract_failed",
                "stage": "plan-shard-CH1",
                "backend": "claude",
                "reason": "provider timed out",
            },
        ]
        ledger.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "plan-shard-CH1",
            ),
            ["claude"],
        )

    def test_provider_budgets_are_scoped_to_the_active_cycle(self) -> None:
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for cycle_id, backend in (
            ("cycle-003", "codex"),
            ("cycle-004", "claude"),
        ):
            action_id = f"{cycle_id}-{backend}"
            records.extend(
                [
                    {
                        "event": "contract_opened",
                        "action_id": action_id,
                        "cycle_id": cycle_id,
                        "stage": "plan-shard-CH1",
                        "backend": backend,
                    },
                    {
                        "event": "contract_failed",
                        "action_id": action_id,
                        "cycle_id": cycle_id,
                        "stage": "plan-shard-CH1",
                        "backend": backend,
                        "reason": "provider timed out",
                    },
                ]
            )
        ledger.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "plan-shard-CH1",
                "cycle-003",
            ),
            ["codex"],
        )
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "plan-shard-CH1",
                "cycle-004",
            ),
            ["claude"],
        )

    def test_same_provider_retry_is_counted_and_receives_safe_context(
        self,
    ) -> None:
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        action_id = "audit-S1-CH1-first"
        records = [
            {
                "event": "contract_opened",
                "action_id": action_id,
                "cycle_id": "cycle-001",
                "stage": "audit-S1-CH1",
                "backend": "claude",
            },
            {
                "event": "contract_failed",
                "action_id": action_id,
                "cycle_id": "cycle-001",
                "stage": "audit-S1-CH1",
                "backend": "claude",
                "reason": "provider timed out",
                "failure_classification": "provider_timeout",
                "diagnostic_sha256": "a" * 64,
            },
        ]
        ledger.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "audit-S1-CH1",
                "cycle-001",
            ),
            ["claude"],
        )
        prompt = backend_selector.append_safe_retry_context(
            "bounded audit",
            ledger,
            "audit-S1-CH1",
            "cycle-001",
            2,
        )
        self.assertIn('"same_provider_retry_allowed": true', prompt)
        self.assertIn('"previous_failure_classification": "provider_timeout"', prompt)
        self.assertNotIn("provider timed out", prompt)

    def test_interrupted_started_action_is_consumed_and_reconciled(
        self,
    ) -> None:
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        target = (
            self.book
            / "synthesis_cycles"
            / "cycle-006"
            / "staged"
            / "attempts"
            / "S1"
            / "audits"
            / "CH2.json"
        )
        action_id = "audit-S1-CH2-interrupted"
        records = [
            {
                "event": "contract_opened",
                "action_id": action_id,
                "book_id": self.book_id,
                "cycle_id": "cycle-006",
                "stage": "audit-S1-CH2",
                "backend": "claude",
                "provider_call_number": 1,
            },
            {
                "event": "contract_started",
                "action_id": action_id,
                "book_id": self.book_id,
                "cycle_id": "cycle-006",
                "stage": "audit-S1-CH2",
                "backend": "claude",
            },
        ]
        ledger.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "audit-S1-CH2",
                "cycle-006",
            ),
            ["claude"],
        )

        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.project / "data" / "books",
            ),
        ):
            diagnostic = (
                backend_selector.reconcile_interrupted_staged_action(
                    self.book_id,
                    "cycle-006",
                    "audit-S1-CH2",
                    target,
                )
            )

        self.assertIsNotNone(diagnostic)
        self.assertEqual(
            diagnostic["classification"],
            "provider_process_interrupted",
        )
        updated = [
            json.loads(line)
            for line in ledger.read_text(encoding="utf-8").splitlines()
        ]
        terminal = updated[-1]
        self.assertEqual(terminal["event"], "contract_failed")
        self.assertEqual(
            terminal["failure_classification"],
            "provider_process_interrupted",
        )
        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.project / "data" / "books",
            ),
        ):
            self.assertIsNone(
                backend_selector.reconcile_interrupted_staged_action(
                    self.book_id,
                    "cycle-006",
                    "audit-S1-CH2",
                    target,
                )
            )

    def test_two_same_provider_failures_create_terminal_audit_hold(
        self,
    ) -> None:
        self.initialize_foundation()
        self.save_editions()
        stage = "audit-S1-CH1"
        target = (
            staged_synthesis.staged_directory(
                self.project,
                self.book_id,
            )
            / "attempts"
            / "S1"
            / "audits"
            / "CH1.json"
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        records = []
        for number in (1, 2):
            action_id = f"audit-S1-CH1-{number}"
            records.extend(
                [
                    {
                        "event": "contract_opened",
                        "action_id": action_id,
                        "cycle_id": "cycle-001",
                        "stage": stage,
                        "backend": "claude",
                    },
                    {
                        "event": "contract_failed",
                        "action_id": action_id,
                        "cycle_id": "cycle-001",
                        "stage": stage,
                        "backend": "claude",
                        "reason": "provider timed out",
                        "failure_classification": "provider_timeout",
                        "diagnostic_sha256": f"{number}" * 64,
                    },
                ]
            )
        ledger.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        hold = staged_synthesis.save_provider_stage_hold(
            self.project,
            self.book_id,
            "cycle-001",
            stage,
            target,
            "b" * 64,
            2,
            ["claude", "claude"],
        )
        self.assertEqual(hold["status"], "audit_provider_held")
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "audit_provider_held",
        )

    def test_inventory_provider_budget_exhaustion_is_terminal(self) -> None:
        staged_synthesis.prepare(
            self.project,
            self.book_id,
            self.candidate,
            self.profile,
        )
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        preflight = {
            "schema_version": "1.0",
            "backend": "codex",
            "stage_contract": "inventory",
            "book_id": self.book_id,
            "chapter_id": "CH1",
            "input_hash": task["input_hash"],
            "required_tools": [
                "read_staged_synthesis_inventory_input",
                "submit_staged_synthesis_inventory_unit",
            ],
            "registered_tools_verified": True,
            "provider_permissions_verified": True,
            "bounded_input_verified": True,
            "chapter_sha256": task["packet_sha256"],
        }
        preflight["capability_sha256"] = (
            book_synthesis.canonical_json_sha256(
                preflight,
                "capability_sha256",
            )
        )
        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.project / "data" / "books",
            ),
            patch.object(
                backend_selector,
                "preflight_staged_capabilities",
                return_value=preflight,
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=RuntimeError("provider timed out"),
            ) as provider,
        ):
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "provider timed out"):
                    backend_selector.run_next_staged_synthesis_action(
                        "codex",
                        "/bin/codex",
                        self.book_id,
                    )
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(
                book_synthesis.workflow_state(self.project, self.book_id),
                "inventory_provider_held",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "terminal: inventory_provider_held",
            ):
                backend_selector.run_next_staged_synthesis_action(
                    "codex",
                    "/bin/codex",
                    self.book_id,
                )
            self.assertEqual(provider.call_count, 2)

    def test_exhausted_inventory_stage_can_enter_one_fresh_recovery_cycle(
        self,
    ) -> None:
        self.create_sharded_terminal_synthesis_hold()
        book_synthesis.authorize_terminal_synthesis_successor_cycle(
            self.project,
            self.book_id,
            "Regenerate inventory after the terminal synthesis hold.",
        )
        self.prepare()
        active = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(active)
        prior_cycle_id = active["cycle_id"]
        self.assertEqual(prior_cycle_id, "cycle-004")
        stage = "inventory-CH1"
        task = staged_synthesis.inventory_task(
            self.project,
            self.book_id,
            "CH1",
        )
        target = (
            staged_synthesis.staging_directory(
                self.project,
                self.book_id,
            )
            / "inventory_units"
            / "CH1.json"
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        records = []
        for number, provider_name in enumerate(
            ("codex", "claude"),
            start=1,
        ):
            action_id = f"{stage}-{number}"
            records.extend(
                [
                    {
                        "event": "contract_opened",
                        "action_id": action_id,
                        "cycle_id": prior_cycle_id,
                        "stage": stage,
                        "backend": provider_name,
                    },
                    {
                        "event": "contract_failed",
                        "action_id": action_id,
                        "cycle_id": prior_cycle_id,
                        "stage": stage,
                        "backend": provider_name,
                        "reason": "provider timed out",
                        "failure_classification": "provider_timeout",
                        "diagnostic_sha256": f"{number}" * 64,
                    },
                ]
            )
        with ledger.open("a", encoding="utf-8") as stream:
            for record in records:
                stream.write(json.dumps(record) + "\n")

        with (
            patch.object(backend_selector, "PROJECT_PATH", self.project),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                self.project / "data" / "books",
            ),
        ):
            hold = backend_selector.reconcile_terminal_provider_stage_hold(
                self.book_id,
                stage,
                target,
                task["input_hash"],
                2,
            )
        self.assertIsNotNone(hold)
        self.assertEqual(hold["status"], "inventory_provider_held")
        prior_hold_sha = hold["hold_sha256"]

        authorization = book_synthesis.authorize_inventory_recovery_cycle(
            self.project,
            self.book_id,
            "Retry from fresh packets after diagnosing the inventory failure.",
        )
        self.assertEqual(
            authorization["transition_kind"],
            "retry_after_inventory_provider_hold",
        )
        self.assertEqual(authorization["held_stage"], stage)
        self.assertEqual(
            book_synthesis.verify_active_cycle_authorization(
                self.project,
                self.book_id,
            )["authorization_sha256"],
            authorization["authorization_sha256"],
        )
        successor = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(successor)
        self.assertEqual(successor["cycle_id"], "cycle-005")
        self.assertEqual(
            book_synthesis.read_json_object(
                self.project / authorization["prior_hold_path"],
                "prior hold",
            )["hold_sha256"],
            prior_hold_sha,
        )
        self.prepare()
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "inventory_required",
        )
        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "single inventory-recovery cycle",
        ):
            book_synthesis.authorize_inventory_recovery_cycle(
                self.project,
                self.book_id,
                "A second inventory recovery must not be available.",
            )

    def test_audit_recovery_cycle_preserves_s1_and_resumes_next_unit(
        self,
    ) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.promote_active_sharded_cycle()
        first_audit = self.save_one_passing_audit("CH1")
        hold = self.create_audit_provider_hold("CH2")
        prior_cycle = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(prior_cycle)
        prior_root = self.project / prior_cycle["root_path"]
        preserved = {
            "book": self.sha(prior_root / "attempts" / "S1" / "book.md"),
            "submission": self.sha(
                prior_root / "attempts" / "S1" / "submission.json"
            ),
            "ch1_audit": self.sha(
                prior_root
                / "staged"
                / "attempts"
                / "S1"
                / "audits"
                / "CH1.json"
            ),
            "hold": hold["hold_sha256"],
        }

        authorization = book_synthesis.authorize_audit_recovery_cycle(
            self.project,
            self.book_id,
            "Resume the held CH2 audit without regenerating S1.",
        )

        active = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(active)
        self.assertEqual(active["cycle_id"], "cycle-004")
        self.assertEqual(
            authorization["transition_kind"],
            "resume_after_audit_provider_hold",
        )
        self.assertEqual(authorization["held_unit_id"], "CH2")
        self.assertEqual(
            authorization["preserved_audit_unit_ids"],
            ["CH1"],
        )
        recovery_root = self.project / active["root_path"]
        self.assertEqual(
            self.sha(recovery_root / "attempts" / "S1" / "book.md"),
            preserved["book"],
        )
        self.assertEqual(
            self.sha(recovery_root / "attempts" / "S1" / "submission.json"),
            preserved["submission"],
        )
        self.assertEqual(
            self.sha(
                recovery_root
                / "staged"
                / "attempts"
                / "S1"
                / "audits"
                / "CH1.json"
            ),
            preserved["ch1_audit"],
        )
        self.assertEqual(
            book_synthesis.canonical_json_sha256(
                first_audit,
                "audit_unit_sha256",
            ),
            first_audit["audit_unit_sha256"],
        )
        self.assertFalse(
            (recovery_root / "staged" / "provider_holds").exists()
        )
        self.assertEqual(
            book_synthesis.workflow_state(self.project, self.book_id),
            "audit_units_S1",
        )
        self.assertEqual(
            staged_synthesis.next_audit_unit(
                self.project,
                self.book_id,
                1,
            ),
            "CH2",
        )
        ledger = self.book / "runs" / "synthesis.controller.jsonl"
        self.assertEqual(
            backend_selector.consumed_synthesis_stage_backends(
                ledger,
                "audit-S1-CH2",
                "cycle-004",
            ),
            [],
        )
        verified = book_synthesis.verify_active_cycle_authorization(
            self.project,
            self.book_id,
        )
        self.assertEqual(
            verified["authorization_sha256"],
            authorization["authorization_sha256"],
        )

    def test_audit_recovery_can_complete_a_passing_receipt(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.promote_active_sharded_cycle()
        self.save_one_passing_audit("CH1")
        self.create_audit_provider_hold("CH2")
        authorization = book_synthesis.authorize_audit_recovery_cycle(
            self.project,
            self.book_id,
            "Resume the held audit through one bounded successor.",
        )

        receipt = self.save_audits()
        verified = book_synthesis.verify_receipt(
            self.project,
            self.book_id,
            self.candidate["candidate_sha256"],
        )

        self.assertEqual(receipt["lifecycle_status"], "ready_for_editorial")
        self.assertEqual(receipt["cycle_id"], authorization["cycle_id"])
        self.assertEqual(receipt["foundation_cycle_id"], "cycle-003")
        self.assertEqual(
            receipt["cycle_authorization_sha256"],
            authorization["authorization_sha256"],
        )
        self.assertEqual(
            verified["receipt_sha256"],
            receipt["receipt_sha256"],
        )

    def test_audit_recovery_rejects_reused_artifact_tampering(self) -> None:
        self.authorize_and_prepare_sharded_cycle()
        self.promote_active_sharded_cycle()
        self.save_one_passing_audit("CH1")
        self.create_audit_provider_hold("CH2")
        book_synthesis.authorize_audit_recovery_cycle(
            self.project,
            self.book_id,
            "Bind the copied recovery foundation to its source hashes.",
        )
        active = book_synthesis.active_synthesis_cycle(
            self.project,
            self.book_id,
        )
        self.assertIsNotNone(active)
        recovery_book = (
            self.project / active["root_path"] / "attempts" / "S1" / "book.md"
        )
        recovery_book.write_text("tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(
            book_synthesis.BookSynthesisError,
            "Recovered artifact changed",
        ):
            book_synthesis.verify_active_cycle_authorization(
                self.project,
                self.book_id,
            )


if __name__ == "__main__":
    unittest.main()
