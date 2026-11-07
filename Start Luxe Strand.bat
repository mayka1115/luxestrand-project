@echo off
setlocal
cd /d "%~dp0"
if not defined PORT set "PORT=4000"

if exist ".venv\Scripts\python.exe" goto verify_python
where py >nul 2>nul
if errorlevel 1 goto missing_python
py -3 -m venv .venv
if errorlevel 1 goto venv_failed

:verify_python
".venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if errorlevel 1 goto old_python

".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto install_failed

:register_startup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Register Luxe Strand Startup.ps1"
if errorlevel 1 goto startup_failed

powershell.exe -NoProfile -Command "$response = try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/health' -TimeoutSec 2 } catch { $null }; if ($null -ne $response -and $response.StatusCode -eq 200 -and $response.Content.Contains('ok')) { exit 0 }; exit 1"
if not errorlevel 1 goto open_site

start "Luxe Strand Python server - close this window to stop" /D "%~dp0" "%~dp0.venv\Scripts\python.exe" app.py

powershell.exe -NoProfile -Command "$deadline = (Get-Date).AddSeconds(30); do { try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/health' -TimeoutSec 2; if ($response.StatusCode -eq 200 -and $response.Content.Contains('ok')) { exit 0 } } catch {}; Start-Sleep -Seconds 1 } while ((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 goto server_failed

:open_site
start "" "http://127.0.0.1:%PORT%"
endlocal
exit /b 0

:missing_python
echo Python 3.9 or later was not found. Install Python with the py launcher, reopen this launcher, and try again.
pause
exit /b 1

:old_python
echo Python 3.9 or later is required. Upgrade Python and try again.
pause
exit /b 1

:venv_failed
echo Could not create the Python virtual environment.
pause
exit /b 1

:install_failed
echo Could not install project dependencies from requirements.txt.
pause
exit /b 1

:startup_failed
echo Could not add Luxe Strand to your Windows Startup folder.
pause
exit /b 1

:server_failed
echo The Python server did not respond at http://127.0.0.1:%PORT% within 30 seconds.
echo Check the Luxe Strand server window for the error message.
pause
exit /b 1
