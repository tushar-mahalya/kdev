## What and why

<!-- The problem this solves, and how. Link the issue: "Fixes #123". -->

## How I checked it

<!-- Tests you added or ran. If it touches the box, the tunnel or the restore:
     what you ran against real Kaggle (CPU boxes: `kdev up --gpu none`). -->

## Checklist

- [ ] A bug fix comes with a test that fails without it; new behaviour with a test that would catch it breaking
- [ ] `uv run pytest` and `uv run pre-commit run --all-files` pass locally
- [ ] What users will notice is under `## [Unreleased]` in `CHANGELOG.md`
- [ ] The README is updated if a command or its behaviour changed
- [ ] Any AI-generated code has been reviewed line by line by me, and I can explain it

AI tools used (if any):
