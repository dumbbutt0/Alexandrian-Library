# Frontend V1

This dependency-free local frontend is the public browser surface for the
durable workflow service and verified bookshelf.

Run it from the repository root:

```text
uv run python3 frontend_server.py --open
```

The server binds to `127.0.0.1:8765` by default. It writes only through the
canonical workflow service. Browser state is never workflow authority.

## Included

- A valid empty shelf for a clean clone and a verified shelf populated from
  `data/bookshelf/catalog.json`
- One persistent 3D book object per catalog entry, with shelf, focus, and
  inspection poses
- A catalog-driven cover for each finalized book, bound to the exact book and
  assembly hashes with an exact in-book quotation
- Keyboard, pointer, scroll, previous/next, and catalog-tick navigation
- Book and assembly hash verification before a final book opens
- A responsive two-page reader and continuous plain view
- Exact evidence receipt markers and a verified provenance panel
- Semantic tables, generated contents, printing/PDF, EPUB and Markdown
  downloads, a portable evidence manifest, and browser-local reading position
  restoration
- A provider-aware worktable for creating, continuing, finishing, resuming,
  and advancing canonical workflows
- A canonical Finish-readiness contract that explains current holds, bounded
  automation, human-only decisions, and recovery boundaries before mutation
- A compact durable timeline derived from append-only transition events
- Guided and low-interruption entry modes; low-interruption runs record the
  same inquiry-scoped Finish guardrails used by the one-click completion path
- Restart-safe source, chapter-input, retry, recovery, and terminal holds
- One global mutation lock for shared knowledge, source-policy, and shelf state

## Visual reference volumes

The ordinary V1 shelf shows only verified catalog entries. Add `?demo=1` to the
local URL to append four clearly labeled `REF` volumes for visual evaluation.
They are not written into the backend catalog, have no book artifacts, cannot
be opened, and are counted separately from verified volumes.

## Boundary

The CSS shelf is the zero-dependency V1 renderer. Its motion model follows the
same important separation used by Mint's MIT-licensed
[Complete Shelf](https://github.com/mintdotgg/mint-playground/tree/main/experiences/complete-shelf):
a volume has shelved, presented, and inspection poses. This implementation is
an original DOM and CSS adaptation for the Library's catalog and reader
contracts. A future Three.js renderer can replace the scene without changing
those contracts.

The project-authored cover artworks and generation prompts are stored under
`frontend/assets/`. The title and edition remain live HTML rather than baked
image text. The Archive Sentinel scene is the room identity; individual books
receive distinct title-free art through the post-assembly cover-design stage.
If generated art is unavailable, a deterministic cover is served without
blocking the workflow.

The cover assets and their generation prompts use CC BY-SA 4.0. Frontend source
code uses MPL-2.0. See `LICENSE-SCOPE.md` at the repository root.
