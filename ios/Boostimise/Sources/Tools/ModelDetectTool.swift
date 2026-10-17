import Foundation
import Observation

private let MARKER_MAX_LEN = 120   // a message that doesn't START with the code must be this short to count
private let GROUP_MAX_MSGS = 80    // never look further than this many messages away from a code
private let NEAR_MSGS = 12         // a model name written in chat must be this close to the code
private let RANGE_BELOW = 40       // codes this far below the list's smallest number still count as boundaries
private let RANGE_ABOVE = 300      // ... and this far above its largest number

struct Msg {
    let id: String
    let text: String
    let imgs: Int
    let codes: [String]
}

final class CodeGroup {
    let code: String
    let markerId: String
    let markerText: String
    var imageIds: [String] = []
    var context: [String] = []
    var near: [String] = []
    var shared: [String] = []

    init(code: String, markerId: String, markerText: String) {
        self.code = code
        self.markerId = markerId
        self.markerText = markerText
    }
}

/// Codes (from the searched blocks) that label this message (wa_driver.py's marker_codes).
func markerCodes(_ text0: String, hasImages: Bool, ranges: [String: (Int, Int)]) -> [String] {
    let text = text0.trimmed
    if text.isEmpty { return [] }
    let first = ProductCodes.codeMatches(text).first
    let startsWithCode = first != nil && first!.start == 0
    if !startsWithCode && (text as NSString).length > MARKER_MAX_LEN && !hasImages { return [] }
    return ProductCodes.codesInText(text).filter { code in
        let (p, n) = ProductCodes.split(code)
        guard let r = ranges[p] else { return false }
        return n >= r.0 - RANGE_BELOW && n <= r.1 + RANGE_ABOVE
    }
}

/// For every found code, the messages (photos + text) that belong to it (wa_driver.py's build_groups).
/// mode "below": from the code's first message until another code starts; "above": back from the
/// code's last message to the previous code. A code in several places: the one with photos wins, then the oldest.
func buildGroups(_ msgs: [Msg], found: Set<String>, mode: String, maxImages: Int) -> [String: CodeGroup] {
    struct Seg {
        let first: Int
        var last: Int
        var codes: Set<String>
    }
    var segments: [Seg] = []
    for (i, m) in msgs.enumerated() where !m.codes.isEmpty {
        if var last = segments.last, m.codes.contains(where: { last.codes.contains($0) }) {
            last.last = i
            last.codes.formUnion(m.codes)
            segments[segments.count - 1] = last
        } else {
            segments.append(Seg(first: i, last: i, codes: Set(m.codes)))
        }
    }
    var groups: [String: ((Int, Int), CodeGroup)] = [:]
    for (k, seg) in segments.enumerated() {
        let span: [Int]
        let other: Int?
        if mode == "above" {
            let start = k > 0 ? segments[k - 1].last + 1 : 0
            let lo = max(start, seg.last - GROUP_MAX_MSGS)
            span = seg.last >= lo ? Array((lo...seg.last).reversed()) : []
            other = k > 0 ? start - 1 : nil
        } else {
            let end = k + 1 < segments.count ? segments[k + 1].first : msgs.count
            let hi = min(end, seg.first + GROUP_MAX_MSGS)
            span = hi > seg.first ? Array(seg.first..<hi) : []
            other = k + 1 < segments.count ? end : nil
        }
        // the segment's codes in the order they first appear in it
        var segCodes: [String] = []
        for j in seg.first...seg.last { for c in msgs[j].codes where seg.codes.contains(c) && !segCodes.contains(c) { segCodes.append(c) } }
        for code in segCodes where found.contains(code) {
            let g = CodeGroup(code: code, markerId: msgs[seg.first].id, markerText: msgs[seg.first].text)
            var count = 0
            var hasPhotos = false
            for (pos, j) in span.enumerated() {
                let m = msgs[j]
                if m.imgs > 0 && count < maxImages {
                    g.imageIds.append(m.id)
                    count += m.imgs
                    hasPhotos = true
                }
                if !m.text.isEmpty && m.codes.isEmpty { g.context.append(m.text) }
                if !m.text.isEmpty && pos <= NEAR_MSGS {
                    let mine = j > seg.last ? j - seg.last : (j < seg.first ? seg.first - j : 0)
                    let closerToOther = other != nil && abs(j - other!) < mine
                    if closerToOther { g.shared.append(m.text) } else { g.near.append(m.text) }
                }
            }
            let score = (hasPhotos ? 1 : 0, -seg.first)
            if let prev = groups[code] {
                if score.0 > prev.0.0 || (score.0 == prev.0.0 && score.1 > prev.0.1) { groups[code] = (score, g) }
            } else {
                groups[code] = (score, g)
            }
        }
    }
    return groups.mapValues { $0.1 }
}

/// Model Detect (tools/model_detect): finds each code's photos in WhatsApp and writes the phone model
/// next to the code. Reads written names (chat text, text in the photos) for free; can ask Claude.
@MainActor @Observable
final class ModelDetectTool {
    @MainActor @Observable
    final class Result: Identifiable {
        let id = UUID()
        let code: String
        var model = ""
        var confidence = ""
        var note = ""
        var images = 0
        var chat = ""
        var done = false
        var photoNames: [String] = []
        init(_ code: String) { self.code = code }
    }

