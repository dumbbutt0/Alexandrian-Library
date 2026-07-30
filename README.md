# Alexandrian Library

Alexandrian Library is a local research prototype that captures durable
conversations, reviews their claims and evidence, and develops approved
material into books with human checkpoints.

Before final editorial approval, the Library performs one bounded whole-book
synthesis and an independent claim-preservation audit. A blocking audit permits
one correction and one final audit. There is no third autonomous attempt.

The synthesis stage uses Alexandrian Plain Technical English v1, a
project-owned readability profile. It does not claim ASD-STE100 compliance and
does not perform new research.

## V0.1 environment

- Python 3.10 or newer
- `uv`
- Codex or Claude; only one is required
- Linux and WSL are the currently tested environments
- Ollama and a custom Pi harness are planned, not supported in V0.1

Create the project environment:

```text
uv sync
```

Claude reads the repository's `.mcp.json`.

Codex project configuration is active only after you explicitly trust the
exact worktree. A checked-in configuration is a proposal, not proof that Codex
loaded it:

1. Clone the repository.
2. Copy `.codex/config.example.toml` to `.codex/config.toml`.
3. Inspect `.codex/config.toml`, replace both placeholder paths with the
   absolute path of this clone, and keep this local file private.
4. Start Codex from the repository root.
5. Review the trust prompt and explicitly trust this exact worktree if you
   accept its project-controlled configuration.
6. Run the Library environment check.
7. Confirm that `Codex project readiness` reports `codex_ready`.

You can inspect the MCP servers Codex actually loaded from the repository root:

```text
codex mcp list
```

The Library never adds or changes a Codex trust entry for you. A new clone at a
different absolute path requires its own trust decision.

Codex readiness uses explicit states:

- `codex_not_installed`
- `codex_project_config_missing`
- `codex_project_untrusted`
- `codex_mcp_not_effective`
- `codex_mcp_server_unstartable`
- `codex_ready`

Check the complete local environment before starting:

```text
python3 backend_selector.py --check
```

The check inspects local versions, programs, project files, effective host
configuration, schemas, skills, source policy, and storage permissions. For
Codex, it runs `codex mcp list --json`, initializes the effective Alexandrian
MCP server, and verifies its required tool inventory. It does not call a model,
access research-source domains, alter project trust, or create durable library
data.

Start the terminal workflow:

```text
uv run python3 backend_selector.py
```

Run the tests:

```text
uv run python3 -m unittest discover -s tests
```

Private conversations, research artifacts, books, settings, and source
approvals are stored under `data/`, which is excluded from Git.

## Licensing

Alexandrian Library uses a split license:

- Software, tests, schemas, configuration, and agent skills use MPL-2.0.
- Documentation, primers, and intentionally public example books use
  CC BY-SA 4.0.
- Private conversations and generated user books under `data/` are not
  automatically licensed by this repository.

See [LICENSE-SCOPE.md](LICENSE-SCOPE.md) for the exact artifact boundary and
third-party material rules.
