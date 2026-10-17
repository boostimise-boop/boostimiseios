import Foundation
import UIKit
import Vision

struct Identification {
    var model: String
    var confidence: String
    var reason: String
}

private let rxSlashSpaces = Rx(#"\s+"#)
private let rxApple = Rx(#"^apple\s+(?=iphone)"#)
private let rxSamsungGalaxy = Rx(#"^samsung\s+galaxy\s+"#)
private let rxGalaxy = Rx(#"^galaxy\s+"#)
private let rxXiaomi = Rx(#"^xiaomi\s+(?=(redmi|poco)\b)"#)
private let rxPlus = Rx(#"(?<=[a-z0-9])\+(?=\s|$)"#)

func cleanModelName(_ name0: String?) -> String {
    var name = (name0 ?? "").trimmed.lowercased().replacingOccurrences(of: "/", with: " & ")
    name = rxSlashSpaces.replace(name, " ").trimming(" .,:;-\"'")
    name = rxApple.replace(name, "")
    name = rxSamsungGalaxy.replace(name, "samsung ")
    name = rxGalaxy.replace(name, "samsung ")
    name = rxXiaomi.replace(name, "")
    name = rxPlus.replace(name, " plus")
    return name.ifEmpty("unknown")
}

/// Draws an image at a size (scale 1, so the pixels are the size).
func resized(_ img: UIImage, _ w: Int, _ h: Int) -> UIImage {
    let format = UIGraphicsImageRendererFormat()
    format.scale = 1
    format.opaque = true
    return UIGraphicsImageRenderer(size: CGSize(width: w, height: h), format: format).image { _ in
        img.draw(in: CGRect(x: 0, y: 0, width: w, height: h))
    }
}

/// Photo bytes -> JPEG at most 1600 px (WhatsApp's own size), or nil when unusable.
func prepareImage(_ raw: Data, maxSide: Int = 1600) -> Data? {
    guard let img = UIImage(data: raw) else { return nil }
    let w = Int(img.size.width * img.scale), h = Int(img.size.height * img.scale)
    if min(w, h) < 48 { return nil }
    let k = min(1.0, Double(maxSide) / Double(max(w, h)))
    let out = resized(img, max(1, Int(Double(w) * k)), max(1, Int(Double(h) * k)))
    return out.jpegData(compressionQuality: 0.88)
}

/// Free model detection (tools/model_detect/text_identifier.py): the phone model name read from the
/// chat text posted with the photos and from text printed inside the photos (on-device text
/// recognition, Apple Vision): names ("Galaxy A06"), model numbers ("SM-A065F", "V2111") and brand logos.
final class TextIdentifier {
    private let log: (String) -> Void
    private var useOcr = true

    init(log: @escaping (String) -> Void) {
        self.log = log
    }

    private static let brandList: [([String], String)] = [
        (["apple", "iphone"], "iphone"), (["iphone"], "iphone"), (["i", "phone"], "iphone"),
        (["samsung", "galaxy"], "samsung"), (["samsung"], "samsung"), (["galaxy"], "samsung"),
        (["xiaomi", "redmi"], "redmi"), (["xiaomi", "poco"], "poco"), (["xiaomi", "mi"], "mi"),
        (["xiaomi"], "xiaomi"), (["redmi"], "redmi"), (["poco"], "poco"), (["mi"], "mi"),
        (["realme"], "realme"), (["narzo"], "realme narzo"), (["oppo"], "oppo"), (["vivo"], "vivo"),
        (["iqoo"], "iqoo"), (["infinix"], "infinix"), (["tecno"], "tecno"), (["itel"], "itel"),
        (["one", "plus"], "oneplus"), (["oneplus"], "oneplus"), (["nokia"], "nokia"),
        (["motorola"], "motorola"), (["moto"], "moto"), (["huawei"], "huawei"), (["honor"], "honor"),
        (["google", "pixel"], "google pixel"), (["pixel"], "google pixel"), (["symphony"], "symphony"),
        (["walton"], "walton"), (["lava"], "lava"), (["nothing", "phone"], "nothing phone"),
        (["asus"], "asus"), (["zte"], "zte"), (["nord"], "oneplus nord"),
        (["walton", "nexg"], "nexg"), (["nexg"], "nexg"), (["walton", "xanon"], "xanon"), (["xanon"], "xanon"),
        (["spark"], "tecno spark"), (["camon"], "tecno camon"), (["pova"], "tecno pova"),
        (["reno"], "oppo reno"), (["innova"], "symphony innova"), (["helio"], "symphony helio"),
    ]
    // longest first, keeping the list order among equals (Kotlin's sortedBy is stable)
    static let brands: [([String], String)] = stableSortedByLength(brandList) { $0.0.count }

    private static let words: Set<String> = [
        "pro", "max", "plus", "ultra", "lite", "mini", "prime", "neo", "power", "play", "note", "hot", "spark",
        "camon", "pova", "smart", "zero", "fold", "flip", "edge", "fe", "se", "xr", "xs", "x", "5g", "4g",
        "nord", "ce", "go", "core", "narzo", "reno", "find", "nova", "mate", "turbo", "star", "gt", "active",
        "air", "vip", "pop", "phantom", "innova", "helio", "xanon", "nexg",
        "e", "s", "t", "r", "i", "+", "pro+", "a", "c", "y", "v", "k", "f", "m", "p", "g", "z",
    ]
    private static let modelToken = Rx(#"^(?:[a-z]{0,6}\d{1,4}[a-z]{0,4}\+?|[a-z]\d{0,4})$"#)
    private static let numFirst = Rx(#"^\d{1,4}[a-z]?\+?$"#)
    private static let letterFirst = Rx(#"^[a-z]{1,4}\d{1,5}[a-z]{0,4}\+?$"#)
    private static let net: Set<String> = ["5g", "4g"]
    private static let year = Rx(#"^20[0-3]\d$"#)
    private static let seriesStart: Set<String> = ["note", "hot", "spark", "camon", "pova", "narzo", "nord", "reno", "find", "nova",
        "mate", "galaxy", "redmi", "poco", "iphone", "pixel", "smart", "zero", "edge"]
    private static let needsSeries: Set<String> = ["tecno", "infinix", "itel", "samsung", "vivo", "oppo", "poco", "symphony", "walton",
        "nexg", "motorola", "moto", "huawei", "lava", "zte", "asus"]
    private static let filler: Set<String> = ["cover", "covers", "case", "back", "er", "model", "mobile", "set", "phone", "for", "r",
        "no", "name", "number"]
    private static let osWord = Rx(#"^(ui|os)\d*$"#)
    private static let peelList: [String] = [
        "samsung", "galaxy", "apple", "iphone", "xiaomi", "redmi", "poco", "realme", "narzo", "oppo", "vivo",
        "infinix", "tecno", "itel", "oneplus", "nokia", "motorola", "huawei", "honor", "google", "pixel",
        "note", "hot", "spark", "camon", "pova", "nord", "reno", "nova", "mate", "pro", "max", "plus", "ultra",
        "lite", "mini", "mi", "smart", "zero", "walton", "symphony", "lava",
        "gt", "neo", "go", "pop", "nexg", "xanon", "innova", "helio",
        "dual", "sim", "global", "version", "snapdragon", "smartphone", "mobile", "price",
    ]
    private static let peel: [String] = stableSortedByLength(peelList) { $0.count }
    private static let digitSuffix = Rx(#"(\d)(pro|max|plus|ultra|lite|mini|dpi|prime|power|play|edge|(?:se|fe|neo|air|vip)(?![a-z]))"#)
    private static let glued5g = Rx(#"([a-z\d])([45]g)(?![0-9b])"#)
    private static let noise = Rx(#"\bs\s*/\s*n\b|\bimei\s*\d?|\b\d{1,2}\s*[/+]\s*\d{2,4}\s*(gb|ge|g|r)?\b"# +
        #"|\b\d{2,4}\s*[/+]\s*\d{1,2}\s*(gb|g|r)?\b|\b\d+\s*(gb|tb|mb|mp|mah|hz|mm|w)\b"#)
    private static let oForZero = Rx(#"^([a-z]{0,2})(\d[\do]*)([a-z]?)$"#)
    private static let spec = Rx(#"^\d+(gb|tb|mb|mp|mah|w|hz|mm)$"#)
    private static let watermarkWords = Rx(#"\b(?:shot|captured)\s*(?:on|by|with)\b|camera"#, ignoreCase: true)
    private static let notTokenChars = Rx(#"[^a-z0-9+]+"#)
    private static let singleLetter = Rx(#"[a-z]"#)
    private static let lettersThenDigit = Rx(#"^[a-z]{1,4}\d"#)
    private static let bigNumbers = Rx(#"\d{6,}"#)

    private static func splitGlued(_ tok0: String) -> [String] {
        var tok = tok0
        var out: [String] = []
        while true {
            let w = peel.first { w in
                guard tok.hasPrefix(w) else { return false }
                let rest = String(tok.dropFirst(w.count))
                return !rest.isEmpty && (rest.first!.isNumber || peel.contains { rest.hasPrefix($0) } || modelToken.matches(rest))
            }
            guard let w else {
                out.append(tok)
                return out
            }
            out.append(w)
            tok = String(tok.dropFirst(w.count))
        }
    }

    private static func fixOcrDigits(_ tok: String) -> String {
        guard let m = oForZero.matchEntire(tok) else { return tok }
        return m.group(1) + m.group(2).replacingOccurrences(of: "o", with: "0") + m.group(3)
    }

    static func tokens(_ text0: String) -> [String] {
        var text = ProductCodes.stripCodes(text0).lowercased()   // never read the product code as a model
        text = digitSuffix.replace(text, "$1 $2")
        text = noise.replace(text, " ")
        text = glued5g.replace(text, "$1 $2 ")
        return notTokenChars.replace(text, " ").split(separator: " ").map(String.init).filter { !$0.isEmpty }
            .flatMap { splitGlued($0) }.map { fixOcrDigits($0) }
    }

    private static func isModelToken(_ t: String) -> Bool {
        if words.contains(t) { return true }
        return !spec.matches(t) && (numFirst.matches(t) || letterFirst.matches(t) || singleLetter.matches(t))
    }

    private static func hasNumber(_ tokens: [String]) -> Bool { tokens.contains { $0.hasDigit } }
    private static func isAlpha(_ s: String) -> Bool { !s.isEmpty && s.allSatisfy { $0.isLetter } }

    private static func brandAt(_ toks: [String], _ i: Int) -> (String, Int)? {
        for (w, name) in brands where i + w.count <= toks.count && Array(toks[i..<(i + w.count)]) == w {
            return (name, w.count)
        }
        return nil
    }

    /// 'Samsung Galaxy A70 5G' -> ['samsung a70 5g'] ; 'I phone 17pro max cover' -> ['iphone 17 pro max']
    static func modelsWithBrand(_ text: String?) -> [String] {
        var out: [String] = []
        var lines = (text ?? "").lines()
        if lines.isEmpty { lines = [""] }
        for line in lines { out += modelsInLine(tokens(line)) }
        return out
    }

    private static func modelsInLine(_ toks: [String]) -> [String] {
        var out: [String] = []
        var i = 0
        func tok(_ j: Int) -> String { j < toks.count ? toks[j] : "" }
        while i < toks.count {
            guard let b = brandAt(toks, i) else {
                i += 1
                continue
            }
            let (name, n) = b
            var j = i + n
            if j < toks.count && osWord.matches(toks[j]) {
                i = j + 1
                continue
            }
            while j < toks.count && filler.contains(toks[j]) { j += 1 }
            var model: [String] = []
            if j + 1 < toks.count && !isModelToken(toks[j]) && isAlpha(toks[j]) && (2...10).contains(toks[j].count) &&
                !filler.contains(toks[j]) && brandAt(toks, j) == nil && isModelToken(toks[j + 1]) && hasNumber([toks[j + 1]]) {
                model.append(toks[j])
                j += 1
            }
            var extraWord = true
            var hasNum = false
            while j < toks.count && model.count < 5 {
                let t = toks[j]
                if !isModelToken(t) {
                    let nxt = tok(j + 1)
                    if extraWord && !model.isEmpty && !hasNum && isAlpha(t) && (2...10).contains(t.count) &&
                        !filler.contains(t) && brandAt(toks, j) == nil && isModelToken(nxt) &&
                        singleLetter.contains(nxt) && hasNumber([nxt]) {
                        extraWord = false
                        model.append(t)
                        j += 1
                        continue
                    }
                    break
                }
                if let nb = brandAt(toks, j), nb.0 != name, nb.0 != "samsung", !model.isEmpty { break }
                if hasNumber([t]) {
                    if net.contains(t) {
                        if !model.isEmpty {
                            model.append(t)
                            j += 1
                        }
                        break
                    }
                    if hasNum && !year.matches(t) { break }
                    hasNum = true
                }
                model.append(t)
                j += 1
            }
            if model.first == "galaxy" { model.removeFirst() }
            while let last = model.last, last.count == 1, last.first!.isLetter, !["x", "s", "e"].contains(last) {
                model.removeLast()
            }
            var joined: [String] = []
            for t in model {
                if let last = joined.last, name != "iphone", last.count == 1, last.first!.isLetter, t.isAllDigits {
                    joined[joined.count - 1] = last + t
                } else if joined.last != t {
                    joined.append(t)
                }
            }
            model = joined
            var ok = hasNumber(model) || (name == "iphone" && !model.isEmpty && ["x", "xr", "xs", "se"].contains(model[0]))
            let bare = !model.isEmpty && model[0].first!.isNumber && model[0].count != 4
            if (needsSeries.contains(name) && bare) || (name == "symphony helio" && !bare) { ok = false }
            if ok { out.append("\(name) \(model.joined(separator: " "))") }
            i = max(j, i + 1)
        }
        return out
    }

    /// A line that is only a model: 'A70', 'Note 7 pro', 'Y21 cover', 'c55 5g'.
    static func modelWithoutBrand(_ text: String) -> String? {
        let stripped = ProductCodes.stripCodes(text).trimmed
        if stripped.isEmpty || stripped.count > 30 { return nil }
        let toks = tokens(stripped).filter { !filler.contains($0) }
        if toks.isEmpty || toks.count > 5 || !toks.allSatisfy({ isModelToken($0) }) || !hasNumber(toks) { return nil }
        let first = toks[0]
        if !(lettersThenDigit.contains(first) || (seriesStart.contains(first) && toks.count > 1)) { return nil }
        return toks.joined(separator: " ")
    }

    /// (shop name, known) for the model written in a text, or nil.
    static func nameIn(_ text: String) -> (String, Bool?)? {
        let found = modelsWithBrand(text)
        if let f = found.first { return ModelNumbers.resolve(cleanModelName(f), text) }
        let numbers = ModelNumbers.namesFromCodes(text)
        if let n = numbers.first { return (cleanModelName(n), true) }
        return nil
    }

    static func short(_ text: String) -> String {
        "'" + text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ").take(60) + "'"
    }

    // MARK: - reading the photos

    private struct Row {
        let text: String
        let mid: Double
        let h: Double
    }

    /// Text lines in a photo, top to bottom; middle and height as fractions of the photo's height.
    private func readPhoto(_ cg: CGImage?) async -> [Row] {
        guard useOcr, let cg else { return [] }
        let observations: [VNRecognizedTextObservation]
        do {
            observations = try await Task.detached(priority: .userInitiated) { () throws -> [VNRecognizedTextObservation] in
                let req = VNRecognizeTextRequest()
                req.recognitionLevel = .accurate
                req.usesLanguageCorrection = false
                try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req])
                return req.results ?? []
            }.value
        } catch {
            if useOcr { log("Photo text reading (OCR) is not available: \(error.localizedDescription)") }
            useOcr = false
            return []
        }
        struct Box {
            let mid: Double
            let h: Double
            let left: Double
            let text: String
        }
        var boxes: [Box] = []
        for o in observations {
            guard let c = o.topCandidates(1).first, c.confidence >= 0.5 else { continue }
            let r = o.boundingBox   // normalised, origin bottom-left
            let top = 1 - (r.origin.y + r.size.height)
            boxes.append(Box(mid: top + r.size.height / 2, h: r.size.height, left: r.origin.x, text: c.string))
        }
        boxes.sort { a, b in a.mid != b.mid ? a.mid < b.mid : (a.h != b.h ? a.h < b.h : a.left < b.left) }
        var rows: [(mid: Double, h: Double, parts: [(Double, String)])] = []
        for b in boxes {
            if let last = rows.last, abs(b.mid - last.mid) < 0.5 * min(b.h, last.h) {
                rows[rows.count - 1].parts.append((b.left, b.text))
            } else {
                rows.append((b.mid, b.h, [(b.left, b.text)]))
            }
        }
        return rows.map { r in
            Row(text: r.parts.sorted { $0.0 < $1.0 }.map { $0.1 }.joined(separator: " "), mid: r.mid, h: r.h)
        }
    }

    private func watermark(_ rows: [Row], _ k: Int) -> Bool {
        let near = rows[max(0, k - 1)..<min(rows.count, k + 2)].map { $0.text }.joined(separator: " ")
        if Self.watermarkWords.contains(near) { return true }
        let r = rows[k]
        return r.mid > 0.85 && r.h < 0.05 && rows.count <= 6
    }

    private struct PhotoHit {
        let name: String
        let kind: String
        let text: String
    }

    private func inPhoto(_ rows: [Row], smallPrint: Bool = false) -> PhotoHit? {
        let texts = rows.map { $0.text }
        let whole = texts.joined(separator: "\n")
        var weak: PhotoHit?
        for k in texts.indices {
            let options = k + 1 < texts.count ? [[k], [k, k + 1]] : [[k]]
            for lines in options {
                let cand = lines.map { texts[$0] }.joined(separator: " ")
                let found = Self.modelsWithBrand(cand)
                guard let f = found.first else { continue }
                let (name, known) = ModelNumbers.resolve(cleanModelName(f), whole)
                if known == false {
                    weak = weak ?? PhotoHit(name: name, kind: "number?", text: cand)
                } else if !smallPrint && lines.contains(where: { watermark(rows, $0) }) {
                    weak = weak ?? PhotoHit(name: name, kind: "watermark", text: cand)
                } else {
                    return PhotoHit(name: name, kind: "sure", text: cand)
                }
                break
            }
        }
        let numbers = ModelNumbers.namesFromCodes(whole)
        if let n = numbers.first {
            return PhotoHit(name: cleanModelName(n), kind: "sure",
                            text: texts.first { !ModelNumbers.namesFromCodes($0).isEmpty } ?? whole)
        }
        return weak
    }

    private func logo(_ rows: [Row]) -> String? {
        for r in rows {
            let toks = Self.tokens(r.text)
            if !toks.isEmpty, let b = Self.brandAt(toks, 0), b.1 == toks.count { return b.0 }
        }
        return nil
    }

    private static func cgImage(_ jpeg: Data) -> CGImage? {
        guard let img = UIImage(data: jpeg) else { return nil }
        if img.imageOrientation == .up, let cg = img.cgImage { return cg }
        return resized(img, Int(img.size.width * img.scale), Int(img.size.height * img.scale)).cgImage
    }

    private static func scaled(_ cg: CGImage, _ f: Double) -> CGImage? {
        let w = max(1, Int(Double(cg.width) * f)), h = max(1, Int(Double(cg.height) * f))
        return resized(UIImage(cgImage: cg), w, h).cgImage
    }

    private func upscale(_ cg: CGImage, factor: Double = 2, limit: Int = 2600) -> CGImage? {
        let f = min(factor, Double(limit) / Double(max(cg.width, cg.height)))
        if f <= 1.05 { return nil }
        return Self.scaled(cg, f)
    }

    /// The photo cut in n x n overlapping parts, each enlarged, so small print becomes readable.
    private func tiles(_ cg: CGImage, n: Int = 2, overlap: Double = 0.15, target: Int = 1800) -> [CGImage] {
        let tw = Double(cg.width) / Double(n)
        let th = Double(cg.height) / Double(n)
        var out: [CGImage] = []
        for r in 0..<n {
            for c in 0..<n {
                let x0 = Int(max(0, (Double(c) - overlap) * tw))
                let y0 = Int(max(0, (Double(r) - overlap) * th))
                let x1 = Int(min(Double(cg.width), (Double(c + 1) + overlap) * tw))
                let y1 = Int(min(Double(cg.height), (Double(r + 1) + overlap) * th))
                guard var tile = cg.cropping(to: CGRect(x: x0, y: y0, width: x1 - x0, height: y1 - y0)) else { continue }
                let f = min(3.0, Double(target) / Double(max(tile.width, tile.height)))
                if f > 1.05, let big = Self.scaled(tile, f) { tile = big }
                out.append(tile)
            }
        }
        return out
    }

    /// texts: chat texts of the code, closest first. shared: texts that sit closer to another code -
    /// used only when nothing else is found. seen gets the text read in the photos (the AI's hint).
    /// Returns nil when no model name is written anywhere.
    func identify(_ images: [Data], texts: [String], shared: [String] = [], seen: inout [String], brandOnly: Bool = true) async -> Identification? {
        var later: [(Int, Int, Identification)] = []
        func keep(_ rank: Int, _ ident: Identification) { later.append((rank, later.count, ident)) }

        for t in texts {
            guard let hit = Self.nameIn(t) else { continue }
            if hit.1 != false { return Identification(model: hit.0, confidence: "high", reason: "written in chat: \(Self.short(t))") }
            keep(3, Identification(model: "\(hit.0) ?", confidence: "low", reason: "model number not in the phone list: \(Self.short(t))"))
        }

        func photoHit(_ hit: PhotoHit, _ wherePhoto: String) -> Identification? {
            switch hit.kind {
            case "sure":
                return Identification(model: hit.name, confidence: "high", reason: "\(wherePhoto): \(Self.short(hit.text))")
            case "number?":
                keep(3, Identification(model: "\(hit.name) ?", confidence: "low",
                                       reason: "model number not in the phone list, \(wherePhoto): \(Self.short(hit.text))"))
            default:
                keep(4, Identification(model: hit.name, confidence: "low",
                                       reason: "camera watermark, \(wherePhoto): \(Self.short(hit.text)) - the photo may be taken with another phone"))
            }
            return nil
        }

        let pictures = images.map { Self.cgImage($0) }
        var logoHit: (String, Int)?
        for enlarge in [false, true] {
            for (k0, cg) in pictures.enumerated() {
                let k = k0 + 1
                guard let cg else { continue }
                let rows = await readPhoto(enlarge ? upscale(cg) : cg)
                if !enlarge { seen += rows.map { $0.text } }
                if let hit = inPhoto(rows), let id = photoHit(hit, "text in photo \(k)") { return id }
                if logoHit == nil, let lg = logo(rows) { logoHit = (lg, k) }
            }
        }

        for t in texts {
            for line in t.lines() {
                guard let found = Self.modelWithoutBrand(line) else { continue }
                if let lg = logoHit, let both = Self.modelsWithBrand("\(lg.0) \(found)").first {
                    return Identification(model: cleanModelName(both), confidence: "medium",
                                          reason: "written in chat: '\(line.take(40))', brand in photo \(lg.1)")
                }
                return Identification(model: cleanModelName(found), confidence: "medium", reason: "written in chat (no brand): '\(line.take(40))'")
            }
        }

        // a brand is printed on the phone: its model number may be in the small print next to it
        if let lg = logoHit, let cg = pictures[lg.1 - 1] {
            let k = lg.1
            for tile in tiles(cg) {
                if let hit = inPhoto(await readPhoto(tile), smallPrint: true), let id = photoHit(hit, "small print in photo \(k)") { return id }
            }
        }

        for t in shared {
            guard let hit = Self.nameIn(t) else { continue }
            keep(2, Identification(model: hit.0 + (hit.1 == false ? " ?" : ""), confidence: "low",
                                   reason: "written next to another code: \(Self.short(t))"))
            break
        }
        if let best = later.min(by: { $0.0 != $1.0 ? $0.0 < $1.0 : $0.1 < $1.1 }) { return best.2 }
        if let lg = logoHit, brandOnly {
            return Identification(model: "\(lg.0) ?", confidence: "low", reason: "only the brand '\(lg.0)' is printed (photo \(lg.1))")
        }
        return nil
    }

    /// Chat text sent to Claude, with long numbers (phones) hidden.
    static func hideNumbers(_ s: String) -> String { bigNumbers.replace(s, "#") }
}

/// Longest first; equal lengths keep their order.
func stableSortedByLength<T>(_ items: [T], _ length: (T) -> Int) -> [T] {
    let indexed = Array(items.enumerated())
    return indexed.sorted { a, b in
        let la = length(a.element), lb = length(b.element)
        return la != lb ? la > lb : a.offset < b.offset
    }.map { $0.element }
}
