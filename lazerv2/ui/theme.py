# every colour in the app lives here

from PyQt6.QtCore import QObject, pyqtSignal

from lazerv2 import app_config
# x = fill, x_on = what sits on it, x_text = the accent as text

DARK = {
    "bg": "#0A0B0D",
    "surface": "#121419",
    "surface_alt": "#171A21",
    "surface_hover": "#1D212A",
    "border": "#232733",
    "border_strong": "#2E3441",

    "text": "#F2F5FA",
    "text_muted": "#9BA3B4",
    "text_dim": "#646C7E",

    "red": "#B02234",
    "red_hover": "#C62B3F",
    "red_on": "#FFFFFF",
    "red_text": "#E05260",
    "red_soft": "rgba(176, 34, 52, 0.16)",

    "blue": "#1E4FA3",
    "blue_hover": "#2A62C4",
    "blue_on": "#FFFFFF",
    "blue_text": "#6699E8",
    "blue_soft": "rgba(30, 79, 163, 0.16)",

    # any lighter and white text on it drops under 4.5:1 contrast
    "neutral": "#5E6878",
    "neutral_hover": "#6E7889",
    "neutral_on": "#FFFFFF",
    "neutral_text": "#9BA3B4",
    "neutral_soft": "rgba(94, 104, 120, 0.22)",

    "success": "#2E9E5B",
    "warning": "#C98A2B",
    "shadow": "rgba(0, 0, 0, 0.55)",
    "scrollbar": "#2E3441",
    "selection": "rgba(30, 79, 163, 0.45)",
}

LIGHT = {
    "bg": "#EEF1F5",
    "surface": "#FFFFFF",
    "surface_alt": "#F6F8FB",
    "surface_hover": "#EAEEF4",
    "border": "#DCE1E9",
    "border_strong": "#C3CBD8",

    "text": "#14161A",
    "text_muted": "#5A6474",
    "text_dim": "#8A94A6",

    "red": "#F2A8C0",
    "red_hover": "#EC91B0",
    "red_on": "#6B1E36",
    "red_text": "#C2185B",
    "red_soft": "rgba(242, 168, 192, 0.34)",

    "blue": "#A9CFF0",
    "blue_hover": "#8FC0EA",
    "blue_on": "#123A5E",
    "blue_text": "#1B5E9E",
    "blue_soft": "rgba(169, 207, 240, 0.38)",

    "neutral": "#C6CEDA",
    "neutral_hover": "#B6C0CE",
    "neutral_on": "#2A3242",
    "neutral_text": "#5A6474",
    "neutral_soft": "rgba(198, 206, 218, 0.40)",

    "success": "#3FA76A",
    "warning": "#B6842F",
    "shadow": "rgba(20, 22, 26, 0.16)",
    "scrollbar": "#C3CBD8",
    "selection": "rgba(169, 207, 240, 0.75)",
}

PALETTES = {"dark": DARK, "light": LIGHT}

ACCENTS = ("red", "blue", "neutral")

# shared with the sidebar so the two can't disagree
RAIL_COLLAPSED = 64
RAIL_EXPANDED = 232


class _ThemeManager(QObject):

    changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._name = "dark"

    @property
    def name(self):
        return self._name

    @property
    def palette(self):
        return PALETTES[self._name]

    def color(self, token):
        return PALETTES[self._name][token]

    def is_dark(self):
        return self._name == "dark"

    def apply(self, app, name=None, persist=True):
        if name is not None:
            if name not in PALETTES:
                raise ValueError(f"unknown theme: {name}")
            self._name = name

        app.setStyleSheet(build_stylesheet(self.palette))

        # a stylesheet swap on its own leaves stale values behind
        for widget in app.allWidgets():
            widget.style().unpolish(widget)
            widget.style().polish(widget)

        if persist:
            app_config.set_value("theme", self._name)
        self.changed.emit(self._name)

    def toggle(self, app):
        self.apply(app, "light" if self._name == "dark" else "dark")
        return self._name


theme = _ThemeManager()


def restyle(widget):
    # re-apply the stylesheet after changing a property
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def _chevron_svg(color):
    # tree branch arrows, inlined as data URIs
    hexcode = color.lstrip("#")
    closed = (f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" '
              f'fill="none" stroke="%23{hexcode}" stroke-width="2.5" stroke-linecap="round" '
              f'stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>')
    opened = (f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" '
              f'fill="none" stroke="%23{hexcode}" stroke-width="2.5" stroke-linecap="round" '
              f'stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>')
    return closed, opened


