import importlib.metadata
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import backend_selector
import environment_check


class EnvironmentCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.project_path = Path(self.temporary_directory.name)
        self.write_required_project_files()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def write_required_project_files(self) -> None:
        files = {
            "pyproject.toml": "[project]\nname = \"test\"\n",
            "LICENSE": "Mozilla Public License Version 2.0\n",
            "LICENSE-SCOPE.md": "# Licensing\n",
            "LICENSES/CC-BY-SA-4.0.txt": (
                "Attribution-ShareAlike 4.0 International\n"
            ),
            "mcp_server.py": "",
            ".mcp.json": "{}\n",
            ".codex/config.example.toml": "",
            "backend_selector.py": "",
            "workflow_service.py": "",
            "transition_runtime.py": "",
            "frontend_server.py": "",
            "frontend/index.html": "",
            "frontend/styles.css": "",
            "frontend/app.js": "",
            "citation_grounding.py": "",
            "source_document.py": "",
        }
        for relative_path, content in files.items():
            path = self.project_path / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")

        for skill in environment_check.REQUIRED_SKILLS:
            path = self.project_path / "skills" / skill / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Skill\n", encoding="utf-8")

        for schema_name in environment_check.REQUIRED_SCHEMAS:
            path = self.project_path / "schemas" / schema_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema"}),
                encoding="utf-8",
            )

        policy_path = (
            self.project_path / "config" / "source_policy.default.json"
        )
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(
            json.dumps(
                {
                    "approved_domains": [],
                    "allow_subdomains": False,
                    "max_response_bytes": 500000,
                    "max_text_characters": 30000,
                    "timeout_seconds": 15,
                }
            ),
            encoding="utf-8",
        )

    def collect_with_programs(self, programs: dict[str, str | None]) -> list:
        versions = {
            "mcp": "1.27.2",
            "pydantic": "2.13.3",
            "pypdf": "6.14.2",
        }

        def which(program: str) -> str | None:
            return programs.get(program)

        with (
            patch.object(environment_check.shutil, "which", side_effect=which),
            patch.object(
                importlib.metadata,
                "version",
                side_effect=lambda distribution: versions[distribution],
            ),
        ):
            return environment_check.collect_environment_results(
                self.project_path
            )

    def write_codex_project_config(self) -> dict:
        transport = {
            "type": "stdio",
            "command": sys.executable,
            "args": [
                "run",
                str(self.project_path / "mcp_server.py"),
            ],
            "env": {
                "ALEXANDRIAN_PROJECT_PATH": str(self.project_path),
            },
            "cwd": str(self.project_path),
        }
        config_path = self.project_path / ".codex" / "config.toml"
        config_path.write_text(
            "[mcp_servers.alexandrian_library]\n"
            f'command = "{sys.executable}"\n'
            f'args = ["run", "{self.project_path / "mcp_server.py"}"]\n'
            f'cwd = "{self.project_path}"\n'
            "required = true\n\n"
            "[mcp_servers.alexandrian_library.env]\n"
            f'ALEXANDRIAN_PROJECT_PATH = "{self.project_path}"\n',
            encoding="utf-8",
        )
        return transport

    def write_codex_user_trust(self, trusted_path: Path) -> Path:
        config_path = self.project_path / "codex-home" / "config.toml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            f'[projects."{trusted_path}"]\n'
            'trust_level = "trusted"\n',
            encoding="utf-8",
        )
        return config_path

    def test_one_configured_backend_satisfies_the_backend_requirement(
        self,
    ) -> None:
        results = self.collect_with_programs(
            {"uv": "/bin/uv", "claude": "/bin/claude", "codex": None}
        )
        by_label = {result.label: result for result in results}

        self.assertEqual(by_label["Backend: codex"].status, "optional")
        self.assertEqual(by_label["Supported backend"].status, "available")
        self.assertEqual(
            by_label["Runnable full-workflow host"].status,
            "available",
        )
        self.assertFalse(
            [
                result
                for result in results
                if result.required and result.status == "missing"
            ]
        )

    def test_untrusted_codex_is_not_counted_as_a_runnable_host(self) -> None:
        untrusted = environment_check.CheckResult(
            environment_check.CODEX_READINESS_LABEL,
            "missing",
            f"{environment_check.CODEX_PROJECT_UNTRUSTED}: Not trusted.",
            required=False,
        )
        with patch.object(
            environment_check,
            "_codex_readiness_result",
            return_value=untrusted,
        ):
            results = self.collect_with_programs(
                {
                    "uv": "/bin/uv",
                    "claude": None,
                    "codex": "/bin/codex",
                }
            )
        by_label = {result.label: result for result in results}

        self.assertEqual(
            by_label["Runnable full-workflow host"].status,
            "missing",
        )

    def test_no_backend_is_a_required_failure(self) -> None:
        results = self.collect_with_programs(
            {"uv": "/bin/uv", "claude": None, "codex": None}
        )
        by_label = {result.label: result for result in results}

        self.assertEqual(by_label["Supported backend"].status, "missing")
        self.assertEqual(
            environment_check.print_environment_report(results),
            1,
        )

    def test_dependency_check_accepts_declared_lower_bounds(self) -> None:
        versions = {
            "mcp": "1.27.0",
            "pydantic": "2.0.0",
            "pypdf": "5.0.0",
        }

        with (
            patch.object(
                environment_check.shutil,
                "which",
                side_effect=lambda program: f"/bin/{program}",
            ),
            patch.object(
                importlib.metadata,
                "version",
                side_effect=lambda distribution: versions[distribution],
            ),
        ):
            results = environment_check.collect_environment_results(
                self.project_path
            )

        dependency_results = [
            result
            for result in results
            if result.label.startswith("Python dependency:")
        ]
        self.assertEqual(
            [result.status for result in dependency_results],
            ["available", "available", "available"],
        )

    def test_minimum_python_grammar_rejects_python_311_syntax(
        self,
    ) -> None:
        mcp_path = self.project_path / "mcp_server.py"
        mcp_path.write_text(
            "try:\n"
            "    pass\n"
            "except* Exception:\n"
            "    pass\n",
            encoding="utf-8",
        )

        result = environment_check._minimum_python_syntax_result(mcp_path)

        self.assertEqual(result.status, "missing")
        self.assertIn("Python 3.10", result.detail)

    def test_runtime_check_uses_the_real_uv_entry_point(self) -> None:
        completed = Mock(returncode=0, stdout="", stderr="")

        with patch.object(
            environment_check.subprocess,
            "run",
            return_value=completed,
        ) as run:
            result = environment_check._mcp_runtime_result(
                self.project_path,
                "/bin/uv",
            )

        self.assertEqual(result.status, "available")
        self.assertEqual(
            run.call_args.args[0],
            [
                "/bin/uv",
                "run",
                "--offline",
                str(self.project_path / "mcp_server.py"),
            ],
        )
        self.assertIs(
            run.call_args.kwargs["stdin"],
            environment_check.subprocess.DEVNULL,
        )

    def test_codex_readiness_distinguishes_not_installed(self) -> None:
        result = environment_check._codex_readiness_result(
            self.project_path,
            None,
            check_runtime=True,
        )

        self.assertEqual(result.status, "optional")
        self.assertTrue(
            result.detail.startswith(
                f"{environment_check.CODEX_NOT_INSTALLED}:"
            )
        )

    def test_codex_readiness_distinguishes_missing_project_config(
        self,
    ) -> None:
        result = environment_check._codex_readiness_result(
            self.project_path,
            "/bin/codex",
            check_runtime=True,
        )

        self.assertTrue(
            result.detail.startswith(
                f"{environment_check.CODEX_PROJECT_CONFIG_MISSING}:"
            )
        )

    def test_codex_trust_must_match_the_exact_worktree(self) -> None:
        parent_path = self.project_path.parent
        user_config_path = self.write_codex_user_trust(parent_path)

        trusted, detail = environment_check._exact_worktree_is_trusted(
            self.project_path,
            user_config_path,
        )

        self.assertFalse(trusted)
        self.assertIn(str(self.project_path), detail)

    def test_codex_readiness_stops_before_effective_check_when_untrusted(
        self,
    ) -> None:
        self.write_codex_project_config()
        user_config_path = self.write_codex_user_trust(
            self.project_path.parent
        )

        with (
            patch.object(
                environment_check,
                "_project_git_root",
                return_value=(self.project_path, ""),
            ),
            patch.object(
                environment_check,
                "_codex_user_config_path",
                return_value=user_config_path,
            ),
            patch.object(
                environment_check,
                "_effective_codex_mcp_server",
            ) as effective_server,
        ):
            result = environment_check._codex_readiness_result(
                self.project_path,
                "/bin/codex",
                check_runtime=True,
            )

        self.assertTrue(
            result.detail.startswith(
                f"{environment_check.CODEX_PROJECT_UNTRUSTED}:"
            )
        )
        effective_server.assert_not_called()

    def test_codex_readiness_rejects_configuration_codex_did_not_load(
        self,
    ) -> None:
        self.write_codex_project_config()
        user_config_path = self.write_codex_user_trust(self.project_path)

        with (
            patch.object(
                environment_check,
                "_project_git_root",
                return_value=(self.project_path, ""),
            ),
            patch.object(
                environment_check,
                "_codex_user_config_path",
                return_value=user_config_path,
            ),
            patch.object(
                environment_check,
                "_effective_codex_mcp_server",
                return_value=(None, "No effective server."),
            ),
        ):
            result = environment_check._codex_readiness_result(
                self.project_path,
                "/bin/codex",
                check_runtime=True,
            )

        self.assertTrue(
            result.detail.startswith(
                f"{environment_check.CODEX_MCP_NOT_EFFECTIVE}:"
            )
        )

    def test_codex_readiness_requires_protocol_tool_inventory(self) -> None:
        transport = self.write_codex_project_config()
        user_config_path = self.write_codex_user_trust(self.project_path)

        with (
            patch.object(
                environment_check,
                "_project_git_root",
                return_value=(self.project_path, ""),
            ),
            patch.object(
                environment_check,
                "_codex_user_config_path",
                return_value=user_config_path,
            ),
            patch.object(
                environment_check,
                "_effective_codex_mcp_server",
                return_value=(transport, "Effective."),
            ),
            patch.object(
                environment_check,
                "_mcp_tool_inventory_result",
                return_value=environment_check.CheckResult(
                    "Alexandrian MCP tool inventory",
                    "missing",
                    "One required tool is missing.",
                ),
            ),
        ):
            result = environment_check._codex_readiness_result(
                self.project_path,
                "/bin/codex",
                check_runtime=True,
            )

        self.assertTrue(
            result.detail.startswith(
                f"{environment_check.CODEX_MCP_SERVER_UNSTARTABLE}:"
            )
        )

    def test_codex_ready_requires_trust_effective_config_and_protocol(
        self,
    ) -> None:
        transport = self.write_codex_project_config()
        user_config_path = self.write_codex_user_trust(self.project_path)

        with (
            patch.object(
                environment_check,
                "_project_git_root",
                return_value=(self.project_path, ""),
            ),
            patch.object(
                environment_check,
                "_codex_user_config_path",
                return_value=user_config_path,
            ),
            patch.object(
                environment_check,
                "_effective_codex_mcp_server",
                return_value=(transport, "Effective."),
            ),
            patch.object(
                environment_check,
                "_mcp_tool_inventory_result",
                return_value=environment_check.CheckResult(
                    "Alexandrian MCP tool inventory",
                    "available",
                    "All required tools are available.",
                ),
            ),
        ):
            result = environment_check._codex_readiness_result(
                self.project_path,
                "/bin/codex",
                check_runtime=True,
            )

        self.assertEqual(result.status, "available")
        self.assertTrue(
            result.detail.startswith(f"{environment_check.CODEX_READY}:")
        )

    def test_effective_codex_check_uses_exact_project_root(self) -> None:
        completed = Mock(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "name": "alexandrian_library",
                        "enabled": True,
                        "transport": {"type": "stdio"},
                    }
                ]
            ),
            stderr="",
        )

        with patch.object(
            environment_check.subprocess,
            "run",
            return_value=completed,
        ) as run:
            transport, _ = environment_check._effective_codex_mcp_server(
                "/bin/codex",
                self.project_path,
            )

        self.assertEqual(transport, {"type": "stdio"})
        self.assertEqual(
            run.call_args.args[0],
            ["/bin/codex", "mcp", "list", "--json"],
        )
        self.assertEqual(
            run.call_args.kwargs["cwd"],
            self.project_path,
        )

    def test_invalid_default_policy_is_reported_without_rewriting_it(
        self,
    ) -> None:
        policy_path = (
            self.project_path / "config" / "source_policy.default.json"
        )
        policy_path.write_text("{invalid", encoding="utf-8")

        results = self.collect_with_programs(
            {"uv": "/bin/uv", "claude": "/bin/claude", "codex": None}
        )
        policy_result = next(
            result
            for result in results
            if result.label == "Default source policy"
        )

        self.assertEqual(policy_result.status, "missing")
        self.assertEqual(policy_path.read_text(encoding="utf-8"), "{invalid")

    def test_backend_entry_point_dispatches_check_without_starting_chat(
        self,
    ) -> None:
        with (
            patch.object(
                backend_selector.environment_check,
                "run_environment_check",
                return_value=0,
            ) as run_check,
            patch.object(backend_selector, "run_interactive") as run_chat,
        ):
            result = backend_selector.main(["--check"])

        self.assertEqual(result, 0)
        run_check.assert_called_once_with(backend_selector.PROJECT_PATH)
        run_chat.assert_not_called()

    def test_keyboard_interrupt_closes_interactive_cli_cleanly(self) -> None:
        with (
            patch.object(
                backend_selector,
                "run_interactive",
                side_effect=KeyboardInterrupt,
            ),
            patch("builtins.print") as terminal_print,
        ):
            result = backend_selector.main([])

        self.assertEqual(result, 0)
        terminal_print.assert_called_once_with(
            "\nAlexandrian Library closed."
        )

    def test_eof_closes_interactive_cli_cleanly(self) -> None:
        with (
            patch.object(
                backend_selector,
                "run_interactive",
                side_effect=EOFError,
            ),
            patch("builtins.print") as terminal_print,
        ):
            result = backend_selector.main([])

        self.assertEqual(result, 0)
        terminal_print.assert_called_once_with(
            "\nAlexandrian Library closed."
        )


if __name__ == "__main__":
    unittest.main()