    @ObservationIgnored private let prefs = Storage.prefs("model_detect")
    var codeText = ""
    var mode: String
    var maxImages: Int
    var lookbackDays: Int
    var engine: String   // text | claude | none
    var apiKey: String
    var model: String
    var effort: String
    var savePhotos: Bool
    var skipDone: Bool
    /// code group (prefix) -> WhatsApp chat
    var chatFor: [String: WaChat] = [:]
    private(set) var results: [Result] = []
    let log = ToolLog()
    private(set) var running = false
    private(set) var activity = ""
    private(set) var progress = 0.0
    @ObservationIgnored private var job: Task<Void, Never>?
    @ObservationIgnored private var list: ProductCodes.CodeList?
    @ObservationIgnored private var ai: ClaudeIdentifier?

    init() {
        let p = Storage.prefs("model_detect")
        mode = p.string("mode", "below")
        maxImages = p.int("max_images", 4)
        lookbackDays = p.int("lookback_days", 30)
        engine = p.string("engine", "text")
        apiKey = Keychain.get("claude_api_key") ?? ""
        model = p.string("model", ClaudeIdentifier.defaultModel)
        effort = p.string("effort", "high")
        savePhotos = p.bool("save_photos", true)
        skipDone = p.bool("skip_done", true)
    }

    func save() {
        prefs.set("mode", mode)
        prefs.set("max_images", maxImages)
        prefs.set("lookback_days", lookbackDays)
        prefs.set("engine", engine)
        Keychain.set("claude_api_key", apiKey.isEmpty ? nil : apiKey)
        prefs.set("model", model)
        prefs.set("effort", effort)
        prefs.set("save_photos", savePhotos)
        prefs.set("skip_done", skipDone)
        for (p, c) in chatFor { prefs.set("chat_\(p)", c.title) }
    }

    /// The code groups of the list, in order: [("5a", [codes]), …].
    func groups() -> [(String, [String])] {
        let l = ProductCodes.CodeList(codeText)
        return l.blockOrder.map { ($0, l.blocks[$0] ?? []) }
    }

    /// A chat whose name contains the group code ("(5a-)", "5a-") is matched by itself.
    func autoMatch(_ chats: [WaChat]) {
        for (p, _) in groups() where chatFor[p] == nil {
            let remembered = prefs.stringOrNil("chat_\(p)")
            let dashRx = Rx("(?<![0-9a-z])" + Rx.escape(p) + "-")
            let hit = chats.first { $0.title == remembered }
                ?? chats.first { $0.title.lowercased().contains("(\(p)-)") }
                ?? chats.first { dashRx.contains($0.title.lowercased()) }
            if let hit { chatFor[p] = hit }
        }
    }

    func resultText() -> String {
        let l = list ?? ProductCodes.CodeList(codeText)
        var models = l.existing()
        for r in results where !r.model.isEmpty { models[r.code] = r.model }
        return l.write(models)
    }

    func stop() { job?.cancel() }

    func start() {
        if running { return }
        save()
        let l = ProductCodes.CodeList(codeText)
        list = l
        if l.codes.isEmpty {
            log.add("Paste your code list first.")
            return
        }
        results = []
        running = true
        Jobs.shared.begin("model_detect", "Model Detect: starting…")
        job = Task {
            do {
                try await WaEngine.shared.awaitReady { [self] in activity = $0 }
                try await WaEngine.shared.withTurn("Model Detect", { [self] in activity = $0 }) { try await run(l) }
                log.add("All done.")
            } catch is CancellationError {
                log.add("Stopped by user.")
            } catch {
                log.add("Error: \(error.localizedDescription)")
            }
            running = false
            activity = ""
            Jobs.shared.end("model_detect")
        }
    }

