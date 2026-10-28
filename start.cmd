@echo off
cd /d "%~dp0"
docker compose up -d
if errorlevel 1 exit /b 1
if not exist ".venv\Scripts\python.exe" python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
pushd frontend
call npm.cmd install
if errorlevel 1 (popd & exit /b 1)
call npm.cmd run build
if errorlevel 1 (popd & exit /b 1)
popd
echo Open http://127.0.0.1:8000 in your browser. Press Ctrl+C to stop the app.
.venv\Scripts\python.exe -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
