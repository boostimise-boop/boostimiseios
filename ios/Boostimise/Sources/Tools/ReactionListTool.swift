import Foundation
import Observation

/// The reaction buttons (tools/reaction_list/helpers.py): one per reaction of the shop settings, All and
/// Custom. 😢 counts as 😥. Custom = the emojis typed for it.
struct Choice: Hashable {
    let key: String
    let label: String
    let emojis: [String]
}

let CUSTOM = "custom"
let ALL = "all"
private let oldKeys = ["x": "❌", "nophone": "📵", "sad": "😥"]  // the saved choice up to 4.4
private let aliases = ["😥": ["😢"]]

/// The emojis looked for: each one, and right after it the reactions that count as it (😥 → 😢).
func withAliases(_ emojis: [String]) -> [String] {
    var out: [String] = []
    for e in emojis { for x in [e] + (aliases[e] ?? []) where !out.contains(x) { out.append(x) } }
    return out
}

func makeChoices(_ reactions: [ShopReaction]) -> [Choice] {
    var out = reactions.map { Choice(key: $0.emoji, label: $0.emoji, emojis: withAliases([$0.emoji])) }
    if reactions.count > 1 {
        out.append(Choice(key: ALL, label: reactions.map(\.emoji).joined(separator: " "),
                          emojis: withAliases(reactions.map(\.emoji))))
    }
    out.append(Choice(key: CUSTOM, label: "Custom", emojis: []))
    return out
}

/// ["❌": "Cancelled", …], also for the reactions that count as one of them (😢 as 😥).
func reactionLabels(_ reactions: [ShopReaction]) -> [String: String] {
    var out: [String: String] = [:]
    for r in reactions where !r.label.isEmpty { for e in withAliases([r.emoji]) where out[e] == nil { out[e] = r.label } }
    return out
}

enum ReactionEmojis {
    /// As wa_bridge_reactions.js reads reactions: '❤️' and '❤', '👍🏻' and '👍' are the same reaction.
    private static func isMark(_ u: Unicode.Scalar) -> Bool {
        u.value == 0xFE0E || u.value == 0xFE0F || (0x1F3FB...0x1F3FF).contains(u.value)
    }

    /// '👍 ❤️,🔥' -> [👍, ❤, 🔥]: one entry per emoji, without skin tones and variation marks, each once.
    static func split(_ s: String) -> [String] {
        var scalars = String.UnicodeScalarView()
        scalars.append(contentsOf: s.unicodeScalars.filter { !isMark($0) })
        var out: [String] = []
        for ch in String(scalars) {
            let e = String(ch)
            if e.trimmed.isEmpty || [",", ";", "."].contains(e) || out.contains(e) { continue }
            out.append(e)
        }
        return out
    }

    /// The emojis typed for Custom; 😥 also brings 😢, as the 😥 button does.
    static func custom(_ s: String) -> [String] {
        withAliases(split(s))
    }
}

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

/// Reaction List (tools/reaction_list): the codes whose messages got ❌, 📵, 😥 or your own emojis, with the phone
/// number from the order message. Empty code box = every reacted code in the chats read.
@MainActor @Observable
final class ReactionListTool {
    @Observable
    final class Found: Identifiable {
        let id = UUID()
        let code: String
        let name: String
        let order: Int
        var emojis = Set<String>()
        var phones: [String] = []
        var chats: [String] = []
        var t = 0
        /// With "List + photos": what was saved ("3 photos", "No photos" …).
        var photos = ""
        var chatIds: [String] = []

        init(code: String, name: String, order: Int) {
            self.code = code
            self.name = name
            self.order = order
        }

        /// '❌📵', or with what they mean in the shop settings '❌ Cancelled, 📵 Phone off'.
        func react(_ wanted: [String], _ labels: [String: String] = [:]) -> String {
            let found = wanted.filter { emojis.contains($0) }
            if !found.contains(where: { !(labels[$0] ?? "").isEmpty }) { return found.joined() }
            return found.map { e in (labels[e] ?? "").isEmpty ? e : "\(e) \(labels[e]!)" }.joined(separator: ", ")
        }
    }

