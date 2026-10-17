import ImageIO
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

private let modes = [("auto", "Auto"), ("face", "Face"), ("restore", "Restore"), ("upscale", "Upscale")]
private let stages = ["analysis": "Analysis", "face_detection": "Face detection", "face_restoration": "Face restoration",
                      "upscaling": "Upscaling", "finalizing": "Color / sharpen", "saving": "Saving", "queued": "In queue",
                      "waiting": "Waiting", "uploading": "Uploading"]

/// Small copies of the picked photos for the screen (the originals can be 50 MB).
@MainActor
enum Thumbs {
    private static var cache: [URL: UIImage] = [:]

    static func image(_ url: URL, _ maxPixel: Int) -> UIImage? {
        if let i = cache[url] { return i }
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true, kCGImageSourceCreateThumbnailWithTransform: true,
                                     kCGImageSourceThumbnailMaxPixelSize: maxPixel]
        guard let cg = CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary) else { return nil }
        let img = UIImage(cgImage: cg)
        if cache.count > 80 { cache.removeAll() }
        cache[url] = img
        return img
    }
}

struct EnhancerScreen: View {
    @State private var picks: [PhotosPickerItem] = []
    @State private var importing = false
    @State private var advanced = false

    var body: some View {
        let t = Tools.enhancer
        Page {
            let q = Account.shared.quota
            HStack {
                Text(q?.limit == nil ? "Unlimited enhancements today"
                     : "\(q?.remaining ?? 0) of \(q!.limit!) left today" + ((q?.resetsAt ?? "").isEmpty ? "" : " · resets in \(formatResetIn(q!.resetsAt))"))
                    .font(.system(size: 13)).foregroundStyle(Brand.text2)
                Spacer()
                if t.offline { Text("Server offline").font(.system(size: 13)).foregroundStyle(Brand.error) }
            }
            if t.items.isEmpty {
                Card {
                    VStack(spacing: 10) {
                        Text("AI Photo Enhancer").font(.system(size: 22, weight: .bold)).foregroundStyle(Brand.navy)
                        Hint("Upscale 2× / 4×, restore faces and old photos. JPG, PNG or WEBP (iPhone photos are converted), up to \(Int(t.maxUploadMb())) MB.")
                            .multilineTextAlignment(.center)
                        HStack(spacing: 8) {
                            PhotosPicker(selection: $picks, maxSelectionCount: 50, matching: .images) {
                                Text("Upload Images").font(.system(size: 15, weight: .semibold)).padding(.horizontal, 6)
                            }
                            .buttonStyle(.borderedProminent)
                            SecondaryButton("From Files") { importing = true }
                        }
                        Hint("Or open photos in Boostimise from the Files app.")
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 24)
                }
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(t.items) { it in
                            Thumb(item: it, selected: it.id == t.selected?.id) {
                                t.selectedId = it.id
                                t.ensureAnalysis(it)
                            }
                        }
                        PhotosPicker(selection: $picks, maxSelectionCount: 50, matching: .images) {
                            Text("+").font(.system(size: 28)).foregroundStyle(Brand.accent)
                                .frame(width: 72, height: 72)
                                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Brand.border))
                        }
                    }
                }
                if let sel = t.selected {
                    selectedCard(t, sel)
                    if let a = t.analysisOf(sel) { AnalysisCard(data: a) }
                    settingsCard(t)
                    let startable = t.items.filter { isStartable($0.status) }.count
                    PrimaryButton(t.applyAll && startable > 1 ? "ENHANCE \(startable) PHOTOS" : "ENHANCE PHOTO", full: true,
                                  enabled: !t.offline && (t.applyAll ? startable > 0 : isStartable(sel.status))) {
                        t.enhance(onlySelected: true)
                    }
                    CheckRow("Enhance all photos at once", Binding(get: { t.applyAll }, set: { t.applyAll = $0 }))
                    if t.items.filter({ $0.status == "done" }).count > 1 {
                        HStack(spacing: 8) {
                            SecondaryButton("All as JPG (.zip)", enabled: t.downloading.isEmpty) { t.downloadZip("jpg") }
                            SecondaryButton("All as PNG (.zip)", enabled: t.downloading.isEmpty) { t.downloadZip("png") }
                        }
                    }
                    if !t.downloading.isEmpty { Hint("Downloading…") }
                    Hint("Downloads go to Photos and to Files › Boostimise › Enhanced (ZIPs to Files › Boostimise).")
                }
            }
        }
        .onAppear { t.startHealth() }
        .onDisappear { t.stopHealth() }
        .onChange(of: picks) { _, p in
            if !p.isEmpty {
                t.add(pickerItems: p)
                picks = []
            }
        }
        .onChange(of: t.toast) { _, s in
            if !s.isEmpty {
                toast(s)
                t.toast = ""
            }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.image], allowsMultipleSelection: true) { r in
            if case .success(let urls) = r { t.add(urls: urls) }
        }
        .alert(t.denied == "blocked" ? "Account suspended" : "Daily limit reached",
               isPresented: Binding(get: { !t.denied.isEmpty }, set: { if !$0 { t.denied = "" } })) {
            if t.denied == "limit" { Button("See plans") { t.denied = ""; Nav.shared.go(.plans) } }
            Button("Close", role: .cancel) { t.denied = "" }
        } message: {
            let q = Account.shared.quota
            Text(t.denied == "blocked" ? "Your account has been suspended."
                 : "You've used today's enhancements" + ((q?.resetsAt ?? "").isEmpty ? "" : " - more in \(formatResetIn(q!.resetsAt))") +
                   ". A paid plan gives you more every day.")
        }
    }

    @ViewBuilder
    private func selectedCard(_ t: EnhancerTool, _ sel: QueueItem) -> some View {
        Card {
            Compare(item: sel)
            Text(sel.name).font(.system(size: 14, weight: .medium)).foregroundStyle(Brand.navy)
            Hint((sel.dims.map { "\($0.0)×\($0.1) · " } ?? "") + String(format: "%.1f MB", Double(sel.size) / 1_048_576) + " · " +
                 (stages[sel.stage] ?? sel.status))
            if sel.status == "uploading" { ProgressView(value: sel.upload).tint(Brand.accent) }
            if sel.status == "processing" || sel.status == "queued" { ProgressView(value: min(sel.progress / 100, 1)).tint(Brand.accent) }
            if let e = sel.error { Hint(e, Brand.error) }
            if sel.status == "done" {
                HStack(spacing: 8) {
                    PrimaryButton("Download PNG", enabled: t.downloading.isEmpty && sel.job?.bool("png_ready", true) != false) { t.download(sel, "png") }
                    SecondaryButton("JPG", enabled: t.downloading.isEmpty) { t.download(sel, "jpg") }
                }
                if let b = sel.job?.obj("result")?.obj("benchmark") {
                    Hint("\(b.str("input")) → \(b.str("output")) · \(String(format: "%.1f", b.double("total_seconds"))) s" +
                         (b.int("faces_detected") > 0 ? " · \(b.int("faces_restored")) of \(b.int("faces_detected")) faces restored" : ""))
                }
            }
            HStack(spacing: 16) {
                LinkButton("Remove") { t.remove(sel) }
                LinkButton("Remove all") { t.clear() }
            }
        }
        .onAppear { t.ensureAnalysis(sel) }
    }

    @ViewBuilder
    private func settingsCard(_ t: EnhancerTool) -> some View {
        let s = t.settings
        Card(title: "Settings") {
            Flow {
                ForEach(modes.indices, id: \.self) { i in
                    Chip(label: modes[i].1, selected: modes[i].0 == s.mode) { t.settings = presetFor(modes[i].0, s) }
                }
            }
            Flow {
                ForEach([2, 4], id: \.self) { sc in
                    Chip(label: "\(sc)×", selected: sc == s.scale) {
                        var n = s
                        n.scale = sc
                        t.settings = n
                    }
                }
            }
            CheckRow("Face restoration", Binding(get: { t.settings.faceEnhance }, set: { t.settings.faceEnhance = $0 }))
            CheckRow("Colour enhance", Binding(get: { t.settings.colorEnhance }, set: { t.settings.colorEnhance = $0 }))
            LinkButton(advanced ? "Hide advanced" : "Advanced") { advanced.toggle() }
            if advanced {
                if s.faceEnhance {
                    SelectField(label: "Face model", options: [("auto", "Auto (recommended)"), ("codeformer", "Balanced"),
                                                              ("gfpgan", "Smooth"), ("compare", "Compare")],
                                value: Binding(get: { t.settings.faceModel }, set: { t.settings.faceModel = $0 }))
                }
                Text("Strength \(String(format: "%.2f", s.strength))").font(.system(size: 13))
                Slider(value: Binding(get: { t.settings.strength }, set: { t.settings.strength = $0 }), in: 0...1)
                AutoSlider(label: "Denoise", value: Binding(get: { t.settings.denoise }, set: { t.settings.denoise = $0 }))
                AutoSlider(label: "Sharpen", value: Binding(get: { t.settings.sharpen }, set: { t.settings.sharpen = $0 }))
            }
        }
    }
}

