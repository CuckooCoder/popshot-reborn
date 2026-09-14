@echo off
setlocal EnableExtensions
chcp 65001 >nul

rem ==========================================================================
rem  Double-click entry point for packing the client resources:
rem      game_patched\Pack_develop  (plaintext tree, edit here)
rem   -> game_patched\Pack_publish  (encrypted .pkn volumes the client reads)
rem  Incremental: only volumes whose files changed are rewritten.  When the
rem  tree changed, update-gamedata.ps1 is run afterwards so the server-side
rem  tables (terrain, weapons, character props, shop items, icons) follow.
rem  Nothing changed -> both steps are skipped.
rem
rem  Double-click : incremental pack
rem  Command line : build-pack.bat [-Force] [-Check] [-Verify]
rem
rem  *** KEEP THIS FILE ASCII-ONLY ***
rem  Under `chcp 65001` cmd.exe seeks around the batch file by byte offset
rem  while counting characters, so ANY multi-byte (Chinese) text in here --
rem  even inside a rem comment -- drifts that offset and eventually chops a
rem  later command line in half.  All Chinese prompts therefore live in
rem  build-pack.ps1, which PowerShell decodes correctly.
rem  See .claude\FINDINGS.md section 135 and start.bat.
rem ==========================================================================

set "PS1=%~dp0build-pack.ps1"
if not exist "%PS1%" goto :missing

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%ERRORLEVEL%"
goto :end

:missing
echo [ERROR] file not found: %PS1%
set "RC=1"

:end
echo.
pause
exit /b %RC%
