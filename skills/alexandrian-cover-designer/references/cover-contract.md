# Cover submission contract

Submit one `CoverDesignProposal` through `submit_book_cover_design`.

Required fields:

- `book_id`: exact wrapper-supplied finalized book ID.
- `assembly_sha256`: SHA-256 of the supplied `assembly.json` file.
- `core_question`: the final manifest's central question, copied exactly.
- `question_tension`: 40–1200 characters explaining why the question remains
  unresolved and visually productive.
- `dominant_symbol`: one 3–160 character concrete symbol also present exactly
  in `visual_motifs`.
- `diversity_statement`: 40–1200 characters explaining how this design avoids
  the dominant objects and composition of the two most recent covers.
- `quote`: an exact 20–180 character substring of the final `book.md`.
- `concept`: a concise explanation of the book-specific metaphor.
- `visual_motifs`: three to eight unique concrete motifs.
- `palette`: four to eight unique six-digit hexadecimal colors.
- `image_prompt`: a 200–5000 character title-free portrait-art prompt.
  It must explicitly request collage, illustration, painted/photomontage,
  stained-glass, torn-paper, or another layered material treatment. It must
  not request line art, outline-only geometry, wireframes, or thin-line art.
- `fallback_style`: one of `archive_geometry`, `evidence_threads`, or
  `stained_glass_diagram`.

The tool rejects extra fields, stale assembly hashes, changed core questions,
recently repeated dominant symbols, invented quotations,
unsafe colors, duplicate motifs, non-graphical art direction, and a second
submission. It writes the canonical design and a deterministic filled,
textured SVG fallback. Do not call filesystem or network tools.
