"""Account pieces shared by the pages: the quota pill, the mobile-number form with its country
picker, the daily-limit / suspended dialog, the "change mobile number" dialog, and the quota card
and account chip at the bottom of the sidebar (components/QuotaPill.tsx, PhoneForm.tsx,
CountrySelect.tsx, LimitDialog.tsx, dashboard/EditPhoneDialog.tsx, UserMenu.tsx).
"""
from __future__ import annotations

from functools import lru_cache

import phonenumbers
from phonenumbers import PhoneNumberFormat, PhoneNumberType
from PySide6.QtCore import QLocale, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractButton, QFrame, QHBoxLayout, QListWidget, QListWidgetItem, QMenu, QSizePolicy, QVBoxLayout,
    QWidget, QWidgetAction,
)

from . import account as acc
from . import net, supabase, theme
from . import webui as W
from .paths import ASSETS


# ---- quota pill -------------------------------------------------------------------------------
class QuotaPill(QFrame):
    """"Unlimited" for unlimited accounts, "3 of 20 left today" otherwise; hidden without a quota."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pill")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 0, 10, 0)
        lay.setSpacing(6)
        self.icon = W.Icon("zap", W.FG, 14)
        self.text = W.label("", 12, 600)
        lay.addWidget(self.icon)
        lay.addWidget(self.text)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.account = acc.get()
        self.account.changed.connect(self.update_from_account)
        self.update_from_account()

    def update_from_account(self):
        q = self.account.quota
        self.setVisible(q is not None)
        if q is None:
            return
        unlimited, empty = q.remaining is None, q.remaining == 0
        text = ("Unlimited" if unlimited else f"{q.remaining} of {q.limit} free left today" if q.kind == "guest"
                else f"{q.remaining} of {q.limit} left today")
        if unlimited:
            bg, border, fg = W.tint(W.PRIMARY, .1), W.tint(W.PRIMARY, .3), W.PRIMARY
        elif empty:
            bg, border, fg = W.tint(W.DESTRUCTIVE, .1), W.tint(W.DESTRUCTIVE, .3), W.DESTRUCTIVE
        else:
            bg, border, fg = W.CARD, W.BORDER, W.FG
        self.setStyleSheet(f"QFrame#pill {{ background: {bg}; border: 1px solid {border}; border-radius: 13px; }}")
        self.icon.set("infinity" if unlimited else "zap", fg)
        self.text.setText(text)
        W.set_color(self.text, fg)
        self.setToolTip(quota_tooltip(q))


# ---- countries & flags ------------------------------------------------------------------------
@lru_cache(maxsize=1)
def all_countries() -> list[tuple[str, str, str]]:
    """(ISO code, English name, "+880") for every country with a calling code, sorted by name."""
    out = []
    for code in phonenumbers.SUPPORTED_REGIONS:
        territory = QLocale.codeToTerritory(code)
        name = QLocale.territoryToString(territory) if territory != QLocale.AnyTerritory else code
        out.append((code, name or code, f"+{phonenumbers.country_code_for_region(code)}"))
    return sorted(out, key=lambda c: c[1].casefold())


def find_country(code: str | None):
    if not code:
        return None
    code = code.upper()
    return next((c for c in all_countries() if c[0] == code), None)


@lru_cache(maxsize=512)
def flag_pixmap(code: str, ratio: float = 2.0) -> QPixmap:
    """public/flags/XX.svg (country-flag-icons, MIT) at 21 x 14 with rounded corners and a hairline."""
    w, h = 21, 14
    pm = QPixmap(round(w * ratio), round(h * ratio))
    pm.fill(Qt.transparent)
    path = ASSETS / "flags" / f"{code.upper()}.svg"
    if not path.is_file():
        return pm
    p = QPainter(pm)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    p.scale(ratio, ratio)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, w, h), 2, 2)
    p.setClipPath(clip)
    r = QSvgRenderer(str(path))
    r.setAspectRatioMode(Qt.KeepAspectRatioByExpanding)
    r.render(p, QRectF(0, 0, w, h))
    p.setClipping(False)
    p.setPen(QPen(QColor(0, 0, 0, 20), 1))
    p.drawRoundedRect(QRectF(.5, .5, w - 1, h - 1), 2, 2)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


class CountryPopup(QFrame):
    """The searchable list of every country with its flag and calling code."""

    picked = Signal(str)

    def __init__(self, current: str, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        card = QFrame(objectName="pop")
        card.setStyleSheet(f"QFrame#pop {{ background: #ffffff; border: 1px solid {W.tint(W.FG, .1)}; border-radius: 10px; }}")
        W.shadow(card, blur=20, dy=4, alpha=.12)
        outer.addWidget(card)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 4)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(10, 0, 10, 0)
        head.setSpacing(8)
        head.addWidget(W.Icon("search", W.MUTED_FG, 16))
        self.search = W.Input("Search country or code...", 38)
        self.search.setStyleSheet("QLineEdit { border: none; background: transparent; font-size: 14px; padding: 0; }")
        head.addWidget(self.search, 1)
        lay.addLayout(head)
        lay.addWidget(W.Separator())
        self.list = QListWidget()
        self.list.setFixedHeight(288)
        self.list.setIconSize(QSize(21, 14))
        self.list.setStyleSheet(f"""
            QListWidget {{ border: none; background: transparent; outline: 0; padding: 4px; font-size: 14px; }}
            QListWidget::item {{ height: 32px; border-radius: 6px; padding-left: 4px; color: {W.FG}; }}
            QListWidget::item:selected, QListWidget::item:hover {{ background: {W.MUTED}; color: {W.FG}; }}""")
        lay.addWidget(self.list)
        self.empty = W.label("No country found.", 14, 400, W.MUTED_FG, align=Qt.AlignCenter)
        self.empty.setFixedHeight(60)
        self.empty.hide()
        lay.addWidget(self.empty)
        for code, name, dial in all_countries():
            item = QListWidgetItem(QIcon(flag_pixmap(code)), f"{name}    {dial}" + ("   ✓" if code == current else ""))
            item.setData(Qt.UserRole, code)
            item.setData(Qt.UserRole + 1, f"{name} {code} {dial[1:]}".lower())
            self.list.addItem(item)
            if code == current:
                self.list.setCurrentItem(item)
        self.list.itemClicked.connect(self._pick)
        self.list.itemActivated.connect(self._pick)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._enter)
        self.setFixedWidth(336)

    def _filter(self, text: str):
        q = text.strip().lower().lstrip("+")
        shown = 0
        for i in range(self.list.count()):
            it = self.list.item(i)
            hide = bool(q) and q not in it.data(Qt.UserRole + 1)
            it.setHidden(hide)
            shown += not hide
        self.empty.setVisible(shown == 0)
        self.list.setVisible(shown > 0)

    def _enter(self):
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                self._pick(self.list.item(i))
                return

    def _pick(self, item):
        self.picked.emit(item.data(Qt.UserRole))
        self.close()

    def popup(self, at: QPoint):
        self.move(at)
        self.show()
        self.search.setFocus()
        if self.list.currentItem():
            self.list.scrollToItem(self.list.currentItem(), QListWidget.PositionAtCenter)


class CountrySelect(QAbstractButton):
    """The country-code button next to the mobile number: flag, "+880", up-down chevrons."""

    changed = Signal(str)

    def __init__(self, code: str, parent=None):
        super().__init__(parent)
        self.code = code
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(40)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.clicked.connect(self._open)

    def set_code(self, code: str):
        self.code = code
        self.updateGeometry()
        self.update()
        self.changed.emit(code)

    def sizeHint(self):
        c = find_country(self.code)
        text_w = self.fontMetrics().horizontalAdvance(c[2] if c else "Code")
        return QSize(12 + 21 + 8 + text_w + 8 + 14 + 12, 40)

    def _open(self):
        pop = CountryPopup(self.code, self)
        pop.picked.connect(self.set_code)
        pop.popup(self.mapToGlobal(QPoint(-8, self.height() - 4)))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        if not self.isEnabled():
            p.setOpacity(.5)
        p.setPen(QPen(QColor(W.BORDER), 1))
        p.setBrush(QColor(W.MUTED if self.underMouse() else W.BG))
        p.drawRoundedRect(r, W.RADIUS, W.RADIUS)
        c = find_country(self.code)
        x = 12
        if c:
            p.drawPixmap(x, (self.height() - 14) // 2, flag_pixmap(c[0]))
            x += 21 + 8
            p.setPen(QColor(W.FG))
            p.setFont(W.font(14))
            p.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignVCenter | Qt.AlignLeft, c[2])
        else:
            p.setPen(QColor(W.MUTED_FG))
            p.setFont(W.font(14))
            p.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignVCenter | Qt.AlignLeft, "Code")
        p.setOpacity(.5 * (1 if self.isEnabled() else .5))
        p.drawPixmap(self.width() - 12 - 14, (self.height() - 14) // 2,
                     W.icon_pixmap("chevrons-up-down", W.FG, 14, max(2.0, W.dpr())))
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


def pick_country(code: str | None) -> str:
    if find_country(code):
        return code.upper()
    local = QLocale.territoryToCode(QLocale.system().territory())
    return local if find_country(local) else "US"


class PhoneForm(QWidget):
    """Mobile number with a country-code picker (all countries), validated per country, saved with
    save_my_profile (which checks it again)."""

    saved = Signal(str)

    def __init__(self, default_country: str | None = None, default_phone: str | None = None,
                 default_name: str | None = None, ask_name: bool = False, submit_label: str = "Continue", parent=None):
        super().__init__(parent)
        self.ask_name = ask_name
        parsed = None
        if default_phone:
            try:
                parsed = phonenumbers.parse(default_phone, None)
            except phonenumbers.NumberParseException:
                parsed = None
        country = (phonenumbers.region_code_for_number(parsed) if parsed else None) or default_country
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(16)
        if ask_name:
            self.name = W.Input("", 40)
            self.name.setMaxLength(120)
            self.name.setText(default_name or "")
            lay.addLayout(W.v_col(W.label("Full name", 14, 600), self.name, spacing=8))
        self.country = CountrySelect(pick_country(country))
        self.phone = W.Input("", 40)
        self.phone.setText(phonenumbers.national_significant_number(parsed) if parsed else "")
        self.phone.textEdited.connect(self._clean)
        self.phone.returnPressed.connect(self.submit)
        self.country.changed.connect(lambda _: self._placeholder())
        row = W.h_row(self.country, self.phone, spacing=8, stretch_at=1)
        self.error = W.label("", 14, 400, W.DESTRUCTIVE, wrap=True)
        self.error.hide()
        hint = W.h_row(W.Icon("smartphone", W.MUTED_FG, 14),
                       W.label("We only use it for your account - never for spam.", 12, 400, W.MUTED_FG), None, spacing=6)
        self.hint = W.wrap(hint)
        lay.addLayout(W.v_col(W.label("Mobile number", 14, 600), row, self.error, self.hint, spacing=8))
        self.button = W.Button(submit_label, None, "default", "default", height=40, full=True)
        self.button.clicked.connect(self.submit)
        lay.addWidget(self.button)
        self._placeholder()

    def _clean(self, text: str):
        cleaned = "".join(ch for ch in text if ch.isdigit() or ch in " ()-")
        if cleaned != text:
            pos = self.phone.cursorPosition()
            self.phone.setText(cleaned)
            self.phone.setCursorPosition(max(0, pos - (len(text) - len(cleaned))))

    def _placeholder(self):
        try:
            ex = phonenumbers.example_number_for_type(self.country.code, PhoneNumberType.MOBILE)
            self.phone.setPlaceholderText(phonenumbers.format_number(ex, PhoneNumberFormat.NATIONAL) if ex
                                          else "Mobile number")
        except Exception:  # noqa: BLE001
            self.phone.setPlaceholderText("Mobile number")

    def _set_error(self, msg: str | None):
        self.error.setText(msg or "")
        self.error.setVisible(bool(msg))
        self.hint.setVisible(not msg)
        self.phone.set_invalid(bool(msg))

    def _busy(self, on: bool):
        self.button.set_loading(on)
        for w in (self.button, self.phone, self.country, self.name if self.ask_name else None):
            if w is not None:
                w.setEnabled(not on)

    def submit(self):
        self._set_error(None)
        try:
            p = phonenumbers.parse(self.phone.text(), self.country.code)
            valid = phonenumbers.is_valid_number(p)
        except phonenumbers.NumberParseException:
            valid = False
        if not valid:
            self._set_error("Enter a valid mobile number for the selected country.")
            return
        params = {
            "p_country": phonenumbers.region_code_for_number(p) or self.country.code,
            "p_dial_code": f"+{p.country_code}",
            "p_national": phonenumbers.national_significant_number(p),
            "p_e164": phonenumbers.format_number(p, PhoneNumberFormat.E164),
            "p_full_name": self.name.text() if self.ask_name else None,
        }
        self._busy(True)

        def done(result):
            self._busy(False)
            self.saved.emit(str(result or params["p_e164"]))

        def failed(e):
            self._busy(False)
            self._set_error(supabase.db_message(e, "Could not save your number. Please try again."))
        net.run_bg(lambda: acc.get().rpc("save_my_profile", params), done, failed)


class EditPhoneDialog(W.WebDialog):
    def __init__(self, parent, phone: str | None, country: str | None):
        super().__init__(parent, 448)
        self.header("Change mobile number", "Choose your country code and enter your mobile number.")
        form = PhoneForm(country, phone, submit_label="Save")
        form.saved.connect(lambda _: (W.toast.success("Mobile number updated"), self.accept()))
        self.body.addWidget(form)


# ---- daily limit / suspended ------------------------------------------------------------------
class LimitDialog(W.WebDialog):
    """Shown when today's quota is used up (-> plans) or the account is blocked."""

    def __init__(self, parent, reason: str, on_plans=None):
        super().__init__(parent, 448)
        a = acc.get()
        q = a.quota
        if reason == "blocked":
            self.header("Account suspended", "Your account can't enhance photos right now. Please contact support "
                        "if you think this is a mistake.", "ban", W.tint(W.DESTRUCTIVE, .1), W.DESTRUCTIVE)
            close = W.Button("Close", None, "outline")
            close.clicked.connect(self.reject)
            self.add_footer([close])
        else:
            resets = f" - your limit resets in {acc.format_reset_in(q.resets_at)}" if q and q.resets_at else ""
            self.header("Daily limit reached", f"You've enhanced {q.used if q else 0} photos today{resets}. "
                        "Upgrade for unlimited enhancements.", "clock")
            later = W.Button("Later", None, "outline")
            later.clicked.connect(self.reject)
            plans = W.Button("See plans")
            plans.clicked.connect(lambda: (self.accept(), on_plans and on_plans()))
            self.add_footer([later, plans])