private struct AutoSlider: View {
    let label: String
    @Binding var value: Double?

    var body: some View {
        CheckRow("\(label): automatic", Binding(get: { value == nil }, set: { value = $0 ? nil : 0.5 }))
        if let v = value {
            Slider(value: Binding(get: { v }, set: { value = $0 }), in: 0...1)
        }
    }
}

private struct Thumb: View {
    let item: QueueItem
    let selected: Bool
    let onTap: () -> Void

    var body: some View {
        let c: Color? = {
            switch item.status {
            case "done": return Brand.success
            case "failed", "cancelled": return Brand.error
            case "idle": return nil
            default: return Brand.accent
            }
        }()
        Button(action: onTap) {
            ZStack(alignment: .bottomTrailing) {
                if let img = Thumbs.image(item.file, 200) {
                    Image(uiImage: img).resizable().scaledToFill().frame(width: 72, height: 72).clipped()
                } else {
                    Brand.border.frame(width: 72, height: 72)
                }
                if let c { Circle().fill(c).frame(width: 10, height: 10).padding(4) }
            }
            .frame(width: 72, height: 72)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(selected ? Brand.accent : Brand.border, lineWidth: selected ? 3 : 1))
        }
        .buttonStyle(.plain)
    }
}

/// Before / after: drag the divider.
private struct Compare: View {
    let item: QueueItem
    @State private var split: CGFloat = 0.5

