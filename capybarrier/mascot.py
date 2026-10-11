"""Kapi the capybara: Capybarrier's mascot, drawn as inline SVG so it works everywhere.

Moods:
  chill  - all good, eyes closed and content
  guard  - an attack was just blocked, shield up
  onsen  - idle, soaking in the hot spring with steam rising
"""

from __future__ import annotations

FUR = "#b98258"
FUR_DARK = "#9a6640"
FUR_LIGHT = "#dcae84"
INK = "#3b2619"
BLUSH = "#f7a6a1"


def _yuzu(cx: float, cy: float, r: float = 26) -> str:
    return f"""
  <g>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="url(#yuzuGrad)" stroke="#e9a400" stroke-width="2.5"/>
    <circle cx="{cx - r * 0.35}" cy="{cy - r * 0.35}" r="{r * 0.22}" fill="#fff6c8" opacity="0.85"/>
    <circle cx="{cx + r * 0.3}" cy="{cy + r * 0.15}" r="1.8" fill="#e9a400" opacity="0.6"/>
    <circle cx="{cx - r * 0.1}" cy="{cy + r * 0.45}" r="1.8" fill="#e9a400" opacity="0.6"/>
    <circle cx="{cx + r * 0.55}" cy="{cy - r * 0.25}" r="1.6" fill="#e9a400" opacity="0.6"/>
    <path d="M{cx} {cy - r + 2} q 2 -9 -1 -14" stroke="#6b8f3a" stroke-width="3.5" fill="none" stroke-linecap="round"/>
    <path d="M{cx + 1} {cy - r - 6} c 10 -14 30 -12 34 -4 c -10 10 -26 12 -34 4 z" fill="#7cc35f" stroke="#5a9e45" stroke-width="2"/>
  </g>"""


def _eyes(mood: str) -> str:
    if mood == "guard":
        return f"""
  <ellipse cx="158" cy="190" rx="10" ry="12" fill="{INK}"/>
  <ellipse cx="262" cy="190" rx="10" ry="12" fill="{INK}"/>
  <circle cx="161.5" cy="185.5" r="4" fill="#fff"/>
  <circle cx="265.5" cy="185.5" r="4" fill="#fff"/>
  <circle cx="155" cy="195" r="1.8" fill="#fff"/>
  <circle cx="259" cy="195" r="1.8" fill="#fff"/>
  <path d="M144 170 q 14 -8 28 -2" stroke="{INK}" stroke-width="4" fill="none" stroke-linecap="round"/>
  <path d="M248 168 q 14 -6 28 2" stroke="{INK}" stroke-width="4" fill="none" stroke-linecap="round"/>"""
    return f"""
  <path d="M145 192 q 13 12 26 0" stroke="{INK}" stroke-width="5" fill="none" stroke-linecap="round"/>
  <path d="M249 192 q 13 12 26 0" stroke="{INK}" stroke-width="5" fill="none" stroke-linecap="round"/>"""


def _shield() -> str:
    return """
  <g transform="translate(330 318) rotate(10) scale(0.9)">
    <path d="M0 -58 L50 -40 C50 10 30 40 0 58 C-30 40 -50 10 -50 -40 Z" fill="url(#shieldGrad)" stroke="#2f7d5b" stroke-width="5" stroke-linejoin="round"/>
    <path d="M-20 -2 l 14 14 l 28 -30" stroke="#ffffff" stroke-width="9" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
  </g>"""


def _steam() -> str:
    return """
  <g stroke="#ffffff" stroke-width="6" fill="none" stroke-linecap="round" opacity="0.9">
    <path d="M70 150 q -14 -18 0 -36 q 14 -18 0 -36"/>
    <path d="M350 160 q -14 -18 0 -36 q 14 -18 0 -36"/>
    <path d="M100 90 q -10 -14 0 -28 q 10 -14 0 -28"/>
  </g>"""


