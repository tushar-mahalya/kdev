# kdev

[![ci](https://github.com/tushar-mahalya/kdev/actions/workflows/ci.yml/badge.svg)](https://github.com/tushar-mahalya/kdev/actions/workflows/ci.yml)

**A Kaggle notebook as your remote dev box.** Start it with one command, work
in it from VS Code or a shell, and leave whenever you like. Your files are
there next time — on any account in your Kaggle group, from any machine.

```bash
kdev setup      # once per machine
kdev up         # start the box (or connect to the one that is running)
ssh kaggle      # or VS Code → Remote-SSH → kaggle
kdev down       # optional: stop early and hand the quota back
```

- **One box, pooled quota.** Every account in your group runs the same shared
  notebook on its own 30 GPU-hours a week. When one runs short, `kdev up` asks
  which account takes over.
- **Files that persist.** Whatever is in `/kaggle/working` when a session ends
  comes back at the next start, however it ended: `kdev down`, the Kaggle UI,
  a timeout, a crash, your wifi dropping.
- **A real server.** A stable hostname over Cloudflare Tunnel, public-key SSH,
  your notebook's full environment (CUDA, Python, Kaggle variables) in every
  session, interactive or not.

---

## Contents

- [How it works](#how-it-works)
- [Install and set up](#install-and-set-up)
- [Everyday use](#everyday-use)
- [Your files](#your-files)
- [Several accounts, several machines](#several-accounts-several-machines)
- [Commands](#commands)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)
- [Security and limits](#security-and-limits)
- [Development](#development)

---

## How it works

Kaggle's API can save a notebook and run it, but it has no "give me a shell"
call. kdev writes a small cell at the top of your notebook. When Kaggle runs
that cell, it turns the container into an SSH server and connects it to your
Cloudflare tunnel. Your laptop then reaches it by name.

```mermaid
flowchart LR
    subgraph laptop["Your machine"]
        cli["kdev"]
        code["VS Code / ssh"]
    end
    subgraph kaggle["Kaggle"]
        api["Kaggle API"]
        store[("Notebook versions<br/>= saved /kaggle/working")]
        subgraph box["The box (a notebook session)"]
            cell["kdev's cell:<br/>sshd · cloudflared · restore"]
            work["/kaggle/working"]
        end
    end
    edge(("Cloudflare<br/>edge"))

    cli -- "save + run notebook,<br/>quota, logs" --> api
    api --> box
    cell -- "outbound tunnel" --> edge
    code -- "ssh kaggle" --> edge
    edge --> cell
    work -. "saved when the<br/>session ends" .-> store
    store -. "restored at<br/>the next start" .-> work
```

Nothing listens on a public port: the box dials *out* to Cloudflare, and your
`ssh` reaches it through Cloudflare by hostname.

### What `kdev up` does

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant K as kdev
    participant API as Kaggle API
    participant B as The box
    participant CF as Cloudflare

    You->>K: kdev up
    K->>API: session status (already running?)
    alt a box is running
        K-->>You: connect to it — no second session
    else nothing running
        K->>API: which saved version to build on (walk back from latest)
        K->>API: SaveKernel: your notebook + kdev's cell, "Save & Run All"
        API->>B: start the container
        B->>B: record the plan in .kdev/state.json (before anything slow)
        B->>B: sshd, environment, git clone
        B->>CF: open the tunnel
        B-->>K: KDEV_READY (via the session log)
        K->>API: fresh file list + signed URLs for the saved version
        K->>B: "restore this" (over ssh)
        B->>API: download every file straight from Kaggle
        B->>B: put back symlinks, empty dirs, exec bits
        K->>B: ssh check
        K-->>You: ready — open VS Code or a shell
    end
```

The board you watch while this happens shows each step's own clock, so a slow
queue looks like a queue and not a hang:

```
  alice · T4 ×2 · 9h                    1m52s
   ✓ Submitting                                     1s
   ✓ Waiting for a machine                       1m21s
   ✓ Installing SSH server                         14s
   ✓ Exporting notebook environment                 0s
   ✓ Syncing workspace                              0s
   ✓ Starting Cloudflare tunnel                     4s
   ⠹ Restoring your files                           9s
     812/1505 files
   ○ Ready
```

---

## Install and set up

Needs Python 3.11+, `ssh`, and a Kaggle account in a Kaggle group.

```bash
uv tool install git+https://github.com/tushar-mahalya/kdev          # latest
uv tool install git+https://github.com/tushar-mahalya/kdev@v0.1.0   # a release
kdev setup
```

The repository is private, so `git` needs your GitHub login once:
`gh auth login && gh auth setup-git`. `pipx install git+https://…` works the
same way. Upgrade with `uv tool upgrade kdev`.

`kdev setup` does only what is missing, so it is safe to run again at any
time. It will:

1. find (or create) your SSH key;
2. fetch Cloudflare's `cloudflared` if you don't have it — a system install
   (`brew install cloudflared`) always wins;
3. sign a Kaggle account in, through your browser (OAuth);
4. join your group's workspace notebook, or create one;
5. set up the tunnel — **skipped on a second machine**, which takes the tunnel
   from the notebook itself.

### The tunnel

With a domain on Cloudflare (free plan, domain showing *Active*):

```bash
kdev tunnel setup     # opens a browser once to authorise your Cloudflare account
```

It creates the tunnel and its DNS record, and stores the credentials. There's
nothing to configure in the Zero Trust dashboard. Re-running it is a no-op; it
never repoints a hostname that belongs to something else without asking.

|  | Named tunnel (your domain) | Quick tunnel (no domain) |
|---|---|---|
| Setup | once, ~2 minutes | none |
| Hostname | the same forever | new every session |
| After a network drop | same name, `ssh` just reconnects | new name; `kdev up` finds it |
| Speed | identical | identical |

### The workspace

A Kaggle group can't own anything, so one account owns the notebook and shares
it with the group as **Can Edit**:

```bash
kdev workspace create --group <your-group-slug>   # the owner, once
kdev workspace join                               # everyone else
```

---

## Everyday use

```bash
kdev                  # overview: the box, every account's quota, what to do next
kdev up               # start the box, or connect to the one already running
kdev status           # running? who started it? when does it end? files back?
kdev down             # stop it now
```

Bare `kdev` in a terminal ends with a menu of the actions that make sense
right now: connect or stop when the box is up; start it, or switch account,
when it isn't.

The box you start is a setting — pick once, and `kdev up` stops asking:

```bash
kdev config set gpu t4
kdev config set hours 9
```

**Leaving needs no ceremony.** Close the laptop, lose the wifi, walk away.
The box runs to the end of its session and saves as it stops. `kdev down` is
for stopping *early*, to hand the quota back.

---

## Your files

`/kaggle/working` behaves like a disk that is always there.

**Kaggle saves it.** When a "Save & Run All" session ends, Kaggle keeps
`/kaggle/working` as that version's output — and it does so however the
session ends. A clean exit, a crash and a cancel were all measured to save
every file up to the moment the container stopped. Older versions stay
readable (`v1`, `v2`, …), and they belong to the **notebook**, not to an account.

**kdev puts it back.** Kaggle starts every batch session with an empty
`/kaggle/working`; its "Persistence" setting only covers the web editor. So
each `kdev up` works out which saved version you left off at, and the new box
downloads it before you connect.

```mermaid
stateDiagram-v2
    direction LR
    [*] --> Running: kdev up
    Running --> Saved: the session ends, however it ends
    Saved --> Running: kdev up, any account or machine
```

| how the last session ended | what the next `kdev up` gives you |
|---|---|
| `kdev down` | everything |
| **Stop session** in the Kaggle UI | everything (a cancel still saves) |
| the session's time ran out | everything (the box stops itself 2 min early) |
| the tunnel process died | everything |
| the box crashed | everything, up to the crash |
| your laptop or network dropped | the box is still running: `kdev up` connects to it |
| Kaggle's machine itself failed | not testable; a git remote covers it |

### What kdev adds on top of Kaggle

Kaggle's saved output keeps every regular file — thousands of them, dotfiles,
`.git`, large binaries. It **drops symlinks and empty directories, and loses
executable bits**, which would break a Python virtualenv. The box records
those in `.kdev/meta.json` every 30 seconds, and the restore puts them back.

### Picking the version to restore

```mermaid
flowchart TD
    start["latest saved version vN"] --> empty{"has files?"}
    empty -- no --> back["try v(N-1)"] --> empty
    empty -- yes --> state{"its .kdev/state.json says"}
    state -- "restored: true<br/>(or kdev never touched it)" --> one["restore vN alone"]
    state -- "restored: false<br/>(cut short, or worked in<br/>before the restore)" --> stack["restore its base layers,<br/>then vN on top"]
```

A version with no files is a source-only save (group setup, a Quick Save), so
kdev walks past it. A session that was stopped before its restore finished —
or that you worked in before restoring — is not skipped: it gets stacked on
the versions it was meant to be built on, so nothing done in it is lost.

A restore never overwrites a file written in the current session, so it is
safe to run again at any time:

```bash
kdev restore               # finish or repeat the restore on the running box
kdev restore --from v12    # roll back to an older version
kdev workspace files       # what the latest version holds (--version v12 for another)
kdev backup                # optional: copy the box to this machine
```

### Your own cells in the notebook

kdev's cell sits on top, and **nothing below it runs**. When a session ends,
later cells are blanked for that run, so a training cell can't start after
`kdev down` and use quota. Your cells stay in the notebook, untouched.

### Files made in the Kaggle web editor

The editor's interactive session is the one place kdev can't reach: its files
only reach the API when a version is saved. Kaggle describes Quick Save as
saving the notebook "as displayed", which isn't a clear promise to save the
working directory, so check with `kdev workspace files`. Everything made
through kdev is covered.

---

## Several accounts, several machines

```mermaid
flowchart TB
    subgraph group["Kaggle group (Can Edit on one notebook)"]
        a["account A · 30h GPU/week"]
        b["account B · 30h GPU/week"]
        c["account C · 30h GPU/week"]
    end
    nb[("the shared notebook<br/>+ its saved versions")]
    a & b & c -- "each runs it on<br/>its own quota" --> nb
    l1["laptop 1"] & l2["laptop 2"] -- "kdev up / ssh" --> nb
```

- **Quota is per account.** A session is charged to the account that starts
  it, not to the notebook's owner (measured).
- **Switching is a question, not a wall.** If the active account can't finish
  the session you asked for, `kdev up` shows every account's remaining hours
  and lets you pick. `kdev account use <name>` switches by hand.
- **The box is still running?** `kdev up` from any account or machine
  connects to it instead of starting a second one.
- **A new laptop.** `kdev setup` signs you in and joins the workspace. The
  tunnel and git settings come from the notebook, and the new laptop's SSH
  key is saved in the workspace (`.kdev/authorized_keys`). So every machine
  that has used it can reach every later box.
- **Stopping a box someone else started.** Kaggle only lets the account that
  started a session cancel it. When the box doesn't answer, `kdev down` finds
  out which account that was and cancels as it — or tells you which account
  has to.

```bash
kdev account add alice    # sign an account in (browser); re-run to refresh it
kdev account              # every account and its GPU/TPU hours left
kdev account use alice    # it runs the next box
```

---

## Commands

| command | what it does |
|---|---|
| `kdev` | overview of the box, accounts and workspace; in a terminal, the next actions |
| `kdev setup` | get this machine ready; only does what is missing |
| `kdev up` | start the box, or connect to the running one |
| `kdev down` | stop the box; files are saved however it stops |
| `kdev ssh [-- ARGS]` | shell on the box; ssh's exit code is kdev's |
| `kdev status [--json]` | running?, started by, ends at, reachable, restore state |
| `kdev logs [-n N] [--all]` | the session log |
| `kdev restore [--from vN \| --from-backup]` | put saved files back |
| `kdev backup` | copy the running box to this machine |
| `kdev account [--json]` | accounts and quota |
| `kdev account add \| use \| remove` | manage accounts |
| `kdev workspace` | the notebook: owner, sharing, members, saved versions |
| `kdev workspace files [--version vN] [--json]` | what a saved version holds |
| `kdev workspace join \| use \| create \| share` | choose, make, or re-share the notebook |
| `kdev config` / `set KEY VALUE` / `unset KEY` | settings |
| `kdev tunnel` / `tunnel setup` | the Cloudflare tunnel |
| `kdev doctor [--fix]` | check everything; exits 1 if something is broken |

Useful flags on `kdev up`: `--gpu none|t4|p100|tpu`, `--hours N`,
`--account NAME`, `--open code|shell|none`, `--no-restore`, `--replace`, `-y`.

Global: `-v` logs every Kaggle API call with its status and timing;
`--version`; `--install-completion` for shell completion.

**Exit codes:** `0` success · `1` failed (the message says why and what to
run) · `2` usage mistake · `130` cancelled.

**Scripting:** every prompt has a flag, and a prompt never appears without a
terminal. `status`, `account` and `workspace files` take `--json` (data on
stdout, errors on stderr). `NO_COLOR` is honoured.

---

## Settings

`kdev config` lists them; secrets are never shown.

| setting | meaning |
|---|---|
| `gpu` | accelerator for `kdev up`: `none`, `t4`, `p100`, `tpu` (and `l4`, `a100`, `h100` where your account has them) |
| `hours` | session length for `kdev up` (Kaggle's limit: 12h, 9h on TPU) |
| `git-remote` | a repo cloned at boot, pushed every 15 minutes and at teardown |
| `git-deploy-key` | path to that repo's deploy key |
| `ssh-key` | path to this machine's public key |
| `ssh-alias` | the name in `~/.ssh/config` (default `kaggle`) |
| `tunnel-hostname` | the box's hostname |
| `tunnel-token` | a Cloudflare-dashboard tunnel token, instead of `kdev tunnel setup` |

Stored in `~/.config/kdev/config.json` (mode 0600). Move it with
`KDEV_CONFIG_DIR`.

---

## Troubleshooting

```bash
kdev doctor        # every dependency, account token, the workspace and the tunnel's DNS
kdev -v status     # the same command, with each Kaggle API call logged
kdev logs          # what the box itself is saying
```

| you see | what to do |
|---|---|
| `… is not signed in` | `kdev account add <name>` refreshes that account |
| `ssh does not answer` right after starting | give Cloudflare a minute; `kdev ssh` |
| `The session never reported a tunnel` | `kdev logs`; the message names the command to stop it |
| `Only X can cancel session …` | sign X in (`kdev account add`) or stop it in the Kaggle UI |
| `Not all your files are back yet` | `kdev restore` finishes it |
| `… has a session kdev cannot reach` | someone is using the notebook in the Kaggle editor |

---

## Security and limits

- **Access** is public-key SSH only; password login is disabled. The box
  accepts every key saved in the workspace's `.kdev/authorized_keys`: remove a
  line there to revoke a machine.
- **Secrets in the notebook.** Kaggle has no API for notebook Secrets, so the
  tunnel credentials (and a git deploy key, if you set one) sit in the private
  notebook's source. Anyone with Can Edit can read them. Scope a deploy key to
  one repository.
- **Nothing sensitive in crash reports.** kdev never prints tracebacks with
  local variables; `-v` shows a traceback when you ask for one.
- **Kaggle's limits:** 12h per CPU/GPU session and 9h on TPU; weekly GPU/TPU
  quota per account; 20 GB of `/kaggle/working` saved per version.
- **Kaggle's terms.** Kaggle allows one account per person, and restricts
  tunnelling out of notebooks; accounts have been banned for both. kdev does
  nothing to hide what it does. Know the rules you are working under.

---

## Development

```bash
uv sync
uv run pre-commit install        # hooks on commit, tests on push
uv run pytest                    # 180 tests, no network needed
uv run kdev -v …                 # the working tree, with API logging
```

Every commit runs formatting, lint, a secrets scan (`detect-secrets`,
private-key detection), config validation and a lockfile check; every push
runs the tests. CI runs the same hooks, the tests on Python 3.11–3.13, a
build-and-install check, and `gitleaks` over the full history.

**Releasing:** bump `version` in `pyproject.toml`, add a `CHANGELOG.md`
section, then `git tag vX.Y.Z && git push origin vX.Y.Z`. The release workflow
checks that the tag matches the version, runs the tests, and publishes a
GitHub release with the wheel and sdist attached.

```mermaid
flowchart LR
    cli["cli.py<br/>entry point,<br/>one error boundary"]
    cmds["commands/<br/>box · account · workspace<br/>config · tunnel · doctor · home"]
    logic["session · persistence<br/>quota · notebook · bootstrap"]
    edges["api · auth · config<br/>tunnel · cloudflared · sshcfg"]
    ui["ui.py<br/>the design system"]
    kbox["kdev_box.py<br/>runs on the box"]

    cli --> cmds --> logic --> edges
    cmds --> ui
    logic -. "bootstrap ships it<br/>inside kdev's cell" .-> kbox
```

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
