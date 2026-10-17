import Foundation
import Observation
import UIKit
import WebKit

struct WaError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct WaChat: Identifiable, Hashable {
    let id: String
    let title: String
    let t: Int
    var isGroup: Bool { id.hasSuffix("@g.us") }
}

/// Resumes a continuation once: with the result, a timeout or a cancel, whichever comes first.
final class Once<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var cont: CheckedContinuation<T, Error>?
    private var early: Result<T, Error>?
    private var finished = false

    func attach(_ c: CheckedContinuation<T, Error>) {
        lock.lock()
        if let r = early {
            early = nil
            finished = true
            lock.unlock()
            c.resume(with: r)
            return
        }
        cont = c
        lock.unlock()
    }

    func resume(_ r: Result<T, Error>) {
        lock.lock()
        if finished {
            lock.unlock()
            return
        }
        if let c = cont {
            cont = nil
            finished = true
            lock.unlock()
            c.resume(with: r)
        } else {
            if early == nil { early = r }
            lock.unlock()
        }
    }
}

/// The one WhatsApp Web that every tool uses (core/whatsapp.py of the Windows app). It runs in a
/// WKWebView inside the app that says it is desktop Safari, linked to your WhatsApp once with
/// "Link with phone number". The Windows app's scripts (wa_bridge.js, wa_bridge_reactions.js) are
/// injected unchanged and read chats, reactions and photos through WhatsApp Web's own modules.
///
/// The web view lives for the whole app; the shell keeps it on screen behind the tools (so it
/// keeps running) and in front on the WhatsApp page.
@MainActor @Observable
final class WaEngine {
    static let shared = WaEngine()
    static let url = URL(string: "https://web.whatsapp.com/")!

    enum Phase { case off, loading, qr, ready, error }

    private(set) var phase: Phase = .off
    private(set) var message = "Not opened yet"
    private(set) var chats: [WaChat] = []
    private(set) var version = ""
    /// The tool that holds WhatsApp right now (only one reads chats at a time).
    private(set) var holder = ""

    @ObservationIgnored private(set) var webView: WKWebView?
    @ObservationIgnored private var delegate: WaDelegate?
    @ObservationIgnored private var sources: [(String, String)] = []   // window name -> script
    @ObservationIgnored private var poller: Task<Void, Never>?
    @ObservationIgnored private var readySince: Date?
    @ObservationIgnored private var loadingSince: Date?
    @ObservationIgnored private var reloadedOnce = false
    @ObservationIgnored private var wanted = false
    @ObservationIgnored private var pending: [UUID: Once<String?>] = [:]
    @ObservationIgnored private var turnBusy = false
    @ObservationIgnored private var turnWaiters: [CheckedContinuation<Void, Never>] = []
    @ObservationIgnored var console: [String] = []
    @ObservationIgnored var lastURL = ""

    var isReady: Bool { phase == .ready }

    private init() {
        func asset(_ name: String) -> String {
            guard let url = Bundle.main.url(forResource: name, withExtension: nil),
                  let text = try? String(contentsOf: url, encoding: .utf8) else { return "" }
            return text
        }
        // As in the Windows app, each bridge gets its own name in the page.
        sources = [
            ("__waf_basic", asset("wa_bridge.js").replacingOccurrences(of: "window.__waf", with: "window.__waf_basic")),
            ("__waf_reactions", asset("wa_bridge_reactions.js").replacingOccurrences(of: "window.__waf", with: "window.__waf_reactions")),
            ("__wab", asset("boost_bridge.js")),
        ]
    }

    // MARK: - the web view