# ---- the account chip in the sidebar ----------------------------------------------------------
def quota_tooltip(q: acc.Quota) -> str:
    return ("Your account has no daily limit" if q.remaining is None else
            f"{q.used} used today · resets in {acc.format_reset_in(q.resets_at)}"
            + (" · sign in for more" if q.kind == "guest" else ""))


class SidebarQuota(QWidget):
    """The day's enhancements on the navy sidebar: "16 of 20 left today" over a sky-blue bar of
    what is left. Hidden while the sidebar is folded to icons, or without a quota."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.collapsed = False
        self.account = acc.get()
        self.account.changed.connect(self._refresh)
        self._refresh()

    def set_collapsed(self, collapsed: bool):
        self.collapsed = collapsed
        self._refresh()

    def _refresh(self):
        q = self.account.quota
        self.setVisible(q is not None and not self.collapsed)
        self.setToolTip(quota_tooltip(q) if q is not None else "")
        self.update()

    def paintEvent(self, _):
        q = self.account.quota
        if q is None:
            return
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        box = QRectF(self.rect()).adjusted(10, 4, -10, -6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 15))
        p.drawRoundedRect(box, 14, 14)
        unlimited = q.remaining is None
        icon = W.icon_pixmap("infinity" if unlimited else "zap", theme.SKY, 14, max(2.0, W.dpr()))
        p.drawPixmap(QRectF(box.left() + 12, box.top() + 11, 14, 14), icon, QRectF(icon.rect()))
        if unlimited:
            bold, rest = "Unlimited", " enhancements"
        else:
            bold, rest = f"{q.remaining} of {q.limit}", " free left today" if q.kind == "guest" else " left today"
        x = box.left() + 34
        p.setFont(W.font(12.5, 600))
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(x, box.top() + 8, box.width(), 20), Qt.AlignLeft | Qt.AlignVCenter, bold)
        x += p.fontMetrics().horizontalAdvance(bold)
        p.setFont(W.font(12.5))
        p.setPen(QColor(theme.ON_NAVY))
        p.drawText(QRectF(x, box.top() + 8, box.right() - x - 8, 20), Qt.AlignLeft | Qt.AlignVCenter, rest)
        bar = QRectF(box.left() + 12, box.bottom() - 16, box.width() - 24, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(bar, 2.5, 2.5)
        share = 1.0 if unlimited else (q.remaining or 0) / max(1, q.limit or 1)
        if share > 0:
            p.setBrush(QColor(theme.SKY))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.height(), bar.width() * min(1.0, share)),
                                     bar.height()), 2.5, 2.5)
        p.end()


class AccountChip(QAbstractButton):
    """Avatar, name and plan at the bottom of the (navy) sidebar; click for the account menu."""

    def __init__(self, on_menu, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.NoFocus)
        self.collapsed = False
        self.account = acc.get()
        self.account.changed.connect(self._refresh)
        self.clicked.connect(lambda: on_menu(self))
        self._refresh()

    def set_collapsed(self, collapsed: bool):
        self.collapsed = collapsed
        self.update()

    def _refresh(self):
        u = self.account.display_user() or {}
        q = self.account.quota
        self.setToolTip(f"{u.get('name') or 'Your account'}\n{u.get('email') or ''}"
                        + (f"\n{quota_tooltip(q)}" if q is not None else ""))
        self.update()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, _):
        a = self.account
        u = a.display_user() or {}
        p = QPainter(self)
        p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        box = QRectF(self.rect()).adjusted(10, 4, -10, -4)
        if self.underMouse() or not self.collapsed:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 26 if self.underMouse() else 10))
            p.drawRoundedRect(box, 14, 14)
        size = 32
        x = box.center().x() - size / 2 if self.collapsed else box.left() + 8
        r = QRectF(x, box.center().y() - size / 2, size, size)
        clip = QPainterPath()
        clip.addRoundedRect(r, 10, 10)
        p.save()
        p.setClipPath(clip)
        if a.avatar_image is not None:
            img = a.avatar_image.scaled(round(size * 2), round(size * 2), Qt.KeepAspectRatioByExpanding,
                                        Qt.SmoothTransformation)
            p.drawImage(r, img)
        else:
            p.fillRect(r, QColor(theme.SKY))
            p.setPen(QColor(theme.NAVY))
            p.setFont(W.font(12, 800))
            p.drawText(r, Qt.AlignCenter, W.initials_of(u.get("name"), u.get("email")))
        p.restore()
        dot = QColor(theme.LIVE) if a.realtime_state == "subscribed" and not a.offline else QColor(theme.ON_NAVY_MUTED)
        p.setPen(QPen(QColor(theme.NAVY), 2))
        p.setBrush(dot)
        p.drawEllipse(QRectF(r.right() - 9, r.bottom() - 9, 10, 10))
        if not self.collapsed:
            tx = r.right() + 10
            width = box.right() - tx - 4
            p.setPen(QColor("#ffffff"))
            f = W.font(13, 600)
            p.setFont(f)
            name = u.get("name") or u.get("email") or "Your account"
            p.drawText(QRectF(tx, r.top() - 1, width, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(name, Qt.ElideRight, int(width)))
            p.setFont(W.font(11.5))
            p.setPen(QColor(theme.ON_NAVY_MUTED))
            # the plan only: the day's quota is on the card above (and in the tooltip)
            sub = "Offline" if a.offline else (a.plan["name"] if a.plan else "Free account")
            p.drawText(QRectF(tx, r.top() + 16, width, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       p.fontMetrics().elidedText(sub, Qt.ElideRight, int(width)))
        p.end()


def account_menu(parent: QWidget, actions: list[tuple[str, str, object]], sign_out) -> QMenu:
    """components/UserMenu.tsx: name, e-mail and plan, then the pages, then Sign out."""
    a = acc.get()
    u = a.display_user() or {}
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(f"""
        QMenu {{ background: #ffffff; border: 1px solid {W.tint(W.FG, .1)}; border-radius: 10px; padding: 4px; }}
        QMenu::item {{ padding: 6px 10px 6px 8px; border-radius: 6px; color: {W.FG}; font-size: 14px; }}
        QMenu::item:selected {{ background: {W.MUTED}; color: {W.FG}; }}
        QMenu::icon {{ padding-left: 8px; }}
        QMenu::separator {{ height: 1px; background: {W.BORDER}; margin: 4px -4px; }}""")
    head = QWidget()
    col = QVBoxLayout(head)
    col.setContentsMargins(8, 6, 8, 6)
    col.setSpacing(2)
    col.addWidget(W.label(u.get("name") or "Your account", 14, 600))
    col.addWidget(W.label(u.get("email") or "", 12, 400, W.MUTED_FG))
    badge = W.Badge(a.plan["name"], "default", "crown") if a.plan else W.Badge("Free account", "secondary")
    col.addSpacing(4)
    col.addWidget(badge)
    head.setMinimumWidth(232)
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(head)
    menu.addAction(wa)
    menu.addSeparator()
    for icon, text, fn in actions:
        act = QAction(W.qicon(icon, W.FG, 16), text, menu)
        act.triggered.connect(fn)
        menu.addAction(act)
    menu.addSeparator()
    # the destructive item: red text and icon (a plain QAction can't colour its text)
    out = W.Button("Sign out", "log-out", "ghost", "default", font_px=14, weight=400)
    out.setStyleSheet(out.styleSheet().replace(f"color: {W.FG}", f"color: {W.DESTRUCTIVE}")
                      + "QPushButton { text-align: left; padding-left: 8px; border-radius: 6px; }")
    out._icon_name = "log-out"
    out.icon_color = lambda: W.DESTRUCTIVE
    out._apply_icon()
    wa_out = QWidgetAction(menu)
    wa_out.setDefaultWidget(out)
    out.clicked.connect(lambda: (menu.close(), sign_out()))
    menu.addAction(wa_out)
    return menu
