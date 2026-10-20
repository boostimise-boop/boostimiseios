import CryptoKit
import Foundation
import Observation

/// The log of a tool, newest line last (kept short).
@MainActor @Observable
final class ToolLog {
    private(set) var lines: [String] = []

    func add(_ s: String) {
        lines.append(contentsOf: s.lines())
        if lines.count > 400 { lines.removeFirst(lines.count - 400) }
    }

    func clear() { lines.removeAll() }
}

/// Chat filter: all chats, all groups, one chat (by id) or chats whose name contains a text.
struct ChatFilter: Hashable {
    var id = ""
    var title = ""
    var groupsOnly = false

    func toJs(_ since: Int) -> String {
        JSONTools.string(jsonOf(("since", since), ("chatId", id), ("chatTitle", title), ("groupsOnly", groupsOnly)))
    }

    var label: String {
        if groupsOnly { return "All groups" }
        if !id.isEmpty || !title.isEmpty { return title }
        return "All chats"
    }
}

let NEIGHBOUR_MODES: [(String, String)] = [
    ("after", "Captioned with the code + uncaptioned ones sent right after"),
    ("none", "Only photos whose caption contains the code"),
    ("before", "Captioned with the code + uncaptioned ones sent right before"),
    ("both", "Captioned with the code + uncaptioned ones before and after"),
]

/// The list a code belongs to: "6-" for 6-377, "5a-" for 5A-200, "cc-" for CC1023 ("" if it has no list
/// prefix, like #1023), in the shop's code style.
func listOfCode(_ code: String) -> String {
    guard let (prefix, _) = ProductCode.reader().codeParts(code), !prefix.isEmpty else { return "" }
    return prefix + "-"
}