    @ObservationIgnored private let prefs = Storage.prefs("reaction_list")
    var codeText = ""
    /// The buttons, from the shop settings (they change with them).
    var choices: [Choice] { makeChoices(Shop.shared.reactions) }
    var choiceKey: String
    var choice: Choice {
        let c = choices
        return c.first { $0.key == choiceKey } ?? c.first { $0.key == ALL } ?? c[0]
    }
    var labels: [String: String] { reactionLabels(Shop.shared.reactions) }
    /// The emojis typed for the Custom choice ("👍 ❤️ 🔥").
    var customText: String
    /// The reactions of the last run, in the order they are shown.
    private(set) var shown: [String] = []
    var lookbackDays: Int
    var chat: ChatFilter
    /// false = list only; true = also save each listed code's reacted photos in Files › Boostimise › `folder`.
    var withPhotos: Bool
    var folder: String
    var folderName: String { folder.trimmed.ifEmpty("Reaction List") }
    private(set) var results: [Found] = []
    let log = ToolLog()
    private(set) var running = false
    private(set) var activity = ""
    private(set) var progress = 0.0
    private(set) var summary = ""
    @ObservationIgnored private var job: Task<Void, Never>?

    init() {
        let p = Storage.prefs("reaction_list")
        let key = p.string("choice", ALL)
        choiceKey = oldKeys[key] ?? key
        customText = p.string("custom_reactions")
        lookbackDays = p.int("lookback_days", 10)
        chat = ChatFilter(id: p.string("chat_id"), title: p.string("chat_title"), groupsOnly: p.bool("groups_only", false))
        withPhotos = p.bool("with_photos", false)
        folder = p.string("folder", "Reaction List")
    }

    /// The emojis looked for: the chosen button's, or for Custom the ones typed.
    func wanted() -> [String] { choice.key == CUSTOM ? ReactionEmojis.custom(customText) : choice.emojis }

    private func save() {
        prefs.set("choice", choiceKey)
        prefs.set("custom_reactions", customText.trimmed)
        prefs.set("lookback_days", lookbackDays)
        prefs.set("with_photos", withPhotos)
        prefs.set("folder", folder)
        prefs.set("chat_id", chat.id)
        prefs.set("chat_title", chat.title)
        prefs.set("groups_only", chat.groupsOnly)
    }

    func stop() { job?.cancel() }

    /// '1. 2-558 Realme C85 ❌ 01734484040' (or '❌ Cancelled' with a label), one code per line.
    func formatList() -> String {
        let lines = results.enumerated().map { n, f in
            "\(n + 1). " + [f.code, f.name, f.react(shown, labels), f.phones.joined(separator: ", ")].filter { !$0.isEmpty }
                .joined(separator: " ")
        }
        return lines.joined(separator: "\n") + (results.isEmpty ? "" : "\n")
    }

    func start() {
        if running { return }
        save()
        let emojis = wanted()
        if emojis.isEmpty {
            summary = "Type the reactions to look for in the Custom box, e.g. 👍 ❤️ 🔥"
            return
        }
        shown = emojis
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
        let reactions = shown.joined()
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
                    f.emojis.formUnion(ReactionEmojis.split(hit.str("react")))
                    for p in phones where !f.phones.contains(p) { f.phones.append(p) }
                    let chatName = hit.str("chat").ifEmpty(title)
                    if !f.chats.contains(chatName) { f.chats.append(chatName) }
                    f.t = max(f.t, hit.int("t"))
                    if !f.chatIds.contains(target.str("id")) { f.chatIds.append(target.str("id")) }
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
        var photos: (Int, Int)?
        if withPhotos && !byCode.isEmpty { photos = try await savePhotos(byCode, sinceTs: sinceTs, reactions: reactions) }
        let secs = Int(Date().timeIntervalSince(started))
        summary = items.isEmpty ? "Done in \(secs)s: \(byCode.count) code(s) reacted with \(reactions)"
            : "Done in \(secs)s: \(byCode.count) of \(wanted.count) code(s) reacted with \(reactions)"
        summary += photos.map { ", photos saved for \($0.0) (\($0.1) images)." } ?? "."
        log.add(summary)
    }

