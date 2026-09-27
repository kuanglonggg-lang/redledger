@echo off
chcp 65001 >nul
title RedLedger Multi-Group Cluster Manager
cd /d "E:\RedLedger\redledger-multi"

set PYTHON_EXE=E:\RedLedger\server\RedLedgerServer\_internal\python.exe
if not exist "%PYTHON_EXE%" set PYTHON_EXE=python

echo ========================================================
echo   RedLedger Multi-Group High-Performance Cluster
echo ========================================================
echo Starting Cluster Supervisor...
"%PYTHON_EXE%" cluster_manager.py
pause
