# Local work-package validation

All work is local. No external authenticated request, push, PR, repository
creation or App installation occurred. Fake GitHub API requests use a dummy
token on loopback only. The user's MongoDB process was not touched; nothing was
installed globally. All task files are in the named clones/worktree or TMPDIR.

- Mathesis SDK: 18 Rust tests passed; locked build, rustfmt check and clippy with
  `-D warnings` passed. Tests cover deterministic output, archive-index schema
  for collected/record DOIs, concept-DOI exclusion, DOI traversal, mapped and
  unmapped site links, material identity/URL boundaries, output tampering,
  complete public traversal, frozen hashes, statistics and atomic replacement.
  An observed test-server flake was fixed by reading complete TCP HTTP headers.
- Publisher: all 7 tests passed, including the optional full preflight test.
  `evidence/publisher-tests.log` retains the output. The full test uses the actual
  local paper PDF/manuscript and combined template, a loopback public Mathesis
  API with no statement/proof rows, and the actual SDK plus layer fill/check.
  It verifies transport/conversion, not the fixture's research projection.
- Workflow: actionlint was not installed. Ruby's YAML parser and `bash -n`
  validated the daily/manual triggers, minimal workflow permission, full action
  pins, every inline run block and every shell script. Python files compile.
  The App-token action's pinned public action.yml declares all requested inputs.
- Layer: all 14 tests passed with the real selected paper and combined template.
  The 0.3.0 migration is discoverable/idempotent, missing upstream hook support
  is refused, and built/unbuilt materials plus root routing are checked.
- Combined template site suite: 52 tests run, 44 passed and 8 skipped. The new
  hook tests also passed independently (3/3). Both template manifests match.
- Upstream clone: offline `tbook check` passed (2 books, zero errors/warnings).
  Full suite: 994 run, 855 passed, 137 skipped, 2 failures. Both failures use
  `mapfile`, absent from macOS's bundled Bash, in existing release workflow
  tests; no release workflow was modified. Full output is retained in
  `evidence/upstream-tests.log`.
- Chen–Chen consumer: complete paper check passed (16 generated hashes, 8 mapped,
  0 pending); `./tbook check` passed (1 book, zero errors/warnings); upstream and
  layer manifest checks passed; site build and extended site audit passed.
  Root, old landing, materials manifest, visualization and both slide PDFs
  returned HTTP 200 from a temporary local `tbook serve`, then the server stopped.
  The consumer's `docs/project/PUBLISHER-HANDOFF.md` records its evidence.

The locally assembled consumer site has no HTML readings; its manifest
correctly omits them and includes the existing English audience/notes PDFs and
triangle visualization. Typst was unavailable, so covers use CSS fallback.
Fresh browser visual QA was unavailable. No complete reader/toolchain rebuild,
production App/token test, live publisher apply or Pages deployment is claimed.
GitHub branch protections, installation scope and organization policy still
need the dispatcher run. The App must gain Issues write for preparation issues.

The Mathesis publisher branch was rebased onto the current local
`origin/develop`, including merged PR #120, before final pins were recorded.
`publisher.json` contains real local SDK/template commit objects; they must be
pushed (or replaced with reviewed remotely reachable equivalent SHAs) first.
All review checkouts are retained because publication is prohibited and the
new commits are not remotely preserved. No donor or unrelated checkout was
changed. See `PUBLISHER.md` for the operation entry point and
`docs/PUBLISHER.md` for dispatcher order and failure/retry behavior.
