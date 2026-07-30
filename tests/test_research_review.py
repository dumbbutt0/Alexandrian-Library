import json
import os
import subprocess
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import backend_selector
import chapter_controller
import conversation_store
import mcp_server
from mcp.server.fastmcp.exceptions import ToolError


class ResearchReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name)
        self.conversations_path = self.project_path / "data" / "conversations"
        self.reviews_path = self.project_path / "data" / "reviews"
        self.evidence_path = self.project_path / "data" / "evidence"
        self.research_sources_path = (
            self.project_path / "data" / "research_sources"
        )
        self.review_sessions_path = self.project_path / "data" / "review_sessions"
        self.review_decisions_path = self.project_path / "data" / "review_decisions"
        self.source_policy_path = self.project_path / "config" / "source_policy.json"
        self.settings_path = self.project_path / "data" / "settings.json"
        self.conversations_path.mkdir(parents=True)
        self.source_policy_path.parent.mkdir(parents=True)
        self.write_source_policy([])

        mcp_server.PROJECT_PATH = self.project_path
        mcp_server.CONVERSATIONS_PATH = self.conversations_path
        mcp_server.REVIEWS_PATH = self.reviews_path
        mcp_server.EVIDENCE_PATH = self.evidence_path
        mcp_server.RESEARCH_SOURCES_PATH = self.research_sources_path
        mcp_server.REVIEW_SESSIONS_PATH = self.review_sessions_path
        mcp_server.SOURCE_POLICY_PATH = self.source_policy_path
        backend_selector.SETTINGS_PATH = self.settings_path
        backend_selector.REVIEW_SESSIONS_PATH = self.review_sessions_path
        mcp_server.SOURCE_RECEIPTS.clear()
        os.environ["ALEXANDRIAN_AGENT_HOST"] = "test-host"

    def tearDown(self) -> None:
        os.environ.pop("ALEXANDRIAN_AGENT_HOST", None)
        mcp_server.SOURCE_RECEIPTS.clear()
        self.temporary_directory.cleanup()

    def write_source_policy(self, approved_domains: list[str]) -> None:
        policy = {
            "approved_domains": approved_domains,
            "allow_subdomains": False,
            "max_response_bytes": 10000,
            "max_text_characters": 5000,
            "timeout_seconds": 1,
        }
        self.source_policy_path.write_text(
            json.dumps(policy),
            encoding="utf-8",
        )

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
                "content": "Could this question become a research path?",
            },
            {
                "type": "message",
                "role": "assistant",
                "provider": "codex",
                "content": "It needs a testable claim and evidence boundary.",
            },
            {
                "type": "conversation_finished",
                "status": "ready_for_review",
            },
        ]
        conversation_path = self.conversations_path / f"{conversation_id}.jsonl"
        conversation_path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8",
        )

    def make_review(
        self,
        conversation_id: str,
        transcript_reference: str = "T1",
    ) -> mcp_server.ResearchReview:
        grounded_text = {
            "text": "How does an idea become a grounded research path?",
            "transcript_references": [transcript_reference],
        }
        return mcp_server.ResearchReview.model_validate(
            {
                "conversation_id": conversation_id,
                "core_question": grounded_text,
                "curiosity_signature": {
                    "why_this_question_matters": [],
                    "original_intuition": [grounded_text],
                    "surprising_connections": [],
                    "central_tensions": [],
                    "desired_transformation": [],
                    "questions_that_must_remain_open": [grounded_text],
                },
                "scope": {
                    "included_topics": [grounded_text],
                    "excluded_topics": [],
                    "key_definitions": [],
                },
                "starting_model": {
                    "beliefs": [grounded_text],
                    "demonstrated_understanding": [],
                    "misunderstandings": [],
                },
                "belief_revisions": [],
                "claims": [],
                "mentioned_sources": [],
                "checked_sources": [],
                "research_trail": [],
                "evidence_gaps": [grounded_text],
                "connected_ideas": [],
                "separate_branches": [],
                "thought_questions": [grounded_text],
                "experiments": [],
                "use_case_candidates": [],
                "research_route": "research_seed",
                "route_reason": grounded_text,
                "recommended_next_step": "Define one falsifiable claim.",
            }
        )

    def test_numbered_transcript_matches_visible_messages(self) -> None:
        conversation_path = (
            self.conversations_path / "numbered-transcript.jsonl"
        )
        legacy_events = [
            {"type": "conversation_started"},
            {
                "type": "message",
                "role": "user",
                "provider": "codex",
                "content": "Question",
            },
            {"type": "tool_result", "content": "Hidden result"},
            {
                "type": "message",
                "role": "assistant",
                "provider": "claude",
                "content": "Answer",
            },
        ]
        conversation_path.write_text(
            "".join(json.dumps(event) + "\n" for event in legacy_events),
            encoding="utf-8",
        )
        events = conversation_store.load_events(conversation_path)

        transcript = backend_selector.build_numbered_transcript(events)
        v1_path = conversation_store.create_conversation(
            self.conversations_path,
            "safe",
            conversation_id="numbered-transcript-v1",
        )
        user_event = conversation_store.append_message(
            v1_path,
            role="user",
            content="Question",
        )
        conversation_store.append_external_activity(
            v1_path,
            turn_id=user_event["turn_id"],
            activity_type="legacy_tool_result",
            status="recorded",
            purpose="Hidden result",
            result_summary="Hidden result",
        )
        conversation_store.append_message(
            v1_path,
            role="assistant",
            content="Answer",
            turn_id=user_event["turn_id"],
            agent_host="claude",
        )
        v1_transcript = backend_selector.build_numbered_transcript(
            conversation_store.load_events(v1_path)
        )

        self.assertEqual(
            [item["transcript_reference"] for item in transcript],
            ["T1", "T2"],
        )
        self.assertEqual(transcript[1]["agent_host"], "claude")
        self.assertEqual(transcript, v1_transcript)

    def test_multiline_paste_enables_readline_bracketed_paste(self) -> None:
        mock_readline = Mock()

        with patch.object(backend_selector, "readline", mock_readline):
            backend_selector.enable_multiline_terminal_paste()

        mock_readline.parse_and_bind.assert_called_once_with(
            "set enable-bracketed-paste on"
        )

    def test_research_mode_defaults_safe_then_remembers_previous_use(
        self,
    ) -> None:
        self.assertEqual(backend_selector.load_research_mode(), "safe")

        backend_selector.save_research_mode("auto_approve")
        backend_selector.save_active_backend("codex")
        backend_selector.save_active_backend(None)

        settings = json.loads(
            self.settings_path.read_text(encoding="utf-8")
        )
        self.assertEqual(
            backend_selector.load_research_mode(),
            "auto_approve",
        )
        self.assertEqual(settings["research_mode"], "auto_approve")
        self.assertIsNone(settings["active_backend"])
        self.assertEqual(
            backend_selector.normalize_research_mode_choice("auto"),
            "auto_approve",
        )

    def test_public_backend_menu_rejects_deferred_ollama_option(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["3", "2"],
        ):
            selected_backend = backend_selector.select_backend()

        self.assertEqual(selected_backend, "claude")
        self.assertEqual(
            set(backend_selector.BACKENDS.values()),
            {"codex", "claude"},
        )

    def test_review_research_mode_snapshot_does_not_follow_later_changes(
        self,
    ) -> None:
        conversation_id = "mode-snapshot"

        first_mode = backend_selector.snapshot_review_research_mode(
            conversation_id,
            "auto_approve",
        )
        backend_selector.save_research_mode("safe")
        retained_mode = backend_selector.snapshot_review_research_mode(
            conversation_id,
            "safe",
        )
        session = json.loads(
            (
                self.review_sessions_path / f"{conversation_id}.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(first_mode, "auto_approve")
        self.assertEqual(retained_mode, "auto_approve")
        self.assertEqual(session["research_mode"], "auto_approve")

    def test_finish_event_records_snapshotted_research_mode(self) -> None:
        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
        ):
            conversation_path = backend_selector.create_conversation()
            backend_selector.finish_conversation(
                conversation_path,
                "auto_approve",
            )

        events = [
            json.loads(line)
            for line in conversation_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        self.assertEqual(
            events[-1]["payload"]["research_mode"],
            "auto_approve",
        )
        self.assertTrue(
            conversation_store.receipt_path_for(conversation_path).is_file()
        )

    def test_terminal_capture_writes_v1_and_preserves_provider_switch(
        self,
    ) -> None:
        prompts = []

        def capture(
            backend: str,
            _program_path: str,
            prompt: str,
        ) -> tuple[list[dict], str]:
            prompts.append((backend, prompt))
            return [], f"Response from {backend}."

        common_patches = (
            patch.object(backend_selector, "PROJECT_PATH", self.project_path),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
            patch.object(
                backend_selector,
                "load_skill_instructions",
                return_value="Librarian instructions",
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=capture,
            ),
        )

        with common_patches[0], common_patches[1], common_patches[2], common_patches[3]:
            with patch(
                "builtins.input",
                side_effect=["First question", "/bye"],
            ):
                conversation_path = backend_selector.run_capture_prompt_loop(
                    "codex",
                    "/usr/bin/codex",
                    None,
                )
            with patch(
                "builtins.input",
                side_effect=["Second question", "/bye"],
            ):
                conversation_path = backend_selector.run_capture_prompt_loop(
                    "claude",
                    "/usr/bin/claude",
                    conversation_path,
                )

        events = conversation_store.load_events(conversation_path)
        selected_hosts = [
            event["payload"]["agent_host"]["name"]
            for event in events
            if event["type"] == "backend_selected"
        ]
        turns = conversation_store.group_turns(events)

        self.assertEqual(
            selected_hosts,
            ["codex", "claude"],
        )
        self.assertEqual([len(turn["events"]) for turn in turns], [2, 2])
        self.assertIn("First question", prompts[1][1])
        self.assertIn("Response from codex.", prompts[1][1])

    def test_terminal_backend_failure_records_bounded_turn_error(self) -> None:
        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
            patch.object(
                backend_selector,
                "load_skill_instructions",
                return_value="Librarian instructions",
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=RuntimeError(
                    "private provider stderr at /home/example"
                ),
            ),
            patch("builtins.input", return_value="Question"),
        ):
            with self.assertRaises(SystemExit):
                backend_selector.run_capture_prompt_loop(
                    "codex",
                    "/usr/bin/codex",
                    None,
                )

        conversation_path = next(self.conversations_path.glob("*.jsonl"))
        events = conversation_store.load_events(conversation_path)
        user_event = next(
            event
            for event in events
            if event["type"] == "message"
        )
        error_event = next(
            event
            for event in events
            if event["type"] == "backend_error"
        )

        self.assertEqual(user_event["turn_id"], error_event["turn_id"])
        self.assertNotIn(
            "/home/example",
            error_event["payload"]["message"],
        )

    def test_terminal_finish_writes_receipt_before_book_workflow(self) -> None:
        observed_receipt = []

        def continue_workflow(
            _backend: str,
            _program_path: str,
            conversation_path: Path,
        ) -> dict:
            observed_receipt.append(
                conversation_store.verify_receipt(
                    conversation_path,
                    project_root=self.project_path,
                )
            )
            return {"status": "review_held"}

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
            patch.object(
                backend_selector,
                "load_skill_instructions",
                return_value="Librarian instructions",
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                return_value=([], "Answer"),
            ),
            patch.object(
                backend_selector,
                "continue_book_workflow",
                side_effect=continue_workflow,
            ),
            patch(
                "builtins.input",
                side_effect=["Question", "/finish"],
            ),
        ):
            result = backend_selector.run_capture_prompt_loop(
                "codex",
                "/usr/bin/codex",
                None,
            )

        self.assertIsNone(result)
        self.assertEqual(len(observed_receipt), 1)

    def test_terminal_finish_activates_exact_linked_continuation(self) -> None:
        reviews_path = self.project_path / "data" / "reviews"
        decisions_path = self.project_path / "data" / "review_decisions"
        captured_prompts = []
        continuation_paths = []

        def capture(
            _backend: str,
            _program_path: str,
            prompt: str,
        ) -> tuple[list[dict], str]:
            captured_prompts.append(prompt)
            return [], "Answer"

        def continue_workflow(
            _backend: str,
            _program_path: str,
            conversation_path: Path,
        ) -> dict:
            review_id = f"review-{conversation_path.stem}"
            review_path = reviews_path / f"{conversation_path.stem}.json"
            decision_path = (
                decisions_path / f"{conversation_path.stem}.json"
            )
            review_path.parent.mkdir(parents=True, exist_ok=True)
            decision_path.parent.mkdir(parents=True, exist_ok=True)
            review_path.write_text(
                json.dumps(
                    {
                        "review_id": review_id,
                        "conversation_id": conversation_path.stem,
                        "review": {
                            "core_question": {
                                "text": "What survives a provider timeout?"
                            },
                            "claims": [],
                            "checked_sources": [],
                            "evidence_gaps": [],
                            "experiments": [],
                        },
                    }
                ),
                encoding="utf-8",
            )
            decision_path.write_text(
                json.dumps(
                    {
                        "review_id": review_id,
                        "conversation_id": conversation_path.stem,
                        "decision": "needs_research_or_revision",
                        "confirmed_core_question_or_thesis": (
                            "Durable state must control recovery."
                        ),
                        "accepted_belief_revisions": [
                            "Count physical attempts separately."
                        ],
                        "disputed_points": [],
                        "excluded_material": [],
                    }
                ),
                encoding="utf-8",
            )
            continuation_path = (
                backend_selector.create_research_continuation(
                    conversation_path,
                    decision_path,
                )
            )
            continuation_paths.append(continuation_path)
            return {
                "status": "research_continuation",
                "conversation_path": str(continuation_path),
            }

        with (
            patch.object(
                backend_selector,
                "PROJECT_PATH",
                self.project_path,
            ),
            patch.object(
                backend_selector,
                "CONVERSATIONS_PATH",
                self.conversations_path,
            ),
            patch.object(
                backend_selector,
                "REVIEWS_PATH",
                reviews_path,
            ),
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                decisions_path,
            ),
            patch.object(
                backend_selector,
                "load_skill_instructions",
                return_value="Librarian instructions",
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=capture,
            ),
            patch.object(
                backend_selector,
                "continue_book_workflow",
                side_effect=continue_workflow,
            ),
            patch(
                "builtins.input",
                side_effect=[
                    "Initial question",
                    "/finish",
                    "Continue with the missing evidence.",
                    "/bye",
                ],
            ),
        ):
            result = backend_selector.run_capture_prompt_loop(
                "codex",
                "/usr/bin/codex",
                None,
            )

        self.assertEqual(result, continuation_paths[0])
        self.assertEqual(
            len(list(self.conversations_path.glob("*.jsonl"))),
            2,
        )
        continuation_messages = [
            message["content"]
            for message in conversation_store.visible_messages(result)
        ]
        self.assertEqual(
            continuation_messages,
            ["Continue with the missing evidence.", "Answer"],
        )
        self.assertEqual(len(captured_prompts), 2)
        self.assertIn(
            '"research_continuation_context"',
            captured_prompts[1],
        )
        self.assertIn(
            '"parent_review_id"',
            captured_prompts[1],
        )
        self.assertIn(
            "Count physical attempts separately.",
            captured_prompts[1],
        )

    def test_mcp_rejects_tampered_v1_conversation(self) -> None:
        conversation_path = conversation_store.create_conversation(
            self.conversations_path,
            "safe",
            conversation_id="tampered-v1",
        )
        conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )
        events = [
            json.loads(line)
            for line in conversation_path.read_text(
                encoding="utf-8"
            ).splitlines()
        ]
        events[0]["payload"]["application"] = "tampered-application"
        conversation_path.write_text(
            "".join(
                json.dumps(event, ensure_ascii=False) + "\n"
                for event in events
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            ToolError,
            "receipt verification",
        ):
            mcp_server.resolve_finished_conversation("tampered-v1")

    def test_terminal_research_command_updates_persistent_mode(self) -> None:
        responses = iter(["/research auto", "/bye"])

        with (
            patch.object(
                backend_selector,
                "load_skill_instructions",
                return_value="Librarian instructions",
            ),
            patch(
                "builtins.input",
                side_effect=lambda _prompt: next(responses),
            ),
        ):
            conversation_path = backend_selector.run_capture_prompt_loop(
                "codex",
                "/usr/bin/codex",
                None,
            )

        self.assertIsNone(conversation_path)
        self.assertEqual(
            backend_selector.load_research_mode(),
            "auto_approve",
        )

    def test_review_capture_commands_use_narrow_permissions(self) -> None:
        claude_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "review prompt",
            backend_selector.REVIEW_CALL,
        )
        codex_command = backend_selector.build_capture_command(
            "codex",
            "/usr/bin/codex",
            "review prompt",
            backend_selector.REVIEW_CALL,
        )
        claude_book_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "book prompt",
            backend_selector.BOOK_PLAN_CALL,
        )
        codex_book_command = backend_selector.build_capture_command(
            "codex",
            "/usr/bin/codex",
            "book prompt",
            backend_selector.BOOK_PLAN_CALL,
        )
        claude_chapter_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "chapter prompt",
            backend_selector.CHAPTER_DEVELOPMENT_CALL,
        )
        codex_chapter_command = backend_selector.build_capture_command(
            "codex",
            "/usr/bin/codex",
            "chapter prompt",
            backend_selector.CHAPTER_DEVELOPMENT_CALL,
        )
        claude_evaluator_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "evaluation prompt",
            backend_selector.CHAPTER_EVALUATION_CALL,
        )
        codex_evaluator_command = backend_selector.build_capture_command(
            "codex",
            "/usr/bin/codex",
            "evaluation prompt",
            backend_selector.CHAPTER_EVALUATION_CALL,
        )
        claude_researcher_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "research prompt",
            backend_selector.CHAPTER_RESEARCH_CALL,
        )
        codex_researcher_command = backend_selector.build_capture_command(
            "codex",
            "/usr/bin/codex",
            "research prompt",
            backend_selector.CHAPTER_RESEARCH_CALL,
        )
        claude_synthesis_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "synthesis prompt",
            backend_selector.BOOK_SYNTHESIS_CALL,
        )
        claude_audit_command = backend_selector.build_capture_command(
            "claude",
            "/usr/bin/claude",
            "audit prompt",
            backend_selector.SYNTHESIS_AUDIT_CALL,
        )

        self.assertIn("dontAsk", claude_command)
        self.assertIn(backend_selector.CLAUDE_REVIEW_TOOLS, claude_command)
        self.assertIn(
            "WebFetch,WebSearch,Bash,Edit,Write",
            claude_command,
        )
        self.assertIn(
            backend_selector.CLAUDE_BOOK_PLAN_TOOLS,
            claude_book_command,
        )
        self.assertNotIn(
            backend_selector.CLAUDE_REVIEW_TOOLS,
            claude_book_command,
        )
        self.assertIn(
            backend_selector.CLAUDE_CHAPTER_DEVELOPMENT_TOOLS,
            claude_chapter_command,
        )
        self.assertTrue(
            backend_selector.CLAUDE_CHAPTER_DEVELOPMENT_TOOLS.startswith(
                "Read,"
            )
        )
        self.assertIn(
            backend_selector.CLAUDE_CHAPTER_EVALUATION_TOOLS,
            claude_evaluator_command,
        )
        self.assertNotIn(
            "mcp__alexandrian-library__save_chapter_draft",
            backend_selector.CLAUDE_CHAPTER_EVALUATION_TOOLS,
        )
        self.assertIn(
            backend_selector.CLAUDE_CHAPTER_RESEARCH_TOOLS,
            claude_researcher_command,
        )
        self.assertIn(
            "WebSearch",
            backend_selector.CLAUDE_CHAPTER_RESEARCH_TOOLS,
        )
        self.assertIn(
            "WebFetch,Bash,Edit,Write",
            claude_researcher_command,
        )
        self.assertNotIn(
            "mcp__alexandrian-library__save_chapter_draft",
            backend_selector.CLAUDE_CHAPTER_RESEARCH_TOOLS,
        )
        self.assertIn(
            (
                "mcp__alexandrian-library__"
                "read_staged_synthesis_plan_shard_input"
            ),
            backend_selector.CLAUDE_BOOK_SYNTHESIS_TOOLS,
        )
        self.assertIn(
            (
                "mcp__alexandrian-library__"
                "submit_staged_synthesis_plan_shard"
            ),
            backend_selector.CLAUDE_BOOK_SYNTHESIS_TOOLS,
        )
        self.assertIn(
            (
                "mcp__alexandrian-library__"
                "submit_staged_synthesis_global_plan_audit"
            ),
            backend_selector.CLAUDE_SYNTHESIS_AUDIT_TOOLS,
        )
        self.assertIn(
            backend_selector.CLAUDE_BOOK_SYNTHESIS_TOOLS,
            claude_synthesis_command,
        )
        self.assertIn(
            backend_selector.CLAUDE_SYNTHESIS_AUDIT_TOOLS,
            claude_audit_command,
        )
        self.assertEqual(
            claude_synthesis_command.count("--permission-mode"),
            1,
        )
        self.assertEqual(
            codex_command,
            [
                "/usr/bin/codex",
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "review prompt",
            ],
        )
        self.assertEqual(
            codex_book_command,
            [
                "/usr/bin/codex",
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "book prompt",
            ],
        )
        self.assertEqual(
            codex_chapter_command,
            [
                "/usr/bin/codex",
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "chapter prompt",
            ],
        )
        self.assertEqual(
            codex_evaluator_command,
            [
                "/usr/bin/codex",
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "evaluation prompt",
            ],
        )
        self.assertEqual(
            codex_researcher_command,
            [
                "/usr/bin/codex",
                "--search",
                "exec",
                "--json",
                "--sandbox",
                "read-only",
                "research prompt",
            ],
        )

    def test_staged_failure_diagnostic_keeps_safe_tool_evidence(self) -> None:
        project = self.project_path
        book_id = "book-diagnostic-test"
        book = project / "data" / "books" / book_id
        target = (
            book
            / "synthesis_cycles"
            / "cycle-003_staging"
            / "plan_shards"
            / "CH1.json"
        )
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": (
                                "mcp__alexandrian-library__"
                                "submit_staged_synthesis_plan_shard"
                            ),
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "is_error": True,
                            "content": "Permission denied by dontAsk mode.",
                        }
                    ]
                },
            },
        ]
        with (
            patch.object(backend_selector, "PROJECT_PATH", project),
            patch.object(
                backend_selector,
                "BOOKS_PATH",
                project / "data" / "books",
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                return_value=(events, "Submission was denied."),
            ),
            self.assertRaisesRegex(
                RuntimeError,
                "did not produce its exact durable artifact",
            ),
        ):
            backend_selector.run_staged_provider_action(
                "claude",
                "/usr/bin/claude",
                book_id,
                "plan-shard-CH1",
                target,
                "bounded prompt",
                backend_selector.BOOK_SYNTHESIS_CALL,
                maximum_stage_calls=2,
            )

        diagnostic_paths = list(
            (
                book
                / "runs"
                / "synthesis_diagnostics"
            ).glob("*/D01-claude.json")
        )
        self.assertEqual(len(diagnostic_paths), 1)
        diagnostic = json.loads(
            diagnostic_paths[0].read_text(encoding="utf-8")
        )
        self.assertEqual(
            diagnostic["classification"],
            "tool_permission_denied",
        )
        self.assertFalse(diagnostic["raw_events_stored"])
        self.assertFalse(diagnostic["hidden_reasoning_stored"])
        self.assertNotIn("events", diagnostic)
        self.assertEqual(
            diagnostic["tool_calls"][0]["tool_name"],
            (
                "mcp__alexandrian-library__"
                "submit_staged_synthesis_plan_shard"
            ),
        )
        ledger = [
            json.loads(line)
            for line in (
                book / "runs" / "synthesis.controller.jsonl"
            ).read_text(encoding="utf-8").splitlines()
        ]
        failure = ledger[-1]
        self.assertEqual(
            failure["failure_classification"],
            "tool_permission_denied",
        )
        self.assertEqual(
            failure["diagnostic_sha256"],
            diagnostic["diagnostic_sha256"],
        )

    def test_captured_backend_does_not_inherit_terminal_input(self) -> None:
        process = Mock(
            pid=123,
            returncode=0,
        )
        process.communicate.return_value = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "Captured response.",
                    },
                }
            ),
            "",
        )

        with patch.object(
            backend_selector.subprocess,
            "Popen",
            return_value=process,
        ) as popen:
            _, response = backend_selector.capture_backend_response(
                "codex",
                "/usr/bin/codex",
                "Review prompt",
                backend_selector.REVIEW_CALL,
            )

        self.assertEqual(response, "Captured response.")
        self.assertIs(
            popen.call_args.kwargs["stdin"],
            backend_selector.subprocess.DEVNULL,
        )
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(
            process.communicate.call_args.kwargs["timeout"],
            backend_selector.AUTONOMOUS_CALL_TIMEOUT_SECONDS,
        )

    def test_autonomous_backend_timeout_is_bounded_and_sanitized(self) -> None:
        private_prompt = "private planning prompt"
        process = Mock(pid=123, returncode=-9)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(
                cmd=["/usr/bin/codex", private_prompt],
                timeout=backend_selector.AUTONOMOUS_CALL_TIMEOUT_SECONDS,
            ),
            ("", ""),
        ]

        with (
            patch.object(
                backend_selector.subprocess,
                "Popen",
                return_value=process,
            ),
            patch.object(backend_selector.os, "killpg") as killpg,
            self.assertRaisesRegex(
                RuntimeError,
                (
                    "codex book_plan timed out after 600 seconds.*"
                    "Resume will derive the next stage from canonical artifacts"
                ),
            ) as raised,
        ):
            backend_selector.capture_backend_response(
                "codex",
                "/usr/bin/codex",
                private_prompt,
                backend_selector.BOOK_PLAN_CALL,
            )

        killpg.assert_called_once_with(123, backend_selector.signal.SIGKILL)
        self.assertNotIn(private_prompt, str(raised.exception))

    def test_chapter_timeout_consumes_call_when_process_started(self) -> None:
        ledger = chapter_controller.ChapterEventLedger(
            self.project_path / "CH1.controller.jsonl"
        )
        snapshot = chapter_controller.ChapterSnapshot(
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
        decision = chapter_controller.decide_next_action(snapshot)
        contract = ledger.open_contract(snapshot, decision)
        ledger.mark_started(contract["contract_id"])
        process = Mock(pid=123, returncode=-9)
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(
                cmd=["/usr/bin/claude"],
                timeout=backend_selector.AUTONOMOUS_CALL_TIMEOUT_SECONDS,
            ),
            ("", ""),
        ]
        token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
            (ledger.path, contract["contract_id"])
        )
        try:
            with (
                patch.object(
                    backend_selector.subprocess,
                    "Popen",
                    return_value=process,
                ),
                patch.object(backend_selector.os, "killpg"),
                self.assertRaisesRegex(RuntimeError, "timed out"),
            ):
                backend_selector.capture_backend_response(
                    "claude",
                    "/usr/bin/claude",
                    "Chapter prompt",
                    backend_selector.CHAPTER_DEVELOPMENT_CALL,
                )
        finally:
            backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

        self.assertEqual(
            ledger.provider_call_counts(contract["contract_id"]),
            {backend_selector.CHAPTER_DEVELOPMENT_CALL: 1},
        )

    def test_chapter_attempt_is_committed_before_process_dispatch(
        self,
    ) -> None:
        ledger = chapter_controller.ChapterEventLedger(
            self.project_path / "CH1-write-ahead.controller.jsonl"
        )
        snapshot = chapter_controller.ChapterSnapshot(
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
        decision = chapter_controller.decide_next_action(snapshot)
        contract = ledger.open_contract(snapshot, decision)
        ledger.mark_started(contract["contract_id"])
        process = Mock(pid=123, returncode=0)
        process.communicate.return_value = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "Chapter response.",
                    },
                }
            ),
            "",
        )

        def observe_dispatch(*_args, **_kwargs):
            self.assertEqual(
                ledger.provider_call_counts(contract["contract_id"]),
                {backend_selector.CHAPTER_DEVELOPMENT_CALL: 1},
            )
            return process

        token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
            (ledger.path, contract["contract_id"])
        )
        try:
            with patch.object(
                backend_selector.subprocess,
                "Popen",
                side_effect=observe_dispatch,
            ):
                backend_selector.capture_backend_response(
                    "codex",
                    "/usr/bin/codex",
                    "Chapter prompt",
                    backend_selector.CHAPTER_DEVELOPMENT_CALL,
                )
        finally:
            backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

        self.assertEqual(
            ledger.provider_call_counts(contract["contract_id"]),
            {backend_selector.CHAPTER_DEVELOPMENT_CALL: 1},
        )

    def test_chapter_process_launch_failure_keeps_attempt_consumed(
        self,
    ) -> None:
        ledger = chapter_controller.ChapterEventLedger(
            self.project_path / "CH1-launch-failure.controller.jsonl"
        )
        snapshot = chapter_controller.ChapterSnapshot(
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
        decision = chapter_controller.decide_next_action(snapshot)
        contract = ledger.open_contract(snapshot, decision)
        ledger.mark_started(contract["contract_id"])
        token = backend_selector.ACTIVE_CHAPTER_CONTRACT.set(
            (ledger.path, contract["contract_id"])
        )
        try:
            with (
                patch.object(
                    backend_selector.subprocess,
                    "Popen",
                    side_effect=OSError("process launch failed"),
                ),
                self.assertRaisesRegex(OSError, "process launch failed"),
            ):
                backend_selector.capture_backend_response(
                    "claude",
                    "/usr/bin/claude",
                    "Chapter prompt",
                    backend_selector.CHAPTER_DEVELOPMENT_CALL,
                )
        finally:
            backend_selector.ACTIVE_CHAPTER_CONTRACT.reset(token)

        self.assertEqual(
            ledger.provider_call_counts(contract["contract_id"]),
            {backend_selector.CHAPTER_DEVELOPMENT_CALL: 1},
        )
        self.assertEqual(
            ledger.interrupted_contract_id(),
            contract["contract_id"],
        )

    def test_conversation_backend_call_has_no_timeout(self) -> None:
        process = Mock(
            pid=123,
            returncode=0,
        )
        process.communicate.return_value = (
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": "Conversation response.",
                    },
                }
            ),
            "",
        )

        with patch.object(
            backend_selector.subprocess,
            "Popen",
            return_value=process,
        ):
            backend_selector.capture_backend_response(
                "codex",
                "/usr/bin/codex",
                "Conversation prompt",
            )

        self.assertIsNone(process.communicate.call_args.kwargs["timeout"])

    def test_review_restarts_after_wrapper_resolves_unknown_source(self) -> None:
        conversation_id = "provider-neutral-loop"
        self.write_finished_conversation(conversation_id)
        conversation_path = self.conversations_path / f"{conversation_id}.jsonl"
        call_prompts = []

        def capture_side_effect(
            _backend: str,
            _program_path: str,
            prompt: str,
            _call_purpose: str,
        ) -> tuple[list[dict], str]:
            call_prompts.append(prompt)

            if len(call_prompts) == 1:
                session = backend_selector.load_review_session(conversation_id)
                session["pending_requests"].append(
                    {
                        "request_id": "source-request-test",
                        "url": "https://example.com/research",
                        "domain": "example.com",
                        "claim_under_review": "The claim",
                        "reason_for_source": "Check the claim.",
                        "requested_at": "2026-01-01T00:00:00+00:00",
                    }
                )
                backend_selector.save_review_session(conversation_id, session)
            else:
                self.reviews_path.mkdir(parents=True, exist_ok=True)
                (self.reviews_path / f"{conversation_id}.json").write_text(
                    "{}",
                    encoding="utf-8",
                )
                (self.reviews_path / f"{conversation_id}.md").write_text(
                    "# Completed review",
                    encoding="utf-8",
                )

            return [], "Review pass completed."

        with (
            patch.object(backend_selector, "REVIEWS_PATH", self.reviews_path),
            patch.object(
                backend_selector,
                "REVIEW_SESSIONS_PATH",
                self.review_sessions_path,
            ),
            patch.object(
                backend_selector,
                "SOURCE_POLICY_PATH",
                self.source_policy_path,
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                side_effect=capture_side_effect,
            ),
            patch("builtins.input", return_value="3"),
        ):
            _, _, review_body = backend_selector.review_finished_conversation(
                "codex",
                "/usr/bin/codex",
                conversation_path,
            )

        self.assertEqual(review_body, "# Completed review")
        self.assertEqual(len(call_prompts), 2)
        self.assertIn('"decision": "reject"', call_prompts[1])
        self.assertIn("<trusted_source_decisions>", call_prompts[1])

    def test_incomplete_review_reports_bounded_provider_explanation(self) -> None:
        conversation_id = "reviewer-explanation"
        self.write_finished_conversation(conversation_id)
        conversation_path = self.conversations_path / f"{conversation_id}.jsonl"
        provider_response = "The Alexandrian MCP server is not connected."

        with (
            patch.object(backend_selector, "REVIEWS_PATH", self.reviews_path),
            patch.object(
                backend_selector,
                "REVIEW_SESSIONS_PATH",
                self.review_sessions_path,
            ),
            patch.object(
                backend_selector,
                "capture_backend_response",
                return_value=([], provider_response),
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Alexandrian MCP server is not connected",
            ):
                backend_selector.review_finished_conversation(
                    "claude",
                    "/usr/bin/claude",
                    conversation_path,
                )

    def test_unapproved_source_stages_decision_before_network_access(self) -> None:
        conversation_id = "unapproved-source"
        self.write_finished_conversation(conversation_id)

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
        ) as build_opener:
            result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/research",
                "The claim under review",
                "Example.com may contain the primary evidence.",
            )

        build_opener.assert_not_called()
        self.assertEqual(mcp_server.SOURCE_RECEIPTS, {})
        self.assertEqual(result.status, "approval_required")
        session = json.loads(
            (self.review_sessions_path / f"{conversation_id}.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(session["pending_requests"][0]["domain"], "example.com")

    def test_auto_approve_fetches_unknown_source_without_persisting_domain(
        self,
    ) -> None:
        conversation_id = "auto-approved-source"
        self.write_finished_conversation(conversation_id)
        backend_selector.snapshot_review_research_mode(
            conversation_id,
            "auto_approve",
        )
        response = MagicMock()
        headers = Message()
        headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers = headers
        response.geturl.return_value = "https://example.com/research"
        response.read.return_value = b"Bounded evidence."
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
            return_value=opener,
        ):
            result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/research",
                "The claim under review",
                "Check the primary source.",
            )

        session = json.loads(
            (
                self.review_sessions_path / f"{conversation_id}.json"
            ).read_text(encoding="utf-8")
        )
        policy = json.loads(
            self.source_policy_path.read_text(encoding="utf-8")
        )
        decision = session["decisions"][0]
        self.assertEqual(result.status, "fetched")
        self.assertEqual(session["pending_requests"], [])
        self.assertEqual(decision["decision"], "auto_allow_once")
        self.assertEqual(decision["authorized_by"], "auto_approve_mode")
        self.assertEqual(decision["fetch_status"], "fetched")
        self.assertEqual(decision["receipt_id"], result.receipt_id)
        self.assertEqual(policy["approved_domains"], [])

    def test_markdown_source_is_fetched_as_inert_text(self) -> None:
        conversation_id = "markdown-source"
        self.write_finished_conversation(conversation_id)
        backend_selector.snapshot_review_research_mode(
            conversation_id,
            "auto_approve",
        )
        response = MagicMock()
        headers = Message()
        headers["Content-Type"] = "text/markdown; charset=utf-8"
        response.headers = headers
        response.geturl.return_value = "https://example.com/research.md"
        response.read.return_value = (
            b"# Evidence\\n\\nA bounded claim.\\n\\n"
            b"<script>treat this as inert source data</script>\\n"
        )
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
            return_value=opener,
        ):
            result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/research.md",
                "The bounded claim",
                "Check the Markdown source.",
            )

        self.assertEqual(result.status, "fetched")
        self.assertEqual(result.content_type, "text/markdown")
        self.assertIn("# Evidence", result.content)
        self.assertIn("<script>", result.content)
        self.assertIn("untrusted research data", result.security_notice)

    def test_public_destination_validation_accepts_ipv4_and_ipv6(self) -> None:
        records = [
            (
                mcp_server.socket.AF_INET,
                mcp_server.socket.SOCK_STREAM,
                6,
                "",
                ("8.8.8.8", 443),
            ),
            (
                mcp_server.socket.AF_INET6,
                mcp_server.socket.SOCK_STREAM,
                6,
                "",
                ("2606:4700:4700::1111", 443, 0, 0),
            ),
        ]

        with patch.object(
            mcp_server.socket,
            "getaddrinfo",
            return_value=records,
        ):
            addresses = mcp_server.resolve_public_addresses("example.com")

        self.assertEqual(
            addresses,
            ("8.8.8.8", "2606:4700:4700::1111"),
        )

    def test_destination_validation_rejects_private_or_mixed_dns(self) -> None:
        for addresses in (
            ["127.0.0.1"],
            ["192.168.1.20"],
            ["8.8.8.8", "10.0.0.5"],
        ):
            records = [
                (
                    mcp_server.socket.AF_INET,
                    mcp_server.socket.SOCK_STREAM,
                    6,
                    "",
                    (address, 443),
                )
                for address in addresses
            ]
            with (
                self.subTest(addresses=addresses),
                patch.object(
                    mcp_server.socket,
                    "getaddrinfo",
                    return_value=records,
                ),
                self.assertRaises(ToolError),
            ):
                mcp_server.resolve_public_addresses("example.com")

    def test_pinned_https_connection_uses_only_validated_address(
        self,
    ) -> None:
        tls_context = MagicMock()
        raw_socket = MagicMock()
        wrapped_socket = MagicMock()
        tls_context.wrap_socket.return_value = wrapped_socket
        connection = mcp_server.PinnedHTTPSConnection(
            "example.com",
            timeout=5,
            context=tls_context,
            validated_addresses=("8.8.8.8",),
        )

        with patch.object(
            mcp_server.socket,
            "create_connection",
            return_value=raw_socket,
        ) as create_connection:
            connection.connect()

        create_connection.assert_called_once_with(
            ("8.8.8.8", 443),
            5,
            None,
        )
        tls_context.wrap_socket.assert_called_once_with(
            raw_socket,
            server_hostname="example.com",
        )
        self.assertIs(connection.sock, wrapped_socket)

    def test_redirect_cannot_leave_exact_approved_domain(self) -> None:
        policy = mcp_server.SourcePolicy(
            approved_domains=["example.com"],
        )
        handler = mcp_server.GuardedRedirectHandler(policy)
        request = mcp_server.urllib_request.Request(
            "https://example.com/start"
        )

        with self.assertRaises(
            mcp_server.SourceRedirectApprovalRequired
        ) as raised:
            handler.redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://other.example/target",
            )
        self.assertEqual(
            raised.exception.url,
            "https://other.example/target",
        )
        self.assertEqual(raised.exception.domain, "other.example")

    def test_redirect_stages_its_own_source_decision(self) -> None:
        conversation_id = "redirect-approval"
        self.write_finished_conversation(conversation_id)
        self.write_source_policy(["example.com"])

        with patch.object(
            mcp_server,
            "fetch_source_content",
            side_effect=mcp_server.SourceRedirectApprovalRequired(
                "https://docs.example.net/target",
                "docs.example.net",
            ),
        ):
            result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/start",
                "The claim under review",
                "Check the official source.",
            )

        session = json.loads(
            (
                self.review_sessions_path / f"{conversation_id}.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(result.url, "https://docs.example.net/target")
        self.assertEqual(result.domain, "docs.example.net")
        self.assertEqual(
            session["pending_requests"][0]["url"],
            "https://docs.example.net/target",
        )
        self.assertIn(
            "redirect target requires its own trust decision",
            session["pending_requests"][0]["reason_for_source"],
        )
        self.assertEqual(mcp_server.SOURCE_RECEIPTS, {})

    def test_wrapper_records_allow_once_without_changing_domain_policy(self) -> None:
        conversation_id = "allow-once"
        self.write_finished_conversation(conversation_id)
        mcp_server.fetch_research_source(
            conversation_id,
            "https://example.com/research",
            "The claim under review",
            "Check one source.",
        )

        with (
            patch.object(
                backend_selector,
                "REVIEW_SESSIONS_PATH",
                self.review_sessions_path,
            ),
            patch.object(
                backend_selector,
                "SOURCE_POLICY_PATH",
                self.source_policy_path,
            ),
            patch("builtins.input", return_value="1"),
        ):
            resolved = backend_selector.resolve_pending_source_requests(
                conversation_id
            )

        session = json.loads(
            (self.review_sessions_path / f"{conversation_id}.json").read_text(
                encoding="utf-8"
            )
        )
        policy = json.loads(self.source_policy_path.read_text(encoding="utf-8"))
        self.assertTrue(resolved)
        self.assertEqual(session["pending_requests"], [])
        self.assertEqual(session["decisions"][0]["decision"], "allow_once")
        self.assertEqual(policy["approved_domains"], [])

        response = MagicMock()
        headers = Message()
        headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers = headers
        response.geturl.return_value = "https://example.com/research"
        response.read.return_value = b"Evidence for the claim."
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
            return_value=opener,
        ):
            fetch_result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/research",
                "The claim under review",
                "Check one source.",
            )

        self.assertEqual(fetch_result.status, "fetched")

    def test_wrapper_persists_only_the_exact_approved_domain(self) -> None:
        conversation_id = "allow-domain"
        self.write_finished_conversation(conversation_id)
        mcp_server.fetch_research_source(
            conversation_id,
            "https://docs.example.com/research",
            "The claim under review",
            "Check official documentation.",
        )

        with (
            patch.object(
                backend_selector,
                "REVIEW_SESSIONS_PATH",
                self.review_sessions_path,
            ),
            patch.object(
                backend_selector,
                "SOURCE_POLICY_PATH",
                self.source_policy_path,
            ),
            patch("builtins.input", return_value="2"),
        ):
            backend_selector.resolve_pending_source_requests(conversation_id)

        policy = json.loads(self.source_policy_path.read_text(encoding="utf-8"))
        self.assertEqual(policy["approved_domains"], ["docs.example.com"])
        self.assertFalse(policy["allow_subdomains"])

    def test_rejected_source_is_not_fetched_or_globally_blocked(self) -> None:
        conversation_id = "reject-source"
        self.write_finished_conversation(conversation_id)
        mcp_server.fetch_research_source(
            conversation_id,
            "https://example.com/research",
            "The claim under review",
            "Check one source.",
        )

        with (
            patch.object(
                backend_selector,
                "REVIEW_SESSIONS_PATH",
                self.review_sessions_path,
            ),
            patch.object(
                backend_selector,
                "SOURCE_POLICY_PATH",
                self.source_policy_path,
            ),
            patch("builtins.input", return_value="3"),
        ):
            backend_selector.resolve_pending_source_requests(conversation_id)

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
        ) as build_opener:
            result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/research",
                "The claim under review",
                "Check one source.",
            )

        policy = json.loads(self.source_policy_path.read_text(encoding="utf-8"))
        build_opener.assert_not_called()
        self.assertEqual(result.status, "rejected")
        self.assertEqual(policy["approved_domains"], [])

    def test_review_cannot_submit_while_source_decision_is_pending(self) -> None:
        conversation_id = "pending-source"
        self.write_finished_conversation(conversation_id)
        mcp_server.fetch_research_source(
            conversation_id,
            "https://example.com/research",
            "The claim under review",
            "Check one source.",
        )

        with self.assertRaises(ToolError):
            mcp_server.submit_research_review(self.make_review(conversation_id))

        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.json").exists()
        )

    def test_checked_claim_requires_real_source_receipt(self) -> None:
        conversation_id = "receipt-backed-review"
        self.write_finished_conversation(conversation_id)
        self.write_source_policy(["example.com"])
        response = MagicMock()
        headers = Message()
        headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers = headers
        response.geturl.return_value = "https://example.com/research"
        response.read.return_value = (
            b"<html><head><title>Primary Study</title></head>"
            b"<body>The measured result supports the narrow claim.</body></html>"
        )
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
            return_value=opener,
        ):
            source_result = mcp_server.fetch_research_source(
                conversation_id,
                "https://example.com/research",
                "The narrow claim",
            )

        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["claims"] = [
            {
                "claim": "The narrow claim",
                "origin": "assistant",
                "status": "supported_by_checked_source",
                "transcript_references": ["T2"],
                "source_receipt_ids": [source_result.receipt_id],
                "next_verification": "Replicate the measurement.",
            }
        ]
        review_data["checked_sources"] = [
            {
                "receipt_id": source_result.receipt_id,
                "claim_checked": "The narrow claim",
                "finding": "supports",
                "assessment": "The source supports only the narrow form.",
                "author_or_organization": "Example Research Group",
                "publication_date": "2026-01-01",
                "publisher_or_journal": "Example Journal",
                "doi": None,
                "evidence_location": "Results section",
                "evidence_excerpt": (
                    "The measured result supports the narrow claim."
                ),
                "evidence_summary": "The reported measurement supports the claim.",
                "limitations": "The source reports one bounded measurement.",
                "counterevidence_or_alternative_explanation": (
                    "A different baseline could change the comparison."
                ),
                "transcript_references": ["T2"],
            }
        ]
        review_data["research_trail"] = [
            {
                "query_or_question": "What evidence tests the narrow claim?",
                "candidate_source_url": "https://example.com/research",
                "decision": "included",
                "reason": "It directly reports the relevant measurement.",
                "receipt_id": source_result.receipt_id,
                "transcript_references": ["T2"],
            }
        ]

        receipt = mcp_server.submit_research_review(
            mcp_server.ResearchReview.model_validate(review_data)
        )
        saved_json = json.loads(
            (self.reviews_path / f"{conversation_id}.json").read_text(
                encoding="utf-8"
            )
        )

        self.assertEqual(receipt.research_route.value, "research_seed")
        self.assertEqual(
            saved_json["source_receipts"][0]["receipt_id"],
            source_result.receipt_id,
        )
        self.assertNotIn("content", saved_json["source_receipts"][0])
        self.assertNotIn(
            source_result.receipt_id,
            mcp_server.SOURCE_RECEIPTS,
        )
        packet_path = (
            self.evidence_path
            / conversation_id
            / f"{source_result.receipt_id}.json"
        )
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        self.assertEqual(
            packet["evidence_excerpt"],
            "The measured result supports the narrow claim.",
        )
        staged_path = (
            self.research_sources_path
            / conversation_id
            / f"{source_result.receipt_id}.json"
        )
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        self.assertEqual(
            packet["source_receipt_path"],
            str(staged_path.relative_to(self.project_path)),
        )
        self.assertEqual(
            packet["source_receipt_record_sha256"],
            staged["receipt_record_sha256"],
        )
        self.assertEqual(
            packet["evidence_excerpt_sha256"],
            mcp_server.hashlib.sha256(
                packet["evidence_excerpt"].encode("utf-8")
            ).hexdigest(),
        )
        self.assertNotIn("content", packet)
        self.assertEqual(
            receipt.evidence_packet_paths,
            [
                str(
                    packet_path.relative_to(self.project_path)
                )
            ],
        )
        saved_markdown = (
            self.reviews_path / f"{conversation_id}.md"
        ).read_text(encoding="utf-8")
        self.assertIn("## Scope and Working Definitions", saved_markdown)
        self.assertIn("### Research Trail", saved_markdown)
        self.assertIn("Evidence location: Results section", saved_markdown)

    def test_one_time_fetch_is_reused_after_mcp_memory_restart(self) -> None:
        conversation_id = "durable-allow-once"
        claim = "The exact composite claim"
        url = "https://example.com/research"
        self.write_finished_conversation(conversation_id)
        session = mcp_server.load_review_session(conversation_id)
        session["decisions"].append(
            {
                "request_id": "source-request-durable",
                "url": url,
                "domain": "example.com",
                "claim_under_review": claim,
                "reason_for_source": "Check the exact claim.",
                "requested_at": "2026-01-01T00:00:00+00:00",
                "decision": "allow_once",
                "decided_at": "2026-01-01T00:01:00+00:00",
            }
        )
        mcp_server.write_review_session(conversation_id, session)
        response = MagicMock()
        headers = Message()
        headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers = headers
        response.geturl.return_value = url
        response.read.return_value = b"The source qualifies the composite claim."
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response

        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
            return_value=opener,
        ):
            first = mcp_server.fetch_research_source(
                conversation_id,
                url,
                claim,
                "Check the exact claim.",
            )

        staged_path = (
            self.research_sources_path
            / conversation_id
            / f"{first.receipt_id}.json"
        )
        staged = json.loads(staged_path.read_text(encoding="utf-8"))
        self.assertEqual(staged["lifecycle_status"], "fetched_unassessed")
        self.assertIn("untrusted research data", staged["security_notice"])
        saved_session = mcp_server.load_review_session(conversation_id)
        self.assertEqual(
            saved_session["decisions"][0]["receipt_id"],
            first.receipt_id,
        )
        self.assertIn("consumed_at", saved_session["decisions"][0])

        saved_session["fetched_sources"] = []
        saved_session["decisions"][0].pop("receipt_id")
        saved_session["decisions"][0].pop("consumed_at")
        mcp_server.write_review_session(conversation_id, saved_session)
        mcp_server.SOURCE_RECEIPTS.clear()
        with patch.object(
            mcp_server,
            "fetch_source_content",
            side_effect=AssertionError("source was fetched twice"),
        ):
            restored = mcp_server.fetch_research_source(
                conversation_id,
                url,
                claim,
                "Retry the same assessment.",
            )

        self.assertEqual(restored.receipt_id, first.receipt_id)
        self.assertEqual(restored.content_sha256, first.content_sha256)
        repaired_session = mcp_server.load_review_session(conversation_id)
        self.assertEqual(
            repaired_session["decisions"][0]["receipt_id"],
            first.receipt_id,
        )
        self.assertIn("consumed_at", repaired_session["decisions"][0])
        self.assertEqual(
            repaired_session["fetched_sources"][0]["receipt_id"],
            first.receipt_id,
        )

    def test_failed_review_keeps_source_for_corrected_submission(self) -> None:
        conversation_id = "durable-review-correction"
        checked_claim = "The composite claim is only partly established."
        narrower_claim = "One component is established."
        url = "https://example.com/research"
        self.write_finished_conversation(conversation_id)
        self.write_source_policy(["example.com"])
        response = MagicMock()
        headers = Message()
        headers["Content-Type"] = "text/plain; charset=utf-8"
        response.headers = headers
        response.geturl.return_value = url
        response.read.return_value = (
            b"The source supports one component but not the complete claim."
        )
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.object(
            mcp_server.urllib_request,
            "build_opener",
            return_value=opener,
        ):
            fetched = mcp_server.fetch_research_source(
                conversation_id,
                url,
                checked_claim,
            )

        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["claims"] = [
            {
                "claim": narrower_claim,
                "origin": "assistant",
                "status": "supported_by_checked_source",
                "transcript_references": ["T2"],
                "source_receipt_ids": [fetched.receipt_id],
                "next_verification": "Fetch this narrower claim separately.",
            }
        ]
        review_data["checked_sources"] = [
            {
                "receipt_id": fetched.receipt_id,
                "claim_checked": checked_claim,
                "finding": "qualifies",
                "assessment": "Only one component is supported.",
                "author_or_organization": "Example Research Group",
                "publication_date": "2026-01-01",
                "publisher_or_journal": "Example Journal",
                "doi": None,
                "evidence_location": "Main result",
                "evidence_excerpt": (
                    "The source supports one component but not the complete "
                    "claim."
                ),
                "evidence_summary": "The compound claim requires qualification.",
                "limitations": "The source does not establish every component.",
                "counterevidence_or_alternative_explanation": (
                    "The unsupported components may be false."
                ),
                "transcript_references": ["T2"],
            }
        ]
        review_data["research_trail"] = [
            {
                "query_or_question": "Does the source establish the claim?",
                "candidate_source_url": url,
                "decision": "included",
                "reason": "It directly addresses part of the claim.",
                "receipt_id": fetched.receipt_id,
                "transcript_references": ["T2"],
            }
        ]
        mismatched_review = mcp_server.ResearchReview.model_validate(
            review_data
        )
        with self.assertRaisesRegex(
            ToolError,
            "requires a supports source assessment",
        ):
            mcp_server.submit_research_review(mismatched_review)

        staged_path = (
            self.research_sources_path
            / conversation_id
            / f"{fetched.receipt_id}.json"
        )
        self.assertTrue(staged_path.is_file())
        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.json").exists()
        )

        mcp_server.SOURCE_RECEIPTS.clear()
        review_data["claims"][0] = {
            "claim": checked_claim,
            "origin": "assistant",
            "status": "qualified_by_checked_source",
            "transcript_references": ["T2"],
            "source_receipt_ids": [fetched.receipt_id],
            "next_verification": "Check each unresolved component separately.",
        }
        receipt = mcp_server.submit_research_review(
            mcp_server.ResearchReview.model_validate(review_data)
        )

        self.assertEqual(receipt.research_route.value, "research_seed")
        self.assertTrue(
            (
                self.evidence_path
                / conversation_id
                / f"{fetched.receipt_id}.json"
            ).is_file()
        )
        self.assertTrue(staged_path.is_file())

    def test_fabricated_evidence_excerpt_writes_nothing(self) -> None:
        conversation_id = "fabricated-excerpt"
        self.write_finished_conversation(conversation_id)
        receipt_id = "source-real"
        claim = "The narrow claim"
        mcp_server.SOURCE_RECEIPTS[receipt_id] = mcp_server.SourceReceipt(
            receipt_id=receipt_id,
            conversation_id=conversation_id,
            requested_url="https://example.com/research",
            final_url="https://example.com/research",
            domain="example.com",
            title="A Real Source",
            content_type="text/plain",
            retrieved_at="2026-01-01T00:00:00+00:00",
            content_sha256="source-hash",
            claim_under_review=claim,
            content="The source reports a different bounded result.",
            content_truncated=False,
        )
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["claims"] = [
            {
                "claim": claim,
                "origin": "assistant",
                "status": "supported_by_checked_source",
                "transcript_references": ["T2"],
                "source_receipt_ids": [receipt_id],
                "next_verification": "Replicate the measurement.",
            }
        ]
        review_data["checked_sources"] = [
            {
                "receipt_id": receipt_id,
                "claim_checked": claim,
                "finding": "supports",
                "assessment": "The source supports the claim.",
                "author_or_organization": None,
                "publication_date": None,
                "publisher_or_journal": None,
                "doi": None,
                "evidence_location": "Results",
                "evidence_excerpt": "A passage the source never contained.",
                "evidence_summary": "An invented summary.",
                "limitations": "Unknown.",
                "counterevidence_or_alternative_explanation": "Unknown.",
                "transcript_references": ["T2"],
            }
        ]

        with self.assertRaises(ToolError):
            mcp_server.submit_research_review(
                mcp_server.ResearchReview.model_validate(review_data)
            )

        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.json").exists()
        )
        self.assertFalse(
            (self.evidence_path / conversation_id).exists()
        )

    def test_fabricated_source_receipt_writes_nothing(self) -> None:
        conversation_id = "fabricated-receipt"
        self.write_finished_conversation(conversation_id)
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["checked_sources"] = [
            {
                "receipt_id": "source-invented",
                "claim_checked": "Invented claim",
                "finding": "supports",
                "assessment": "Invented assessment",
                "author_or_organization": None,
                "publication_date": None,
                "publisher_or_journal": None,
                "doi": None,
                "evidence_location": "Unknown",
                "evidence_excerpt": "Invented evidence",
                "evidence_summary": "Invented evidence",
                "limitations": "Unknown",
                "counterevidence_or_alternative_explanation": "Unknown",
                "transcript_references": ["T1"],
            }
        ]

        with self.assertRaises(ToolError):
            mcp_server.submit_research_review(
                mcp_server.ResearchReview.model_validate(review_data)
            )

        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.json").exists()
        )
        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.md").exists()
        )

    def test_tool_writes_canonical_json_and_derived_markdown(self) -> None:
        conversation_id = "successful-review"
        self.write_finished_conversation(conversation_id)

        receipt = mcp_server.submit_research_review(
            self.make_review(conversation_id)
        )

        json_path = self.reviews_path / f"{conversation_id}.json"
        markdown_path = self.reviews_path / f"{conversation_id}.md"
        saved_json = json.loads(json_path.read_text(encoding="utf-8"))
        saved_markdown = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(receipt.research_route.value, "research_seed")
        self.assertEqual(saved_json["review_schema_version"], "1.4")
        self.assertEqual(saved_json["reviewer_host"], "test-host")
        self.assertEqual(
            saved_json["review"]["core_question"]["transcript_references"],
            ["T1"],
        )
        self.assertIn("# Alexandrian Research Review", saved_markdown)
        self.assertIn("## Curiosity Signature", saved_markdown)
        self.assertIn("Route: `research_seed`", saved_markdown)
        self.assertIn(
            "#### Source Permissions and Automatic Authorizations",
            saved_markdown,
        )
        self.assertIn(
            "**requires_user_decision; book-blocking:**",
            saved_markdown,
        )

    def test_ready_route_rejects_a_book_blocking_evidence_gap(self) -> None:
        conversation_id = "blocking-gap"
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["research_route"] = "ready_for_book_consideration"

        with self.assertRaisesRegex(
            ValueError,
            "evidence gap remains book-blocking",
        ):
            mcp_server.ResearchReview.model_validate(review_data)

    def test_human_checkpoint_is_stored_separately_from_review(self) -> None:
        conversation_id = "human-checkpoint"
        self.write_finished_conversation(conversation_id)
        mcp_server.submit_research_review(self.make_review(conversation_id))
        review_path = self.reviews_path / f"{conversation_id}.json"
        original_review = review_path.read_text(encoding="utf-8")
        responses = iter(
            [
                "1",
                "",
                "I accepted the narrower claim",
                "The causal interpretation",
                "The unrelated implementation branch",
            ]
        )

        with (
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.review_decisions_path,
            ),
            patch("builtins.input", side_effect=lambda _prompt: next(responses)),
        ):
            decision_path = backend_selector.record_human_review_checkpoint(
                review_path
            )

        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        self.assertEqual(
            decision["decision"],
            "accepted_research_route",
        )
        self.assertEqual(
            decision["accepted_belief_revisions"],
            ["I accepted the narrower claim"],
        )
        self.assertEqual(decision["book_planning_gate"], "blocked")
        self.assertEqual(
            review_path.read_text(encoding="utf-8"),
            original_review,
        )

    def test_human_checkpoint_can_pause_without_a_decision(self) -> None:
        conversation_id = "paused-checkpoint"
        self.write_finished_conversation(conversation_id)
        mcp_server.submit_research_review(self.make_review(conversation_id))
        review_path = self.reviews_path / f"{conversation_id}.json"

        with (
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.review_decisions_path,
            ),
            patch("builtins.input", return_value="0"),
        ):
            decision_path = backend_selector.record_human_review_checkpoint(
                review_path
            )

        self.assertIsNone(decision_path)
        self.assertFalse(
            (self.review_decisions_path / f"{conversation_id}.json").exists()
        )

    def test_curiosity_signature_rejects_assistant_provenance(self) -> None:
        conversation_id = "assistant-curiosity"
        self.write_finished_conversation(conversation_id)
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["curiosity_signature"]["original_intuition"][0][
            "transcript_references"
        ] = ["T2"]

        with self.assertRaises(ToolError):
            mcp_server.submit_research_review(
                mcp_server.ResearchReview.model_validate(review_data)
            )

        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.json").exists()
        )

    def test_review_requires_signature_object_but_allows_empty_fields(
        self,
    ) -> None:
        conversation_id = "empty-curiosity"
        self.write_finished_conversation(conversation_id)
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data_without_signature = dict(review_data)
        review_data_without_signature.pop("curiosity_signature")

        with self.assertRaises(ValueError):
            mcp_server.ResearchReview.model_validate(
                review_data_without_signature
            )

        review_data["curiosity_signature"] = {
            "why_this_question_matters": [],
            "original_intuition": [],
            "surprising_connections": [],
            "central_tensions": [],
            "desired_transformation": [],
            "questions_that_must_remain_open": [],
        }
        receipt = mcp_server.submit_research_review(
            mcp_server.ResearchReview.model_validate(review_data)
        )
        self.assertEqual(receipt.review_id, f"review-{conversation_id}")

    def test_ready_checkpoint_accepts_curiosity_without_extra_prompts(self) -> None:
        conversation_id = "accepted-curiosity"
        self.write_finished_conversation(conversation_id)
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["research_route"] = "ready_for_book_consideration"
        review_data["evidence_gaps"] = []
        mcp_server.submit_research_review(
            mcp_server.ResearchReview.model_validate(review_data)
        )
        review_path = self.reviews_path / f"{conversation_id}.json"
        responses = iter(["1", "", "", "", "", "1"])

        with (
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.review_decisions_path,
            ),
            patch("builtins.input", side_effect=lambda _prompt: next(responses)),
        ):
            decision_path = backend_selector.record_human_review_checkpoint(
                review_path
            )

        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        self.assertEqual(decision["curiosity_signature_decision"], "accepted")
        self.assertEqual(
            decision["approved_curiosity_signature"]["original_intuition"][0][
                "provenance"
            ],
            "review_extraction",
        )

    def test_ready_checkpoint_can_correct_individual_curiosity_fields(
        self,
    ) -> None:
        conversation_id = "corrected-curiosity"
        self.write_finished_conversation(conversation_id)
        review_data = self.make_review(conversation_id).model_dump(mode="json")
        review_data["research_route"] = "ready_for_book_consideration"
        review_data["evidence_gaps"] = []
        mcp_server.submit_research_review(
            mcp_server.ResearchReview.model_validate(review_data)
        )
        review_path = self.reviews_path / f"{conversation_id}.json"
        responses = iter(
            [
                "1",
                "",
                "",
                "",
                "",
                "2",
                "This question matters because I want a reusable method.",
                "",
                "Research graphs;Book architecture",
                "-",
                "Build a reality blueprint.",
                "",
            ]
        )

        with (
            patch.object(
                backend_selector,
                "REVIEW_DECISIONS_PATH",
                self.review_decisions_path,
            ),
            patch("builtins.input", side_effect=lambda _prompt: next(responses)),
        ):
            decision_path = backend_selector.record_human_review_checkpoint(
                review_path
            )

        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        signature = decision["approved_curiosity_signature"]
        self.assertEqual(decision["curiosity_signature_decision"], "corrected")
        self.assertEqual(
            signature["why_this_question_matters"][0]["provenance"],
            "human_checkpoint",
        )
        self.assertEqual(len(signature["surprising_connections"]), 2)
        self.assertEqual(signature["central_tensions"], [])
        self.assertEqual(
            signature["original_intuition"][0]["provenance"],
            "review_extraction",
        )

    def test_invalid_transcript_reference_writes_nothing(self) -> None:
        conversation_id = "invalid-reference"
        self.write_finished_conversation(conversation_id)

        with self.assertRaises(ToolError):
            mcp_server.submit_research_review(
                self.make_review(conversation_id, "T99")
            )

        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.json").exists()
        )
        self.assertFalse(
            (self.reviews_path / f"{conversation_id}.md").exists()
        )


if __name__ == "__main__":
    unittest.main()
