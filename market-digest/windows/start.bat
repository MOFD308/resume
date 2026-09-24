@echo off
chcp 65001 >nul
cd /d "%~dp0.."
title Market Digest（关闭此窗口 = 停止程序）
if not exist ".venv\Scripts\python.exe" (
  echo 还没安装，请先双击 windows\install.bat
  pause
  exit /b 1
)
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
echo Market Digest 正在运行。看板：http://localhost:8000
echo 这个窗口可以最小化，但不要关闭；关闭 = 停止程序。
echo.
rem Open the dashboard a few seconds after the server starts.
start "" /b cmd /c "timeout /t 6 >nul & start http://localhost:8000"
".venv\Scripts\python.exe" -m market_digest run --host 0.0.0.0 --port 8000
echo.
echo 程序已停止。如有报错请截图发给 Claude。
pause
