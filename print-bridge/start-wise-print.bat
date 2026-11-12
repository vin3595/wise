@echo off
REM Edit the two values, then put this file in the Startup folder (Win+R, shell:startup).
cd /d "%~dp0"
python wise_print_bridge.py --server https://your-wise-address --key YOUR_PRINT_KEY
pause
