"""A native ENVY Casebook window: a real desktop app, not a web view.

Built with PyQt6 and styled in the ENVY palette with a live animated header.
Create or open an encrypted case, add note / entity / event / link records,
read a decrypted detail pane, see a neon link chart of the connections you have
entered, and export a plaintext HTML or JSON report. Every save writes a new
encrypted revision file; earlier revisions are never overwritten.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from . import __version__
from . import core
from .core import CasebookError

BG = "#050713"
PANEL = "#0a0e22"
PANEL_HI = "#121735"
INK = "#f2f4ff"
MUTED = "#b9c3df"
DIM = "#6f7898"
PINK = "#ff3db4"
CYAN = "#35e8ff"
LAVENDER = "#ac8cff"
VIOLET = "#8547e8"
LINE = "#1f2547"

# Each record kind's accent colour, shared by the list, details and chart.
KIND_ACCENT = {"note": CYAN, "entity": PINK, "event": LAVENDER, "link": VIOLET}
KIND_FILL = {"note": "#0f2b2f", "entity": "#2a1030", "event": "#1f1b3e", "link": "#241042"}


def lerp(a, b, t):
    from PyQt6.QtGui import QColor
    ca, cb = QColor(a), QColor(b)
    return QColor(int(ca.red() + (cb.red() - ca.red()) * t),
                  int(ca.green() + (cb.green() - ca.green()) * t),
                  int(ca.blue() + (cb.blue() - ca.blue()) * t))


def _build():
    from PyQt6.QtCore import Qt, QTimer, QRectF, QPointF
    from PyQt6.QtGui import (QColor, QPainter, QLinearGradient, QRadialGradient, QFont,
                             QPen, QBrush, QGuiApplication)
    from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QPlainTextEdit,
                                 QLineEdit, QComboBox, QListWidget, QListWidgetItem, QTabWidget,
                                 QVBoxLayout, QHBoxLayout, QSplitter, QGraphicsView, QGraphicsScene,
                                 QInputDialog, QFileDialog, QMessageBox, QLineEdit as QLE)

    class Header(QWidget):
        def __init__(self):
            super().__init__()
            self.setFixedHeight(172)
            self.phase = 0.0
            self.stars = [(((i * 139 + 37) % 1000) / 1000.0,
                           ((i * 83 + 19) % 172) / 172.0,
                           0.6 + (i % 3) * 0.5, (i % 7) / 7.0) for i in range(60)]

        def tick(self, phase):
            self.phase = phase
            self.update()

        def paintEvent(self, _event):
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            p.fillRect(self.rect(), QColor(BG))
            for cx, cy, rad, col, a in ((w - 120, 60, 250, VIOLET, 60),
                                        (60, h, 280, PINK, 52), (w * 0.5, -40, 240, CYAN, 42)):
                g = QRadialGradient(cx, cy, rad)
                c = QColor(col); c.setAlpha(a); g.setColorAt(0, c)
                c2 = QColor(col); c2.setAlpha(0); g.setColorAt(1, c2)
                p.setBrush(QBrush(g)); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(cx, cy), rad, rad)
            for sx, sy, sr, ph in self.stars:
                tw = 0.5 + 0.5 * math.sin(self.phase / 0.5 + ph * 6.28)
                c = QColor(205, 217, 255); c.setAlphaF(0.15 + 0.6 * tw)
                p.setBrush(c); p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(QPointF(sx * w, sy * h), sr, sr)
            pulse = 0.5 + 0.5 * math.sin(self.phase)
            p.setFont(QFont("Helvetica", 23))
            p.setPen(QColor(CYAN) if pulse > 0.5 else QColor(PINK))
            p.drawText(QRectF(24, 28, 42, 44), Qt.AlignmentFlag.AlignCenter, "🗂")
            p.setFont(QFont("Helvetica", 37, QFont.Weight.Bold))
            grad = QLinearGradient(70, 0, 460, 0)
            grad.setColorAt(0, QColor(PINK))
            grad.setColorAt(0.5, QColor(lerp(PINK, "#ffffff", 0.2 * pulse)))
            grad.setColorAt(1, QColor(CYAN))
            glow = QColor(VIOLET); glow.setAlpha(int(55 + 55 * pulse))
            p.setPen(QPen(glow, 1))
            for dx, dy in ((1.3, 1.3), (-1.3, 1.3), (1.3, -1.3)):
                p.drawText(QRectF(70 + dx, 34 + dy, 560, 52), Qt.AlignmentFlag.AlignVCenter, "ENVY CASEBOOK")
            p.setPen(QPen(QBrush(grad), 1))
            p.drawText(QRectF(70, 34, 560, 52), Qt.AlignmentFlag.AlignVCenter, "ENVY CASEBOOK")
            p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
            p.setPen(QColor(LAVENDER))
            p.drawText(QRectF(74, 90, 560, 20), Qt.AlignmentFlag.AlignVCenter,
                       "ENCRYPTED  ·  LOCAL  ·  REVISION-PRESERVING")
            y = 126
            p.setPen(QPen(QColor(LINE), 2)); p.drawLine(74, y, w - 54, y)
            p.setPen(QPen(QColor(VIOLET), 3)); p.drawLine(74, y, 146, y)
            span = (w - 54) - 74
            x = 74 + span * ((self.phase * 0.12) % 1.0)
            halo = QRadialGradient(x, y, 15)
            hc = QColor(PINK); hc.setAlpha(170); halo.setColorAt(0, hc)
            hc2 = QColor(PINK); hc2.setAlpha(0); halo.setColorAt(1, hc2)
            p.setBrush(QBrush(halo)); p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, y), 15, 15)
            p.setBrush(QColor("#ffffff")); p.drawEllipse(QPointF(x, y), 2.5, 2.5)
            p.end()

    class LinkChart(QGraphicsView):
        """A neon chart of the links and references entered in the case."""
        def __init__(self):
            super().__init__()
            self.setScene(QGraphicsScene(self))
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setRenderHint(QPainter.RenderHint.Antialiasing)
            self.setBackgroundBrush(QColor(PANEL))

        def wheelEvent(self, event):
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            if 0.05 <= self.transform().m11() * factor <= 3:
                self.scale(factor, factor)
            event.accept()

        def show_case(self, data):
            self.scene().clear()
            self.resetTransform()
            if not data or not data["records"]:
                text = self.scene().addText("No records yet. Add linked entities and events to see the chart.")
                text.setDefaultTextColor(QColor(DIM))
                return
            model = core.graph_data(data)
            positions, counts = {}, {kind: 0 for kind in core.KINDS}
            columns = {"entity": 0, "event": 1, "note": 2, "link": 3}
            for node in model["nodes"]:
                kind = node["kind"]
                positions[node["id"]] = (columns[kind] * 320, counts[kind] * 112)
                counts[kind] += 1
            for edge in model["edges"]:
                first, second = positions[edge["source"]], positions[edge["target"]]
                x1, y1, x2, y2 = first[0] + 115, first[1] + 32, second[0] + 115, second[1] + 32
                relationship = edge["kind"] == "relationship"
                color = QColor(VIOLET if relationship else "#3a4680")
                pen = QPen(color, 2.0 if relationship else 1.2)
                self.scene().addLine(x1, y1, x2, y2, pen)
                angle = math.atan2(y2 - y1, x2 - x1)
                ax, ay = x1 + (x2 - x1) * 0.66, y1 + (y2 - y1) * 0.66
                for delta in (-0.45, 0.45):
                    self.scene().addLine(ax, ay, ax - 13 * math.cos(angle + delta),
                                         ay - 13 * math.sin(angle + delta), pen)
                if relationship:
                    label = self.scene().addText(edge["label"][:48])
                    label.setDefaultTextColor(color)
                    label.setPos((x1 + x2) / 2, (y1 + y2) / 2 - 20)
            for node in model["nodes"]:
                x, y = positions[node["id"]]
                accent = QColor(KIND_ACCENT[node["kind"]])
                rect = self.scene().addRect(x, y, 230, 66, QPen(accent, 1.6),
                                            QBrush(QColor(KIND_FILL[node["kind"]])))
                rect.setData(0, node["id"])
                text = self.scene().addText(node["kind"].title() + ": " + node["title"][:48] + "\n" + node["id"][:8])
                text.setTextWidth(220)
                text.setDefaultTextColor(QColor(INK))
                text.setPos(x + 6, y + 4)
            if model["omitted_nodes"] or model["omitted_edges"]:
                note = self.scene().addText(
                    f"{model['omitted_nodes']} records and {model['omitted_edges']} links beyond the display limit are not shown.")
                note.setDefaultTextColor(QColor(DIM))
                note.setPos(0, counts and max((counts[k] for k in counts), default=0) * 112 + 30)
            self.scene().setSceneRect(self.scene().itemsBoundingRect().adjusted(-40, -40, 40, 40))
            if self.scene().items():
                self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.source = None     # Path of the current encrypted revision
            self.secret = None     # passphrase bytes, kept in memory for this case only
            self.data = None       # decrypted, validated case dict
            self.setWindowTitle("ENVY Casebook")
            self.resize(1120, 860)
            self.setMinimumSize(900, 720)
            self.setStyleSheet(STYLE)
            root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
            self.header = Header(); root.addWidget(self.header)

            body = QWidget(); body.setObjectName("body")
            bl = QVBoxLayout(body); bl.setContentsMargins(22, 14, 22, 16); bl.setSpacing(10)

            controls = QHBoxLayout()
            for caption, handler, name in (("New encrypted case", self.new_case, "go"),
                                           ("Open case", self.open_case, "pick"),
                                           ("Lock && clear", self.lock, "pick"),
                                           ("Export report", self.export_report, "pick")):
                b = QPushButton(caption); b.setObjectName(name)
                b.setCursor(Qt.CursorShape.PointingHandCursor); b.clicked.connect(handler)
                controls.addWidget(b)
            controls.addStretch(1)
            bl.addLayout(controls)

            self.status = QLabel("No case open. Create a new encrypted case or open an existing one.")
            self.status.setObjectName("status"); self.status.setWordWrap(True)
            bl.addWidget(self.status)

            split = QSplitter(); split.setObjectName("split")
            left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(8)
            ll.addWidget(self._tag("RECORDS"))
            self.filter = QComboBox(); self.filter.addItems(["All records", "Timeline (events)", "Entities", "Links", "Notes"])
            self.filter.currentIndexChanged.connect(self.refresh_records)
            ll.addWidget(self.filter)
            self.records = QListWidget(); self.records.setObjectName("records")
            self.records.currentRowChanged.connect(self.show_selected)
            ll.addWidget(self.records, 1)
            split.addWidget(left)

            self.tabs = QTabWidget(); self.tabs.setObjectName("tabs")
            self.detail = QPlainTextEdit(); self.detail.setObjectName("mono"); self.detail.setReadOnly(True)
            self.tabs.addTab(self.detail, "Record details")
            self.chart = LinkChart(); self.tabs.addTab(self.chart, "Link chart")
            self.tabs.addTab(self._add_tab(), "Add record")
            self.tabs.currentChanged.connect(self._tab_changed)
            split.addWidget(self.tabs)
            split.setStretchFactor(0, 2); split.setStretchFactor(1, 5)
            bl.addWidget(split, 1)

            foot = QLabel("Local and offline. Timestamps and provenance are operator assertions, not trusted "
                          "facts. Each save writes a new encrypted revision; earlier revisions are kept.")
            foot.setObjectName("foot"); foot.setWordWrap(True); bl.addWidget(foot)
            root.addWidget(body, 1)

            self.phase = 0.0
            self.timer = QTimer(self); self.timer.timeout.connect(self._animate); self.timer.start(33)
            self._set_enabled(False)

        # ----- add-record form ---------------------------------------------
        def _add_tab(self):
            tab = QWidget(); g = QVBoxLayout(tab); g.setSpacing(8)
            g.addWidget(self._tag("KIND"))
            self.kind = QComboBox(); self.kind.addItems(list(core.KINDS))
            self.kind.currentIndexChanged.connect(self._kind_changed)
            g.addWidget(self.kind)
            g.addWidget(self._tag("TITLE"))
            self.f_title = QLineEdit(); self.f_title.setObjectName("field"); g.addWidget(self.f_title)
            g.addWidget(self._tag("BODY / OBSERVATION"))
            self.f_body = QPlainTextEdit(); self.f_body.setObjectName("mono"); self.f_body.setMinimumHeight(90)
            g.addWidget(self.f_body, 1)
            self.obs_tag = self._tag("OBSERVED (UTC, ISO 8601 — e.g. 2026-11-06T14:00:00+00:00)")
            g.addWidget(self.obs_tag)
            self.f_obs = QLineEdit(); self.f_obs.setObjectName("field"); g.addWidget(self.f_obs)
            self.ref_tag = self._tag("REFERENCES (existing records — a link needs exactly two)")
            g.addWidget(self.ref_tag)
            self.f_refs = QListWidget(); self.f_refs.setObjectName("records")
            self.f_refs.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
            self.f_refs.setMaximumHeight(120)
            g.addWidget(self.f_refs)
            g.addWidget(self._tag("PROVENANCE SOURCE (path, object ID or URL — optional)"))
            self.f_src = QLineEdit(); self.f_src.setObjectName("field"); g.addWidget(self.f_src)
            g.addWidget(self._tag("PROVENANCE NOTE (how you obtained it — optional)"))
            self.f_note = QLineEdit(); self.f_note.setObjectName("field"); g.addWidget(self.f_note)
            row = QHBoxLayout()
            add = QPushButton("Add record & save revision"); add.setObjectName("go")
            add.setCursor(Qt.CursorShape.PointingHandCursor); add.clicked.connect(self.add_record)
            self.add_status = QLabel(""); self.add_status.setObjectName("status")
            row.addWidget(add); row.addWidget(self.add_status, 1)
            g.addLayout(row)
            self._kind_changed()
            return tab

        def _tag(self, text):
            lab = QLabel(text); lab.setObjectName("tag"); return lab

        def _kind_changed(self):
            kind = self.kind.currentText()
            self.obs_tag.setVisible(kind == "event")
            self.f_obs.setVisible(kind == "event")
            link = kind == "link"
            self.ref_tag.setText("REFERENCES (a link needs exactly two existing records)" if link
                                 else "REFERENCES (existing records — optional)")

        # ----- case lifecycle ----------------------------------------------
        def _ask_passphrase(self, confirm):
            text, ok = QInputDialog.getText(self, "Passphrase",
                                            "Case passphrase (at least 12 characters):", QLE.EchoMode.Password)
            if not ok:
                return None
            secret = text.encode("utf-8")
            if confirm:
                again, ok2 = QInputDialog.getText(self, "Confirm passphrase",
                                                  "Repeat the passphrase:", QLE.EchoMode.Password)
                if not ok2:
                    return None
                if again.encode("utf-8") != secret:
                    self._warn("Passphrases do not match.")
                    return None
            try:
                return core.check_passphrase(secret)
            except CasebookError as exc:
                self._warn(str(exc)); return None

        def new_case(self):
            title, ok = QInputDialog.getText(self, "New case", "Case title:")
            if not ok or not title.strip():
                return
            dest, _ = QFileDialog.getSaveFileName(self, "Create encrypted case", "case.ecase",
                                                  "Encrypted case (*.ecase);;All files (*)")
            if not dest:
                return
            secret = self._ask_passphrase(confirm=True)
            if secret is None:
                return
            try:
                if Path(dest).exists():
                    Path(dest).unlink()  # the save dialog already confirmed the name
                core.create(dest, title.strip(), secret)
                self._open_path(Path(dest), secret)
                self.add_status.setText("Case created. Add your first record here.")
            except (CasebookError, OSError) as exc:
                self._warn(str(exc))

        def open_case(self):
            path, _ = QFileDialog.getOpenFileName(self, "Open encrypted case", "",
                                                  "Encrypted case (*.ecase);;All files (*)")
            if not path:
                return
            secret = self._ask_passphrase(confirm=False)
            if secret is None:
                return
            self._open_path(Path(path), secret)

        def _open_path(self, path, secret):
            try:
                data, _ = core._load(path, secret)
            except CasebookError as exc:
                self._warn(str(exc)); return
            self.source, self.secret, self.data = path, secret, data
            self._set_enabled(True)
            self.refresh_records()
            self._update_status()
            self._refresh_refs()
            self.chart.show_case(self.data)

        def lock(self):
            self.source = self.secret = self.data = None
            self.records.clear(); self.detail.setPlainText(""); self.f_refs.clear()
            self.chart.show_case(None)
            self._set_enabled(False)
            self.status.setText("Locked. The passphrase was cleared from memory.")

        def add_record(self):
            if not self.data:
                return
            kind = self.kind.currentText()
            refs = [self.f_refs.item(i.row()).data(Qt.ItemDataRole.UserRole)
                    for i in self.f_refs.selectedIndexes()]
            value = {"kind": kind, "title": self.f_title.text(), "body": self.f_body.toPlainText(),
                     "references": refs}
            if kind == "event":
                value["observed_utc"] = self.f_obs.text().strip()
            if self.f_src.text().strip():
                value["provenance"] = [{"source": self.f_src.text().strip(), "note": self.f_note.text().strip()}]
            dest = self._next_revision_path()
            try:
                result = core.append_record(self.source, dest, value, self.secret)
            except (CasebookError, OSError) as exc:
                self.add_status.setText(str(exc)); self.add_status.setStyleSheet(f"#status{{color:{PINK};}}")
                return
            # Move the window onto the new revision; the previous file stays on disk.
            self.source = Path(result["output"])
            self.data, _ = core._load(self.source, self.secret)
            self.f_title.clear(); self.f_body.clear(); self.f_obs.clear()
            self.f_src.clear(); self.f_note.clear(); self.f_refs.clearSelection()
            self.refresh_records(); self._refresh_refs(); self._update_status()
            self.chart.show_case(self.data)
            self.add_status.setText(f"Saved revision {result['revision']} → {Path(result['output']).name}")
            self.add_status.setStyleSheet(f"#status{{color:{CYAN};}}")

        def export_report(self):
            if not self.data:
                return
            fmt_list = ["HTML report", "JSON report"]
            choice, ok = QInputDialog.getItem(self, "Export report", "Format:", fmt_list, 0, False)
            if not ok:
                return
            fmt = "html" if choice.startswith("HTML") else "json"
            dest, _ = QFileDialog.getSaveFileName(self, "Export plaintext report",
                                                  f"case-report.{fmt}", f"{fmt.upper()} (*.{fmt});;All files (*)")
            if not dest:
                return
            try:
                if Path(dest).exists():
                    Path(dest).unlink()
                result = core.export(self.source, dest, self.secret, fmt)
            except (CasebookError, OSError) as exc:
                self._warn(str(exc)); return
            self._warn(f"Exported a plaintext {fmt.upper()} report to {result['output']}.\n\n"
                       f"SHA-256: {result['sha256']}\n\nThis file is not encrypted — store it per your policy.",
                       icon=QMessageBox.Icon.Information, title="Report exported")

        # ----- display ------------------------------------------------------
        def _filter_kind(self):
            return {0: None, 1: "event", 2: "entity", 3: "link", 4: "note"}[self.filter.currentIndex()]

        def refresh_records(self):
            self.records.clear()
            if not self.data:
                return
            kind = self._filter_kind()
            rows = [r for r in self.data["records"] if kind is None or r["kind"] == kind]
            if self.filter.currentIndex() == 1:  # timeline: order events by observed time
                rows.sort(key=lambda r: (r.get("observed_utc") or "", r["id"]))
            self.visible = rows
            for r in rows:
                item = QListWidgetItem(f"[{r['kind']}]  {r['title']}")
                item.setData(Qt.ItemDataRole.UserRole, r["id"])
                item.setForeground(QColor(KIND_ACCENT[r["kind"]]))
                self.records.addItem(item)
            if rows:
                self.records.setCurrentRow(0)
            else:
                self.detail.setPlainText("No records of this kind yet.")

        def show_selected(self, row):
            if not self.data or row < 0 or row >= len(self.visible):
                return
            r = self.visible[row]
            lines = [f"{r['kind'].upper()}  ·  {r['title']}", f"id        {r['id']}",
                     f"recorded  {r['recorded_utc']}"]
            if r.get("observed_utc"):
                lines.append(f"observed  {r['observed_utc']}")
            if r["references"]:
                lines.append("refs      " + ", ".join(ref[:8] for ref in r["references"]))
            lines.append("")
            lines.append(r.get("body") or "(no body)")
            if r["provenance"]:
                lines.append("")
                lines.append("PROVENANCE")
                for item in r["provenance"]:
                    lines.append(f"  · {item['source']}" + (f" — {item['note']}" if item.get("note") else ""))
                    if item.get("sha256"):
                        lines.append(f"    sha256 {item['sha256']}")
            lines.append("")
            lines.append(f"record sha256  {r['record_sha256']}")
            self.detail.setPlainText("\n".join(lines))

        def _refresh_refs(self):
            self.f_refs.clear()
            if not self.data:
                return
            for r in self.data["records"]:
                item = QListWidgetItem(f"[{r['kind']}] {r['title']}  ({r['id'][:8]})")
                item.setData(Qt.ItemDataRole.UserRole, r["id"])
                self.f_refs.addItem(item)

        def _update_status(self):
            if not self.data:
                return
            d = self.data
            self.status.setText(
                f"<b style='color:{CYAN}'>{d['title']}</b> &nbsp;·&nbsp; revision <b>{d['revision']}</b> "
                f"&nbsp;·&nbsp; {len(d['records'])} record(s) &nbsp;·&nbsp; "
                f"<span style='color:{DIM}'>{self.source.name}</span>")

        def _tab_changed(self, index):
            if index == 1 and self.data:
                self.chart.show_case(self.data)

        def _next_revision_path(self):
            base = self.source
            stem = base.stem
            for suffix in (".r", "-r", ".rev"):
                if stem.rsplit(suffix, 1)[0] != stem and stem.rsplit(suffix, 1)[-1].isdigit():
                    stem = stem.rsplit(suffix, 1)[0]
                    break
            rev = (self.data["revision"] + 1) if self.data else 1
            candidate = base.with_name(f"{stem}.r{rev}{base.suffix}")
            n = rev
            while candidate.exists():
                n += 1
                candidate = base.with_name(f"{stem}.r{n}{base.suffix}")
            return candidate

        def _set_enabled(self, on):
            for w in (self.filter, self.records, self.tabs):
                w.setEnabled(on)

        def _warn(self, message, icon=None, title="Casebook"):
            box = QMessageBox(self); box.setWindowTitle(title)
            box.setIcon(icon or QMessageBox.Icon.Warning); box.setText(message); box.exec()

        def _animate(self):
            self.phase += 0.05
            self.header.tick(self.phase)

    return QApplication, Window


STYLE = f"""
#body {{ background: {BG}; }}
QWidget {{ color: {INK}; font-family: Helvetica, Arial, sans-serif; font-size: 13px; }}
#tag {{ color: {MUTED}; font-weight: bold; font-size: 11px; letter-spacing: 1px; }}
#status {{ color: {MUTED}; font-size: 12px; }}
#foot {{ color: {DIM}; font-size: 11px; }}
#mono {{ background: {PANEL_HI}; border: 1px solid {LINE}; border-radius: 9px; color: {INK};
         font-family: "Courier New", monospace; font-size: 13px; padding: 8px; }}
