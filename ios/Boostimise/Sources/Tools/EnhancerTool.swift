import Foundation
import ImageIO
import Observation
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

private let MAX_ACTIVE = 2       // jobs on the server at once (1 processing + 1 queued keeps the GPU busy)
private let POLL_NS: UInt64 = 600_000_000
private let ACTIVE: Set<String> = ["uploading", "queued", "processing"]

func isFinished(_ s: String) -> Bool { ["done", "failed", "cancelled"].contains(s) }
func isStartable(_ s: String) -> Bool { s == "idle" || isFinished(s) }

/// One photo in the list (tools/photo_enhancer/queue.py's QueueItem).
@MainActor @Observable
final class QueueItem: Identifiable {
    let id: String
    let file: URL
    let name: String
    let size: Int
    var settings: EnhanceSettings
    var dims: (Int, Int)?
    var status = "idle"   // idle waiting uploading queued processing done failed cancelled
    var stage = "idle"
    var progress = 0.0    // 0...100 (server)
    var upload = 0.0      // 0...1
    var jobId: String?
    var job: JSON?
    var error: String?
    @ObservationIgnored var recordId: String?
    @ObservationIgnored var uploadJob: Task<Void, Never>?

    init(id: String, file: URL, name: String, size: Int, settings: EnhanceSettings) {
        self.id = id
        self.file = file
        self.name = name
        self.size = size
        self.settings = settings
    }
}

/// Width and height of an image file without decoding it.
func imageSize(_ url: URL) -> (Int, Int)? {
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let p = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
          let w = p[kCGImagePropertyPixelWidth] as? Int, let h = p[kCGImagePropertyPixelHeight] as? Int else { return nil }
    let o = p[kCGImagePropertyOrientation] as? Int ?? 1
    return o >= 5 ? (h, w) : (w, h)
}

/// The AI Photo Enhancer - the website's enhancer (lib/useEnhanceQueue.ts): photos wait until you press
/// Enhance; uploads are paced a few jobs ahead of the GPU; all active jobs are polled with one request.
/// Each enhancement is taken from the account's daily quota first and its outcome recorded.
@MainActor @Observable
final class EnhancerTool {
    var items: [QueueItem] = []
    var selectedId: String?
    var settings = EnhanceSettings()
    var applyAll = false
    private(set) var health: JSON?
    private(set) var offline = false
    /// "<id>:<scale>" -> analysis (nil value = failed / still running)
    var analyses: [String: JSON?] = [:]
    var toast = ""
    var denied = ""       // limit | blocked | onboarding - shows the limit dialog
    private(set) var downloading = ""
    @ObservationIgnored private var polling = false
    @ObservationIgnored private var failures = 0
    @ObservationIgnored private var cooldownUntil = Date.distantPast
    @ObservationIgnored private var batchAnnounced = true
    @ObservationIgnored private var counter = 0
    @ObservationIgnored private var healthJob: Task<Void, Never>?

    var selected: QueueItem? { items.first { $0.id == selectedId } ?? items.first }

    func maxUploadMb() -> Double { health?.obj("limits")?.double("max_upload_mb", 50) ?? 50 }

