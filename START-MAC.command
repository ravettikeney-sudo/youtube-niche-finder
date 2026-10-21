#!/usr/bin/env bash
# Niche Finder — запуск на Mac. Двойной клик по файлу.
cd "$(dirname "$0")" || exit 1
clear
echo "====================================================="
echo "   Niche Finder — запуск"
echo "   Не закрывайте это окно, пока пользуетесь сервисом"
echo "====================================================="
echo

# 1. Python
if ! python3 -c "import sys; assert sys.version_info>=(3,10)" 2>/dev/null; then
  echo "[1/4] Нужен Python 3.10 или новее."
  echo "      Сейчас откроется python.org — скачайте и установите Python для macOS,"
  echo "      затем снова запустите этот файл."
  open "https://www.python.org/downloads/macos/"
  read -r -p "Нажмите Enter, чтобы закрыть…"
  exit 1
fi
echo "[1/4] Python найден."

# 2. Библиотеки
[ -x .venv/bin/python ] || { echo "[2/4] Первый запуск: готовлю окружение…"; python3 -m venv .venv; }
echo "[2/4] Проверяю и обновляю библиотеки…"
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { echo "Ошибка установки. Проверьте интернет."; read -r; exit 1; }
.venv/bin/python -m pip install -q --disable-pip-version-check -U yt-dlp

# 3. Публичная ссылка
mkdir -p data tools
pkill -f "tools/cloudflared tunnel" 2>/dev/null
if [ ! -x tools/cloudflared ]; then
  echo "[3/4] Скачиваю программу для публичной ссылки (один раз)…"
  ARCH=$( [ "$(uname -m)" = "arm64" ] && echo arm64 || echo amd64 )
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-$ARCH.tgz" -o tools/cf.tgz \
    && tar -xzf tools/cf.tgz -C tools && rm -f tools/cf.tgz && chmod +x tools/cloudflared
fi
LINK=""
if [ -x tools/cloudflared ]; then
  echo "[3/4] Создаю публичную ссылку…"
  rm -f data/tunnel.log
  tools/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 2> data/tunnel.log &
  CF=$!
  for _ in $(seq 1 40); do
    LINK=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" data/tunnel.log 2>/dev/null | tail -1)
    [ -n "$LINK" ] && break; sleep 1
  done
fi

# 4. Сервер
echo "[4/4] Запускаю сервис…"
echo
echo "-----------------------------------------------------"
if [ -n "$LINK" ]; then
  echo "  Ссылка для коллег:  $LINK"
  echo "  (уже скопирована в буфер обмена)"
  printf "%s" "$LINK" | pbcopy
else
  echo "  Публичная ссылка не появилась. Сервис работает на этом компьютере."
fi
echo "  На этом компьютере:  http://localhost:8000"
echo "  Чтобы остановить — закройте это окно."
echo "-----------------------------------------------------"
( sleep 2; open "http://localhost:8000" ) &
trap '[ -n "$CF" ] && kill $CF 2>/dev/null' EXIT
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000 --log-level warning
