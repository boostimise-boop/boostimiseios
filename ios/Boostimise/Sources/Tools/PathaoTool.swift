import Foundation
import Observation
import UIKit
import WebKit

/// Something the user has to do first (log in to Pathao, open a page).
struct NeedUser: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

/// Pathao Auto-fill (tools/pathao_autofill): reads the order message of each code in WhatsApp and
/// fills Pathao's New Delivery form on merchant.pathao.com, which runs inside the app. You check the
/// form and tap Save; the next code fills itself after the save.
@MainActor @Observable
final class PathaoTool {
    static let PENDING = "Pending"
    static let FETCHING = "Reading WhatsApp…"
    static let FILLED = "Filled - waiting for Save"
    static let SAVED = "Saved ✓"
    static let SAVED_EARLIER = "Saved earlier"
    static let SKIPPED = "Skipped"
    static let NOT_FOUND = "Not found"
    static let IN_PATHAO = "Saved (in Pathao)"   // "Check Pathao" found an order with this code as its Merchant Order ID

    static let checkDays = 45   // how far back "Check Pathao" reads the orders
    private static let restartDrop = 40   // a prefix starting its codes again (6-980 -> 6-47..6-1) drops at least this much...
    private static let restartRatio = 3   // ...to at most a third of the numbers before

    static let defaultChats: [(String, String)] = [
        ("2", "Free Cover BD(2-)"),
        ("3", "Cover Print BD 0.1 (3-)"),
        ("4", "Customise Cover BD (4-)"),
        ("4a", "Customise Cover BD(4a-)"),
        ("5", "Misty Cover House 3.0 (5-)"),
        ("5a", "Misty Cover House (5a-)"),
        ("6", "Customise Phone Case (6-)"),
    ]
    static let pathaoURL = URL(string: "https://merchant.pathao.com/")!

    // key -> (placeholder, which match, label shown above the box)
    static let fieldOrder = ["order_id", "amount", "phone", "phone2", "name", "address"]
    static let fields: [String: (String, Int, String)] = [
        "order_id": ("Type ID (Optional)", 0, "Merchant Order ID"),
        "amount": ("Enter the amount including delivery charge", 0, "Amount to Collect"),
        "phone": ("Enter phone number", 0, "Recipient's Phone"),
        "phone2": ("Enter phone number", 1, "Recipient's Secondary Phone"),
        "name": ("Type Name", 0, "Recipient's Name"),
        "address": ("Enter full address", 0, "Recipient's Address"),
    ]

