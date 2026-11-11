# Security & privacy

QuotaPill runs locally and queries the CodexBar CLI. It does not embed API keys,
credentials, cookies or OAuth tokens in the repository. Do not submit credentials
in logs, bug reports or screenshots. Upstream CodexBar is responsible for fetching
and authenticating against quota sources; see its own documentation and license.

The installer does not edit `~/.codexbar/config.json` or any authentication file.
It downloads the official CLI release only when no existing executable is found,
verifies the matching release SHA-256 checksum, and never runs third-party installers.

SHA-256 checksum verification helps detect download corruption or tampering in transit;
it does not constitute a publisher identity/signature verification, and it does not
independently defend against a compromised upstream release account.

For security issues, avoid posting sensitive material publicly; contact the repository
maintainer through an appropriate private channel if available.
