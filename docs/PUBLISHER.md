# Catalog publisher

The workflow runs daily at **03:23 UTC** and manually with `workflow_dispatch`.
A manual `dry_run=true` performs staging without minting an App token or writing
GitHub. Runs execute only on the default branch. Organization-wide concurrency
`mathesis-papers-publisher`, with cancellation disabled, prevents overlap.

`GITHUB_TOKEN` has only `contents: read` for checkout. The App token is minted
**after** staging succeeds, by `actions/create-github-app-token` pinned to
`fee1f7d63c2ff003460e3d139729b119787bc349`. Both checkout uses are also pinned.
The action revokes its token at job completion. No other long-lived token is
used. The token requests administration, contents, pages, workflows and issues
write; metadata read is implicit. Pull-request write and actions read are not
requested because this workflow does not use them.

Install `mathesis-papers-publisher` on **all repositories** in the organization,
including future repositories, before enabling publication. Keep
`MATHESIS_APP_ID` in the repository variable and `MATHESIS_APP_PRIVATE_KEY` in
the repository secret. Add **Issues: write** to the App before installation:
the supplied App permissions do not yet include permission to create the
preparation issue. GitHub may require an installation permission update after
changing the App. Organization policy must allow the App to create repositories,
write workflow files and update these repositories' default branches.

`publisher.json` pins the exact Mathesis and combined template commits. Publish
those objects before running the workflow. The SDK build uses its checked-in
Rust toolchain and lockfile. The workflow runs SDK tests, builds the release
binary and tests the publisher against a local fake API. `prepare` then:

1. Snapshots all anonymous public Mathesis pages, fetches frozen bytes, renders
   and checks the catalog.
2. Combines durable `repositories.json` with eligible work ids explicitly listed
   in `publish.json`. The reviewed allow-list contains the 39 public collected works.
   Existing durable mappings are reused without renaming repositories.
3. Fetches mapped repository manifests anonymously from
   `https://papers.trureturing.org/<repo>/mathesis-materials.json`. A 404 means
   no materials yet. Other HTTP, malformed, identity or version errors abort.
4. Renders and checks again with the proposed mapping and local manifests.
5. Checks out the exact combined template commit, imports its checked-in
   preparation checklist, and fills every new paper offline with `paper.py fill`.
   The recorded title is explicitly reused for both edition headings, pending a
   reviewed translation; no translation or reading text is generated. Runs
   `paper.py check` and `./tbook check --offline` on each skeleton.
6. Seals all planned publishing bytes and file modes. A failed stage writes
   nothing remotely and does not edit publisher control files.

`apply` verifies the seal, control files and shared repository heads, including
the org site's existing CNAME, before publication. Remote control files are checked again to reject concurrent allow-list/pin changes. For each newly mapped work,
it creates from `paper-template` or detects an existing repository from that
template. A populated paper must match the work identity and contain both
integration manifests; its content is preserved. An unpopulated repository
must have the correct template provenance. It enables Pages with
`build_type=workflow` **before** the initialization commit, so the inherited
Pages probe can deploy that push. It atomically commits the pinned template
and filled skeleton, retaining executable and symlink modes. It opens exactly
one issue titled **Prepare the reading, visualizations and slides**, with the
layer checklist; open, closed and paginated issues are checked to avoid repeats.
It generates no book text, visualizations or slides.

It then updates only `profile/README.md` in `.github`; the generated org index,
DOI redirects, `.nojekyll` and `CNAME` in `mathesis-papers.github.io`; and catalog
JSON, README, repository mapping and per-work materials in `catalog`. Unrelated
files (including workflows and publisher controls) remain. The org site keeps
its existing Pages publication configuration; its deployed source must be the
default branch root. No commit/ref write occurs when bytes and modes match.
The catalog mapping is committed last, after each paper's issue and Pages setup
succeeded. If a later API call fails, earlier per-repository updates may exist;
there is no cross-repository GitHub transaction. Retry resumes template-only or
already filled repositories and does not create another issue or overwrite
human content. Ref updates are never forced; a concurrent human push causes a
failure and the next run recomputes from fresh heads.

