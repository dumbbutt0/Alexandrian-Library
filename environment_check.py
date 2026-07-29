"""Environment and runtime checks for the Alexandrian Library prototype."""

from __future__ import annotations

import ast
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MINIMUM_PYTHON = (3, 10)
DEPENDENCY_REQUIREMENTS = {
    "mcp": ((1, 27), 2),
    "pydantic": ((2, 0), 3),
}
SUPPORTED_BACKENDS = ("codex", "claude")
REQUIRED_SKILLS = (
    "alexandrian-librarian",
    "alexandrian-reviewer",
    "alexandrian-book-planner",
    "alexandrian-chapter-developer",
    "alexandrian-chapter-evaluator",
    "alexandrian-chapter-researcher",
    "alexandrian-book-synthesizer",
    "alexandrian-claim-auditor",
)
REQUIRED_SCHEMAS = (
    "chapter-controller-event.v1.schema.json",
    "conversation-event.v1.schema.json",
    "conversation-receipt.v1.schema.json",
    "synthesis-cycle-authorization.v1.schema.json",
    "synthesis-sharded-cycle-authorization.v1.schema.json",
    "synthesis-successor-cycle-authorization.v1.schema.json",
    "synthesis-terminal-successor-authorization.v1.schema.json",
    "synthesis-audit-recovery-authorization.v1.schema.json",
    "synthesis-plan-shard.v1.schema.json",
    "synthesis-tool-attempt.v1.schema.json",
    "synthesis-provider-stage-hold.v1.schema.json",
    "synthesis-provider-diagnostic.v1.schema.json",
    "synthesis-global-plan-audit.v1.schema.json",
    "synthesis-plan-audit.v1.schema.json",
)
REQUIRED_LICENSE_FILES = (
    "LICENSE",
    "LICENSE-SCOPE.md",
    "LICENSES/CC-BY-SA-4.0.txt",
)
REQUIRED_POLICY_FIELDS = {
    "approved_domains",
    "allow_subdomains",
    "max_response_bytes",
    "max_text_characters",
    "timeout_seconds",
}


@dataclass(frozen=True)
class CheckResult:
    """One environment fact and whether it blocks the V0.1 workflow."""

    label: str
    status: str
    detail: str
    required: bool = True


def _version_parts(value: str) -> tuple[int, ...]:
    """Extract the numeric release prefix from an installed version."""
    match = re.match(r"^(\d+(?:\.\d+)*)", value)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def _dependency_result(
    distribution: str,
    minimum: tuple[int, ...],
    maximum_major: int,
) -> CheckResult:
    """Check one required distribution without importing its runtime code."""
    try:
        version = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return CheckResult(
            f"Python dependency: {distribution}",
            "missing",
            f"Install {distribution}>={minimum[0]}.{minimum[1]}.",
        )

    parts = _version_parts(version)
    normalized_parts = parts + (0,) * max(0, len(minimum) - len(parts))
    compatible = (
        bool(parts)
        and normalized_parts[: len(minimum)] >= minimum
        and parts[0] < maximum_major
    )
    if not compatible:
        return CheckResult(
            f"Python dependency: {distribution}",
            "missing",
            (
                f"Installed {version}; V0.1 requires "
                f">={'.'.join(str(part) for part in minimum)},"
                f"<{maximum_major}."
            ),
        )

    return CheckResult(
        f"Python dependency: {distribution}",
        "available",
        f"Installed version {version}.",
    )


def _required_file(path: Path, label: str) -> CheckResult:
    """Check one required project file."""
    if path.is_file():
        return CheckResult(label, "available", str(path))
    return CheckResult(label, "missing", f"Expected file: {path}")