    private func run(_ l: ProductCodes.CodeList) async throws {
        var reader: TextIdentifier?
        if engine != "none" {
            reader = TextIdentifier(log: { [weak self] s in
                Task { @MainActor in self?.log.add(s) }
            })
        }
        ai = nil
        if engine == "claude" {
            if !apiKey.trimmed.isEmpty {
                ai = ClaudeIdentifier(apiKey: apiKey.trimmed, model: model, effort: effort)
                log.add("Photos with no written name go to \(model) (effort \(effort)).")
            } else {
                log.add("No Claude API key - only written model names will be used.")
            }
        }
        let existing = l.existing()
        let since = Int((Calendar.current.date(byAdding: .day, value: -lookbackDays, to: Date()) ?? Date()).timeIntervalSince1970)
        let total = max(l.codes.count, 1)
        var doneCount = 0
        for prefix in l.blockOrder {
            try Task.checkCancellation()
            let codes0 = l.blocks[prefix] ?? []
            let codes = skipDone ? codes0.filter { !ProductCodes.isDone(existing[$0]) } : codes0
            if codes.isEmpty {
                doneCount += codes0.count
                continue
            }
            let rows = codes.map { Result($0) }
            results += rows
            guard let chat = chatFor[prefix] else {
                for r in rows {
                    r.note = "no chat chosen for group \(prefix)"
                    r.done = true
                }
                log.add("Group \(prefix): choose its WhatsApp chat first.")
                continue
            }
            log.add("=== \(chat.title): \(codes.count) codes ===")
            activity = "Reading \(chat.title)…"
            Jobs.shared.progress("model_detect", "Model Detect: reading \(chat.title)")
            let opts = JSONTools.string(jsonOf(("since", since), ("maxLoads", 400)))
            let res = (try await WaEngine.shared.call("window.__wab.messages(\(WaEngine.shared.quote(chat.id)), \(opts))",
                                                      timeout: 30 * 60) as? JSON) ?? [:]
            if res.has("error") {
                log.add("\(chat.title): \(res.str("error"))")
                for r in rows {
                    r.note = res.str("error")
                    r.done = true
                }
                continue
            }
            let ranges = [prefix: l.prefixRange(prefix)]
            let msgs = res.objs("messages").map { m -> Msg in
                let text = m.str("text")
                let img = m.bool("image")
                return Msg(id: m.str("id"), text: text, imgs: img ? 1 : 0, codes: markerCodes(text, hasImages: img, ranges: ranges))
            }
            let found = Set(msgs.flatMap { $0.codes }).intersection(Set(codes))
            log.add("  \(msgs.count) messages read, \(found.count)/\(codes.count) codes found.")
            let groups = buildGroups(msgs, found: found, mode: mode, maxImages: maxImages)
            for r in rows.reversed() {   // bottom of the list first
                try Task.checkCancellation()
                doneCount += 1
                progress = Double(doneCount) / Double(total)
                r.chat = chat.title
                guard let g = groups[r.code] else {
                    r.model = "NOT FOUND"
                    r.note = res.bool("reached") ? "code message not found in this chat"
                        : "older than what WhatsApp Web loaded - raise 'Look back' and run again"
                    r.done = true
                    continue
                }
                r.note = "collecting photos…"
                activity = "\(r.code): collecting photos"
                var images: [Data] = []
                for id in g.imageIds {
                    if images.count >= maxImages { break }
                    guard let d = try? await WaEngine.shared.call("window.__waf_basic.download(\(WaEngine.shared.quote(id)))",
                                                                  timeout: 180) as? JSON else { continue }
                    let data = d.str("data")
                    guard !data.isEmpty, let raw = Data(base64Encoded: data, options: .ignoreUnknownCharacters) else { continue }
                    if let img = prepareImage(raw) { images.append(img) }
                }
                if images.isEmpty {
                    r.model = "NO IMAGE"
                    r.note = "code found ('\(g.markerText.take(40))') but no photos next to it"
                    r.done = true
                    continue
                }
                r.images = images.count
                if savePhotos {
                    let rel = "Model Detect/\(r.code)"
                    var names: [String] = []
                    for (i, img) in images.enumerated() {
                        let name = "photo\(i + 1).jpg"
                        Storage.delete(rel, name)
                        try? Storage.save(rel, name, img)
                        names.append(name)
                    }
                    r.photoNames = names
                }
                guard let reader else {
                    r.note = "photos saved"
                    r.done = true
                    continue
                }
                r.note = "reading…"
                await identify(r, reader, images, g)
            }
        }
    }

    private func identify(_ r: Result, _ reader: TextIdentifier, _ images: [Data], _ g: CodeGroup) async {
        defer { r.done = true }
        do {
            var seen: [String] = []
            var id = await reader.identify(images, texts: g.near, shared: g.shared, seen: &seen)
            // nothing written, only the brand logo ("redmi ?") or an unsure reading: let the AI look at the phone
            if id == nil || id!.confidence == "low", let ai {
                var unique: [String] = []
                for t in g.near + g.context where !unique.contains(t) { unique.append(t) }
                let context = TextIdentifier.hideNumbers(unique.joined(separator: "\n")).take(800)
                var hint = ""
                if let id { hint = "\nRead so far (not sure): \(id.model.trimEnd(" ?")) - \(id.reason)" }
                if !seen.isEmpty {
                    var u: [String] = []
                    for s in seen where !u.contains(s) { u.append(s) }
                    hint += "\nText printed in the photos: " + u.joined(separator: " | ").take(400)
                }
                r.note = "asking Claude…"
                let answer = try await ai.identify(code: r.code, images: images, context: context + hint)
                if id == nil || answer.model != "unknown" { id = answer }
            }
            if let id {
                r.model = id.model
                r.confidence = id.confidence
                r.note = id.reason
                log.add("  \(r.code): \(id.model) (\(id.confidence))")
            } else {
                r.note = "no model name written - see the photos"
                log.add("  \(r.code): no model name found")
            }
        } catch let e as ClaudeKeyError {
            log.add("Claude API refused the key or model - identification with Claude is off. (\(e.message))")
            ai = nil
            r.note = "API key / model error"
        } catch {
            r.note = "error: \(error.localizedDescription)".take(200)
            log.add("  \(r.code): \(error.localizedDescription)")
        }
    }
}