/// True if a chat name is for this list, e.g. "My Shop (6-)" for "6-" (match_renamer's group_in_name).
func listInName(_ list: String, _ name: String) -> Bool {
    guard list.count > 1 else { return false }
    return Rx("(?<![0-9A-Za-z])" + Rx.escape(String(list.dropLast())) + #"\s*-(?!\s*\d)"#, ignoreCase: true).contains(name)
}

func dateText(_ d: Date, _ format: String) -> String {
    let f = DateFormatter()
    f.locale = Locale(identifier: "en_US_POSIX")
    f.dateFormat = format
    return f.string(from: d)
}

func sha256Hex(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

/// Fetch by Code (tools/whatsapp_fetch): finds the photos of each code in the
/// WhatsApp chats and saves them as "2-558 Realme C85.jpg" in Files › Boostimise › <tool>.
@MainActor @Observable
final class FetchTool {
    @MainActor @Observable
    final class Row: Identifiable {
        let id = UUID()
        let item: CodeItem
        var kind = ""       // "" | working | ok | none | error | skipped
        var text = ""
        var saved = ""
        init(_ item: CodeItem) { self.item = item }
    }

    let key: String
    let title: String
    @ObservationIgnored private let prefs: Prefs
    var codeText = ""
    private(set) var rows: [Row] = []
    let log = ToolLog()
    private(set) var running = false
    private(set) var activity = ""
    private(set) var progress = 0.0
    private(set) var summary = ""
    @ObservationIgnored private var job: Task<Void, Never>?

    // settings (remembered)
    var folder: String
    var chat: ChatFilter
    var neighbors: String
    var gapMinutes: Int
    var lookbackDays: Int
    var includeDate: Bool
    var skipExisting: Bool
    /// Each list in its own chat: the 6- codes in "My Shop (6-)", the 5a- codes in …
    var perList: Bool
    /// The chat of each list ("6-" -> chat); a list without one is searched in `chat`.
    var listChats: [String: ChatFilter] = [:]

    init(key: String, title: String) {
        self.key = key
        self.title = title
        let p = Storage.prefs(key)
        prefs = p
        folder = p.string("folder", title)
        chat = ChatFilter(id: p.string("chat_id"), title: p.string("chat_title"), groupsOnly: p.bool("groups_only", false))
        neighbors = p.string("neighbors", "after")
        gapMinutes = p.int("gap_minutes", 5)
        lookbackDays = p.int("lookback_days", 10)
        includeDate = p.bool("include_date", false)
        skipExisting = p.bool("skip_existing", true)
        perList = p.bool("per_list", false)
        if let saved = JSONTools.parse(p.string("list_chats", "{}")) as? JSON {
            for (k, v) in saved {
                if let o = v as? JSON { listChats[k] = ChatFilter(id: o.str("id"), title: o.str("title")) }
            }
        }
    }

    var folderName: String { folder.trimmed.ifEmpty(title) }

    func saveSettings() {
        var lc = JSON()
        for (k, f) in listChats { lc[k] = jsonOf(("id", f.id), ("title", f.title)) }
        prefs.set("folder", folder)
        prefs.set("chat_id", chat.id)
        prefs.set("chat_title", chat.title)
        prefs.set("groups_only", chat.groupsOnly)
        prefs.set("neighbors", neighbors)
        prefs.set("gap_minutes", gapMinutes)
        prefs.set("lookback_days", lookbackDays)
        prefs.set("include_date", includeDate)
        prefs.set("skip_existing", skipExisting)
        prefs.set("per_list", perList)
        prefs.set("list_chats", JSONTools.string(lc))
    }

    /// The lists in the code list, in order, with how many codes each has: [("6-", 7), ("5a-", 22), …].
    func lists() -> [(String, Int)] {
        var order: [String] = []
        var count: [String: Int] = [:]
        for r in rows {
            let l = listOfCode(r.item.code)
            if l.isEmpty { continue }
            if count[l] == nil { order.append(l) }
            count[l, default: 0] += 1
        }
        return order.map { ($0, count[$0] ?? 0) }
    }

    /// Where a list's codes are searched.
    func chatFor(_ list: String) -> ChatFilter { perList ? (listChats[list] ?? chat) : chat }

    /// Gives each list without a chat the chat named for it ("… (6-)"), the most recent one if several.
    func autoFillListChats() {
        let chats = WaEngine.shared.chats
        if chats.isEmpty { return }
        for (list, _) in lists() {
            if let now = listChats[list], chats.contains(where: { $0.id == now.id }) { continue }
            guard let match = chats.filter({ listInName(list, $0.title) }).max(by: { $0.t < $1.t }) else { continue }
            listChats[list] = ChatFilter(id: match.id, title: match.title)
        }
    }

    func parse() {
        let items = Codes.parseCodeList(codeText)
        var old: [CodeItem: Row] = [:]
        for r in rows { old[r.item] = r }
        rows = items.map { old[$0] ?? Row($0) }
    }

    func stop() { job?.cancel() }

    func start(only: [Row]? = nil) {
        if running { return }
        parse()
        if rows.isEmpty {
            summary = "Paste your code list first."
            return
        }
        saveSettings()
        let todoRows = only ?? rows
        for r in todoRows {
            r.kind = ""
            r.text = "Queued"
            r.saved = ""
        }
        running = true
        summary = ""
        Jobs.shared.begin(key, "\(title): starting…")
        job = Task {
            do {
                try await WaEngine.shared.awaitReady { [self] in
                    activity = $0
                    Jobs.shared.progress(key, "\(title): \($0)")
                }
                try await WaEngine.shared.withTurn(title, { [self] in activity = $0 }) { try await runJob(todoRows) }
            } catch is CancellationError {
                log.add("Stopped by user.")
                summary = "Stopped."
            } catch {
                log.add("Error: \(error.localizedDescription)")
                summary = "Error: \(error.localizedDescription)"
            }
            for r in todoRows where r.kind == "working" || r.text == "Queued" {
                r.kind = ""
                r.text = "Stopped"
            }
            running = false
            activity = ""
            Jobs.shared.end(key)
        }
    }

    private func runJob(_ todoRows: [Row]) async throws {
        let started = Date()
        var ok = 0, none = 0, skipped = 0, errors = 0, files = 0
        let existing = Storage.namesIn(folderName)
        var todo: [(Row, String)] = []
        for r in todoRows {
            let base = r.item.baseName(includeDate: includeDate)
            if skipExisting, let prev = Codes.existingOutput(existing, base) {
                r.kind = "skipped"
                r.text = "Already saved"
                r.saved = prev
                skipped += 1
            } else {
                todo.append((r, base))
            }
        }
        if !todo.isEmpty {
            let scan = try await self.scan(todo)
            for (i, pair) in todo.enumerated() {
                let (row, base) = pair
                let images = (scan.found[String(i)] ?? [:]).values.sorted { $0.int("t") < $1.int("t") }
                var kind = "error"
                var n = 0
                do {
                    (kind, n) = try await downloadAndSave(row, base, images, mentions: scan.mentions[String(i)] ?? 0,
                                                          since: scan.since)
                } catch is CancellationError {
                    throw CancellationError()
                } catch {
                    row.kind = "error"
                    row.text = "Error: \(error.localizedDescription)"
                    log.add("[\(row.item.code)] error: \(error.localizedDescription)")
                }
                switch kind {
                case "ok": ok += 1
                case "none": none += 1
                default: errors += 1
                }
                files += n
                progress = Double(scan.chats + i + 1) / Double(scan.chats + todo.count)
            }
        }
        let secs = Int(Date().timeIntervalSince(started))
        summary = "Done in \(secs)s: \(ok) code(s) saved (\(files) images), \(none) not found, \(skipped) skipped, \(errors) error(s)."
        log.add(summary)
    }

    private struct Scan {
        var found: [String: [String: JSON]]
        var mentions: [String: Int]
        var since: Date
        var chats: Int
    }

    private func scan(_ todo: [(Row, String)]) async throws -> Scan {
        let since = Codes.scanSince(todo.map { $0.0.item }, lookbackDays: lookbackDays)
        let sinceTs = Int(since.timeIntervalSince1970)
        let api = "window.__waf_basic"
        let dd = dateText(since, "dd/MM/yyyy")
        // One pass per chat filter: all codes in `chat`, or with perList each list's codes in its own chat.
        var groupOrder: [ChatFilter] = []
        var groups: [ChatFilter: [Int]] = [:]
        for i in todo.indices {
            let f = chatFor(listOfCode(todo[i].0.item.code))
            if groups[f] == nil { groupOrder.append(f) }
            groups[f, default: []].append(i)
        }
        var passes: [(ChatFilter, [Int], [JSON])] = []
        for f in groupOrder {
            let targets = objects(try await WaEngine.shared.call("\(api).scanTargets(\(f.toJs(sinceTs)))"))
            passes.append((f, groups[f] ?? [], targets))
        }
        let chatCount = passes.reduce(0) { $0 + $1.2.count }
        let total = chatCount + todo.count
        progress = 0
        log.add("Reading \(chatCount) chat(s) active since \(dd) for \(todo.count) code(s)…")
        for (r, _) in todo {
            r.kind = "working"
            r.text = "Reading chats…"
        }

        var found: [String: [String: JSON]] = [:]
        var mentions: [String: Int] = [:]
        var short = 0
        // (chat, the options with that pass's codes) for every chat to read
        var reads: [(JSON, String)] = []
        for (filter, keys, targets) in passes {
            if perList {
                var names: [String] = []
                for i in keys {
                    let n = listOfCode(todo[i].0.item.code).ifEmpty("other")
                    if !names.contains(n) { names.append(n) }
                }
                log.add("\(names.joined(separator: ", ")) (\(keys.count) code(s)) → \(filter.label)" +
                    (targets.isEmpty ? ": not active since \(dd)" : ""))
            }
            let codes: [JSON] = keys.map { jsonOf(("key", String($0)), ("pattern", Codes.codePattern(todo[$0].0.item.code))) }
            let opts = JSONTools.string(ProductCode.scanPatterns(jsonOf(("codes", codes), ("since", sinceTs), ("neighbors", neighbors),
                                                                        ("gapMinutes", gapMinutes), ("maxNeighbors", 30), ("maxLoads", 300))))
            for t in targets { reads.append((t, opts)) }
        }
        for (n, read) in reads.enumerated() {
            try Task.checkCancellation()
            let (target, opts) = read
            let t = target.str("title").ifEmpty(target.str("id"))
            activity = "Reading chat \(n + 1)/\(chatCount): \(t)"
            Jobs.shared.progress(key, "\(title): \(activity)")
            var res: JSON = [:]
            do {
                res = (try await WaEngine.shared.call("\(api).scanChat(\(WaEngine.shared.quote(target.str("id"))), \(opts))",
                                                      timeout: 30 * 60) as? JSON) ?? [:]
            } catch let e as WaError {
                log.add("\(t): \(e.message)")
            }
            if res.has("error") {
                log.add("\(t): \(res.str("error"))")
                continue
            }
            for w in res.strs("warnings") { log.add("\(t): \(w)") }
            let hits = res.obj("hits") ?? [:]
            let hitKeys = Array(hits.keys).sorted { (Int($0) ?? 0) < (Int($1) ?? 0) }
            for k in hitKeys {
                var bucket = found[k] ?? [:]
                for img in hits.objs(k) where bucket[img.str("id")] == nil { bucket[img.str("id")] = img }
                found[k] = bucket
                if let idx = Int(k), idx < todo.count { todo[idx].0.text = "Reading chats… \(bucket.count) photo(s) found" }
            }
            for (k, v) in res.obj("mentions") ?? [:] { mentions[k, default: 0] += (v as? NSNumber)?.intValue ?? 0 }
            if !hitKeys.isEmpty {
                log.add("\(t): photos for " + hitKeys.compactMap { Int($0) }.filter { $0 < todo.count }.map { todo[$0].0.item.code }
                    .joined(separator: ", "))
            }
            if res.int("loaded") > 0 && !res.bool("reached") && res.bool("exhausted") { short += 1 }
            progress = Double(n + 1) / Double(max(total, 1))
        }
        if short > 0 { log.add("Note: \(short) chat(s) have no older messages on WhatsApp Web (before \(dd)).") }
        return Scan(found: found, mentions: mentions, since: since, chats: chatCount)
    }

    private func downloadAndSave(_ row: Row, _ base: String, _ images: [JSON], mentions: Int,
                                 since: Date) async throws -> (String, Int) {
        let code = row.item.code
        if images.isEmpty {
            let detail: String
            if mentions > 0 { detail = "code is in \(mentions) message(s), none with a photo" }
            else { detail = "not in any chat since \(dateText(since, "dd/MM"))" }
            row.kind = "none"
            row.text = "No photos (\(detail))"
            log.add("[\(code)] no photos: \(detail).")
            return ("none", 0)
        }
        let chats = Set(images.map { $0.str("chat").ifEmpty("?") }).sorted()
        let marks = Set(images.compactMap { $0.str("react").nilIfEmpty }).sorted().joined()
        log.add("[\(code)] \(images.count) photo(s) in: \(chats.joined(separator: ", "))" + (marks.isEmpty ? "" : "  (reacted \(marks))"))
        var files: [(Data, String)] = []
        var hashes = Set<String>()
        var failed = 0
        for (i, img) in images.enumerated() {
            try Task.checkCancellation()
            row.kind = "working"
            row.text = "Downloading \(i + 1)/\(images.count)…"
            activity = "[\(code)] downloading \(i + 1)/\(images.count)"
            var res: JSON
            do {
                res = (try await WaEngine.shared.call("window.__waf_basic.download(\(WaEngine.shared.quote(img.str("id"))))",
                                                      timeout: 180) as? JSON) ?? [:]
            } catch let e as WaError {
                res = jsonOf(("error", e.message))
            }
            let data = res.str("data")
            guard !res.has("error") || res.isNull("error"), !data.isEmpty,
                  let bytes = Data(base64Encoded: data, options: .ignoreUnknownCharacters) else {
                failed += 1
                log.add("[\(code)] photo \(i + 1) failed: \(res.str("error").ifEmpty("no data"))")
                continue
            }
            if !hashes.insert(sha256Hex(bytes)).inserted { continue }   // same picture posted / forwarded twice
            files.append((bytes, Codes.extFor(res.str("mimetype"), bytes, filename: res.str("filename"))))
        }
        if files.isEmpty {
            row.kind = "error"
            row.text = "Found \(images.count), download failed"
            return ("error", 0)
        }
        // one photo -> "<base>.jpg"; several -> "<base> 1.jpg", "<base> 2.jpg", ...
        var first = ""
        for (i, f) in files.enumerated() {
            let name = files.count == 1 ? "\(base)\(f.1)" : "\(base) \(i + 1)\(f.1)"
            Storage.delete(folderName, name)
            try Storage.save(folderName, name, f.0)
            if i == 0 { first = name }
        }
        var text = "Saved \(files.count) image" + (files.count > 1 ? "s" : "")
        if failed > 0 { text += " (\(failed) failed)" }
        row.kind = "ok"
        row.text = text
        row.saved = first
        log.add("[\(code)] \(text) → \(folderName)/\(first)")
        return ("ok", files.count)
    }
}
