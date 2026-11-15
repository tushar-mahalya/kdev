---
title: "Settings"
description: "Every setting kdev config knows, and where it is stored."
group: Reference
order: 7
---

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
