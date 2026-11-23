"""Transcrição com palavra e tempo, usando faster-whisper (roda local, sem enviar nada para a nuvem)."""
from __future__ import annotations

import importlib.util
import threading
from typing import Callable, Optional

import numpy as np

# Um transcritor recebe áudio float32 mono 16 kHz e devolve palavras com tempo relativo ao início do áudio.
Transcriber = Callable[[np.ndarray, str], list]

_models: dict = {}
_lock = threading.Lock()


def whisper_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def get_transcriber(model_size: str = "small") -> Optional[Transcriber]:
    if not whisper_available():
        return None

    def run(audio: np.ndarray, language: str) -> list:
        from faster_whisper import WhisperModel

        with _lock:
            model = _models.get(model_size)
            if model is None:
                model = _models[model_size] = WhisperModel(model_size, device="auto", compute_type="auto")
        segments, _info = model.transcribe(
            audio,
            language=None if language in ("", "auto") else language,
            word_timestamps=True,
            vad_filter=True,
            beam_size=1,
        )
        words = []
        for seg in segments:
            ws = list(seg.words or [])
            for j, w in enumerate(ws):
                text = (w.word or "").strip()
                if not text:
                    continue
                words.append({
                    "w": text,
                    "t0": float(w.start),
                    "t1": float(w.end),
                    "end": j == len(ws) - 1 or text[-1] in ".!?…",
                })
        return words

    return run
