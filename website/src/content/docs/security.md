---
title: "Security and limits"
description: "Access, secrets, host keys, concurrent starts, and the rules you work under."
group: Reference
order: 9
---

- **Access** is public-key SSH only; password login is disabled. The box
  accepts every key saved in the workspace's `.kdev/authorized_keys`: remove a
  line there to revoke a machine.
- **Secrets in the notebook.** Kaggle has no API for notebook Secrets, so the
  tunnel credentials (and a git deploy key, if you set one) sit in the private
  notebook's source. Anyone with Can Edit can read them. Scope a deploy key to
  one repository.
- **Host keys.** Every session is a new container with a new host key, so the
  `kaggle` block in `~/.ssh/config` doesn't pin one; a pinned key would break
  VS Code on every other machine. Only a box holding the workspace's tunnel
  credentials can answer on that hostname, and anyone holding those can
  already edit the notebook that builds the box.
- **Simultaneous starts converge on one box.** If several machines run
  `kdev up` together, the lowest live notebook version wins. Each losing
  client stops its own run as the account that started it, waits for the
  cancellation to finish, then connects to the winner. Status, logs and stop
  commands find that winner even when a cancelled version is newer. Startup
  verifies the SSH session before restoring files; cancelled contenders do
  not replace the winner's saved workspace. Their output remains available
  through `kdev restore --from vN`. If Kaggle cannot confirm a cancellation,
  kdev reports the run to stop and does not declare the shared tunnel ready.
- **Metadata in the last 30 s.** After a hard kill, symlinks, empty folders and
  executable bits created in the final 30 seconds are gone. File contents never
  are.
- **Nothing sensitive in crash reports.** kdev never prints tracebacks with
  local variables; `-v` shows a traceback when you ask for one.
- **Kaggle's limits:** 12h per CPU/GPU session and 9h on TPU; weekly GPU/TPU
  quota per account; 20 GB of `/kaggle/working` saved per version.
- **Kaggle's terms.** Kaggle allows one account per person, and restricts
  tunnelling out of notebooks; accounts have been banned for both. kdev does
  nothing to hide what it does. Know the rules you are working under.