    static let desktopUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) " +
        "Version/18.0 Safari/605.1.15"

    /// The web view for the shell's layout (made on first use).
    func view() -> WKWebView {
        if let v = webView { return v }
        let d = WaDelegate(self)
        delegate = d
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = .default()
        cfg.limitsNavigationsToAppBoundDomains = true
        cfg.defaultWebpagePreferences.preferredContentMode = .desktop
        cfg.allowsInlineMediaPlayback = true
        cfg.mediaTypesRequiringUserActionForPlayback = .all
        let ucc = WKUserContentController()
        ucc.addUserScript(WKUserScript(source: Self.desktopViewport, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.addUserScript(WKUserScript(source: Self.consoleHook, injectionTime: .atDocumentStart, forMainFrameOnly: true))
        ucc.add(d, name: "boostLog")
        cfg.userContentController = ucc
        let v = WKWebView(frame: CGRect(x: 0, y: 0, width: 400, height: 700), configuration: cfg)
        v.customUserAgent = Self.desktopUserAgent
        v.navigationDelegate = d
        v.uiDelegate = d
        v.allowsBackForwardNavigationGestures = false
        if #available(iOS 16.4, *) { v.isInspectable = true }
        webView = v
        if wanted { load() }
        return v
    }

    // Runs before WhatsApp Web's own scripts (and again after each page load):
    // - WhatsApp's login page needs a PC's width, and WhatsApp puts its own viewport tag back, so
    //   every viewport tag is kept at a PC's width.
    // - WhatsApp asks for permanent ("persisted") storage; in an app's web view the answer can be
    //   no, and WhatsApp then never connects. The data is kept anyway, so the request is answered yes.
    static let desktopViewport = """
(function(){
  if (window.__boostPatched) return; window.__boostPatched = true;
  var W = 'width=1100';
  var fix = function(){
    var ms = document.querySelectorAll('meta[name=viewport]');
    if (!ms.length && (document.head || document.documentElement)) {
      var m = document.createElement('meta'); m.name = 'viewport'; m.content = W;
      (document.head || document.documentElement).appendChild(m); return;
    }
    for (var i = 0; i < ms.length; i++) if (ms[i].content !== W) ms[i].content = W;
  };
  fix();
  try { new MutationObserver(fix).observe(document.documentElement || document, {subtree: true, childList: true, attributes: true, attributeFilter: ['content']}); } catch (e) {}
  document.addEventListener('DOMContentLoaded', fix);
  try {
    var st = navigator.storage;
    if (st) {
      st.persist = function(){ return Promise.resolve(true); };
      st.persisted = function(){ return Promise.resolve(true); };
    }
  } catch (e) {}
})();void 0;
"""

    // Errors and warnings of the page, for the Details button.
    static let consoleHook = """
(function(){
  if (window.__boostConsole) return; window.__boostConsole = true;
  var post = function(level, args){
    try {
      var text = Array.prototype.map.call(args, function(a){ try { return typeof a === 'string' ? a : (a && a.message) || JSON.stringify(a); } catch (e) { return String(a); } }).join(' ');
      window.webkit.messageHandlers.boostLog.postMessage(level + ' ' + text.slice(0, 300));
    } catch (e) {}
  };
  ['error', 'warn'].forEach(function(level){
    var orig = console[level];
    console[level] = function(){ post(level.charAt(0).toUpperCase(), arguments); return orig.apply(console, arguments); };
  });
  window.addEventListener('error', function(e){ post('E', [e.message + ' (' + (e.filename || '').split('/').pop() + ':' + e.lineno + ')']); });
})();void 0;
"""

    private static let pageProbe = """
(function(){var b=document.body;return JSON.stringify({url:location.href,title:document.title,
  text:(b&&b.innerText||'').replace(/\\s+/g,' ').slice(0,600),canvas:document.querySelectorAll('canvas').length,
  width:innerWidth,sw:!!navigator.serviceWorker,ctrl:!!(navigator.serviceWorker&&navigator.serviceWorker.controller),
  req:typeof window.require,ua:navigator.userAgent});})()
"""

    // The login page: a QR code, or the "log in with phone number" steps (no QR canvas there).
    private static let loginProbe = """
(function(){var t=(document.body&&document.body.innerText)||'';
  return !!document.querySelector('canvas') || /phone number|link with|log ?in|scan|enter code|steps to/i.test(t);})()
"""

    func addConsole(_ line: String) {
        console.append(line)
        if console.count > 40 { console.removeFirst(console.count - 40) }
    }

    private func load() {
        guard let v = webView else { return }
        phase = .loading
        message = "Loading WhatsApp Web…"
        readySince = nil
        loadingSince = Date()
        v.load(URLRequest(url: Self.url))
    }

    /// What the page is doing, for the Details button.
    func diagnostics() async -> String {
        guard webView != nil else { return "WhatsApp is not open" }
        let page = (await evalRaw(Self.pageProbe) as? String) ?? "?"
        let st = (try? await status()).map { JSONTools.string($0) } ?? "nil"
        var out = "Boostimise \(Config.appVersion) · iOS \(UIDevice.current.systemVersion) · \(UIDevice.current.model)\n"
        out += "Phase \(phase) · \(message)\n"
        out += "UA: \(Self.desktopUserAgent)\n"
        out += "Last page: \(lastURL)\n"
        out += "Status: \(st)\n"
        out += "Page: \(page)\n"
        out += "Console (\(console.count)):\n"
        for l in console.suffix(25) { out += l + "\n" }
        return out
    }

    /// Opens WhatsApp Web (once) and starts watching its state.
    func open() {
        wanted = true
        if webView != nil && (phase == .off || phase == .error) { load() }
        if poller == nil { poller = Task { await watch() } }
    }

    func reload(clearCache: Bool = false) {
        wanted = true
        if clearCache {
            WKWebsiteDataStore.default().removeData(ofTypes: [WKWebsiteDataTypeDiskCache, WKWebsiteDataTypeMemoryCache],
                                                     modifiedSince: .distantPast) {}
        }
        load()
        message = "Reloading WhatsApp Web…"
        if poller == nil { poller = Task { await watch() } }
    }

    /// Signs this WhatsApp Web out (WhatsApp > Linked devices on the phone also does that).
    func forgetLogin() {
        let store = WKWebsiteDataStore.default()
        store.fetchDataRecords(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes()) { records in
            let wa = records.filter { $0.displayName.lowercased().contains("whatsapp") }
            store.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), for: wa) {
                Task { @MainActor in
                    WaEngine.shared.chats = []
                    WaEngine.shared.reload()
                }
            }
        }
    }

    func pageStarted() {
        failPending("WhatsApp Web reloaded")
    }

    // MARK: - state

    private func watch() async {
        while !Task.isCancelled {
            let st = try? await status()
            let ready = st?.bool("ready") ?? false
            var qr = st?.bool("qr") ?? false
            if !ready && !qr { qr = (await evalRaw(Self.loginProbe) as? Bool) ?? false }
            let now = Date()
            if ready, let st {
                if readySince == nil {
                    readySince = now
                    phase = .loading
                    message = "Logged in, loading chats…"
                }
                let waited = now.timeIntervalSince(readySince ?? now)
                if phase != .ready && ((st.int("chats") > 0 && waited > 3) || waited > 20) {
                    version = st.str("version")
                    phase = .ready
                    message = "Connected"
                    try? await refreshChats()
                }
                loadingSince = nil
            } else {
                readySince = nil
                if qr {
                    phase = .qr
                    message = "Not linked yet: tap \"Log in with phone number\" below"
                    loadingSince = nil
                } else {
                    if phase == .ready || phase == .qr {
                        phase = .loading
                        message = "Loading WhatsApp Web…"
                    }
                    if loadingSince == nil { loadingSince = now }
                    // stuck on the logo with neither a QR code nor chats: clear the cache and reload, once
                    if !reloadedOnce, let since = loadingSince, now.timeIntervalSince(since) > 45 {
                        reloadedOnce = true
                        reload(clearCache: true)
                    }
                }
            }
            try? await Task.sleep(nanoseconds: phase == .ready ? 2_500_000_000 : 1_000_000_000)
        }
    }

    func refreshChats() async throws {
        let arr = objects(try await call("window.__waf_basic.listChats()"))
        chats = arr.map { WaChat(id: $0.str("id"), title: $0.str("title"), t: $0.int("t")) }
    }

    private func status() async throws -> JSON? {
        try await call("window.__waf_basic.status()", timeout: 8) as? JSON
    }

    /// Waits until WhatsApp Web is linked and loaded; `onWait` gets what it is waiting for.
    func awaitReady(_ onWait: (String) -> Void = { _ in }) async throws {
        open()
        var told = ""
        while phase != .ready {
            try Task.checkCancellation()
            let msg = phase == .qr ? "WhatsApp is not linked yet - open the WhatsApp page and link it"
                : "Waiting for WhatsApp Web to load…"
            if msg != told {
                told = msg
                onWait(msg)
            }
            try await Task.sleep(nanoseconds: 1_000_000_000)
        }
    }

    /// Runs `block` holding WhatsApp; another tool waits meanwhile (core/whatsapp.py's turn()).
    func withTurn<T>(_ tool: String, _ onWait: (String) -> Void, _ block: () async throws -> T) async throws -> T {
        if turnBusy {
            onWait("Waiting for \(holder) to finish with WhatsApp…")
            while turnBusy {
                await withCheckedContinuation { turnWaiters.append($0) }
            }
            try Task.checkCancellation()
        }
        turnBusy = true
        holder = tool
        defer {
            holder = ""
            turnBusy = false
            if !turnWaiters.isEmpty { turnWaiters.removeFirst().resume() }
        }
        return try await block()
    }

    // MARK: - calling the bridges

    /// Evaluates a script and returns its value (nil on error).
    func evalRaw(_ js: String) async -> Any? {
        guard let v = webView else { return nil }
        return await withCheckedContinuation { (cont: CheckedContinuation<Any?, Never>) in
            v.evaluateJavaScript(js) { result, _ in cont.resume(returning: result) }
        }
    }

    private func ensureBridges() async {
        let probe = "[" + sources.map { "typeof window.\($0.0)" }.joined(separator: ",") + "].join(',')"
        guard let res = await evalRaw(probe) as? String else { return }
        let types = res.split(separator: ",", omittingEmptySubsequences: false).map(String.init)
        for (i, (_, src)) in sources.enumerated() where (i < types.count ? types[i] : "") != "object" {
            _ = await evalRaw(src + "\n;void 0;")
        }
    }

    static func jsMessage(_ error: Error) -> String {
        let ns = error as NSError
        if let m = ns.userInfo["WKJavaScriptExceptionMessage"] as? String, !m.isEmpty {
            return m.hasPrefix("Error: ") ? String(m.dropFirst(7)) : m
        }
        return error.localizedDescription
    }

    /// Evaluates a JavaScript expression (it may return a Promise) and returns its JSON value.
    func call(_ expr: String, timeout: TimeInterval = 120) async throws -> Any? {
        guard let v = webView else { throw WaError("WhatsApp is not open") }
        await ensureBridges()
        let body = "const __r = await (\(expr));\nreturn JSON.stringify(__r === undefined ? null : __r);"
        let id = UUID()
        let once = Once<String?>()
        pending[id] = once
        defer { pending[id] = nil }
        let text: String? = try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<String?, Error>) in
                once.attach(cont)
                v.callAsyncJavaScript(body, arguments: [:], in: nil, in: .page) { result in
                    switch result {
                    case .success(let value): once.resume(.success(value as? String))
                    case .failure(let error): once.resume(.failure(WaError(WaEngine.jsMessage(error))))
                    }
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
                    once.resume(.failure(WaError("WhatsApp Web did not answer in time")))
                }
            }
        } onCancel: {
            once.resume(.failure(CancellationError()))
        }
        return JSONTools.parse(text)
    }

    func quote(_ s: String) -> String { JSONTools.quote(s) }

    private func failPending(_ reason: String) {
        for (_, o) in pending { o.resume(.failure(WaError(reason))) }
        pending.removeAll()
    }

    /// Chat titles compared the way WhatsApp's search does for the Pathao tool:
    /// case, spaces, brackets and emoji ignored.
    func normName(_ s: String) -> String {
        Self.notNameChars.replace(s.lowercased(), " ").trimmed
    }

    private static let notNameChars = Rx(#"[^\p{L}\p{M}\p{N}]+"#)
}

