# Security Policy

## Supported Versions

The current `main` branch and the latest GitHub release are supported for security reports.

## Reporting a Vulnerability

Please do not open a public issue for sensitive security reports.

Use GitHub private vulnerability reporting if it is enabled for the repository. If it is not available, contact the maintainer through the support channel listed in `SUPPORT.md` and include:

- A clear description of the vulnerability.
- Reproduction steps or proof of concept.
- Affected versions or commits.
- Expected impact.

## Scope

Relevant reports include vulnerabilities in local file handling, WebView bridge exposure, command execution, model/download handling, and packaged release behavior.

Dependency vulnerabilities should include the package name, version, advisory link, and whether the vulnerable code path is reachable in this app.