    var body: some View {
        let ratio: CGFloat = item.dims.map { CGFloat($0.0) / CGFloat(max($0.1, 1)) } ?? 1
        GeometryReader { geo in
            let w = geo.size.width
            ZStack {
                if let img = Thumbs.image(item.file, 1400) {
                    Image(uiImage: img).resizable().scaledToFit().frame(width: w, height: geo.size.height)
                }
                if item.status == "done", let job = item.job, let url = URL(string: EnhancerApi.resultURL(job, "preview")) {
                    AsyncImage(url: url) { phase in
                        if let image = phase.image { image.resizable().scaledToFit() } else { Color.clear }
                    }
                    .frame(width: w, height: geo.size.height)
                    .mask(alignment: .trailing) { Rectangle().frame(width: w * (1 - split)) }
                    Rectangle().fill(Color.white).frame(width: 2).position(x: w * split, y: geo.size.height / 2)
                    Text("Before").font(.system(size: 11)).foregroundStyle(.white).padding(6)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    Text("After").font(.system(size: 11)).foregroundStyle(.white).padding(6)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topTrailing)
                }
            }
            .contentShape(Rectangle())
            .gesture(DragGesture(minimumDistance: 0).onChanged { g in split = min(max(g.location.x / max(w, 1), 0), 1) })
        }
        .aspectRatio(min(max(ratio, 0.5), 2), contentMode: .fit)
        .background(Color(hex: 0x111827))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

private struct AnalysisCard: View {
    let data: JSON

    var body: some View {
        if let a = data.obj("analysis") {
            let faces = data.arr("faces")?.count ?? 0
            Card(title: "AI analysis") {
                Hint("quality \(Int(a.double("quality_score")))/100 · \(a.int("width"))×\(a.int("height")) · " +
                     "\(faces) face\(faces == 1 ? "" : "s")" + (a.bool("grayscale") ? " · B&W" : ""))
                bar("Blur", min(max((a.double("blur") - 0.3) / 0.45, 0), 1))
                bar("Noise", a.double("noise"))
                bar("Compression", a.double("jpeg_artifacts"))
                bar("Low resolution", a.double("low_resolution"))
                ForEach(data.obj("plan")?.strs("reasons") ?? [], id: \.self) { Hint("✓ \($0)") }
            }
        }
    }

    private func bar(_ name: String, _ v: Double) -> some View {
        HStack {
            Text(name).font(.system(size: 12)).frame(width: 110, alignment: .leading)
            ProgressView(value: min(max(v, 0), 1)).tint(Brand.accent)
        }
    }
}