    func startHealth() {
        if healthJob != nil { return }
        healthJob = Task {
            while !Task.isCancelled {
                do {
                    health = try await EnhancerApi.health()
                    offline = false
                } catch is CancellationError {
                    return
                } catch {
                    offline = true
                }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }

    func stopHealth() {
        healthJob?.cancel()
        healthJob = nil
    }

    private func updateBusy() {
        let active = items.filter { ACTIVE.contains($0.status) || $0.status == "waiting" }.count
        if active > 0 { Jobs.shared.begin("enhancer", "Enhancing \(active) photo(s)…") } else { Jobs.shared.end("enhancer") }
    }

    // MARK: - adding

    /// Photos chosen in the photo picker. iPhone photos are usually HEIC: those become JPEG.
    func add(pickerItems: [PhotosPickerItem]) {
        Task {
            var files: [URL] = []
            for (i, p) in pickerItems.enumerated() {
                guard let data = try? await p.loadTransferable(type: Data.self) else { continue }
                let types = p.supportedContentTypes
                let stamp = Int(Date().timeIntervalSince1970)
                let ext: String
                var bytes = data
                if types.contains(where: { $0.conforms(to: .jpeg) }) { ext = "jpg" }
                else if types.contains(where: { $0.conforms(to: .png) }) { ext = "png" }
                else if types.contains(where: { $0.conforms(to: .webP) }) { ext = "webp" }
                else {
                    guard let img = UIImage(data: data), let jpg = img.jpegData(compressionQuality: 0.95) else { continue }
                    bytes = jpg
                    ext = "jpg"
                }
                let url = Storage.freeCacheFile("enhancer", "IMG_\(stamp)_\(i + 1).\(ext)")
                if (try? bytes.write(to: url)) != nil { files.append(url) }
            }
            addFiles(files, copied: true)
        }
    }

    /// Photos from the Files app or "Open in Boostimise".
    func add(urls: [URL]) {
        var files: [URL] = []
        for u in urls {
            let scoped = u.startAccessingSecurityScopedResource()
            defer { if scoped { u.stopAccessingSecurityScopedResource() } }
            let ext = u.pathExtension.lowercased()
            if EnhancerApi.accepted.contains(ext) {
                let dest = Storage.freeCacheFile("enhancer", u.lastPathComponent)
                if (try? FileManager.default.copyItem(at: u, to: dest)) != nil { files.append(dest) }
            } else if let data = try? Data(contentsOf: u), let img = UIImage(data: data), let jpg = img.jpegData(compressionQuality: 0.95) {
                let dest = Storage.freeCacheFile("enhancer", (u.lastPathComponent as NSString).deletingPathExtension + ".jpg")
                if (try? jpg.write(to: dest)) != nil { files.append(dest) }
            } else {
                toast = "\(u.lastPathComponent): Unsupported file type. Please use JPG, JPEG, PNG or WEBP."
            }
        }
        addFiles(files, copied: true)
    }

    private func addFiles(_ files: [URL], copied: Bool) {
        let maxMb = maxUploadMb()
        let q = Account.shared.quota
        var added = 0
        var skipped = 0
        for file in files {
            let size = ((try? FileManager.default.attributesOfItem(atPath: file.path)[.size]) as? NSNumber)?.intValue ?? 0
            let ext = file.pathExtension.lowercased()
            var err: String?
            if !EnhancerApi.accepted.contains(ext) { err = "Unsupported file type. Please use JPG, JPEG, PNG or WEBP." }
            else if size == 0 { err = "The file is empty." }
            else if Double(size) > maxMb * 1024 * 1024 { err = "File is too large (max \(Int(maxMb)) MB)." }
            if let err {
                toast = "\(file.lastPathComponent): \(err)"
                try? FileManager.default.removeItem(at: file)
                skipped += 1
                continue
            }
            let it = QueueItem(id: "\(String(Int(Date().timeIntervalSince1970 * 1000), radix: 16))-\(counter)", file: file,
                               name: file.lastPathComponent, size: size, settings: settings)
            counter += 1
            it.dims = imageSize(file)
            items.append(it)
            added += 1
            if selectedId == nil { selectedId = it.id }
        }
        if let rem = q?.remaining, added > rem {
            toast = "You have \(rem) enhancement(s) left today; the rest wait for tomorrow or a plan."
        }
        if skipped > 0 && toast.isEmpty { toast = "\(skipped) file(s) could not be added." }
        if let s = selected { ensureAnalysis(s) }
    }

    func ensureAnalysis(_ it: QueueItem) {
        let key = "\(it.id):\(settings.scale)"
        if analyses.keys.contains(key) || offline { return }
        analyses[key] = .some(nil)
        let scale = settings.scale
        Task {
            let a = try? await EnhancerApi.analyze(it.file, it.name, scale: scale)
            analyses[key] = .some(a ?? nil)
        }
    }

    func analysisOf(_ it: QueueItem) -> JSON? { analyses["\(it.id):\(settings.scale)"] ?? nil }

    // MARK: - running

    func enhance(onlySelected: Bool) {
        let ids: Set<String>? = onlySelected && !applyAll ? Set([selected?.id].compactMap { $0 }) : nil
        let targets = items.filter { isStartable($0.status) && (ids == nil || ids!.contains($0.id)) }
        if targets.isEmpty { return }
        for it in targets { if let j = it.jobId { Task { await EnhancerApi.cancel(j) } } }
        if targets.count > 1 { batchAnnounced = false }
        for it in targets {
            it.settings = settings
            it.status = "waiting"
            it.stage = "waiting"
            it.progress = 0
            it.upload = 0
            it.jobId = nil
            it.job = nil
            it.error = nil
            it.recordId = nil
        }
        updateBusy()
        pump()
    }

    private func pump() {
        let wait = cooldownUntil.timeIntervalSinceNow
        if wait > 0 {
            Task {
                try? await Task.sleep(nanoseconds: UInt64((wait + 0.05) * 1_000_000_000))
                pump()
            }
            return
        }
        while true {
            let active = items.filter { ACTIVE.contains($0.status) }.count
            guard let next = items.first(where: { $0.status == "waiting" }), active < MAX_ACTIVE else { break }
            next.status = "uploading"
            next.stage = "uploading"
            next.upload = 0
            start(next)
        }
        let sent = items.filter { $0.status != "idle" }
        if sent.count > 1 && !batchAnnounced && sent.allSatisfy({ isFinished($0.status) }) {
            batchAnnounced = true
            let ok = sent.filter { $0.status == "done" }.count
            if ok > 0 { toast = "\(ok) of \(sent.count) photos enhanced" }
        }
        updateBusy()
    }

    private func contains(_ it: QueueItem) -> Bool { items.contains { $0 === it } }

    private func start(_ it: QueueItem) {
        it.uploadJob = Task {
            let res = await Account.shared.reserveEnhancement(fileName: it.name, fileSize: it.size, mode: it.settings.mode,
                                                              scale: it.settings.scale, width: it.dims?.0, height: it.dims?.1)
            Account.shared.setQuota(res.quota)
            if !contains(it) || it.status != "uploading" {
                if let id = res.id { await Account.shared.finishEnhancement(id, status: "cancelled") }
                pump()
                return
            }
            if !res.ok {
                // nothing more can start today: park this photo and everything still waiting
                for i in items where i === it || i.status == "waiting" {
                    i.status = "idle"
                    i.stage = "idle"
                    i.upload = 0
                }
                if res.code == "onboarding" { Account.shared.refresh() } else { denied = res.code }
                updateBusy()
                return
            }
            it.recordId = res.id
            do {
                let jobId = try await EnhancerApi.submit(it.file, it.name, it.settings) { f in it.upload = f }
                if !contains(it) {
                    await EnhancerApi.cancel(jobId)
                    settle(res.id, "cancelled")
                } else {
                    if let id = res.id { Task { await Account.shared.finishEnhancement(id, jobId: jobId) } }
                    it.status = "queued"
                    it.stage = "queued"
                    it.jobId = jobId
                    poll()
                }
            } catch is CancellationError {
                settle(res.id, "cancelled")
            } catch let e as HttpError {
                if e.status == 503 {   // the server's queue is full (other users): try again shortly
                    settle(res.id, "cancelled")
                    it.status = "waiting"
                    it.stage = "waiting"
                    it.recordId = nil
                    cooldownUntil = Date().addingTimeInterval(3)
                } else {
                    let msg = e.status == 0 ? "Cannot reach the enhancement server." : e.message
                    settle(res.id, "failed", error: msg)
                    it.status = "failed"
                    it.stage = "failed"
                    it.error = msg
                    toast = "\(it.name): \(msg)"
                }
            } catch {
                settle(res.id, "failed", error: error.localizedDescription)
                it.status = "failed"
                it.error = error.localizedDescription
            }
            pump()
        }
    }

    private func settle(_ recordId: String?, _ status: String, error: String? = nil, jobId: String? = nil,
                        w: Int? = nil, h: Int? = nil, secs: Double? = nil) {
        guard let recordId else { return }
        Task {
            await Account.shared.finishEnhancement(recordId, status: status, jobId: jobId, outW: w, outH: h, seconds: secs, error: error)
            if status != "completed" {
                try? await Task.sleep(nanoseconds: 600_000_000)
                Account.shared.refresh()   // failed / cancelled ones are refunded
            }
        }
    }

    private func tracked() -> [QueueItem] {
        items.filter {
            $0.jobId != nil && ($0.status == "queued" || $0.status == "processing" ||
                ($0.status == "done" && $0.job?.has("png_ready") == true && $0.job?.bool("png_ready") == false))
        }
    }

    private func poll() {
        if polling { return }
        polling = true
        Task {
            while true {
                let tr = tracked()
                if tr.isEmpty { break }
                do {
                    let res = try await EnhancerApi.jobs(tr.compactMap { $0.jobId }) ?? [:]
                    failures = 0
                    onJobs(tr, res)
                } catch {
                    failures += 1
                    if failures >= 20 {
                        for it in tr where it.status != "done" {
                            settle(it.recordId, "failed", error: "Lost contact with the enhancement server")
                            it.status = "failed"
                            it.error = "Lost contact with the enhancement server"
                        }
                        toast = "Lost contact with the enhancement server"
                        failures = 0
                        break
                    }
                }
                try? await Task.sleep(nanoseconds: POLL_NS)
            }
            polling = false
            updateBusy()
        }
    }

    private func onJobs(_ tracked: [QueueItem], _ res: JSON) {
        var byId: [String: JSON] = [:]
        for j in res.objs("jobs") { byId[j.str("job_id")] = j }
        let missing = Set(res.strs("missing"))
        var finished = false
        for it in tracked {
            guard let jid = it.jobId else { continue }
            guard let j = byId[jid] else {
                if missing.contains(jid) && contains(it) {
                    if it.status != "done" { settle(it.recordId, "failed", error: "Job expired or the server restarted") }
                    it.status = "failed"
                    it.error = "Job expired or the server restarted"
                    finished = true
                }
                continue
            }
            if !contains(it) { continue }
            let status = j.str("status")
            switch status {
            case "completed":
                if it.status != "done" {
                    finished = true
                    let result = j.obj("result") ?? [:]
                    let bench = result.obj("benchmark") ?? [:]
                    let inp = j.obj("input") ?? [:]
                    let w = result.int("width") > 0 ? result.int("width") : (inp.int("output_width") > 0 ? inp.int("output_width") : nil)
                    let h = result.int("height") > 0 ? result.int("height") : (inp.int("output_height") > 0 ? inp.int("output_height") : nil)
                    settle(it.recordId, "completed", jobId: jid, w: w, h: h, secs: bench.doubleOrNil("total_seconds"))
                    if batchAnnounced && result.has("width") {
                        toast = "Enhanced to \(result.int("width"))×\(result.int("height")) in " +
                            String(format: "%.1fs", bench.double("total_seconds"))
                    }
                }
                it.status = "done"
                it.stage = "done"
                it.progress = 100
                it.job = j
            case "failed", "cancelled":
                let err = j.obj("error")?.strOrNil("message")
                settle(it.recordId, status, error: err)
                it.status = status
                it.stage = status
                it.job = j
                it.error = err
                if status == "failed" { toast = "\(it.name): \(err ?? "processing failed")" }
                finished = true
            default:
                let queued = status == "queued"
                it.status = queued ? "queued" : "processing"
                it.stage = queued ? "queued" : j.str("stage").ifEmpty("processing")
                it.progress = j.double("progress")
                it.job = j
            }
        }
        if finished { pump() }
    }

    // MARK: - editing the list

    private func stop(_ it: QueueItem) {
        it.uploadJob?.cancel()
        if let j = it.jobId { Task { await EnhancerApi.cancel(j) } }   // cancels if running, deletes results if finished
        if it.status == "queued" || it.status == "processing" { settle(it.recordId, "cancelled") }
    }

    func remove(_ it: QueueItem) {
        stop(it)
        items.removeAll { $0 === it }
        try? FileManager.default.removeItem(at: it.file)
        if selectedId == it.id { selectedId = items.first?.id }
        pump()
    }

    func clear() {
        for it in items {
            stop(it)
            try? FileManager.default.removeItem(at: it.file)
        }
        items.removeAll()
        selectedId = nil
        updateBusy()
    }

    // MARK: - downloads

    func download(_ it: QueueItem, _ kind: String) {
        guard let job = it.job else { return }
        Task {
            downloading = kind
            defer { downloading = "" }
            do {
                let (tmp, name) = try await EnhancerApi.download(EnhancerApi.resultURL(job, kind),
                    fallback: "\(job.str("original_name").ifEmpty("photo"))_enhanced.\(kind)")
                let saved = try Storage.move(tmp, "Enhanced", name)
                let inPhotos = await Storage.saveToPhotos(saved)
                toast = inPhotos ? "Saved to Photos and to Files › Boostimise › Enhanced"
                    : "Saved to Files › Boostimise › Enhanced › \(name)"
            } catch {
                toast = "Download failed: \(error.localizedDescription)"
            }
        }
    }

    /// One ZIP with all finished results, in Files › Boostimise.
    func downloadZip(_ fmt: String) {
        let ids = items.filter { $0.status == "done" }.compactMap { $0.jobId }
        if ids.isEmpty { return }
        Task {
            downloading = "zip"
            defer { downloading = "" }
            do {
                let (tmp, name) = try await EnhancerApi.download("\(Config.apiBase)/api/download-zip",
                    fallback: "enhanced_photos_\(ids.count)_\(fmt).zip", form: ["ids": ids.joined(separator: ","), "format": fmt])
                try Storage.move(tmp, "", name)
                toast = "Saved to Files › Boostimise › \(name)"
            } catch {
                toast = "Download failed: \(error.localizedDescription)"
            }
        }
    }
}
