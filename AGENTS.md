# AGENTS.md

Instructions for AI coding agents working in this repository. Humans: see
[CONTRIBUTING.md](CONTRIBUTING.md), which this file summarises.

## What kdev is

kdev is a Python CLI (`kdev-cli` on PyPI) that turns a Kaggle notebook session
into a remote dev box: SSH and VS Code in over a Cloudflare tunnel, with the
working directory restored from the notebook's saved versions on every start.
The project site is in `website/` (Astro, Cloudflare Pages).

## Setup and checks

Needs Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                  # create .venv with the dev tools
uv run pre-commit install                # commit, commit-msg and push hooks
uv run pytest                            # the test suite; offline
uv run pytest --cov=kdev                 # with coverage; fails under 71%
uv run ruff check .                      # lint
uv run ruff format --check .             # formatting (drop --check to fix)
uv run basedpyright                      # type check, basic mode
uv run pre-commit run --all-files        # every hook, as CI runs them
uv run kdev -v ...                       # run the working tree, with API logging
```

Website, from `website/`: `npm ci`, `npm run check` (types), `npm run build`,
`npm run check:links` (run it after the build).

## Layout

- `src/kdev/cli.py`: entry point and error handling. `commands/`: one module
  per command group; they parse arguments, call logic and render with `ui`.
- `session.py`, `persistence.py`, `quota.py`, `notebook.py`, `bootstrap.py`:
  the behaviour, testable without a terminal. `tunnel.py`, `cloudflared.py`,
  `sshcfg.py`: the tunnel and SSH config. `api.py`, `auth.py`, `config.py`:
  Kaggle access, accounts, settings.
- `src/kdev/kdev_box.py` runs inside the Kaggle container: standard library
  only, never import from the rest of the package.
- `src/kdev/ui.py`: every visual element. `errors.KdevError`: every failure a
  user can act on, with the command that fixes it.
- `tests/`: one file per module. `tests/test_cli.py` runs commands through the
  real entry point against a fake Kaggle. `tests/conftest.py` fakes the home
  directory and blocks the network.
- `docs/ARCHITECTURE.md`: how it works, read it before changing the box, the
  tunnel or the restore. `website/src/content/docs/`: the user docs.

## Conventions

- Commits and pull request titles are Conventional Commits: `type: subject` or
  `type(scope): subject`; types `feat fix docs test ci build chore refactor perf
  style revert`; lowercase subject, no final period, the why in the body. A
  `commit-msg` hook checks it.
- Anything users will notice goes under `## [Unreleased]` in `CHANGELOG.md`.
  Update the README, `website/src/content/docs/` and `docs/ARCHITECTURE.md`
  when a command or how things work changes.
- A bug fix comes with a test that fails without the fix. Take expected values
  from an independent source, not from the code under test.
- Match the surrounding code: no new comments or long docstrings it does not
  already have. Everything the user sees goes through `ui.py`.
- Every GitHub Action is pinned to a full commit SHA with the version in a
  comment (`@<sha> # v1.2.3`); new pre-commit hooks are pinned the same way.
  Every workflow sets least-privilege `permissions:`.
- Contributors sign the [CLA](CLA.md) on their first pull request; the bot asks.
- Lower bounds in `pyproject.toml` must be true: CI runs the tests against the
  lowest versions allowed (`uv run --isolated --resolution lowest-direct pytest`).

## Rules for agents

- Never commit or print secrets: Kaggle tokens, tunnel credentials, deploy
  keys, `~/.kaggle`, `~/.config/kdev`. Tests use a throwaway home.
- Tests stay offline. Use the fake Kaggle in `tests/test_cli.py`; do not add a
  test that needs a Kaggle account, a network or a real `cloudflared`.
- Do not hand-edit generated files: `uv.lock` (use `uv lock`),
  `website/package-lock.json` (use npm), `.secrets.baseline`, `website/dist/`.
- Do not add `Co-Authored-By` or "Generated with" trailers to commits unless
  the human asks.
- Do not open issues or pull requests, post comments, push, or change GitHub
  settings unless the human asks. Do not force push.
- Keep changes small and on topic: one change per pull request, no drive-by
  reformatting.
- Run `uv run pytest` and `uv run pre-commit run --all-files` before saying a
  change is done, and say what you did not run.
- Disclose AI assistance in the pull request, as the template asks.
