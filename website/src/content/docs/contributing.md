---
title: "Contributing"
description: "Set up a dev environment, try a change against real Kaggle, and send a pull request."
group: Project
order: 10
---

Contributions are welcome — bug reports, fixes, docs and features alike. kdev
is small and opinionated, so for anything bigger than a fix, open an issue
first and say what you want to change and why. It saves you writing code that
goes a different way than the project.

## Ways to help

- **Ask a question** in [Discussions](https://github.com/tushar-mahalya/kdev/discussions):
  how to do something, or whether what you see is expected.
- **Report a bug.** Open an issue with what you ran, what you expected and
  what happened. The output of the same command with `-v` (`kdev -v up …`)
  helps most. It logs every Kaggle API call; check it for anything you would
  rather not share before you paste it.
- **Fix something.** Issues labelled
  [`good first issue`](https://github.com/tushar-mahalya/kdev/labels/good%20first%20issue)
  are small and well described; [`help wanted`](https://github.com/tushar-mahalya/kdev/labels/help%20wanted)
  ones are bigger. Say in the issue that you are on it, so two people don't
  fix the same thing.
- **Improve the docs.** If something here was unclear when you set up, it will
  be unclear to the next person too.
- **Report a vulnerability** privately, as [SECURITY.md](https://github.com/tushar-mahalya/kdev/blob/main/SECURITY.md)
  describes, never in a public issue.

## Set up

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tushar-mahalya/kdev && cd kdev
uv sync
uv run pre-commit install        # hooks on commit and commit message, tests on push
uv run basedpyright              # basic-mode type check, also on every commit
uv run pytest                    # unit and regression tests, no network needed
uv run pytest --cov              # the same suite with a local coverage report
uv run kdev -v …                 # the working tree, with API logging
```

Every commit runs formatting, lint, type-checking (`basedpyright`), a secrets scan
(`detect-secrets`, private-key detection), a spell check (`typos`), config
validation, a workflow security audit (`zizmor`, `actionlint`) and a lockfile
check, and its message must be a Conventional Commit; every push runs the tests. CI runs the same hooks, the tests on Python
3.11–3.13, a build-and-install check, and `gitleaks` over the full history.

## Trying a change against real Kaggle

The test suite needs no Kaggle account, and most changes need nothing more.
When yours touches the box, the tunnel or the restore, try it for real:

- Use **your own** workspace (`kdev workspace create --group <slug>`), not a
  shared one someone else works in.
- Start **CPU boxes**: `uv run kdev up --gpu none --hours 1`. They cost no GPU
  quota. A GPU run spends the quota of whichever account starts it.
- `kdev down` when you are done, and `kdev workspace files` to see exactly
  what was saved.

## How the code is laid out

The full picture, with diagrams, is in
[docs/ARCHITECTURE.md](https://github.com/tushar-mahalya/kdev/blob/main/docs/ARCHITECTURE.md).

| layer | rule |
|---|---|
| `commands/` | parse arguments, call logic, render with `ui`; no API calls of their own beyond reads |
| `session`, `persistence`, `quota`, `notebook` | the behaviour; testable without a terminal |
| `kdev_box.py` | standard library only; it's shipped into the container and runs there |
| `ui.py` | every visual element, with its tokens and rules written down at the top |
| `errors.KdevError` | every failure a user can act on: a message and the command that fixes it |

Tests are split by module. `tests/test_cli.py` runs every command through
the real entry point against a fake Kaggle. `tests/test_kdev_box.py` runs the
on-box restore against a temporary directory.

## Pull requests

- **One change per pull request**, with a description of the problem it
  solves and how you checked it.
- **A bug fix comes with a test** that fails without the fix. New behaviour
  comes with the smallest test that would catch it breaking. Tests stay
  offline: use the fake Kaggle in `tests/test_cli.py`.
- **Match the code around you.** `kdev_box.py` stays standard-library only.
  Everything the user sees goes through `ui.py`. A failure the user can act on
  is a `KdevError` with the command that fixes it.
- **Commit messages and the pull request title** follow
  [Conventional Commits](https://www.conventionalcommits.org/): `type: subject`
  or `type(scope): subject`, with the type one of `feat`, `fix`, `docs`,
  `test`, `ci`, `build`, `chore`, `refactor`, `perf`, `style` or `revert`. Start
  the subject with a lowercase word and leave off the final period; put the
  *why* in the body. A `commit-msg` hook checks each commit, and a check on the
  pull request checks its title, because we squash-merge and the title becomes
  the commit on `main`.
- **Record what users will notice** under `## [Unreleased]` in
  [CHANGELOG.md](https://github.com/tushar-mahalya/kdev/blob/main/CHANGELOG.md), and update the README if a command or its
  behaviour changes.

## Automated review

[CodeRabbit](https://coderabbit.ai) reviews every pull request: it posts a
summary and line comments within a few minutes, and reviews again when you
push. Its comments are suggestions. Fix what is right, reply to what isn't,
and leave the rest; a maintainer decides what merges. You can talk to it in
the pull request: mention `@coderabbitai` with a question, or
`@coderabbitai review` to ask for a fresh review.

## Contributor License Agreement

Before your first pull request can merge, you sign the kdev
[Contributor License Agreement](https://github.com/tushar-mahalya/kdev/blob/main/CLA.md). It says that you wrote what you
contribute (or have the right to submit it) and lets the project keep
distributing it. You keep the copyright in your work.

You don't need to do anything in advance: the CLA Assistant bot comments on
your first pull request with a link, and signing takes one click with your
GitHub account. It covers all your later pull requests. If the agreement
itself changes, the bot asks you to sign the new version.

## Releasing

For maintainers: bump `version` in `pyproject.toml`, move the `Unreleased`
notes in `CHANGELOG.md` under the new version, then
`git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow checks that
the tag matches the version, runs the tests, and publishes a GitHub release
with the wheel and sdist attached.

## License

kdev is [MIT licensed](https://github.com/tushar-mahalya/kdev/blob/main/LICENSE). By contributing, you agree that your
contributions are licensed under the same terms, and you grant the rights
set out in the [Contributor License Agreement](https://github.com/tushar-mahalya/kdev/blob/main/CLA.md).
