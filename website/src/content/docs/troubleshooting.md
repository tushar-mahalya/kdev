---
title: "Troubleshooting"
description: "What to run when something looks wrong, and what each message means."
group: Reference
order: 8
---

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
| `your last box is still saving your files` | nothing: `kdev up` waits, then starts |
| `… has a session kdev cannot reach` | someone is using the notebook in the Kaggle editor |
