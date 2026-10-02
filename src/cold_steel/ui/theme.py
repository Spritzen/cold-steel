"""Light and dark. "system" follows the desktop; "light" and "dark" force one.

Qt is asked for the colour scheme, but a desktop's platform theme (KDE's
among them) may ignore that, so a forced scheme also sets its own palette.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from cold_steel.store.settings import Theme

Role = QPalette.ColorRole

# (role, light, dark)
COLOURS: tuple[tuple[QPalette.ColorRole, str, str], ...] = (
    (Role.Window, "#eff0f1", "#2a2e32"),
    (Role.WindowText, "#232629", "#e8e9ea"),
    (Role.Base, "#ffffff", "#1d2023"),
    (Role.AlternateBase, "#f4f5f6", "#24282c"),
    (Role.ToolTipBase, "#f7f7f7", "#31363b"),
    (Role.ToolTipText, "#232629", "#e8e9ea"),
    (Role.PlaceholderText, "#7f8c8d", "#8a9196"),
    (Role.Text, "#232629", "#e8e9ea"),
    (Role.Button, "#fcfcfc", "#31363b"),
    (Role.ButtonText, "#232629", "#e8e9ea"),
    (Role.BrightText, "#ffffff", "#ffffff"),
    (Role.Light, "#ffffff", "#40464c"),
    (Role.Midlight, "#f4f5f6", "#363b40"),
    (Role.Mid, "#bcc0c4", "#1f2226"),
    (Role.Dark, "#888e93", "#151719"),
    (Role.Shadow, "#474a4c", "#0e1012"),
    (Role.Highlight, "#3daee9", "#3daee9"),
    (Role.HighlightedText, "#ffffff", "#ffffff"),
    (Role.Link, "#2980b9", "#5aaef0"),
    (Role.LinkVisited, "#7f3c9e", "#a77fcf"),
)
# Greyed text on disabled widgets.
DISABLED = (Role.WindowText, Role.Text, Role.ButtonText)
DISABLED_COLOUR = ("#a0a4a8", "#6e7479")


def palette(dark: bool) -> QPalette:
    result = QPalette()
    for role, light_colour, dark_colour in COLOURS:
        result.setColor(role, QColor(dark_colour if dark else light_colour))
    for role in DISABLED:
        result.setColor(QPalette.ColorGroup.Disabled, role, QColor(DISABLED_COLOUR[dark]))
    return result


def apply_theme(app: QApplication, theme: Theme) -> None:
    hints = app.styleHints()
    if theme == "system":
        hints.unsetColorScheme()
        app.setPalette(QPalette())  # nothing set: the desktop's palette again
        return
    dark = theme == "dark"
    hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
    app.setPalette(palette(dark))
