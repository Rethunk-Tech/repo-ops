# Security Policy

## Reporting a Vulnerability

Rethunk-Tech takes security seriously. To report a vulnerability in repo-ops:

- **Do not** open a public GitHub issue for security vulnerabilities.
- Submit a **[private security advisory](https://github.com/Rethunk-Tech/repo-ops/security/advisories/new)** on GitHub (preferred).
- Include as much detail as possible: affected versions, reproduction steps, potential impact, and suggested remediation if known.

We will acknowledge receipt within 48 hours and aim to triage and respond within 7 days for confirmed issues.

## Reporting a Code of Conduct Violation

Code of Conduct violations should be reported privately — **not** via public issues or security advisories.

- Contact a [Rethunk-Tech organization owner](https://github.com/orgs/Rethunk-Tech/people) on GitHub through a private message, or
- Use any **Contact maintainers** link shown on the [repository home page](https://github.com/Rethunk-Tech/repo-ops).

All complaints will be reviewed and investigated promptly and fairly. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for enforcement guidelines.

## Supported Versions

This project follows semantic versioning. Only the latest minor version receives security fixes. Older versions should be upgraded to receive patches.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| < latest | :x:                |

## Scope

repo-ops is internal tooling for repository maintenance. Most attack surface is local CLI usage; however, any dependency or workflow-related issues are in scope.
