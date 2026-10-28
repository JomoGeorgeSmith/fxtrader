# Read first — FX Analyst transfer

1. Open **`PROJECT_HANDOVER.md`** for the next assistant's working context.
2. Follow **`NEW_MACHINE_SETUP.md`** to install/check Docker, Python, Node.js/npm, Git and OpenChamber, rebuild project dependencies and restore saved results.
3. Keep the project folder intact, including hidden `.git`, `shared/` and `runtime/`. GitHub alone does not contain all of the latest work or the Excel/audit files.

## Included

- Current source **including uncommitted changes**, Git history/branch metadata, tests, docs, policies and branding.
- All four current Excel workbooks; previous experiment backups and snapshot workbooks.
- Saved analyses and application-specific chats in a consistent SQLite backup, plus local result/audit artifacts.
- Local reference documents. This bundle may contain internal banking reference material and conversation content even though it contains no API credentials. Transfer/store it using an employer-approved secure method, not a public download link or public repository.
- A manifest of payload paths/hashes, Git working status, dependency versions and captured history counts.

## Intentionally excluded

- `apikey.txt`, `.env*`, private keys/certificates, provider authentication and known credential files.
- Old `.venv`, `node_modules`, `frontend/dist`, bytecode/build caches and transient lock files.
- Rebuildable calculation caches and database journal/WAL/SHM files (the SQLite online-backup API captures a consistent standalone database instead).
- Git hooks and machine-specific Git credentials/proxy configuration; repository history, index and branch refs are retained.
- The old machine's OpenCode/OpenChamber profile, provider sessions, scheduled tasks and full development-chat database. The handover summarizes that working context; application chat history is a separate thing and is included.
- Docker container images and Kafka's old live event log. Compose recreates Kafka; saved application snapshots/audits are retained. Startup never replays previous-boot events into paid work.

Git's copied configuration is normalized to an HTTPS GitHub origin with no embedded credentials or machine-specific credential hooks. Git objects, branch refs, index and uncommitted working files are retained. You will need to authenticate Git separately before a future push. No commit/push/merge is performed by packaging.

## Verify extracted payload

After installing Python, run this from the extracted project root **before installation changes any files**:

```powershell
@'
import hashlib, json
from pathlib import Path
root = Path.cwd()
m = json.loads((root / 'TRANSFER_MANIFEST.json').read_text(encoding='utf-8'))
bad = []
for item in m['files']:
    p = root / item['path']
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != item['sha256']:
        bad.append(item['path'])
print('PASS: payload hashes match' if not bad else 'FAIL: missing/changed files: ' + ', '.join(bad))
raise SystemExit(bool(bad))
'@ | python -
```

The manifest itself cannot hash itself; keep the ZIP's separate `.sha256` checksum alongside it to detect accidental archive corruption. This is an integrity check, not encryption or proof of trust. The ZIP is not password-encrypted; use secure storage/transport.

No paid model request is needed to extract, verify, install, run offline tests or restore saved results. Do not click Run analyst or submit chat until the user explicitly approves paid testing.