def capybara_svg(mood: str = "chill", size: int | None = 220, background: bool = False) -> str:
    size_attr = f' width="{size}" height="{size}"' if size else ""
    onsen = mood == "onsen"
    parts = [f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 420"{size_attr} role="img" aria-label="Kapi the capybara ({mood})">
  <defs>
    <radialGradient id="furGrad" cx="45%" cy="30%" r="80%">
      <stop offset="0%" stop-color="#cc966b"/>
      <stop offset="100%" stop-color="{FUR}"/>
    </radialGradient>
    <radialGradient id="yuzuGrad" cx="38%" cy="32%" r="75%">
      <stop offset="0%" stop-color="#ffe88a"/>
      <stop offset="100%" stop-color="#ffbf1f"/>
    </radialGradient>
    <linearGradient id="shieldGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#5fd39b"/>
      <stop offset="100%" stop-color="#3aa776"/>
    </linearGradient>
    <linearGradient id="waterGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#9ee0ee"/>
      <stop offset="100%" stop-color="#5bb8d4"/>
    </linearGradient>
    <clipPath id="tubClip"><rect x="24" y="250" width="372" height="160" rx="46"/></clipPath>
  </defs>"""]
    if background:
        parts.append('  <circle cx="210" cy="215" r="200" fill="#fff4e3"/>')
    if onsen:
        parts.append(_steam())
    else:
        parts.append('  <ellipse cx="210" cy="394" rx="140" ry="14" fill="#000" opacity="0.08"/>')
        parts.append(f'  <ellipse cx="150" cy="380" rx="34" ry="18" fill="{FUR_DARK}"/>')
        parts.append(f'  <ellipse cx="270" cy="380" rx="34" ry="18" fill="{FUR_DARK}"/>')
    # body: a soft, slightly boxy loaf
    parts.append('  <path d="M56 300 C52 196 104 126 210 126 C316 126 368 196 364 300 C361 364 302 390 210 390 C118 390 59 364 56 300 Z" fill="url(#furGrad)"/>')
    # small round capybara ears
    parts.append(f'  <ellipse cx="124" cy="144" rx="18" ry="15" fill="{FUR_DARK}" transform="rotate(-25 124 144)"/>')
    parts.append(f'  <ellipse cx="296" cy="144" rx="18" ry="15" fill="{FUR_DARK}" transform="rotate(25 296 144)"/>')
    parts.append('  <ellipse cx="126" cy="147" rx="8" ry="6" fill="#e7a98c" transform="rotate(-25 126 147)"/>')
    parts.append('  <ellipse cx="294" cy="147" rx="8" ry="6" fill="#e7a98c" transform="rotate(25 294 147)"/>')
    # the big blunt capybara snout
    parts.append(f'  <rect x="126" y="204" width="168" height="114" rx="57" fill="{FUR_LIGHT}"/>')
    parts.append(_eyes(mood))
    parts.append(f'  <rect x="170" y="220" width="80" height="30" rx="15" fill="{INK}"/>')
    parts.append('  <ellipse cx="190" cy="228" rx="8" ry="4" fill="#ffffff" opacity="0.35"/>')
    parts.append(f'  <path d="M210 250 v 16 M210 266 q -14 14 -26 2 M210 266 q 14 14 26 2" stroke="{INK}" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    parts.append(f'  <ellipse cx="112" cy="238" rx="22" ry="12" fill="{BLUSH}" opacity="0.8"/>')
    parts.append(f'  <ellipse cx="308" cy="238" rx="22" ry="12" fill="{BLUSH}" opacity="0.8"/>')
    parts.append(f'  <path d="M190 134 q 8 -16 16 0 q 8 -16 16 0" stroke="{FUR_DARK}" stroke-width="5" fill="none" stroke-linecap="round"/>')
    if not onsen:
        parts.append(f'  <ellipse cx="150" cy="338" rx="26" ry="20" fill="{FUR_DARK}"/>')
        parts.append(f'  <ellipse cx="270" cy="338" rx="26" ry="20" fill="{FUR_DARK}"/>')
    parts.append(_yuzu(212, 100))
    if mood == "guard":
        parts.append(_shield())
    if onsen:
        parts.append('  <g clip-path="url(#tubClip)"><path d="M0 318 q 25 -14 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 t 50 0 V420 H0 Z" fill="url(#waterGrad)" opacity="0.95"/></g>')
        parts.append('  <path d="M60 334 q 20 -10 40 0 M310 338 q 20 -10 40 0" stroke="#ffffff" stroke-width="5" fill="none" stroke-linecap="round" opacity="0.85"/>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_assets(directory: str = "assets") -> list[str]:
    import os

    os.makedirs(directory, exist_ok=True)
    paths = []
    for mood in ("chill", "guard", "onsen"):
        path = os.path.join(directory, f"kapi-{mood}.svg")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(capybara_svg(mood, size=None))
        paths.append(path)
    return paths


if __name__ == "__main__":
    print(write_assets())
