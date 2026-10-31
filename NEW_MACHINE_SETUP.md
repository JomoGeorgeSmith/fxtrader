# New machine setup — instructions for the next assistant

**Goal:** install/check dependencies, restore this existing FX Analyst project and start it locally without regenerating Excel data or initiating paid model work. Read `PROJECT_HANDOVER.md` first.

The packaged startup instructions are for **Windows 10/11**, as used on the original machine. If the new computer runs macOS/Linux, adapt environment/launch commands after checking the OS; do not run `start.cmd` or blindly copy Windows paths.

## 1. Restore the files

1. Extract the transfer ZIP to a normal local development folder, for example `C:\Projects\FX Analyst`. Avoid an active OneDrive/network-sync folder while running SQLite and editing Excel.
2. The archive contains one `FX Analyst` project folder. Open that folder in OpenChamber, not just its parent.
3. Preserve the hidden `.git` directory, current uncommitted source files, all four `shared/*.xlsx` workbooks and `runtime/` history/backups. **Do not replace this tree with a fresh GitHub clone.**
4. `TRANSFER_MANIFEST.json` lists packaged files and SHA-256 hashes. Verify it before changing files using the command in `TRANSFER_README.md`.

## 2. Check and install dependencies

The next assistant should inspect what is already installed, then help install **only what is missing** from official sources. Corporate machines may require IT permission, admin access, enabled virtualization, WSL 2 and/or a restart. Do not bypass company restrictions or restart without asking the user. Check the employer's Docker Desktop licensing requirements.

| Dependency | Required version / purpose | Official source |
|---|---|---|
| OpenChamber | Prefer the same current release as the old machine; development workspace | https://github.com/openchamber/openchamber/releases |
| Python | **3.12.x recommended**, Python 3.12+ supported by this project; backend and tests | https://www.python.org/downloads/windows/ |
| Node.js + npm | **Node.js 24.x LTS recommended**, includes npm; frontend build and tests | https://nodejs.org/en/download |
| Git for Windows | Current supported release; history, branch and PR work | https://git-scm.com/downloads/win |
| Docker Desktop | Current supported Windows release, Docker Compose v2, Linux containers; runs Kafka | https://docs.docker.com/desktop/setup/install/windows-install/ |

OpenChamber Desktop bundles a matching OpenCode CLI; normally no separate OpenCode installation is required for Desktop. CLI/web installations differ: follow their official installation documentation, not guessed commands.

Open a **new terminal after installing** so PATH changes are picked up. Check:

```powershell
python --version
py -3.12 --version
node --version
npm.cmd --version
git --version
docker --version
docker compose version
docker info
```

`py -3.12` is an optional Windows launcher check: one working supported Python is sufficient. If `python` opens the Microsoft Store or resolves to an unexpected version, fix PATH/app execution aliases or use the verified `py` launcher to create the venv. Do not assume Python is installed because an alias exists. `docker info` must confirm a running daemon, not merely an installed CLI.

Docker Desktop must be started and switched to **Linux containers**. Enable its required virtualization/WSL configuration with IT approval if necessary. Kafka uses local port **9092**; the FX API uses **8000**. Check conflicts before stopping any process, especially processes unrelated to this project.

## 3. Credentials — configure privately

The archive intentionally excludes `apikey.txt`, `.env*` and provider sign-ins. Install/configure the required provider login for OpenChamber separately. That is distinct from the FX application's OpenAI API key.

For the application, the user should privately configure `OPENAI_API_KEY` on the new machine, or create an ignored `apikey.txt` in the project root using a local editor. Never paste the value into chat, a command argument, screenshots or Git. A credential is only necessary for an approved paid analyst/chat request, not offline tests or restoring saved results. Keep the analyst model `gpt-4.1`.

## 4. Recreate project dependencies; do not copy old executables

`.venv`, `node_modules`, frontend build outputs and Python caches were intentionally excluded. They are machine-specific/rebuildable.

In PowerShell, from the extracted `FX Analyst` folder:

```powershell
# Use the installed, verified Python; prefer 3.12 on Windows.
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Push-Location frontend
npm.cmd ci
npm.cmd run build
Pop-Location
```

