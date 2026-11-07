# search box plus a tree of DMs and guild channels

import requests
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit,
                             QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
                             QVBoxLayout, QWidget)

from lazerv2.globals import get_brand_font
from lazerv2.ui import threads
from lazerv2.ui.widgets import Avatar
from lazerv2 import session_store
ICON_TIMEOUT = 3
MAX_ICON_WORKERS = 8
_MISSING = object()


class IconWorker(QThread):
    image_ready = pyqtSignal(str, bytes)   # url, data (b"" on failure)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            res = requests.get(self.url, timeout=ICON_TIMEOUT)
        except requests.RequestException:
            self.image_ready.emit(self.url, b"")
            return
        self.image_ready.emit(self.url, res.content if res.status_code == 200 else b"")


def server_wide_target(server):
    # one purge target for everything you wrote in a server, threads and forum
    # posts included. It is searched as a whole; if search is unavailable the
    # worker falls back to these channels one by one.
    return {
        "id": f"guild:{server['id']}",
        "name": f"Everything in {server['name']}",
        "type": "guild_all",
        "guild_id": server["id"],
        "icon": server.get("icon"),
        "channels": [{"id": c["id"], "name": c.get("name", "")}
                     for c in server.get("channels", [])],
    }


class ChannelPicker(QWidget):
    selection_changed = pyqtSignal(int)

    def __init__(self, dms=None, servers=None, parent=None, server_wide=False):
        super().__init__(parent)
        # the purge page offers whole-server targets; the guard watches
        # individual channels, so it doesn't
        self.server_wide = server_wide
        self.dms = dms if dms is not None else []
        self.servers = servers if servers is not None else []
        self.workers = []
        self._url_cache = {}        # url -> bytes, or None for "failed"
        self._inflight = {}         # url -> labels waiting
        self._queued = []           # urls waiting for a free worker
        self._selected_servers = set()
        self._shutting_down = False

        self.init_ui()
        if self.dms or self.servers:
            QTimer.singleShot(0, self.populate_tree)

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.search_bar = QLineEdit()
        self.search_bar.setObjectName("SearchBar")
        self.search_bar.setPlaceholderText("Search by name...")
        self.search_bar.textChanged.connect(self.filter_tree)
        layout.addWidget(self.search_bar)

        self.tree_widget = QTreeWidget()
        self.tree_widget.setHeaderHidden(True)
        self.tree_widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self.tree_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.tree_widget.itemSelectionChanged.connect(self.handle_tree_selection)
        layout.addWidget(self.tree_widget)

    def set_data(self, dms, servers):
        # refill the tree once DataFetcher is done without losing the ticks
        selected = self.get_selected_ids(visible_only=False)
        self.dms = dms or []
        self.servers = servers or []
        self.populate_tree(selected)

    def populate_tree(self, keep_selected=None):
        selected = self.get_selected_ids(visible_only=False) if keep_selected is None else keep_selected
        self.tree_widget.setUpdatesEnabled(False)
        self.tree_widget.clear()
        self._selected_servers = set()

        if not self.dms and not self.servers:
            item = QTreeWidgetItem(["Loading channels..."])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.tree_widget.addTopLevelItem(item)
            self.tree_widget.setUpdatesEnabled(True)
            self.selection_changed.emit(0)
            return

        for dm in self.dms:
            self.add_tree_item(dm, "Direct Message", parent_item=None)

        for server in self.servers:
            server_item = self.add_tree_item(server, "Server", is_bold=True, parent_item=None)
            if self.server_wide:
                self.add_tree_item(server_wide_target(server),
                                   "All channels, threads and forum posts",
                                   parent_item=server_item)
            for chan in server.get("channels", []):
                chan['icon'] = server.get('icon')
                kind = chan.get("kind") or "Channel"
                self.add_tree_item(chan, f"{kind} in {server['name']}",
                                   is_sub=True, parent_item=server_item)

        self._restore_selection(selected)
        if self.search_bar.text():
            self.filter_tree()
        self.tree_widget.setUpdatesEnabled(True)
        self.selection_changed.emit(len(self.get_selected()))

    def add_tree_item(self, item, subtext, is_bold=False, is_sub=False, parent_item=None):
        container = QWidget()
        item_layout = QHBoxLayout(container)
        item_layout.setContentsMargins(5, 7, 15, 7)
        item_layout.setSpacing(12)

        icon_label = Avatar(32, letter=item['name'] or "?")

        text_container = QVBoxLayout()
        text_container.setSpacing(1)
        name_label = QLabel(("# " if is_sub else "") + item['name'])
        name_label.setFont(get_brand_font(10, is_bold))
        detail_label = QLabel(subtext)
        detail_label.setObjectName("Dim")
        detail_label.setFont(get_brand_font(8))
        text_container.addWidget(name_label)
        text_container.addWidget(detail_label)

        item_layout.addWidget(icon_label)
        item_layout.addLayout(text_container)
        item_layout.addStretch()

        # passing the parent already attaches the item
        tree_item = QTreeWidgetItem(parent_item if parent_item else self.tree_widget)
        tree_item.setData(0, Qt.ItemDataRole.UserRole, item)

        self.tree_widget.setItemWidget(tree_item, 0, container)
        self.request_icon(icon_label, self.get_icon_url(item))
        return tree_item

    def request_icon(self, label, url):
        if not url or self._shutting_down:
            return

        cached = self._url_cache.get(url, _MISSING)
        if cached is not _MISSING:
            if cached:
                self._apply_icon(label, cached)
            return

        waiters = self._inflight.get(url)
        if waiters is not None:
            waiters.append(label)
            return

        stored = session_store.get_cached_image(url)
        if stored:
            self._url_cache[url] = stored
            self._apply_icon(label, stored)
            return

        self._inflight[url] = [label]
        self._queued.append(url)
        self._drain_queue()

    def _drain_queue(self):
        self._prune_workers()
        while self._queued and len(self.workers) < MAX_ICON_WORKERS:
            url = self._queued.pop(0)
            if url not in self._inflight:
                continue
            worker = IconWorker(url)
            worker.image_ready.connect(self._on_icon_ready)
            self.workers.append(worker)
            worker.start()

    def _on_icon_ready(self, url, data):
        self._url_cache[url] = data or None
        if data:
            session_store.cache_image(url, data)
        for label in self._inflight.pop(url, []):
            self._apply_icon(label, data)
        self._drain_queue()

    @staticmethod
    def _apply_icon(label, data):
        # the label can be gone by the time the fetch lands
        if not data:
            return
        try:
            label.set_image(data)
        except RuntimeError:
            pass

    def _prune_workers(self):
        self.workers = [worker for worker in self.workers if worker.isRunning()]

    def get_icon_url(self, item):
        item_type = item.get('type')
        if item_type == 'dm' and item.get('recipients'):
            recipient = item['recipients'][0]
            if recipient.get('avatar'):
                return (f"https://cdn.discordapp.com/avatars/{recipient['id']}/"
                        f"{recipient['avatar']}.png?size=64")
        elif item_type in ('server', 'channel', 'guild_all') and item.get('icon'):
            guild_id = item.get('guild_id') or item['id']
            return f"https://cdn.discordapp.com/icons/{guild_id}/{item['icon']}.png?size=64"
        return None

    def handle_tree_selection(self):
        current = set()
        for item in self.tree_widget.selectedItems():
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get('type') == 'server':
                current.add(str(data['id']))

        # only tick a server's children when that server was just selected,
        # otherwise deselecting one channel inside it would undo itself
        newly_selected = current - self._selected_servers
        self._selected_servers = current

        if newly_selected:
            self.tree_widget.blockSignals(True)
            for item in self.tree_widget.selectedItems():
                data = item.data(0, Qt.ItemDataRole.UserRole)
                if data and data.get('type') == 'server' and str(data['id']) in newly_selected:
                    for index in range(item.childCount()):
                        item.child(index).setSelected(True)
            self.tree_widget.blockSignals(False)

        self.selection_changed.emit(len(self.get_selected()))

    @staticmethod
    def _effectively_hidden(item):
        while item is not None:
            if item.isHidden():
                return True
            item = item.parent()
        return False

    def _selected_data(self, visible_only):
        selected = []
        iterator = QTreeWidgetItemIterator(self.tree_widget,
                                           QTreeWidgetItemIterator.IteratorFlag.Selected)
        while iterator.value():
            item = iterator.value()
            data = item.data(0, Qt.ItemDataRole.UserRole)
            if data and data.get('type') != 'server':
                if not visible_only or not self._effectively_hidden(item):
                    selected.append(data)
            iterator += 1
        return selected

    def get_selected(self):
        # hidden rows are not part of a purge, or search would silently widen it
        return self._selected_data(visible_only=True)

    def get_selected_ids(self, visible_only=True):
        return {str(item['id']) for item in self._selected_data(visible_only=visible_only)}

    def _restore_selection(self, selected):
        if not selected:
            return
        self.tree_widget.blockSignals(True)
        for index in range(self.tree_widget.topLevelItemCount()):
            parent = self.tree_widget.topLevelItem(index)
            parent_data = parent.data(0, Qt.ItemDataRole.UserRole)
            if parent_data and str(parent_data.get('id')) in selected:
                parent.setSelected(True)
                self._selected_servers.add(str(parent_data['id']))
            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                child_data = child.data(0, Qt.ItemDataRole.UserRole)
                if child_data and str(child_data.get('id')) in selected:
                    child.setSelected(True)
                    parent.setExpanded(True)
        self.tree_widget.blockSignals(False)

    def clear_selection(self):
        self.tree_widget.clearSelection()
        self._selected_servers = set()
        self.selection_changed.emit(0)

    def filter_tree(self):
        query = self.search_bar.text().lower()
        self.tree_widget.setUpdatesEnabled(False)

        for index in range(self.tree_widget.topLevelItemCount()):
            parent = self.tree_widget.topLevelItem(index)
            parent_data = parent.data(0, Qt.ItemDataRole.UserRole)

            if not parent_data:
                continue

            parent_match = query in parent_data['name'].lower()
            child_match_count = 0

            for child_index in range(parent.childCount()):
                child = parent.child(child_index)
                child_data = child.data(0, Qt.ItemDataRole.UserRole)
                if child_data and query in child_data['name'].lower():
                    child.setHidden(False)
                    child_match_count += 1
                else:
                    child.setHidden(True)

            if parent_match or child_match_count > 0:
                parent.setHidden(False)
                if query and child_match_count > 0:
                    parent.setExpanded(True)
            else:
                parent.setHidden(True)

        self.tree_widget.setUpdatesEnabled(True)
        # the visible set changed, so the count has to follow
        self.selection_changed.emit(len(self.get_selected()))

    def shutdown(self):
        # every worker has a 3s request timeout; keep any still running
        # referenced until it ends instead of blocking or terminate()ing it
        self._shutting_down = True
        for worker in self.workers:
            threads.park(worker)
        self.workers = []
        self._inflight.clear()
        self._queued.clear()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)