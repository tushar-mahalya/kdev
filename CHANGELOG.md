# Changelog

All notable changes are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

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
