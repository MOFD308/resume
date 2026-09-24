@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
echo ============================================
echo   Market Digest 安装
echo ============================================
echo.

rem --- 1. Python ---------------------------------------------------------
where py >nul 2>nul
if errorlevel 1 (
  echo [错误] 没找到 Python。
  echo 请先到 https://www.python.org/downloads/ 下载安装 Python 3.12，
  echo 安装第一页务必勾选 "Add python.exe to PATH"，装完后重新双击本文件。
  pause
  exit /b 1
)
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
  echo [错误] Python 版本太旧，需要 3.10 或以上。请安装 Python 3.12 后重试。
  pause
  exit /b 1
)
echo [1/4] Python 已就绪

rem --- 2. Virtual environment + packages ---------------------------------
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
)
echo [2/4] 正在安装依赖（第一次需要几分钟）...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
if errorlevel 1 (
  echo [错误] 依赖安装失败，请把上面的报错截图发给 Claude。
  pause
  exit /b 1
)
echo       依赖安装完成

rem --- 3. Deno: yt-dlp needs a JavaScript runtime to download YouTube audio ---
where deno >nul 2>nul
if errorlevel 1 (
  echo [3/4] 正在安装 Deno（下载 YouTube 音频需要）...
  winget install --id DenoLand.Deno -e --accept-package-agreements --accept-source-agreements >nul 2>nul
  if errorlevel 1 (
    echo       自动安装失败。请手动安装：https://deno.com/ （不装的话，没有字幕的视频可能无法转写）
  ) else (
    echo       Deno 安装完成
  )
) else (
  echo [3/4] Deno 已就绪
)

rem --- 4. Secrets file ---------------------------------------------------
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [4/4] 已创建 .env，马上用记事本打开，请填入密钥后保存并关闭记事本。
  notepad ".env"
) else (
  echo [4/4] .env 已存在（如需修改，用记事本打开 market-digest\.env）
)

echo.
echo 安装完成！双击 windows\start.bat 启动；双击 windows\autostart.bat 设置开机自动运行。
pause
