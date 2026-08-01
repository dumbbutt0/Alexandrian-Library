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
- Keyboard, pointer, scroll, previous/next, and catalog-tick navigation
- Book and assembly hash verification before a final book opens
- A responsive two-page reader and continuous plain view
- Exact evidence receipt markers and a verified provenance panel
- Semantic tables, generated contents, printing, and browser-local reading
  position restoration
- A provider-aware worktable for creating, continuing, finishing, resuming,
  and advancing canonical workflows
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

The project-authored cover artwork and generation prompt are stored under
`frontend/assets/`. The title and edition remain live HTML rather than baked
image text.

The cover asset and its generation prompt use CC BY-SA 4.0. Frontend source
code uses MPL-2.0. See `LICENSE-SCOPE.md` at the repository root.
