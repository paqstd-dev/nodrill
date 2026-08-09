---
paths:
  - ".github/workflows/*.yml"
---

# GitHub Actions

`make audit` runs zizmor over these files and CI runs it again, so the hardening below is checked, not aspirational.
Run the audit before pushing rather than letting CI report it.

- Third-party actions are pinned to a full commit SHA with the tag in a trailing comment.
  A moving tag on its own is a finding.
- The file starts at `permissions: {}`.
  A job that needs a token grants it itself, narrowly, with a comment saying what for.
- `actions/checkout` passes `persist-credentials: false` unless the job actually pushes.
- The uv version is pinned once in `env.UV_VERSION` and read from there by every `setup-uv` step; bump it in one place.
- `publish.yml` keeps build and publish as separate jobs on purpose: the build disables the uv cache so a poisoned cache cannot reach a published wheel, and `id-token: write` for Trusted Publishing exists only on the publish job.
  Uploading is `pypa/gh-action-pypi-publish`, not `uv publish`, because only the action generates PEP 740 attestations.
- CI and the `Makefile` run the same commands in the same order.
  Changing one without the other is how the local gate stops meaning anything.
