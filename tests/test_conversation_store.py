import json
import tempfile
import unittest
from pathlib import Path

import conversation_store


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name).resolve()
        self.conversations_path = (
            self.project_path / "data" / "conversations"
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_conversation(self, conversation_id: str = "test-conversation") -> Path:
        return conversation_store.create_conversation(
            self.conversations_path,
            "safe",
            conversation_id=conversation_id,
        )

    def test_formal_schemas_define_the_v1_contract(self) -> None:
        schema_path = (
            Path(conversation_store.__file__).parent
            / "schemas"
            / "conversation-event.v1.schema.json"
        )
        receipt_schema_path = (
            Path(conversation_store.__file__).parent
            / "schemas"
            / "conversation-receipt.v1.schema.json"
        )

        event_schema = json.loads(schema_path.read_text(encoding="utf-8"))
        receipt_schema = json.loads(
            receipt_schema_path.read_text(encoding="utf-8")
        )

        self.assertEqual(
            set(event_schema["properties"]["type"]["enum"]),
            conversation_store.EVENT_TYPES,
        )
        self.assertEqual(
            receipt_schema["properties"]["status"]["const"],
            "final",
        )

    def test_provider_switching_preserves_order_and_shared_turns(self) -> None:
        conversation_path = self.create_conversation()
        conversation_store.append_backend_selected(
            conversation_path,
            agent_host="codex",
            model_provider="openai",
        )
        first_user = conversation_store.append_message(
            conversation_path,
            role="user",
            content="What is the first claim?",
        )
        conversation_store.append_message(
            conversation_path,
            role="assistant",
            content="First define its evidence boundary.",
            turn_id=first_user["turn_id"],
            agent_host="codex",
            model_provider="openai",
        )
        conversation_store.append_backend_selected(
            conversation_path,
            agent_host="claude",
            model_provider="anthropic",
        )
        second_user = conversation_store.append_message(
            conversation_path,
            role="user",
            content="Can another harness continue?",
        )
        conversation_store.append_message(
            conversation_path,
            role="assistant",
            content="Yes, because the stored messages are provider-neutral.",
            turn_id=second_user["turn_id"],
            agent_host="claude",
            model_provider="anthropic",
        )

        events = conversation_store.load_events(conversation_path)
        turns = conversation_store.group_turns(events)
        messages = conversation_store.visible_messages(conversation_path)

        self.assertEqual(
            [event["sequence"] for event in events],
            list(range(1, 8)),
        )
        self.assertEqual(len(turns), 2)
        self.assertEqual(len(turns[0]["events"]), 2)
        self.assertEqual(
            [message["agent_host"] for message in messages],
            [None, "codex", None, "claude"],
        )

    def test_continuation_lineage_exposes_parent_and_local_review_messages(
        self,
    ) -> None:
        parent = self.create_conversation("parent-conversation")
        parent_turn = conversation_store.append_message(
            parent,
            role="user",
            content="Parent question",
        )
        conversation_store.append_message(
            parent,
            role="assistant",
            content="Parent answer",
            turn_id=parent_turn["turn_id"],
            agent_host="claude",
        )
        conversation_store.finish_conversation(
            parent,
            project_root=self.project_path,
            research_mode="safe",
        )
        child = conversation_store.create_conversation(
            self.conversations_path,
            "safe",
            conversation_id="child-conversation",
            continuation={
                "kind": "research_revision",
                "parent_conversation_id": parent.stem,
            },
        )
        child_turn = conversation_store.append_message(
            child,
            role="user",
            content="Child question",
        )
        conversation_store.append_message(
            child,
            role="assistant",
            content="Child answer",
            turn_id=child_turn["turn_id"],
            agent_host="codex",
        )

        messages = conversation_store.visible_messages_from_lineage(
            child,
            project_root=self.project_path,
        )

        self.assertEqual(
            [message["content"] for message in messages],
            [
                "Parent question",
                "Parent answer",
                "Child question",
                "Child answer",
            ],
        )

    def test_finish_is_idempotent_and_writes_exact_hash_receipt(self) -> None:
        conversation_path = self.create_conversation()
        user_event = conversation_store.append_message(
            conversation_path,
            role="user",
            content="Finish this once.",
        )
        conversation_store.append_message(
            conversation_path,
            role="assistant",
            content="Ready.",
            turn_id=user_event["turn_id"],
            agent_host="codex",
        )

        first_receipt = conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )
        transcript_after_first_finish = conversation_path.read_bytes()
        receipt_path = conversation_store.receipt_path_for(conversation_path)
        receipt_after_first_finish = receipt_path.read_bytes()
        second_receipt = conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )

        self.assertEqual(first_receipt, second_receipt)
        self.assertEqual(
            conversation_path.read_bytes(),
            transcript_after_first_finish,
        )
        self.assertEqual(receipt_path.read_bytes(), receipt_after_first_finish)
        self.assertEqual(first_receipt["event_count"], 4)
        self.assertEqual(
            conversation_store.verify_receipt(
                conversation_path,
                project_root=self.project_path,
            ),
            first_receipt,
        )

    def test_missing_receipt_is_recovered_without_a_second_finish_event(
        self,
    ) -> None:
        conversation_path = self.create_conversation()
        first_receipt = conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )
        receipt_path = conversation_store.receipt_path_for(conversation_path)
        receipt_path.unlink()

        recovered_receipt = conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )

        self.assertEqual(recovered_receipt, first_receipt)
        self.assertEqual(
            [
                event["type"]
                for event in conversation_store.load_events(conversation_path)
            ].count("conversation_finished"),
            1,
        )

    def test_append_after_finish_is_rejected(self) -> None:
        conversation_path = self.create_conversation()
        conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )

        with self.assertRaises(conversation_store.ConversationFinishedError):
            conversation_store.append_message(
                conversation_path,
                role="user",
                content="This must not be appended.",
            )

    def test_changed_finished_transcript_fails_receipt_verification(self) -> None:
        conversation_path = self.create_conversation()
        conversation_store.finish_conversation(
            conversation_path,
            project_root=self.project_path,
            research_mode="safe",
        )
        with conversation_path.open("a", encoding="utf-8") as transcript:
            transcript.write(" \n")

        with self.assertRaises(
            conversation_store.ConversationIntegrityError
        ):
            conversation_store.verify_receipt(
                conversation_path,
                project_root=self.project_path,
            )

    def test_interrupted_json_line_is_reported(self) -> None:
        conversation_path = self.create_conversation()
        with conversation_path.open("a", encoding="utf-8") as transcript:
            transcript.write('{"schema_version":"1.0"')

        with self.assertRaisesRegex(
            conversation_store.ConversationFormatError,
            "invalid JSON on line 2",
        ):
            conversation_store.load_events(conversation_path)

    def test_legacy_transcript_is_stably_normalized_without_rewrite(self) -> None:
        conversation_path = (
            self.conversations_path / "legacy-conversation.jsonl"
        )
        conversation_path.parent.mkdir(parents=True)
        legacy_events = [
            {
                "type": "conversation_started",
                "conversation_id": "legacy-conversation",
            },
            {
                "type": "message",
                "role": "user",
                "provider": "codex",
                "content": "Can this old record remain readable?",
            },
            {
                "type": "message",
                "role": "assistant",
                "provider": "codex",
                "content": "Yes, through an in-memory adapter.",
            },
            {
                "type": "conversation_finished",
                "status": "ready_for_review",
            },
        ]
        conversation_path.write_text(
            "".join(json.dumps(event) + "\n" for event in legacy_events),
            encoding="utf-8",
        )
        original_bytes = conversation_path.read_bytes()

        first_load = conversation_store.load_events(conversation_path)
        second_load = conversation_store.load_events(conversation_path)

        self.assertEqual(first_load, second_load)
        self.assertEqual(conversation_path.read_bytes(), original_bytes)
        self.assertEqual(first_load[0]["schema_version"], "1.0")
        self.assertEqual(first_load[1]["turn_id"], first_load[2]["turn_id"])
        self.assertEqual(
            conversation_store.visible_messages(conversation_path)[0][
                "content"
            ],
            "Can this old record remain readable?",
        )

        with self.assertRaises(conversation_store.ConversationFormatError):
            conversation_store.append_message(
                conversation_path,
                role="user",
                content="Do not mix formats.",
            )

    def test_unsupported_major_version_is_rejected(self) -> None:
        conversation_path = self.create_conversation()
        event = json.loads(
            conversation_path.read_text(encoding="utf-8").splitlines()[0]
        )
        event["schema_version"] = "2.0"
        conversation_path.write_text(
            json.dumps(event) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            conversation_store.ConversationFormatError,
            "Unsupported conversation schema major version",
        ):
            conversation_store.load_events(conversation_path)

    def test_paths_and_backend_errors_are_safely_bounded(self) -> None:
        conversation_path = self.create_conversation()
        turn_id = conversation_store.new_turn_id()

        with self.assertRaisesRegex(
            conversation_store.ConversationFormatError,
            "project-relative POSIX",
        ):
            conversation_store.append_message(
                conversation_path,
                role="user",
                turn_id=turn_id,
                content=[
                    {
                        "type": "artifact_ref",
                        "path": "C:\\Users\\example\\secret.txt",
                        "media_type": "text/plain",
                        "sha256": "a" * 64,
                    }
                ],
            )

        error_event = conversation_store.append_backend_error(
            conversation_path,
            turn_id=turn_id,
            agent_host="claude",
            category="provider_unavailable",
            message="Provider failed.\n/raw/private/detail",
            retryable=True,
        )

        self.assertEqual(
            error_event["payload"]["message"],
            "Provider failed. /raw/private/detail",
        )


if __name__ == "__main__":
    unittest.main()
