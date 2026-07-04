"""Colour palettes. The graph is the brand — one accent colour, muted chrome.

Every theme defines the same seven roles, so adding one is just a new dict.
Consumers can also override individual colours per-request with the `accent`
and `bg` query params (see web/public.py) — pick the nearest theme, then nudge
it to match the host site.
"""

THEMES = {
    # --- neutrals ---
    "light": {
        "bg": "#ffffff",
        "border": "#e6e8eb",
        "text": "#1a1f27",
        "muted": "#9aa3ad",
        "grid": "#f0f2f4",
        "baseline": "#e2e5e8",
        "accent": "#5b6ee8",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#22282f",
        "text": "#e8edf2",
        "muted": "#707a85",
        "grid": "#1a2027",
        "baseline": "#252c34",
        "accent": "#7c8cf8",
    },
    "mono": {
        "bg": "#ffffff",
        "border": "#e5e5e5",
        "text": "#111111",
        "muted": "#a3a3a3",
        "grid": "#f5f5f5",
        "baseline": "#e5e5e5",
        "accent": "#111111",
    },
    "mono-dark": {
        "bg": "#0a0a0a",
        "border": "#262626",
        "text": "#f5f5f5",
        "muted": "#737373",
        "grid": "#171717",
        "baseline": "#262626",
        "accent": "#fafafa",
    },
    # --- cool / vivid ---
    "midnight": {
        "bg": "#0a0e1a",
        "border": "#1b2233",
        "text": "#dbe4f5",
        "muted": "#64718c",
        "grid": "#131a2b",
        "baseline": "#1e2740",
        "accent": "#38bdf8",
    },
    "terminal": {
        "bg": "#0a0f0a",
        "border": "#1d2b1d",
        "text": "#d0f0d0",
        "muted": "#5c7a5c",
        "grid": "#121f12",
        "baseline": "#1c331c",
        "accent": "#4ade80",
    },
    "sunset": {
        "bg": "#1e1122",
        "border": "#372140",
        "text": "#f8ecf6",
        "muted": "#8f7396",
        "grid": "#2a1830",
        "baseline": "#372140",
        "accent": "#fb7185",
    },
    # --- warm / paper ---
    "paper": {
        "bg": "#faf6ee",
        "border": "#e8e0cf",
        "text": "#2c2a24",
        "muted": "#a09880",
        "grid": "#f1ead9",
        "baseline": "#e8e0cf",
        "accent": "#c2410c",
    },
    # --- earthy / organic portfolio palettes ---
    "oat": {
        "bg": "#f2ecde",
        "border": "#e0d8c4",
        "text": "#202e27",
        "muted": "#5e6a5e",
        "grid": "#eae3d2",
        "baseline": "#e0d8c4",
        "accent": "#3a643a",
    },
    "pine": {
        "bg": "#222d27",
        "border": "#32413a",
        "text": "#e7e8dd",
        "muted": "#9aa79a",
        "grid": "#2a362f",
        "baseline": "#32413a",
        "accent": "#7cc47c",
    },
    "sage": {
        "bg": "#e1e4d8",
        "border": "#c9cfbe",
        "text": "#25302a",
        "muted": "#5b6457",
        "grid": "#d7dbcb",
        "baseline": "#c9cfbe",
        "accent": "#35624a",
    },
}

DEFAULT_THEME = "light"
