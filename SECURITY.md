# Security Policy

## Reporting a Vulnerability

Rethunk-Tech takes security seriously. To report a vulnerability in repo-ops or any Rethunk-Tech project:

- **Do not** open a public GitHub issue for security vulnerabilities.
- Email security reports privately to the maintainers via the Rethunk-Tech organization contact channels (or use the security advisory feature on GitHub if available for the repository).
- Include as much detail as possible: affected versions, reproduction steps, potential impact, and suggested remediation if known.

We will acknowledge receipt within 48 hours and aim to triage and respond within 7 days for confirmed issues.

## Supported Versions

This project follows semantic versioning. Only the latest minor version receives security fixes. Older versions should be upgraded to receive patches.

| Version | Supported          |
| ------- | ------------------ |
| latest  | :white_check_mark: |
| < latest | :x:                |

## Scope

repo-ops is internal tooling for repository maintenance. Most attack surface is local CLI usage; however, any dependency or workflow-related issues are in scope.