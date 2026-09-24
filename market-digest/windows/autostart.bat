@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Market Digest.lnk"

if exist "%SHORTCUT%" (
  choice /m "已设置开机自启。要取消吗"
  if errorlevel 2 exit /b 0
  del "%SHORTCUT%"
  echo 已取消开机自启。
  pause
  exit /b 0
)

rem Startup-folder shortcut (no admin rights needed); window starts minimised.
powershell -NoProfile -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:SHORTCUT);" ^
  "$s.TargetPath = '%~dp0start.bat'; $s.WorkingDirectory = '%~dp0..'; $s.WindowStyle = 7; $s.Save()"
if errorlevel 1 (
  echo 设置失败，请截图发给 Claude。
  pause
  exit /b 1
)
echo 已设置：以后开机登录后，程序会自动在后台（最小化窗口）启动。

choice /m "是否同时关闭电脑“插电时自动睡眠”（睡眠时程序会暂停）"
if errorlevel 2 goto done
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
echo 已关闭插电时的自动睡眠（显示器仍会按原设置自动关闭，不影响程序）。
:done
pause
