<p align="center">
  <img src="https://raw.githubusercontent.com/tushar-mahalya/kdev/main/docs/assets/kdev-logo.png" alt="kdev" width="520">
</p>

<p align="center">
  <b>A Kaggle notebook as your remote dev box.</b><br>
  Start it with one command, work in it from VS Code or a shell, and leave whenever you like.<br>
  Your files are there next time — on any account in your Kaggle group, from any machine.
</p>

<p align="center">
  <a href="https://github.com/tushar-mahalya/kdev/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/tushar-mahalya/kdev/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pypi.org/project/kdev-cli/"><img alt="PyPI" src="https://img.shields.io/pypi/v/kdev-cli?logo=pypi&logoColor=white&color=00b8d4"></a>
  <a href="https://pypi.org/project/kdev-cli/"><img alt="Python versions" src="https://img.shields.io/pypi/pyversions/kdev-cli?logo=python&logoColor=white&color=3776AB"></a>
  <a href="https://github.com/tushar-mahalya/kdev/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/tushar-mahalya/kdev?label=release&logo=github&color=2ea44f"></a>
  <a href="https://github.com/tushar-mahalya/kdev/blob/main/LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-2ea44f"></a>
  <a href="https://scorecard.dev/viewer/?uri=github.com/tushar-mahalya/kdev"><img alt="OpenSSF Scorecard" src="https://api.scorecard.dev/projects/github.com/tushar-mahalya/kdev/badge"></a>
  <a href="https://kdev-cli.pages.dev"><img alt="Website" src="https://img.shields.io/badge/website-kdev--cli.pages.dev-00b8d4"></a>
  <a href="https://github.com/tushar-mahalya/kdev/blob/main/CONTRIBUTING.md"><img alt="PRs welcome" src="https://img.shields.io/badge/PRs-welcome-00b8d4"></a>
</p>

<p align="center">
  <a href="https://kdev-cli.pages.dev"><b>Website</b></a> ·
  <a href="https://kdev-cli.pages.dev/docs/">Docs</a> ·
  <a href="https://kdev-cli.pages.dev/docs/how-it-works/">How it works</a> ·
  <a href="https://kdev-cli.pages.dev/docs/commands/">Commands</a> ·
  <a href="https://github.com/tushar-mahalya/kdev/blob/main/CHANGELOG.md">Changelog</a>
</p>

## Why kdev

Kaggle gives every account free GPU hours, but only inside a notebook. kdev
turns that notebook into a machine you can `ssh` into or open in VS Code.

- **One box, pooled quota.** Every account in your Kaggle group runs the same
  shared notebook on its own weekly GPU hours. When one runs short, `kdev up`
  asks which account takes over.
- **Files that persist.** Whatever is in `/kaggle/working` when a session ends
  comes back at the next start, however it ended: `kdev down`, a timeout, a
  crash, your wifi dropping.
- **A real server.** A stable hostname over Cloudflare Tunnel, public-key SSH,
  and your notebook's full environment (CUDA, Python, Kaggle variables).
  No port is opened on the box.

## Install

Needs Python 3.11+, `ssh`, and a Kaggle account in a Kaggle group.

```bash
uv tool install kdev-cli      # or: pipx install kdev-cli
```

The package is `kdev-cli`; the command it installs is `kdev`. Upgrade with
`uv tool upgrade kdev-cli`.

## Quick start

```bash
kdev setup      # once per machine: SSH key, cloudflared, Kaggle sign-in, workspace, tunnel
kdev up         # start the box, or connect to the one already running
ssh kaggle      # or VS Code → Remote-SSH → kaggle
kdev down       # optional: stop early and hand the quota back
```

`kdev setup` only does what is missing, so it is safe to run again. Two parts
of it you may want to run on their own:

- **The tunnel.** With a domain on Cloudflare, `kdev tunnel setup` creates a
  named tunnel with a hostname that never changes. Without one, `kdev setup`
  offers a free quick tunnel, on a new hostname each session.
- **The workspace.** One account owns the notebook and shares it with the
  group: `kdev workspace create --group <your-group-slug>`, once. Everyone else
  runs `kdev workspace join`.

