import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import backend_selector
import conversation_store
import workflow_service


class WorkflowServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.conversations_path = self.project_path / "data" / "conversations"
        self.reviews_path = self.project_path / "data" / "reviews"
        self.review_sessions_path = self.project_path / "data" / "review_sessions"
        self.decisions_path = self.project_path / "data" / "review_decisions"
        self.books_path = self.project_path / "data" / "books"
        self.knowledge_path = self.project_path / "data" / "knowledge"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def patches(self) -> tuple:
        return (
            patch.object(backend_selector, "PROJECT_PATH", self.project_path),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
            patch.object(backend_selector, "REVIEWS_PATH", self.reviews_path),
            patch.object(
                backend_selector,
                "REVIEW_SESSIONS_PATH",
                self.review_sessions_path,
            ),
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.decisions_path,
            ),
            patch.object(backend_selector, "BOOKS_PATH", self.books_path),
            patch.object(
                backend_selector,
                "KNOWLEDGE_PATH",
                self.knowledge_path,
            ),
        )

    def active_patches(self) -> ExitStack:
        stack = ExitStack()
        for active_patch in self.patches():
            stack.enter_context(active_patch)
        stack.enter_context(
            patch("builtins.input", side_effect=AssertionError("input() called"))
        )
        return stack

    def create(self) -> dict:
        return workflow_service.create_conversation("codex", "safe")

    def write_ready_review(self, conversation_id: str) -> None:
        signature = {
            field_name: [
                {
                    "text": f"Recorded {field_name}",
                    "transcript_references": ["T1"],
                }
            ]
            for field_name, _, _ in backend_selector.CURIOSITY_SIGNATURE_FIELDS
        }
        document = {
            "review_id": "review-1",
            "conversation_id": conversation_id,
            "review": {
                "core_question": {"text": "What should the book establish?"},
                "research_route": "ready_for_book_consideration",
                "curiosity_signature": signature,
            },
        }
        self.reviews_path.mkdir(parents=True, exist_ok=True)
        (self.reviews_path / f"{conversation_id}.json").write_text(
            json.dumps(document) + "\n",
            encoding="utf-8",
        )
        (self.reviews_path / f"{conversation_id}.md").write_text(
            "# Review\n",
            encoding="utf-8",
        )

    def test_create_binds_provider_and_returns_structured_active_hold(self) -> None:
        with self.active_patches():
            created = self.create()
            events = conversation_store.load_events(
                Path(created["conversation_path"])
            )

        self.assertEqual(created["schema_version"], "1.0")
        self.assertEqual(created["provider"], "codex")
        self.assertEqual(created["stage"], "conversation_active")
        self.assertEqual(
            created["pending_action"]["commands"],
            ["submit_turn", "finish_conversation"],
        )
        self.assertEqual(
            [event["type"] for event in events],
            ["conversation_started", "backend_selected"],
        )

    def test_submit_turn_reuses_bound_provider_and_saves_one_turn(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "find_backend_program",
            return_value="/usr/bin/codex",
        ), patch.object(
            backend_selector,
            "load_skill_instructions",
            return_value="Librarian instructions",
        ), patch.object(
            backend_selector,
            "capture_backend_response",
            return_value=([{"type": "complete"}], "Grounded answer"),
        ) as capture:
            created = self.create()
            result = workflow_service.submit_conversation_turn(
                created["conversation_id"],
                "What should we investigate?",
            )
            messages = conversation_store.visible_messages(
                Path(created["conversation_path"])
            )

        self.assertEqual(result["status"], "turn_completed")
        self.assertEqual(result["provider"], "codex")
        self.assertEqual(result["assistant_response"], "Grounded answer")
        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant"],
        )
        self.assertEqual(capture.call_args.args[0], "codex")
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(
            [message["role"] for message in result["workflow"]["messages"]],
            ["user", "assistant"],
        )
        self.assertEqual(
            result["workflow"]["opening_question"],
            "What should we investigate?",
        )

    def test_provider_failure_is_durable_and_not_retried(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "find_backend_program",
            return_value="/usr/bin/codex",
        ), patch.object(
            backend_selector,
            "load_skill_instructions",
            return_value="Librarian instructions",
        ), patch.object(
            backend_selector,
            "capture_backend_response",
            side_effect=RuntimeError("provider failed"),
        ) as capture:
            created = self.create()
            with self.assertRaises(workflow_service.WorkflowOperationError) as caught:
                workflow_service.submit_conversation_turn(
                    created["conversation_id"],
                    "Preserve this failed turn.",
                )
            events = conversation_store.load_events(
                Path(created["conversation_path"])
            )

        self.assertEqual(caught.exception.code, "backend_call_failed")
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(events[-1]["type"], "backend_error")
        self.assertEqual(events[-1]["payload"]["agent_host"], "codex")

    def test_finish_enters_existing_review_stage_and_is_resumable(self) -> None:
        with self.active_patches():
            created = self.create()
            finished = workflow_service.finish_conversation(
                created["conversation_id"]
            )
            resumable = workflow_service.list_resumable_workflows()

        self.assertTrue(finished["finished"])
        self.assertEqual(finished["stage"], "research_review")
        self.assertEqual(
            finished["pending_action"]["kind"],
            "backend_transition",
        )
        self.assertEqual(resumable["workflow_count"], 1)
        self.assertEqual(
            resumable["workflows"][0]["conversation_id"],
            created["conversation_id"],
        )

    def test_review_checkpoint_is_exposed_as_automatic_transition(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.reviews_path.mkdir(parents=True)
            (self.reviews_path / f"{conversation_id}.json").write_text(
                '{"review_id":"review-1","conversation_id":"'
                + conversation_id
                + '","review":{}}\n',
                encoding="utf-8",
            )
            (self.reviews_path / f"{conversation_id}.md").write_text(
                "# Review\n",
                encoding="utf-8",
            )
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(inspected["stage"], "review_checkpoint")
        self.assertEqual(
            inspected["pending_action"]["kind"],
            "backend_transition",
        )
        self.assertTrue(
            inspected["pending_action"]["command_available"]
        )
        self.assertEqual(
            inspected["pending_action"]["command"],
            "advance_workflow",
        )

    def test_checkpoint_projection_exposes_context_without_browser_inference(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.write_ready_review(conversation_id)
            inspected = workflow_service.inspect_workflow(conversation_id)

        action = inspected["pending_action"]
        self.assertEqual(action["kind"], "backend_transition")
        self.assertEqual(
            action["context"]["core_question"],
            "What should the book establish?",
        )
        self.assertTrue(action["context"]["book_planning_eligible"])
        self.assertEqual(
            action["context"]["decision_mode"],
            "automatic",
        )
        self.assertEqual(
            action["context"]["workflow_policy_version"],
            "source-permissions-only-v1",
        )
        self.assertNotIn("allowed_decisions", action["context"])

    def test_workflow_list_is_compact_but_keeps_opening_question(self) -> None:
        with self.active_patches():
            created = self.create()
            backend_selector.append_conversation_message(
                Path(created["conversation_path"]),
                "user",
                "codex",
                "A durable opening question",
            )
            listed = workflow_service.list_resumable_workflows()

        workflow = listed["workflows"][0]
        self.assertEqual(
            workflow["opening_question"],
            "A durable opening question",
        )
        self.assertEqual(workflow["message_count"], 1)
        self.assertNotIn("messages", workflow)

    def test_finished_conversation_rejects_turn_without_provider_call(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "capture_backend_response",
        ) as capture:
            created = self.create()
            workflow_service.finish_conversation(created["conversation_id"])
            with self.assertRaises(workflow_service.WorkflowConflictError) as caught:
                workflow_service.submit_conversation_turn(
                    created["conversation_id"],
                    "This must not run.",
                )

        self.assertEqual(caught.exception.code, "conversation_finished")
        capture.assert_not_called()

    def test_stale_repeated_turn_cannot_repeat_provider_call(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "find_backend_program",
            return_value="/usr/bin/codex",
        ), patch.object(
            backend_selector,
            "load_skill_instructions",
            return_value="Librarian instructions",
        ), patch.object(
            backend_selector,
            "capture_backend_response",
            return_value=([{"type": "complete"}], "One answer"),
        ) as capture:
            created = self.create()
            expected_revision = created["revision"]
            workflow_service.submit_conversation_turn(
                created["conversation_id"],
                "Run this exactly once.",
                expected_stage="conversation_active",
                expected_revision=expected_revision,
            )
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as caught:
                workflow_service.submit_conversation_turn(
                    created["conversation_id"],
                    "Run this exactly once.",
                    expected_stage="conversation_active",
                    expected_revision=expected_revision,
                )

        self.assertEqual(caught.exception.code, "stale_workflow_revision")
        self.assertEqual(capture.call_count, 1)

    def test_review_decision_uses_canonical_writer_and_rejects_replay(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.write_ready_review(conversation_id)
            current = workflow_service.inspect_workflow(
                conversation_id
            )
            recorded = workflow_service.submit_checkpoint_decision(
                conversation_id,
                {
                    "kind": "research_review",
                    "decision": "approved_for_book_planning",
                    "curiosity_signature_decision": "accepted",
                },
                expected_stage=current["stage"],
                expected_revision=current["revision"],
            )
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as replay_error:
                workflow_service.submit_checkpoint_decision(
                    conversation_id,
                    {
                        "kind": "research_review",
                        "decision": "approved_for_book_planning",
                        "curiosity_signature_decision": "accepted",
                    },
                    expected_stage=current["stage"],
                    expected_revision=current["revision"],
                )
            saved = json.loads(
                (self.decisions_path / f"{conversation_id}.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(recorded["status"], "checkpoint_recorded")
        self.assertEqual(saved["decision"], "approved_for_book_planning")
        self.assertEqual(saved["book_planning_gate"], "ready")
        self.assertEqual(
            replay_error.exception.code,
            "stale_workflow_stage",
        )

    def test_review_decision_can_be_recorded_without_input(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.write_ready_review(conversation_id)
            result = backend_selector.record_automatic_review_decision(
                self.reviews_path / f"{conversation_id}.json"
            )

        saved = json.loads(result.read_text(encoding="utf-8"))
        self.assertEqual(saved["decision"], "approved_for_book_planning")
        self.assertEqual(
            saved["decision_authority"],
            "automated_audit_policy",
        )
        self.assertEqual(
            saved["workflow_policy_version"],
            "source-permissions-only-v1",
        )

    def test_advance_delegates_once_to_noninteractive_controller(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "find_backend_program",
            return_value="/usr/bin/codex",
        ), patch.object(
            backend_selector,
            "continue_book_workflow",
            return_value={
                "status": "awaiting_review_checkpoint",
                "checkpoint_kind": "research_review",
            },
        ) as controller:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            current = workflow_service.inspect_workflow(conversation_id)
            result = workflow_service.advance_workflow(
                conversation_id,
                expected_stage=current["stage"],
                expected_revision=current["revision"],
            )

        self.assertEqual(result["status"], "workflow_advanced")
        self.assertEqual(
            result["result"]["status"],
            "awaiting_review_checkpoint",
        )
        self.assertFalse(controller.call_args.kwargs["interactive_checkpoints"])
        self.assertEqual(controller.call_count, 1)

    def test_source_decisions_require_exact_complete_coverage(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            session = backend_selector.load_review_session(conversation_id)
            session["pending_requests"] = [
                {
                    "request_id": "R1",
                    "claim_under_review": "Claim one",
                    "domain": "example.com",
                    "url": "https://example.com/one",
                    "reason_for_source": "Reason one",
                },
                {
                    "request_id": "R2",
                    "claim_under_review": "Claim two",
                    "domain": "example.org",
                    "url": "https://example.org/two",
                    "reason_for_source": "Reason two",
                },
            ]
            backend_selector.save_review_session(conversation_id, session)
            current = workflow_service.inspect_workflow(conversation_id)
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as incomplete:
                workflow_service.submit_checkpoint_decision(
                    conversation_id,
                    {
                        "kind": "research_source_permissions",
                        "decisions": [
                            {"request_id": "R1", "decision": "reject"}
                        ],
                    },
                    expected_stage=current["stage"],
                    expected_revision=current["revision"],
                )
            unchanged = backend_selector.load_review_session(conversation_id)

        self.assertEqual(incomplete.exception.code, "checkpoint_rejected")
        self.assertEqual(len(unchanged["pending_requests"]), 2)
        self.assertEqual(unchanged["decisions"], [])

    def test_pending_source_hold_cannot_repeat_provider_call(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "capture_backend_response",
        ) as capture:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            session = backend_selector.load_review_session(conversation_id)
            session["pending_requests"] = [
                {
                    "request_id": "R1",
                    "claim_under_review": "One claim",
                    "domain": "example.com",
                    "url": "https://example.com/source",
                    "reason_for_source": "One reason",
                }
            ]
            backend_selector.save_review_session(conversation_id, session)
            result = backend_selector.continue_book_workflow(
                "codex",
                "/usr/bin/codex",
                Path(created["conversation_path"]),
                interactive_checkpoints=False,
            )

        self.assertEqual(result["status"], "source_approval_required")
        self.assertEqual(
            result["checkpoint_kind"],
            "research_source_permissions",
        )
        capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
