# Alexandrian Library

Alexandrian Library V1 is a local research application that captures durable
conversations, reviews their claims and evidence, and develops validated
material into verified books.

Routine research routing, plan acceptance, synthesis correction, finalization,
and bookshelf projection proceed automatically only when their deterministic
or independent audit gates pass. The user remains the authority for unknown
source access and for exceptional chapter holds that require an answer,
changed-condition retry, recovery authorization, or exact-draft acceptance.
These exceptions are reconstructed from durable files after restart. Unresolved
decisions remain unresolved; the system never records them as human decisions.
A blocking whole-book audit permits one correction and one final audit. There
is no third autonomous attempt.

The synthesis stage uses Alexandrian Plain Technical English v1, a
project-owned readability profile. It does not claim ASD-STE100 compliance and
does not perform new research.

## V1 environment

- Python 3.10 or newer
- `uv`
- Codex or Claude; only one is required
- Linux and WSL are the currently tested environments
- Ollama and a custom Pi harness are planned, not supported in V1

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

Start the complete local browser application:

```text
uv run python3 frontend_server.py --open
```

The frontend binds to `127.0.0.1:8765` by default. It exposes a versioned local
API, derives workflow checkpoints from canonical artifacts, verifies final book
and assembly hashes before reading, and resolves evidence by exact receipt ID.
It serializes all mutations because different books share the source policy,
knowledge graph, and bookshelf projection.

When a Windows browser cannot reach WSL loopback, bind only to the exact private
WSL address reported for the current process:

```text
uv run python3 frontend_server.py --host <exact-wsl-ipv4> --port 8765
```

The server rejects `0.0.0.0` and any address other than loopback or that exact
private WSL interface. Do not expose this V1 server to a public network.

If a chapter provider call ends after only part of its required MCP transition,
the Library lists the exact canonical artifact changes and pauses for review.
The user can keep the hold or reject the incomplete transition. Rejection does
not delete the artifacts, does not restore the consumed provider attempt, and
permits at most one retry after the user records a relevant changed condition.

Run the tests:

```text
uv run python3 -m unittest discover -s tests
```

Private conversations, research artifacts, books, settings, and source
approvals are stored under `data/`, which is excluded from Git.

The ordinary bookshelf contains only verified catalog entries. Add `?demo=1`
to the local frontend URL only when you intentionally want the four visual
reference volumes used to judge shelf composition.

## Licensing

Alexandrian Library uses a split license:

- Software, tests, schemas, configuration, and agent skills use MPL-2.0.
- Documentation, primers, and intentionally public example books use
  CC BY-SA 4.0.
- Project-authored cover art and its generation prompt use CC BY-SA 4.0.
- Private conversations and generated user books under `data/` are not
  automatically licensed by this repository.

See [LICENSE-SCOPE.md](LICENSE-SCOPE.md) for the exact artifact boundary and
third-party material rules.
