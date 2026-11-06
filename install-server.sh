#!/usr/bin/env bash
# Niche Finder — установка на сервер Linux (Ubuntu / Debian) одной командой:
#   curl -fsSL https://raw.githubusercontent.com/ravettikeney-sudo/youtube-niche-finder/main/install-server.sh | sudo bash
# С доменом и https (домен уже должен указывать на IP сервера):
#   curl -fsSL https://raw.githubusercontent.com/ravettikeney-sudo/youtube-niche-finder/main/install-server.sh | DOMAIN=niche.example.ru bash
# Повторный запуск = обновление до последней версии (ключи, отчёты и домен сохраняются).
set -e
REPO="https://github.com/ravettikeney-sudo/youtube-niche-finder.git"
RAW="https://raw.githubusercontent.com/ravettikeney-sudo/youtube-niche-finder/main/install-server.sh"
DIR="/opt/niche-finder"
PORT="${PORT:-80}"

[ "$(id -u)" = 0 ] || { echo "Запустите через sudo"; exit 1; }
echo "== Niche Finder: установка =="

IP=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')

# ---- домен: из параметра или сохранённый при прошлой установке
DOMAIN="${DOMAIN:-$(cat "$DIR/data/domain" 2>/dev/null || true)}"
DOMAIN=$(echo "$DOMAIN" | tr 'A-Z' 'a-z' | sed -E 's#^https?://##; s#/.*$##; s/^www\.//')
if [ -n "$DOMAIN" ]; then
  RESOLVED=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)
  if [ "$RESOLVED" != "$IP" ]; then
    echo
    echo "!! Домен $DOMAIN пока не указывает на этот сервер."
    echo "   Сейчас он ведёт на: ${RESOLVED:-никуда}, а нужно: $IP"
    echo "   Проверьте A-запись домена у регистратора. После изменения DNS обновляется"
    echo "   от 15 минут до нескольких часов — потом запустите команду ещё раз."
    if [ -f "$DIR/data/domain" ]; then
      echo "   Продолжаю обновление с прежними настройками."
    else
      echo "   Сервис продолжает работать по старому адресу http://$IP"
      exit 1
    fi
  fi
fi

echo "[1/6] Системные пакеты…"
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

echo "[2/6] Код…"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO" "$DIR"; fi
mkdir -p "$DIR/data"

echo "[3/6] Библиотеки…"
[ -x "$DIR/.venv/bin/python" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --disable-pip-version-check -r "$DIR/requirements.txt"

if [ -n "$DOMAIN" ]; then
  APP_HOST=127.0.0.1; APP_PORT=8000          # наружу смотрит Caddy (https), сервис — только внутри
  LINK="https://$DOMAIN"
else
  APP_HOST=0.0.0.0; APP_PORT=$PORT
  LINK="http://$IP$( [ "$PORT" = 80 ] || echo ":$PORT" )"
fi

echo "[4/6] Служба (запускается сама после перезагрузки)…"
cat > /etc/systemd/system/niche-finder.service <<EOF
[Unit]
Description=Niche Finder
After=network-online.target
[Service]
WorkingDirectory=$DIR
Environment=DATA_DIR=$DIR/data
Environment=PUBLIC_URL=$LINK
ExecStartPre=-$DIR/.venv/bin/pip install -q -U yt-dlp
ExecStart=$DIR/.venv/bin/uvicorn app:app --host $APP_HOST --port $APP_PORT --proxy-headers --forwarded-allow-ips 127.0.0.1 --log-level warning
Restart=always
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q niche-finder
systemctl restart niche-finder

echo "[5/6] Защищённое соединение (https)…"
if [ -n "$DOMAIN" ]; then
  if ! command -v caddy >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null 2>&1 || {
      DEBIAN_FRONTEND=noninteractive apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https gnupg >/dev/null
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
      curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
      apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq caddy >/dev/null
    }
  fi
  cat > /etc/caddy/Caddyfile <<EOF
# Niche Finder: сертификат выпускается и продлевается автоматически (Let's Encrypt)
$DOMAIN {
	encode gzip
	reverse_proxy 127.0.0.1:$APP_PORT
}
# старая ссылка по IP перенаправляет на домен
http://$IP {
	redir https://$DOMAIN{uri} permanent
}
EOF
  # www.домен → домен (если www тоже указывает на сервер)
  if [ "$(getent ahostsv4 "www.$DOMAIN" 2>/dev/null | awk '{print $1}' | head -1)" = "$IP" ]; then
    printf 'www.%s {\n\tredir https://%s{uri} permanent\n}\n' "$DOMAIN" "$DOMAIN" >> /etc/caddy/Caddyfile
  fi
  systemctl enable -q caddy
  systemctl restart caddy
  echo "$DOMAIN" > "$DIR/data/domain"
  # временная ссылка Cloudflare больше не нужна
  systemctl disable -q --now niche-tunnel 2>/dev/null || true
else
  echo "    домен не указан — пропускаю (сервис доступен по http://$IP)"
fi

echo "[6/6] Команда управления…"
cat > /usr/local/bin/niche-finder <<EOF
#!/bin/sh
case "\$1" in
  url)     echo "$LINK" ;;
  restart) systemctl restart niche-finder; command -v caddy >/dev/null && systemctl restart caddy; sleep 5; echo "$LINK" ;;
  update)  curl -fsSL $RAW | bash ;;
  https)   [ -n "\$2" ] || { echo "Укажите домен: niche-finder https niche.example.ru"; exit 1; }
           curl -fsSL $RAW | DOMAIN="\$2" bash ;;
  logs)    journalctl -u niche-finder -n 80 --no-pager; command -v caddy >/dev/null && journalctl -u caddy -n 20 --no-pager ;;
  stop)    systemctl stop niche-finder ;;
  *)       echo "niche-finder url | update | https <домен> | restart | logs | stop" ;;
esac
EOF
chmod +x /usr/local/bin/niche-finder

# без домена оставляем запасную ссылку Cloudflare, как раньше
TUNNEL=""
if [ -z "$DOMAIN" ]; then
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
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:$APP_PORT
StandardError=append:$DIR/data/tunnel.log
Restart=always
[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable -q --now niche-tunnel
  systemctl restart niche-tunnel
  for _ in $(seq 1 30); do TUNNEL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" $DIR/data/tunnel.log 2>/dev/null | tail -1); [ -n "$TUNNEL" ] && break; sleep 1; done
fi

OK=""
for _ in $(seq 1 40); do
  if [ -n "$DOMAIN" ]; then
    curl -fsS --max-time 5 "https://$DOMAIN/api/health" >/dev/null 2>&1 && { OK=1; break; }
  else
    curl -fsS --max-time 5 "http://127.0.0.1:$APP_PORT/api/health" >/dev/null 2>&1 && { OK=1; break; }
  fi
  sleep 2
done

echo
echo "====================================================="
echo " Готово!"
echo " Ссылка для коллег:  $LINK"
[ -n "$TUNNEL" ] && echo " Запасная ссылка:    $TUNNEL"
if [ -n "$OK" ]; then
  echo " Сервис работает ✓"
elif [ -n "$DOMAIN" ]; then
  echo " Сертификат ещё выпускается — откройте ссылку через 2–3 минуты."
  echo " Если не заработает: niche-finder logs"
else
  echo " Сервис ещё запускается — проверьте ссылку через минуту"
fi
echo
echo " Показать ссылку ещё раз:  niche-finder url"
echo " Обновить сервис:          niche-finder update"
[ -z "$DOMAIN" ] && echo " Подключить домен:         niche-finder https ваш-домен.ru"
echo "====================================================="