If the Python launcher is unavailable but `python` is verified, use `python -m venv .venv` instead. No activation script is needed; use the venv executable directly, avoiding execution-policy changes. `frontend/package-lock.json` is included for reproducible npm installation. A package/version inventory from the old environment is recorded in the transfer manifest for diagnostics, not permission to change models or update dependencies indiscriminately.

Python packages are declared in `requirements.txt`: FastAPI, Uvicorn, openpyxl, kafka-python and LangGraph, plus their dependencies. The frontend lockfile declares React, TypeScript, Vite, lucide-react and type packages. Do not replace requirements or regenerate the lockfile merely to get an install working; diagnose first.

Package downloads and the Kafka image need network access. Those are not paid analyst requests, but still follow company network policies.

## 5. Offline verification before launching

From the project root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
Push-Location frontend
node --experimental-strip-types --test tests/sounds.test.ts tests/reviewNotes.test.mjs tests/recommendedAction.test.ts
npm.cmd run build
Pop-Location
git status --short --branch
```

Expected last-known test counts: **145 backend and 19 frontend tests**. Tests use mocked/offline model behavior. A test that intentionally exercises an unavailable chat can log `Chat answer unavailable (RuntimeError)` while the suite still passes.

Confirm that `shared/` contains `Bank_Balances.xlsx`, `Daily_Rate_Sheet.xlsx`, `Weekly_Disbursements.xlsx` and `FX_Simulation.xlsx`. Do not reseed, synthesize again, change dates, fix perceived opportunities or edit balances as part of installation. Meaningful workbook edits can trigger paid analysis once the API is running.

## 6. Start the app

With Docker Desktop running, either double-click `start.cmd` or use these manual commands after installation/build:

```powershell
docker compose up -d
docker compose ps
.\.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. Keep the API terminal open. `start.cmd` itself starts Kafka, creates a missing venv, installs Python/npm packages, builds the frontend and launches the API; it does **not** install system Python, Docker, Node.js or Git.

Startup should restore saved analysis without creating a run. Safe, read-only checks from another terminal:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
$s = Invoke-RestMethod http://127.0.0.1:8000/api/state
$s | Select-Object stage, active_run_id, message
```

Expected: `stage` ready, `active_run_id` empty, saved-analysis-restored/no-new-analysis message. The transferred audit currently contains 62 analyses and 12 application chat messages; the archive manifest has its exact captured counts. Existing cards predate the new scenario tools and are deliberately not rewritten.

**Do not click Run analyst, submit suggestion chat, POST `/api/analyse` or edit workbooks to test the watcher without explicit paid-run approval.** Keep automatic meaningful-workbook-change analysis enabled. If a save is necessary for migration, stop the API first and restart afterward.

To stop the API use Ctrl+C in its terminal. To stop only this project's Kafka container without deleting it, run `docker compose stop` from this folder. Do not use broad Docker prune/delete commands or stop unrelated company containers.

## 7. OpenChamber context

Add/open the restored folder in OpenChamber and start a new coding session. Paste:

> Read PROJECT_HANDOVER.md, NEW_MACHINE_SETUP.md, TRANSFER_README.md and README.md. Help me install missing dependencies and restore this existing project. Preserve all workbooks, saved history and uncommitted changes. Run only offline checks; do not initiate paid analyst/chat requests without my explicit approval. Keep gpt-4.1 and automatic meaningful-workbook monitoring unchanged.

The ZIP includes **application** chat history in `runtime/audit.sqlite` and a detailed handover of the **development** conversation. It does not transplant OpenCode/OpenChamber's entire session database, browser state, provider logins or other projects. Exact development-chat migration is separate: first check versions/storage locations, stop relevant services and back up both machines. Do not overwrite an existing new-machine OpenCode database.

## Troubleshooting boundaries

- Missing Docker daemon/virtualization: solve installation/service prerequisites; do not replace Kafka or remove event monitoring.
- Blank UI: ensure `npm.cmd run build` succeeded before starting/restart the API so it mounts `frontend/dist`.
- No saved results: check the extracted `runtime/audit.sqlite` and manifest before creating a new database or copying over files.
- Old workbook dates: these are preserved historical simulation inputs, not an installation error. Changing them is a separate user-authorized data task and may trigger paid analysis.
- Provider unavailable: offline restore/setup is still possible; do not launch a paid request to diagnose credentials without approval.
- No executable price or real settlement guarantees: expected POC boundary, not a reason to fabricate bank terms.
