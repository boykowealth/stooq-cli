"""Off-white and off-black themes with a single blue accent.

Palette values are defined once here; the stylesheet only references theme
variables, never hard-coded colors.
"""

from __future__ import annotations

from textual.theme import Theme

# Shared accent and signal colors, tuned to read on both surfaces.
ACCENT_LIGHT = "#2563EB"
ACCENT_DARK = "#6EA8FF"
UP_LIGHT, DOWN_LIGHT = "#0E7C4A", "#C0392B"
UP_DARK, DOWN_DARK = "#4CC38A", "#F07868"

STOOQ_LIGHT = Theme(
    name="stooq-light",
    primary=ACCENT_LIGHT,
    secondary="#5B6472",
    accent=ACCENT_LIGHT,
    foreground="#20242C",
    background="#FAFAF7",
    surface="#FAFAF7",
    panel="#EDEDE8",
    success=UP_LIGHT,
    warning="#B7791F",
    error=DOWN_LIGHT,
    dark=False,
    variables={
        "footer-key-foreground": ACCENT_LIGHT,
        "up-color": UP_LIGHT,
        "down-color": DOWN_LIGHT,
    },
)

STOOQ_DARK = Theme(
    name="stooq-dark",
    primary=ACCENT_DARK,
    secondary="#9AA4B2",
    accent=ACCENT_DARK,
    foreground="#E6E6E2",
    background="#121417",
    surface="#121417",
    panel="#1E2126",
    success=UP_DARK,
    warning="#D9A441",
    error=DOWN_DARK,
    dark=True,
    variables={
        "footer-key-foreground": ACCENT_DARK,
        "up-color": UP_DARK,
        "down-color": DOWN_DARK,
    },
)


def chart_colors(dark: bool) -> dict:
    """Colors for plotext charts, matched to the active theme."""
    if dark:
        return {
            "bg": (18, 20, 23),
            "fg": (230, 230, 226),
            "grid": (60, 64, 70),
            "accent": (110, 168, 255),
            "series": [
                (110, 168, 255),
                (76, 195, 138),
                (217, 164, 65),
                (240, 120, 104),
                (186, 140, 255),
                (120, 200, 210),
            ],
        }
    return {
        "bg": (250, 250, 247),
        "fg": (32, 36, 44),
        "grid": (205, 205, 200),
        "accent": (37, 99, 235),
        "series": [
            (37, 99, 235),
            (14, 124, 74),
            (183, 121, 31),
            (192, 57, 43),
            (124, 58, 237),
            (13, 148, 166),
        ],
    }
