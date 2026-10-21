import Foundation
import UIKit

private let minFaceConfidence = 0.8
// The biggest face must be at least this part of the photo's short side. People photos sent for a
// cover measured 0.15 and up; a contact picture or wallpaper in a phone screenshot is smaller.
private let minFaceShare = 0.07
private let parallelPhotos = 2      // photos checked / enhanced at once: the server's GPU works on one while the next uploads
private let pollSeconds: UInt64 = 1

let ENHANCE_SCALES: [(Int, String)] = [(4, "4× (recommended)"), (2, "2×"), (0, "Don't enhance")]
private let photoExts: Set<String> = ["jpg", "jpeg", "png", "webp", "bmp", "gif", "jfif", "heic", "heif"]

/// "2-283 Infinix hot 50i", with only (bracketed) text after it: a note or "(2)".
func isName(_ name: String, _ base: String) -> Bool {
    let n = name.lowercased(), b = base.lowercased()
    return n.hasPrefix(b) && Rx(#"(\s*\([^()]*\))*"#).matches(String(n.dropFirst(b.count)))
}

/// Text in (brackets) from the WhatsApp captions that is not already in the name.
func captionNote(_ captions: [String], _ label: String) -> String {
    var notes: [String] = []
    for cap in captions {
        for m in Rx(#"\(([^()\n]*)\)?"#).findAll(cap) {
            let text = m.group(1).trimmed
            if !text.isEmpty && !label.lowercased().contains(text.lowercased()) && !notes.contains(text) { notes.append(text) }
        }
    }
    return notes.isEmpty ? "" : " (\(notes.joined(separator: "; ")))"
}

/// The names the Windows app gives (match_renamer safe_filename).
func windowsSafeName(_ name: String) -> String {
    var n = Rx(#"[\\/:*?"<>|\r\n\t]+"#).replace(name, " ")
    n = Rx(#"\s+"#).replace(n, " ").trimmed
    func trimEnd() { while n.hasSuffix(".") || n.hasSuffix(" ") { n.removeLast() } }
    trimEnd()
    n = String(n.prefix(150))
    trimEnd()
    return n.isEmpty ? "unnamed" : n
}

/// Faces big and sure enough to be a photo of people, from the server's analysis.
func peopleIn(_ data: JSON?) -> Int {
    let a = data?.obj("analysis") ?? [:]
    let short = Double(min(a.int("width"), a.int("height")))
    return (data?.objs("faces") ?? []).filter {
        $0.double("confidence") >= minFaceConfidence && $0.double("size_px") >= minFaceShare * short
    }.count
}

/// Fetch & Enhance (tools/fetch_enhance): Fetch by Code's search, then for each code only the photos with
/// people in them are kept and enhanced (4×) on the enhancement server. Phones, screenshots and other photos
/// without a person are never saved. They get the names Rename by Match gives: one photo
/// "2-226 Realme P3 Ultra.jpg", two or more a folder "2-221 Oppo a6 pro 5g" with img1.jpg, img2.jpg …
/// Each enhancement is taken from the account's daily quota exactly like in the Photo Enhancer.
@MainActor @Observable
final class FetchEnhanceTool {
    let fetch = FetchTool(key: "fetch_enhance", title: "Fetch & Enhance")
    @ObservationIgnored private let prefs = Storage.prefs("fetch_enhance")
    var scale: Int { didSet { prefs.set("scale", scale) } }     // 4 | 2 | 0 = don't enhance
    var peopleOnly: Bool { didSet { prefs.set("people_only", peopleOnly) } }
    var addNote: Bool { didSet { prefs.set("add_note", addNote) } }
    /// Why the quota said no (limit | blocked | onboarding | update_required): shows the limit dialog.
    var denied = ""

    init() {
        let p = Storage.prefs("fetch_enhance")
        scale = p.int("scale", 4)
        peopleOnly = p.bool("people_only", true)
        addNote = p.bool("add_note", false)
        fetch.prepare = { [unowned self] in try await self.prepare() }
        fetch.existingLookup = { [unowned self] in self.existingLookup() }
        fetch.saveCode = { [unowned self] in try await self.saveCode($0, $1, $2, $3, $4) }
    }

    private var log: ToolLog { fetch.log }

    private func prepare() async throws {
        if !peopleOnly && scale == 0 { return }
        do {
            _ = try await EnhancerApi.health()
        } catch {
            throw StopRun(message: "cannot reach the enhancement server (\(error.localizedDescription)). Check the internet and try again.")
        }
        if scale > 0 && Account.shared.quota?.remaining == 0 {
            denied = "limit"
            throw StopRun(message: "no enhancements left today.")
        }
    }

    /// The photo ("base.jpg", "base (note).jpg") or folder ("base (note)/img1.jpg") saved for a code before,
    /// or Fetch by Code's "base 1.jpg".
    private func existingLookup() -> (String) -> String? {
        let dir = Storage.folder(fetch.folderName)
        let names = (try? FileManager.default.contentsOfDirectory(atPath: dir.path)) ?? []
        var folders: [String] = []
        var files: Set<String> = []
        for n in names {
            var isDir: ObjCBool = false
            _ = FileManager.default.fileExists(atPath: dir.appendingPathComponent(n).path, isDirectory: &isDir)
            if isDir.boolValue {
                let inside = (try? FileManager.default.contentsOfDirectory(atPath: dir.appendingPathComponent(n).path)) ?? []
                if !inside.isEmpty { folders.append(n) }
            } else {
                files.insert(n)
            }
        }
        return { base in
            if let f = folders.first(where: { isName($0, base) }) { return f + "/" }
            if let f = files.sorted().first(where: {
                photoExts.contains(($0 as NSString).pathExtension.lowercased()) && isName(($0 as NSString).deletingPathExtension, base)
            }) { return f }
            return Codes.existingOutput(files, base)
        }
    }

    // ---- one code ----------------------------------------------------------------------------
    private func saveCode(_ row: FetchTool.Row, _ base: String, _ files: [(Data, String)], _ failed: Int,
                          _ images: [JSON]) async throws -> (String, Int) {
        let code = row.item.code
        let work = Storage.cacheFolder("fetch_enhance/\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: work) }
        do {
            var paths: [URL] = []
            for (i, f) in files.enumerated() {
                let (d, e) = uploadable(f.0, f.1)
                let url = work.appendingPathComponent("\(code.replacingOccurrences(of: "/", with: "-")) \(i + 1)\(e)")
                try d.write(to: url)
                paths.append(url)
            }
            var kept = paths
            if peopleOnly { kept = try await people(row, paths) }
            if kept.isEmpty {
                let text = "No photos of people (\(paths.count) skipped: phones, screenshots …)"
                row.kind = "none"
                row.text = text
                log.add("[\(code)] \(text)")
                return ("none", 0)
            }
            let photos: [(Data, String)]
            var plain = 0
            if scale > 0 {
                let r = try await enhanceAll(row, kept)
                photos = r.0
                plain = r.1
            } else {
                photos = try kept.map { (try Data(contentsOf: $0), "." + $0.pathExtension.lowercased()) }
            }
            let captions = images.map { $0.str("caption") }.filter { !$0.isEmpty }
            let label = windowsSafeName(base + (addNote ? captionNote(captions, base) : ""))
            let saved = try saveNamed(label, photos)

            var text = "\(photos.count) photo\(photos.count > 1 ? "s" : "") " + (scale > 0 ? "enhanced \(scale)×" : "saved")
            if kept.count < paths.count { text += ", \(paths.count - kept.count) without people skipped" }
            if plain > 0 { text += " (\(plain) not enhanced, saved as received)" }
            if failed > 0 { text += " (\(failed) download(s) failed)" }
            row.kind = plain > 0 ? "error" : "ok"
            row.text = text
            row.saved = saved
            log.add("[\(code)] \(text) → \(fetch.folderName)/\(saved)")
            return ("ok", photos.count)
        } catch let e as StopRun {
            row.kind = "error"
            row.text = "Not saved: \(e.message)"
            log.add("[\(code)] not saved: \(e.message)")
            throw e
        }
    }

    /// One photo: "<label>.jpg". Several: the folder "<label>" with img1.jpg, img2.jpg … Returns what was saved.
    private func saveNamed(_ label: String, _ photos: [(Data, String)]) throws -> String {
        if photos.count == 1 {
            let name = label + photos[0].1
            Storage.delete(fetch.folderName, name)
            try Storage.save(fetch.folderName, name, photos[0].0)
            return name
        }
        let sub = "\(fetch.folderName)/\(label)"
        for (i, p) in photos.enumerated() {
            let name = "img\(i + 1)\(p.1)"
            Storage.delete(sub, name)
            try Storage.save(sub, name, p.0)
        }
        return label + "/"
    }

    /// The server takes JPG, PNG and WEBP; anything else (HEIC, GIF …) becomes a JPG.
    private func uploadable(_ data: Data, _ ext: String) -> (Data, String) {
        if EnhancerApi.accepted.contains(String(ext.dropFirst()).lowercased()) { return (data, ext) }
        guard let jpg = UIImage(data: data)?.jpegData(compressionQuality: 0.95) else { return (data, ext) }
        return (jpg, ".jpg")
    }

    private final class Gate<R> {
        var next = 0
        var stop: Error?
        var results: [Result<R, Error>?]
        init(_ n: Int) { results = Array(repeating: nil, count: n) }
    }

    /// Runs `f` on every item, `parallelPhotos` at a time; a failure is that item's result, except a stop.
    private func parallel<T, R>(_ items: [T], _ f: @escaping @MainActor (T) async throws -> R) async throws -> [Result<R, Error>] {
        let g = Gate<R>(items.count)
        let workers = (0..<min(parallelPhotos, items.count)).map { _ in
            Task { @MainActor in
                while g.stop == nil && g.next < items.count {
                    let i = g.next
                    g.next += 1
                    do {
                        g.results[i] = .success(try await f(items[i]))
                    } catch is CancellationError {
                        g.stop = g.stop ?? CancellationError()
                    } catch let e as StopRun {
                        g.stop = g.stop ?? e
                    } catch {
                        g.results[i] = .failure(error)
                    }
                }
            }
        }
        await withTaskCancellationHandler {
            for w in workers { await w.value }
        } onCancel: {
            for w in workers { w.cancel() }
        }
        try Task.checkCancellation()
        if let stop = g.stop { throw stop }
        return g.results.map { $0 ?? .failure(CancellationError()) }
    }

    /// The photos with people in them (a photo that can't be checked is kept).
    private func people(_ row: FetchTool.Row, _ paths: [URL]) async throws -> [URL] {
        let code = row.item.code
        row.kind = "working"
        row.text = "Looking for people in \(paths.count) photo(s)…"
        fetch.activity = "[\(code)] looking for people in \(paths.count) photo(s)"
        let results = try await parallel(paths) { url -> Int in
            let analysis = try await EnhancerApi.analyze(url, url.lastPathComponent, scale: 4)
            return peopleIn(analysis)
        }
        var kept: [URL] = []
        for (path, r) in zip(paths, results) {
            switch r {
            case .success(let n): if n > 0 { kept.append(path) }
            case .failure(let e):
                log.add("[\(code)] photo \(path.deletingPathExtension().lastPathComponent.split(separator: " ").last ?? "") " +
                        "could not be checked for people (\(e.localizedDescription)), so it is kept")
                kept.append(path)
            }
        }
        log.add("[\(code)] \(kept.count) of \(paths.count) photo(s) with people")
        return kept
    }

    /// The enhanced photos (a photo the server fails on is kept as received), and how many failed.
    private func enhanceAll(_ row: FetchTool.Row, _ paths: [URL]) async throws -> ([(Data, String)], Int) {
        let code = row.item.code
        if let left = Account.shared.quota?.remaining, left < paths.count {
            denied = "limit"
            throw StopRun(message: "\(left) enhancement(s) left today, \(code) needs \(paths.count).")
        }
        var settings = EnhanceSettings()
        settings.scale = scale
        var done = 0
        row.kind = "working"
        row.text = "Enhancing \(scale)× 0/\(paths.count)…"
        fetch.activity = "[\(code)] enhancing \(paths.count) photo(s) \(scale)×"
        let results = try await parallel(paths) { [self] path -> Data in
            let data = try await enhanceOne(path, settings)
            done += 1
            row.text = "Enhancing \(scale)× \(done)/\(paths.count)…"
            return data
        }
        var photos: [(Data, String)] = []
        var plain = 0
        for (path, r) in zip(paths, results) {
            switch r {
            case .success(let d): photos.append((d, ".jpg"))
            case .failure(let e):
                plain += 1
                log.add("[\(code)] photo \(path.deletingPathExtension().lastPathComponent.split(separator: " ").last ?? "") " +
                        "not enhanced (\(e.localizedDescription)), saved as received")
                photos.append(((try? Data(contentsOf: path)) ?? Data(), "." + path.pathExtension.lowercased()))
            }
        }
        return (photos, plain)
    }

    // ---- one photo on the enhancement server (the Photo Enhancer's quota steps) ---------------
    /// Records how a reserved enhancement ended; failed / cancelled ones are refunded.
    private func settle(_ recordId: String?, _ status: String, error: String? = nil, jobId: String? = nil,
                        w: Int? = nil, h: Int? = nil, secs: Double? = nil) {
        guard let recordId else { return }
        Task {
            await Account.shared.finishEnhancement(recordId, status: status, jobId: jobId, outW: w, outH: h, seconds: secs, error: error)
            if status != "completed" {
                try? await Task.sleep(nanoseconds: 600_000_000)
                Account.shared.refresh()
            }
        }
    }

    private func enhanceOne(_ file: URL, _ s: EnhanceSettings) async throws -> Data {
        let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.intValue ?? 0
        let img = UIImage(contentsOfFile: file.path)?.cgImage
        var recordId: String?
        var jobId = ""
        while true {
            try Task.checkCancellation()
            let res = await Account.shared.reserveEnhancement(fileName: file.lastPathComponent, fileSize: size, mode: s.mode,
                                                              scale: s.scale, width: img?.width, height: img?.height)
            Account.shared.setQuota(res.quota)
            if !res.ok {   // nothing more can start today
                if res.code == "onboarding" { Account.shared.refresh() } else { denied = res.code }
                throw StopRun(message: res.message.ifEmpty("Today's limit is reached."))
            }
            recordId = res.id
            do {
                jobId = try await EnhancerApi.submit(file, file.lastPathComponent, s, onUpload: { _ in })
                break
            } catch is CancellationError {
                settle(recordId, "cancelled")
                throw CancellationError()
            } catch let e as HttpError where e.status == 503 {
                settle(recordId, "cancelled")   // the server's queue is full (other users): try again
                try await Task.sleep(nanoseconds: 3_000_000_000)
            } catch {
                settle(recordId, "failed", error: error.localizedDescription)
                throw error
            }
        }
        if let id = recordId { let j = jobId; Task { await Account.shared.finishEnhancement(id, jobId: j) } }
        let job: JSON
        do {
            job = try await waitJob(jobId)
        } catch is CancellationError {
            let j = jobId
            Task { await EnhancerApi.cancel(j) }
            settle(recordId, "cancelled")
            throw CancellationError()
        } catch {
            settle(recordId, "failed", error: error.localizedDescription)
            throw error
        }
        let result = job.obj("result") ?? [:]
        let inp = job.obj("input") ?? [:]
        let w = result.int("width") > 0 ? result.int("width") : (inp.int("output_width") > 0 ? inp.int("output_width") : nil)
        let h = result.int("height") > 0 ? result.int("height") : (inp.int("output_height") > 0 ? inp.int("output_height") : nil)
        settle(recordId, "completed", jobId: jobId, w: w, h: h, secs: result.obj("benchmark")?.doubleOrNil("total_seconds"))
        let j = jobId
        defer { Task { await EnhancerApi.cancel(j) } }   // saved here: delete it from the server
        let (tmp, _) = try await EnhancerApi.download(EnhancerApi.resultURL(job, "jpg"), fallback: "enhanced.jpg")
        defer { try? FileManager.default.removeItem(at: tmp) }
        return try Data(contentsOf: tmp)
    }

    private func waitJob(_ jobId: String) async throws -> JSON {
        var misses = 0
        while true {
            try Task.checkCancellation()
            let res: JSON
            do {
                res = try await EnhancerApi.jobs([jobId]) ?? [:]
                misses = 0
            } catch is CancellationError {
                throw CancellationError()
            } catch {
                misses += 1
                if misses >= 20 { throw EnhanceFailure.lost }
                try await Task.sleep(nanoseconds: pollSeconds * 1_000_000_000)
                continue
            }
            if let job = res.objs("jobs").first(where: { $0.str("job_id") == jobId }) {
                switch job.str("status") {
                case "completed": return job
                case "failed", "cancelled":
                    throw EnhanceFailure.failed(job.obj("error")?.str("message").nilIfEmpty ?? "processing \(job.str("status"))")
                default: break
                }
            } else if res.strs("missing").contains(jobId) {
                throw EnhanceFailure.failed("Job expired or the server restarted")
            }
            try await Task.sleep(nanoseconds: pollSeconds * 1_000_000_000)
        }
    }

    /// A photo's enhancement failed (that photo is saved as received; the run goes on).
    private enum EnhanceFailure: LocalizedError {
        case lost
        case failed(String)
        var errorDescription: String? {
            switch self {
            case .lost: return "Lost contact with the enhancement server"
            case .failed(let m): return m
            }
        }
    }
}
