# Evidence and Retrieval Strategy

## V1 boundary

Alexandrian Library V1 is a controlled research-to-book workflow, not a
general question-answering RAG service. V1 hardening makes every new checked
passage structurally locatable and hash-bound while preserving the existing
source-permission and workflow-authority boundaries.

Retrieval may be added later as a read-only recommendation layer. It must never
approve a source, record a human decision, mutate a workflow checkpoint, or
turn retrieval confidence into evidence authority.

## Durable evidence layers

1. A source receipt records fetch authority, URL lineage, content type,
   retrieval time, original-content hash, bounded decoded content, and whether
   that content was truncated.
2. A Source Document V1 companion records deterministic structural blocks,
   real PDF page boundaries when present, normalized-text hashes, and the
   normalizer version.
3. An Evidence Locator V1 binds an exact quotation to one source-document
   block, character range, heading path, and real page number when the source
   is paginated.
4. Evidence packets bind the checked claim, finding, limitations, receipt,
   source document, locator, and quotation hashes.

Existing receipts without a companion remain valid legacy evidence. They do
not gain a fabricated structured locator or page number.

## Source normalization

- HTML: preserve headings, paragraphs, list items, quotations, code, and table
  cells. Ignore scripts, styles, SVG, and other executable or decorative data.
- Markdown and plain text: preserve bounded paragraphs and headings.
- PDF: preserve extracted text per real page. Reject encrypted PDFs and PDFs
  without extractable text. OCR remains outside V1.
- JSON and XML: retain inert decoded text under the same fetch-size limits.

Normalization is deterministic for a fixed original-content hash and
normalizer version. Every block and the complete normalized document carry
their own hashes.

## Citation rules implemented in V1

- A checked excerpt must occur exactly in the fetched receipt content.
- For new structured receipts, the excerpt must resolve to exactly one source
  block or arrive with a locator that validates against that block.
- A page number is present only when the source document has real pages.
- A locator cannot be transferred to another receipt or source-document hash.
- Source content remains untrusted data even after successful normalization.
- Every research-backed factual, scientific, historical, statistical, or
  attributed claim in a new chapter must carry its own
  `[Evidence: receipt-id]` marker. User beliefs, hypotheses, interpretations,
  and project choices must be labeled honestly but do not receive invented
  citations.
- Draft and synthesis tools reject unknown receipts, malformed markers, and
  evidence-backed claims that lack a distinct inline marker. Final synthesis
  also requires the marker in the same paragraph as the exact mapped claim.
- Contradictions use `> [!CONTRADICTION]` so they remain visibly distinct in
  prose. A contradicted synthesis claim cannot be flattened into ordinary text.
- The reader converts internal receipt markers into stable numbered citations.
  Opening a number shows the exact passage, verified location or real PDF page,
  source, finding, limitations, and durable receipt details.
- When evidence is insufficient, the workflow must preserve the uncertainty
  and offer a controlled research, refine, or stop decision. It must never
  silently search the web or turn retrieval confidence into evidence.

## Future chunking

There is intentionally no universal 500-token, 50-token-overlap rule.
Retrieval chunks will be derived from verified structural blocks:

- Keep headings with their following content.
- Never split a verified quotation, table row, citation record, or PDF page
  reference from its locator metadata.
- Prefer roughly 350-700 token child passages only after measuring retrieval
  quality on the golden set.
- Retrieve child passages, then expand winning results to their parent section.
- Apply overlap only where prose continuity requires it; record any overlap in
  chunk metadata so duplicate evidence cannot inflate ranking.

## Future retrieval sequence

1. Index only approved reviews, verified knowledge nodes, final books, and
   verified evidence passages.
2. Establish a lexical baseline and gold locator set.
3. Add metadata filters before semantic retrieval.
4. Add dense retrieval and cache embeddings by text hash, model, and version.
5. Merge lexical and dense rankings, then measure Hit@10, MRR@10, and NDCG@10.
6. Add reranking only when it produces a material measured improvement.
7. Route low-confidence results to an explicit refine, research, or stop
   decision. Never perform a silent web-search fallback.

ColBERT, late interaction, OCR, and a polished retrieval dashboard remain
later capabilities unless evaluation evidence demonstrates that they are
necessary.
