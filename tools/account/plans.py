"""Plans - the website's app/pricing/PricingView.tsx: the free account and the paid plans, live
from Supabase (edits in the admin panel show up at once), and the plan request an admin activates
once the payment is confirmed (components/RequestPlanButton.tsx)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from core import account as acc
from core import net, supabase
from core import webui as W

INTERVAL = {"month": "/ month", "year": "/ year", "lifetime": "one-time"}
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "BDT": "৳", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "CA$"}


def format_price(cents: int, currency: str) -> str:
    amount = cents / 100
    text = f"{amount:,.2f}" if cents % 100 else f"{amount:,.0f}"
    sym = SYMBOLS.get((currency or "").upper())
    return f"{sym}{text}" if sym else f"{amount:.2f} {currency}"


def _feature(text: str) -> QHBoxLayout:
    row = W.h_row(W.Icon("check", W.PRIMARY, 16), W.label(text, 14, 400, W.FG, wrap=True), spacing=10, stretch_at=1)
    row.setAlignment(Qt.AlignTop)
    return row


class RequestPlanDialog(W.WebDialog):
    def __init__(self, parent, plan: dict, price: str):
        super().__init__(parent, 448)
        self.plan = plan
        self.header(f"Request {plan['name']}", f"{price}. Our team activates your plan as soon as your payment is "
                    "confirmed - you'll see it on your dashboard.")
        self.note = W.TextArea("e.g. transaction ID", 3)
        self.body.addLayout(W.v_col(W.label("Payment reference or message (optional)", 14, 600), self.note, spacing=8))
        cancel = W.Button("Cancel", None, "outline")
        cancel.clicked.connect(self.reject)
        self.send = W.Button("Send request")
        self.send.clicked.connect(self.submit)
        self.add_footer([cancel, self.send])

    def submit(self):
        note = self.note.toPlainText().strip()[:500] or None
        self.send.set_loading(True)
        self.send.setEnabled(False)

        def done(_):
            W.toast.success("Request sent - we'll activate your plan once payment is confirmed.")
            self.accept()

        def failed(e):
            self.send.set_loading(False)
            self.send.setEnabled(True)
            W.toast.error(supabase.db_message(e, "Could not send your request"))
        net.run_bg(lambda: acc.get().rpc("request_my_subscription", {"p_plan_id": self.plan["id"], "p_note": note}),
                   done, failed)


class PlansPage(W.WebPage):
    def __init__(self):
        super().__init__()
        self.account = acc.get()
        self.plans: list[dict] = []
        self.subs: list[dict] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll)
        page = W.WebPage()
        scroll.setWidget(page)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 56, 24, 64)
        lay.setSpacing(0)
        lay.addWidget(W.label("Simple pricing", 44, 700, align=Qt.AlignCenter))
        lay.addSpacing(12)
        self.subtitle = W.label("", 17, 400, W.MUTED_FG, wrap=True, align=Qt.AlignCenter)
        lay.addWidget(self.subtitle)
        lay.addSpacing(48)
        self.grid = W.ResponsiveGrid(240, 4, 20)
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(self.grid, 1000)
        holder.addStretch(1)
        self.grid.setMaximumWidth(1152)
        lay.addLayout(holder)
        lay.addSpacing(40)
        self.note = W.label("", 12, 400, W.MUTED_FG, wrap=True, align=Qt.AlignCenter)
        lay.addWidget(self.note)
        lay.addStretch(1)
        self.account.live.connect(lambda _: self.load())
        self.account.changed.connect(self._render)
        self._render()
        self.load()

    def page_shown(self):
        self.load()

    def load(self):
        if not self.account.user or self.account.offline:
            return
        a = self.account

        def work():
            plans = a.select("plans", "select=id,name,description,price_cents,currency,billing_interval,daily_limit,"
                             "features&is_active=eq.true&order=sort_order")
            subs = a.select("subscriptions", "select=plan_id,status,expires_at&status=in.(active,pending)")
            return plans, subs

        def done(res):
            self.plans, self.subs = res
            self._render()
        net.run_bg(work, done, lambda e: None)

    def _render(self):
        limits = self.account.limits
        user_limit = limits.get("user")
        self.subtitle.setText("Every Boostimise account is free with Google sign-in. Enhance "
                              + ("without a daily limit." if user_limit is None else "more every day with a paid plan."))
        now = datetime.now().astimezone()

        def active(s):
            exp = s.get("expires_at")
            try:
                ok = not exp or datetime.fromisoformat(exp.replace("Z", "+00:00")) > now
            except ValueError:
                ok = True
            return s.get("status") == "active" and ok
        active_plan = next((s["plan_id"] for s in self.subs if active(s)), None)
        pending_plan = next((s["plan_id"] for s in self.subs if s.get("status") == "pending"), None)

        self.grid.clear()
        cards = 1 + len(self.plans)
        self.grid.max_cols = min(4, cards)
        self.grid.setMaximumWidth({1: 400, 2: 800, 3: 1152}.get(cards, 1152))

        # free account
        free = self._card(highlight=not self.plans, badge="Recommended" if not self.plans else None)
        free.add(W.Icon("user", W.PRIMARY, 20))
        self._head(free, "Free account", "Sign in with Google", "Free")
        for f in ("Unlimited photos every day" if user_limit is None else f"{user_limit} photos per day",
                  "AI Upscale 2× / 4×", "Face & photo restoration", "Daily, weekly & monthly stats", "Enhancement history"):
            free.add(_feature(f))
        free.lay.addStretch(1)
        free.lay.addSpacing(24)
        btn = W.Button("Included" if active_plan else "Current plan", None, "secondary", full=True)
        btn.setEnabled(False)
        free.add(btn)
        self.grid.add(free.holder)

        # paid plans (managed in the admin panel)
        for i, p in enumerate(self.plans):
            price = format_price(p.get("price_cents", 0), p.get("currency", "USD"))
            card = self._card(highlight=i == 0, badge="Most popular" if i == 0 else None)
            card.add(W.Icon("crown", W.PRIMARY, 20))
            self._head(card, p["name"], p.get("description") or "", price, INTERVAL.get(p.get("billing_interval"), ""))
            card.add(_feature("Unlimited photos every day" if p.get("daily_limit") is None
                              else f"{p['daily_limit']} photos per day"))
            for f in p.get("features") or []:
                card.add(_feature(str(f)))
            card.lay.addStretch(1)
            card.lay.addSpacing(24)
            state = ("current" if active_plan == p["id"] else "pending" if pending_plan == p["id"]
                     else "other-pending" if pending_plan else "available")
            if state == "current":
                b = W.Button("Current plan", None, "secondary", full=True)
                b.setEnabled(False)
            elif state == "pending":
                b = W.Button("Requested - awaiting approval", None, "secondary", full=True)
                b.setEnabled(False)
            else:
                b = W.Button(f"Get {p['name']}", None, "default", full=True)
                b.setEnabled(state == "available")
                full_price = f"{price} {INTERVAL.get(p.get('billing_interval'), '')}"
                b.clicked.connect(lambda _=False, plan=p, fp=full_price: self._request(plan, fp))
            card.add(b)
            self.grid.add(card.holder)

        self.note.setText(f"Daily enhancements reset every day at midnight ({limits.get('timezone') or 'UTC'})."
                          + (" Paid plans are activated by our team once your payment is confirmed." if self.plans else ""))

    def _card(self, highlight: bool, badge: str | None) -> W.Box:
        """A plan card; ``card.holder`` goes in the grid, with the badge sitting on the card's top
        border (-top-2.5 left-6) so every card's content lines up."""
        holder = QWidget()
        holder.setStyleSheet("background: transparent;")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 10, 0, 0)
        card = W.Box(radius=18, bg=W.CARD, border=W.tint(W.PRIMARY, .5) if highlight else W.BORDER, padding=24, spacing=0)
        hl.addWidget(card)
        if badge:
            b = W.Badge(badge, "default", parent=holder)
            b.adjustSize()
            b.move(24, 0)
            b.raise_()
        card.holder = holder
        return card

    @staticmethod
    def _head(card: W.Box, title: str, sub: str, price: str, interval: str = ""):
        card.lay.addSpacing(16)
        card.add(W.label(title, 16, 700))
        card.lay.addSpacing(4)
        card.add(W.label(sub, 14, 400, W.MUTED_FG, wrap=True))
        card.lay.addSpacing(20)
        row = W.h_row(W.label(price, 36, 700), spacing=6)
        if interval:
            row.addWidget(W.label(interval, 14, 400, W.MUTED_FG), 0, Qt.AlignBottom)
        row.addStretch(1)
        card.add(row)
        card.lay.addSpacing(24)
        card.lay.setSpacing(10)

    def _request(self, plan: dict, price: str):
        if RequestPlanDialog(self, plan, price).exec():
            self.load()

