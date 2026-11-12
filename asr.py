"""Распознавание речи для роликов без субтитров (faster-whisper на процессоре).

Скачиваем только звук самого лёгкого качества, распознаём первые WINDOW секунд.
Одновременно идёт только одно распознавание — чтобы не съесть память сервера."""
import os
import tempfile
import threading
import time

import yt_dlp

import analyzer as A

WINDOW = 240          # сколько секунд ролика распознаём
MAX_DUR = 3 * 3600    # слишком длинные эфиры не качаем
_lock = threading.Lock()
_model = None
_model_name = None


def _mem_gb():
    try:
        with open("/proc/meminfo") as f:
            for ln in f:
                if ln.startswith("MemTotal"):
                    return int(ln.split()[1]) / 1024 / 1024
    except Exception:
        pass
    return 2.0


def model_name():
    return os.environ.get("ASR_MODEL") or ("small" if _mem_gb() >= 3.5 else "base")


def available():
    try:
        import faster_whisper  # noqa: F401
        import av  # noqa: F401
        return True
    except Exception:
        return False


def _get_model():
    global _model, _model_name
    if _model is None:
        from faster_whisper import WhisperModel
        root = os.path.join(os.environ.get("DATA_DIR") or "data", "asr-models")
        os.makedirs(root, exist_ok=True)
        _model_name = model_name()
        _model = WhisperModel(_model_name, device="cpu", compute_type="int8",
                              cpu_threads=max(1, (os.cpu_count() or 2)), download_root=root)
    return _model


def _download_audio(vid, folder):
    o = A._opts(flat=False)
    o.pop("skip_download", None)
    o.update({"format": "worstaudio[acodec=opus]/worstaudio/bestaudio", "outtmpl": os.path.join(folder, "a.%(ext)s"),
              "noplaylist": True, "max_filesize": 120 * 1024 * 1024})
    o["extractor_args"] = {k: v for k, v in (o.get("extractor_args") or {}).items() if k != "youtube"} or {}
    with yt_dlp.YoutubeDL(o) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=True)
    if not info:
        return None, None
    files = [os.path.join(folder, f) for f in os.listdir(folder) if f.startswith("a.") and not f.endswith(".part")]
    return (files[0] if files else None), info


def _decode(path, seconds):
    """Первые seconds секунд звука → 16 кГц моно float32 (без системного ffmpeg — через PyAV)."""
    import av
    import numpy as np
    out, need = [], int(16000 * seconds)
    got = 0
    with av.open(path) as c:
        st = next(s for s in c.streams if s.type == "audio")
        rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for fr in c.decode(st):
            for r in rs.resample(fr) or []:
                a = r.to_ndarray().reshape(-1)
                out.append(a)
                got += a.size
            if got >= need:
                break
    if not out:
        return None
    return (np.concatenate(out)[:need].astype("float32") / 32768.0)


def transcribe(vid, lang=None, duration=None):
    """→ {"items": [(сек, текст)], "window": сек распознано, "duration": длина ролика, "title", "secs": время работы}
    или None (не скачалось / нет речи)."""
    if not available():
        return None
    if duration and duration > MAX_DUR:
        return None
    t0 = time.time()
    with tempfile.TemporaryDirectory(prefix="nf-asr-") as tmp:
        try:
            path, info = _download_audio(vid, tmp)
        except Exception:
            return None
        if not path:
            return None
        audio = _decode(path, WINDOW)
        if audio is None or audio.size < 16000 * 5:
            return None
        window = audio.size / 16000
        with _lock:
            m = _get_model()
            segs, _ = m.transcribe(audio, language=(lang or None), beam_size=1, vad_filter=True,
                                   condition_on_previous_text=False)
            items = [(round(s.start, 1), s.text.strip()) for s in segs if s.text.strip()]
    words = sum(len(t.split()) for _, t in items)
    if words < 25:          # речи почти нет: музыка, надписи, игра без комментариев
        return None
    return {"items": items, "window": window, "duration": (info or {}).get("duration") or duration,
            "title": (info or {}).get("title"), "secs": round(time.time() - t0, 1), "model": _model_name}
