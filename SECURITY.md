# Security

## Reporting a vulnerability

Please report it privately through
**GitHub → Security → Report a vulnerability** on this repository, not in a
public issue. You will get a reply within a week.

## What kdev handles

- **Kaggle OAuth tokens**, one per account, in `~/.config/kdev/creds/` (mode 0600).
- **Cloudflare tunnel credentials** and, if configured, a **git deploy key**, in
  `~/.config/kdev/config.json` (mode 0600).
- Kaggle has no API for notebook Secrets, so the tunnel credentials and deploy
  key are written into the private notebook's source, where anyone with
  *Can Edit* on it can read them. Scope a deploy key to one repository.
- The box accepts every public key listed in the workspace's
  `.kdev/authorized_keys`; delete a line there to revoke a machine.

kdev never prints tokens or credentials, including in crash reports.
