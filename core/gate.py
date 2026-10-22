"""The screens in front of the tools: Boostimise is used signed in with Google.

  SignInPage      app/login/LoginView.tsx (Continue with Google)
  OnboardingPage  app/onboarding/OnboardingView.tsx (the mobile number, once, after the first sign-in)
  BlockedPage     the "Account suspended" message (LimitDialog's blocked variant)
  UpdatePage      this version may not run (core/guard.py): update required, switched off, offline
  LoadingPage     while the saved sign-in is checked

Gate is the stack of them; core/shell.py shows it instead of the tools until the account is ready.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QStackedWidget, QVBoxLayout, QWidget

from . import account as acc
from . import guard as gd
from . import theme
from . import webui as W
from .account_widgets import PhoneForm
from .config import AUTH_REDIRECT, PRIVACY_URL, TERMS_URL


def _centered(page: W.WebPage, column: QWidget, max_width: int) -> W.WebPage:
    """Puts ``column`` in the middle of ``page``; the page scrolls when the window is too short."""
    holder = QWidget()
    holder.setStyleSheet("background: transparent;")
    row = QHBoxLayout(holder)
    row.setContentsMargins(16, 48, 16, 48)
    row.addStretch(1)
    column.setMaximumWidth(max_width)
    column.setMinimumWidth(min(max_width, 300))
    row.addWidget(column, 100)
    row.addStretch(1)
    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    v = QVBoxLayout(inner)
    v.setContentsMargins(0, 0, 0, 0)
    v.addStretch(1)
    v.addWidget(holder)
    v.addStretch(1)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setStyleSheet("QScrollArea { background: transparent; } QScrollArea > QWidget > QWidget { background: transparent; }")
    scroll.viewport().setAutoFillBackground(False)
    scroll.setWidget(inner)
    lay = QVBoxLayout(page)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(scroll)
    return page


def _logo_row() -> QHBoxLayout:
    return W.h_row(None, W.FullLogo(), None)


def _card(padding: int = 32, spacing: int = 20) -> W.Box:
    card = W.Box(radius=18, bg=W.CARD, border=W.BORDER, padding=padding, spacing=spacing)
    W.shadow(card, blur=40, dy=16, alpha=.08, color=W.NAVY)  # shadow-xl shadow-brand-navy/5
    return card


class SignInPage(W.WebPage):
    def __init__(self):
        super().__init__()
        self.account = acc.get()
        col = QWidget()
        col.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(24)
        lay.addLayout(_logo_row())

        card = _card()
        card.add(W.v_col(W.label("Sign in to Boostimise", 20, 700, align=Qt.AlignCenter),
                         W.label("Free account - no password to remember.", 14, 400, W.MUTED_FG, align=Qt.AlignCenter),
                         spacing=6))
        self.error = W.Alert("", "", "destructive", "circle-alert")
        self.error.hide()
        card.add(self.error)
        self.google = W.Button("Continue with Google", "google", "outline", "xl", full=True, weight=600)
        self.google.setIconSize(self.google.iconSize())
        self.google.clicked.connect(self._sign_in)
        card.add(self.google)

        self.waiting = QWidget()
        self.waiting.setStyleSheet("background: transparent;")
        wl = QVBoxLayout(self.waiting)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        self.waiting_text = W.label("Finish signing in in your browser…", 13, 400, W.MUTED_FG, wrap=True,
                                    align=Qt.AlignCenter)
        cancel = W.Button("Cancel", None, "link", "sm")
        cancel.clicked.connect(lambda: self.account.cancel_sign_in(""))
        wl.addWidget(self.waiting_text)
        wl.addLayout(W.h_row(None, cancel, None))
        self.waiting.hide()
        card.add(self.waiting)

        terms = W.label(f'By continuing, you agree to our <a href="{TERMS_URL}" style="color:{W.MUTED_FG};">Terms</a> '
                        f'and <a href="{PRIVACY_URL}" style="color:{W.MUTED_FG};">Privacy Policy</a>.',
                        12, 400, W.MUTED_FG, wrap=True, align=Qt.AlignCenter)
        terms.setOpenExternalLinks(True)
        card.add(terms)

        feats = QWidget()
        feats.setStyleSheet(f"background: transparent; border-top: 1px solid {W.BORDER};")
        fl = QVBoxLayout(feats)
        fl.setContentsMargins(0, 20, 0, 0)
        fl.setSpacing(10)
        self.limit_line = W.label("", 14, 400, W.MUTED_FG)
        for icon, widget in (("infinity", self.limit_line),
                             ("chart-column", W.label("Your daily, weekly & monthly stats", 14, 400, W.MUTED_FG)),
                             ("shield-check", W.label("Secure sign-in with your Google account", 14, 400, W.MUTED_FG))):
            row = W.h_row(W.Icon(icon, W.PRIMARY, 16), widget, None, spacing=10)
            w = W.wrap(row)
            w.setStyleSheet("background: transparent; border: none;")
            fl.addWidget(w)
        card.add(feats)
        lay.addWidget(card)
        lay.addWidget(W.label("One Google sign-in unlocks every Boostimise tool: the AI Photo Enhancer, "
                              "the WhatsApp tools and the photo tools.", 13, 400, W.MUTED_FG, wrap=True,
                              align=Qt.AlignCenter))
        _centered(self, col, 384)

        self.account.sign_in_state.connect(self._on_state)
        self.account.changed.connect(self._update_limits)
        self.account.signed_out.connect(lambda reason: self._show_error(reason))
        self._update_limits()

    def _update_limits(self):
        user = self.account.limits.get("user")
        self.limit_line.setText("Unlimited enhancements every day" if user is None else f"{user} enhancements every day")

    def _sign_in(self):
        self._show_error("")
        self.account.sign_in()

    def _show_error(self, msg: str):
        self.error.title.setText(msg)
        self.error.setVisible(bool(msg))

    def _on_state(self, state: str, message: str):
        waiting = state in ("waiting", "working")
        self.google.set_loading(waiting)
        self.google.setEnabled(not waiting)
        self.waiting.setVisible(waiting)
        self.waiting_text.setText("Signing you in…" if state == "working" else
                                  "Finish signing in in your browser…")
        if state == "error":
            if "timed out" in message.lower():
                message += (f"\n\nIf the browser didn't come back to Boostimise, {AUTH_REDIRECT} has to be added to "
                            "the Supabase project's Redirect URLs (Authentication → URL Configuration).")
            self._show_error(message)
        elif state == "idle":
            self._show_error("")


class OnboardingPage(W.WebPage):
    """Step 2 of 2: the mobile number, required once after the first sign-in."""

    def __init__(self):
        super().__init__()
        self.account = acc.get()
        col = QWidget()
        col.setStyleSheet("background: transparent;")
        self.lay = QVBoxLayout(col)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(24)
        self.lay.addLayout(_logo_row())
        self.card = _card(32, 24)
        self.welcome = W.label("", 14, 400, W.MUTED_FG, wrap=True)
        self.card.add(W.v_col(W.label("STEP 2 OF 2", 12, 700, W.PRIMARY, upper_spacing=1),
                              W.label("Add your mobile number", 20, 700), self.welcome, spacing=6))
        self.form_holder = QVBoxLayout()
        self.card.add(self.form_holder)
        self.lay.addWidget(self.card)
        self.email = W.label("", 12, 400, W.MUTED_FG, align=Qt.AlignCenter)
        self.lay.addWidget(self.email)
        other = W.Button("Use a different account", "log-out", "ghost-muted", "sm")
        other.clicked.connect(self.account.sign_out)
        self.lay.addLayout(W.h_row(None, other, None))
        _centered(self, col, 448)
        self.form: PhoneForm | None = None

    def prepare(self):
        """(Re)builds the form for the signed-in user each time the page is shown."""
        u = self.account.display_user() or {}
        first = self.account.first_name()
        self.welcome.setText(f"Welcome{', ' + first if first else ''}! Your mobile number finishes your account "
                             "- you only do this once.")
        self.email.setText(f"Signed in as {u.get('email') or ''}")
        if self.form is not None:
            self.form.hide()
            self.form.deleteLater()
        st = self.account.status
        self.form = PhoneForm(st.country if st else None, None, u.get("name"), ask_name=True, submit_label="Finish")
        self.form.saved.connect(self._saved)
        self.form_holder.addWidget(self.form)

    def _saved(self, _phone: str):
        W.toast.success("Welcome to Boostimise!")
        self.account.refresh()


class BlockedPage(W.WebPage):
    def __init__(self):
        super().__init__()
        self.account = acc.get()
        col = QWidget()
        col.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(24)
        lay.addLayout(_logo_row())
        card = _card(32, 16)
        tile = W.Icon("ban", W.DESTRUCTIVE, 20)
        tile.setFixedSize(44, 44)
        tile.setAlignment(Qt.AlignCenter)
        tile.setStyleSheet(f"background: {W.tint(W.DESTRUCTIVE, .1)}; border-radius: 14px;")
        card.add(tile)
        card.add(W.label("Account suspended", 18, 700))
        card.add(W.label("Your account can't use Boostimise right now. Please contact support if you think this "
                         "is a mistake.", 14, 400, W.MUTED_FG, wrap=True))
        again = W.Button("Check again", "refresh-cw", "outline")
        again.clicked.connect(self.account.refresh)
        out = W.Button("Sign out", "log-out")
        out.clicked.connect(self.account.sign_out)
        card.add(W.h_row(None, again, out))
        lay.addWidget(card)
        _centered(self, col, 448)


# reason -> icon, colour, title (core/guard.py, desktop_verdict in schema.sql)
_LOCKS = {
    "update_required": ("download", W.PRIMARY, "Update required"),
    "version_blocked": ("download", W.PRIMARY, "Please update Boostimise"),
    "disabled": ("ban", W.DESTRUCTIVE, "Boostimise is switched off"),
    "device_blocked": ("ban", W.DESTRUCTIVE, "This computer is blocked"),
    "offline": ("wifi-off", W.AMBER, "You're offline"),
}


class UpdatePage(W.WebPage):
    """This copy may not run right now; shows why, the new version and how to get it."""

    def __init__(self):
        super().__init__()
        self.guard = gd.get()
        self.url: str | None = None
        col = QWidget()
        col.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(24)
        lay.addLayout(_logo_row())
        card = _card(32, 16)
        self.tile = W.Icon("download", W.PRIMARY, 20)
        self.tile.setFixedSize(44, 44)
        self.tile.setAlignment(Qt.AlignCenter)
        card.add(self.tile)
        self.title = W.label("", 18, 700)
        card.add(self.title)
        self.message = W.label("", 14, 400, W.MUTED_FG, wrap=True)
        card.add(self.message)
        self.versions = W.label("", 13, 400, W.MUTED_FG, wrap=True)
        card.add(self.versions)
        self.notes_box = W.Box(radius=12, bg=W.MUTED, border=None, padding=14, spacing=6)
        self.notes_box.add(W.label("WHAT'S NEW", 11, 700, W.MUTED_FG, upper_spacing=1))
        self.notes = W.label("", 13, 400, W.FG, wrap=True)
        self.notes_box.add(self.notes)
        card.add(self.notes_box)
        self.download = W.Button("Download update", "download", "default", "lg", full=True, weight=600)
        self.download.clicked.connect(self._open_download)
        card.add(self.download)
        self.again = W.Button("Check again", "refresh-cw", "outline", full=True)
        self.again.clicked.connect(self.guard.check)
        card.add(self.again)
        lay.addWidget(card)
        lay.addWidget(W.label(f"Boostimise {theme.APP_VERSION}", 12, 400, W.MUTED_FG, align=Qt.AlignCenter))
        _centered(self, col, 448)
        self.guard.changed.connect(self.refresh)

    def refresh(self):
        v = self.guard.verdict or {}
        reason = v.get("reason") or "update_required"
        icon, color, title = _LOCKS.get(reason, _LOCKS["update_required"])
        self.tile.set(icon, color)
        self.tile.setStyleSheet(f"background: {W.tint(color, .1)}; border-radius: 14px;")
        self.title.setText(title)
        self.message.setText(v.get("message") or "")
        updating = reason in ("update_required", "version_blocked")
        latest = v.get("latest_version")
        self.versions.setText(f"You have version {theme.APP_VERSION}"
                              + (f" · latest is {latest}" if latest and latest != theme.APP_VERSION else "") + ".")
        self.versions.setVisible(updating)
        notes = (v.get("notes") or "").strip()
        self.notes.setText(notes)
        self.notes_box.setVisible(updating and bool(notes))
        self.url = v.get("download_url")
        self.download.setVisible(updating and bool(self.url))
        checking = self.guard.checking
        self.again.set_loading(checking)
        self.again.setEnabled(not checking)
        self.again.setText("Checking…" if checking else "Check again")

    def _open_download(self):
        if self.url:
            QDesktopServices.openUrl(QUrl(self.url))


class LoadingPage(W.WebPage):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addLayout(W.h_row(None, W.Spinner(24, W.PRIMARY), None))
        lay.addSpacing(10)
        lay.addWidget(W.label("Signing you in…", 14, 400, W.MUTED_FG, align=Qt.AlignCenter))
        lay.addStretch(1)


class Gate(QStackedWidget):
    """Shows the screen for the account's state (core/account.py states)."""

    ready = Signal()

    def __init__(self):
        super().__init__()
        self.loading = LoadingPage()
        self.sign_in = SignInPage()
        self.onboarding = OnboardingPage()
        self.blocked = BlockedPage()
        self.update_page = UpdatePage()
        for w in (self.loading, self.sign_in, self.onboarding, self.blocked, self.update_page):
            self.addWidget(w)

    def show_update(self):
        self.update_page.refresh()
        self.setCurrentWidget(self.update_page)

    def show_state(self, state: str):
        if state == acc.ONBOARDING:
            self.onboarding.prepare()
        page = {acc.LOADING: self.loading, acc.SIGNED_OUT: self.sign_in, acc.ONBOARDING: self.onboarding,
                acc.BLOCKED: self.blocked}.get(state)
        if page is not None:
            self.setCurrentWidget(page)
