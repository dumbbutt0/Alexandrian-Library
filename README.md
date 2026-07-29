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

Claude reads the repository's `.mcp.json`. For Codex, copy
`.codex/config.example.toml` to `.codex/config.toml`, replace its placeholder
clone paths, and keep the resulting local file private.

Check the complete local environment before starting:

```text
python3 backend_selector.py --check
```

The check inspects local versions, programs, project files, host
configuration, schemas, skills, source policy, and storage permissions. It
does not start a model or MCP server, access the network, or create durable
library data.

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
