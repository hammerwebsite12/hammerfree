@echo off
:: Right-click this file → Run as administrator
taskkill /IM QuickPlay.exe /F >nul 2>&1
timeout /t 2 /nobreak >nul
copy /Y "D:\My Drive\Workspace\plazipanker\dist\QuickPlay.exe" "C:\Program Files (x86)\QuickPlay\QuickPlay.exe"
if errorlevel 1 (
  echo FAILED — Run as Administrator.
  pause
  exit /b 1
)
echo.
echo DEPLOYED OK
dir "C:\Program Files (x86)\QuickPlay\QuickPlay.exe"
pause
