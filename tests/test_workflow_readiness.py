import unittest
from unittest.mock import patch

import workflow_service


class WorkflowReadinessTests(unittest.TestCase):
    def test_guarded_finish_reports_exact_automation_boundary(self) -> None:
        readiness = workflow_service._finish_readiness(
            stage="conversation_active",
            pending_action={"kind": "conversation_input"},
            research_mode="safe",
            question_mode="manual",
            finish_policy=None,
            message_count=4,
        )
        self.assertEqual(readiness["status"], "ready")
        self.assertTrue(readiness["finish_available"])
        self.assertIn(
            "inquiry-scoped exact-URL source approvals",
            readiness["will_continue_automatically"],
        )
        self.assertIn("new authorship claims", readiness["will_pause_for"])

    def test_nondelegable_checkpoint_is_visible_as_a_human_decision(self) -> None:
        readiness = workflow_service._finish_readiness(
            stage="chapter_development",
            pending_action={
                "kind": "human_checkpoint",
                "decision_kind": "chapter_human_input",
                "context": {},
            },
            research_mode="auto_approve",
            question_mode="manual",
            finish_policy={"policy_version": "finish-with-guardrails-v1"},
            message_count=6,
        )
        self.assertEqual(readiness["status"], "decision_required")
        human_check = next(
            check for check in readiness["checks"] if check["id"] == "human_authority"
        )
        self.assertEqual(human_check["status"], "attention")

    def test_auto_defer_makes_only_chapter_questions_automatic(self) -> None:
        readiness = workflow_service._finish_readiness(
            stage="chapter_development",
            pending_action={
                "kind": "human_checkpoint",
                "decision_kind": "chapter_human_input",
                "context": {},
            },
            research_mode="auto_approve",
            question_mode="auto_defer",
            finish_policy={"policy_version": "finish-with-guardrails-v1"},
            message_count=6,
        )
        self.assertEqual(readiness["status"], "advancing")
        self.assertIn(
            "explicit deferral of new chapter-framing questions",
            readiness["will_continue_automatically"],
        )

    def test_timeline_keeps_failures_retries_artifacts_and_current_state(self) -> None:
        with patch(
            "workflow_service.transition_events",
            return_value={
                "events": [
                    {
                        "kind": "chapter_research",
                        "phase": "provider_started",
                    },
                    {
                        "kind": "chapter_research",
                        "phase": "validation_failed",
                        "recorded_at": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "kind": "chapter_research",
                        "phase": "mechanical_retry_scheduled",
                        "mechanical_attempt": 2,
                    },
                    {
                        "kind": "chapter_research",
                        "phase": "durable_artifact_saved",
                    },
                ]
            },
        ):
            timeline = workflow_service._workflow_timeline(
                "20260101T000000000000Z-abcdef12",
                "chapter_development",
            )
        self.assertEqual(
            [item["status"] for item in timeline],
            ["attention", "retry", "complete", "current"],
        )


if __name__ == "__main__":
    unittest.main()
