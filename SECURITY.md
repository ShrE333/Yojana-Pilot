# Security and data handling

Yojana-Pilot provides informational assistance, not official government decisions or submissions. Before a public deployment, add authentication where appropriate, request limits, persistent sessions, a durable webhook queue and suitable operational controls.

## Configuration and citizen information

- Keep credentials in environment variables or an ignored `.env`; `.env.example` contains empty credential fields only.
- Never commit identity numbers, bank details, phone numbers, document uploads, conversation transcripts or private runtime databases.
- Messages submitted to a configured language provider leave the server. Provider and service logging policies may apply.
- Conversation and WAHA state are currently held in memory. This is not durable production case management.
- WAHA is disabled by default and supports opted-in one-to-one text only. Configuration status does not verify connectivity.

## Reviewed public catalog

`data/schemes.db` is deliberately tracked: it is the public scheme catalog required for offline operation, not a citizen database. The publication audit found 267 scheme records, 1,008 crawl records, and an empty metadata table. No citizen/session/account tables or sensitive identity-field keys were found. The SQLite integrity check passed, and its scheme rows matched the remote catalog.

All other database files and SQLite sidecars are ignored. To experiment without changing the bundled catalog, set `SCHEME_DB` to a private ignored path, for example `data/local-runtime.db`.

## Local secret scan

Install development dependencies and scan tracked/staged source without contacting credential providers:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m detect_secrets scan --no-verify
```

Review findings instead of treating a detector as proof that no secret exists. The consolidation audit reviewed two keyword hits in `tests/test_waha_bridge.py`; both are intentionally fictional test credentials. A separate scan of all text files from both existing remote commits found no credential candidates. No real credential was identified that requires rotation in the reviewed history.

If a real credential is committed later, revoke/rotate it first and coordinate any history remediation with the repository owner. Do not silently rewrite shared Git history. Do not report a vulnerability with live credentials or citizen information in a public issue; use the repository owner's private contact channel.