def _accent_rules(p):
    blocks = []
    for accent in ACCENTS:
        base, hover, on, soft, text = (p[accent], p[f"{accent}_hover"], p[f"{accent}_on"],
                                       p[f"{accent}_soft"], p[f"{accent}_text"])
        blocks.append(f"""
/* ---- accent: {accent} ---- */
*[accent="{accent}"] QPushButton#PrimaryBtn {{
    background-color: {base};
    color: {on};
    border: 1px solid {base};
}}
*[accent="{accent}"] QPushButton#PrimaryBtn:hover {{
    background-color: {hover};
    border-color: {hover};
}}
*[accent="{accent}"] QPushButton#PrimaryBtn:disabled {{
    background-color: {p['surface_alt']};
    color: {p['text_dim']};
    border-color: {p['border']};
}}
/* ghost buttons use _text, never the fill - the fill is a pastel in light
   mode and would be unreadable as text */
*[accent="{accent}"] QPushButton#GhostBtn {{
    background-color: transparent;
    color: {text};
    border: 1px solid {p['border_strong']};
}}
*[accent="{accent}"] QPushButton#GhostBtn:hover {{
    background-color: {soft};
    border-color: {text};
}}
*[accent="{accent}"] QPushButton#GhostBtn:disabled {{
    color: {p['text_dim']};
    border-color: {p['border']};
}}
*[accent="{accent}"] QLabel#SectionHeader {{
    color: {text};
}}
*[accent="{accent}"] QProgressBar::chunk {{
    background-color: {base};
}}
*[accent="{accent}"] QFrame#AccentBar {{
    background-color: {base};
}}
*[accent="{accent}"] QLineEdit:focus,
*[accent="{accent}"] QComboBox:focus,
*[accent="{accent}"] QComboBox:hover {{
    border-color: {base};
}}
*[accent="{accent}"] QTableWidget::item:selected {{
    background-color: {soft};
    color: {p['text']};
}}
QPushButton#NavBtn[accent="{accent}"]:checked {{
    background-color: {soft};
    color: {p['text']};
    border-left: 3px solid {base};
}}

/* selected segment is a filled control, so _on not _text */
*[accent="{accent}"] QPushButton#Segment:checked {{
    background-color: {base};
    color: {on};
    border-color: {base};
}}
*[accent="{accent}"] QPushButton#Segment:checked:hover {{
    background-color: {hover};
    border-color: {hover};
}}
*[accent="{accent}"] QFrame#Chip {{
    background-color: {soft};
    border-color: {text};
}}
*[accent="{accent}"] QFrame#Chip QLabel {{
    color: {text};
}}
*[accent="{accent}"] QLabel#StepDot[step="current"] {{
    background-color: {base};
    color: {on};
    border-color: {base};
}}
*[accent="{accent}"] QLabel#StepDot[step="done"] {{
    background-color: {soft};
    color: {text};
    border-color: {text};
}}
*[accent="{accent}"] QFrame#StepLine[step="done"] {{
    background-color: {text};
}}""")
    return "\n".join(blocks)


