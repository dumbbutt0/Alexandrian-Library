import json
import tempfile
import unittest
from pathlib import Path

import synthesis_tool_budget


class SynthesisToolBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary_directory.name)
        self.book_id = "book-budget-test"
        self.cycle_id = "cycle-004"
        self.action_id = "plan-shard-CH2-test"
        self.stage = "plan-shard-CH2"
        self.backend = "claude"
        self.input_hash = "a" * 64
        self.ledger = (
            self.project
            / "data"
            / "books"
            / self.book_id
            / "runs"
            / "synthesis.controller.jsonl"
        )
        self.ledger.parent.mkdir(parents=True)
        self._open_action(self.action_id)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _open_action(self, action_id: str) -> None:
        event = {
            "schema_version": "1.0",
            "event": "contract_opened",
            "book_id": self.book_id,
            "cycle_id": self.cycle_id,
            "action_id": action_id,
            "stage": self.stage,
            "backend": self.backend,
            "input_hash": self.input_hash,
        }
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")

    def environment(self, action_id: str | None = None) -> dict[str, str]:
        values = {
            "book_id": self.book_id,
            "cycle_id": self.cycle_id,
            "action_id": action_id or self.action_id,
            "stage": self.stage,
            "backend": self.backend,
            "input_hash": self.input_hash,
        }
        return {
            synthesis_tool_budget.CONTEXT_ENV[field]: value
            for field, value in values.items()
        }

    def reserve(
        self,
        tool_name: str,
        arguments: object | None = None,
        action_id: str | None = None,
    ) -> dict:
        return synthesis_tool_budget.reserve_tool_attempt(
            self.project,
            tool_name,
            {} if arguments is None else arguments,
            self.environment(action_id),
        )

    def test_plan_input_can_be_read_only_once(self) -> None:
        reservation = self.reserve(synthesis_tool_budget.READ_TOOL)
        synthesis_tool_budget.record_tool_result(
            self.project,
            reservation,
            "success",
        )
        with self.assertRaisesRegex(
            synthesis_tool_budget.SynthesisToolBudgetError,
            "read budget is exhausted",
        ):
            self.reserve(synthesis_tool_budget.READ_TOOL)

    def test_controller_state_is_portable_when_host_drops_environment(
        self,
    ) -> None:
        reservation = synthesis_tool_budget.reserve_tool_attempt(
            self.project,
            synthesis_tool_budget.READ_TOOL,
            {
                "book_id": self.book_id,
                "chapter_id": "CH2",
                "input_hash": self.input_hash,
            },
            {},
        )
        self.assertEqual(reservation["action_id"], self.action_id)
        self.assertEqual(reservation["backend"], "claude")

    def test_correctable_first_submission_permits_one_second_attempt(
        self,
    ) -> None:
        first = self.reserve(synthesis_tool_budget.SUBMIT_TOOL)
        synthesis_tool_budget.record_tool_result(
            self.project,
            first,
            "correctable_validation_rejected",
            "proposal defect",
        )
        second = self.reserve(synthesis_tool_budget.SUBMIT_TOOL)
        self.assertEqual(second["attempt_number"], 2)
        synthesis_tool_budget.record_tool_result(
            self.project,
            second,
            "success",
        )
        with self.assertRaisesRegex(
            synthesis_tool_budget.SynthesisToolBudgetError,
            "submission budget is exhausted",
        ):
            self.reserve(synthesis_tool_budget.SUBMIT_TOOL)

    def test_inventory_has_one_read_and_one_correctable_resubmission(
        self,
    ) -> None:
        action_id = "inventory-CH1-test"
        stage = "inventory-CH1"
        event = {
            "schema_version": "1.0",
            "event": "contract_opened",
            "book_id": self.book_id,
            "cycle_id": self.cycle_id,
            "action_id": action_id,
            "stage": stage,
            "backend": self.backend,
            "input_hash": self.input_hash,
            "tool_budget": {
                "controlled_tools": [
                    synthesis_tool_budget.INVENTORY_READ_TOOL,
                    synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                ]
            },
        }
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        environment = {
            synthesis_tool_budget.CONTEXT_ENV[field]: value
            for field, value in {
                "book_id": self.book_id,
                "cycle_id": self.cycle_id,
                "action_id": action_id,
                "stage": stage,
                "backend": self.backend,
                "input_hash": self.input_hash,
            }.items()
        }
        read = synthesis_tool_budget.reserve_tool_attempt(
            self.project,
            synthesis_tool_budget.INVENTORY_READ_TOOL,
            {},
            environment,
        )
        synthesis_tool_budget.record_tool_result(
            self.project,
            read,
            "success",
        )
        first = synthesis_tool_budget.reserve_tool_attempt(
            self.project,
            synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
            {},
            environment,
        )
        synthesis_tool_budget.record_tool_result(
            self.project,
            first,
            "correctable_validation_rejected",
            "missing canonical contribution",
        )
        second = synthesis_tool_budget.reserve_tool_attempt(
            self.project,
            synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
            {},
            environment,
        )
        self.assertEqual(second["attempt_number"], 2)
        synthesis_tool_budget.record_tool_result(
            self.project,
            second,
            "success",
        )
        with self.assertRaisesRegex(
            synthesis_tool_budget.SynthesisToolBudgetError,
            "submission budget is exhausted",
        ):
            synthesis_tool_budget.reserve_tool_attempt(
                self.project,
                synthesis_tool_budget.INVENTORY_SUBMIT_TOOL,
                {},
                environment,
            )
        summary = synthesis_tool_budget.finalize_action_budget(
            self.project,
            self.book_id,
            action_id,
        )
        self.assertEqual(summary["maximum_reads"], 1)
        self.assertEqual(summary["maximum_submissions"], 2)
        self.assertEqual(summary["maximum_successful_writes"], 1)

    def test_schema_rejection_consumes_first_and_blocks_retry(self) -> None:
        first = self.reserve(
            synthesis_tool_budget.SUBMIT_TOOL,
            {"proposal": "not-an-object"},
        )
        synthesis_tool_budget.record_tool_result(
            self.project,
            first,
            "argument_validation_rejected",
            "schema error",
        )
        with self.assertRaisesRegex(
            synthesis_tool_budget.SynthesisToolBudgetError,
            "only after a correctable",
        ):
            self.reserve(synthesis_tool_budget.SUBMIT_TOOL)

    def test_crashed_attempt_remains_consumed(self) -> None:
        self.reserve(synthesis_tool_budget.SUBMIT_TOOL)
        with self.assertRaisesRegex(
            synthesis_tool_budget.SynthesisToolBudgetError,
            "consumed without a completed result",
        ):
            self.reserve(synthesis_tool_budget.SUBMIT_TOOL)

    def test_actions_have_independent_budgets_and_hash_bound_summary(
        self,
    ) -> None:
        first = self.reserve(synthesis_tool_budget.SUBMIT_TOOL)
        synthesis_tool_budget.record_tool_result(
            self.project,
            first,
            "success",
        )
        other_action = "plan-shard-CH2-other"
        self._open_action(other_action)
        other = self.reserve(
            synthesis_tool_budget.SUBMIT_TOOL,
            action_id=other_action,
        )
        self.assertEqual(other["attempt_number"], 1)

        summary = synthesis_tool_budget.finalize_action_budget(
            self.project,
            self.book_id,
            self.action_id,
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary["maximum_submissions"], 2)
        self.assertEqual(len(summary["attempt_receipts"]), 2)
        self.assertEqual(len(summary["budget_sha256"]), 64)

    def test_audit_process_has_exactly_one_raw_submission(self) -> None:
        action_id = "audit-S1-CH1-test"
        stage = "audit-S1-CH1"
        event = {
            "schema_version": "1.0",
            "event": "contract_opened",
            "book_id": self.book_id,
            "cycle_id": self.cycle_id,
            "action_id": action_id,
            "stage": stage,
            "backend": self.backend,
            "input_hash": self.input_hash,
            "tool_budget": {
                "controlled_tools": [
                    synthesis_tool_budget.AUDIT_UNIT_TOOL,
                ]
            },
        }
        with self.ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event) + "\n")
        values = {
            "book_id": self.book_id,
            "cycle_id": self.cycle_id,
            "action_id": action_id,
            "stage": stage,
            "backend": self.backend,
            "input_hash": self.input_hash,
        }
        environment = {
            synthesis_tool_budget.CONTEXT_ENV[field]: value
            for field, value in values.items()
        }
        first = synthesis_tool_budget.reserve_tool_attempt(
            self.project,
            synthesis_tool_budget.AUDIT_UNIT_TOOL,
            {"audit": {"book_id": self.book_id, "unit_id": "CH1"}},
            environment,
        )
        synthesis_tool_budget.record_tool_result(
            self.project,
            first,
            "argument_validation_rejected",
            "schema error",
        )
        with self.assertRaisesRegex(
            synthesis_tool_budget.SynthesisToolBudgetError,
            "write submission budget is exhausted",
        ):
            synthesis_tool_budget.reserve_tool_attempt(
                self.project,
                synthesis_tool_budget.AUDIT_UNIT_TOOL,
                {"audit": {"book_id": self.book_id, "unit_id": "CH1"}},
                environment,
            )
        summary = synthesis_tool_budget.finalize_action_budget(
            self.project,
            self.book_id,
            action_id,
        )
        self.assertEqual(summary["maximum_submissions"], 1)
        self.assertEqual(summary["second_submission_rule"], "none")


if __name__ == "__main__":
    unittest.main()
