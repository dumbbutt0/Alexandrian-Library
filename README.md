# Alexandrian Library

Alexandrian Library V1 is a local research application that captures durable
conversations, reviews their claims and evidence, and develops validated
material into verified books.

Routine research routing, plan acceptance, synthesis correction, finalization,
provenance-bound cover design, and bookshelf projection proceed automatically
only when their deterministic or independent audit gates pass. The user remains
the authority for unknown
source access and for exceptional chapter holds that require an answer,
changed-condition retry, recovery authorization, or exact-draft acceptance.
These exceptions are reconstructed from durable files after restart. Unresolved
decisions remain unresolved; the system never records them as human decisions.
A blocking whole-book audit permits one correction and one final audit. There
is no third autonomous attempt.

The synthesis stage targets the Alexandrian Narrative Research Book standard:
engaging nonfiction whose claims remain inspectable as research artifacts.
The conversation supplies the narrative engine—starting belief, tension,
discovery, reversal, bounded synthesis, and the next open question. Technical
definitions, procedures, interface copy, and summaries may still use
ASD-STE100-inspired clarity, but the main chapters do not claim ASD-STE100
compliance and do not use controlled technical prose as their primary voice.
The complete reading-layer and inspection-layer contract is documented in
[`docs/alexandrian-narrative-research-standard.md`](docs/alexandrian-narrative-research-standard.md).

Newly fetched sources also receive a deterministic, hash-bound structured
document. HTML retains stable content blocks, textual PDFs retain real page
boundaries, and exact evidence excerpts resolve to verified locators. Existing
V1 receipts remain valid without fabricated locators. The evidence boundary and
future retrieval sequence are documented in
[`docs/evidence-and-retrieval-strategy.md`](docs/evidence-and-retrieval-strategy.md).

New chapters and synthesized editions must cite every approved
evidence-backed claim inline. Internal receipt markers render as unobtrusive
numbered citations; opening a citation reveals the exact passage, verified
location, finding, limitations, and receipt. Contradictions remain visibly
marked instead of being flattened into ordinary prose. When evidence is
insufficient, the workflow preserves the uncertainty and keeps source access
behind the existing Reject, Allow once, and Allow site decision boundary.

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
Conversation turns run as visible background jobs, so one provider may finish
writing and save its canonical turn while another inquiry is opened. Codex and
Claude can be switched between turns without splitting the durable transcript.
Workflow advances still share one global mutation lane because books use the
same source policy, knowledge graph, and bookshelf projection. The worktable
shows named phases, elapsed time, and phase progress; it does not claim an exact
completion percentage that the provider cannot report.

After final assembly, the cover designer receives the immutable book and
assembly hashes. It must choose an exact quotation from the final book and
submit a title-free visual brief through the Alexandrian MCP tool. A failed or
unavailable art provider falls back to deterministic archive artwork, so cover
presentation cannot create a new workflow hold. The shelf catalog records and
re-verifies the design and image hashes before serving the cover.

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

The shelf room uses the Archive Sentinel artwork as its frontend identity: a
half-paper, half-stained-glass eye watches over a suspended wood shelf supported
by illustrated hands. Book covers use their own motifs and do not repeat the
Sentinel composition.

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
