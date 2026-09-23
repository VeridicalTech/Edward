# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.2.x   | yes       |
| < 0.2   | no        |

## Reporting a vulnerability

**Do not open a public issue for security reports.**

Preferred channel: **GitHub Private Vulnerability Reporting** at
https://github.com/VeridicalTech/Edward/security/advisories/new

Fallback: contact the maintainer (@rin259) directly via GitHub.

Please include:

- affected version (`edward --version`) and platform;
- a minimal reproduction (command line, policy file, audit log);
- your assessment of impact, if you have one.

You will get an acknowledgement within 7 days, a fix-or-mitigation timeline
once the report is triaged, and credit in the release notes unless you ask
to stay anonymous.

## Scope

**In scope:** the `edward` package and CLI (`wrap`, `demo`, `eval`, `audit`,
`doctor`, `keygen`, `verify`, `policy-template`), the Ed25519 receipt chain
(signing and verification), the approval server, policy loading, and the
subprocess wrappers that gate the agent.

**Out of scope:** the wrapped coding agent itself (Pi / Codex / custom), the
operator's own scorer endpoint deployment, issues that require physical
access to the machine, and social engineering.

## Safe harbor

We consider security research conducted in good faith — including
vulnerabilities found through automated tooling — to be authorized activity,
provided you respect the privacy of other users, avoid service degradation,
and use confirmed vulnerabilities only to demonstrate the issue.

## Why the surface is small

Edward ships **zero runtime dependencies**, makes no network calls by
default (the scorer endpoint is operator-configured, LAN-oriented), and
signs every intervention decision into a verifiable receipt chain. If you
find that surface larger than it should be, that itself is worth a report.
