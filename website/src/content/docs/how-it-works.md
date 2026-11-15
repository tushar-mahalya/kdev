---
title: "How it works"
description: "What kdev writes into your notebook, and what happens between kdev up and a shell."
group: Concepts
order: 3
---

Kaggle's API can save a notebook and run it, but it has no "give me a shell"
call. kdev writes a small cell at the top of your notebook. When Kaggle runs
that cell, it turns the container into an SSH server and connects it to your
Cloudflare tunnel. Your laptop then reaches it by name.

> **Your machine** runs `kdev`, which talks to the **Kaggle API** to save
> and run the notebook, read quota and stream logs. **The box** (a notebook
> session) runs kdev's cell: `sshd`, `cloudflared` and the restore. Your
> `ssh` goes to **Cloudflare's edge**, which forwards to the box's outbound
> tunnel. When the session ends, `/kaggle/working` is saved as a **notebook
> version**, and the next start restores it.

Nothing listens on a public port: the box dials *out* to Cloudflare, and your
`ssh` reaches it through Cloudflare by hostname.

## What `kdev up` does

1. **Is a box already running?** kdev asks the Kaggle API for the session
   status. If one is up, it connects to it: no second session.
2. **Which saved version to build on.** It walks back from the latest version
   to the one that holds your files.
3. **Save & Run All.** Your notebook goes up with kdev's cell on top
   (`SaveKernel`), and Kaggle starts the container.
4. **The box sets itself up.** It records the plan in `.kdev/state.json` before
   anything slow, then starts `sshd`, exports the notebook environment and
   clones your repo.
5. **The tunnel opens.** `cloudflared` dials out to Cloudflare and the box
   reports `KDEV_READY` through the session log.
6. **Restore.** kdev fetches a fresh file list with signed URLs for the saved
   version and tells the box to restore it over ssh. The box downloads every
   file straight from Kaggle, then puts back symlinks, empty directories and
   exec bits.
7. **Hand over.** A final ssh check, and kdev opens VS Code or a shell.

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
