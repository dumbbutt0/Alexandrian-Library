import tempfile
import unittest
from pathlib import Path

import transition_runtime


class TransitionRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.event_path = Path(self.temporary_directory.name) / "events.jsonl"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def spec(self) -> transition_runtime.TransitionSpec:
        return transition_runtime.TransitionSpec(
            transition_id="cycle-001:audit:S1:test",
            kind="whole_book_audit_S1",
            expected_states=("audit_S1",),
            success_states=("ready_for_editorial",),
            provider="codex",
        )

    def test_mechanical_failure_retries_without_changing_semantic_state(self) -> None:
        state = {"value": "audit_S1"}
        calls = []

        def provider_call():
            calls.append(len(calls) + 1)
            if len(calls) == 1:
                return ([{"item": {"result": "1 validation error: Field required"}}], "")
            state["value"] = "ready_for_editorial"
            return ([], "Audit saved.")

        result = transition_runtime.run_provider_transition(
            transition_runtime.TransitionSpec(
                **{
                    **self.spec().__dict__,
                    "maximum_mechanical_attempts": 2,
                }
            ),
            event_path=self.event_path,
            state_reader=lambda: state["value"],
            provider_call=provider_call,
        )

        self.assertTrue(result["completed"])
        self.assertEqual(result["mechanical_attempts"], 2)
        self.assertEqual(calls, [1, 2])
        phases = [
            event["phase"]
            for event in transition_runtime.read_events(self.event_path)
        ]
        self.assertIn("mechanical_retry_scheduled", phases)
        self.assertEqual(phases[-1], "durable_artifact_saved")

    def test_mechanical_failure_does_not_replay_by_default(self) -> None:
        calls = []
        result = transition_runtime.run_provider_transition(
            self.spec(),
            event_path=self.event_path,
            state_reader=lambda: "audit_S1",
            provider_call=lambda: (
                calls.append(1) or [],
                "provider timed out before a durable transition",
            ),
        )

        self.assertFalse(result["completed"])
        self.assertEqual(result["mechanical_attempts"], 1)
        self.assertEqual(calls, [1])

    def test_nonmechanical_response_does_not_retry(self) -> None:
        calls = []
        result = transition_runtime.run_provider_transition(
            self.spec(),
            event_path=self.event_path,
            state_reader=lambda: "audit_S1",
            provider_call=lambda: (calls.append(1) or [], "Review is blocking."),
        )

        self.assertFalse(result["completed"])
        self.assertEqual(calls, [1])

    def test_explicit_contract_error_is_always_mechanical(self) -> None:
        self.assertTrue(
            transition_runtime.is_mechanical_failure(
                [],
                "mechanical_contract_error",
            )
        )

    def test_saved_artifact_is_reconciled_after_provider_error(self) -> None:
        state = {"value": "audit_S1"}

        def provider_call():
            state["value"] = "ready_for_editorial"
            raise RuntimeError("provider disconnected after the write")

        result = transition_runtime.run_provider_transition(
            self.spec(),
            event_path=self.event_path,
            state_reader=lambda: state["value"],
            provider_call=provider_call,
        )

        self.assertTrue(result["completed"])
        self.assertEqual(result["after_state"], "ready_for_editorial")

    def test_contract_envelope_names_exact_tool_and_template(self) -> None:
        contract = transition_runtime.contract_envelope(
            transition_id="cycle-001:audit:S1:test",
            kind="whole_book_synthesis_audit",
            required_tool="submit_book_synthesis_audit",
            expected_resulting_states=["ready_for_editorial"],
            submission_template={"audit": {"book_id": "book-test"}},
        )

        self.assertEqual(contract["contract_version"], "transition-contract.v1")
        self.assertEqual(contract["required_tool"], "submit_book_synthesis_audit")
        self.assertFalse(contract["mechanical_errors_consume_semantic_attempt"])


if __name__ == "__main__":
    unittest.main()
