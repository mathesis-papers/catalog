# Exact-version paper materials

The ordinary hourly publisher still archives eligible public manuscripts and
refreshes the catalog. The material adapter extends that workflow after the
core publication and readback. It does not run another review, deposit a DOI,
register a formalization request, certify a proof or award credit.

A material job uses the existing `mathesis.paper-production-envelope.v1`,
Mathesis-owned production skill, template fill interface, fixed serial renderer,
EPUB checks, browser checks and product checks. Visualization is assessed;
`not_useful` with a reason is a completed assessment. Reading, slides and video
have separate publication states. A missing video remains pending.

## Durable ownership and credentials

`mathesis-papers/production-state` is private. It holds exact mutation intents,
original CMA operation IDs, immutable source packs, build receipts and public
readback receipts in Git transactions. It is operational state, not another
mathematical request queue. Work/version/source identity owns one model job;
a template update cannot silently start a second authoring attempt. Existing
manual cohort versions are reserved in `production.json`; a later manuscript
version becomes a new candidate in its original repository.

GitHub's organization App token is read only by the publisher. CMA receives
only public, hash-bound source inputs and immutable repository/template/skill
pins. Its separate NyxID proxy key is stored in the Actions secret
`MATHESIS_MATERIALS_CMA_TOKEN`. It is restricted to the personal CMA service;
no publisher credential is copied to CMA. Rotate it before the expiry recorded
in the operator's credential receipt. Configure `CMA_EXTERNAL_WORKSPACE` and
`CMA_COLLECTION_WORKSPACE` as repository variables, resolving to distinct live
sandboxes. The adapter checks actual sandbox IDs before new admission.

Set `MATHESIS_MATERIALS_AUTOMATION=probe` to verify private state-repository
access and CMA credentials without calling a model. Production requires both
`production.json.enabled=true` and the variable set to `enabled`. They remain
off while the second live CMA sandbox and its real acceptance test are missing.
This does not change open registration, author submission limits or BYOK policy.

## Resume, validate, publish

1. Reconcile existing operations before admitting a missing public version.
   Persist the complete provider intent before its POST. Retry an uncertain
   POST with the original body and idempotency key. Terminal provider failures
   remain `needs_attention`; they do not restart authoring.
2. Retrieve the exact source pack and validate its identity/hashes. Preserve it
   in the private ledger before requesting agent deletion. The importer accepts
   book content and its mapping; frozen manuscript bytes and managed template
   files cannot be changed. Read-only template symlinks must match the baseline;
   authored content cannot introduce symlinks.
   CMA's file API reads paths relative to retained `/home`. The adapter binds
   the provider's assigned worktree path to the admitted sandbox, then reads
   `mathesis-content/<key>` inside that worktree. A changed worktree stops
   retrieval; a missing old checkpoint binding is recovered from the original
   agent without creating another job.
   The statement-comparison record is authored data alongside the book mapping.
   The host regenerates the README guide and cited-declaration index using the
   installed `paper.py front` and `tbook pin --refresh` interfaces, requiring
   their bytes to match the handoff. The frozen README prefix, library repository
   and admitted library revision cannot change through this route. A worker's
   proposed declaration metadata is never copied over the independently resolved
   index.
   Agent cleanup stops the original agent and waits for its stopped state before
   deleting it. Each operation persists its own idempotency key. An uncertain
   reply reuses that key; acknowledged operations are observed without issuing
   another mutation, and the deletion receipt is retained until completion.
3. Attach immutable version history before building. New versions preserve the
   prior manuscript, DOI metadata and exposition sources in the same repository.
   Template manifests detect human edits; conflicting source changes stop for
   an explicit rebase. No whole Trureturing build is downloaded.
4. Render one paper with the pinned public toolchain image. The container gets
   no CMA or publisher token. Run the existing bilingual reading, slide/notes,
   EPUB, browser, visualization and actual-film checks. Preserve a build failure
   without asking CMA to write the paper again.
5. Check the source inventory against the fixed build. Fence the repository
   head, reserve the authored work from collection refresh and atomically update
   its source ref. Save the exact intended Git tree before the ref update. A lost
   publication receipt is recovered by comparing that tree, including modes;
   an intervening human edit is not overwritten.
6. A later bounded reconciliation requires a successful Pages run at the checked
   source commit, then checks the exact deployed material manifest
   and actual reading/media responses. Only public readback marks those products
   published. The existing hourly catalog/material sidecar reconciliation then
   exposes them in Mathesis.

The hourly publisher concurrency group also serializes material publication.
No PR validation, required check, verifier environment or upstream workflow is
changed. Its job budget permits one serial film render; a fresh organization
App token is minted after rendering because the earlier token may have expired.

Run `python3 scripts/hosted_materials.py plan --workspace /tmp/material-plan`
for an anonymous, zero-model plan. `reconcile`, `build`, `publish` and `attention`
are workflow steps. A model's `prepared` output means source prepared; it does
not assert QA, publication or formal verification. Use the saved job and source
pack to repair failures. Do not remove a checkpoint or use a fresh key to get
around a failed stage.
