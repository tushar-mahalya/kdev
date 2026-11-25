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

How decisions are made is in [GOVERNANCE.md](https://github.com/tushar-mahalya/kdev/blob/main/GOVERNANCE.md); where to get help is
in [SUPPORT.md](https://github.com/tushar-mahalya/kdev/blob/main/SUPPORT.md).

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
uv run pytest --cov              # the same suite with a coverage report; fails under 71%
uv run kdev -v …                 # the working tree, with API logging
```

Every commit runs formatting, lint, type-checking (`basedpyright`), a secrets
scan (`detect-secrets`, private-key detection), a spell check (`typos`), config
validation, a workflow security audit (`zizmor`, `actionlint`) and a lockfile
check, and its message must be a Conventional Commit; every push runs the tests.

CI runs the same hooks, the tests on Python 3.11–3.13, the tests again on the
lowest version of each dependency that `pyproject.toml` allows (if they fail,
raise that floor), a build-and-install check, and `gitleaks` over the full
history. The single job `CI passed` is green only when all of those are. Pull
requests also get a review of the dependencies they add.

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

## AI-assisted contributions

AI tools are welcome here, as long as a person is behind the pull request.
Agent-assisted work is fine when you direct it, review every line, run the
tests and can explain every change. [AGENTS.md](https://github.com/tushar-mahalya/kdev/blob/main/AGENTS.md) tells agents how the
repository works.

- **Fully agentic pull requests are not accepted.** If an agent wrote the code,
  the tests and the description and no person reviewed them, we may close the
  pull request without a detailed review.
- **Agents act through you.** An agent must not open an issue or a pull
  request, post a comment or mention a maintainer on its own. You do that, and
  you have read what you post.
- **Write in your own words.** The description of a pull request, an issue and
  your replies in review should show what you understand. Answer reviewers'
  questions yourself.
- **Say what you used.** The pull request template asks which AI tools you
  used and for which parts: code, tests, description, something else.
- **Tests do not come from the code under test.** A bug fix needs a test that
  fails without the fix. Take the expected values from somewhere independent
  (Kaggle's documentation, behaviour you saw on a real box, the spec of the
  tool), not from whatever the code returns today.
- **Match the code around you.** Leave out comments and long docstrings the
  surrounding code does not have, and abstractions nobody asked for.
- **Submit only what you may license.** A model can reproduce code from other
  projects. Send only what you are allowed to license under the
  [MIT license](https://github.com/tushar-mahalya/kdev/blob/main/LICENSE) and the [Contributor License Agreement](https://github.com/tushar-mahalya/kdev/blob/main/CLA.md).

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

## Labels

Labels are defined in [`.github/labels.yml`](https://github.com/tushar-mahalya/kdev/blob/main/.github/labels.yml). The "Sync
labels" workflow applies that file when it changes on `main`: it creates and
updates labels and never deletes one. A second workflow labels each pull
request from the paths it changes (`area: cli`, `area: box`, `area: website`
and so on, in [`.github/labeler.yml`](https://github.com/tushar-mahalya/kdev/blob/main/.github/labeler.yml)) and from its branch
prefix (`feat/`, `fix/`, `docs/`). The labels also sort the notes GitHub
generates for a release ([`.github/release.yml`](https://github.com/tushar-mahalya/kdev/blob/main/.github/release.yml)).

## Repository rulesets

Branch and tag protection is written down in [`.github/rulesets`](https://github.com/tushar-mahalya/kdev/tree/main/.github/rulesets),
so a change to it is reviewed like any other:

- `main.json`: a change reaches `main` only through a pull request that
  passes `CI passed` and `license/cla` (the CLA Assistant check), has one
  approval from a code owner, has every review thread resolved, and is
  squash-merged, so history stays linear. The branch must be up to date with
  `main` before it merges. The maintainer can bypass this, for a release or an
  urgent fix; CI still runs on what is pushed.
- `main-integrity.json`: nobody, the maintainer included, can force-push to
  or delete `main`. It has no bypass.
- `release-tags.json`: `v*` tags cannot be created, moved or deleted, except
  by the maintainer.

`CI passed` is the last job in `ci.yml`; it needs every other job there. Pull
requests also run `PR title`, `Dependency review` and the labeler, which are
not required checks.

The files are not applied by themselves. A maintainer applies a changed one
with the GitHub CLI, `POST` for a ruleset that does not exist yet and `PUT`
with its ID to update one (`gh api repos/tushar-mahalya/kdev/rulesets` lists
the IDs):

```bash
gh api repos/tushar-mahalya/kdev/rulesets --method POST --input .github/rulesets/main-integrity.json
gh api repos/tushar-mahalya/kdev/rulesets/<id> --method PUT --input .github/rulesets/main.json
```

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
