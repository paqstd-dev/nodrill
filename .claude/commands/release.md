---
description: Prepare a release commit, stopping before the tag is pushed
argument-hint: <version, e.g. 0.2.0>
allowed-tools: Bash(make:*), Bash(git status:*), Bash(git diff:*), Bash(git log:*), Read, Edit
---

Prepare the release of version `$1`.
Pushing the tag publishes to PyPI, so stop before that and hand it back to me.

1. Refuse to continue if the working tree is dirty or the branch is not `main`.
2. Set `__version__` in `src/nodrill/__init__.py` to `$1`.
   That is the only place the version lives; hatch reads it from there.
3. Run `make -k`.
   Every target has to pass — this is the last gate before an immutable upload.
4. Run `make build` so the sdist, the wheel and `twine check --strict` are exercised locally rather than for the first time in `publish.yml`.
5. Show me `git diff` and the commands to finish the release, without running them:

   ```bash
   git commit -am "Release $1"
   git tag v$1
   git push origin main v$1
   ```

Do not commit, tag or push.
There is no changelog file: summarise what changed since the previous tag, in the shape of release notes I can paste into the GitHub release.
