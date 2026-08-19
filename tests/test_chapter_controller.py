import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import backend_selector
import chapter_controller


class ChapterControllerDecisionTests(unittest.TestCase):
    def snapshot(self, **changes) -> chapter_controller.ChapterSnapshot:
        base = chapter_controller.ChapterSnapshot(
            book_id="book-test",
            chapter_id="CH1",
            legacy_status="active",
            developer_calls=1,
            evaluator_calls=1,
            researcher_calls=0,
            call_limit=12,
            workspace_exists=True,
            matching_assessment_exists=True,
            assessment_outcome="continue_development",
            human_input_exists=False,
            closeout_authorized=False,
            recovery_kind=None,
            final_closeout_pending=False,
            source_requests_pending=False,
        )
        return replace(base, **changes)

    def test_complete_is_terminal_and_non_executable(self) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(legacy_status="chapter_complete")
        )
        self.assertEqual(
            decision.state,
            chapter_controller.ChapterState.COMPLETE,
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.RETURN_COMPLETE,
        )
        self.assertFalse(decision.executable)

    def test_human_input_waits_until_a_decision_exists(self) -> None:
        waiting = chapter_controller.decide_next_action(
            self.snapshot(legacy_status="human_input_required")
        )
        applying = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="human_input_required",
                human_input_exists=True,
            )
        )
        self.assertEqual(
            waiting.action,
            chapter_controller.ActionKind.REQUEST_HUMAN_INPUT,
        )
        self.assertEqual(
            applying.action,
            chapter_controller.ActionKind.APPLY_HUMAN_INPUT,
        )
        self.assertEqual(applying.max_provider_calls, 1)

    def test_recovery_human_input_needs_separate_closeout_authority(
        self,
    ) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="human_input_required",
                human_input_exists=True,
                recovery_kind="research_access_retry",
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.REQUEST_CLOSEOUT_AUTHORIZATION,
        )
        self.assertFalse(decision.executable)

    def test_exhausted_recovery_with_recorded_input_routes_to_closeout(
        self,
    ) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="research_access_recovery_failed",
                assessment_outcome="ready_to_draft",
                human_input_exists=True,
                recovery_kind="research_access_retry",
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.REQUEST_CLOSEOUT_AUTHORIZATION,
        )

    def test_authorized_final_repair_overrides_failed_closeout_status(
        self,
    ) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="human_input_closeout_failed",
                developer_calls=10,
                evaluator_calls=8,
                call_limit=20,
                recovery_kind="final_pass_revision",
                final_closeout_pending=True,
                assessment_outcome="ready_for_final_pass",
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.RUN_FINAL_PASS_REPAIR,
        )
        self.assertEqual(decision.max_provider_calls, 1)

    def test_exhausted_performance_repair_waits_for_bounded_authority(
        self,
    ) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(legacy_status="performance_repair_required")
        )
        self.assertEqual(
            decision.state,
            chapter_controller.ChapterState.RECOVERY_REQUIRED,
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.REQUEST_RECOVERY_AUTHORIZATION,
        )
        self.assertFalse(decision.executable)

    def test_planned_revision_no_progress_remains_ledger_retryable(
        self,
    ) -> None:
        retryable = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="controller_no_progress",
                developer_calls=6,
                evaluator_calls=3,
                call_limit=12,
                recovery_kind="planned_draft_revision",
                assessment_outcome="revise_draft",
            )
        )
        exhausted = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="controller_no_progress",
                developer_calls=7,
                evaluator_calls=5,
                call_limit=12,
                recovery_kind="planned_draft_revision",
                assessment_outcome="revise_draft",
            )
        )

        self.assertEqual(
            retryable.action,
            chapter_controller.ActionKind.RUN_CHAPTER_PHASE,
        )
        self.assertTrue(retryable.executable)
        self.assertEqual(
            exhausted.action,
            chapter_controller.ActionKind.RETURN_FAILURE,
        )

    def test_authorized_closeout_keeps_authority_after_input_is_applied(
        self,
    ) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="active",
                closeout_authorized=True,
                recovery_kind="research_access_retry",
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.RUN_HUMAN_INPUT_CLOSEOUT,
        )
        self.assertEqual(decision.max_provider_calls, 1)

    def test_interrupted_contract_precedes_all_other_routing(self) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="active",
                interrupted_contract_id="CC-1-example",
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.RECONCILE_INTERRUPTED_ACTION,
        )
        self.assertFalse(decision.executable)

    def test_zero_remaining_calls_cannot_execute(self) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                developer_calls=6,
                evaluator_calls=6,
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.RETURN_FAILURE,
        )
        self.assertEqual(decision.max_provider_calls, 0)

    def test_final_pass_preparation_is_allowed_at_exact_call_limit(
        self,
    ) -> None:
        decision = chapter_controller.decide_next_action(
            self.snapshot(
                developer_calls=5,
                evaluator_calls=5,
                researcher_calls=2,
                assessment_outcome="ready_for_final_pass",
            )
        )
        self.assertEqual(
            decision.action,
            chapter_controller.ActionKind.PREPARE_FINAL_PASS_CLOSEOUT,
        )
        self.assertEqual(
            decision.state,
            chapter_controller.ChapterState.FINAL_PASS_REQUIRED,
        )
        self.assertEqual(decision.max_provider_calls, 0)
        self.assertTrue(decision.executable)

    def test_resolved_source_requests_do_not_hold_the_controller(
        self,
    ) -> None:
        waiting = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="source_approval_required",
                source_requests_pending=True,
            )
        )
        resumed = chapter_controller.decide_next_action(
            self.snapshot(
                legacy_status="source_approval_required",
                source_requests_pending=False,
            )
        )
        self.assertEqual(
            waiting.action,
            chapter_controller.ActionKind.REQUEST_SOURCE_APPROVAL,
        )
        self.assertTrue(resumed.executable)

    def test_artifact_state_selects_develop_evaluate_and_research(
        self,
    ) -> None:
        develop = chapter_controller.decide_next_action(
            self.snapshot(workspace_exists=False)
        )
        evaluate = chapter_controller.decide_next_action(
            self.snapshot(matching_assessment_exists=False)
        )
        research = chapter_controller.decide_next_action(
            self.snapshot(assessment_outcome="continue_research")
        )
        self.assertEqual(
            develop.state,
            chapter_controller.ChapterState.WORKSPACE_DEVELOPMENT,
        )
        self.assertEqual(
            evaluate.state,
            chapter_controller.ChapterState.AWAITING_EVALUATION,
        )
        self.assertEqual(
            research.state,
            chapter_controller.ChapterState.RESEARCH_REQUIRED,
        )


class ChapterEventLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = (
            Path(self.temporary_directory.name) / "CH1.controller.jsonl"
        )
        self.ledger = chapter_controller.ChapterEventLedger(self.path)
        self.snapshot = chapter_controller.ChapterSnapshot(
            book_id="book-test",
            chapter_id="CH1",
            legacy_status="active",
            developer_calls=0,
            evaluator_calls=0,
            researcher_calls=0,
            call_limit=2,
            workspace_exists=False,
            matching_assessment_exists=False,
            assessment_outcome=None,
            human_input_exists=False,
            closeout_authorized=False,
            recovery_kind=None,
            final_closeout_pending=False,
            source_requests_pending=False,
        )
        self.decision = chapter_controller.decide_next_action(
            self.snapshot
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_contract_is_idempotent_before_execution_starts(self) -> None:
        first = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        second = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.assertEqual(first["contract_id"], second["contract_id"])
        self.assertEqual(
            [
                event["event_type"]
                for event in self.ledger.read()
            ],
            ["legacy_state_imported", "contract_opened"],
        )

    def test_started_unclosed_contract_is_an_interruption(self) -> None:
        contract = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(contract["contract_id"])
        self.assertEqual(
            self.ledger.interrupted_contract_id(),
            contract["contract_id"],
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "requires reconciliation",
        ):
            self.ledger.open_contract(self.snapshot, self.decision)

    def test_provider_acknowledgements_cannot_exceed_budget(self) -> None:
        contract = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(contract["contract_id"])
        self.ledger.acknowledge_provider_call(
            contract["contract_id"],
            backend_selector.CHAPTER_DEVELOPMENT_CALL,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "exceeds the action contract",
        ):
            self.ledger.acknowledge_provider_call(
                contract["contract_id"],
                backend_selector.CHAPTER_EVALUATION_CALL,
            )

    def test_artifact_review_requirement_keeps_contract_active(self) -> None:
        contract = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(contract["contract_id"])
        self.ledger.acknowledge_provider_call(
            contract["contract_id"],
            backend_selector.CHAPTER_DEVELOPMENT_CALL,
        )
        self.ledger.require_artifact_review(
            contract["contract_id"],
            backend_selector.CHAPTER_DEVELOPMENT_CALL,
            "RuntimeError",
            "The workspace transition is incomplete.",
        )

        requirement = self.ledger.artifact_review_requirement(
            contract["contract_id"]
        )

        self.assertEqual(
            requirement["message"],
            "The workspace transition is incomplete.",
        )
        self.assertEqual(requirement["artifact_changes"], [])
        self.assertEqual(
            self.ledger.interrupted_contract_id(),
            contract["contract_id"],
        )
        self.assertEqual(self.ledger.failed_contracts(self.ledger.read()), [])

    def test_same_failed_snapshot_cannot_retry(self) -> None:
        contract = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(contract["contract_id"])
        self.ledger.fail(
            contract["contract_id"],
            "RuntimeError",
            "preflight failed",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "cannot retry without explicit",
        ):
            self.ledger.open_contract(self.snapshot, self.decision)

    def test_changed_environment_authorizes_the_only_same_state_retry(
        self,
    ) -> None:
        first = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(first["contract_id"])
        self.ledger.fail(
            first["contract_id"],
            "provider_preflight_failed",
            "MCP was unavailable.",
        )
        self.ledger.authorize_retry(
            first["contract_id"],
            "MCP configuration was repaired and verified.",
        )
        second = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.assertEqual(second["attempt_number"], 2)

    def test_only_one_authorized_changed_snapshot_retry_is_allowed(
        self,
    ) -> None:
        first = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(first["contract_id"])
        self.ledger.fail(first["contract_id"], "RuntimeError", "first")
        self.ledger.authorize_retry(
            first["contract_id"],
            "The provider configuration changed.",
        )

        changed = replace(
            self.snapshot,
            developer_calls=1,
            call_limit=4,
        )
        changed_decision = chapter_controller.decide_next_action(changed)
        second = self.ledger.open_contract(changed, changed_decision)
        self.assertEqual(second["attempt_number"], 2)
        self.ledger.mark_started(second["contract_id"])
        self.ledger.fail(second["contract_id"], "RuntimeError", "second")

        changed_again = replace(
            self.snapshot,
            developer_calls=2,
            call_limit=4,
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "exhausted its one changed-input retry",
        ):
            self.ledger.open_contract(
                changed_again,
                chapter_controller.decide_next_action(changed_again),
            )

    def test_exhausted_human_input_does_not_poison_new_assessment(
        self,
    ) -> None:
        first_snapshot = replace(
            self.snapshot,
            legacy_status="human_input_required",
            workspace_exists=True,
            matching_assessment_exists=True,
            assessment_outcome="human_input_required",
            human_input_exists=True,
            assessment_sha256="a" * 64,
            workspace_sha256="w" * 64,
            human_input_sha256="h" * 64,
            call_limit=4,
        )
        first_decision = chapter_controller.decide_next_action(
            first_snapshot
        )
        first = self.ledger.open_contract(first_snapshot, first_decision)
        self.ledger.mark_started(first["contract_id"])
        self.ledger.fail(first["contract_id"], "RuntimeError", "first")
        self.ledger.authorize_retry(
            first["contract_id"],
            "The same assessment received an exact changed condition.",
        )
        retry_snapshot = replace(first_snapshot, developer_calls=1)
        retry = self.ledger.open_contract(
            retry_snapshot,
            chapter_controller.decide_next_action(retry_snapshot),
        )
        self.ledger.mark_started(retry["contract_id"])
        self.ledger.fail(retry["contract_id"], "RuntimeError", "second")

        new_assessment = replace(
            retry_snapshot,
            developer_calls=2,
            assessment_sha256="b" * 64,
            human_input_sha256="i" * 64,
        )
        new_decision = chapter_controller.decide_next_action(new_assessment)

        self.assertIsNone(
            self.ledger.retry_requirement(new_assessment, new_decision)
        )
        contract = self.ledger.open_contract(
            new_assessment,
            new_decision,
        )
        self.assertEqual(contract["attempt_number"], 1)

    def test_proven_system_repair_restores_one_fresh_retry_budget(self) -> None:
        first = self.ledger.open_contract(self.snapshot, self.decision)
        self.ledger.mark_started(first["contract_id"])
        self.ledger.fail(first["contract_id"], "RuntimeError", "first")
        self.ledger.authorize_retry(
            first["contract_id"],
            "The automatic question constraint was recorded.",
        )
        second = self.ledger.open_contract(self.snapshot, self.decision)
        self.ledger.mark_started(second["contract_id"])
        self.ledger.fail(second["contract_id"], "RuntimeError", "second")

        self.ledger.authorize_system_repair(
            [first["contract_id"], second["contract_id"]],
            self.decision.action.value,
            "mutable_knowledge_graph_snapshot_validation",
            {"context_sha256": "a" * 64},
        )

        repaired = self.ledger.open_contract(self.snapshot, self.decision)
        self.assertEqual(repaired["attempt_number"], 1)
        self.assertEqual(
            self.ledger.failed_contracts(self.ledger.read()),
            [],
        )
        with self.assertRaisesRegex(RuntimeError, "already used system repair"):
            self.ledger.authorize_system_repair(
                [first["contract_id"], second["contract_id"]],
                self.decision.action.value,
                "mutable_knowledge_graph_snapshot_validation",
                {"context_sha256": "a" * 64},
            )

    def test_contract_preserves_the_exact_run_baseline(self) -> None:
        baseline = {
            "book_id": "book-test",
            "chapter_id": "CH1",
            "status": "active",
            "developer_passes_total": 0,
        }
        contract = self.ledger.open_contract(
            self.snapshot,
            self.decision,
            run_baseline=baseline,
        )
        self.assertEqual(contract["run_baseline"], baseline)
        self.assertEqual(
            contract["snapshot"]["chapter_id"],
            self.snapshot.chapter_id,
        )

    def test_interrupted_contract_can_be_explicitly_reconciled(
        self,
    ) -> None:
        contract = self.ledger.open_contract(
            self.snapshot,
            self.decision,
        )
        self.ledger.mark_started(contract["contract_id"])
        self.ledger.reconcile(
            contract["contract_id"],
            "no_provider_output",
            self.snapshot,
            "The provider process did not start.",
        )
        self.assertIsNone(self.ledger.interrupted_contract_id())
        self.assertEqual(
            self.ledger.read()[-1]["event_type"],
            "contract_reconciled",
        )

    def test_hash_chain_detects_tampering(self) -> None:
        self.ledger.initialize(self.snapshot)
        event = json.loads(self.path.read_text(encoding="utf-8"))
        event["payload"]["chapter_id"] = "CH9"
        self.path.write_text(
            json.dumps(event) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(
            RuntimeError,
            "integrity validation",
        ):
            self.ledger.read()


class ChapterControllerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        self.books = self.project / "data" / "books"
        self.book_id = "book-test"
        self.chapter_id = "CH1"
        self.run_path = (
            self.books
            / self.book_id
            / "runs"
            / f"{self.chapter_id}.json"
        )
        self.run_path.parent.mkdir(parents=True)
        self.write_run()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_run(self) -> None:
        self.run_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "book_id": self.book_id,
                    "chapter_id": self.chapter_id,
                    "status": "active",
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                    "developer_passes_total": 0,
                    "evaluator_passes_total": 0,
                    "researcher_passes_total": 0,
                    "final_pass_requested": False,
                    "repeated_assessment_signature_count": 0
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def patches(self):
        return (
            patch.object(backend_selector, "PROJECT_PATH", self.project),
            patch.object(backend_selector, "BOOKS_PATH", self.books),
        )

    def test_retry_condition_is_bound_only_to_the_next_contract(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = backend_selector.build_chapter_controller_snapshot(
                self.book_id,
                self.chapter_id,
            )
            decision = chapter_controller.decide_next_action(snapshot)
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            failed = ledger.open_contract(snapshot, decision)
            ledger.mark_started(failed["contract_id"])
            ledger.fail(
                failed["contract_id"],
                "no_progress",
                "The provider made no workspace transition.",
            )
            ledger.authorize_retry(
                failed["contract_id"],
                "Narrow the chapter and preserve all unresolved choices.",
            )
            retry = ledger.open_contract(snapshot, decision)
            ledger.mark_started(retry["contract_id"])
            token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
                (ledger.path, retry["contract_id"])
            )
            try:
                authorization = (
                    backend_selector.active_chapter_retry_authorization()
                )
            finally:
                backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

            self.assertEqual(
                authorization,
                {
                    "failed_contract_id": failed["contract_id"],
                    "action": decision.action.value,
                    "changed_condition": (
                        "Narrow the chapter and preserve all unresolved "
                        "choices."
                    ),
                },
            )

            ledger.complete(retry["contract_id"], snapshot)
            later = ledger.open_contract(snapshot, decision)
            token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
                (ledger.path, later["contract_id"])
            )
            try:
                self.assertIsNone(
                    backend_selector.active_chapter_retry_authorization()
                )
            finally:
                backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

    def test_chapter_prompt_exposes_bounded_retry_condition(self) -> None:
        changed_condition = (
            "Remove the deferred answers as dependencies and narrow claims."
        )
        prompt = backend_selector.build_chapter_developer_prompt(
            "Developer rules",
            self.book_id,
            self.chapter_id,
            True,
            {"current_version": 2},
            [],
            retry_authorization={
                "failed_contract_id": "CC-1-example",
                "action": "run_chapter_phase",
                "changed_condition": changed_condition,
            },
        )

        self.assertIn('"retry_authorization": {', prompt)
        self.assertIn(changed_condition, prompt)
        self.assertIn(
            "exact human-authorized narrowing for this transition",
            prompt,
        )

    def test_exhausted_retry_status_is_saved_without_provider_work(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = backend_selector.build_chapter_controller_snapshot(
                self.book_id,
                self.chapter_id,
            )
            decision = chapter_controller.decide_next_action(snapshot)
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            first = ledger.open_contract(snapshot, decision)
            ledger.mark_started(first["contract_id"])
            ledger.fail(first["contract_id"], "RuntimeError", "first")
            ledger.authorize_retry(first["contract_id"], "Narrow scope")
            second = ledger.open_contract(snapshot, decision)
            ledger.mark_started(second["contract_id"])
            ledger.fail(second["contract_id"], "RuntimeError", "second")

            result = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            saved = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "controller_retry_exhausted")
        self.assertEqual(saved["status"], "controller_retry_exhausted")
        self.assertEqual(
            saved["stop_reason"],
            "The chapter action exhausted its one authorized retry.",
        )

    def test_preflight_failure_is_not_counted_and_cannot_loop(self) -> None:
        def fail_after_legacy_precount(*_args):
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] += 1
            backend_selector.save_chapter_run(run)
            raise RuntimeError("provider preflight failed")

        with self.patches()[0], self.patches()[1], patch.object(
            backend_selector,
            "run_chapter_completion_loop",
            side_effect=fail_after_legacy_precount,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "provider preflight failed",
            ):
                backend_selector.run_chapter_controller_action(
                    "codex",
                    "/usr/bin/codex",
                    self.book_id,
                    self.chapter_id,
                )

            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            self.assertEqual(run["developer_passes_total"], 0)
            retry = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            self.assertEqual(
                retry["status"],
                "retry_authorization_required",
            )

    def test_legacy_performance_no_progress_hold_requires_ledger_proof(
        self,
    ) -> None:
        with self.patches()[0], self.patches()[1]:
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "performance_repair_required",
                    "workflow_runtime_version": "performance-v2",
                    "performance_repair_cycles_total": 1,
                    "last_assessment_outcome": "revise_draft",
                }
            )
            backend_selector.save_chapter_run(run)
            snapshot = backend_selector.build_chapter_controller_snapshot(
                self.book_id,
                self.chapter_id,
            )
            legacy_bad_decision = chapter_controller.NextAction(
                chapter_controller.ChapterState.WORKSPACE_DEVELOPMENT,
                chapter_controller.ActionKind.RUN_CHAPTER_PHASE,
                "The current assessment permits another development phase.",
                1,
            )
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(
                snapshot,
                legacy_bad_decision,
                run_baseline=run,
            )
            ledger.mark_started(contract["contract_id"])
            run["status"] = "controller_no_progress"
            backend_selector.save_chapter_run(run)
            ledger.fail(
                contract["contract_id"],
                "no_progress",
                "No approved artifact or provider-call record changed.",
            )

            self.assertTrue(
                backend_selector.performance_repair_review_required(
                    self.book_id,
                    self.chapter_id,
                )
            )

            ledger.append(
                "diagnostic_recorded",
                {"note": "A later unrelated event breaks direct lineage."},
            )
            self.assertFalse(
                backend_selector.performance_repair_review_required(
                    self.book_id,
                    self.chapter_id,
                )
            )

    def test_performance_repair_hold_opens_no_zero_call_contract(self) -> None:
        with self.patches()[0], self.patches()[1], patch.object(
            backend_selector,
            "run_chapter_completion_loop",
        ) as provider_executor:
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run.update(
                {
                    "status": "performance_repair_required",
                    "workflow_runtime_version": "performance-v2",
                    "performance_repair_cycles_total": 1,
                    "last_assessment_outcome": "revise_draft",
                }
            )
            backend_selector.save_chapter_run(run)

            held = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )

        provider_executor.assert_not_called()
        self.assertEqual(held["status"], "performance_repair_required")
        self.assertEqual(
            held["controller_action"],
            "request_recovery_authorization",
        )
        self.assertFalse(
            backend_selector.chapter_controller_ledger_path(
                self.book_id,
                self.chapter_id,
            ).exists()
        )

    def test_acknowledged_call_becomes_the_only_counted_call(self) -> None:
        def acknowledge_and_finish(*_args):
            ledger_path, contract_id = (
                backend_selector.ACTIVE_CHAPTER_CONTRACT.get()
            )
            chapter_controller.ChapterEventLedger(
                ledger_path
            ).acknowledge_provider_call(
                contract_id,
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["developer_passes_total"] = 3
            run["status"] = "chapter_complete"
            backend_selector.save_chapter_run(run)
            return run

        with self.patches()[0], self.patches()[1], patch.object(
            backend_selector,
            "run_chapter_completion_loop",
            side_effect=acknowledge_and_finish,
        ):
            run = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
        self.assertEqual(run["developer_passes_total"], 1)
        self.assertEqual(run["status"], "chapter_complete")
        ledger = chapter_controller.ChapterEventLedger(
            self.books
            / self.book_id
            / "runs"
            / f"{self.chapter_id}.controller.jsonl"
        )
        self.assertEqual(
            ledger.read()[-1]["event_type"],
            "contract_completed",
        )

    def test_exhausted_active_run_materializes_call_limit_hold(self) -> None:
        run = json.loads(self.run_path.read_text(encoding="utf-8"))
        run.update(
            {
                "developer_passes_total": 5,
                "evaluator_passes_total": 4,
                "researcher_passes_total": 3,
            }
        )
        self.run_path.write_text(
            json.dumps(run) + "\n",
            encoding="utf-8",
        )

        with (
            self.patches()[0],
            self.patches()[1],
            patch.object(
                backend_selector,
                "run_chapter_completion_loop",
            ) as provider_executor,
        ):
            result = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            persisted = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        provider_executor.assert_not_called()
        self.assertEqual(result["status"], "agent_call_limit_reached")
        self.assertIn("lifetime", result["stop_reason"])
        self.assertEqual(persisted["status"], "agent_call_limit_reached")

    def test_final_repair_budget_takes_priority_over_old_closeout_limit(
        self,
    ) -> None:
        run = json.loads(self.run_path.read_text(encoding="utf-8"))
        run.update(
            {
                "developer_passes_total": 10,
                "evaluator_passes_total": 8,
                "status": "human_input_closeout_failed",
            }
        )
        recovery = {
            "recovery_kind": "final_pass_revision",
            "baseline_agent_calls_total": 18,
            "max_recovery_agent_calls": 2,
        }
        closeout = {
            "baseline_agent_calls_total": 13,
            "max_calls": 6,
        }
        with (
            patch.object(
                backend_selector,
                "load_chapter_recovery_authorization",
                return_value=recovery,
            ),
            patch.object(
                backend_selector,
                "load_chapter_human_input_closeout",
                return_value=closeout,
            ),
        ):
            limit = backend_selector.chapter_provider_call_limit(
                self.book_id,
                self.chapter_id,
                run,
            )

        self.assertEqual(limit, 20)

    def test_exact_limit_prepares_two_call_final_closeout_without_provider(
        self,
    ) -> None:
        run = json.loads(self.run_path.read_text(encoding="utf-8"))
        run.update(
            {
                "developer_passes_total": 5,
                "evaluator_passes_total": 5,
                "researcher_passes_total": 2,
            }
        )
        self.run_path.write_text(
            json.dumps(run) + "\n",
            encoding="utf-8",
        )
        workspace_document = {
            "workspace_sha256": "w" * 64,
            "workspace": {
                "draft_versions": [
                    {
                        "draft_id": "D1",
                        "relative_path": "drafts/CH1/D1.md",
                        "sha256": "d" * 64,
                    }
                ]
            },
        }
        assessment_path = (
            self.books
            / self.book_id
            / "assessments"
            / self.chapter_id
            / "A6.json"
        )
        assessment_document = {
            "assessment_id": "A6",
            "assessment_sha256": "a" * 64,
            "assessment": {
                "outcome": "ready_for_final_pass",
                "most_important_gap": "Only the final pass remains.",
                "required_next_actions": ["Perform the final pass."],
                "human_input_requests": [],
                "research_request": None,
                "criteria": {
                    "central_lesson": {"status": "satisfied"}
                },
            },
        }
        matching_assessment = (
            assessment_path,
            assessment_document,
        )

        with (
            self.patches()[0],
            self.patches()[1],
            patch.object(
                backend_selector,
                "load_chapter_workspace_state",
                return_value={"current_json_path": "unused.json"},
            ),
            patch.object(
                backend_selector,
                "load_workspace_document",
                return_value=workspace_document,
            ),
            patch.object(
                backend_selector,
                "matching_latest_assessment",
                return_value=matching_assessment,
            ),
            patch.object(
                backend_selector,
                "run_chapter_completion_loop",
            ) as provider_executor,
        ):
            prepared = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            restarted_snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            restarted_decision = chapter_controller.decide_next_action(
                restarted_snapshot
            )

        provider_executor.assert_not_called()
        self.assertEqual(
            backend_selector.chapter_agent_calls_total(prepared),
            12,
        )
        self.assertTrue(prepared["final_pass_requested"])
        self.assertEqual(prepared["final_pass_source_draft_id"], "D1")
        self.assertEqual(
            prepared["final_closeout_baseline_agent_calls_total"],
            12,
        )
        self.assertEqual(
            prepared["final_closeout_source_draft_id"],
            "D1",
        )
        self.assertEqual(prepared["last_assessment_id"], "A6")
        self.assertEqual(restarted_snapshot.call_limit, 14)
        self.assertEqual(restarted_snapshot.calls_remaining, 2)
        self.assertEqual(
            restarted_decision.action,
            chapter_controller.ActionKind.RUN_FINAL_PASS_CLOSEOUT,
        )
        self.assertEqual(restarted_decision.max_provider_calls, 1)

        ledger = chapter_controller.ChapterEventLedger(
            self.books
            / self.book_id
            / "runs"
            / f"{self.chapter_id}.controller.jsonl"
        )
        events = ledger.read()
        contract_id = next(
            event["payload"]["contract_id"]
            for event in events
            if event["event_type"] == "contract_opened"
        )
        opened = next(
            event["payload"]
            for event in events
            if event["event_type"] == "contract_opened"
        )
        self.assertEqual(opened["max_provider_calls"], 0)
        self.assertEqual(
            opened["action"],
            "prepare_final_pass_closeout",
        )
        self.assertEqual(
            ledger.provider_call_counts(contract_id),
            {},
        )
        self.assertEqual(events[-1]["event_type"], "contract_completed")

    def test_ambiguous_provider_side_effect_holds_across_restart(self) -> None:
        provider_calls = 0

        def leave_partial_artifact(*_args):
            nonlocal provider_calls
            provider_calls += 1
            ledger_path, contract_id = (
                backend_selector.ACTIVE_CHAPTER_CONTRACT.get()
            )
            ledger = chapter_controller.ChapterEventLedger(ledger_path)
            ledger.acknowledge_provider_call(
                contract_id,
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            before_artifacts = (
                backend_selector.chapter_provider_artifact_state(
                    self.book_id
                )
            )
            context_directory = (
                self.books / self.book_id / "contexts"
            )
            context_directory.mkdir(parents=True)
            (
                context_directory / f"{self.chapter_id}.json"
            ).write_text("{}\n", encoding="utf-8")
            (
                context_directory / f"{self.chapter_id}.md"
            ).write_text("Incomplete context\n", encoding="utf-8")
            backend_selector.finish_chapter_provider_transition(
                self.book_id,
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
                before_artifacts,
                RuntimeError("provider timed out"),
                RuntimeError(
                    "Context exists, but the workspace transition is "
                    "missing."
                ),
            )

        with self.patches()[0], self.patches()[1], patch.object(
            backend_selector,
            "run_chapter_completion_loop",
            side_effect=leave_partial_artifact,
        ):
            first = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            second = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
            held = (
                backend_selector.reconcile_interrupted_chapter_action(
                    self.book_id,
                    self.chapter_id,
                )
            )

        self.assertEqual(
            first["status"],
            "controller_reconciliation_required",
        )
        self.assertEqual(
            second["status"],
            "controller_reconciliation_required",
        )
        self.assertEqual(
            held["status"],
            "artifact_review_required",
        )
        self.assertEqual(
            [change["path"] for change in held["artifact_changes"]],
            [
                f"contexts/{self.chapter_id}.json",
                f"contexts/{self.chapter_id}.md",
            ],
        )
        self.assertEqual(provider_calls, 1)

    def test_exact_artifacts_recover_provider_error_without_retry(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(snapshot, decision)
            ledger.mark_started(contract["contract_id"])
            ledger.acknowledge_provider_call(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
                (ledger.path, contract["contract_id"])
            )
            try:
                backend_selector.finish_chapter_provider_transition(
                    self.book_id,
                    backend_selector.CHAPTER_DEVELOPMENT_CALL,
                    {},
                    RuntimeError("provider timed out"),
                    None,
                )
            finally:
                backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

        self.assertEqual(
            ledger.read()[-1]["event_type"],
            "provider_error_recovered",
        )
        self.assertEqual(
            ledger.provider_call_counts(contract["contract_id"]),
            {backend_selector.CHAPTER_DEVELOPMENT_CALL: 1},
        )

    def test_empty_provider_failure_remains_a_normal_failure(self) -> None:
        with self.patches()[0], self.patches()[1], self.assertRaisesRegex(
            RuntimeError,
            "provider timed out",
        ):
            backend_selector.finish_chapter_provider_transition(
                self.book_id,
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
                {},
                RuntimeError("provider timed out"),
                RuntimeError("workspace is missing"),
            )

    def test_partial_artifact_requires_review_instead_of_retry(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(snapshot, decision)
            ledger.mark_started(contract["contract_id"])
            ledger.acknowledge_provider_call(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            context_path = (
                self.books
                / self.book_id
                / "contexts"
                / f"{self.chapter_id}.json"
            )
            context_path.parent.mkdir(parents=True)
            context_path.write_text("{}\n", encoding="utf-8")
            token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
                (ledger.path, contract["contract_id"])
            )
            try:
                with self.assertRaisesRegex(
                    backend_selector.ChapterArtifactReconciliationRequired,
                    "do not prove the required single transition",
                ):
                    backend_selector.finish_chapter_provider_transition(
                        self.book_id,
                        backend_selector.CHAPTER_DEVELOPMENT_CALL,
                        {},
                        RuntimeError("provider timed out"),
                        RuntimeError("workspace is missing"),
                    )
            finally:
                backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

        self.assertEqual(
            ledger.artifact_review_requirement(
                contract["contract_id"]
            )["failure_kind"],
            "RuntimeError",
        )
        self.assertEqual(
            ledger.artifact_review_requirement(
                contract["contract_id"]
            )["artifact_changes"],
            [
                {
                    "path": f"contexts/{self.chapter_id}.json",
                    "change_type": "added",
                    "before_sha256": None,
                    "after_sha256": backend_selector.file_sha256(
                        context_path
                    ),
                }
            ],
        )
        self.assertEqual(
            ledger.interrupted_contract_id(),
            contract["contract_id"],
        )

    def test_human_rejection_closes_partial_transition_for_retry(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(snapshot, decision)
            ledger.mark_started(contract["contract_id"])
            ledger.acknowledge_provider_call(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            context_path = (
                self.books
                / self.book_id
                / "contexts"
                / f"{self.chapter_id}.json"
            )
            context_path.parent.mkdir(parents=True)
            context_path.write_text("{}\n", encoding="utf-8")
            ledger.require_artifact_review(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
                "RuntimeError",
                "Workspace initialization is missing.",
                [
                    {
                        "path": f"contexts/{self.chapter_id}.json",
                        "change_type": "added",
                        "before_sha256": None,
                        "after_sha256": (
                            backend_selector.file_sha256(context_path)
                        ),
                    }
                ],
            )
            with patch(
                "builtins.input",
                side_effect=[
                    "1",
                    (
                        "The valid context exists, but the required workspace "
                        "transition is missing."
                    ),
                ],
            ):
                reviewed = (
                    backend_selector
                    .record_chapter_artifact_review_checkpoint(
                        self.book_id,
                        self.chapter_id,
                    )
                )
            current = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            retry = ledger.retry_requirement(
                current,
                chapter_controller.decide_next_action(current),
            )

        self.assertEqual(
            reviewed["payload"]["disposition"],
            "provider_output_rejected",
        )
        self.assertTrue(context_path.is_file())
        self.assertIsNone(ledger.interrupted_contract_id())
        self.assertEqual(retry["status"], "authorization_required")
        self.assertEqual(
            retry["failed_contract_id"],
            contract["contract_id"],
        )

    def test_human_can_keep_partial_transition_held(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(snapshot, decision)
            ledger.mark_started(contract["contract_id"])
            ledger.require_artifact_review(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
                "RuntimeError",
                "Workspace initialization is missing.",
            )
            with patch("builtins.input", return_value="2"):
                reviewed = (
                    backend_selector
                    .record_chapter_artifact_review_checkpoint(
                        self.book_id,
                        self.chapter_id,
                    )
                )

        self.assertIsNone(reviewed)
        self.assertEqual(
            ledger.interrupted_contract_id(),
            contract["contract_id"],
        )

    def test_artifact_change_list_is_exact_and_sorted(self) -> None:
        changes = backend_selector.chapter_provider_artifact_changes(
            {
                "modified.md": "a" * 64,
                "removed.md": "b" * 64,
            },
            {
                "added.md": "c" * 64,
                "modified.md": "d" * 64,
            },
        )

        self.assertEqual(
            [(item["path"], item["change_type"]) for item in changes],
            [
                ("added.md", "added"),
                ("modified.md", "modified"),
                ("removed.md", "removed"),
            ],
        )

    def test_unchanged_zero_call_action_stops_as_no_progress(self) -> None:
        def unchanged(*_args):
            return backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        with self.patches()[0], self.patches()[1], patch.object(
            backend_selector,
            "run_chapter_completion_loop",
            side_effect=unchanged,
        ):
            run = backend_selector.run_chapter_controller_action(
                "codex",
                "/usr/bin/codex",
                self.book_id,
                self.chapter_id,
            )
        self.assertEqual(run["status"], "controller_no_progress")
        self.assertIn("changed no approved artifact", run["stop_reason"])

    def test_zero_ack_interruption_restores_exact_call_baseline(
        self,
    ) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            baseline = (
                backend_selector.load_or_create_chapter_run(
                    self.book_id,
                    self.chapter_id,
                )
            )
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(
                snapshot,
                decision,
                run_baseline=baseline,
            )
            ledger.mark_started(contract["contract_id"])
            changed = dict(baseline)
            changed["developer_passes_total"] = 1
            backend_selector.save_chapter_run(changed)

            result = (
                backend_selector.reconcile_interrupted_chapter_action(
                    self.book_id,
                    self.chapter_id,
                )
            )
            restored = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(
            result["status"],
            "retry_authorization_required",
        )
        self.assertEqual(restored["developer_passes_total"], 0)
        self.assertEqual(
            ledger.read()[-1]["payload"]["disposition"],
            "no_provider_output",
        )

    def test_ack_without_artifact_change_authorizes_bounded_retry(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            baseline = (
                backend_selector.load_or_create_chapter_run(
                    self.book_id,
                    self.chapter_id,
                )
            )
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(
                snapshot,
                decision,
                run_baseline=baseline,
            )
            ledger.mark_started(contract["contract_id"])
            ledger.acknowledge_provider_call(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            result = (
                backend_selector.reconcile_interrupted_chapter_action(
                    self.book_id,
                    self.chapter_id,
                )
            )
            current = backend_selector.build_chapter_controller_snapshot(
                self.book_id,
                self.chapter_id,
            )
            retry = ledger.retry_requirement(
                current,
                chapter_controller.decide_next_action(current),
            )
            recovered_run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )

        self.assertEqual(result["status"], "controller_reconciled")
        self.assertEqual(
            result["call_kind"],
            backend_selector.CHAPTER_DEVELOPMENT_CALL,
        )
        self.assertEqual(result["artifact_changes"], [])
        self.assertTrue(result["replacement_call_available"])
        self.assertEqual(recovered_run["developer_passes_total"], 1)
        self.assertIsNone(ledger.interrupted_contract_id())
        self.assertIsNone(retry)
        self.assertEqual(
            ledger.read()[-1]["payload"]["disposition"],
            "interrupted_no_artifact",
        )

    def test_legacy_empty_artifact_review_is_reconciled_automatically(
        self,
    ) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            decision = chapter_controller.decide_next_action(snapshot)
            baseline = (
                backend_selector.load_or_create_chapter_run(
                    self.book_id,
                    self.chapter_id,
                )
            )
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            contract = ledger.open_contract(
                snapshot,
                decision,
                run_baseline=baseline,
            )
            ledger.mark_started(contract["contract_id"])
            ledger.acknowledge_provider_call(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            ledger.require_artifact_review(
                contract["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
                "InterruptedProviderCall",
                (
                    "One provider call was acknowledged, but no canonical "
                    "chapter artifact transition was observed after restart."
                ),
            )
            reconciliation = (
                backend_selector.reconcile_interrupted_chapter_action(
                    self.book_id,
                    self.chapter_id,
                )
            )
            current = (
                backend_selector.build_chapter_controller_snapshot(
                    self.book_id,
                    self.chapter_id,
                )
            )
            retry = ledger.retry_requirement(
                current,
                chapter_controller.decide_next_action(current),
            )

        self.assertEqual(
            reconciliation["status"],
            "controller_reconciled",
        )
        self.assertTrue(reconciliation["replacement_call_available"])
        self.assertIsNone(ledger.interrupted_contract_id())
        self.assertIsNone(retry)

    def test_legacy_automatic_interruption_does_not_exhaust_retry(self) -> None:
        with self.patches()[0], self.patches()[1]:
            snapshot = backend_selector.build_chapter_controller_snapshot(
                self.book_id,
                self.chapter_id,
            )
            decision = chapter_controller.decide_next_action(snapshot)
            baseline = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            ledger = chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    self.book_id,
                    self.chapter_id,
                )
            )
            interrupted = ledger.open_contract(
                snapshot,
                decision,
                run_baseline=baseline,
            )
            ledger.mark_started(interrupted["contract_id"])
            ledger.acknowledge_provider_call(
                interrupted["contract_id"],
                backend_selector.CHAPTER_DEVELOPMENT_CALL,
            )
            ledger.reconcile(
                interrupted["contract_id"],
                "provider_output_rejected",
                snapshot,
                (
                    "The acknowledged provider process ended during restart "
                    "without changing any canonical chapter artifact. The "
                    "incomplete output was rejected and its attempt remains "
                    "counted."
                ),
            )
            retry_contract = ledger.open_contract(
                snapshot,
                decision,
                run_baseline=baseline,
            )
            ledger.mark_started(retry_contract["contract_id"])
            ledger.fail(
                retry_contract["contract_id"],
                "RuntimeError",
                "The provider missed its required tool transition.",
            )
            requirement = ledger.retry_requirement(snapshot, decision)

        self.assertEqual(requirement["status"], "authorization_required")
        self.assertEqual(
            requirement["failed_contract_id"],
            retry_contract["contract_id"],
        )

    def test_first_human_input_uses_the_pending_assessment_id(self) -> None:
        with self.patches()[0], self.patches()[1]:
            run = backend_selector.load_or_create_chapter_run(
                self.book_id,
                self.chapter_id,
            )
            run["status"] = "human_input_required"
            run["last_assessment_id"] = "A1"
            run["last_assessment_sha256"] = "a" * 64
            run["human_input_requests"] = ["Approve the condition?"]
            backend_selector.save_chapter_run(run)
            path = backend_selector.record_chapter_human_input(
                self.book_id,
                self.chapter_id,
                ["Approved with the stated boundary."],
                "Test authorization.",
            )
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["assessment_id"], "A1")
        self.assertEqual(
            document["decisions"][0]["question"],
            "Approve the condition?",
        )


if __name__ == "__main__":
    unittest.main()
