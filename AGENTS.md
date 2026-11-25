# Niche Finder

Niche Finder (ytniche.ru) анализирует YouTube-ниши, конкурентов, хиты, темы и формулы заголовков. Он разбирает обложки по тексту, лицам, цветам и стилю. «Сценарий на молекулы» сравнивает сценарий с хитами и провалами своего канала и с релевантными роликами других каналов. Отчёты помогают выбирать темы, названия и обложки для Never Built, The Concrete Why и The Wealth Line. Выгруженные отчёты пользователь хранит в `channels/<канал>/` в Video Studio или Map Studio.

## Устройство

- `app.py` — FastAPI, фоновые задачи, история отчётов и раздача интерфейса. `static/index.html`, `static/app.js` и `static/app.css` — браузерный интерфейс. `export.py` формирует отчёты для выгрузки.
- `analyzer.py` получает публичные данные через yt-dlp. `niche.py`, `insights.py` и `topics.py` определяют тематику, язык, конкурентов и закономерности в нише.
- `thumbs.py` анализирует обложки через OpenCV, модель YuNet (`models/face_yunet.onnx`) и Tesseract. `thumbtest.py` проверяет варианты обложек и заголовков.
- `molecules.py` разбирает сценарий и считает метрики. `video.py` читает субтитры. `asr.py` при необходимости распознаёт первые минуты публичного аудио локально через faster-whisper.
- `access.py` управляет ключами и SQLite. Рабочая база, отчёты и модели распознавания речи хранятся в `data/` (или в каталоге `DATA_DIR`). `bot.py` и `paysettings.py` обслуживают выдачу ключей и оплату.

## Запуск

- Windows — `START-WINDOWS.bat`, macOS — `START-MAC.command`. Локальный адрес `http://localhost:8000`. Эти скрипты также могут открыть публичную ссылку Cloudflare.
- Для разработки создайте окружение Python 3.12 командой `python -m venv .venv`, активируйте его, выполните `python -m pip install -r requirements.txt` и `python -m uvicorn app:app --host 127.0.0.1 --port 8000`. Вход в отчёты требует выданного ключа Niche Finder.
- Для распознавания текста установите Tesseract с пакетами `eng` и `rus`. На Windows добавьте его в `PATH` или установите в стандартную папку. Docker-образ и серверный установщик устанавливают Tesseract сами. Серверные инструкции находятся в `docs/INSTALL.md`, контейнеры описаны в `docker-compose.yml`.

## Правила проекта

- Общайтесь с владельцем по-русски, коротко и простыми словами. При передаче изменений отдавайте готовые файлы с указанием папки. Используйте отдельные файлы, если архив не нужен.
- Получайте данные YouTube бесплатно через yt-dlp, без vidIQ, платных API, входа в YouTube, браузерных cookies и файлов cookies. Для поиска и списка роликов используйте `--flat-playlist`, для публичных субтитров — клиент `web_embedded` и нужный язык. При сбое начните с `python -m pip install -U yt-dlp`. Превью доступны по адресу `https://i.ytimg.com/vi/<id>/hqdefault.jpg`.
- Проверяйте текст и лица на самой обложке. Сбой OCR означает, что текст не удалось проверить, и не служит признаком отсутствия надписи.
- Для сравнения сценария проверяйте и конкретную тему ролика, и нишу канала. Числа и одиночные совпадения в заголовках не подтверждают тематику. Используйте субтитры или речь на языке сценария. Если сравнимых данных мало, показывайте это в отчёте.
- Сохраняйте расчёт длины сценария по темпу речи. При темпе 148 слов/мин 296 слов дают около двух минут.

Команды ручной проверки из справки:

```text
yt-dlp --no-check-certificates --flat-playlist --print "%(title)s | %(channel)s | %(view_count)s | %(url)s" "ytsearch10:запрос"
yt-dlp --no-check-certificates --flat-playlist --print "%(title)s | %(view_count)s | %(duration)s" "https://www.youtube.com/@КАНАЛ/videos"
yt-dlp --extractor-args "youtube:player_client=web_embedded" --ignore-no-formats-error --skip-download --write-auto-subs --sub-langs en --sub-format vtt "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Проверка изменений

- Запустите `python -m unittest discover -s tests` и проверьте затронутый экран или выгрузку. Сохраняйте возможность открывать старые отчёты.
