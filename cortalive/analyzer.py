"""Acha os momentos de tensão da live e decide onde cada corte começa e termina.

Como funciona, em ordem:
1. Mede o volume de cada segundo do áudio (barato, mesmo em lives de várias horas).
2. Calcula a tensão: quanto o volume sobe em relação ao normal daquele trecho (grito, explosão,
   risada alta) e quão alto está em termos absolutos.
3. Pega os picos mais fortes, com distância mínima entre eles.
4. Transcreve só a janela em volta de cada pico, não a live inteira. É o que deixa o app rápido.
5. Reordena pelos picos que também têm fala acelerada, ajusta o começo e o fim do corte para
   caber em frases inteiras e monta título e etiquetas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from . import media

S_AUDIO = "Lendo o áudio"
S_TENSION = "Medindo a tensão"
S_ASR = "Transcrevendo os momentos fortes"
S_PICK = "Escolhendo os cortes"
S_PREVIEW = "Gerando prévias"
STAGES = [S_AUDIO, S_TENSION, S_ASR, S_PICK, S_PREVIEW]

Report = Callable[[str, float, str], None]


@dataclass
class Options:
    length: tuple = (30, 60)
    count: int = 8
    language: str = "pt"
    model: str = "small"


# ---------- sinal ----------

def _movavg(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(x) == 0:
        return x.astype(float).copy()
    pad = w // 2
    xp = np.pad(x.astype(float), (pad, w - 1 - pad), mode="edge")
    return np.convolve(xp, np.ones(w) / w, mode="valid")


def _rolling_median(x: np.ndarray, w: int) -> np.ndarray:
    w |= 1
    pad = w // 2
    xp = np.pad(x, pad, mode="edge")
    out = np.empty(len(x))
    step = 20000  # em blocos para não gastar memória em lives muito longas
    for a in range(0, len(x), step):
        b = min(len(x), a + step)
        out[a:b] = np.median(sliding_window_view(xp[a:b + w - 1], w), axis=1)
    return out


def _norm(x: np.ndarray, lo_p: float, hi_p: float) -> np.ndarray:
    if len(x) == 0:
        return x
    lo, hi = np.percentile(x, [lo_p, hi_p])
    if hi - lo < 1e-6:
        return np.zeros_like(x, dtype=float)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def tension(db: np.ndarray) -> dict:
    db = np.maximum(np.asarray(db, dtype=float), -80.0)
    s3 = _movavg(db, 3)
    base = _rolling_median(s3, 91)          # "volume normal" dos 90 s em volta
    burst_n = _norm(s3 - base, 50, 99.5)    # quanto subiu acima do normal
    loud_n = _norm(s3, 10, 99.5)            # quão alto está no geral
    v = _movavg(0.65 * burst_n + 0.35 * loud_n, 5)
    v = np.where(s3 < -62, 0.0, v)          # trecho mudo não conta
    return {"v": v, "s3": s3, "base": base, "burst_n": burst_n, "loud_n": loud_n}


def pick_peaks(v: np.ndarray, n: int, gap: int, floor: float = 0.2) -> list:
    w = v.astype(float).copy()
    out: list = []
    for _ in range(n):
        if not len(w):
            break
        i = int(np.argmax(w))
        if w[i] < floor:
            break
        out.append(i)
        w[max(0, i - gap): i + gap + 1] = -1
    if not out and len(v):
        out = [int(np.argmax(v))]
    return out


def curve(v: np.ndarray, n: int = 240) -> list:
    if len(v) == 0:
        return [0.0] * n
    edges = np.linspace(0, len(v), n + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        a = min(a, len(v) - 1)
        out.append(float(v[a:max(b, a + 1)].max()))
    m = max(out) or 1.0
    return [round(x / m, 4) for x in out]


# ---------- texto e limites do corte ----------

def _title(words: list, peak: float, n: int) -> str:
    near = [w for w in words if peak - 6 <= w["t0"] <= peak + 10]
    if not near:
        return f"Momento de tensão {n}"
    picked = []
    for w in near[:8]:
        picked.append(w["w"])
        if len(picked) >= 3 and w.get("end"):  # para na primeira frase completa
            break
    text = re.sub(r"^[\W_]+|[\W_]+$", "", " ".join(picked))
    return (text[:1].upper() + text[1:]) if text else f"Momento de tensão {n}"


def _bounds(peak: float, words: list, lo: float, hi: float, total: float) -> tuple:
    dur = min((lo + hi) / 2, total)
    start = max(0.0, min(peak - 0.6 * dur, total - dur))
    end = start + dur
    if words:
        s = min(words, key=lambda w: abs(w["t0"] - start))
        e = min(words, key=lambda w: abs(w["t1"] - end))
        s2 = max(0.0, s["t0"] - 0.2) if abs(s["t0"] - start) <= 3 else start
        e2 = min(total, e["t1"] + 0.3) if abs(e["t1"] - end) <= 4 else end
        if lo * 0.9 <= e2 - s2 <= hi * 1.1 and s2 <= peak <= e2 - 1:
            start, end = s2, e2
    return start, end


def _tags(T: dict, p: int, rate_n: Optional[float]) -> list:
    found = []
    pre = T["s3"][max(0, p - 8): max(0, p - 2)]
    if len(pre) and pre.mean() < T["base"][p] - 4 and T["burst_n"][p] > 0.5:
        found.append((T["burst_n"][p] + 0.05, "Silêncio e explosão"))
    found.append((T["burst_n"][p], "Pico de voz"))
    found.append((T["loud_n"][p], "Áudio alto"))
    if rate_n is not None:
        found.append((rate_n, "Fala acelerada"))
    found = [f for f in sorted(found, reverse=True) if f[0] > 0.55][:2]
    return [t for _, t in found] or ["Momento forte"]


# ---------- pipeline principal ----------

def analyze(src, workdir, opts: Options, transcriber, report: Report) -> dict:
    workdir = Path(workdir)
    info = media.probe(src)
    D = info["duration"]
    if not info["has_audio"]:
        raise media.MediaError("Esse vídeo não tem áudio, então não dá para medir a tensão.")
    lo, hi = opts.length

    wav = workdir / "audio.wav"
    report(S_AUDIO, 0, "")
    media.extract_audio(src, wav, D, lambda f: report(S_AUDIO, f, ""))

    report(S_TENSION, 0, "")
    db = media.rms_db_per_second(wav, lambda f: report(S_TENSION, f, ""))
    T = tension(db)
    v = T["v"]

    n = max(1, opts.count)
    n_cand = max(n * 2, n + 3)
    peaks = pick_peaks(v, n_cand, gap=int(hi), floor=0.2)

    warnings: list = []
    if transcriber is None:
        warnings.append("O Whisper não está instalado, então os cortes saem sem legenda. "
                        "Instale com: pip install faster-whisper")

    cands = []
    for k, p in enumerate(peaks):
        report(S_ASR, k / max(1, len(peaks)), f"Momento {k + 1} de {len(peaks)}")
        pt = p + 0.5
        words: list = []
        if transcriber is not None:
            w0, w1 = max(0.0, pt - 0.6 * hi - 4), min(D, pt + 0.4 * hi + 6)
            try:
                audio = media.read_window(wav, w0, w1)
                for w in transcriber(audio, opts.language):
                    words.append({**w, "t0": w["t0"] + w0, "t1": w["t1"] + w0})
            except Exception as e:  # uma janela com problema não derruba a análise
                warnings.append(f"Falha ao transcrever o momento em {int(pt)} s: {e}")
        near = sum(1 for w in words if pt - 10 <= w["t0"] <= pt + 10)
        cands.append({"p": p, "t": pt, "words": words, "rate": near / 20.0})
    report(S_ASR, 1, "")

    report(S_PICK, 0, "")
    use_rate = transcriber is not None and any(c["words"] for c in cands)
    if use_rate:
        rates = np.array([c["rate"] for c in cands])
        span = rates.max() - rates.min()
        for c, r in zip(cands, rates):
            c["rate_n"] = float((r - rates.min()) / span) if span > 1e-9 else 0.5
    for c in cands:
        c["final"] = 0.75 * float(v[c["p"]]) + 0.25 * c["rate_n"] if use_rate else float(v[c["p"]])
    chosen = sorted(sorted(cands, key=lambda c: -c["final"])[:n], key=lambda c: c["t"])

    clips = []
    for i, c in enumerate(chosen, 1):
        start, end = _bounds(c["t"], c["words"], lo, hi, D)
        dur = end - start
        words = [
            {"w": w["w"], "t0": round(w["t0"] - start, 3), "t1": round(min(dur, w["t1"] - start), 3), "end": bool(w["end"])}
            for w in c["words"] if start <= w["t0"] < end
        ]
        clips.append({
            "id": i,
            "title": _title(c["words"], c["t"], i),
            "peakT": round(c["t"], 2),
            "start": round(start, 2),
            "dur": round(dur, 2),
            "score": min(99, int(round(50 + 50 * c["final"]))),
            "tags": _tags(T, c["p"], c.get("rate_n") if use_rate else None),
            "hue": int(start * 37) % 360,
            "words": words,
        })
    report(S_PICK, 1, "")

    return {
        "duration": D,
        "width": info["width"],
        "height": info["height"],
        "curve": curve(v),
        "clips": clips,
        "warnings": warnings,
    }
