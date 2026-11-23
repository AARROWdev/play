"""Shared look for the AARROWdev Windows apps (Bigfoot, Cratedig, PLAY).

The same file ships in each app's repo. Change it in one, copy it to the others.
A theme is a dict of colours: accent, light, bg, base, alt, mid, bar, line, sel.
"""
TEXT = "#e6e8ec"
MUTED = "#b9bcc4"
PLACEHOLDER = "#7d8088"

BLUE = dict(accent="#2f9bff", light="#7cc4ff", lighter="#d6efff", bg="#111419", base="#13161c",
            alt="#181c23", mid="#1f242c", bar="#171b22", line="#2a3039", sel="#24405e")


def stylesheet(t):
    a, line = t["accent"], t["line"]
    return f"""
        QWidget#bar {{ background:{t['bar']}; border-top:1px solid {line}; }}
        QTreeView, QTableView, QTableWidget, QListView {{ border:1px solid {line}; }}
        QHeaderView::section {{ background:{t['alt']}; color:{MUTED}; border:0; border-right:1px solid {line};
                                padding:4px 6px; }}
        QLineEdit, QComboBox {{ background:{t['base']}; border:1px solid {line}; border-radius:4px; padding:4px 6px; }}
        QLineEdit:focus, QComboBox:focus {{ border-color:{a}; }}
        QPushButton#primary {{ background:{a}; color:#04121f; font-weight:700; border:0; border-radius:4px;
                               padding:5px 18px; }}
        QPushButton#primary:hover {{ background:{t['light']}; }}
        QPushButton#primary:disabled {{ background:#33404f; color:#8a96a3; }}
        QPushButton#chip {{ background:{t['alt']}; border:1px solid {line}; border-radius:4px; padding:3px 10px; }}
        QPushButton#chip:hover {{ border-color:{a}; }}
        QToolButton {{ padding:4px 8px; border-radius:4px; }}
        QToolButton:hover {{ background:{line}; }}
        QToolButton:checked {{ background:{t['sel']}; }}
        QSlider::groove:horizontal {{ height:4px; background:#33363d; border-radius:2px; }}
        QSlider::sub-page:horizontal {{ background:{a}; border-radius:2px; }}
        QSlider::handle:horizontal {{ background:#f2f2f2; width:12px; margin:-5px 0; border-radius:6px; }}
        QProgressBar {{ background:{t['base']}; border:1px solid {line}; border-radius:3px; color:{TEXT};
                        text-align:center; font-size:11px; }}
        QProgressBar::chunk {{ background:{a}; border-radius:2px; }}
        QTabWidget::pane {{ border:0; }}
        QTabBar::tab {{ background:{t['alt']}; color:{MUTED}; padding:6px 14px; border:0; margin-right:2px; }}
        QTabBar::tab:selected {{ background:{t['sel']}; color:#ffffff; }}
        QLabel#crumb {{ color:#c9d3e0; }}
        QLabel#now {{ color:{a}; font-weight:600; }}
        QSplitter::handle {{ background:{t['mid']}; }}
        QMenu::item:selected {{ background:{t['sel']}; }}
    """


_fusion_for = None      # the application Fusion was set on


def apply(app, t=BLUE, extra=""):
    """Colours, palette and stylesheet (plus an app's own extra rules) for the whole application. Fusion is set
    once per application: setting it again replaces the style object that the stylesheet style and every polished
    widget still use (a crash). The style's name can't tell: with a stylesheet it reads ''. """
    global _fusion_for
    from PySide6.QtGui import QColor, QPalette     # here, so the colours above can be read without Qt
    if _fusion_for is not app:
        app.setStyle("Fusion")
        _fusion_for = app
    p = QPalette()
    for role, key in ((QPalette.Window, "mid"), (QPalette.Base, "base"), (QPalette.AlternateBase, "alt"),
                      (QPalette.Button, "mid"), (QPalette.ToolTipBase, "alt"), (QPalette.Highlight, "sel")):
        p.setColor(role, QColor(t[key]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText, QPalette.ToolTipText):
        p.setColor(role, QColor(TEXT))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.PlaceholderText, QColor(PLACEHOLDER))
    app.setPalette(p)
    app.setStyleSheet(stylesheet(t) + extra)       # once (each set repolishes every widget)