Leaving needs no ceremony: close the laptop and walk away. The box runs to the
end of its session and saves as it stops.

## Everyday commands

| command | what it does |
|---|---|
| `kdev` | the home screen: the box and every account's quota, live; one key per action |
| `kdev up` | start the box, or connect to the running one |
| `kdev down` | stop the box; files are saved however it stops |
| `kdev ssh` | open a shell on the box |
| `kdev status` | running? who started it? when does it end? files back? |
| `kdev forward PORT` | reach a service on the box through localhost (`9000:8888` remaps) |
| `kdev logs` | stream the session's log |
| `kdev history` | past saved sessions, who ran them and how they ended |
| `kdev restore` | finish or repeat the restore of your saved files |
| `kdev backup` | copy the running box's files to this machine |
| `kdev account` | accounts and their GPU/TPU hours left (`add`, `use`, `remove`) |
| `kdev workspace` | the shared notebook (`files`, `join`, `use`, `create`, `share`) |
| `kdev doctor` | check everything kdev depends on |

Run bare `kdev` and press a key: `1` starts the box, `s` shows its status, `q`
quits. The highlighted item always shows the command it runs, so everything
the menu does you can also type or script.

Useful flags on `kdev up`: `--gpu none|t4|p100|tpu`, `--hours N`,
`--account NAME`, `--open code|shell|none`, `--no-restore`, `--replace`, `-y`.
Add `-v` to any command to log each Kaggle API call. `status`, `history`,
`account` and `workspace files` take `--json` for scripts.
See the [full command reference](https://kdev-cli.pages.dev/docs/commands/).

## Settings

Pick once with `kdev config set`, and `kdev up` stops asking:

```bash
kdev config set gpu t4      # none, t4, p100, tpu (and l4, a100, h100 where available)
kdev config set hours 9     # Kaggle's limit: 12h, 9h on TPU
kdev config                 # list every setting; secrets are never shown
```

Other settings cover a git remote that the box clones at boot and pushes every
15 minutes, your SSH key and alias, and the tunnel. They are stored in
`~/.config/kdev/config.json` (mode 0600). All of them:
[Settings](https://kdev-cli.pages.dev/docs/settings/).

## Troubleshooting

```bash
kdev doctor        # every dependency, account token, the workspace and the tunnel's DNS
kdev -v status     # the same command, with each Kaggle API call logged
kdev logs          # what the box itself is saying
```

Most messages name the command that fixes them. Common ones are listed in
[Troubleshooting](https://kdev-cli.pages.dev/docs/troubleshooting/).

## Documentation

- [Install and set up](https://kdev-cli.pages.dev/docs/): the tunnel and the workspace in detail
- [How it works](https://kdev-cli.pages.dev/docs/how-it-works/): the architecture, what `kdev up` does, how files persist, with diagrams
- [Your files](https://kdev-cli.pages.dev/docs/files/) and [Several accounts, several machines](https://kdev-cli.pages.dev/docs/accounts/)
- [Security and limits](https://kdev-cli.pages.dev/docs/security/)
- [Architecture for contributors](https://github.com/tushar-mahalya/kdev/blob/main/docs/ARCHITECTURE.md), including how the code is laid out

## Contributing

Bug reports, fixes, docs and features are welcome. For anything bigger than a
fix, open an issue first. Questions go to
[Discussions](https://github.com/tushar-mahalya/kdev/discussions). How to set
up, test without spending GPU quota, and send a pull request:
[CONTRIBUTING.md](https://github.com/tushar-mahalya/kdev/blob/main/CONTRIBUTING.md).

## Security

Access is public-key SSH only. Kaggle has no API for notebook Secrets, so the
tunnel credentials sit in the private notebook's source, readable by anyone
with Can Edit. Kaggle allows one account per person and restricts tunnelling
out of notebooks; know the rules you are working under. Details are in
[Security and limits](https://kdev-cli.pages.dev/docs/security/). Report a
vulnerability privately, as
[SECURITY.md](https://github.com/tushar-mahalya/kdev/blob/main/SECURITY.md)
describes.

## License

[MIT](https://github.com/tushar-mahalya/kdev/blob/main/LICENSE). kdev is an
independent project, not affiliated with Kaggle, Google or Cloudflare.
