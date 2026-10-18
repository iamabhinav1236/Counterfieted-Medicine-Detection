# Release notes

## Final project package

- Consolidated the verification website, QR workflow, admin registry, role workspaces, and traceability ledger.
- Removed branded team references and pre-populated fictional registry entries.
- Added pending approval for manufacturer, distributor, and pharmacist accounts.
- Added CSRF validation to account and administrator HTML forms.
- Added role checks for supply-chain events and manufacturer registry scope.
- Improved hash-ledger append concurrency and added integrity regression coverage.
- Hardened the private-network IP Webcam proxy against loopback and non-private targets.
- Fixed flagged-scan metrics and repeated-scan warning priority.
- Added Docker Compose persistence, production secret validation, and SQLite backup utility.