def build_stylesheet(palette):
    p = palette
    closed, opened = _chevron_svg(p["text_dim"])

    return f"""
/* =========================== base =========================== */
/* transparent by default. a blanket QWidget background also paints every
   layout container, which stamps page-coloured boxes inside cards. only
   windows and real surfaces get a background. */
QWidget {{
    background: transparent;
    color: {p['text']};
    font-family: 'Inter', 'Segoe UI Variable Display', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog, QWidget#Window {{
    background-color: {p['bg']};
}}
QToolTip {{
    background-color: {p['surface_alt']};
    color: {p['text']};
    border: 1px solid {p['border_strong']};
    padding: 6px 9px;
    border-radius: 6px;
}}

/* =========================== text =========================== */
QLabel {{
    background: transparent;
    border: none;
    color: {p['text']};
}}
QLabel#Muted      {{ color: {p['text_muted']}; }}
QLabel#Dim        {{ color: {p['text_dim']}; }}
QLabel#PageTitle  {{ font-size: 22px; font-weight: 700; }}
QLabel#CardValue  {{ font-size: 27px; font-weight: 700; }}
QLabel#CardLabel  {{ color: {p['text_muted']}; font-size: 11px; }}
QLabel#SectionHeader {{
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 1.4px;
    color: {p['text_muted']};
}}
QLabel#Logo {{
    font-size: 19px;
    font-weight: 700;
    letter-spacing: 1px;
    color: {p['text']};
}}
QLabel#Avatar {{
    background-color: {p['surface_alt']};
    border-radius: 25px;
}}
QLabel#AvatarLg {{
    background-color: {p['surface_alt']};
    border-radius: 36px;
}}
QLabel#AvatarSm {{
    background-color: {p['surface_alt']};
    border-radius: 16px;
}}

/* ========================== surfaces ========================= */
QFrame#Card, QFrame#StatCard, QFrame#FilterCard, QFrame#ProfileCard {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 12px;
}}
QFrame#StatCard:hover, QFrame#ProfileCard:hover {{
    border-color: {p['border_strong']};
    background-color: {p['surface_alt']};
}}
QFrame#Divider {{
    background-color: {p['border']};
    border: none;
    max-height: 1px;
}}

/* ========================== sidebar ========================== */
QFrame#Sidebar {{
    background-color: {p['surface']};
    border: none;
    border-right: 1px solid {p['border']};
}}
/* padding 0 - NavBtn lays out its icon and label itself */
QPushButton#NavBtn {{
    background-color: transparent;
    color: {p['text_muted']};
    text-align: left;
    padding: 0px;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton#NavBtn:hover {{
    background-color: {p['surface_hover']};
    color: {p['text']};
}}
QPushButton#RailBtn {{
    background-color: transparent;
    border: none;
    border-radius: 8px;
    padding: 8px;
}}
QPushButton#RailBtn:hover {{
    background-color: {p['surface_hover']};
}}
QPushButton#AccountBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 0px;
    text-align: left;
    color: {p['text']};
    font-weight: 600;
}}
QPushButton#AccountBtn:hover {{
    background-color: {p['surface_hover']};
    border-color: {p['border']};
}}

/* ============================ menu =========================== */
QMenu {{
    background-color: {p['surface']};
    border: 1px solid {p['border_strong']};
    border-radius: 10px;
    padding: 6px;
}}
QMenu::item {{
    padding: 8px 26px 8px 12px;
    border-radius: 6px;
    color: {p['text']};
}}
QMenu::item:selected {{
    background-color: {p['surface_hover']};
}}
QMenu::item:disabled {{
    color: {p['text_dim']};
}}
QMenu::separator {{
    height: 1px;
    background-color: {p['border']};
    margin: 5px 8px;
}}
QMenu::icon {{
    padding-left: 10px;
}}

/* =========================== inputs ========================== */
QLineEdit {{
    background-color: {p['surface_alt']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 9px 13px;
    color: {p['text']};
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
}}
QLineEdit:focus {{
    background-color: {p['surface']};
}}
QLineEdit#SearchBar {{
    border-radius: 18px;
    padding: 10px 18px;
}}
QComboBox {{
    background-color: {p['surface_alt']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 9px 13px;
    color: {p['text']};
}}
QComboBox::drop-down {{
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: url('data:image/svg+xml;utf8,{opened}');
    width: 14px;
    height: 14px;
}}
QComboBox QAbstractItemView {{
    background-color: {p['surface']};
    border: 1px solid {p['border_strong']};
    border-radius: 8px;
    padding: 4px;
    color: {p['text']};
    selection-background-color: {p['surface_hover']};
    selection-color: {p['text']};
    outline: none;
}}

/* ========================== buttons ========================== */
QPushButton {{
    background-color: {p['surface_alt']};
    color: {p['text']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    padding: 10px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {p['surface_hover']};
    border-color: {p['border_strong']};
}}
QPushButton:disabled {{
    color: {p['text_dim']};
    background-color: {p['surface']};
    border-color: {p['border']};
}}
QPushButton#DangerBtn {{
    background-color: {p['red']};
    color: {p['red_on']};
    border-color: {p['red']};
}}
QPushButton#DangerBtn:hover {{
    background-color: {p['red_hover']};
    border-color: {p['red_hover']};
}}
QPushButton#DangerBtn:disabled {{
    background-color: {p['surface_alt']};
    color: {p['text_dim']};
    border-color: {p['border']};
}}
QPushButton#LinkBtn {{
    background: transparent;
    border: none;
    color: {p['text_muted']};
    text-decoration: underline;
    padding: 4px;
    font-weight: 500;
}}
QPushButton#LinkBtn:hover {{
    color: {p['text']};
}}

/* =========================== tables ========================== */
QTableWidget {{
    background-color: {p['surface']};
    alternate-background-color: {p['surface_alt']};
    border: 1px solid {p['border']};
    border-radius: 10px;
    gridline-color: {p['border']};
    outline: none;
}}
QTableWidget::item {{
    padding: 7px;
    border: none;
    color: {p['text']};
}}
QHeaderView::section {{
    background-color: {p['surface_alt']};
    color: {p['text_muted']};
    padding: 9px;
    border: none;
    border-bottom: 1px solid {p['border']};
    font-weight: 700;
    font-size: 10px;
}}
QTableCornerButton::section {{
    background-color: {p['surface_alt']};
    border: none;
}}

/* ============================ tree =========================== */
QTreeWidget {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 10px;
    outline: none;
}}
QTreeWidget::item {{
    background-color: transparent;
    border-bottom: 1px solid {p['border']};
    padding: 2px;
    color: {p['text']};
}}
QTreeWidget::item:hover {{
    background-color: {p['surface_hover']};
}}
QTreeWidget::item:selected {{
    background-color: {p['selection']};
    color: {p['text']};
}}
QTreeView::branch:has-children:!has-siblings:closed,
QTreeView::branch:closed:has-children:has-siblings {{
    image: url('data:image/svg+xml;utf8,{closed}');
}}
QTreeView::branch:open:has-children:!has-siblings,
QTreeView::branch:open:has-children:has-siblings {{
    image: url('data:image/svg+xml;utf8,{opened}');
}}

/* ========================== progress ========================= */
QProgressBar {{
    background-color: {p['surface_alt']};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {p['neutral']};
    border-radius: 4px;
}}

/* ========================= scrollbars ======================== */
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: {p['scrollbar']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {p['text_dim']};
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
}}
QScrollBar::handle:horizontal {{
    background: {p['scrollbar']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
    border: none;
    background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* ====================== segmented control =================== */
/* radius comes from the position property so the group reads as one control.
   the -1px margin stops adjacent borders doubling into a 2px seam. */
QPushButton#Segment {{
    background-color: {p['surface_alt']};
    color: {p['text_muted']};
    border: 1px solid {p['border']};
    border-radius: 0px;
    padding: 8px 14px;
    font-weight: 600;
}}
QPushButton#Segment:hover {{
    background-color: {p['surface_hover']};
    color: {p['text']};
}}
QPushButton#Segment[segment="first"] {{
    border-top-left-radius: 8px;
    border-bottom-left-radius: 8px;
}}
QPushButton#Segment[segment="last"] {{
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
    margin-left: -1px;
}}
QPushButton#Segment[segment="middle"] {{
    margin-left: -1px;
}}
QPushButton#Segment[segment="only"] {{
    border-radius: 8px;
}}

/* ============================ chips ========================= */
QFrame#Chip {{
    background-color: {p['surface_alt']};
    border: 1px solid {p['border_strong']};
    border-radius: 12px;
}}
QFrame#Chip QLabel {{
    color: {p['text']};
    font-weight: 600;
}}
QPushButton#ChipClose {{
    background: transparent;
    border: none;
    border-radius: 8px;
    color: {p['text_dim']};
    font-size: 10px;
    padding: 0px;
}}
QPushButton#ChipClose:hover {{
    background-color: {p['border_strong']};
    color: {p['text']};
}}

/* ========================= step indicator =================== */
QLabel#StepDot {{
    background-color: {p['surface_alt']};
    border: 1px solid {p['border_strong']};
    border-radius: 12px;
    color: {p['text_dim']};
    font-weight: 700;
    font-size: 11px;
}}
QLabel#StepLabel {{
    color: {p['text_dim']};
    font-weight: 600;
}}
QLabel#StepLabel[step="current"] {{ color: {p['text']}; }}
QLabel#StepLabel[step="done"]    {{ color: {p['text_muted']}; }}
QFrame#StepLine {{
    background-color: {p['border']};
    border: none;
}}

/* ========================== action bar ====================== */
QFrame#ActionBar {{
    background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 10px;
}}

/* ======================= status dots ======================== */
QLabel#DotOn      {{ color: {p['success']}; font-size: 15px; }}
QLabel#DotOff     {{ color: {p['text_dim']}; font-size: 15px; }}
QLabel#DotWarn    {{ color: {p['warning']}; font-size: 15px; }}
QLabel#DotDanger  {{ color: {p['red_text']}; font-size: 15px; }}

/* ==================== message box / dialog =================== */
QMessageBox {{
    background-color: {p['surface']};
}}
QMessageBox QLabel {{
    color: {p['text']};
}}
{_accent_rules(p)}
"""
