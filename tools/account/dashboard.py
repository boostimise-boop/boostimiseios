"""Dashboard - the website's app/dashboard/DashboardView.tsx: totals for today / this week / this
month, photos enhanced per day, the account and plan, and the latest enhancements. Everything is
read from Supabase (RLS: only this user's rows) and reloads by itself on every Realtime change.
"""
from __future__ import annotations

import math
from datetime import date, datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QBoxLayout, QGridLayout, QHBoxLayout, QScrollArea, QToolTip, QVBoxLayout, QWidget,
)

from core import account as acc
from core import net
from core import webui as W
from core.account_widgets import EditPhoneDialog

MODE_LABEL = {"auto": "Auto", "face": "Face Enhance", "restore": "Photo Restore", "upscale": "Upscale"}


def _dt(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()
    except (ValueError, AttributeError):
        return None


def fmt_date(iso: str) -> str:
    d = _dt(iso)
    return f"{d:%b} {d.day}, {d:%I:%M %p}".replace(" 0", " ") if d else "-"


def day_label(iso_day: str) -> str:
    try:
        d = date.fromisoformat(iso_day[:10])
    except ValueError:
        return iso_day
    return f"{d:%b} {d.day}"


def status_badge(status: str) -> W.Badge:
    if status == "completed":
        return W.Badge("Enhanced", "secondary", "circle-check", fg=W.EMERALD_700)
    if status == "reserved":
        return W.Badge("Processing", "secondary", "clock")
    return W.Badge("Failed" if status == "failed" else "Cancelled", "secondary", "circle-x", fg=W.MUTED_FG)


def card(title: str | None = None, description: str | None = None, action: QWidget | None = None,
         size: str = "default") -> tuple[W.Box, QVBoxLayout]:
    """shadcn <Card> with an optional header (title, description, action at the right)."""
    pad = 12 if size == "sm" else 16
    box = W.Card(padding=pad, spacing=pad)
    if title:
        head = QVBoxLayout()
        head.setSpacing(4)
        head.addWidget(W.label(title, 16, 600))
        if description is not None:
            box.description = W.label(description, 14, 400, W.MUTED_FG)
            head.addWidget(box.description)
        row = QHBoxLayout()
        row.addLayout(head, 1)
        if action is not None:
            row.addWidget(action, 0, Qt.AlignTop)
        box.add(row)
    content = QVBoxLayout()
    content.setSpacing(12)
    box.add(content)
    box.lay.addStretch(1)  # extra height (a taller neighbour) goes below the content
    return box, content


class UsageChart(QWidget):
    """Completed enhancements per day (one series, so the card title names it - no legend).
    Hover a column for its date and count."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: list[dict] = []
        self.hover = -1
        self.setMouseTracking(True)
        self.setFixedHeight(224)

    def set_data(self, data: list[dict]):
        self.data = data or []
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(40, 8, max(10, self.width() - 44), self.height() - 8 - 28)

    def _ticks(self) -> list[int]:
        top = max([d.get("count", 0) for d in self.data] + [1])
        step = max(1, math.ceil(top / 4))
        nice = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 10 ** 9) if s >= step)
        return list(range(0, (math.ceil(top / nice)) * nice + 1, nice))

    def _slot(self, i: int, plot: QRectF) -> QRectF:
        n = max(1, len(self.data))
        w = plot.width() / n
        return QRectF(plot.x() + i * w, plot.y(), w, plot.height())

    def mouseMoveEvent(self, e):
        plot = self._plot()
        n = len(self.data)
        x = e.position().x()
        i = int((x - plot.x()) / (plot.width() / n)) if n and plot.left() <= x <= plot.right() else -1
        if i != self.hover:
            self.hover = i
            self.update()
        if 0 <= i < n:
            d = self.data[i]
            QToolTip.showText(e.globalPosition().toPoint(),
                              f"<b>{day_label(d['day'])}</b><br>Photos enhanced&nbsp;&nbsp;<b>{d.get('count', 0)}</b>", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        plot = self._plot()
        ticks = self._ticks()
        top = ticks[-1] or 1
        p.setFont(W.font(12))
        # grid + y axis (recessive)
        for t in ticks:
            y = plot.bottom() - plot.height() * t / top
            pen = QPen(QColor(W.tint(W.BORDER, .5, W.BG) if t else W.BORDER), 1)
            if t:  # the site's dashed grid (CartesianGrid strokeDasharray 3 3)
                pen.setDashPattern([3, 3])
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), round(y) + .5), QPointF(plot.right(), round(y) + .5))
            p.setPen(QColor(W.MUTED_FG))
            p.drawText(QRectF(0, y - 8, 32, 16), Qt.AlignRight | Qt.AlignVCenter, str(t))
        n = len(self.data)
        if not n:
            p.end()
            return
        slot_w = plot.width() / n
        bar_w = max(2.0, min(28.0, slot_w - 2))  # a 2 px surface gap between neighbours
        for i, d in enumerate(self.data):
            s = self._slot(i, plot)
            if i == self.hover:
                c = QColor(W.MUTED)
                c.setAlphaF(.5)
                p.fillRect(s, c)
            v = d.get("count", 0)
            if v <= 0:
                continue
            h = plot.height() * v / top
            r = QRectF(s.center().x() - bar_w / 2, plot.bottom() - h, bar_w, h)
            path = QPainterPath()
            rad = min(4.0, bar_w / 2, h)
            path.moveTo(r.left(), r.bottom())
            path.lineTo(r.left(), r.top() + rad)
            path.quadTo(r.left(), r.top(), r.left() + rad, r.top())
            path.lineTo(r.right() - rad, r.top())
            path.quadTo(r.right(), r.top(), r.right(), r.top() + rad)
            path.lineTo(r.right(), r.bottom())
            path.closeSubpath()
            p.fillPath(path, QColor(W.mix(W.BLUE, "#ffffff", .15) if i == self.hover else W.BLUE))
        # x labels, spaced so they never collide (minTickGap 24)
        p.setPen(QColor(W.MUTED_FG))
        fm = p.fontMetrics()
        label_w = fm.horizontalAdvance("Sep 30") + 24
        every = max(1, math.ceil(label_w / slot_w))
        for i in range(0, n, every):
            s = self._slot(i, plot)
            p.drawText(QRectF(s.center().x() - label_w / 2, plot.bottom() + 8, label_w, 18), Qt.AlignCenter,
                       day_label(self.data[i]["day"]))
        p.end()


class DashboardPage(W.WebPage):
    def __init__(self):
        super().__init__()
        self.account = acc.get()
        self.data: dict | None = None
        self._loading = False
        self._again = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet(f"QScrollArea {{ background: {W.BG}; }}")
        outer.addWidget(scroll)
        page = QWidget()
        page.setStyleSheet(f"background: {W.BG};")
        scroll.setWidget(page)
        row = QHBoxLayout(page)
        row.setContentsMargins(24, 32, 24, 32)
        self.main = QWidget()
        self.main.setMaximumWidth(1152)
        row.addStretch(1)
        row.addWidget(self.main, 1000)
        row.addStretch(1)
        lay = QVBoxLayout(self.main)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(24)

        # greeting
        self.avatar = W.Avatar(56)
        self.hello = W.label("Hi", 24, 700)
        self.badges = QHBoxLayout()
        self.badges.setSpacing(8)
        greet = W.v_col(self.hello, self.badges, spacing=6)
        go = W.Button("Enhance photos", "sparkles", "brand", "lg", height=40)
        go.clicked.connect(lambda: self._open("photo_enhancer"))
        lay.addLayout(W.h_row(self.avatar, greet, None, go, spacing=16))

        # tiles
        self.tiles = W.ResponsiveGrid(200, 4, 16, breakpoints=(2, 2, 4))
        self.tile_widgets = []
        for label, icon in (("Total enhanced", "image"), ("Today", "sun"), ("This week", "calendar-range"),
                            ("This month", "calendar-days")):
            box, content = card(size="sm")
            value = W.label("—", 30, 700)
            hint = W.label("", 12, 400, W.MUTED_FG)
            content.addLayout(W.h_row(W.label(label, 14, 600, W.MUTED_FG), None, W.Icon(icon, W.MUTED_FG, 16)))
            content.addWidget(value)
            content.addWidget(hint)
            self.tiles.add(box)
            self.tile_widgets.append((value, hint))
        lay.addWidget(self.tiles)

        # chart + account / plan
        self.split = QBoxLayout(QBoxLayout.LeftToRight)
        self.split.setSpacing(16)
        chart_card, chart_content = card("Photos enhanced per day", "Last 30 days")
        self.chart_card = chart_card
        self.chart = UsageChart()
        self.chart_empty = W.Box(radius=10, bg=W.BG, border=W.BORDER, padding=16, dashed=True, spacing=8)
        self.chart_empty.setFixedHeight(224)
        self.chart_empty.lay.addStretch(1)
        self.chart_empty.add(W.Icon("image", W.MUTED_FG, 24), 0, Qt.AlignHCenter)
        self.chart_empty.add(W.label("No enhanced photos yet.", 14, 400, W.MUTED_FG, align=Qt.AlignCenter))
        first = W.Button("Enhance your first photo", None, "link", "sm")
        first.clicked.connect(lambda: self._open("photo_enhancer"))
        self.chart_empty.add(first, 0, Qt.AlignHCenter)
        self.chart_empty.lay.addStretch(1)
        chart_content.addWidget(self.chart)
        chart_content.addWidget(self.chart_empty)
        self.split.addWidget(chart_card, 2)

        side = QVBoxLayout()
        side.setSpacing(16)
        acc_card, acc_content = card("Account")
        self.email = W.label("", 14)
        self.phone = W.label("—", 14, 400, mono=True)
        edit = W.Button("", "pencil", "ghost", "icon-sm")
        edit.setToolTip("Change mobile number")
        edit.clicked.connect(self._edit_phone)
        self.since = W.label("", 14, 400, W.MUTED_FG)
        acc_content.addLayout(W.h_row(W.Icon("mail", W.MUTED_FG, 16), self.email, None, spacing=10))
        acc_content.addLayout(W.h_row(W.Icon("phone", W.MUTED_FG, 16), self.phone, None, edit, spacing=10))
        self.since_row = W.wrap(W.h_row(W.Icon("calendar-days", W.MUTED_FG, 16), self.since, None, spacing=10))
        acc_content.addWidget(self.since_row)
        side.addWidget(acc_card)
        self.plan_btn = W.Button("View plans", None, "outline", "sm")
        self.plan_btn.clicked.connect(lambda: self._open("plans"))
        plan_card, plan_content = card("Plan", action=self.plan_btn)
        self.plan_name = W.label("Free account", 14, 600)
        self.plan_text = W.label("", 14, 400, W.MUTED_FG, wrap=True)
        plan_content.addWidget(self.plan_name)
        plan_content.addWidget(self.plan_text)
        self.pending = W.Box(radius=10, bg=W.tint(W.AMBER, .1), border=W.tint(W.AMBER, .3), padding=(12, 6, 6, 6),
                             layout="h", spacing=8)
        self.pending_text = W.label("", 12, 400, W.FG, wrap=True)
        withdraw = W.Button("Withdraw", None, "ghost", "sm")
        withdraw.clicked.connect(self._withdraw)
        self.pending.add(W.Icon("clock", W.FG, 14))
        self.pending.add(self.pending_text, 1)
        self.pending.add(withdraw)
        self.pending.hide()
        plan_content.addWidget(self.pending)
        side.addWidget(plan_card)
        side.addStretch(1)
        self.split.addLayout(side, 1)
        lay.addLayout(self.split)

        # history
        hist, self.hist_content = card("Recent enhancements", "")
        self.hist_card = hist
        self.table = QGridLayout()
        self.table.setHorizontalSpacing(16)
        self.table.setVerticalSpacing(0)
        self.hist_content.addLayout(self.table)
        self.nothing = W.label("Nothing here yet.", 14, 400, W.MUTED_FG, align=Qt.AlignCenter)
        self.nothing.setMinimumHeight(64)
        self.hist_content.addWidget(self.nothing)
        lay.addWidget(hist)
        lay.addStretch(1)

        self.account.live.connect(lambda _: self.load())
        self.account.changed.connect(self._render_header)
        self._render_header()
        self.load()

    # ---- data --------------------------------------------------------------------------
    def page_shown(self):
        self.load()

    def load(self):
        uid = (self.account.user or {}).get("id")
        if not uid or self.account.offline:
            return
        if self._loading:
            self._again = True
            return
        self._loading = True
        a = self.account

        def work():
            return {
                "stats": a.rpc("my_enhancement_stats", {"p_days": 30}) or {},
                "recent": a.select("enhancements", "select=id,file_name,mode,scale,input_width,input_height,output_width,"
                                   "output_height,status,processing_seconds,created_at&order=created_at.desc&limit=12"),
                "subs": a.select("subscriptions", "select=id,plan_id,status,expires_at,plans(name)"
                                 "&order=created_at.desc&limit=5"),
                "profile": next(iter(a.select("profiles", f"select=email,phone_e164,phone_country,created_at&id=eq.{uid}")), None),
            }

        def done(data):
            self._loading = False
            self.data = data
            self._render()
            if self._again:
                self._again = False
                self.load()

        def failed(e):
            self._loading = False
            if not getattr(e, "status", 1) == 0:
                W.toast.error(f"Dashboard: {getattr(e, 'message', e)}")
        net.run_bg(work, done, failed)

    # ---- render ------------------------------------------------------------------------
    def _render_header(self):
        a = self.account
        u = a.display_user() or {}
        name = u.get("name") or "there"
        self.avatar.set_user(name, u.get("email"), a.avatar_image)
        self.hello.setText(f"Hi, {name.split(' ')[0]}")
        W.clear_layout(self.badges)
        active = self._active_sub()
        if active:
            self.badges.addWidget(W.Badge((active.get("plans") or {}).get("name") or active["plan_id"], "default", "crown"))
        elif a.plan:
            self.badges.addWidget(W.Badge(a.plan["name"], "default", "crown"))
        else:
            self.badges.addWidget(W.Badge("Free account", "secondary"))
        if self._daily_limit() is None:
            self.badges.addWidget(W.Badge("Unlimited daily enhancements", "outline", "infinity"))
        live = a.realtime_state == "subscribed" and not a.offline
        self.badges.addWidget(W.Badge("Live" if live else "Offline", "outline", "radio" if live else "wifi-off",
                                      fg=W.EMERALD_700 if live else W.MUTED_FG))
        self.badges.addStretch(1)
        if self.data:
            self._render_tiles()

    def _active_sub(self) -> dict | None:
        now = datetime.now().astimezone()
        for s in (self.data or {}).get("subs") or []:
            exp = _dt(s.get("expires_at") or "")
            if s.get("status") == "active" and (not s.get("expires_at") or (exp and exp > now)):
                return s
        return None

    def _daily_limit(self):
        q = self.account.quota
        return q.limit if q else self.account.limits.get("user")

    def _render_tiles(self):
        stats = self.data.get("stats") or {}
        q = self.account.quota
        limit = self._daily_limit()
        today_hint = "No daily limit" if limit is None else f"{q.remaining if q else limit} of {limit} left"
        values = [(stats.get("total", 0), "All time"), (stats.get("today", 0), today_hint),
                  (stats.get("week", 0), "Since Monday"), (stats.get("month", 0), date.today().strftime("%B"))]
        for (value_lab, hint_lab), (v, hint) in zip(self.tile_widgets, values):
            value_lab.setText(f"{v:,}")
            hint_lab.setText(hint)

    def _render(self):
        d = self.data or {}
        stats = d.get("stats") or {}
        self._render_header()
        daily = stats.get("daily") or []
        best = max([x.get("count", 0) for x in daily] + [0])
        self.chart_card.description.setText("Last 30 days" + (f" · best day {best}" if best else ""))
        empty = not stats.get("total")
        self.chart.setVisible(not empty)
        self.chart_empty.setVisible(empty)
        self.chart.set_data(daily)

        prof = d.get("profile") or {}
        u = self.account.display_user() or {}
        self.email.setText(prof.get("email") or u.get("email") or "")
        self.phone.setText(prof.get("phone_e164") or "—")
        created = _dt(prof.get("created_at") or "")
        self.since.setText(f"Member since {created:%B %Y}" if created else "")
        self.since_row.setVisible(bool(created))

        active = self._active_sub()
        pending = next((s for s in d.get("subs") or [] if s.get("status") == "pending"), None)
        limit = self._daily_limit()
        self.plan_name.setText(((active.get("plans") or {}).get("name") or active["plan_id"]) if active else "Free account")
        until = ""
        if active and active.get("expires_at"):
            exp = _dt(active["expires_at"])
            until = f" Active until {exp:%m/%d/%Y}." if exp else ""
        self.plan_text.setText(("Unlimited enhancements every day." if limit is None else f"{limit} enhancements per day.") + until)
        self.plan_btn.setText("Change" if active else "View plans")
        self.pending.setVisible(bool(pending))
        if pending:
            self._pending_id = pending["id"]
            self.pending_text.setText(f"{(pending.get('plans') or {}).get('name') or pending['plan_id']} requested - "
                                      "waiting for approval")

        recent = d.get("recent") or []
        self.hist_card.description.setText(f"Your latest {len(recent)} photos")
        W.clear_layout(self.table)
        self.nothing.setVisible(not recent)
        if recent:
            heads = ["Photo", "Mode", "Size", "Time", "Status", "Date"]
            right = {3, 5}
            for c, h in enumerate(heads):
                lab = W.label(h, 14, 600, align=(Qt.AlignRight if c in right else Qt.AlignLeft) | Qt.AlignVCenter)
                lab.setFixedHeight(40)
                self.table.addWidget(lab, 0, c)
            self.table.addWidget(W.Separator(), 1, 0, 1, len(heads))
            for n, e in enumerate(recent):
                r = 2 + n * 2
                name = W.label(e.get("file_name") or "Photo", 14, 600)
                name.setMaximumWidth(260)
                name.setText(name.fontMetrics().elidedText(e.get("file_name") or "Photo", Qt.ElideRight, 240))
                name.setToolTip(e.get("file_name") or "")
                size = "-"
                if e.get("input_width") and e.get("input_height"):
                    size = f"{e['input_width']}×{e['input_height']}"
                if e.get("output_width") and e.get("output_height"):
                    size += f" → {e['output_width']}×{e['output_height']}"
                secs = e.get("processing_seconds")
                cells = [name, W.label(f"{MODE_LABEL.get(e.get('mode') or '', '-')} · {e.get('scale') or '-'}×", 14),
                         W.label(size, 12, 400, W.MUTED_FG, mono=True),
                         W.label(f"{float(secs):.1f}s" if secs else "-", 12, 400, W.FG, mono=True,
                                 align=Qt.AlignRight | Qt.AlignVCenter),
                         status_badge(e.get("status") or ""),
                         W.label(fmt_date(e.get("created_at") or ""), 14, 400, W.MUTED_FG,
                                 align=Qt.AlignRight | Qt.AlignVCenter)]
                for c, w in enumerate(cells):
                    if not isinstance(w, W.Badge):
                        w.setFixedHeight(44)
                    self.table.addWidget(w, r, c, Qt.AlignVCenter | (Qt.AlignRight if c in right else Qt.AlignLeft))
                if n < len(recent) - 1:
                    self.table.addWidget(W.Separator(), r + 1, 0, 1, len(heads))
            self.table.setColumnStretch(0, 2)
            self.table.setColumnStretch(2, 2)

    def resizeEvent(self, e):
        self.split.setDirection(QBoxLayout.LeftToRight if e.size().width() >= 980 else QBoxLayout.TopToBottom)
        super().resizeEvent(e)

    # ---- actions -----------------------------------------------------------------------
    def _open(self, key: str):
        from core.shell import find_shell
        shell = find_shell(self)
        if shell is not None:
            shell.open_tool(key)

    def _edit_phone(self):
        prof = (self.data or {}).get("profile") or {}
        EditPhoneDialog(self, prof.get("phone_e164"), prof.get("phone_country")).exec()
        self.load()

    def _withdraw(self):
        pid = getattr(self, "_pending_id", None)
        if not pid:
            return

        def done(_):
            W.toast("Request withdrawn")
            self.load()
        net.run_bg(lambda: self.account.rpc("cancel_my_subscription_request", {"p_id": pid}), done,
                   lambda e: W.toast.error("Could not cancel the request"))