In book mode the publisher preserves existing authored content. Collection
mode refreshes frozen projections with the ownership and version protections
below. Neither mode creates accepted-record repositories, deposits archives,
registers DOIs, writes the Mathesis server or generates research content. The archive index contains
only already published exact-version DOIs and follows `mathesis.archive-index.v1`.
Material output follows the fields documented in the pinned SDK and layer.

## Local validation

```sh
python3 -m unittest discover -s tests
ruby scripts/validate-workflow.rb
bash -n scripts/build-catalog.sh
```

If available, also run `actionlint .github/workflows/publish.yml`. The Ruby
fallback parses YAML, checks the triggers/permissions/action pins, and runs
`bash -n` on every inline run block and shell script. Tests use a loopback fake
GitHub API; they perform no network writes. They cover creation, unchanged
reruns, template-only recovery, issue-failure recovery preserving human edits,
closed/paginated issue deduplication, stale DOI cleanup, executable/symlink
modes, CNAME/unrelated-file preservation, tampering and unrelated-repo refusal,
and preparation failure before publication. An optional seventh integration test
runs the actual SDK snapshot/fetch/render/check and layer fill/check against a
loopback public-API fixture, using a real local paper and template:

```sh
MATHESIS_CATALOG_BINARY=/absolute/path/to/mathesis-catalog \
MATHESIS_TEST_TEMPLATE=/absolute/path/to/combined-template \
MATHESIS_TEST_PAPER=/absolute/path/to/selected-paper \
  python3 -m unittest discover -s tests
```

The fixture contains no statement/proof rows and is explicitly a publisher
transport/conversion test, not a substitute research snapshot.

## Dispatcher order

1. Review and push the upstream optional site-hook branch, including its film
   sandbox root correction, then integrate it upstream or retain its reviewed
   change in the combined template. The correction changes only the test
   fixture; production hook behavior is unchanged.
2. Review and push Mathesis layer 0.3.1; push the combined template branch with
   the hook, the fixture correction, the layer and the preexisting compatibility
   work intact. The pinned template includes the guarded post-build bootstrap,
   so new repositories can run the inherited fixture tests successfully.
3. Push the Mathesis catalog SDK commit. If the dispatcher squashes or rebases,
   update `publisher.json` to the actual remotely reachable SDK/template SHAs.
4. Review and push the updated Chen–Chen consumer. Its layer migration configures
   the root redirect; its Pages build publishes the new materials manifest.
5. Review and push this catalog publisher branch and merge through the normal
   review process. `publish.json` and `repositories.json` must remain reviewed
   control files; do not copy all prospective repository names from `works.json`.
6. Grant Issues write and install the App on all org repositories. Confirm the
   org site's current Pages source is its default branch root and preserve its
   custom-domain configuration. Run a manual dry run, then a publishing run.

No push, PR, repository creation, App installation or authenticated request was
performed in the local preparation. GitHub App installation access, branch
protection compatibility, real token issuance, repository generation and Pages
publication remain dispatcher checks.

## Formalization content projection

The updated SDK also emits `output/formalization/`. Prepare runs the SDK's
complete output check and the generated `formalization/scripts/check.py`,
requires schema `mathesis.formalization-repository.v1`, and seals its files with
the same stage. The profile and org index link `mathesis-papers/formalization`.
A paper mapping cannot use that reserved name.

Apply requires that repository to **already exist**, public and active. Its
head is read with the other shared destinations before any write. This publisher
does not create it. The existing sync mechanism uses a single non-forced ref
update per changed repository and makes no commit for an unchanged tree. It
manages the generated root JSON/Markdown/Lake files, `.gitignore`,
`.mathesis/generated.json`, `.github/workflows/check.yml`, the two generated
scripts, and `targets/`, `curated/`, `proofs/`, `disproofs/`, `index/`,
`environment/` and `ci/mathlib/`. Other files and workflows remain intact.
Stale generated files in those namespaces are removed. An apply failure resumes
on retry, under the existing organization-wide workflow concurrency.

