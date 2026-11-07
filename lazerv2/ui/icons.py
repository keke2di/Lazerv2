# inline svg icons, recoloured per theme

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

# 24x24 and stroked, so one colour swap restyles the glyph
_STROKE = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
           'stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round">'
           '{body}</svg>')

_BODIES = {
    # nav
    "overview": '<rect x="3" y="3" width="7" height="9" rx="1.5"/>'
                '<rect x="14" y="3" width="7" height="5" rx="1.5"/>'
                '<rect x="14" y="12" width="7" height="9" rx="1.5"/>'
                '<rect x="3" y="16" width="7" height="5" rx="1.5"/>',
    "purge": '<path d="M3 6h18"/><path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>'
             '<path d="M19 6l-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6"/>'
             '<path d="M10 11v6"/><path d="M14 11v6"/>',
    "guard": '<path d="M12 3l7 3v6c0 4.5-3 8-7 9-4-1-7-4.5-7-9V6z"/>'
             '<path d="M9 12l2 2 4-4"/>',

    # chrome
    "chevron_down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron_right": '<polyline points="9 18 15 12 9 6"/>',
    "sun": '<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2"/><path d="M12 20v2"/>'
           '<path d="M4.2 4.2l1.4 1.4"/><path d="M18.4 18.4l1.4 1.4"/><path d="M2 12h2"/>'
           '<path d="M20 12h2"/><path d="M4.2 19.8l1.4-1.4"/><path d="M18.4 5.6l1.4-1.4"/>',
    "moon": '<path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
              '<polyline points="16 17 21 12 16 7"/><path d="M21 12H9"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6l-1 14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1L5 6"/>'
             '<path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "check": '<polyline points="4 12 9 17 20 6"/>',
    "user_plus": '<path d="M15 20v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>'
                 '<circle cx="8.5" cy="7" r="4"/><path d="M19 8v6"/><path d="M22 11h-6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    "server": '<rect x="3" y="4" width="18" height="7" rx="2"/>'
              '<rect x="3" y="13" width="18" height="7" rx="2"/>'
              '<path d="M7 7.5h.01"/><path d="M7 16.5h.01"/>',
    "message": '<path d="M21 11.5a8.5 8.5 0 0 1-9 8.5 9.7 9.7 0 0 1-4-.9L3 21l1.9-4.6A8.4 8.4 0 0 1 4 11.5a8.5 8.5 0 0 1 17 0z"/>',
    "hash": '<path d="M4 9h16"/><path d="M4 15h16"/><path d="M10 3L8 21"/><path d="M16 3l-2 18"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>',
    # brand mark, kept distinct from the purge trash glyph
    "bolt": '<path d="M13 2L4 14h6l-1 8 9-12h-6z"/>',
}

_cache = {}


def available():
    return sorted(_BODIES)


def _device_ratio():
    app = QApplication.instance()
    if app is None:
        return 1.0
    screen = app.primaryScreen()
    return (screen.devicePixelRatio() if screen else 1.0) or 1.0


def pixmap(name, color, size=20, width=2.0):
    if name not in _BODIES:
        raise KeyError(f"unknown icon: {name!r} (have: {', '.join(available())})")

    ratio = _device_ratio()
    key = (name, color, size, width, round(ratio, 2))
    cached = _cache.get(key)
    if cached is not None:
        return cached

    svg = _STROKE.format(c=color, w=width, body=_BODIES[name])
    renderer = QSvgRenderer(QByteArray(svg.encode()))

    physical = max(1, int(round(size * ratio)))
    pm = QPixmap(physical, physical)
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(ratio, ratio)
    renderer.render(painter)
    painter.end()

    _cache[key] = pm
    return pm


def icon(name, color, size=20, width=2.0):
    return QIcon(pixmap(name, color, size, width))


def clear_cache():
    _cache.clear()
