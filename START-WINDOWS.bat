@echo off
chcp 65001 >nul
title Niche Finder
cd /d "%~dp0"
echo.
echo  =====================================================
echo     Niche Finder - запуск
echo     Не закрывайте это окно, пока пользуетесь сервисом
echo  =====================================================
echo.

rem ---------- 1. Python
set "PY="
python -c "import sys; assert sys.version_info>=(3,10)" >nul 2>nul && set "PY=python"
if not defined PY py -3 -c "import sys; assert sys.version_info>=(3,10)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  echo [1/4] Python не найден. Устанавливаю автоматически, это 1-3 минуты...
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
  if errorlevel 1 (
    echo.
    echo  Не получилось установить Python автоматически.
    echo  Сейчас откроется сайт python.org. Скачайте Python, при установке
    echo  ОБЯЗАТЕЛЬНО отметьте галочку "Add python.exe to PATH".
    echo  Затем снова запустите этот файл.
    start https://www.python.org/downloads/
    pause
    exit /b
  )
  echo.
  echo  Python установлен. Закройте это окно и запустите START-WINDOWS.bat ещё раз.
  pause
  exit /b
)
echo [1/4] Python найден.

rem ---------- 2. Библиотеки
if not exist ".venv\Scripts\python.exe" (
  echo [2/4] Первый запуск: готовлю окружение...
  %PY% -m venv .venv
)
echo [2/4] Проверяю и обновляю библиотеки...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -U yt-dlp
if errorlevel 1 (
  echo  Ошибка установки библиотек. Проверьте интернет и запустите снова.
  pause
  exit /b
)

rem ---------- 3. Публичная ссылка (Cloudflare)
if not exist data mkdir data
if not exist tools mkdir tools
taskkill /im cloudflared.exe /f >nul 2>nul
if not exist "tools\cloudflared.exe" (
  echo [3/4] Скачиваю программу для публичной ссылки ^(один раз^)...
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'tools\cloudflared.exe'"
)
set "LINK="
if exist "tools\cloudflared.exe" (
  echo [3/4] Создаю публичную ссылку...
  del /q data\tunnel.log >nul 2>nul
  start "Niche Finder - ссылка (не закрывать)" /min cmd /c "tools\cloudflared.exe tunnel --no-autoupdate --url http://127.0.0.1:8000 2> data\tunnel.log"
  for /f "usebackq delims=" %%U in (`powershell -NoProfile -Command "for($i=0;$i -lt 40;$i++){ if(Test-Path data\tunnel.log){ $m=Select-String -Path data\tunnel.log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -Last 1; if($m){ $m.Matches[0].Value; break } }; Start-Sleep 1 }"`) do set "LINK=%%U"
) else (
  echo [3/4] Не удалось скачать cloudflared - сервис будет доступен только на этом компьютере.
)

rem ---------- 4. Сервер
echo [4/4] Запускаю сервис...
echo.
echo  -----------------------------------------------------
if defined LINK (
  echo   Ссылка для коллег:  %LINK%
  echo   ^(уже скопирована в буфер обмена^)
  echo %LINK%| clip
) else (
  echo   Публичная ссылка не появилась. Сервис работает на этом компьютере.
)
echo   На этом компьютере:  http://localhost:8000
echo   Админ-панель - вкладка "Ключи" после входа с админ-ключом.
echo  -----------------------------------------------------
echo   Чтобы остановить сервис - закройте это окно.
echo.
start "" http://localhost:8000
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000 --log-level warning
taskkill /im cloudflared.exe /f >nul 2>nul
pause
