"""Gera legendas ASS palavra por palavra, em 4 estilos (os mesmos da prévia no app).

A legenda mostra grupos de 3 palavras (4 no Minimal) e destaca a palavra que está sendo falada.
Cada palavra vira um evento próprio, trocando a cor/tamanho da palavra ativa.
Fontes: coloque Anton.ttf e Inter-*.ttf em cortalive/fonts/ para ficar igual à prévia.
Sem elas, o ffmpeg usa uma fonte parecida do sistema.
"""
from __future__ import annotations

PLAY_W, PLAY_H = 1080, 1920


def _c(hexrgb: str, alpha: int = 0) -> str:
    h = hexrgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}&".upper()


# font, size, bold, spacing, outline, shadow, uppercase, chunk, cor do texto, cor do contorno
STYLES = {
    "impacto": dict(font="Anton", size=128, bold=0, spacing=1, outline=9, shadow=3, upper=True, chunk=3,
                    text="#FFFFFF", edge="#000000"),
    "karaoke": dict(font="Inter", size=92, bold=1, spacing=0, outline=0, shadow=4, upper=False, chunk=3,
                    text="#FFFFFF", edge="#000000"),
    "neon": dict(font="Inter", size=80, bold=1, spacing=6, outline=6, shadow=0, upper=True, chunk=3,
                 text="#FFFFFF", edge="#19D3FF"),
    "minimal": dict(font="Inter", size=70, bold=1, spacing=0, outline=2, shadow=2, upper=False, chunk=4,
                    text="#FFFFFF", edge="#000000"),
}

# alinhamento ASS e margem vertical (fora da área onde TikTok/Reels/Shorts cobrem a tela)
POSITIONS = {"top": (8, 300), "mid": (5, 0), "bot": (2, 340)}


def _t(sec: float) -> str:
    cs = max(0, int(round(sec * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("{", "(").replace("}", ")").replace("\\", "").replace("\n", " ")
    return text.upper() if upper else text


def chunks(words: list, n: int) -> list:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(i)
        if len(cur) >= n or w.get("end"):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _active(style: str, text: str) -> str:
    if style == "impacto":
        return "{\\1c" + _c("#FFD60A") + "\\fscx114\\fscy114}" + text + "{\\r}"
    if style == "karaoke":
        return "{\\bord18\\3c" + _c("#E5412F") + "\\1c" + _c("#FFFFFF") + "\\shad0}" + text + "{\\r}"
    if style == "neon":
        return "{\\1c" + _c("#C8FFF6") + "\\3c" + _c("#19FFD1") + "\\bord8\\blur7}" + text + "{\\r}"
    return "{\\u1}" + text + "{\\r}"  # minimal


def _idle(style: str, text: str) -> str:
    if style == "neon":
        return "{\\1a&H66&\\3a&H99&\\blur7}" + text + "{\\r}"
    if style == "minimal":
        return "{\\1a&H5A&}" + text + "{\\r}"
    if style == "karaoke":
        return "{\\bord0}" + text + "{\\r}"
    return text


def build_ass(words: list, style: str = "impacto", pos: str = "bot") -> str:
    st = STYLES.get(style, STYLES["impacto"])
    style = style if style in STYLES else "impacto"
    align, margin_v = POSITIONS.get(pos, POSITIONS["bot"])
    blur_default = "{\\blur7}" if style == "neon" else ""
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {PLAY_W}\nPlayResY: {PLAY_H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{st['font']},{st['size']},{_c(st['text'])},&H000000FF&,{_c(st['edge'])},&H99000000&,"
        f"{st['bold']},0,0,0,100,100,{st['spacing']},0,1,{st['outline']},{st['shadow']},{align},70,70,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    groups = chunks(words, st["chunk"])
    lines = []
    for gi, idxs in enumerate(groups):
        next_start = words[groups[gi + 1][0]]["t0"] if gi + 1 < len(groups) else None
        for k, wi in enumerate(idxs):
            w = words[wi]
            start = w["t0"]
            if k + 1 < len(idxs):
                end = words[idxs[k + 1]]["t0"]
            else:
                end = w["t1"] + 0.35
                if next_start is not None:
                    end = min(end, next_start)
            if end <= start:
                end = start + 0.12
            parts = []
            for wj in idxs:
                txt = _clean(words[wj]["w"], st["upper"])
                parts.append(_active(style, txt) if wj == wi else _idle(style, txt))
            lines.append(f"Dialogue: 0,{_t(start)},{_t(end)},Default,,0,0,0,,{blur_default}{' '.join(parts)}")
    return header + "\n".join(lines) + "\n"
