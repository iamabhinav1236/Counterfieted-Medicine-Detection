# TrueDose AI — Medicine Verification & Traceability

TrueDose AI is a Flask-based medicine-code verification and traceability application. It includes a medicine registry, QR generation and scanning, explainable rule-based risk indicators, role-specific dashboards, administrator-managed account approvals, CSV import/export, audit events, and a SHA-256 hash-linked ledger.

**Important:** this is a deployable operational pilot, not a regulator-certified medicine-authentication service. A registry match does not prove the physical package is genuine. The risk engine is deterministic rules, not a trained machine-learning model. The ledger is hash-linked SQLite storage, not a decentralized blockchain. Do not use this application as the sole basis for medication decisions.

## Quick start — Windows PowerShell

1. Extract the ZIP and open the `TrueDose_AI` folder in VS Code or a terminal.
2. Create a virtual environment:
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
   If script activation is blocked, use Command Prompt and run `.venv\Scripts\activate.bat`.
3. Install dependencies:
   ```powershell
   python -m pip install -r requirements.txt
   ```
4. Copy `.env.example` to `.env` and replace the placeholder values with unique values. Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Set a strong `TRUEDOSE_ADMIN_PASSWORD` that you keep private.
5. Start the server:
   ```powershell
   python app.py
   ```
6. Open `http://127.0.0.1:5000`.

If the admin password or secret key is missing/left as the example placeholder during local development, the app generates a temporary value and prints the temporary admin password in the terminal. A temporary password changes after a restart; configure `.env` for a stable password. Production/Docker mode refuses to start with placeholder secrets, preventing worker processes from using inconsistent credentials. Never publish `.env` or use the development server directly on the public internet.

## Role dashboards and access

- `/register` — create an account.
- `/login` — sign in; `/portal` opens the role-specific dashboard.
- `/admin/login` — administrator sign-in; `/admin` is the registry and account operations area. `/admin/audit-history` provides searchable, paginated administrator-only audit history.
- Customer accounts are activated immediately. Manufacturer, distributor, and pharmacist accounts remain pending until an administrator approves them. Approval is an application control only; the administrator must independently verify licenses and organization identity before approval.
- Manufacturers can append batch-release/custody events only for registry entries whose manufacturer name matches their organization. Distributors can append dispatch, receipt, and custody-transfer events. Pharmacists and customers have read/verification workflows.
- Account role and organization are not identity credentials. This application does not connect to a licensing authority.

## Main capabilities

- Persistent SQLite registry with add, edit, delete, CSV import/export, and validation.
- Unique code validation and QR PNG generation for registry codes.
- Browser QR scanning where supported, with a private-Wi-Fi IP Webcam stream option.
- Explainable flags for unknown codes, expired batches, recalled/suspended records, suspected-clone status, and repeated scans.
- Scan activity, operational statistics, admin audit records, and health endpoint.
- Password-hashed portal accounts, admin password configuration, CSRF checks on HTML form mutations, HttpOnly/SameSite session cookies, security headers, file upload size limit, and role approval workflow.
- SHA-256 hash-linked traceability events with a chain integrity check.
- Docker and persistent-volume configuration.

## QR and phone camera

For built-in camera scanning, use a browser that supports `BarcodeDetector` and grant camera permission. If that API is unavailable, the page may use its QR decoding fallback loaded from a CDN, so internet access may be needed. For IP Webcam, keep the phone and computer on the same private Wi-Fi and enter the phone's private IPv4 stream URL. The server blocks loopback, link-local, reserved, and non-private IP addresses; only the expected IP Webcam stream paths are accepted. Do not expose this endpoint to untrusted users.

## Verification result meaning

- **Registered record:** a matching code exists and no higher-priority registry warning was found.
- **Unknown code:** no matching code exists in the configured local registry.
- **Expired / recalled / suspended / suspicious:** the configured record carries that warning.
- **Repeated scans:** a code was scanned repeatedly in a short period; this can have legitimate causes and is an investigation signal, not proof of counterfeiting.

All results are based on locally configured data. QR labels can be copied or replaced, and false or stale registry data can lead to false conclusions. A physical authenticity check requires trusted manufacturer/regulator integrations and secure serialized identifiers.

## API endpoints

- `GET /api/health` — service/database health and ledger integrity.
- `GET /api/stats` — aggregate metrics and recent scan summary.
- `GET /api/medicines` — current registry records.
- `POST /api/verify` — JSON body `{"code":"YOUR-CODE"}`; records the attempt and returns risk signals.
- `GET /api/qr?code=YOUR-CODE` — QR PNG for a code that exists in the registry.
- `GET /api/audit` — recent verification attempts.
- `GET /api/ledger/status` — hash-chain integrity status.
- `GET /api/ledger` — public-safe recent ledger metadata and integrity status; private event payloads and account names are not returned by this endpoint.
- `/ledger` — paginated human-readable ledger with full previous/block hashes and integrity status. `/ledger/export.json` exports the full ledger payload for administrators only.
- `/history` — searchable, paginated verification history with filtered CSV export.
- `/admin/audit-history` — protected searchable/paginated administrative audit log with CSV export. Clearing scan history retains a ledger/audit event describing the action.
- `/admin` — password-protected registry, account approvals, audit log, and CSV import/export.

## Local database and backups

The default database is `truedose.db` next to `app.py`. Set `TRUEDOSE_DB_PATH` to a persistent absolute path to relocate it. Stop writes before copying the database file manually, or use SQLite's backup API to avoid an inconsistent copy. Keep encrypted, access-controlled backups and periodically test restoring them. Do not put real patient data in this application; it is not designed to store patient health records.

## Tests

Install dependencies, then run:
```powershell
python -m unittest -v tests.py
```

The tests cover health, registry verification, searchable history and exports, admin-only audit history, CSRF protection, role-account approval, ledger tamper detection, history-clear trace recording, and rejection of unsafe camera proxy addresses. Run them after every code change and on the target machine.

## Docker

1. Copy `.env.example` to `.env` and set unique secrets/passwords.
2. Build and start:
   ```bash
   docker compose up --build -d
   ```
3. Open the configured host port and check `/api/health`.

The Compose file stores SQLite data in a named Docker volume. Configure HTTPS at a trusted reverse proxy before any internet-facing deployment. Set `TRUEDOSE_COOKIE_SECURE=1` only when requests are served over HTTPS.

## Blockchain / ledger scope

Each ledger event stores a previous hash and a SHA-256 hash over canonical event fields. Integrity checks can detect edits to the stored event chain. However, this is **not a distributed blockchain**: it has no consensus, independent validator nodes, external anchoring, or cryptographic identity attestation. An attacker with full database control could rewrite the entire database and recompute the chain. Stronger production assurance requires a permissioned multi-node ledger or independent external anchoring, verified organization identities, key management, managed database infrastructure, monitoring, backups, and security review.

## Before real-world production

Before using this with real pharmaceutical supply chains, arrange a security review; deploy behind HTTPS with production WSGI; use managed database and tested backups; add rate limiting and MFA; integrate authoritative manufacturer/regulator data; enforce serialized identifiers and cryptographic signatures; verify business licenses; establish incident response, privacy, and legal processes; and perform acceptance tests with authorized stakeholders. The app deliberately does not claim that these integrations or regulatory certifications are already present.

## Consistent database backup

Run `python backup_db.py` to create a timestamped SQLite backup and verify its integrity. You can provide a destination path, for example `python backup_db.py backups/truedose-copy.db`. The script reads `TRUEDOSE_DB_PATH` when configured.
