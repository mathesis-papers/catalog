# Local accepted-paper production

`scripts/serial_content.py` consumes a frozen cohort and runs one local Codex
paper worker at a time. The envelope, prompt, result schema and pinned template
are the executor contract; a CMA adapter should consume those same inputs.
This command does not call CMA, deposit DOIs, publish repositories or deploy.
The ordinary catalog publisher remains deterministic and model-free.

Keep the run workspace outside every paper repository. Its `cohort.json` has
schema `mathesis.serial-cohort.v1`, `api_base`, and `papers`, each containing
`id`, `version`, `title` and `repository` (`mathesis-papers/<mapped name>`).
Additional frozen observations such as DOI, concept DOI and pinned Lean URL
may be included. Freeze the selection once instead of expanding an active
batch whenever upstream delivers another article.

```sh
python3 scripts/serial_content.py prepare --workspace /path/to/run \
  --template /path/to/published-paper-template \
  --toolchain /path/to/existing-compatible-toolchain --paper <work-id>
python3 scripts/serial_content.py run --workspace /path/to/run \
  --template /path/to/published-paper-template \
  --toolchain /path/to/existing-compatible-toolchain
python3 scripts/serial_content.py status --workspace /path/to/run
```

Omit `--paper` to process the frozen cohort in order. Shared-request registration
requires `--allow-request-registration`, under the operator's existing authority.
The content worker searches both open and closed issues and follows the
installed template's mathematical deduplication and provenance contract. The
flag does not authorize other remote writes. No publication credentials need
to be passed as task inputs. The GitHub CLI may use the operator's existing
login for the explicitly authorized request registration.

The supplied compatible toolchain shares ignored `.venv`, `node_modules` and
the Tectonic resource cache. Put mdBook on the process PATH or set `MDBOOK`;
`PW_CHROMIUM_PATH` may select an existing working browser. Check available
memory, disk and active Lean/Lake executables before heavy work. This runner
does not rebuild Lean libraries. It refuses to start below 8 GiB free disk.

Collection checkouts remain untouched. For an unconverted collection the
runner copies only publisher-owned projection files, uses the existing catalog
`legacy_book_projection`, and invokes the published template's `paper.py fill`
in a separate local authored preview. This is not an in-place migration of the
published repository. Prior-version URLs remain unchanged. Publishing those
authored sources later requires a reviewed collection-to-book migration that
preserves earlier public versions and updates `book_works` consistently.

The worker writes content sources and comparison records. The host then runs
complete layer and language checks, the installed HTML/PDF/Beamer builds, site
assembly, browser checks, artifact identity checks and PDF rendering. Interactive
visualization is optional: record `not_useful` with a concrete reason when prose
or static figures teach the result sufficiently. A missing planned video stays
pending. Never advertise files that have not actually been built.

`local_checks_passed` records actual local checks, not publication, whole-paper
Lean coverage, independent verification, visual approval or leaderboard credit.
The host binds those outputs to a source inventory as well as render hashes;
changed sources need a rebuild of the existing products, without a model rerun.
Inspect the rendered contact sheets and mathematical scope before publication.
An operational failure does not reject a paper or make its mathematics open.
Worker output paths and request URLs are machine fields, not Markdown prose.

Checkpoints, transcripts and QA stay in the external run workspace. Successful
checkpoints skip repeated model generation. An interrupted attempt requires
inspection of its existing thread and artifacts; the runner never blindly
restarts it. After repairing a report or output, recheck without a model:

```sh
python3 scripts/serial_content.py check --workspace /path/to/run --run /path/to/existing-attempt
```

Keep authored source commits and unique evidence before cleaning reproducible
build/stage caches. Do not remove a shared dependency or a live job's output.
The current harness has no automatic GitHub-to-platform receipt/credit bridge;
canonical request links preserve coordination but do not implement that bridge.

## Local collection-to-book migration

After inspecting and committing one paper's sources, prepare its migration with:

```sh
python3 scripts/promote_content.py --run /path/to/checked-attempt \
  --output /outside/paper-repos/promotion
```

The command creates a local branch based on the original collection commit. It
preserves every `versions/vN/` byte and original author/DOI identity, replaces
only collection-owned files, and refuses conflicts with unrelated human work.
Checked render hashes must still match. The original source checkout is never
edited. The Mathesis layer must support the generated collection-history record
before the migrated repository passes its layout and site checks.

`promotion.json` and `publisher-proposal.json` stay outside the paper checkout.
The latter adds the work to `book_works`; publication must coordinate that
publisher control change so a scheduled collection refresh cannot overwrite
authored content. This command does not edit controls, commit the new branch,
push, trigger Pages, reserve/deposit a DOI or grant proof credit. Revalidate the
migrated checkout and preserved version URLs before publication.