def _minimum_python_syntax_result(path: Path) -> CheckResult:
    """Parse the MCP server using the project's minimum Python grammar."""
    try:
        source = path.read_text(encoding="utf-8")
        ast.parse(
            source,
            filename=str(path),
            feature_version=MINIMUM_PYTHON[1],
        )
    except FileNotFoundError:
        return CheckResult(
            "Python 3.10 syntax compatibility",
            "missing",
            f"Expected file: {path}",
        )
    except (OSError, SyntaxError) as error:
        return CheckResult(
            "Python 3.10 syntax compatibility",
            "missing",
            f"The MCP server does not parse as Python 3.10: {error}",
        )
    return CheckResult(
        "Python 3.10 syntax compatibility",
        "available",
        f"{path.name} parses with the minimum supported grammar.",
    )


def _mcp_runtime_result(project_path: Path, uv_path: str) -> CheckResult:
    """Start the MCP server through the same uv entry point used by hosts."""
    command = [
        uv_path,
        "run",
        "--offline",
        str(project_path / "mcp_server.py"),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=project_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return CheckResult(
            "Alexandrian MCP runtime startup",
            "missing",
            f"The uv MCP startup check could not finish: {error}",
        )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if len(detail) > 1_000:
            detail = detail[:1_000].rstrip() + "..."
        return CheckResult(
            "Alexandrian MCP runtime startup",
            "missing",
            detail or f"uv exited with code {completed.returncode}.",
        )
    return CheckResult(
        "Alexandrian MCP runtime startup",
        "available",
        "uv started and cleanly closed the configured MCP server.",
    )


def _json_object(path: Path, label: str) -> tuple[CheckResult, dict | None]:
    """Read a required JSON object without modifying it."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return CheckResult(label, "missing", f"Expected file: {path}"), None
    except (json.JSONDecodeError, OSError) as error:
        return CheckResult(label, "missing", f"Cannot read valid JSON: {error}"), None

    if not isinstance(value, dict):
        return CheckResult(label, "missing", "The file must contain a JSON object."), None
    return CheckResult(label, "available", str(path)), value


def collect_environment_results(
    project_path: Path,
    check_runtime: bool = False,
) -> list[CheckResult]:
    """Collect V0.1 readiness facts without network or model calls."""
    project_path = project_path.resolve()
    results: list[CheckResult] = []

    python_ready = sys.version_info >= MINIMUM_PYTHON
    results.append(
        CheckResult(
            "Python",
            "available" if python_ready else "missing",
            (
                f"{sys.version.split()[0]} installed; "
                f"{MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer required."
            ),
        )
    )
    results.append(
        _required_file(project_path / "pyproject.toml", "Project metadata")
    )
    missing_license_files = [
        relative_path
        for relative_path in REQUIRED_LICENSE_FILES
        if not (project_path / relative_path).is_file()
    ]
    results.append(
        CheckResult(
            "License boundary",
            "missing" if missing_license_files else "available",
            (
                "Missing: " + ", ".join(missing_license_files)
                if missing_license_files
                else "MPL-2.0, CC BY-SA 4.0, and their scope map are present."
            ),
        )
    )

    for distribution, requirement in DEPENDENCY_REQUIREMENTS.items():
        minimum, maximum_major = requirement
        results.append(
            _dependency_result(distribution, minimum, maximum_major)
        )

    uv_path = shutil.which("uv")
    results.append(
        CheckResult(
            "uv",
            "available" if uv_path else "missing",
            uv_path or "Install uv to run the MCP server and project environment.",
        )
    )

    backend_paths = {
        backend: shutil.which(backend) for backend in SUPPORTED_BACKENDS
    }
    for backend, executable in backend_paths.items():
        results.append(
            CheckResult(
                f"Backend: {backend}",
                "available" if executable else "optional",
                executable or "Not installed; the other supported backend can be used.",
                required=False,
            )
        )
    available_backends = [
        backend for backend, executable in backend_paths.items() if executable
    ]
    results.append(
        CheckResult(
            "Supported backend",
            "available" if available_backends else "missing",
            (
                "Available: " + ", ".join(available_backends)
                if available_backends
                else "Install Codex or Claude."
            ),
        )
    )

    results.append(
        _required_file(project_path / "mcp_server.py", "Alexandrian MCP server")
    )
    results.append(
        _minimum_python_syntax_result(project_path / "mcp_server.py")
    )
    if check_runtime:
        results.append(
            (
                _mcp_runtime_result(project_path, uv_path)
                if uv_path
                else CheckResult(
                    "Alexandrian MCP runtime startup",
                    "missing",
                    "uv is unavailable, so the MCP server cannot be started.",
                )
            )
        )
    results.append(
        _required_file(project_path / ".mcp.json", "Claude MCP configuration")
    )
    results.append(
        _required_file(
            project_path / ".codex" / "config.example.toml",
            "Codex MCP configuration template",
        )
    )

    codex_config = project_path / ".codex" / "config.toml"
    results.append(
        CheckResult(
            "Local Codex MCP configuration",
            "available" if codex_config.is_file() else "optional",
            (
                str(codex_config)
                if codex_config.is_file()
                else "Copy config.example.toml when Codex will run the workflow."
            ),
            required=False,
        )
    )
    configured_hosts = []
    if backend_paths["claude"] and (project_path / ".mcp.json").is_file():
        configured_hosts.append("claude")
    if backend_paths["codex"] and codex_config.is_file():
        configured_hosts.append("codex")
    results.append(
        CheckResult(
            "Runnable full-workflow host",
            "available" if configured_hosts else "missing",
            (
                "Configured: " + ", ".join(configured_hosts)
                if configured_hosts
                else "Configure MCP for at least one installed backend."
            ),
        )
    )

    missing_skills = [
        skill
        for skill in REQUIRED_SKILLS
        if not (project_path / "skills" / skill / "SKILL.md").is_file()
    ]
    results.append(
        CheckResult(
            "Alexandrian skills",
            "missing" if missing_skills else "available",
            (
                "Missing: " + ", ".join(missing_skills)
                if missing_skills
                else f"All {len(REQUIRED_SKILLS)} required skills are present."
            ),
        )
    )

    schema_results = []
    for schema_name in REQUIRED_SCHEMAS:
        schema_result, schema = _json_object(
            project_path / "schemas" / schema_name,
            f"Schema: {schema_name}",
        )
        if schema is not None and "$schema" not in schema:
            schema_result = CheckResult(
                f"Schema: {schema_name}",
                "missing",
                "The JSON document has no $schema declaration.",
            )
        schema_results.append(schema_result)
    results.extend(schema_results)

    policy_result, policy = _json_object(
        project_path / "config" / "source_policy.default.json",
        "Default source policy",
    )
    if policy is not None:
        missing_policy_fields = sorted(REQUIRED_POLICY_FIELDS - policy.keys())
        if missing_policy_fields:
            policy_result = CheckResult(
                "Default source policy",
                "missing",
                "Missing fields: " + ", ".join(missing_policy_fields),
            )
    results.append(policy_result)

    data_path = project_path / "data"
    permission_target = data_path if data_path.is_dir() else project_path
    storage_ready = permission_target.is_dir() and os.access(
        permission_target,
        os.W_OK | os.X_OK,
    )
    results.append(
        CheckResult(
            "Local storage permissions",
            "available" if storage_ready else "missing",
            (
                f"{permission_target} can host local runtime data."
                if storage_ready
                else f"{permission_target} is not writable."
            ),
        )
    )

    return results


def print_environment_report(results: list[CheckResult]) -> int:
    """Print one report and return nonzero only for required failures."""
    print("Alexandrian Library V0.1 environment check\n")
    for result in results:
        print(f"[{result.status.upper()}] {result.label}: {result.detail}")

    failures = [
        result
        for result in results
        if result.required and result.status == "missing"
    ]
    if failures:
        print(f"\nResult: not ready ({len(failures)} required check(s) failed).")
        return 1

    print("\nResult: ready for the supported local V0.1 workflow.")
    return 0


def run_environment_check(project_path: Path) -> int:
    """Run and display the complete V0.1 environment and startup check."""
    return print_environment_report(
        collect_environment_results(project_path, check_runtime=True)
    )