    // Finds a Pathao input by placeholder, falling back to the label text above it.
    private static let jsFind = #"""
const __vis = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
const __find = (spec) => {
  if (!spec) return null;
  const inputs = [...document.querySelectorAll('input, textarea')].filter(__vis);
  const ph = (spec.ph || '').trim().toLowerCase();
  if (ph) {
    const hits = inputs.filter(e => (e.getAttribute('placeholder') || '').trim().toLowerCase() === ph);
    if (hits.length > spec.nth) return hits[spec.nth];
  }
  if (!spec.label) return null;
  const norm = (s) => (s || '').replace(/[‘’`]/g, "'").replace(/\s+/g, ' ').trim().toLowerCase();
  const want = norm(spec.label);
  for (const lab of document.querySelectorAll('label, span, div, p, legend, strong, b, h5, h6')) {
    if (norm(lab.textContent) !== want || !__vis(lab)) continue;
    const next = inputs.find(i => lab.compareDocumentPosition(i) & Node.DOCUMENT_POSITION_FOLLOWING);
    if (next) return next;
  }
  return null;
};
"""#

    // The orders created from `from` to `to` (YYYY-MM-DD), read from the API behind the merchant panel's
    // Orders page with the panel's own login (the result waits in window.__ccbdOrders). Archived orders
    // are left out, like on that page, and so are return orders (RC...): they carry the merchant as
    // recipient, and the delivery they return may be older.
    private static let jsPathaoOrders = #"""
(function (from, to) {
  window.__ccbdOrders = {pending: true};
  (async () => {
    let token = '';
    try { token = JSON.parse(localStorage.getItem('pathaoMerchant:auth') || '{}').token || ''; } catch (e) {}
    if (!token) return {error: 'login'};
    const orders = [];
    for (let page = 1; page <= 100; page++) {
      const qs = new URLSearchParams({page: page, limit: 500, archive: 0, from_date: from, to_date: to});
      let r;
      for (let tries = 1; ; tries++) {   // "Too Many Requests": wait a little longer each time
        r = await fetch('/api/v1/orders/all?' + qs, {headers: {Authorization: 'Bearer ' + token, Accept: 'application/json'}});
        if (r.status !== 429 || tries === 6) break;
        await new Promise(done => setTimeout(done, 3000 * tries));
      }
      if (r.status === 401 || r.status === 403) return {error: 'login'};
      if (!r.ok) return {error: 'Pathao answered ' + r.status};
      const d = (await r.json()).data || {};
      for (const o of d.data || []) {
        if (o.order_type === 'Return' || !o.merchant_order_id) continue;
        orders.push([o.merchant_order_id, o.recipient_phone || '', o.recipient_name || '',
                     o.order_amount == null ? '' : String(o.order_amount), o.order_created_at || '', o.order_consignment_id || '']);
      }
      if (!d.last_page || page >= d.last_page) break;
    }
    return {orders: orders};
  })().then(r => { window.__ccbdOrders = r; }, e => { window.__ccbdOrders = {error: String(e)}; });
})("__FROM__", "__TO__");
"""#

    // Remembers when Save was clicked, and every POST/PUT answer.
    static let jsHooks = #"""
if (!window.__ccbdHooked) {
  window.__ccbdHooked = true;
  const mark = (on) => {
    try { on ? sessionStorage.setItem('__ccbdSave', String(Date.now())) : sessionStorage.removeItem('__ccbdSave'); } catch (e) {}
  };
  document.addEventListener('click', (e) => {
    let el = e.target;
    for (let i = 0; el && i < 5; i++, el = el.parentElement) {
      if (el.matches && el.matches('input, textarea, select') && !['submit', 'button'].includes(el.type)) return;
      const t = ((el.innerText || el.value || '') + '').trim().toLowerCase();
      if (t.length > 40) return;
      if (['save', 'save order', 'submit', 'confirm'].includes(t)) { mark(true); return; }
      if (['cancel', 'reset', 'clear'].includes(t)) { mark(false); return; }
    }
  }, true);
  document.addEventListener('submit', () => mark(true), true);
  const note = (start, status, method) => {
    try {
      const a = JSON.parse(sessionStorage.getItem('__ccbdResp') || '[]');
      a.push([start, status, method]);
      sessionStorage.setItem('__ccbdResp', JSON.stringify(a.slice(-40)));
    } catch (e) {}
  };
  const of = window.fetch;
  window.fetch = function (input, init) {
    const method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const start = Date.now();
    const p = of.apply(this, arguments);
    if (method === 'POST' || method === 'PUT') p.then(r => note(start, r.status, method), () => note(start, 0, method));
    return p;
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m) { this.__m = String(m || 'GET').toUpperCase(); return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (this.__m === 'POST' || this.__m === 'PUT') {
      const start = Date.now(), m = this.__m;
      this.addEventListener('loadend', () => note(start, this.status, m));
    }
    return os.apply(this, arguments);
  };
}
"""#

    @MainActor @Observable
    final class Item: Identifiable {
        let id = UUID()
        let code: String
        var status = PathaoTool.PENDING
        var order: OrderParser.Order?
        var raw = ""
        @ObservationIgnored var formPhone = ""
        init(_ code: String) { self.code = code }
    }

    struct ChatName: Identifiable {
        let id = UUID()
        var prefix: String
        var name: String
    }

    @ObservationIgnored private let prefs = Storage.prefs("pathao_autofill")
    let log = ToolLog()
    private(set) var items: [Item] = []
    private(set) var listName = ""
    @ObservationIgnored private var listURL: URL?
    private(set) var listText = ""
    private(set) var cur: Item?
    private(set) var running = false
    private(set) var waiting = false
    private(set) var state = "Open your code list, log in to Pathao below, then press Start."
    var attention = ""
    var lookbackDays: Int
    var writePhone: Bool
    var startFromBottom: Bool
    var chatNames: [ChatName]

    private var createURL: String {
        get { prefs.string("pathao_create_url") }
        set { prefs.set("pathao_create_url", newValue) }
    }

    // order messages read from WhatsApp: code -> (message id, text, order)
    @ObservationIgnored private var cache: [String: (String, String, OrderParser.Order)] = [:]
    @ObservationIgnored private var duplicates: [String: Set<String>] = [:]
    @ObservationIgnored private var readPrefixes = Set<String>()
    @ObservationIgnored private var job: Task<Void, Never>?

    // the save watch
    @ObservationIgnored private var baselineToasts = Set<String>()
    @ObservationIgnored private var fillURL = ""
    @ObservationIgnored private var armedMs = 0.0
    @ObservationIgnored private var successHits = 0
    @ObservationIgnored private var resetHits = 0
    @ObservationIgnored private var resetWarned = false

    init() {
        let p = Storage.prefs("pathao_autofill")
        lookbackDays = p.int("lookback_days", 30)
        writePhone = p.bool("write_phone_to_list", true)
        startFromBottom = p.bool("start_from_bottom", true)
        var names = Self.defaultChats
        if let saved = JSONTools.parse(p.string("chat_names")) as? JSON {
            for (k, v) in saved.sorted(by: { $0.key < $1.key }) {
                let n = (v as? String) ?? ""
                if let i = names.firstIndex(where: { $0.0 == k }) { names[i].1 = n } else { names.append((k, n)) }
            }
        }
        chatNames = names.map { ChatName(prefix: $0.0, name: $0.1) }
    }

    func saveSettings() {
        var m = JSON()
        for c in chatNames { m[c.prefix] = c.name }
        prefs.set("lookback_days", lookbackDays)
        prefs.set("write_phone_to_list", writePhone)
        prefs.set("start_from_bottom", startFromBottom)
        prefs.set("chat_names", JSONTools.string(m))
        cache.removeAll()
        readPrefixes.removeAll()
    }

    private func chatFor(_ prefix: String) -> String {
        (chatNames.first { $0.prefix == prefix }?.name ?? "\(prefix)-").trimmed
    }

    private func savedCodes() -> Set<String> { prefs.stringSet("saved_codes") }

    private static func nowMs() -> Double { Date().timeIntervalSince1970 * 1000 }

    // MARK: - the list

    func openList(_ url: URL) throws {
        let text = try Storage.readText(url)
        listURL = url
        listName = url.lastPathComponent
        load(text)
    }

    func pasteList(_ text: String) {
        listURL = nil
        listName = "pasted list"
        load(text)
    }

    private func load(_ text: String) {
        listText = text
        var codes = OrderParser.readCodeList(text)
        if startFromBottom { codes.reverse() }
        let saved = savedCodes()
        items = codes.map { c in
            let it = Item(c)
            if saved.contains(c) { it.status = Self.SAVED_EARLIER }
            return it
        }
        cur = nil
        running = false
        waiting = false
        state = "\(codes.count) codes loaded (\(items.filter { $0.status == Self.SAVED_EARLIER }.count) saved earlier). Press Start."
    }

    // MARK: - the Pathao page

    @ObservationIgnored private(set) var web: WKWebView?
    @ObservationIgnored private var webDelegate: PathaoDelegate?

    func view() -> WKWebView {
        if let v = web { return v }
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(source: "(function(){\(Self.jsHooks)})();void 0;", injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        cfg.userContentController = ucc
        let v = WKWebView(frame: CGRect(x: 0, y: 0, width: 400, height: 500), configuration: cfg)
        let d = PathaoDelegate()
        webDelegate = d
        v.navigationDelegate = d
        v.uiDelegate = d
        if #available(iOS 16.4, *) { v.isInspectable = true }
        v.load(URLRequest(url: URL(string: createURL) ?? Self.pathaoURL))
        web = v
        return v
    }

    private func eval(_ js: String) async -> Any? {
        guard let v = web else { return nil }
        return await withCheckedContinuation { (cont: CheckedContinuation<Any?, Never>) in
            v.evaluateJavaScript(js) { result, _ in cont.resume(returning: result) }
        }
    }

    private func spec(_ key: String) -> String {
        let f = Self.fields[key]!
        return JSONTools.string(jsonOf(("ph", f.0), ("nth", f.1), ("label", f.2)))
    }

    private func pageState() async -> JSON? {
        let specs = "{" + ["order_id", "phone", "address"].map { "\"\($0)\": \(spec($0))" }.joined(separator: ",") + "}"
        let js = "(function(specs){" + Self.jsFind + Self.jsHooks + #"""
  const val = (k) => { const el = __find(specs[k]); return el ? (el.value || '') : null; };
  const oid = val('order_id');
  const toasts = [];
  document.querySelectorAll('[role="alert"], [role="status"], [role="dialog"], [class*="toast" i], [class*="notif" i], [class*="snack" i], [class*="swal" i], [class*="alert" i], [class*="message" i]').forEach(el => {
    const t = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (t && t.length <= 300 && /success|created|placed|সফল/i.test(t) && __vis(el)) toasts.push(t);
  });
  let click = 0, resp = [];
  try { click = parseInt(sessionStorage.getItem('__ccbdSave') || '0', 10) || 0; } catch (e) {}
  try { resp = JSON.parse(sessionStorage.getItem('__ccbdResp') || '[]'); } catch (e) {}
  return JSON.stringify({url: location.href, docStart: performance.timeOrigin, click, form: oid !== null,
          orderId: oid, phone: val('phone'), address: val('address'), toasts, resp});
"""# + "})(" + specs + ")"
        return JSONTools.parse(await eval(js) as? String) as? JSON
    }

    /// Sets a field the way typing does, so Pathao's React form takes the value.
    private func setField(_ key: String, _ value: String) async -> Bool {
        let js = "(function(spec, value){" + Self.jsFind + #"""
  const el = __find(spec);
  if (!el) return false;
  el.focus();
  const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, value);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'End'}));
  return true;
"""# + "})(" + spec(key) + "," + JSONTools.quote(value) + ")"
        return (await eval(js) as? Bool) == true
    }

    private func fieldValue(_ key: String) async -> String? {
        await eval("(function(spec){" + Self.jsFind + " const el = __find(spec); return el ? (el.value || '') : null; })(\(spec(key)))") as? String
    }

    private func waitForm(_ timeout: TimeInterval = 40) async throws {
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            try Task.checkCancellation()
            if let st = await pageState(), st.bool("form") {
                createURL = st.str("url")
                return
            }
            try await Task.sleep(nanoseconds: 500_000_000)
        }
        throw NeedUser("Pathao's 'New Delivery' form is not open. Log in to Pathao below, open New Delivery, then press Start.")
    }

    /// Every code starts on a fresh, empty New Delivery form.
    private func prepareForm() async throws {
        let st = await pageState()
        if let st, st.bool("form") && st.str("orderId").isEmpty && st.str("phone").isEmpty && st.str("address").isEmpty {
            createURL = st.str("url")
            return
        }
        let url = createURL
        if url.isEmpty {
            if let st, st.bool("form") { return }
            throw NeedUser("Open Pathao's 'New Delivery' page below, then press Start.")
        }
        state = "Opening a fresh Pathao New Delivery form…"
        if let u = URL(string: url) { web?.load(URLRequest(url: u)) }
        try await Task.sleep(nanoseconds: 1_500_000_000)
        try await waitForm()
    }

    private func fill(_ item: Item, prepare: Bool) async throws {
        guard let o = item.order else { return }
        if prepare { try await prepareForm() } else { try await waitForm(15) }
        var values: [(String, String)] = [("order_id", item.code), ("amount", o.amount), ("phone", o.phone), ("phone2", o.phone2),
                                          ("name", o.name)]
        if !o.address.isEmpty { values.append(("address", o.address)) }   // no address: leave what Pathao suggests
        for (k, v) in values {
            if !(await setField(k, v)) {
                let label = Self.fields[k]!.2
                if k == "phone2" || k == "address" {
                    if !v.isEmpty { log.add("Could not find the '\(label)' box - fill \(v) by hand.") }
                } else {
                    throw NeedUser("Could not find the '\(label)' box on the Pathao page. Open the New Delivery form and press Start.")
                }
            }
            if k == "phone2" { try await Task.sleep(nanoseconds: 700_000_000) }   // Pathao may look the phone up and fill things in
        }
        try await Task.sleep(nanoseconds: 600_000_000)
        for (k, v) in values {   // put our values back if Pathao's lookup overwrote any
            if let now = await fieldValue(k), now.trimmed != v.trimmed { _ = await setField(k, v) }
        }
    }

    private func arm() async {
        _ = await eval("(function(){try{sessionStorage.removeItem('__ccbdSave');}catch(e){}})()")
        let st = await pageState() ?? [:]
        baselineToasts = Set(st.strs("toasts"))
        fillURL = st.str("url")
        armedMs = Self.nowMs()
        successHits = 0
        resetHits = 0
        resetWarned = false
        waiting = true
    }

    private func urlKey(_ url: String) -> String {
        guard let u = URL(string: url) else { return url }
        return (u.host ?? "") + u.path.trimEnd("/")
    }

    private func postedSince(_ st: JSON, _ ms: Double) -> Bool {
        for r in st.arr("resp") ?? [] {
            guard let a = r as? [Any], a.count >= 2 else { continue }
            let t = (a[0] as? NSNumber)?.doubleValue ?? 0
            let status = (a[1] as? NSNumber)?.intValue ?? 0
            if t >= ms && (200...299).contains(status) { return true }
        }
        return false
    }

    /// Saved = Save was tapped, Pathao accepted a POST after it, and the page shows it.
    private func checkSaved() async throws {
        guard let item = cur else {
            waiting = false
            return
        }
        guard let st = await pageState() else { return }
        if st.bool("form") && st.str("orderId").trimmed.lowercased() == item.code && !st.str("phone").isEmpty {
            item.formPhone = st.str("phone")
        }
        let click = st.double("click")
        if click == 0 {
            let cleared = st.bool("form") && st.str("orderId").isEmpty && st.str("phone").isEmpty
            resetHits = cleared ? resetHits + 1 : 0
            if resetHits == 3 {
                if !postedSince(st, armedMs) {
                    log.add("\(item.code): the Pathao form was emptied without Save - filling it again.")
                    try await fill(item, prepare: false)
                    await arm()
                } else if !resetWarned {
                    resetWarned = true
                    attention = "The Pathao form was emptied. If \(item.code) was saved tap 'Saved → Next', otherwise 'Fill again'."
                }
            }
            return
        }
        if !postedSince(st, click - 500) {
            successHits = 0
            return
        }
        let toasts = st.strs("toasts")
        let newToast = toasts.contains { !baselineToasts.contains($0) }
        let moved = urlKey(st.str("url")) != urlKey(fillURL)
        let cleared = (!st.bool("form") || st.str("orderId").isEmpty) && st.double("docStart") < click
        if newToast || moved {
            try await onSaved()
        } else if cleared {
            successHits += 1
            if successHits >= 2 { try await onSaved() }
        } else {
            successHits = 0
        }
    }

    private func onSaved() async throws {
        guard let item = cur else { return }
        recordSaved(item)
        cur = nil
        waiting = false
        try await Task.sleep(nanoseconds: 1_000_000_000)
    }

    private func recordSaved(_ item: Item) {
        var saved = savedCodes()
        saved.insert(item.code)
        prefs.setStringSet("saved_codes", saved)
        let o = item.order
        let row = [item.code, dateText(Date(), "yyyy-MM-dd HH:mm:ss"), o?.name, o?.phone, o?.phone2, o?.amount, o?.address]
            .map { ($0 ?? "").replacingOccurrences(of: "\t", with: " ").replacingOccurrences(of: "\n", with: " ") }
            .joined(separator: "\t")
        let logText = String((prefs.string("saved_log") + row + "\n").suffix(200_000))
        prefs.set("saved_log", logText)
        item.status = Self.SAVED
        log.add("✓ \(item.code) saved.")
        if writePhone { writePhoneToList(item) }
    }

    private func writePhoneToList(_ item: Item) {
        guard let phone = (OrderParser.findPhones(item.formPhone) + OrderParser.findPhones(item.order?.phone)).first else {
            log.add("\(item.code): ⚠ no phone number to write in the list - type it there yourself.")
            return
        }
        guard let changed = OrderParser.writePhoneToList(listText, code: item.code, phone: phone) else {
            log.add("\(item.code): the list has no line with just \(item.code) any more - phone not written.")
            return
        }
        listText = changed
        if let url = listURL {
            do {
                try Storage.writeText(url, changed)
                log.add("\(item.code): wrote \(phone) next to it in \(listName).")
            } catch {
                log.add("\(item.code): ⚠ could not write \(phone) in \(listName) (\(error.localizedDescription)) - type it there yourself.")
            }
        } else {
            log.add("\(item.code): wrote \(phone) next to it in the list (copy the list to keep it).")
        }
    }

    // MARK: - Check Pathao

    /// One Pathao order: Merchant Order ID, phone, name, amount, created time, consignment id.
    private struct PathaoOrder {
        let id: String
        let phone: String
        let name: String
        let amount: String
        let created: String
        let consignment: String
    }

    private(set) var checking = false

    private static func median(_ v: [Int]) -> Double {
        let s = v.sorted()
        return s.count % 2 == 1 ? Double(s[s.count / 2]) : Double(s[s.count / 2 - 1] + s[s.count / 2]) / 2.0
    }

    /// {prefix: created time} of when each prefix last started its codes again from 1. The same code
    /// comes back in every round (5-36 on 5 Oct and again after 5-1 on 13 Oct), so only the orders of
    /// the current round say whether a code of the list is in Pathao. A restart is where the 10 orders
    /// before were high and the 10 from there on are low (a list is often saved from the bottom, so a
    /// round can start 6-47, 6-46...); one late order of an old round in between, or a few late ones of
    /// this round, don't count as one.
    private static func roundStarts(_ orders: [PathaoOrder]) -> [String: String] {
        var byPrefix: [String: [(String, Int)]] = [:]
        for o in orders {
            for code in OrderParser.findCodes(o.id) {
                let (prefix, number) = OrderParser.splitCode(code)
                byPrefix[prefix, default: []].append((o.created, number))
            }
        }
        var starts: [String: String] = [:]
        for (prefix, list) in byPrefix {
            let rows = list.sorted { $0.0 != $1.0 ? $0.0 < $1.0 : $0.1 < $1.1 }
            let nums = rows.map { $0.1 }
            var last = -1
            if nums.count < 2 { continue }
            for i in 1..<nums.count {
                let before = median(Array(nums[max(0, i - 10)..<i]))
                let after = median(Array(nums[i..<min(nums.count, i + 10)]))
                if before - after >= Double(restartDrop) && after * Double(restartRatio) <= before
                    && Double(nums[i]) - after < Double(restartDrop) {
                    if last < 0 || i - last > 10 { starts[prefix] = rows[i].0 }   // the first order of a new round
                    last = i
                }
            }
        }
        return starts
    }

    /// Reads the orders the merchant panel lists (the Pathao page below must be open and logged in).
    private func readPathaoOrders() async throws -> [PathaoOrder] {
        guard let host = await eval("location.hostname") as? String, host.hasSuffix("pathao.com") else {
            throw NeedUser("Open merchant.pathao.com in the page below and log in, then tap Check Pathao again.")
        }
        let today = Date()
        let from = Calendar.current.date(byAdding: .day, value: -Self.checkDays, to: today) ?? today
        let script = Self.jsPathaoOrders
            .replacingOccurrences(of: "__FROM__", with: dateText(from, "yyyy-MM-dd"))
            .replacingOccurrences(of: "__TO__", with: dateText(today, "yyyy-MM-dd"))
        _ = await eval(script)
        let end = Date().addingTimeInterval(4 * 60)
        while Date() < end {
            try Task.checkCancellation()
            try await Task.sleep(nanoseconds: 1_000_000_000)
            let text = await eval("JSON.stringify(window.__ccbdOrders || null)") as? String
            guard let res = JSONTools.parse(text) as? JSON else {
                throw NeedUser("The Pathao page changed while its orders were read. Tap Check Pathao again.")
            }
            if res.bool("pending") { continue }
            let error = res.str("error")
            if error == "login" {
                throw NeedUser("Pathao is not logged in. Log in to merchant.pathao.com in the page below, then tap Check Pathao again.")
            }
            if !error.isEmpty { throw NeedUser("Could not read the orders from Pathao: \(error).") }
            return (res.arr("orders") ?? []).compactMap { row -> PathaoOrder? in
                guard let r = row as? [Any], r.count >= 6 else { return nil }
                let s = r.map { ($0 as? String) ?? "" }
                return PathaoOrder(id: s[0], phone: s[1], name: s[2], amount: s[3], created: s[4], consignment: s[5])
            }
        }
        throw NeedUser("Pathao took too long to answer. Tap Check Pathao again.")
    }

    /// Looks the loaded codes up in Pathao's orders (the code is an order's Merchant Order ID), counting only
    /// the orders of each prefix's current round of codes (see roundStarts). A code found there is marked as
    /// saved with that order's phone, and the phone is written next to it in the list; the codes not found
    /// stay as they are.
    func checkPathao() {
        if items.isEmpty {
            state = "Open or paste your code list first."
            return
        }
        if checking { return }
        checking = true
        attention = ""
        Task {
            do {
                try await doCheckPathao()
            } catch is CancellationError {
                // stopped
            } catch let e as NeedUser {
                attention = e.message
            } catch {
                attention = "Error: \(error.localizedDescription)"
                log.add("Error: \(error.localizedDescription)")
            }
            checking = false
        }
    }

    private func doCheckPathao() async throws {
        state = "Reading the orders from Pathao…"
        let orders = try await readPathaoOrders()
        let starts = Self.roundStarts(orders)
        var byCode: [String: [PathaoOrder]] = [:]
        for o in orders {
            for code in OrderParser.findCodes(o.id) {   // "2-257+258": one order for both codes
                if o.created >= (starts[OrderParser.splitCode(code).0] ?? "") { byCode[code, default: []].append(o) }
            }
        }
        var shown = Set<String>()
        for item in items {
            let prefix = OrderParser.splitCode(item.code).0
            if shown.insert(prefix).inserted, let s = starts[prefix] {
                log.add("\(prefix)-: codes started again from 1 on \(String(s.prefix(16))) - older orders are not counted.")
            }
        }

        var text = listText
        var found: [String] = []
        var missing: [String] = []
        var written = 0
        for item in items {
            let hits = (byCode[item.code] ?? []).sorted { $0.created > $1.created }
            guard let o = hits.first else {
                missing.append(item.code)
                continue
            }
            if item === cur && item.status == Self.FILLED {
                log.add("\(item.code): ⚠ Pathao already has an order \(o.consignment) with this code - it is on the form now, check it is not saved twice.")
                continue
            }
            var phones: [String] = []
            for p in hits.flatMap({ OrderParser.findPhones($0.phone) }) where !phones.contains(p) { phones.append(p) }
            let old = item.order
            item.order = OrderParser.Order(code: item.code, name: o.name, phone: phones.first ?? o.phone, phone2: old?.phone2 ?? "",
                                           address: old?.address ?? "", amount: o.amount, warnings: [])
            item.status = Self.IN_PATHAO
            found.append(item.code)
            if hits.count > 1 {
                let orderList = hits.map { $0.consignment + " " + $0.phone }.joined(separator: ", ")
                log.add("\(item.code): ⚠ \(hits.count) Pathao orders of the current round have this code (\(orderList)). The newest is shown.")
            }
            if writePhone, let phone = phones.first, let changed = OrderParser.writePhoneToList(text, code: item.code, phone: phone) {
                text = changed
                written += 1
            }
        }

        if written > 0 {
            listText = text
            if let url = listURL {
                do {
                    try Storage.writeText(url, text)
                } catch {
                    log.add("⚠ could not write the phones in \(listName) (\(error.localizedDescription)) - type them there yourself.")
                    written = 0
                }
            }
        }
        var summary = "Read \(orders.count) Pathao orders of the last \(Self.checkDays) days; \(found.count) of the \(items.count) codes are among them"
        if written > 0 { summary += ", phone written next to \(written) in \(listName)" }
        log.add(summary + ".")
        if !missing.isEmpty { log.add("Not in Pathao (\(missing.count)): \(missing.joined(separator: ", "))") }
        state = "\(found.count) of \(items.count) codes are already in Pathao; \(missing.count) are not."
    }

    // MARK: - WhatsApp

    /// Reads the prefix's chat once and caches every order message in it (newest wins).
    private func readChat(_ prefix: String) async throws {
        let name = chatFor(prefix)
        try await WaEngine.shared.awaitReady { [self] in state = $0 }
        if WaEngine.shared.chats.isEmpty { try await WaEngine.shared.refreshChats() }
        let want = WaEngine.shared.normName(name)
        guard let chat = WaEngine.shared.chats.first(where: { WaEngine.shared.normName($0.title) == want }) else {
            throw NeedUser("WhatsApp has no chat called '\(name)'. Fix the chat name for \(prefix)- in the settings.")
        }
        state = "Reading WhatsApp chat '\(chat.title)'…"
        let since = Int((Calendar.current.date(byAdding: .day, value: -lookbackDays, to: Date()) ?? Date()).timeIntervalSince1970)
        let opts = JSONTools.string(jsonOf(("since", since), ("maxLoads", 400)))
        let res = try await WaEngine.shared.withTurn("Pathao Auto-fill", { [self] in state = $0 }) {
            (try await WaEngine.shared.call("window.__wab.messages(\(WaEngine.shared.quote(chat.id)), \(opts))", timeout: 30 * 60) as? JSON) ?? [:]
        }
        if res.has("error") { throw NeedUser("\(chat.title): \(res.str("error"))") }
        let msgs = res.objs("messages")
        for m in msgs.reversed() {   // newest first, so the newest message of a code wins
            let text = m.str("text")
            for code in OrderParser.findCodes(text) {
                if OrderParser.splitCode(code).0 != prefix { continue }
                var order = OrderParser.parseOrder(text)
                order.code = code
                if order.phone.isEmpty && order.name.isEmpty && order.address.isEmpty { continue }
                let prev = cache[code]
                if let prev, prev.0 != m.str("id"), !order.phone.isEmpty, !prev.2.phone.isEmpty, order.phone != prev.2.phone {
                    duplicates[code, default: []].formUnion([order.phone, prev.2.phone])
                }
                if prev == nil || (!order.phone.isEmpty && prev!.2.phone.isEmpty) {
                    cache[code] = (m.str("id"), text, order)
                }
            }
        }
        readPrefixes.insert(prefix)
        log.add("Read \(msgs.count) messages of '\(chat.title)' (last \(lookbackDays) days).")
    }

    // MARK: - the flow

    func start() {
        if items.isEmpty {
            state = "Open or paste your code list first."
            return
        }
        saveSettings()
        running = true
        attention = ""
        if job != nil { return }
        Jobs.shared.begin("pathao", "Pathao Auto-fill is running")
        job = Task {
            do {
                try await loop()
            } catch is CancellationError {
                // paused
            } catch let e as NeedUser {
                attention = e.message
                state = "Waiting for you."
                running = false
            } catch {
                attention = "Error: \(error.localizedDescription)"
                log.add("Error: \(error.localizedDescription)")
                running = false
            }
            job = nil
            Jobs.shared.end("pathao")
        }
    }

    private func loop() async throws {
        while running {
            try Task.checkCancellation()
            if waiting {
                try await checkSaved()
                try await Task.sleep(nanoseconds: 700_000_000)
                continue
            }
            let next: Item?
            if let c = cur, c.status == Self.PENDING || c.status == Self.FETCHING { next = c } else { next = items.first { $0.status == Self.PENDING } }
            guard let next else {
                running = false
                state = "All codes in the list are done."
                log.add("Reached the end of the list.")
                return
            }
            try await process(next, prepare: true, explicit: false)
        }
    }

    private func process(_ item: Item, prepare: Bool, explicit: Bool) async throws {
        if let c = cur, c !== item, c.status == Self.FILLED { c.status = Self.PENDING }
        cur = item
        waiting = false
        item.status = Self.FETCHING
        let prefix = OrderParser.splitCode(item.code).0
        do {
            if explicit || !readPrefixes.contains(prefix) { try await readChat(prefix) }
        } catch {
            item.status = Self.PENDING
            cur = nil
            throw error
        }
        guard let rec = cache[item.code] else {
            item.status = Self.NOT_FOUND
            cur = nil
            log.add("\(item.code): no message starting with \(item.code) in '\(chatFor(prefix))' (last \(lookbackDays) days). Skipped.")
            return
        }
        item.order = rec.2
        item.raw = rec.1
        for w in rec.2.warnings { log.add("\(item.code): ⚠ \(w) - check it before saving.") }
        if let d = duplicates[item.code] {
            log.add("\(item.code): ⚠ more than one order message with this code (phones \(d.sorted().joined(separator: ", "))). The newest was used - check it.")
        }
        do {
            try await fill(item, prepare: prepare)
        } catch {
            item.status = Self.PENDING
            cur = nil
            throw error
        }
        item.status = Self.FILLED
        await arm()
        state = "\(item.code) is filled in Pathao. Check it, finish the form and tap Save; the next code fills itself after the save."
    }

    func pause() {
        running = false
        waiting = false
        job?.cancel()
        state = "Paused. Press Start to continue."
    }

    func skip() {
        guard let item = cur ?? items.first(where: { $0.status == Self.PENDING }) else { return }
        item.status = Self.SKIPPED
        log.add("\(item.code): skipped.")
        cur = nil
        waiting = false
        if !running { start() }
    }

    func markSaved() {
        guard let item = cur else {
            log.add("No code is on the Pathao form right now.")
            return
        }
        recordSaved(item)
        cur = nil
        waiting = false
        if !running { start() }
    }

    /// Fill the form again with the (edited) values of the current code.
    func refill(_ order: OrderParser.Order) {
        guard let item = cur else { return }
        item.order = order
        Task {
            do {
                try await fill(item, prepare: false)
                item.status = Self.FILLED
                await arm()
                state = "\(item.code) is filled with your values. Tap Save there."
                if !running { start() }
            } catch {
                attention = error.localizedDescription
            }
        }
    }

    /// Read the code's order message again (the chat is read fresh).
    func reread(_ item: Item) {
        let old = job
        pause()
        Task {
            _ = await old?.value
            item.status = Self.PENDING
            readPrefixes.remove(OrderParser.splitCode(item.code).0)
            cur = item
            start()
        }
    }
}

/// Pathao stays in its view; other sites (help links) open in Safari.
final class PathaoDelegate: NSObject, WKNavigationDelegate, WKUIDelegate {
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        // target=_blank: open it in the same view
        if let url = navigationAction.request.url { webView.load(URLRequest(url: url)) }
        return nil
    }
}
