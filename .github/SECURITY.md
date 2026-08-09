# Security Policy

## Supported versions

Only the latest released version is supported.
Fixes ship in a new release rather than as patches to older ones.

nodrill has no runtime dependencies and does not parse untrusted input, so its realistic security surface is narrow: values leaking across a context boundary they should not cross — between threads, between asyncio tasks, or out of a `provider` block that has exited.
Reports in that shape are treated as security issues, not ordinary bugs.

## Reporting a vulnerability

Report privately through GitHub: [open a draft security advisory](https://github.com/paqstd-dev/nodrill/security/advisories/new). Please do not open a public issue for a suspected vulnerability.

Include the smallest program that reproduces the leak, the Python version, and whether threads or asyncio are involved.

Expect an acknowledgement within a week.
Once a fix is ready it is released and the advisory is published with credit, unless you would rather not be named.