#mono:focus, #field:focus {{ border: 1px solid {CYAN}; }}
#field {{ background: {PANEL_HI}; border: 1px solid {LINE}; border-radius: 8px; color: {INK}; padding: 7px 9px; }}
#records {{ background: {PANEL_HI}; border: 1px solid {LINE}; border-radius: 9px; padding: 4px; }}
#records::item {{ padding: 5px 6px; border-radius: 6px; }}
#records::item:selected {{ background: {VIOLET}; color: {INK}; }}
QComboBox {{ background: {PANEL_HI}; border: 1px solid {LINE}; border-radius: 8px; color: {INK}; padding: 6px 10px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {INK}; selection-background-color: {VIOLET}; }}
QSplitter#split::handle {{ background: {LINE}; width: 2px; }}
QTabWidget#tabs::pane {{ border: 1px solid {LINE}; border-radius: 12px; background: {PANEL}; }}
QTabBar::tab {{ background: {PANEL_HI}; color: {MUTED}; padding: 8px 20px; margin-right: 6px;
                border-top-left-radius: 9px; border-top-right-radius: 9px; font-weight: bold; }}
QTabBar::tab:selected {{ color: {INK}; border-bottom: 2px solid {PINK}; }}
QPushButton#go {{ background: {PINK}; color: {BG}; border: none; border-radius: 10px; font-weight: bold; padding: 9px 18px; }}
QPushButton#go:hover {{ background: {CYAN}; }}
QPushButton#pick {{ background: {PANEL_HI}; border: 1px solid {LAVENDER}; border-radius: 9px; color: {INK};
                    font-weight: bold; padding: 9px 16px; }}
QPushButton#pick:hover {{ background: #1b2150; }}
QGraphicsView {{ border: 1px solid {LINE}; border-radius: 12px; }}
"""


def run_app() -> int:
    try:
        from PyQt6.QtWidgets import QApplication  # noqa: F401
    except Exception:
        print("PyQt6 is required for the Casebook window. Use 'envy-casebook' subcommands in a terminal instead.")
        return 2
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("No display found. Use 'envy-casebook new/add/inspect/export' in a terminal instead.")
        return 2
    try:
        QApplication, Window = _build()
        app = QApplication.instance() or QApplication(sys.argv)
        window = Window(); window.show()
        return app.exec()
    except Exception as exc:  # pragma: no cover
        print(f"Could not open the window: {exc}")
        return 2
