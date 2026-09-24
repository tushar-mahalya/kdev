# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.4] - 2026-09-24

### Added
- Each release carries a Sigstore-signed build provenance for its wheel and
  sdist (`kdev_cli-<version>.sigstore.json`), recorded with GitHub so that
  `gh attestation verify` can check any copy of the files. SECURITY.md says how.

## [0.1.3] - 2026-09-24

### Added
- kdev is on PyPI as `kdev-cli` (PyPI does not allow the name `kdev`):
  `uv tool install kdev-cli` or `pipx install kdev-cli`; the command is still
  `kdev`. Releases are
  published by GitHub Actions through Trusted Publishing, with an attestation
  for each file, so no upload token exists to leak.

### Changed
- Works with rich 15.

## [0.1.2] - 2026-09-24

### Fixed
- The `kdev up` status board smeared across the terminal once the box was up:
  frames repeated and lines drifted right. kdev's background ssh calls asked
  for a tty (the ssh block sets `RequestTTY yes`), which put the terminal in
  raw mode under the board. They now pass `-T`.
- `kdev up` right after a session was cancelled could build on the version
  before it and drop that session's work: Kaggle lists a cancelled version's
  files bit by bit while it says `CANCEL_REQUESTED`. `up` now waits for the
  save to finish.
- A file whose name has a space or non-ASCII letters (`notes ü.txt`) could
  never be restored: Kaggle signs its URL unescaped and urllib refused it.
- A project file named like one of the box's own (`web/static/custom.css`) was
  silently left out of every restore and backup. Only the top-level ones are
  skipped now.
- A restore killed part-way could corrupt a large file on the next start: the
  half-downloaded `.kdev-part` it left was saved, restored as a user file, and
  landed on the real file's download. Leftovers are ignored, and a download
  cut short is retried instead of kept.
- A restore killed part-way lost exec bits (e.g. `.git/hooks`) for the files it
  had already brought back. They are now recorded before the first download.
- `kdev up` straight after `kdev down` found the box still saving (Kaggle says
  RUNNING until the upload ends, about 2 min per GB), tried to connect and
  failed. It now waits for the save and starts afresh. A second `kdev down`
  says the box is already stopping instead of cancelling it, and `kdev status`
  says "stopping".
- VS Code and `ssh kaggle` failed with "Host key verification failed" on any
  machine that had not started the current box: every box has a new host key.
  The `kaggle` block no longer pins one.
- `kdev down` removed the `kaggle` block, so that machine could not reach the
  next box another machine started. With a named tunnel it now stays, and
  `status`, `backup`, `restore` and `setup` write it when it is missing.
- A file deleted in a session came back after a restore that had not finished:
  the version underneath was restored whole. Now it only fills in what that
  restore never delivered, which the box logs in `.kdev/fetched`.
- `kdev backup` kept files deleted on the box, and files from other notebooks,
  and `kdev restore --from-backup` then overwrote what the session had written.
  The backup is now an exact copy, and pushing it only adds what is missing.
- `kdev restore --from v999` reported success for a version that does not
  exist, and `--from` changed the version the box records as its base.
- `kdev workspace` said "0 file(s)" and `workspace files` "nothing saved yet"
  while a box was running: they read the running version, which has no files
  until it ends. Both now show the newest version with files.
- `kdev setup`, and every other yes/no question, stopped with "interrupted"
  when run without a terminal. They now take the default answer.

## [0.1.1] - 2026-09-23

### Fixed
- `kdev up` failed with "No runs found for this kernel (HTTP 404)" on a
  notebook that had never been run, such as one just made in the Kaggle
  editor. Kaggle reports that as a 404 rather than a status; kdev now reads it
  as "nothing running" and starts the box. The same fix covers `kdev down` and
  replacing a running box, and `kdev status` says "never run yet".
- `kdev up` renamed a notebook made in the Kaggle editor to its slug (for
  example "v0.1.0_testing" became "v0-1-0-testing"). It now keeps the title.

## [0.1.0] - 2026-09-23

First release.

### Added
- `kdev up` starts a Kaggle notebook session as an SSH box over a Cloudflare
  tunnel, or connects to the one already running, from any account or machine.
- Files persist across sessions, accounts and machines: every session restores
  the notebook's last saved `/kaggle/working`, including symlinks, empty
  directories and executable bits, which Kaggle's own snapshot drops.
- A session that is cut short, or worked in before its restore, is stacked on
  the versions it was meant to hold instead of being skipped.
- Several Kaggle accounts share one group notebook; `kdev up` asks which
  account takes over when the active one is short on quota.
- `kdev down` stops a box over ssh, or cancels it through the API as the
  account that started it when it cannot be reached.
- Commands: `up`, `down`, `ssh`, `status`, `logs`, `restore`, `backup`,
  `account`, `workspace`, `config`, `tunnel`, `doctor`, `setup`, and an
  interactive overview on bare `kdev`.
- `--json` output for `status`, `account` and `workspace files`; `-v` logs every
  Kaggle API call.
