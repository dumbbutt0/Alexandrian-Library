"""Environment and runtime checks for the Alexandrian Library prototype."""

from __future__ import annotations

import ast
import asyncio
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


MINIMUM_PYTHON = (3, 10)
DEPENDENCY_REQUIREMENTS = {
    "mcp": ((1, 27), 2),
    "pypdf": ((5, 0), 7),
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
    "alexandrian-cover-designer",
)
REQUIRED_SCHEMAS = (
    "citation-contract.v1.schema.json",
    "cover-design.v1.schema.json",
    "evidence-locator.v1.schema.json",
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
    "source-document.v1.schema.json",
)
REQUIRED_LICENSE_FILES = (
    "LICENSE",
    "LICENSE-SCOPE.md",
    "LICENSES/CC-BY-SA-4.0.txt",
)
REQUIRED_APPLICATION_FILES = (
    "backend_selector.py",
    "workflow_service.py",
    "transition_runtime.py",
    "frontend_server.py",
    "frontend/index.html",
    "frontend/styles.css",
    "frontend/app.js",
    "citation_grounding.py",
    "source_document.py",
)
REQUIRED_POLICY_FIELDS = {
    "approved_domains",
    "allow_subdomains",
    "max_response_bytes",
    "max_text_characters",
    "timeout_seconds",
}
REQUIRED_MCP_TOOLS = {
    "fetch_research_source",
    "submit_chapter_research_source_plan",
    "fetch_chapter_research_source",
    "submit_research_review",
    "promote_review_to_knowledge_index",
    "submit_book_manifest",
    "prepare_chapter_context",
    "submit_chapter_research_packet",
    "initialize_chapter_workspace",
    "save_chapter_draft",
    "submit_chapter_assessment",
    "revise_chapter_workspace",
    "prepare_book_synthesis_context",
    "read_book_synthesis_attempt_input",
    "read_book_synthesis_audit_input",
    "initialize_book_synthesis",
    "save_book_synthesis_attempt",
    "submit_book_synthesis_audit",
    "submit_staged_synthesis_inventory_unit",
    "prepare_staged_synthesis_plan_shards",
    "submit_staged_synthesis_plan_shard",
    "submit_staged_synthesis_plan",
    "submit_staged_synthesis_plan_audit",
    "read_staged_synthesis_global_plan_audit_input",
    "submit_staged_synthesis_global_plan_audit",
    "save_staged_synthesis_edition_unit",
    "submit_staged_synthesis_audit_unit",
    "submit_staged_synthesis_global_audit",
}
CODEX_READINESS_LABEL = "Codex project readiness"
CODEX_NOT_INSTALLED = "codex_not_installed"
CODEX_PROJECT_CONFIG_MISSING = "codex_project_config_missing"
CODEX_PROJECT_UNTRUSTED = "codex_project_untrusted"
CODEX_MCP_NOT_EFFECTIVE = "codex_mcp_not_effective"
CODEX_MCP_SERVER_UNSTARTABLE = "codex_mcp_server_unstartable"
CODEX_READY = "codex_ready"


@dataclass(frozen=True)
class CheckResult:
    """One environment fact and whether it blocks the V1 workflow."""

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
                f"Installed {version}; V1 requires "
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


