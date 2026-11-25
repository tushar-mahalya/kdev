# Governance

kdev has one maintainer. This page says what that means for decisions.

## Roles

| Role | Who | Responsibilities |
| --- | --- | --- |
| Maintainer | [@tushar-mahalya](https://github.com/tushar-mahalya) | Sets direction, reviews and merges pull requests, publishes releases, enforces the [Code of Conduct](CODE_OF_CONDUCT.md). |
| Contributor | Anyone | Opens issues, discussions and pull requests under [CONTRIBUTING.md](CONTRIBUTING.md). |

The maintainer listed in [CODEOWNERS](.github/CODEOWNERS) reviews every change to `main`.

## Decisions

- Small changes are decided in the pull request.
- A change to a command's behaviour, to what kdev saves or restores, to the
  workflows or to how kdev is distributed needs an issue first, so the idea is
  agreed before the code is written.
- The maintainer makes the final decision when there is no consensus, and
  says why in the issue or pull request.
- Contributors do not need to sign anything beyond the
  [Contributor License Agreement](CLA.md).

## Becoming a maintainer

Contributors with a sustained record of good reviews and pull requests may be
invited. A new maintainer is added to CODEOWNERS and to this page in a pull
request.

## Repository protection

`main` and the release tags are protected by repository rulesets. Their
definitions are versioned in [.github/rulesets](.github/rulesets). Contributors
change `main` through reviewed pull requests. The maintainer may push to `main`
directly, for a release or an urgent fix. Nobody can force-push to or delete
`main`. [CONTRIBUTING.md](CONTRIBUTING.md#repository-rulesets) has the details
and how a maintainer applies them.