Mathesis remains the authority; the content repository is a projection. Exact
sources and original verifier receipts are copied, and the public leaderboards
are displayed without locally computing credit. CI checks integrity and compiles
compatible target propositions with pinned Mathlib; it does not rebuild the
prepared library, compile proof/disproof bundles or replace the independent
Mathesis verifier. See the source checkout's `docs/FORMALIZATION_REPOSITORY.md`.

`publisher.json` pins the Mathesis commit whose `sdk/catalog` renders the
formalization tree. A pin older than that renderer fails formalization staging
before any authenticated write.

Local verification also covers formalization sync, unchanged reruns, stale
source cleanup, preserved unrelated files/workflows, stage tampering and missing
or private formalization repositories failing before any write.

## Independent collection of the existing corpus

`paper_publication: "collection"` in `publisher.json` publishes already public,
CC-BY-4.0 collected papers directly. `publish.json` explicitly authorizes the
39 currently collected works. `book_works` preserves the existing Chen–Chen
reading project; its authored content and publication workflow remain intact.
New collection repositories still originate from `paper-template`, but their
first publisher commit contains only the frozen paper projection, public
reading pages, versioned originals and a small static Pages workflow. They do
not wait for book conversion, statement comparisons, CMA or proof jobs.

Before any writes, prepare reads each exact public version anonymously and
checks its work/version against the SDK projection. Only existing English and
Chinese explanations are copied. Missing exposition leaves the abstract and
paper available, without an invented reading. Static pages expose authors,
abstract, PDF, source, citation, an existing DOI and pinned Lean source when
available. The material manifest advertises only actual readings. The regular
publisher discovers these manifests after Pages deployment.

The `.mathesis/collection.json` ownership marker binds generated paths to
SHA-256 values and Git blobs. An existing collection is refreshed only when
its generated files still match the previous ownership inventory. Human edits
cause an explicit conflict; unrelated files are preserved. Old `versions/vN/`
directories are retained when the current version advances. A populated book
without this marker is never converted or overwritten. New and updated
collections use the same stage seal and non-forced commit/ref transactions.
Collection creates no review, proof receipt or leaderboard credit, and needs
no Issues permission. The original book publication mode remains available.

## Paper repository names and address migration

`repository_naming: "doi-v1"` names new repositories after the paper's concept
DOI (the identifier shared by its versions), falling back to its version DOI.
For example, `10.5281/zenodo.23884900` becomes
`10.5281-zenodo.23884900`. The complete DOI remains unchanged in citations.
Papers without a DOI use their readable work name. Unsafe characters are
replaced with hyphens; collisions and invalid DOI values stop preparation.
A durable mapping is retained when subsequent versions are published.

The reviewed mapping migrates the 39 existing repositories in place: 37 use
DOIs, and the two without DOIs use their work names. `repository_aliases` in
`publisher.json` records each earlier address. Repository renaming preserves
Git history, issues, authorship and GitHub redirects. The publisher refuses to
create a second repository if an expected rename is not complete. It accepts
an earlier name in paper metadata only for the explicitly recorded same work.

Public material manifests from the previous address are checked and their
address prefixes adapted to the new repository; reading, slides and
visualization paths are preserved. The organization site publishes redirects
for old landing/reading addresses, preserves old direct PDF downloads, and
handles other old nested paths through its 404 page. GitHub Pages does not
provide server redirects on repository rename, so browser redirects are explicit.

Collection updates keep original version snapshots byte-for-byte, even when
the current address changes. Existing authored books receive only three
address metadata changes: `site.toml`, `paper.json`, and the corresponding
integrity hash in `.mathesis/generated.json`. Chapters, figures, slides, proofs,
Lean evidence, source files and PDFs are preserved. Other user edits remain
protected by the existing ownership checks.
