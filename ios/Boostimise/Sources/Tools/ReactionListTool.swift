import Foundation
import Observation

/// The reaction choices (tools/reaction_list/helpers.py). 😢 counts as 😥.
struct Choice: Hashable {
    let key: String
    let label: String
    let emojis: [String]
}

let CHOICES = [
    Choice(key: "x", label: "❌", emojis: ["❌"]),
    Choice(key: "nophone", label: "📵", emojis: ["📵"]),
    Choice(key: "sad", label: "😥", emojis: ["😥", "😢"]),
    Choice(key: "all", label: "❌ 📵 😥", emojis: ["❌", "📵", "😥", "😢"]),
]

enum ReactionCodes {
    // A date such as 16-10-2026 or 2026-10-16 is not a code.
    private static let date = Rx(#"(?<![0-9A-Za-z])(?:(?:[12]?\d|3[01])[-/.](?:0?[1-9]|1[0-2])[-/.](?:\d{4}|\d{2})"# +
        #"|\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?![0-9A-Za-z])"#)
    // One code of a list: "2-558", "2:558+", "5A_380" ...
    private static let code = Rx(#"\s*("# + ProductCode.prefix + #")\s*"# + ProductCode.sep + #"\s*(\d{1,6})[\s+\-]*"#,
                                 ignoreCase: true)
    private static let numAndRest = Rx(#"(\d+)(.*)"#)
    private static let maxCodes = 3

    static func normalize(_ c: String) -> String {
        guard let m = code.matchEntire(c) else { return c.trimmed.lowercased() }
        return OrderParser.normalizeCode(m.group(1), m.group(2))
    }

    /// The codes a reacted message is about, if it names no more than maxCodes.
    static func messageCodes(_ text: String) -> [String] {
        let written = ProductCode.codesInText(date.replace(bnToAscii(text), " "))
        var codes: [String] = []
        for c in OrderParser.findCodes(text) + written where !codes.contains(c) { codes.append(c) }
        return codes.count <= maxCodes ? codes : []
    }

    /// 2-558 < 2-560 < 3-12 < 5a-380.
    static func less(_ a: String, _ b: String) -> Bool {
        let ka = key(a), kb = key(b)
        if ka.0 != kb.0 { return ka.0 < kb.0 }
        if ka.1 != kb.1 { return ka.1 < kb.1 }
        return ka.2 < kb.2
    }

    private static func key(_ c: String) -> (Int, String, Int) {
        guard let m = code.matchEntire(c), let p = numAndRest.matchEntire(m.group(1)) else { return (1_000_000, c, 0) }
        return (Int(p.group(1)) ?? 0, p.group(2).lowercased(), Int(m.group(2)) ?? 0)
    }
}

/// Reaction List (tools/reaction_list): the codes whose messages got ❌, 📵 or 😥, with the phone
/// number from the order message. Empty code box = every reacted code in the chats read.
@MainActor @Observable
final class ReactionListTool {
    final class Found: Identifiable {
        let id = UUID()
        let code: String
        let name: String
        let order: Int
        var emojis = Set<String>()
        var phones: [String] = []
        var chats: [String] = []
        var t = 0

        init(code: String, name: String, order: Int) {
            self.code = code
            self.name = name
            self.order = order
        }

        func react(_ wanted: [String]) -> String { wanted.filter { emojis.contains($0) }.joined() }
    }

    @ObservationIgnored private let prefs = Storage.prefs("reaction_list")
    var codeText = ""
    var choice: Choice
    var lookbackDays: Int
    var chat: ChatFilter
    private(set) var results: [Found] = []
    let log = ToolLog()
    private(set) var running = false
    private(set) var activity = ""
    private(set) var progress = 0.0
    private(set) var summary = ""
    @ObservationIgnored private var job: Task<Void, Never>?

    init() {
        let p = Storage.prefs("reaction_list")
        let key = p.string("choice", "all")
        choice = CHOICES.first { $0.key == key } ?? CHOICES[3]
        lookbackDays = p.int("lookback_days", 10)
        chat = ChatFilter(id: p.string("chat_id"), title: p.string("chat_title"), groupsOnly: p.bool("groups_only", false))
    }

    private func save() {
        prefs.set("choice", choice.key)
        prefs.set("lookback_days", lookbackDays)
        prefs.set("chat_id", chat.id)
        prefs.set("chat_title", chat.title)
        prefs.set("groups_only", chat.groupsOnly)
    }

    func stop() { job?.cancel() }

    /// '1. 2-558 Realme C85 ❌ 01734484040', one code per line.
    func formatList() -> String {
        let lines = results.enumerated().map { n, f in
            "\(n + 1). " + [f.code, f.name, f.react(choice.emojis), f.phones.joined(separator: ", ")].filter { !$0.isEmpty }
                .joined(separator: " ")
        }
        return lines.joined(separator: "\n") + (results.isEmpty ? "" : "\n")
    }

    func start() {
        if running { return }
        save()
        results = []
        summary = ""
        running = true
        Jobs.shared.begin("reaction_list", "Reaction List: starting…")
        job = Task {
            do {
                try await WaEngine.shared.awaitReady { [self] in activity = $0 }
                try await WaEngine.shared.withTurn("Reaction List", { [self] in activity = $0 }) { try await run() }
            } catch is CancellationError {
                log.add("Stopped by user.")
                summary = "Stopped."
            } catch {
                log.add("Error: \(error.localizedDescription)")
                summary = "Error: \(error.localizedDescription)"
            }
            running = false
            activity = ""
            Jobs.shared.end("reaction_list")
        }
    }

    private func run() async throws {
        let started = Date()
        let items = Codes.parseCodeList(codeText)
        var listed: [String: (Int, CodeItem)] = [:]
        for (i, it) in items.enumerated() { listed[ReactionCodes.normalize(it.code)] = (i + 1, it) }
        let wanted = Set(listed.keys)
        let reactions = choice.emojis.joined()
        let since = Codes.scanSince(items, lookbackDays: lookbackDays)
        let sinceTs = Int(since.timeIntervalSince1970)
        let targets = objects(try await WaEngine.shared.call("window.__waf_reactions.scanTargets(\(chat.toJs(sinceTs)))"))
        let what = items.isEmpty ? "every code" : "\(items.count) code(s)"
        log.add("Reading \(targets.count) chat(s) active since \(dateText(since, "dd/MM/yyyy")): \(what) reacted with \(reactions)…")
        let opts = JSONTools.string(jsonOf(("since", sinceTs), ("reactions", reactions), ("gapMinutes", 5), ("maxLoads", 300)))
        var byCode: [String: Found] = [:]
        var noCode = 0
        var orphans = 0
        for (n, target) in targets.enumerated() {
            try Task.checkCancellation()
            let title = target.str("title").ifEmpty(target.str("id"))
            activity = "Reading chat \(n + 1)/\(targets.count): \(title)"
            Jobs.shared.progress("reaction_list", "Reaction List: \(activity)")
            var res: JSON = [:]
            do {
                res = (try await WaEngine.shared.call(
                    "window.__waf_reactions.reactedMessages(\(WaEngine.shared.quote(target.str("id"))), \(opts))",
                    timeout: 30 * 60) as? JSON) ?? [:]
            } catch let e as WaError {
                log.add("\(title): \(e.message)")
            }
            progress = Double(n + 1) / Double(max(targets.count, 1))
            if res.has("error") {
                log.add("\(title): \(res.str("error"))")
                continue
            }
            for w in res.strs("warnings") { log.add("\(title): \(w)") }
            orphans += res.int("orphans")
            var newCodes = Set<String>()
            for hit in res.objs("hits") {
                let text = hit.str("text")
                let inText = ReactionCodes.messageCodes(text)
                if inText.isEmpty { noCode += 1 }
                let phones = OrderParser.findPhones(text)
                for code in inText {
                    if !wanted.isEmpty && !wanted.contains(code) { continue }
                    let entry = listed[code]
                    let f: Found
                    if let existing = byCode[code] {
                        f = existing
                    } else {
                        f = Found(code: entry?.1.code ?? code, name: entry?.1.name ?? "", order: entry?.0 ?? 0)
                        byCode[code] = f
                    }
                    for e in hit.str("react").map({ String($0) }) { f.emojis.insert(e) }
                    for p in phones where !f.phones.contains(p) { f.phones.append(p) }
                    let chatName = hit.str("chat").ifEmpty(title)
                    if !f.chats.contains(chatName) { f.chats.append(chatName) }
                    f.t = max(f.t, hit.int("t"))
                    newCodes.insert(code)
                }
            }
            if !newCodes.isEmpty {
                log.add("\(title): " + newCodes.sorted(by: ReactionCodes.less).joined(separator: ", "))
                let all = Array(byCode.values)
                results = wanted.isEmpty
                    ? all.sorted { ReactionCodes.less(ReactionCodes.normalize($0.code), ReactionCodes.normalize($1.code)) }
                    : all.sorted { $0.order < $1.order }
            }
        }
        if noCode > 0 { log.add("\(noCode) reacted message(s) name no code (left out).") }
        if orphans > 0 { log.add("\(orphans) reacted photo(s) have no code written before them (left out).") }
        let secs = Int(Date().timeIntervalSince(started))
        summary = items.isEmpty ? "Done in \(secs)s: \(byCode.count) code(s) reacted with \(reactions)."
            : "Done in \(secs)s: \(byCode.count) of \(wanted.count) code(s) reacted with \(reactions)."
        log.add(summary)
    }
}
