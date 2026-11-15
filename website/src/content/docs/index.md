---
title: "Install and set up"
description: "Install kdev, sign in to Kaggle, set up the Cloudflare tunnel and the shared workspace notebook."
group: Start
order: 1
---

Needs Python 3.11+, `ssh`, and a Kaggle account in a Kaggle group.

```bash
uv tool install kdev-cli      # or: pipx install kdev-cli
kdev setup
```

The package is `kdev-cli` (PyPI does not allow `kdev`); the command it installs
is `kdev`. Upgrade with `uv tool upgrade kdev-cli`.

To run what is on `main` but not released yet:
`uv tool install git+https://github.com/tushar-mahalya/kdev`.

`kdev setup` does only what is missing, so it is safe to run again at any
time. It will:

1. find (or create) your SSH key;
2. fetch Cloudflare's `cloudflared` if you don't have it — a system install
   (`brew install cloudflared`) always wins;
3. sign a Kaggle account in, through your browser (OAuth);
4. join your group's workspace notebook, or create one;
5. set up the tunnel — **skipped on a second machine**, which takes the tunnel
   from the notebook itself.

## The tunnel

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

## The workspace

A Kaggle group can't own anything, so one account owns the notebook and shares
it with the group as **Can Edit**:

```bash
kdev workspace create --group <your-group-slug>   # the owner, once
kdev workspace join                               # everyone else
```
