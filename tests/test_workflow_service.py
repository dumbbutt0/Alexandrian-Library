import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

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
            patch.object(
                backend_selector,
                "find_backend_program",
                return_value="/usr/bin/codex",
            )
        )
        stack.enter_context(
            patch("builtins.input", side_effect=AssertionError("input() called"))
        )
        return stack

    def create(self) -> dict:
        return workflow_service.create_conversation("codex", "safe")

    def test_experience_projection_covers_every_canonical_stage(self) -> None:
        for stage in workflow_service.WORKFLOW_STAGE_PROGRESS:
            projection = workflow_service.workflow_experience_projection(
                stage,
                workflow_service._pending_action(stage),
                book_creation_active=stage not in {
                    "conversation_active",
                    "complete",
                    "workflow_error",
                },
            )
            with self.subTest(stage=stage):
                self.assertEqual(
                    projection["schema_version"],
                    "workflow-experience.v1",
                )
                self.assertTrue(projection["headline"])
                self.assertTrue(projection["detail"])
                self.assertTrue(projection["technical_detail_available"])

    def test_experience_projection_distinguishes_source_and_authorship_holds(
        self,
    ) -> None:
        source = workflow_service.workflow_experience_projection(
            "research_review",
            {
                "kind": "human_checkpoint",
                "decision_kind": "research_source_permissions",
            },
            book_creation_active=True,
        )
        authorship = workflow_service.workflow_experience_projection(
            "chapter_development",
            {
                "kind": "human_checkpoint",
                "decision_kind": "chapter_human_input",
            },
            book_creation_active=True,
        )

        self.assertEqual(source["state"], "needs_source_decision")
        self.assertEqual(source["primary_action"], "review_source")
        self.assertEqual(authorship["state"], "needs_human_judgment")
        self.assertEqual(authorship["primary_action"], "record_decision")

    def test_inspected_workflow_includes_user_facing_experience(self) -> None:
        with self.active_patches():
            workflow = self.create()

        self.assertEqual(workflow["experience"]["state"], "conversation")
        self.assertEqual(
            workflow["experience"]["primary_action"],
            "continue_inquiry",
        )

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

    def enter_chapter_stage(
        self,
        conversation_id: str,
        *,
        status: str,
        assessment_id: str | None = None,
        human_input_requests: list[dict] | None = None,
        stop_reason: str | None = None,
    ) -> tuple[str, dict]:
        """Create the smallest canonical, restartable chapter workflow."""
        self.write_ready_review(conversation_id)
        review_id = "review-1"
        self.decisions_path.mkdir(parents=True, exist_ok=True)
        (self.decisions_path / f"{conversation_id}.json").write_text(
            json.dumps(
                {
                    "review_id": review_id,
                    "conversation_id": conversation_id,
                    "book_planning_gate": "ready",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (self.knowledge_path / "nodes" / review_id).mkdir(
            parents=True,
            exist_ok=True,
        )
        book_id = f"book-{conversation_id}"
        book_directory = self.books_path / book_id
        book_directory.mkdir(parents=True, exist_ok=True)
        (book_directory / "manifest.json").write_text(
            json.dumps(
                {
                    "book_id": book_id,
                    "chapter_plan": [
                        {
                            "chapter_id": "CH1",
                            "title": "A bounded chapter",
                            "readiness": "ready_to_draft",
                            "material_disposition": "include",
                            "dependencies": [],
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (book_directory / "outline.md").write_text(
            "# Outline\n",
            encoding="utf-8",
        )
        (book_directory / "approval.json").write_text(
            json.dumps(
                {
                    "book_id": book_id,
                    "chapter_drafting_gate": "ready",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        run = backend_selector.load_or_create_chapter_run(book_id, "CH1")
        run.update(
            {
                "status": status,
                "last_assessment_id": assessment_id,
                "human_input_requests": human_input_requests or [],
                "stop_reason": stop_reason,
            }
        )
        backend_selector.save_chapter_run(run)
        return book_id, run

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
        self.assertEqual(created["overall_progress"], 1)
        self.assertEqual(created["progress"]["scale_min"], 1)
        self.assertEqual(created["progress"]["scale_max"], 100)
        self.assertEqual(created["progress"]["basis"], "durable_stage")
        self.assertEqual(created["review_state"]["status"], "not_ready")
        self.assertFalse(created["book_creation"]["active"])
        self.assertEqual(
            created["lineage_root_conversation_id"],
            created["conversation_id"],
        )

    def test_workflow_stage_progress_distinguishes_overall_milestones(self) -> None:
        self.assertEqual(workflow_service.workflow_overall_progress("conversation_active"), 1)
        self.assertEqual(workflow_service.workflow_overall_progress("synthesis_audit"), 86)
        self.assertEqual(workflow_service.workflow_overall_progress("complete"), 100)
        self.assertGreaterEqual(
            workflow_service.workflow_overall_progress("synthesis_correction"),
            workflow_service.workflow_overall_progress("synthesis_audit"),
        )
        self.assertGreaterEqual(
            workflow_service.workflow_overall_progress("editorial_revision"),
            workflow_service.workflow_overall_progress("editorial_checkpoint"),
        )

    def test_e2e_receipt_records_provider_switches_runtime_and_review_state(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            switched = workflow_service.switch_conversation_provider(
                conversation_id,
                "claude",
                expected_stage=created["stage"],
                expected_revision=created["revision"],
            )
            workflow_service.finish_conversation(conversation_id)
            session = backend_selector.load_review_session(conversation_id)
            session["decisions"] = [{
                "request_id": "source-1",
                "domain": "example.org",
                "url": "https://example.org/source",
                "decision": "allow_once",
                "decided_at": "2026-01-01T00:00:00+00:00",
            }]
            backend_selector.save_review_session(conversation_id, session)
            book_id = f"book-{conversation_id}"
            book_directory = self.books_path / book_id
            book_directory.mkdir(parents=True, exist_ok=True)
            book_path = book_directory / "book.md"
            book_path.write_text("# Verified book\n", encoding="utf-8")
            assembly_path = book_directory / "assembly.json"
            assembly_path.write_text(
                json.dumps({
                    "book_id": book_id,
                    "lifecycle_status": "final",
                    "book_path": str(book_path.relative_to(self.project_path)),
                    "book_sha256": backend_selector.file_sha256(book_path),
                    "finalization_authority": "automated_audit_policy",
                    "workflow_policy_version": "source-permissions-only-v1",
                    "synthesis_receipt_path": "receipt.json",
                    "synthesis_receipt_file_sha256": "a" * 64,
                    "synthesis_audit_sha256": "b" * 64,
                }) + "\n",
                encoding="utf-8",
            )
            workflow_service.append_runtime_event(conversation_id, {
                "runtime_session_id": "runtime-one",
                "build_sha256": "c" * 64,
                "process_id": 10,
                "server_started_at": 1.0,
                "event": "job_finished",
                "job_id": "job-one",
                "kind": "background_book",
                "provider": "claude",
                "status": "completed",
                "elapsed_seconds": 12.5,
                "recorded_at": "2026-01-01T00:00:12+00:00",
            })
            receipt = workflow_service.write_e2e_run_receipt(conversation_id)

        self.assertEqual(switched["provider"], "claude")
        self.assertEqual(receipt["provider_sequence"], ["codex", "claude"])
        self.assertEqual(receipt["provider_switch_count"], 1)
        self.assertEqual(receipt["source_decision_counts"]["allow_once"], 1)
        self.assertEqual(receipt["review_state"]["status"], "generated_for_review")
        self.assertEqual(receipt["timings"]["background_runtime_seconds"], 12.5)
        self.assertTrue((book_directory / "run_receipt.json").is_file())

    def test_create_rejects_unavailable_provider_before_writing(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "find_backend_program",
            return_value=None,
        ):
            with self.assertRaises(
                workflow_service.WorkflowOperationError
            ) as caught:
                workflow_service.create_conversation("codex", "safe")

        self.assertEqual(caught.exception.code, "provider_unavailable")
        self.assertEqual(list(self.conversations_path.glob("*.jsonl")), [])

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

    def test_provider_switch_preserves_shared_context_for_the_next_turn(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "load_skill_instructions",
            return_value="Librarian instructions",
        ), patch.object(
            backend_selector,
            "capture_backend_response",
            side_effect=[
                ([{"type": "complete"}], "Codex answer"),
                ([{"type": "complete"}], "Claude answer"),
            ],
        ) as capture:
            created = self.create()
            first = workflow_service.submit_conversation_turn(
                created["conversation_id"],
                "Remember this premise.",
            )
            switched = workflow_service.switch_conversation_provider(
                created["conversation_id"],
                "claude",
                expected_stage=first["workflow"]["stage"],
                expected_revision=first["workflow"]["revision"],
            )
            second = workflow_service.submit_conversation_turn(
                created["conversation_id"],
                "Now extend it.",
                expected_stage=switched["stage"],
                expected_revision=switched["revision"],
            )

        self.assertEqual(switched["provider"], "claude")
        self.assertEqual(second["provider"], "claude")
        self.assertEqual(
            [message["content"] for message in second["workflow"]["messages"]],
            [
                "Remember this premise.",
                "Codex answer",
                "Now extend it.",
                "Claude answer",
            ],
        )
        self.assertEqual(capture.call_args_list[0].args[0], "codex")
        self.assertEqual(capture.call_args_list[1].args[0], "claude")
        self.assertIn("Remember this premise.", capture.call_args_list[1].args[2])
        self.assertIn("Codex answer", capture.call_args_list[1].args[2])

    def test_provider_switch_is_rejected_after_finish(self) -> None:
        with self.active_patches():
            created = self.create()
            finished = workflow_service.finish_conversation(
                created["conversation_id"]
            )
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as caught:
                workflow_service.switch_conversation_provider(
                    created["conversation_id"],
                    "claude",
                    expected_stage=finished["stage"],
                    expected_revision=finished["revision"],
                )

        self.assertEqual(caught.exception.code, "provider_switch_not_allowed")

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
        self.assertTrue(finished["book_creation"]["active"])
        self.assertEqual(
            finished["book_creation"]["label"],
            "Book generation in progress",
        )

    def test_guarded_finish_records_durable_inquiry_policy(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            finished = workflow_service.finish_conversation(
                conversation_id,
                finish_mode="guardrails",
                expected_stage=created["stage"],
                expected_revision=created["revision"],
            )
            policy = workflow_service.load_finish_guardrails_policy(
                conversation_id
            )
            session = backend_selector.load_review_session(conversation_id)

        self.assertTrue(finished["finish_policy"]["enabled"])
        self.assertEqual(finished["research_mode"], "auto_approve")
        self.assertEqual(session["research_mode"], "auto_approve")
        self.assertEqual(policy["scope"], "inquiry")
        self.assertEqual(
            policy["source_authority"],
            "exact_url_allow_once",
        )
        self.assertIn(
            "final_pass_repair",
            policy["automatic_checkpoint_kinds"],
        )
        self.assertIn(
            "chapter_human_input",
            policy["nondelegable_checkpoint_kinds"],
        )
        self.assertEqual(
            policy["finish_policy_sha256"],
            backend_selector.canonical_json_sha256(
                policy,
                "finish_policy_sha256",
            ),
        )

    def test_source_mode_toggle_resolves_waiting_url_and_is_reversible(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            session = backend_selector.load_review_session(conversation_id)
            session["decisions"].append(
                {
                    "request_id": "source-request-earlier",
                    "decision": "reject",
                }
            )
            session["pending_requests"].append(
                {
                    "request_id": "source-request-waiting",
                    "url": "https://example.com/evidence",
                    "domain": "example.com",
                    "claim_under_review": "A bounded claim",
                    "reason_for_source": "Check the primary source.",
                    "requested_at": "2026-08-04T00:00:00Z",
                }
            )
            backend_selector.save_review_session(conversation_id, session)
            waiting = workflow_service.inspect_workflow(conversation_id)

            enabled = workflow_service.set_workflow_research_mode(
                conversation_id,
                "auto_approve",
                expected_stage=waiting["stage"],
                expected_revision=waiting["revision"],
            )
            enabled_session = backend_selector.load_review_session(
                conversation_id
            )

            self.assertEqual(enabled["pending_sources_resolved"], 1)
            self.assertEqual(enabled["workflow"]["research_mode"], "auto_approve")
            self.assertEqual(enabled_session["pending_requests"], [])
            self.assertEqual(len(enabled_session["decisions"]), 2)
            self.assertEqual(
                enabled_session["decisions"][1]["decision"],
                "allow_once",
            )
            self.assertEqual(
                enabled_session["decisions"][1]["authorized_by"],
                "auto_approve_mode",
            )

            disabled = workflow_service.set_workflow_research_mode(
                conversation_id,
                "safe",
                expected_stage=enabled["workflow"]["stage"],
                expected_revision=enabled["workflow"]["revision"],
            )
            self.assertEqual(disabled["workflow"]["research_mode"], "safe")
            self.assertEqual(
                len(
                    backend_selector.load_review_session(conversation_id)[
                        "decisions"
                    ]
                ),
                2,
            )

            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as stale:
                workflow_service.set_workflow_research_mode(
                    conversation_id,
                    "auto_approve",
                    expected_stage=waiting["stage"],
                    expected_revision=waiting["revision"],
                )
            self.assertEqual(stale.exception.code, "stale_workflow_revision")

    def test_question_mode_defers_waiting_questions_and_is_reversible(self) -> None:
        questions = [
            "Which interpretation should remain human-owned?",
            "Which scope should the chapter use?",
        ]
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, run = self.enter_chapter_stage(
                conversation_id,
                status="human_input_required",
                assessment_id="A2",
                human_input_requests=questions,
            )
            run["last_assessment_sha256"] = "b" * 64
            backend_selector.save_chapter_run(run)
            waiting = workflow_service.inspect_workflow(conversation_id)

            enabled = workflow_service.set_workflow_question_mode(
                conversation_id,
                "auto_defer",
                expected_stage=waiting["stage"],
                expected_revision=waiting["revision"],
            )
            session = backend_selector.load_review_session(conversation_id)
            human_input_record = backend_selector.load_chapter_human_input(
                book_id,
                "CH1",
                "A2",
            )
            self.assertIsNotNone(human_input_record)
            _, human_input = human_input_record

            self.assertEqual(enabled["pending_questions_resolved"], 2)
            self.assertEqual(enabled["workflow"]["question_mode"], "auto_defer")
            self.assertEqual(
                enabled["workflow"]["pending_action"]["kind"],
                "backend_transition",
            )
            self.assertEqual(
                [item["question"] for item in human_input["decisions"]],
                questions,
            )
            self.assertTrue(
                all(
                    item["response"]
                    == workflow_service.AUTO_DEFER_QUESTION_RESPONSE
                    for item in human_input["decisions"]
                )
            )
            self.assertEqual(
                session["question_mode_changes"][-1]["authorized_by"],
                "human_workflow_ui",
            )

            disabled = workflow_service.set_workflow_question_mode(
                conversation_id,
                "manual",
                expected_stage=enabled["workflow"]["stage"],
                expected_revision=enabled["workflow"]["revision"],
            )
            self.assertEqual(disabled["workflow"]["question_mode"], "manual")
            self.assertIsNotNone(
                backend_selector.load_chapter_human_input(
                    book_id,
                    "CH1",
                    "A2",
                )
            )

            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as stale:
                workflow_service.set_workflow_question_mode(
                    conversation_id,
                    "auto_defer",
                    expected_stage=waiting["stage"],
                    expected_revision=waiting["revision"],
                )
            self.assertEqual(stale.exception.code, "stale_workflow_revision")

    def test_finish_and_advance_runs_the_first_transition_automatically(self) -> None:
        progress = []
        advanced_workflow = {
            "schema_version": "1.0",
            "conversation_id": "conversation-test",
            "stage": "review_checkpoint",
        }
        with self.active_patches(), patch.object(
            workflow_service,
            "advance_workflow",
            return_value={"workflow": advanced_workflow},
        ) as advance:
            created = self.create()
            result = workflow_service.finish_and_advance_workflow(
                created["conversation_id"],
                expected_stage=created["stage"],
                expected_revision=created["revision"],
                progress=lambda percent, phase: progress.append((percent, phase)),
            )

        self.assertEqual(result, advanced_workflow)
        advance.assert_called_once()
        self.assertEqual(advance.call_args.kwargs["expected_stage"], "research_review")
        self.assertEqual(progress[0], (8, "Sealing the conversation"))
        self.assertIn((18, "Independent review started"), progress)

    def test_finish_and_advance_retries_excerpt_correction_in_one_command(
        self,
    ) -> None:
        prompts = []

        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]

            def capture_side_effect(
                _backend: str,
                _program_path: str,
                prompt: str,
                _call_purpose: str,
            ) -> tuple[list[dict], str]:
                prompts.append(prompt)
                if len(prompts) == 1:
                    return (
                        [],
                        "The review was not saved: validation rejected the "
                        "Android evidence excerpt. A wrapper-provided "
                        "validation-correction pass is required before "
                        "resubmission.",
                    )
                self.write_ready_review(conversation_id)
                review_path = self.reviews_path / f"{conversation_id}.json"
                review = json.loads(review_path.read_text(encoding="utf-8"))
                review["review"]["research_route"] = "research_seed"
                review_path.write_text(
                    json.dumps(review) + "\n",
                    encoding="utf-8",
                )
                return [], "Corrected review submitted."

            with patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=capture_side_effect,
            ):
                result = workflow_service.finish_and_advance_workflow(
                    conversation_id,
                    expected_stage=created["stage"],
                    expected_revision=created["revision"],
                )

        self.assertEqual(len(prompts), 2)
        self.assertIn('"code": "exact_excerpt_required"', prompts[1])
        self.assertEqual(result["stage"], "conversation_active")
        self.assertEqual(
            result["lineage"]["parent_conversation_id"],
            conversation_id,
        )

    def test_guarded_finish_applies_bounded_repair_and_keeps_advancing(
        self,
    ) -> None:
        policy = {
            "policy_version": "finish-with-guardrails-v1",
            "finish_policy_sha256": "p" * 64,
            "automatic_checkpoint_kinds": list(
                workflow_service.GUARDRAIL_AUTOMATIC_CHECKPOINTS
            ),
        }
        initial = {
            "conversation_id": "conversation-test",
            "stage": "research_review",
            "stage_label": "research review",
            "revision": "r2",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        repair = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "stage_label": "chapter development",
            "revision": "r3",
            "pending_action": {
                "kind": "human_checkpoint",
                "decision_kind": "final_pass_repair",
                "command_available": True,
                "context": {
                    "book_id": "book-conversation-test",
                    "chapter_id": "CH2",
                },
            },
        }
        resumed = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "stage_label": "chapter development",
            "revision": "r4",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        complete = {
            "conversation_id": "conversation-test",
            "stage": "complete",
            "stage_label": "complete",
            "revision": "r5",
            "pending_action": {"kind": "complete"},
        }
        with patch.object(
            workflow_service,
            "finish_conversation",
            return_value=initial,
        ) as finish, patch.object(
            workflow_service,
            "load_finish_guardrails_policy",
            return_value=policy,
        ), patch.object(
            workflow_service,
            "advance_workflow",
            side_effect=[{"workflow": repair}, {"workflow": complete}],
        ) as advance, patch.object(
            workflow_service,
            "submit_checkpoint_decision",
            return_value={"workflow": resumed},
        ) as submit:
            result = workflow_service.finish_and_advance_workflow(
                "conversation-test",
                finish_mode="guardrails",
                expected_stage="conversation_active",
                expected_revision="r1",
            )

        self.assertEqual(result, complete)
        self.assertEqual(advance.call_count, 2)
        finish.assert_called_once_with(
            "conversation-test",
            None,
            finish_mode="guardrails",
            expected_stage="conversation_active",
            expected_revision="r1",
        )
        decision = submit.call_args.args[1]
        self.assertEqual(decision["kind"], "final_pass_repair")
        self.assertIn("p" * 64, decision["approval_note"])
        self.assertIn("does not authorize new authorship", decision["approval_note"])

    def test_guarded_finish_runs_one_bounded_research_continuation(
        self,
    ) -> None:
        policy = {
            "policy_version": "finish-with-guardrails-v1",
            "finish_policy_sha256": "p" * 64,
            "automatic_checkpoint_kinds": list(
                workflow_service.GUARDRAIL_AUTOMATIC_CHECKPOINTS
            ),
        }
        root_finished = {
            "conversation_id": "conversation-root",
            "stage": "research_review",
            "stage_label": "research review",
            "revision": "r2",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        child_active = {
            "conversation_id": "conversation-child",
            "stage": "conversation_active",
            "stage_label": "active conversation",
            "revision": "c1",
            "research_mode": "auto_approve",
            "pending_action": {"kind": "conversation_turn"},
        }
        child_after_turn = {**child_active, "revision": "c2"}
        child_finished = {
            "conversation_id": "conversation-child",
            "stage": "research_review",
            "stage_label": "research review",
            "revision": "c3",
            "research_mode": "auto_approve",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        complete = {
            "conversation_id": "conversation-child",
            "stage": "complete",
            "stage_label": "complete",
            "revision": "c4",
            "pending_action": {"kind": "complete"},
        }
        with patch.object(
            workflow_service,
            "finish_conversation",
            side_effect=[root_finished, child_finished],
        ) as finish, patch.object(
            workflow_service,
            "load_finish_guardrails_policy",
            return_value=policy,
        ), patch.object(
            workflow_service,
            "advance_workflow",
            side_effect=[
                {"workflow": child_active},
                {"workflow": complete},
            ],
        ) as advance, patch.object(
            workflow_service,
            "submit_conversation_turn",
            return_value={"workflow": child_after_turn},
        ) as submit:
            result = workflow_service.finish_and_advance_workflow(
                "conversation-root",
                finish_mode="guardrails",
                expected_stage="conversation_active",
                expected_revision="r1",
            )

        self.assertEqual(result, complete)
        self.assertEqual(advance.call_count, 2)
        submit.assert_called_once_with(
            "conversation-child",
            workflow_service.GUARDRAIL_RESEARCH_CONTINUATION_PROMPT,
            expected_stage="conversation_active",
            expected_revision="c1",
        )
        self.assertEqual(finish.call_count, 2)
        self.assertEqual(finish.call_args_list[1].args[0], "conversation-child")
        self.assertEqual(
            finish.call_args_list[1].kwargs["finish_mode"],
            "standard",
        )

    def test_guarded_finish_stops_before_a_second_research_continuation(
        self,
    ) -> None:
        policy = {
            "policy_version": "finish-with-guardrails-v1",
            "finish_policy_sha256": "p" * 64,
            "automatic_checkpoint_kinds": list(
                workflow_service.GUARDRAIL_AUTOMATIC_CHECKPOINTS
            ),
        }
        root_finished = {
            "conversation_id": "conversation-root",
            "stage": "research_review",
            "stage_label": "research review",
            "revision": "r2",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        first_child = {
            "conversation_id": "conversation-child-1",
            "stage": "conversation_active",
            "revision": "c1",
            "research_mode": "auto_approve",
            "pending_action": {"kind": "conversation_turn"},
        }
        first_child_finished = {
            "conversation_id": "conversation-child-1",
            "stage": "research_review",
            "stage_label": "research review",
            "revision": "c3",
            "research_mode": "auto_approve",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        second_child = {
            "conversation_id": "conversation-child-2",
            "stage": "conversation_active",
            "revision": "d1",
            "research_mode": "auto_approve",
            "pending_action": {"kind": "conversation_turn"},
        }
        with patch.object(
            workflow_service,
            "finish_conversation",
            side_effect=[root_finished, first_child_finished],
        ), patch.object(
            workflow_service,
            "load_finish_guardrails_policy",
            return_value=policy,
        ), patch.object(
            workflow_service,
            "advance_workflow",
            side_effect=[
                {"workflow": first_child},
                {"workflow": second_child},
            ],
        ), patch.object(
            workflow_service,
            "submit_conversation_turn",
            return_value={"workflow": {**first_child, "revision": "c2"}},
        ) as submit:
            result = workflow_service.finish_and_advance_workflow(
                "conversation-root",
                finish_mode="guardrails",
                expected_stage="conversation_active",
                expected_revision="r1",
            )

        self.assertEqual(result, second_child)
        self.assertEqual(submit.call_count, 1)

    def test_guarded_finish_stops_for_personal_authorship(self) -> None:
        policy = {
            "policy_version": "finish-with-guardrails-v1",
            "finish_policy_sha256": "p" * 64,
            "automatic_checkpoint_kinds": list(
                workflow_service.GUARDRAIL_AUTOMATIC_CHECKPOINTS
            ),
        }
        authorship = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "revision": "r2",
            "pending_action": {
                "kind": "human_checkpoint",
                "decision_kind": "chapter_human_input",
                "command_available": True,
                "context": {"questions": ["What do you mean?"]},
            },
        }
        with patch.object(
            workflow_service,
            "finish_conversation",
            return_value=authorship,
        ), patch.object(
            workflow_service,
            "load_finish_guardrails_policy",
            return_value=policy,
        ), patch.object(
            workflow_service,
            "advance_workflow",
        ) as advance, patch.object(
            workflow_service,
            "submit_checkpoint_decision",
        ) as submit:
            result = workflow_service.finish_and_advance_workflow(
                "conversation-test",
                finish_mode="guardrails",
                expected_stage="conversation_active",
                expected_revision="r1",
            )

        self.assertEqual(result, authorship)
        advance.assert_not_called()
        submit.assert_not_called()

    def test_guarded_finish_auto_defers_preauthorized_questions(self) -> None:
        policy = {
            "policy_version": "finish-with-guardrails-v1",
            "finish_policy_sha256": "p" * 64,
            "automatic_checkpoint_kinds": list(
                workflow_service.GUARDRAIL_AUTOMATIC_CHECKPOINTS
            ),
        }
        authorship = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "revision": "r2",
            "question_mode": "auto_defer",
            "pending_action": {
                "kind": "human_checkpoint",
                "decision_kind": "chapter_human_input",
                "context": {"questions": ["What do you mean?"]},
            },
        }
        resumed = {
            **authorship,
            "revision": "r3",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        complete = {
            "conversation_id": "conversation-test",
            "stage": "complete",
            "revision": "r4",
            "question_mode": "auto_defer",
            "pending_action": {"kind": "complete"},
        }
        with patch.object(
            workflow_service,
            "finish_conversation",
            return_value=authorship,
        ), patch.object(
            workflow_service,
            "load_finish_guardrails_policy",
            return_value=policy,
        ), patch.object(
            workflow_service,
            "_auto_defer_pending_questions",
            return_value=(resumed, 1),
        ) as defer, patch.object(
            workflow_service,
            "advance_workflow",
            return_value={"workflow": complete},
        ) as advance:
            result = workflow_service.finish_and_advance_workflow(
                "conversation-test",
                finish_mode="guardrails",
                expected_stage="conversation_active",
                expected_revision="r1",
            )

        self.assertEqual(result, complete)
        defer.assert_called_once_with(authorship)
        advance.assert_called_once()

    def test_guardrails_resume_after_a_later_human_decision(self) -> None:
        policy = {
            "policy_version": "finish-with-guardrails-v1",
            "finish_policy_sha256": "p" * 64,
            "automatic_checkpoint_kinds": list(
                workflow_service.GUARDRAIL_AUTOMATIC_CHECKPOINTS
            ),
        }
        closeout = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "revision": "r2",
            "pending_action": {
                "kind": "human_checkpoint",
                "decision_kind": "chapter_human_input_closeout",
                "command_available": True,
                "context": {
                    "book_id": "book-conversation-test",
                    "chapter_id": "CH2",
                },
            },
        }
        resumed = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "revision": "r3",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        complete = {
            "conversation_id": "conversation-test",
            "stage": "complete",
            "revision": "r4",
            "pending_action": {"kind": "complete"},
        }
        with patch.object(
            workflow_service,
            "_assert_expected_state",
            return_value=closeout,
        ), patch.object(
            workflow_service,
            "load_finish_guardrails_policy",
            return_value=policy,
        ), patch.object(
            workflow_service,
            "submit_checkpoint_decision",
            return_value={"workflow": resumed},
        ) as submit, patch.object(
            workflow_service,
            "advance_workflow",
            return_value={"workflow": complete},
        ):
            result = workflow_service.advance_workflow_with_guardrails(
                "conversation-test",
                expected_stage="chapter_development",
                expected_revision="r2",
            )

        self.assertEqual(result["workflow"], complete)
        self.assertEqual(
            submit.call_args.args[1]["kind"],
            "chapter_human_input_closeout",
        )

    def test_background_book_advances_automatic_stages_to_editorial_review(self) -> None:
        active = {
            "conversation_id": "conversation-test",
            "research_mode": "safe",
            "stage": "conversation_active",
            "stage_label": "active conversation",
            "revision": "r2",
        }
        automatic = {
            "conversation_id": "conversation-test",
            "stage": "book_plan_checkpoint",
            "stage_label": "book plan checkpoint",
            "revision": "r3",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        editorial = {
            "conversation_id": "conversation-test",
            "stage": "editorial_checkpoint",
            "stage_label": "editorial checkpoint",
            "revision": "r4",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        progress = []
        with patch.object(
            workflow_service,
            "submit_conversation_turn",
            return_value={"workflow": active},
        ) as opening, patch.object(
            workflow_service,
            "finish_and_advance_workflow",
            return_value=automatic,
        ) as finish, patch.object(
            workflow_service,
            "advance_workflow",
            return_value={"workflow": editorial},
        ) as advance:
            result = workflow_service.build_book_to_review(
                "conversation-test",
                "Why does this matter?",
                expected_stage="conversation_active",
                expected_revision="r1",
                progress=lambda percent, phase: progress.append((percent, phase)),
            )

        self.assertEqual(result, editorial)
        opening.assert_called_once()
        finish.assert_called_once()
        advance.assert_called_once_with(
            "conversation-test",
            expected_stage="book_plan_checkpoint",
            expected_revision="r3",
            progress=advance.call_args.kwargs["progress"],
        )
        self.assertIn((98, "Book ready for editorial review"), progress)

    def test_background_book_never_invents_a_human_decision(self) -> None:
        active = {
            "conversation_id": "conversation-test",
            "research_mode": "safe",
            "stage": "conversation_active",
            "stage_label": "active conversation",
            "revision": "r2",
        }
        source_hold = {
            "conversation_id": "conversation-test",
            "stage": "research_review",
            "stage_label": "research review",
            "revision": "r3",
            "pending_action": {
                "kind": "human_checkpoint",
                "command_available": True,
            },
        }
        with patch.object(
            workflow_service,
            "submit_conversation_turn",
            return_value={"workflow": active},
        ), patch.object(
            workflow_service,
            "finish_and_advance_workflow",
            return_value=source_hold,
        ), patch.object(
            workflow_service,
            "advance_workflow",
        ) as advance:
            result = workflow_service.build_book_to_review(
                "conversation-test",
                "Why does this matter?",
                expected_stage="conversation_active",
                expected_revision="r1",
            )

        self.assertEqual(result, source_hold)
        advance.assert_not_called()

    def test_human_can_replace_a_synthesis_hold_with_streamlined_foundation(self) -> None:
        held = {
            "conversation_id": "conversation-test",
            "stage": "synthesis_held",
            "revision": "r1",
        }
        recovered = {
            "conversation_id": "conversation-test",
            "stage": "book_synthesis",
            "revision": "r2",
        }
        with patch.object(
            workflow_service,
            "_assert_expected_state",
            return_value=held,
        ), patch.object(
            workflow_service.backend,
            "prepare_streamlined_synthesis_foundation",
            return_value={"state": "synthesis_required"},
        ) as prepare, patch.object(
            workflow_service,
            "inspect_workflow",
            return_value=recovered,
        ):
            result = workflow_service.authorize_streamlined_synthesis_recovery(
                "conversation-test",
                "The editor approved the two-call synthesis path.",
                expected_stage="synthesis_held",
                expected_revision="r1",
            )

        self.assertEqual(result["workflow"], recovered)
        prepare.assert_called_once_with(
            "book-conversation-test",
            reason="The editor approved the two-call synthesis path.",
            authorized_by="human_editor",
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

    def test_completed_continuation_hides_its_unfinished_parent_lineage(self) -> None:
        parent_id = "20260101T000000000000Z-aaaaaaaa"
        child_id = "20260101T000001000000Z-bbbbbbbb"
        self.conversations_path.mkdir(parents=True)
        for conversation_id in (parent_id, child_id):
            (self.conversations_path / f"{conversation_id}.jsonl").write_text(
                "placeholder\n",
                encoding="utf-8",
            )
        projected = {
            parent_id: {
                "conversation_id": parent_id,
                "stage": "research_continued",
            },
            child_id: {
                "conversation_id": child_id,
                "stage": "complete",
                "lineage": {
                    "kind": "research_revision",
                    "parent_conversation_id": parent_id,
                },
            },
        }
        with self.active_patches(), patch.object(
            workflow_service,
            "inspect_workflow",
            side_effect=lambda conversation_id, **_: projected[conversation_id],
        ):
            listed = workflow_service.list_resumable_workflows()

        self.assertEqual(listed["workflow_count"], 0)
        self.assertEqual(listed["workflows"], [])

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

    def test_runtime_observation_does_not_change_authoritative_revision(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.append_runtime_event(
                conversation_id,
                {
                    "schema_version": "1.0",
                    "event": "job_started",
                    "job_id": "job-one",
                    "kind": "conversation_turn",
                    "provider": "codex",
                    "status": "running",
                },
            )
            after_runtime_event = workflow_service.inspect_workflow(
                conversation_id,
                include_messages=False,
            )
            book_directory = self.books_path / f"book-{conversation_id}"
            (book_directory / "canonical-test.json").write_text(
                '{"state":"changed"}\n',
                encoding="utf-8",
            )
            after_canonical_change = workflow_service.inspect_workflow(
                conversation_id,
                include_messages=False,
            )

        self.assertEqual(after_runtime_event["revision"], created["revision"])
        self.assertNotEqual(
            after_canonical_change["revision"],
            created["revision"],
        )

    def test_runtime_observation_cannot_stale_the_provider_operation(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "load_skill_instructions",
            return_value="Librarian instructions",
        ), patch.object(
            backend_selector,
            "capture_backend_response",
            return_value=([{"type": "complete"}], "One answer"),
        ) as capture:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.append_runtime_event(
                conversation_id,
                {
                    "schema_version": "1.0",
                    "event": "job_started",
                    "job_id": "job-one",
                    "kind": "conversation_turn",
                    "provider": "codex",
                    "status": "running",
                },
            )
            result = workflow_service.submit_conversation_turn(
                conversation_id,
                "Run after telemetry is recorded.",
                expected_stage=created["stage"],
                expected_revision=created["revision"],
            )

        self.assertEqual(result["workflow"]["message_count"], 2)
        capture.assert_called_once()

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

    def test_advance_opens_and_groups_a_linked_research_continuation(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            backend_selector.append_conversation_message(
                Path(created["conversation_path"]),
                "user",
                "codex",
                "The durable lineage question",
            )
            workflow_service.finish_conversation(conversation_id)
            backend_selector.set_review_research_mode(
                conversation_id,
                "auto_approve",
                authorized_by="human_workflow_ui",
            )
            backend_selector.set_review_question_mode(
                conversation_id,
                "auto_defer",
                authorized_by="human_workflow_ui",
            )
            self.write_ready_review(conversation_id)
            decision_path = backend_selector.record_human_review_decision(
                self.reviews_path / f"{conversation_id}.json",
                decision="needs_research_or_revision",
                confirmed_core_question_or_thesis=(
                    "The durable lineage question"
                ),
            )
            child_path = backend_selector.create_research_continuation(
                Path(created["conversation_path"]),
                decision_path,
            )
            current = workflow_service.inspect_workflow(conversation_id)
            with patch.object(
                backend_selector,
                "continue_book_workflow",
                return_value={
                    "status": "research_continuation",
                    "conversation_id": conversation_id,
                    "conversation_path": str(child_path),
                    "parent_review_id": f"review-{conversation_id}",
                },
            ):
                result = workflow_service.advance_workflow(
                    conversation_id,
                    expected_stage=current["stage"],
                    expected_revision=current["revision"],
                )
            listed = workflow_service.list_resumable_workflows()
            with patch.object(
                backend_selector,
                "load_skill_instructions",
                return_value="Librarian instructions",
            ), patch.object(
                backend_selector,
                "capture_backend_response",
                return_value=([{"type": "complete"}], "Continued answer"),
            ) as capture:
                child_current = workflow_service.inspect_workflow(
                    child_path.stem
                )
                continued = workflow_service.submit_conversation_turn(
                    child_path.stem,
                    "Continue with all prior context.",
                    expected_stage=child_current["stage"],
                    expected_revision=child_current["revision"],
                )

        self.assertEqual(result["workflow"]["conversation_id"], child_path.stem)
        self.assertEqual(result["workflow"]["provider"], "codex")
        self.assertEqual(result["workflow"]["research_mode"], "auto_approve")
        self.assertEqual(
            result["workflow"]["opening_question"],
            "The durable lineage question",
        )
        self.assertEqual(result["workflow"]["local_message_count"], 0)
        self.assertEqual(result["workflow"]["message_count"], 1)
        self.assertEqual(
            [message["content"] for message in result["workflow"]["messages"]],
            ["The durable lineage question"],
        )
        self.assertEqual(
            result["workflow"]["lineage"]["parent_conversation_id"],
            conversation_id,
        )
        self.assertEqual(listed["workflow_count"], 1)
        self.assertEqual(listed["workflows"][0]["conversation_id"], child_path.stem)
        self.assertTrue(result["workflow"]["book_creation"]["active"])
        self.assertEqual(
            result["workflow"]["lineage_root_conversation_id"],
            conversation_id,
        )
        self.assertEqual(result["workflow"]["lineage_depth"], 1)
        self.assertGreaterEqual(result["workflow"]["overall_progress"], 20)
        self.assertEqual(
            result["workflow"]["progress"]["basis"],
            "durable_lineage",
        )
        provider_prompt = capture.call_args.args[2]
        self.assertIn("The durable lineage question", provider_prompt)
        self.assertIn('"workflow_context"', provider_prompt)
        self.assertIn('"stage": "conversation_active"', provider_prompt)
        self.assertIn('"parent_review_id": "review-1"', provider_prompt)
        self.assertEqual(
            [message["content"] for message in continued["workflow"]["messages"]],
            [
                "The durable lineage question",
                "Continue with all prior context.",
                "Continued answer",
            ],
        )

    def test_legacy_continuation_inherits_parent_mode_before_finish(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            backend_selector.set_review_research_mode(
                conversation_id,
                "auto_approve",
                authorized_by="human_workflow_ui",
            )
            backend_selector.set_review_question_mode(
                conversation_id,
                "auto_defer",
                authorized_by="human_workflow_ui",
            )
            self.write_ready_review(conversation_id)
            review_path = self.reviews_path / f"{conversation_id}.json"
            decision_path = backend_selector.record_human_review_decision(
                review_path,
                decision="needs_research_or_revision",
                confirmed_core_question_or_thesis="A bounded question",
            )
            child_path = backend_selector.create_conversation(
                {
                    "kind": "research_revision",
                    "parent_conversation_id": conversation_id,
                    "parent_review_id": "review-1",
                    "parent_review_sha256": backend_selector.file_sha256(
                        review_path
                    ),
                    "parent_decision_sha256": backend_selector.file_sha256(
                        decision_path
                    ),
                },
                "safe",
            )
            backend_selector.ensure_backend_selection(child_path, "codex")
            child = workflow_service.inspect_workflow(child_path.stem)
            finished = workflow_service.finish_conversation(child_path.stem)
            child_session = backend_selector.load_review_session(
                child_path.stem
            )

        self.assertEqual(child["research_mode"], "auto_approve")
        self.assertEqual(child["question_mode"], "auto_defer")
        self.assertEqual(finished["research_mode"], "auto_approve")
        self.assertEqual(finished["question_mode"], "auto_defer")
        self.assertEqual(child_session["research_mode"], "auto_approve")
        self.assertNotIn("question_mode", child_session)

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

    def test_chapter_source_checkpoint_is_reconstructed_after_restart(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "capture_backend_response",
        ) as capture:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, _ = self.enter_chapter_stage(
                conversation_id,
                status="source_approval_required",
                assessment_id="A1",
                stop_reason="A source requires permission.",
            )
            session_path = backend_selector.chapter_research_session_path(
                book_id,
                "CH1",
                "A1",
            )
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "book_id": book_id,
                        "chapter_id": "CH1",
                        "assessment_id": "A1",
                        "research_mode": "safe",
                        "pending_requests": [
                            {
                                "request_id": "SRC1",
                                "domain": "example.org",
                                "url": "https://example.org/source",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            inspected = workflow_service.inspect_workflow(conversation_id)
            restarted = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(inspected, restarted)
        self.assertEqual(inspected["stage"], "chapter_development")
        self.assertEqual(
            inspected["pending_action"]["decision_kind"],
            "chapter_source_permissions",
        )
        self.assertEqual(
            inspected["pending_action"]["context"]["pending_requests"][0][
                "request_id"
            ],
            "SRC1",
        )
        capture.assert_not_called()

    def test_resolved_chapter_source_checkpoint_is_backend_runnable(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "capture_backend_response",
        ) as capture:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, _ = self.enter_chapter_stage(
                conversation_id,
                status="source_approval_required",
                assessment_id="A1",
                stop_reason="A source requires permission.",
            )
            session_path = backend_selector.chapter_research_session_path(
                book_id,
                "CH1",
                "A1",
            )
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text(
                json.dumps(
                    {
                        "book_id": book_id,
                        "chapter_id": "CH1",
                        "assessment_id": "A1",
                        "research_mode": "safe",
                        "pending_requests": [],
                        "decisions": [
                            {
                                "request_id": "SRC1",
                                "domain": "example.org",
                                "url": "https://example.org/source",
                                "decision": "allow_once",
                            }
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            inspected = workflow_service.inspect_workflow(conversation_id)
            restarted = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(inspected, restarted)
        self.assertEqual(
            inspected["pending_action"]["kind"],
            "backend_transition",
        )
        self.assertTrue(inspected["pending_action"]["command_available"])
        capture.assert_not_called()

    def test_empty_interruption_review_remains_backend_runnable(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, baseline = self.enter_chapter_stage(
                conversation_id,
                status="active",
            )
            snapshot = backend_selector.build_chapter_controller_snapshot(
                book_id,
                "CH1",
            )
            decision = backend_selector.chapter_controller.decide_next_action(
                snapshot
            )
            ledger = backend_selector.chapter_controller.ChapterEventLedger(
                backend_selector.chapter_controller_ledger_path(
                    book_id,
                    "CH1",
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

            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(inspected["stage"], "chapter_development")
        self.assertEqual(
            inspected["pending_action"]["kind"],
            "backend_transition",
        )
        self.assertTrue(inspected["pending_action"]["command_available"])

    def test_chapter_human_input_is_reconstructed_with_exact_questions(self) -> None:
        questions = ["Which meaning should the chapter preserve?"]
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.enter_chapter_stage(
                conversation_id,
                status="human_input_required",
                assessment_id="A2",
                human_input_requests=questions,
            )
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(
            inspected["pending_action"]["decision_kind"],
            "chapter_human_input",
        )
        self.assertEqual(
            inspected["pending_action"]["context"]["questions"],
            questions,
        )

    def test_exhausted_human_closeout_offers_exact_final_pass_repair(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, run = self.enter_chapter_stage(
                conversation_id,
                status="human_input_closeout_failed",
                assessment_id="A8",
                stop_reason=(
                    "The remaining recovery budget cannot contain both "
                    "final attempts."
                ),
            )
            run.update(
                {
                    "last_assessment_outcome": "ready_for_final_pass",
                    "final_pass_requested": True,
                    "final_closeout_source_draft_id": "D4",
                    "final_closeout_developer_call_started_at": None,
                    "final_closeout_evaluator_call_started_at": None,
                }
            )
            backend_selector.save_chapter_run(run)
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(
            inspected["pending_action"]["decision_kind"],
            "final_pass_repair",
        )
        self.assertEqual(
            inspected["pending_action"]["context"]["book_id"],
            book_id,
        )
        self.assertTrue(inspected["pending_action"]["command_available"])

    def test_exhausted_performance_repair_offers_bounded_final_repair(self) -> None:
        with self.active_patches():
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, run = self.enter_chapter_stage(
                conversation_id,
                status="performance_repair_required",
                assessment_id="A3",
                stop_reason="The remaining audited defect needs review.",
            )
            run.update(
                {
                    "workflow_runtime_version": "performance-v2",
                    "performance_repair_cycles_total": 1,
                    "last_assessment_outcome": "revise_draft",
                }
            )
            backend_selector.save_chapter_run(run)
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(
            inspected["pending_action"]["decision_kind"],
            "final_pass_repair",
        )
        self.assertEqual(
            inspected["pending_action"]["context"]["book_id"],
            book_id,
        )
        self.assertIn(
            "exactly one terminal correction",
            inspected["pending_action"]["context"]["reason"],
        )
        self.assertTrue(inspected["pending_action"]["command_available"])

    def test_legacy_performance_no_progress_can_surface_repair(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "performance_repair_review_required",
            return_value=True,
        ):
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.enter_chapter_stage(
                conversation_id,
                status="controller_no_progress",
                assessment_id="A3",
                stop_reason="No approved artifact changed.",
            )
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(
            inspected["pending_action"]["decision_kind"],
            "final_pass_repair",
        )
        self.assertTrue(inspected["pending_action"]["command_available"])

    def test_authorized_legacy_performance_repair_is_resumable(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "load_chapter_recovery_authorization",
            return_value={"recovery_kind": "final_pass_revision"},
        ), patch.object(
            workflow_service,
            "_chapter_retry_action",
            return_value=None,
        ):
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.enter_chapter_stage(
                conversation_id,
                status="controller_no_progress",
                assessment_id="A3",
                stop_reason="No approved artifact changed.",
            )
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(
            inspected["pending_action"]["kind"],
            "backend_transition",
        )
        self.assertTrue(inspected["pending_action"]["command_available"])

    def test_authorized_planned_revision_retry_is_resumable(self) -> None:
        executable = Mock()
        executable.action = (
            backend_selector.chapter_controller.ActionKind.RUN_CHAPTER_PHASE
        )
        with self.active_patches(), patch.object(
            backend_selector,
            "load_chapter_recovery_authorization",
            return_value={"recovery_kind": "planned_draft_revision"},
        ), patch.object(
            backend_selector,
            "chapter_controller_dry_run",
            return_value=executable,
        ), patch.object(
            workflow_service,
            "_chapter_retry_action",
            return_value=None,
        ):
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.enter_chapter_stage(
                conversation_id,
                status="controller_no_progress",
                assessment_id="A3",
                stop_reason="No approved artifact changed.",
            )
            inspected = workflow_service.inspect_workflow(conversation_id)

        self.assertEqual(
            inspected["pending_action"]["kind"],
            "backend_transition",
        )
        self.assertTrue(inspected["pending_action"]["command_available"])

    def test_reconstructed_chapter_hold_blocks_advance_and_wrong_decision(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "find_backend_program",
            return_value="/usr/bin/codex",
        ), patch.object(
            backend_selector,
            "continue_book_workflow",
        ) as controller:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            book_id, _ = self.enter_chapter_stage(
                conversation_id,
                status="human_input_required",
                assessment_id="A3",
                human_input_requests=["Clarify this."],
            )
            current = workflow_service.inspect_workflow(conversation_id)
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as advance_error:
                workflow_service.advance_workflow(
                    conversation_id,
                    expected_stage=current["stage"],
                    expected_revision=current["revision"],
                )
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as decision_error:
                workflow_service.submit_checkpoint_decision(
                    conversation_id,
                    {
                        "kind": "chapter_recovery",
                        "book_id": book_id,
                        "chapter_id": "CH1",
                        "approval_note": "This is not the pending authority.",
                    },
                    expected_stage=current["stage"],
                    expected_revision=current["revision"],
                )

        self.assertEqual(advance_error.exception.code, "advance_not_allowed")
        self.assertEqual(
            decision_error.exception.code,
            "checkpoint_not_pending",
        )
        controller.assert_not_called()

    def test_auto_defer_bypasses_first_and_exhausted_retry_holds(self) -> None:
        for status in ("authorization_required", "exhausted"):
            with self.subTest(status=status):
                ledger = Mock()
                ledger.path.is_file.return_value = False
                ledger.active_contract.return_value = None
                ledger.retry_requirement.return_value = {
                    "status": status,
                    "failed_contract_id": "CC-2-example",
                }
                with patch.object(
                    backend_selector,
                    "build_chapter_controller_snapshot",
                    return_value=object(),
                ), patch.object(
                    backend_selector.chapter_controller,
                    "decide_next_action",
                    return_value=object(),
                ), patch.object(
                    backend_selector.chapter_controller,
                    "ChapterEventLedger",
                    return_value=ledger,
                ), patch.object(
                    backend_selector,
                    "can_apply_deferred_chapter_human_input",
                    return_value=True,
                ):
                    action = workflow_service._chapter_retry_action(
                        "book-conversation-test",
                        "CH1",
                    )

                self.assertEqual(action["kind"], "backend_transition")
                self.assertEqual(
                    action["operation"],
                    "apply_deferred_chapter_human_input",
                )
                self.assertTrue(action["command_available"])

    def test_repeated_auto_deferred_questions_project_draft_acceptance(
        self,
    ) -> None:
        ledger = Mock()
        ledger.path.is_file.return_value = False
        ledger.active_contract.return_value = None
        with patch.object(
            backend_selector,
            "build_chapter_controller_snapshot",
            return_value=object(),
        ), patch.object(
            backend_selector.chapter_controller,
            "decide_next_action",
            return_value=object(),
        ), patch.object(
            backend_selector.chapter_controller,
            "ChapterEventLedger",
            return_value=ledger,
        ), patch.object(
            backend_selector,
            "can_accept_auto_deferred_chapter_draft",
            return_value=True,
        ):
            action = workflow_service._chapter_retry_action(
                "book-conversation-test",
                "CH1",
            )

        self.assertEqual(action["kind"], "backend_transition")
        self.assertEqual(
            action["operation"],
            "accept_auto_deferred_chapter_draft",
        )
        self.assertTrue(action["command_available"])

    def test_exhausted_stale_graph_attempts_project_system_repair(self) -> None:
        ledger = Mock()
        ledger.path.is_file.return_value = False
        ledger.active_contract.return_value = None
        ledger.retry_requirement.return_value = {
            "status": "exhausted",
            "failed_contract_id": "CC-7-example",
        }
        with patch.object(
            backend_selector,
            "build_chapter_controller_snapshot",
            return_value=object(),
        ), patch.object(
            backend_selector.chapter_controller,
            "decide_next_action",
            return_value=object(),
        ), patch.object(
            backend_selector.chapter_controller,
            "ChapterEventLedger",
            return_value=ledger,
        ), patch.object(
            backend_selector,
            "can_apply_deferred_chapter_human_input",
            return_value=False,
        ), patch.object(
            backend_selector,
            "stale_graph_retry_repair_evidence",
            return_value={"repair_code": "mutable_graph"},
        ):
            action = workflow_service._chapter_retry_action(
                "book-conversation-test",
                "CH1",
            )

        self.assertEqual(action["kind"], "backend_transition")
        self.assertEqual(
            action["operation"],
            "repair_stale_graph_retry_exhaustion",
        )
        self.assertTrue(action["command_available"])

    def test_exhausted_planned_revision_misroute_projects_system_repair(
        self,
    ) -> None:
        ledger = Mock()
        ledger.path.is_file.return_value = False
        ledger.active_contract.return_value = None
        ledger.retry_requirement.return_value = {
            "status": "exhausted",
            "failed_contract_id": "CC-13-example",
        }
        with patch.object(
            backend_selector,
            "build_chapter_controller_snapshot",
            return_value=object(),
        ), patch.object(
            backend_selector.chapter_controller,
            "decide_next_action",
            return_value=object(),
        ), patch.object(
            backend_selector.chapter_controller,
            "ChapterEventLedger",
            return_value=ledger,
        ), patch.object(
            backend_selector,
            "can_apply_deferred_chapter_human_input",
            return_value=False,
        ), patch.object(
            backend_selector,
            "stale_graph_retry_repair_evidence",
            return_value=None,
        ), patch.object(
            backend_selector,
            "planned_revision_draft_misroute_repair_evidence",
            return_value={"repair_code": "planned_revision"},
        ):
            action = workflow_service._chapter_retry_action(
                "book-conversation-test",
                "CH1",
            )

        self.assertEqual(action["kind"], "backend_transition")
        self.assertEqual(
            action["operation"],
            "repair_planned_revision_draft_misroute",
        )
        self.assertTrue(action["command_available"])

    def test_auto_questions_projects_changed_condition_retry_as_transition(self) -> None:
        checkpoint = {
            "kind": "human_checkpoint",
            "decision_kind": "chapter_retry",
            "command_available": True,
            "context": {
                "book_id": "book-conversation-test",
                "chapter_id": "CH1",
                "failed_contract_id": "CC-2-example",
            },
        }

        projected = workflow_service._auto_question_retry_transition(
            "auto_defer",
            checkpoint,
        )

        self.assertEqual(projected["kind"], "backend_transition")
        self.assertEqual(
            projected["operation"],
            "authorize_auto_question_chapter_retry",
        )
        self.assertIn(
            "exactly one canonical workspace transition",
            projected["context"]["changed_condition"],
        )
        self.assertEqual(
            workflow_service._auto_question_retry_transition(
                "manual",
                checkpoint,
            ),
            checkpoint,
        )

    def test_advance_applies_deferred_input_without_provider_call(self) -> None:
        current = {
            "conversation_id": "conversation-test",
            "stage": "chapter_development",
            "revision": "r1",
            "pending_action": {
                "kind": "backend_transition",
                "operation": "apply_deferred_chapter_human_input",
                "command_available": True,
                "context": {
                    "book_id": "book-conversation-test",
                    "chapter_id": "CH1",
                },
            },
        }
        resumed = {
            **current,
            "revision": "r2",
            "pending_action": {
                "kind": "backend_transition",
                "command_available": True,
            },
        }
        recovery = {
            "status": "deferred_human_input_applied",
            "workspace_version": 2,
        }
        with patch.object(
            workflow_service,
            "_assert_expected_state",
            return_value=current,
        ), patch.object(
            workflow_service,
            "_bound_book_id",
            return_value="book-conversation-test",
        ), patch.object(
            backend_selector,
            "apply_deferred_chapter_human_input",
            return_value=recovery,
        ) as apply_deferred, patch.object(
            workflow_service,
            "inspect_workflow",
            return_value=resumed,
        ), patch.object(
            backend_selector,
            "continue_book_workflow",
        ) as controller:
            result = workflow_service.advance_workflow(
                "conversation-test",
                expected_stage="chapter_development",
                expected_revision="r1",
            )

        self.assertEqual(result["result"], recovery)
        self.assertEqual(result["workflow"], resumed)
        apply_deferred.assert_called_once_with(
            "book-conversation-test",
            "CH1",
        )
        controller.assert_not_called()

    def test_terminal_chapter_status_is_a_non_runnable_hold(self) -> None:
        with self.active_patches(), patch.object(
            backend_selector,
            "continue_book_workflow",
        ) as controller:
            created = self.create()
            conversation_id = created["conversation_id"]
            workflow_service.finish_conversation(conversation_id)
            self.enter_chapter_stage(
                conversation_id,
                status="research_limit_reached",
                stop_reason="The bounded research budget was exhausted.",
            )
            current = workflow_service.inspect_workflow(conversation_id)
            with self.assertRaises(
                workflow_service.WorkflowConflictError
            ) as caught:
                workflow_service.advance_workflow(
                    conversation_id,
                    expected_stage=current["stage"],
                    expected_revision=current["revision"],
                )

        self.assertEqual(current["pending_action"]["kind"], "terminal_hold")
        self.assertEqual(
            current["pending_action"]["status"],
            "research_limit_reached",
        )
        self.assertEqual(caught.exception.code, "advance_not_allowed")
        controller.assert_not_called()


if __name__ == "__main__":
    unittest.main()
