#!/usr/bin/env bash
# Niche Finder — установка на сервер Linux (Ubuntu / Debian) одной командой:
#   curl -fsSL https://raw.githubusercontent.com/ravettikeney-sudo/youtube-niche-finder/main/install-server.sh | sudo bash
# Повторный запуск = обновление до последней версии (ключи и отчёты сохраняются).
set -e
REPO="https://github.com/ravettikeney-sudo/youtube-niche-finder.git"
DIR="/opt/niche-finder"
PORT="${PORT:-80}"

[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }
echo "== Niche Finder: установка =="

echo "[1/5] Системные пакеты…"
if command -v apt-get >/dev/null; then
  apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl python3 python3-venv >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q git curl python3 >/dev/null
fi

# подкачка для серверов с 1 ГБ памяти, чтобы анализ не падал при нагрузке
if [ "$(swapon --show | wc -l)" -eq 0 ] && [ "$(awk '/MemTotal/{print $2}' /proc/meminfo)" -lt 2500000 ]; then
  fallocate -l 1G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile && echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

echo "[2/5] Код…"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
mkdir -p "$DIR/data"

echo "[3/5] Библиотеки…"
[ -x "$DIR/.venv/bin/python" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"

IP=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')
LINK_DIRECT="http://$IP$( [ "$PORT" = 80 ] || echo ":$PORT" )"

echo "[4/5] Служба (запускается сама после перезагрузки)…"
cat > /etc/systemd/system/niche-finder.service <<EOF
[Unit]
Description=Niche Finder
After=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=DATA_DIR=$DIR/data
Environment=PUBLIC_URL=$LINK_DIRECT
ExecStartPre=-$DIR/.venv/bin/pip install -q -U yt-dlp
ExecStart=$DIR/.venv/bin/uvicorn app:app --host 0.0.0.0 --port $PORT --log-level warning
Restart=always
[Install]
WantedBy=multi-user.target
EOF

echo "[5/5] Публичная ссылка (Cloudflare)…"
ARCH=$(dpkg --print-architecture 2>/dev/null || uname -m); case "$ARCH" in x86_64) ARCH=amd64;; aarch64) ARCH=arm64;; esac
if ! command -v cloudflared >/dev/null; then
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH" -o /usr/local/bin/cloudflared
  chmod +x /usr/local/bin/cloudflared
fi
cat > /etc/systemd/system/niche-tunnel.service <<EOF
[Unit]
Description=Niche Finder public link
After=niche-finder.service
[Service]
ExecStartPre=/bin/sh -c ': > $DIR/data/tunnel.log'
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$PORT
StandardError=append:$DIR/data/tunnel.log
Restart=always
[Install]
WantedBy=multi-user.target
EOF

# удобная команда управления
cat > /usr/local/bin/niche-finder <<EOF
#!/bin/sh
case "\$1" in
  url)     echo "$LINK_DIRECT"; echo "запасная: \$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" $DIR/data/tunnel.log | tail -1)" ;;
  restart) systemctl restart niche-finder niche-tunnel; sleep 8; \$0 url ;;
  update)  curl -fsSL https://raw.githubusercontent.com/ravettikeney-sudo/youtube-niche-finder/main/install-server.sh | bash ;;
  logs)    journalctl -u niche-finder -n 80 --no-pager ;;
  stop)    systemctl stop niche-finder niche-tunnel ;;
  *)       echo "niche-finder url | restart | update | logs | stop" ;;
esac
EOF
chmod +x /usr/local/bin/niche-finder

systemctl daemon-reload
systemctl enable -q --now niche-finder niche-tunnel
systemctl restart niche-finder niche-tunnel

LINK=""
for _ in $(seq 1 30); do LINK=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" $DIR/data/tunnel.log 2>/dev/null | tail -1); [ -n "$LINK" ] && break; sleep 1; done
sleep 2
echo
echo "====================================================="
echo " Готово!"
echo " Ссылка для коллег:  $LINK_DIRECT"
[ -n "$LINK" ] && echo " Запасная ссылка:    $LINK"
curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/health" >/dev/null && echo " Сервис работает ✓" || echo " Сервис ещё запускается — проверьте ссылку через минуту"
echo
echo " Показать ссылку ещё раз:  niche-finder url"
echo " Обновить сервис:          niche-finder update"
echo "====================================================="