async def _mcp_protocol_probe(transport: dict) -> list[str]:
    """Initialize the effective stdio server and request its tool inventory."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    environment = os.environ.copy()
    environment.update(transport.get("env", {}))
    parameters = StdioServerParameters(
        command=transport["command"],
        args=transport.get("args", []),
        env=environment,
        cwd=transport["cwd"],
    )
    with open(os.devnull, "w", encoding="utf-8") as error_log:
        async with stdio_client(
            parameters,
            errlog=error_log,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = await session.list_tools()
                return [tool.name for tool in response.tools]


def _mcp_tool_inventory_result(transport: dict) -> CheckResult:
    """Verify MCP initialization and tools through the effective transport."""
    try:
        tool_names = asyncio.run(
            asyncio.wait_for(
                _mcp_protocol_probe(transport),
                timeout=15,
            )
        )
    except Exception as error:
        return CheckResult(
            "Alexandrian MCP tool inventory",
            "missing",
            f"The MCP initialize/tools-list check could not finish: {error}",
        )
    missing_tools = sorted(REQUIRED_MCP_TOOLS - set(tool_names))
    if missing_tools:
        return CheckResult(
            "Alexandrian MCP tool inventory",
            "missing",
            "Missing tools: " + ", ".join(missing_tools),
        )
    return CheckResult(
        "Alexandrian MCP tool inventory",
        "available",
        f"All {len(REQUIRED_MCP_TOOLS)} required tools are registered.",
    )


def _toml_document(path: Path) -> tuple[dict | None, str | None]:
    """Read one TOML document without exposing its contents in errors."""
    try:
        with path.open("rb") as source:
            document = tomllib.load(source)
    except FileNotFoundError:
        return None, f"Expected file: {path}"
    except (OSError, tomllib.TOMLDecodeError) as error:
        return None, f"Cannot read valid TOML at {path}: {error}"
    if not isinstance(document, dict):
        return None, f"The TOML document at {path} must be a table."
    return document, None


def _codex_user_config_path() -> Path:
    """Resolve the user-level Codex configuration without changing it."""
    configured_home = os.environ.get("CODEX_HOME")
    if configured_home:
        return Path(configured_home).expanduser().resolve() / "config.toml"
    return Path.home() / ".codex" / "config.toml"


def _exact_worktree_is_trusted(
    project_path: Path,
    user_config_path: Path,
) -> tuple[bool, str]:
    """Check the explicit user-owned trust record for one exact worktree."""
    document, error = _toml_document(user_config_path)
    if document is None:
        return False, error or "The user Codex configuration is unavailable."
    projects = document.get("projects")
    if not isinstance(projects, dict):
        return False, (
            f"No project trust entries exist in {user_config_path}."
        )
    for configured_path, settings in projects.items():
        if not isinstance(configured_path, str) or not isinstance(settings, dict):
            continue
        try:
            resolved_path = Path(configured_path).expanduser().resolve()
        except OSError:
            continue
        if resolved_path != project_path:
            continue
        if settings.get("trust_level") == "trusted":
            return True, f"Exact trusted worktree: {project_path}"
        return False, (
            f"The exact worktree is not trusted in {user_config_path}."
        )
    return False, (
        f"No explicit trust entry exists for {project_path} in "
        f"{user_config_path}."
    )


def _resolved_executable(value: object, cwd: Path) -> Path | None:
    """Resolve one configured executable without running it."""
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value).expanduser()
    if candidate.is_absolute() or candidate.parent != Path("."):
        if not candidate.is_absolute():
            candidate = cwd / candidate
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        return resolved if resolved.is_file() else None
    found = shutil.which(value)
    if found is None:
        return None
    try:
        return Path(found).resolve(strict=True)
    except OSError:
        return None


def _resolved_mcp_script(args: object, cwd: Path) -> Path | None:
    """Resolve the configured Alexandrian MCP script from launcher arguments."""
    if not isinstance(args, list) or any(
        not isinstance(argument, str) for argument in args
    ):
        return None
    matches = [
        Path(argument).expanduser()
        for argument in args
        if Path(argument).name == "mcp_server.py"
    ]
    if len(matches) != 1:
        return None
    candidate = matches[0]
    if not candidate.is_absolute():
        candidate = cwd / candidate
    try:
        return candidate.resolve(strict=True)
    except OSError:
        return None


def _validate_codex_server_identity(
    server: object,
    project_path: Path,
) -> tuple[bool, str]:
    """Validate one configured Alexandrian MCP identity for this worktree."""
    if not isinstance(server, dict):
        return False, "The Alexandrian MCP configuration must be a table."
    cwd_value = server.get("cwd")
    if not isinstance(cwd_value, str):
        return False, "The Alexandrian MCP working directory is missing."
    try:
        server_cwd = Path(cwd_value).expanduser().resolve(strict=True)
    except OSError:
        return False, "The Alexandrian MCP working directory does not exist."
    if server_cwd != project_path:
        return False, (
            "The Alexandrian MCP working directory points at another clone."
        )
    if _resolved_executable(server.get("command"), server_cwd) is None:
        return False, "The Alexandrian MCP launcher is unavailable."
    if (
        _resolved_mcp_script(server.get("args"), server_cwd)
        != project_path / "mcp_server.py"
    ):
        return False, "The Alexandrian MCP script points at another clone."
    environment = server.get("env")
    if not isinstance(environment, dict):
        return False, "The Alexandrian MCP environment is missing."
    project_value = environment.get("ALEXANDRIAN_PROJECT_PATH")
    if not isinstance(project_value, str):
        return False, "ALEXANDRIAN_PROJECT_PATH is missing."
    try:
        configured_project = (
            Path(project_value).expanduser().resolve(strict=True)
        )
    except OSError:
        return False, "ALEXANDRIAN_PROJECT_PATH does not exist."
    if configured_project != project_path:
        return False, "ALEXANDRIAN_PROJECT_PATH points at another clone."
    return True, f"Alexandrian MCP identity matches {project_path}."


def _project_git_root(project_path: Path) -> tuple[Path | None, str]:
    """Resolve the Git root used for Codex project configuration discovery."""
    git_path = shutil.which("git")
    if git_path is None:
        return None, "Git is unavailable, so the project root cannot be verified."
    try:
        completed = subprocess.run(
            [git_path, "-C", str(project_path), "rev-parse", "--show-toplevel"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"The Git project root check could not finish: {error}"
    if completed.returncode != 0:
        return None, (
            completed.stderr.strip()
            or "Git could not resolve the project root."
        )
    try:
        return Path(completed.stdout.strip()).resolve(strict=True), ""
    except OSError:
        return None, "Git returned a project root that does not exist."


def _effective_codex_mcp_server(
    codex_path: str,
    project_path: Path,
) -> tuple[dict | None, str]:
    """Ask Codex for the MCP servers effective in the exact worktree."""
    try:
        completed = subprocess.run(
            [codex_path, "mcp", "list", "--json"],
            cwd=project_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"Codex MCP discovery could not finish: {error}"
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if len(detail) > 1_000:
            detail = detail[:1_000].rstrip() + "..."
        return None, detail or "Codex MCP discovery failed."
    try:
        servers = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        return None, f"Codex MCP discovery returned invalid JSON: {error}"
    if not isinstance(servers, list):
        return None, "Codex MCP discovery must return a JSON array."
    matches = [
        server
        for server in servers
        if isinstance(server, dict)
        and server.get("name") == "alexandrian_library"
    ]
    if len(matches) != 1:
        return None, (
            "Codex did not expose exactly one alexandrian_library MCP server."
        )
    server = matches[0]
    if not server.get("enabled"):
        return None, "Codex reports the Alexandrian MCP server as disabled."
    transport = server.get("transport")
    if not isinstance(transport, dict) or transport.get("type") != "stdio":
        return None, "Codex reports an unsupported Alexandrian MCP transport."
    return transport, "Codex exposes the project Alexandrian MCP server."


def _codex_readiness_result(
    project_path: Path,
    codex_path: str | None,
    check_runtime: bool,
) -> CheckResult:
    """Derive one explicit Codex readiness state without changing trust."""
    if codex_path is None:
        return CheckResult(
            CODEX_READINESS_LABEL,
            "optional",
            f"{CODEX_NOT_INSTALLED}: Install Codex to use this host.",
            required=False,
        )

    config_path = project_path / ".codex" / "config.toml"
    if not config_path.is_file():
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            (
                f"{CODEX_PROJECT_CONFIG_MISSING}: Inspect and copy "
                f"{project_path / '.codex' / 'config.example.toml'} to "
                f"{config_path}."
            ),
            required=False,
        )

    git_root, git_error = _project_git_root(project_path)
    if git_root != project_path:
        detail = (
            git_error
            or "The resolved Git root does not match the Library worktree."
        )
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            f"{CODEX_MCP_NOT_EFFECTIVE}: {detail}",
            required=False,
        )

    document, parse_error = _toml_document(config_path)
    if document is None:
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            f"{CODEX_MCP_NOT_EFFECTIVE}: {parse_error}",
            required=False,
        )
    servers = document.get("mcp_servers")
    server = (
        servers.get("alexandrian_library")
        if isinstance(servers, dict)
        else None
    )
    valid_identity, identity_detail = _validate_codex_server_identity(
        server,
        project_path,
    )
    if (
        not valid_identity
        or not isinstance(server, dict)
        or server.get("required") is not True
    ):
        if valid_identity:
            identity_detail = (
                "The project MCP server must set required = true."
            )
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            f"{CODEX_MCP_NOT_EFFECTIVE}: {identity_detail}",
            required=False,
        )

    trusted, trust_detail = _exact_worktree_is_trusted(
        project_path,
        _codex_user_config_path(),
    )
    if not trusted:
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            (
                f"{CODEX_PROJECT_UNTRUSTED}: {trust_detail} Inspect "
                f"{config_path}, then launch Codex from {project_path} and "
                "approve trust."
            ),
            required=False,
        )

    effective_server, effective_detail = _effective_codex_mcp_server(
        codex_path,
        project_path,
    )
    effective_identity, effective_identity_detail = (
        _validate_codex_server_identity(
            effective_server,
            project_path,
        )
    )
    if effective_server is None or not effective_identity:
        detail = (
            effective_detail
            if effective_server is None
            else effective_identity_detail
        )
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            f"{CODEX_MCP_NOT_EFFECTIVE}: {detail}",
            required=False,
        )

    if not check_runtime:
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            (
                f"{CODEX_MCP_SERVER_UNSTARTABLE}: MCP initialization and "
                "tool inventory were not checked."
            ),
            required=False,
        )

    inventory_result = _mcp_tool_inventory_result(effective_server)
    if inventory_result.status != "available":
        return CheckResult(
            CODEX_READINESS_LABEL,
            "missing",
            f"{CODEX_MCP_SERVER_UNSTARTABLE}: {inventory_result.detail}",
            required=False,
        )

    return CheckResult(
        CODEX_READINESS_LABEL,
        "available",
        (
            f"{CODEX_READY}: Exact worktree trust, effective MCP identity, "
            "server startup, and required tools are verified."
        ),
        required=False,
    )


def codex_project_readiness(
    project_path: Path,
    *,
    check_runtime: bool = True,
) -> CheckResult:
    """Verify effective Codex readiness for one exact project worktree."""
    return _codex_readiness_result(
        project_path.resolve(),
        shutil.which("codex"),
        check_runtime,
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
    """Collect V1 readiness facts without network or model calls."""
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
    for relative_path in REQUIRED_APPLICATION_FILES:
        results.append(
            _required_file(
                project_path / relative_path,
                f"Application file: {relative_path}",
            )
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
    codex_readiness = _codex_readiness_result(
        project_path,
        backend_paths["codex"],
        check_runtime,
    )
    results.append(codex_readiness)

    configured_hosts = []
    if backend_paths["claude"] and (project_path / ".mcp.json").is_file():
        configured_hosts.append("claude")
    if codex_readiness.detail.startswith(f"{CODEX_READY}:"):
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
    print("Alexandrian Library V1 environment check\n")
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

    print("\nResult: ready for the supported local V1 workflow.")
    return 0


def run_environment_check(project_path: Path) -> int:
    """Run and display the complete V1 environment and startup check."""
    return print_environment_report(
        collect_environment_results(project_path, check_runtime=True)
    )