    /// Saves the reacted photos of each listed code as "2-558 Realme C85.jpg" (as Fetch by Code names them):
    /// the photos with the reaction, or all photos of a code whose message has it. Returns (codes, images) saved.
    private func savePhotos(_ byCode: [String: Found], sinceTs: Int, reactions: String) async throws -> (Int, Int) {
        let codes = byCode.keys.sorted(by: ReactionCodes.less)
        var key: [String: String] = [:]
        for (i, c) in codes.enumerated() { key[c] = String(i) }
        func base(_ c: String) -> String { CodeItem(code: byCode[c]!.code, name: byCode[c]!.name).baseName() }
        let existing = Storage.namesIn(folderName)
        var todo: [String] = []
        for c in codes {
            if Codes.existingOutput(existing, base(c)) != nil, let f = byCode[c] { f.photos = "Saved earlier" } else { todo.append(c) }
        }
        var chatOrder: [String] = []
        var byChat: [String: [String]] = [:]
        for c in todo {
            for id in byCode[c]!.chatIds {
                if byChat[id] == nil { chatOrder.append(id) }
                byChat[id, default: []].append(c)
            }
        }
        log.add("Finding the photos of \(todo.count) code(s) in \(chatOrder.count) chat(s)…")
        let total = Double(max(chatOrder.count + todo.count, 1))
        var found: [String: [String: JSON]] = [:]
        for (n, chatId) in chatOrder.enumerated() {
            try Task.checkCancellation()
            activity = "Finding photos: chat \(n + 1)/\(chatOrder.count)"
            Jobs.shared.progress("reaction_list", "Reaction List: \(activity)")
            let list: [JSON] = (byChat[chatId] ?? []).map { jsonOf(("key", key[$0]!), ("pattern", Codes.codePattern($0))) }
            let opts = JSONTools.string(jsonOf(("codes", list), ("since", sinceTs), ("neighbors", "after"), ("gapMinutes", 5),
                                               ("maxNeighbors", 30), ("maxLoads", 300), ("reactions", reactions)))
            var res: JSON = [:]
            do {
                res = (try await WaEngine.shared.call("window.__waf_reactions.scanChat(\(WaEngine.shared.quote(chatId)), \(opts))",
                                                      timeout: 30 * 60) as? JSON) ?? [:]
            } catch let e as WaError {
                log.add("Photos: \(e.message)")
            }
            if res.has("error") { log.add("Photos: \(res.str("error"))") }
            let hits = res.obj("hits") ?? [:]
            for k in hits.keys {
                var bucket = found[k] ?? [:]
                for img in hits.objs(k) where bucket[img.str("id")] == nil { bucket[img.str("id")] = img }
                found[k] = bucket
            }
            progress = Double(n + 1) / total
        }
        var saved = 0
        var images = 0
        for (i, c) in todo.enumerated() {
            try Task.checkCancellation()
            let f = byCode[c]!
            progress = Double(chatOrder.count + i + 1) / total
            let imgs = (found[key[c]!] ?? [:]).values.sorted { $0.int("t") < $1.int("t") }
            if imgs.isEmpty {
                f.photos = "No photos"
                log.add("[\(f.code)] no reacted photos.")
                continue
            }
            var files: [(Data, String)] = []
            var hashes = Set<String>()
            for (j, img) in imgs.enumerated() {
                try Task.checkCancellation()
                f.photos = "Downloading \(j + 1)/\(imgs.count)…"
                activity = "[\(f.code)] downloading \(j + 1)/\(imgs.count)"
                var res: JSON
                do {
                    res = (try await WaEngine.shared.call("window.__waf_reactions.download(\(WaEngine.shared.quote(img.str("id"))))",
                                                          timeout: 180) as? JSON) ?? [:]
                } catch let e as WaError {
                    res = jsonOf(("error", e.message))
                }
                let data = res.str("data")
                guard !res.has("error") || res.isNull("error"), !data.isEmpty,
                      let bytes = Data(base64Encoded: data, options: .ignoreUnknownCharacters) else {
                    log.add("[\(f.code)] photo \(j + 1) failed: \(res.str("error").ifEmpty("no data"))")
                    continue
                }
                if !hashes.insert(sha256Hex(bytes)).inserted { continue }   // same picture posted / forwarded twice
                files.append((bytes, Codes.extFor(res.str("mimetype"), bytes, filename: res.str("filename"))))
            }
            if files.isEmpty {
                f.photos = "Found \(imgs.count), download failed"
                continue
            }
            // one photo -> "<base>.jpg"; several -> "<base> 1.jpg", "<base> 2.jpg", ...
            for (n, file) in files.enumerated() {
                let name = files.count == 1 ? "\(base(c))\(file.1)" : "\(base(c)) \(n + 1)\(file.1)"
                Storage.delete(folderName, name)
                try Storage.save(folderName, name, file.0)
            }
            saved += 1
            images += files.count
            f.photos = "\(files.count) photo" + (files.count > 1 ? "s" : "")
            log.add("[\(f.code)] saved \(f.photos) → \(folderName)")
        }
        return (saved, images)
    }
}
