# Alexandrian V1 performance runtime

This release reduces waiting without weakening canonical workflow authority.
The durable artifact graph remains the source of truth after every restart.

## Safe defaults

- At most two dependency-ready chapters run at the same time.
- At most three provider calls run globally.
- A chapter that depends on another chapter waits for the dependency's exact
  `chapter_complete` run state.
- Candidate assembly and the final claim audit still wait for every included
  chapter.
- Intermediate chapter evaluations compare the changed state with the prior
  assessment. Final-pass evaluations always run as full audits.
- Normal chapter call ceilings are sized from approved claim and open-evidence
  counts. Recovery and human-authorized closeout ceilings remain separate.
- An exact URL can reuse a hash-verified normalized source only inside the same
  book. Reuse creates a new assessment-bound receipt and source-document hash;
  it never shares authority across books.
- When every URL in a source plan is already domain-approved and uncached, the
  network reads run concurrently. Receipts and session updates are still
  committed serially in immutable source-plan order. Any permission-sensitive
  batch remains serial and stops at the first human decision.

## Provider dispatcher

The dispatcher labels each call `fast`, `standard`, or `deep`. It resumes the
existing provider session when the provider exposes a safe session identifier.
Optional model overrides are opt-in environment values:

- `ALEXANDRIAN_CODEX_FAST_MODEL`
- `ALEXANDRIAN_CODEX_STANDARD_MODEL`
- `ALEXANDRIAN_CODEX_DEEP_MODEL`
- `ALEXANDRIAN_CLAUDE_FAST_MODEL`
- `ALEXANDRIAN_CLAUDE_STANDARD_MODEL`
- `ALEXANDRIAN_CLAUDE_DEEP_MODEL`

If no override is present, the configured provider default is unchanged. This
is the fallback for CLIs that do not expose a compatible warm-process mode.

Provider-bound state is encoded as compact JSON. The same canonical paths,
identities, and hashes are retained; only display whitespace is omitted. On a
safe provider-session resume, unchanged role instructions are replaced by a
short reuse directive. The subprocess transport remains the recovery fallback
until a provider exposes a locally verified stable worker protocol.

Concurrency can be reduced for a small machine with
`ALEXANDRIAN_PROVIDER_CONCURRENCY` and `ALEXANDRIAN_CHAPTER_PARALLELISM`.
Values are clamped to 1–8 and 1–4 respectively.

## Measurement

Every provider call appends prompt-free timing to
`data/performance/provider_calls.jsonl`. Records include queue time, time to
first observable activity, active time, total time, prompt/output byte counts,
task tier, success state, and whether a saved provider session was resumed.
Schema 1.1 also records the canonical workflow identifiers and whether the
call used a cold or resumed subprocess transport. Prompts, responses, URLs,
and book text are not recorded. Mocked test calls aimed at the live repository
are excluded; telemetry tests use a temporary project path.

Provider artifact reconciliation hashes only the active chapter's context,
workspace, drafts, assessments, and research artifacts during live chapter
transitions. The legacy broad scan remains available for old restart records.
This reduces filesystem work and prevents a sibling chapter's valid write from
being mistaken for recovery evidence.

The worktable shows the latest durable transition event, provider, transition
kind, attempt number, and elapsed workflow time. These observations never
change the authoritative workflow revision.

## Failure behavior

Provider inactivity and absolute deadlines still terminate the exact provider
process group. Parallel waves stop scheduling new chapters when any chapter
reaches a human/source/blocker hold. Already-finished sibling artifacts remain
valid and are discovered on resume. Disabling parallelism restores the prior
sequential chapter behavior without migrating artifacts.
