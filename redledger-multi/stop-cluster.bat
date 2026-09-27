@echo off
chcp 65001 >nul
title Stop RedLedger Cluster

echo Stopping Router and Workers...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8766 :8771 :8772 :8773"') do (
    taskkill /F /PID %%a 2>nul
)
echo RedLedger cluster stopped.
pause