/// The web view's delegate (WebKit needs an NSObject).
final class WaDelegate: NSObject, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    private weak var engine: WaEngine?

    init(_ engine: WaEngine) {
        self.engine = engine
    }

    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        MainActor.assumeIsolated { engine?.pageStarted() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        MainActor.assumeIsolated { engine?.lastURL = webView.url?.absoluteString ?? "" }
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        // WhatsApp Web stays here; a link to another site (from a chat) opens in Safari
        guard let url = navigationAction.request.url, let scheme = url.scheme?.lowercased() else {
            decisionHandler(.allow)
            return
        }
        let host = url.host?.lowercased() ?? ""
        let main = navigationAction.targetFrame?.isMainFrame ?? true
        if !main || !["http", "https"].contains(scheme) || host.hasSuffix("whatsapp.com") || host.hasSuffix("whatsapp.net") {
            decisionHandler(["http", "https", "about", "blob", "data"].contains(scheme) || !main ? .allow : .cancel)
            return
        }
        UIApplication.shared.open(url)
        decisionHandler(.cancel)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
            UIApplication.shared.open(url)
        }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        MainActor.assumeIsolated { engine?.reload() }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if let s = message.body as? String {
            MainActor.assumeIsolated { engine?.addConsole(s) }
        }
    }
}
