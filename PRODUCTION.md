# Exact-version paper materials

The ordinary hourly publisher still archives eligible public manuscripts and
refreshes the catalog. The material adapter extends that workflow after the
core publication and readback. It does not run another review, deposit a DOI,
certify a proof or award credit. The source envelope separately controls whether
the content worker may register existing shared-queue formalization requests.

A material job uses the existing `mathesis.paper-production-envelope.v1`,
Mathesis-owned production skill, template fill interface, fixed serial renderer,
EPUB checks, browser checks and product checks. Visualization is assessed;
`not_useful` with a reason is a completed assessment. Reading remains Chinese
and English, selected by the website's existing global language switch. New
presentations and speaker notes are English only (`editions=["en"]`, whose
first source is `slides.tex`). Existing checked Chinese presentations can be
reused without another generation. Reading, slides and video
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
pins. Its separate bearer credential is stored in the Actions secret
`MATHESIS_MATERIALS_CMA_TOKEN`. The material adapter uses CMA's official
`https://cma.nyxid.ai` lifecycle API. NyxID-issued owner access tokens are accepted
by that API; its authorization still applies. The former NyxID proxy origin
remains an explicit compatibility option. That proxy currently rejects adding
an agent to an owned workspace created directly in CMA, so new internal work
pins the official endpoint in its original intent. Pre-migration intents retain
the former broker, including uncertain creates; changing a default never
replays one of those requests through another admission system. Tokens are sent only to the allowlisted CMA/NyxID
origins; credentials in URLs, alternate ports, queries and redirects are refused.
No publisher credential is copied to CMA. Rotate it before the expiry recorded
in the operator's credential receipt. Configure `CMA_COLLECTION_WORKSPACE` for
an existing live Mathesis workspace. The previous `CMA_EXTERNAL_WORKSPACE`
setting is accepted as a fallback. `execution_mode=shared` and
`authoring_concurrency=6` run up to six independent Codex content tasks on that
machine using GPT-6.2 Sol with high reasoning effort. Existing admitted tasks
keep their original model and operation; model changes apply to new admissions.
Host rendering and publishing remain serialized. Finished
sources waiting for the renderer are also bounded by that capacity.
The pinned renderer performs the complete source, EPUB, browser and media checks.
Publication reads back its successful receipt and exact source/site inventories;
it does not rerun container tools on the runner or overwrite the check log.
The operator assigns two fixed sandboxes: internal collected-paper materials
use `CMA_COLLECTION_WORKSPACE`; external review uses the separate
`CMA_EXTERNAL_WORKSPACE`. Both observed allocations have 8192 MiB memory.
Each job keeps its original context; processes inside one assigned sandbox
provide concurrency. `lanes=["collection"]`
admits our collected papers during
preparation. An explicitly configured `isolated` mode retains distinct-sandbox
checks. No route creates a new sandbox.

Set `MATHESIS_MATERIALS_AUTOMATION=probe` to verify private state-repository
access and CMA credentials without calling a model. Production requires both
`production.json.enabled=true` and the variable set to `enabled`. The existing
hourly publisher observes the original operation, builds finished sources, then
publishes and checks the exact public version. Enabling does not restart a
completed authoring attempt. A failed stage keeps its source and reason for
inspection while later eligible papers can progress on subsequent runs.
This does not change open registration, author submission limits or BYOK policy.

`allow_request_registration=true` authorizes the content worker to register
deduplicated requests only in `trureturing-requests` using its existing GitHub
service access. It does not grant publisher credentials, upstream code changes,
DOI deposits, proof certification or credit. Missing service permission remains
an unresolved stage; no request URL is invented.

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
   no CMA or publisher token. Run the existing bilingual reading, English slide/notes,
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
Failed core public readback is retained in `paper-factory-receipts` and makes
the run fail after independent material work finishes. It does not mark a
paper published, waive a file or version check, or prevent other preserved
material jobs from being observed, rendered and published under their own gates.
Generated collection workflows use the released Pages v5 upload/deploy pair
and bind the artifact name to the run and attempt. Retrying a deployment cannot
select an older attempt's artifact or collide with another Pages workflow.
No PR validation, required check, verifier environment or upstream workflow is
changed. Its job budget permits one serial film render; a fresh organization
App token is minted after rendering because the earlier token may have expired.

Run `python3 scripts/hosted_materials.py plan --workspace /tmp/material-plan`
for an anonymous, zero-model plan. `reconcile`, `build`, `publish` and `attention`
are workflow steps. A model's `prepared` output means source prepared; it does
not assert QA, publication or formal verification. Use the saved job and source
pack to repair failures. Do not remove a checkpoint or use a fresh key to get
around a failed stage.

### Communication diagnostics and saved-build recovery

CMA display names are bounded to its 64-character/80-byte limit, with a source
key suffix. An older oversized name is corrected only after an observed 422
`invalid_request` explicitly identifies the name field, before any lifecycle
receipt exists. Preserve the rejected request and replay key, then save the
name-only correction with a new key. Timeouts and other uncertain requests
always retain their original body and key.

Provider waits retain the HTTP status and allowlisted problem code/field, never
raw provider payloads, headers or credentials. Each reconciliation reports phase
counts, waiting jobs and parked failures in its Actions log and step summary;
`needs_attention` must not be described as up to date.

The production toolchain digest must match all eight recorded template input
hashes, including `requirements.txt`. The 2026-11-21 recovery selects the already
published matching image rather than rebuilding or editing upstream. Its
`saved_build_recovery` entries bind an operator-authorized retry to one immutable
source archive and that exact image. Each recovery ID runs once. Another build
failure remains parked; it never asks CMA to regenerate completed mathematics.

Host QA failures retain the exact failed command, exit code and bounded final
output in the existing job checkpoint. Publication failures name their own
stage while preserving the successful build receipt. The private ledger keeps
each diagnostic bundle under its content digest and points the job at its latest
failure; retries never overwrite previous checks or an immutable source pack.
Long builds also announce each stage's start and completion in the live workflow
log. The same checkpoint retains completed stages and the active stage, including
when a later check fails. Exact command paths remain in the private ledger.

The default-branch publisher checks out current controls when its serial job
starts, rather than the event's older queued commit. A preceding publication
can therefore reserve a completed book without making the next queued run's
controls stale. Existing optimistic remote-write fences still reject changes
that occur after that checkout. PR validation and source/template pins retain
their existing behavior.

### Late DOI discovery

`archive-discovery-index.json` extends the SDK's unchanged archive index with
published version DOIs found after a collected paper arrived. Only a unique
match for the exact title, ordered authors and affiliations, version, license,
PDF and source archive enters the candidate index. Missing or failed lookups
remain unconfirmed and never create a deposit or edit frozen delivery bytes.
Point Mathesis's existing `WISHPOOL_ARCHIVE_INDEX_URL` at
`https://raw.githubusercontent.com/mathesis-papers/catalog/main/archive-discovery-index.json`.
Mathesis independently checks the registry and both source hashes before
attaching each citation. No additional model run or operator token is needed.

Browser readiness checks have finite deadlines for navigation, font loading,
video playback, screenshots and cleanup. The existing private browser receipt
is written before each operation and on failure, so a stalled renderer retains
its precise page/action and completed checks. A 15-minute process-group bound
covers a browser that stops responding entirely; it terminates inspection children
and records the timeout without losing authored sources. Workflow-level interruption
still records the active check even if the worker could not write an exception.
These are inspection failures, not failures of the mathematical manuscript.
