# Security policy

## Supported versions

Security fixes go into the latest release only. Upgrade with
`uv tool upgrade kdev-cli` (or `pipx upgrade kdev-cli`).

| Version | Supported |
|---|---|
| latest release | ✅ |
| anything older | ❌ |

## Reporting a vulnerability

Please report it **privately** through GitHub's private vulnerability reporting:
**<https://github.com/tushar-mahalya/kdev/security/advisories/new>**. Don't open
a public issue, discussion or pull request for it.

Tell us, as far as you can:

- what an attacker can do, and what they need first (access to your machine,
  Can Edit on the workspace notebook, a position on the network…);
- the steps to reproduce it, with the kdev version (`kdev --version`) and your
  operating system;
- a suggested fix, if you have one.

What happens next:

1. **Within 7 days** you get a reply confirming we have it.
2. We assess it and keep you updated while we work on a fix.
3. The fix ships in a new release. We aim to do that **within 90 days**, and
   much sooner for anything serious.
4. We publish a GitHub Security Advisory with the details, crediting you unless
   you would rather not be named. Please keep the issue private until then.

## Verifying a release

Every release from 0.1.4 on is built and published by GitHub Actions, with a
Sigstore-signed build provenance for its wheel and sdist. To check that a file
was built from this repository by its release workflow:

```bash
gh attestation verify kdev_cli-0.1.4-py3-none-any.whl --repo tushar-mahalya/kdev
```

The same signed provenance is attached to each GitHub release as
`kdev_cli-<version>.sigstore.json`, for checking without going to GitHub:
`gh attestation verify <file> --repo tushar-mahalya/kdev --bundle kdev_cli-<version>.sigstore.json`.
The files on PyPI carry their own attestations, shown under "Verified details"
on the project page.

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
