import json
import tempfile
import unittest
from pathlib import Path

import source_policy_store


class SourcePolicyStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name)
        self.default_path = (
            self.project_path / "config" / "source_policy.default.json"
        )
        self.local_path = self.project_path / "data" / "source_policy.json"
        self.default_policy = {
            "approved_domains": [],
            "allow_subdomains": False,
            "max_response_bytes": 500000,
            "max_text_characters": 30000,
            "timeout_seconds": 15,
        }
        self.default_path.parent.mkdir(parents=True)
        self.default_path.write_text(
            json.dumps(self.default_policy),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_local_policy_is_initialized_from_public_default(
        self,
    ) -> None:
        policy = source_policy_store.load_or_initialize(
            self.local_path,
            self.default_path,
        )

        self.assertEqual(policy, self.default_policy)
        self.assertEqual(
            json.loads(self.local_path.read_text(encoding="utf-8")),
            self.default_policy,
        )

    def test_existing_local_approvals_do_not_change_public_default(self) -> None:
        local_policy = {
            **self.default_policy,
            "approved_domains": ["arxiv.org", "www.w3.org"],
        }
        source_policy_store.save(self.local_path, local_policy)

        loaded = source_policy_store.load_or_initialize(
            self.local_path,
            self.default_path,
        )

        self.assertEqual(loaded, local_policy)
        self.assertEqual(
            json.loads(self.default_path.read_text(encoding="utf-8")),
            self.default_policy,
        )

    def test_invalid_local_json_fails_without_replacing_it(self) -> None:
        self.local_path.parent.mkdir(parents=True)
        self.local_path.write_text("{invalid", encoding="utf-8")

        with self.assertRaises(source_policy_store.SourcePolicyStoreError):
            source_policy_store.load_or_initialize(
                self.local_path,
                self.default_path,
            )

        self.assertEqual(
            self.local_path.read_text(encoding="utf-8"),
            "{invalid",
        )


if __name__ == "__main__":
    unittest.main()
