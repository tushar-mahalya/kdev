---
title: "Commands"
description: "Every kdev command, its flags, exit codes and scripting behaviour."
group: Reference
order: 6
---

> `kdev forward`, `kdev history` and the 15- and 5-minute end-of-session
> warnings are on `main` and ship in the next release. To use them now:
> `uv tool install git+https://github.com/tushar-mahalya/kdev`.

| command | what it does |
|---|---|
| `kdev` | the home screen: the box and every account's quota, live, with one key per action |
| `kdev setup` | get this machine ready; only does what is missing |
| `kdev up` | start the box, or connect to the running one |
| `kdev down` | stop the box; files are saved however it stops |
| `kdev ssh [-- ARGS]` | shell on the box; ssh's exit code is kdev's |
| `kdev forward PORT...` | forward localhost ports to services on the box (`9000:8888` remaps a port) |
| `kdev status [--json]` | running?, started by, ends at, reachable, restore state |
| `kdev history [-n N] [--json]` | recent saved sessions, who ran them and how they ended |
| `kdev logs [-n N] [--all]` | the session log |
| `kdev restore [--from vN \| --from-backup]` | put saved files back |
| `kdev backup [--force]` | copy the running box to this machine (refuses a box that never finished restoring, unless `--force`) |
| `kdev account [--json]` | accounts and quota |
| `kdev account add \| use \| remove` | manage accounts |
| `kdev workspace` | the notebook: owner, sharing, members, saved versions |
| `kdev workspace files [--version vN] [--all] [--json]` | what a saved version holds: each top-level folder and its file count; `--all` lists every file |
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
terminal. `status`, `history`, `account` and `workspace files` take `--json` (data on
stdout, errors on stderr). `NO_COLOR` is honoured.
