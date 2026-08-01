# Frontend V1

This is a dependency-free, local-first shell over the existing deterministic
bookshelf contract.

Run it from the repository root:

```bash
python3 frontend_server.py --open
```

The server binds to `127.0.0.1:8765` by default. It does not rebuild the
bookshelf or write workflow state.

## Included

- A shelf-first library populated from `data/bookshelf/catalog.json`
- A continuous shelf with one cover always presented beside the remaining
  spines
- Six-phase previous/next navigation: retreat the current cover, turn it
  edge-on, shelve it, extract the next spine, turn the next cover forward,
  and settle it into the fixed presentation position
- Click-gated inspection, so browsing changes the presented cover while free
  3D spinning begins only after the reader activates that book
- Mint-style deferred focus: clicking a different visible spine completes the
  full shelf exchange first, then enters inspection; arrows and catalog ticks
  only change the presented volume
- Bounded, content-informed spine-height normalization that preserves subtle
  physical variety without arbitrary index-based jumps
- Layered front board, authored spine, page block, fore-edge, floor shadow,
  and a back panel carrying the central inquiry and verified edition metadata
  so the selected volume reads as one complete physical book
- Unlimited horizontal drag-to-spin inspection with a bounded 18-degree
  vertical tilt; a click on the displayed volume returns it to the shelf
- Click, scroll, arrow-key, previous/next, and catalog-tick selection controls
- An original holy-cyber cover for the first finalized volume
- Native cover-image rendering, pixel-aligned resting geometry, and
  overlay-based lighting so fine artwork survives the 3D presentation
- A recessed cabinet plane, modeled shelf lip, corner fittings, and cast
  shadows that give the dark archive a physical room boundary
- Hash verification before a finalized book is opened
- A responsive two-page reader with page buttons and keyboard controls
- A plain continuous-reading option and generated table of contents
- A visible new-inquiry worktable that truthfully reports that backend handoff
  is not wired yet

## Visual reference volumes

The current frontend prototype appends four clearly labeled `REF` volumes in
the browser so the shelf, selection, and generic-cover systems can be judged
with a realistic row of books. They are visual fixtures only: they are not
written into the backend catalog, have no book artifacts, cannot be opened in
the reader, and are counted separately from verified volumes.

## Boundary

The CSS shelf is the zero-dependency V1 renderer. Its motion model follows the
same important separation used by Mint's MIT-licensed
[Complete Shelf](https://github.com/mintdotgg/mint-playground/tree/main/experiences/complete-shelf):
a volume has shelved, rotation-lane, presented, and inspection poses, with
rotation occurring only after it clears the row. This implementation is an
original DOM/CSS adaptation for Inquirion's catalog and reader contracts. It
establishes navigation, selection, reader, and catalog behavior while the
backend stabilizes. A future Three.js renderer can replace the scene without
changing those contracts.

The first cover artwork is stored at
`frontend/assets/branches-cover-holy-cyber-v3.png`. Its exact title and edition
remain live HTML rather than baked image text.
