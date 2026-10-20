import Foundation

let BN_DIGITS = Array("০১২৩৪৫৬৭৮৯")

/// Bangla digits -> ASCII digits.
func bnToAscii(_ s: String) -> String {
    var out = ""
    out.reserveCapacity(s.count)
    for ch in s {
        if let i = BN_DIGITS.firstIndex(of: ch) { out.append(Character(String(i))) } else { out.append(ch) }
    }
    return out
}

private let badChars = Rx(#"[<>:"/\\|?*\x00-\x1f]"#)
private let reservedNames: Set<String> = Set(["CON", "PRN", "AUX", "NUL"] + (1...9).map { "COM\($0)" } + (1...9).map { "LPT\($0)" })

func safeFileName(_ name: String) -> String {
    var n = badChars.replace(name, "-")
    n = squeezeSpaces(n).trimmed.trimEnd(". ")
    if reservedNames.contains(n.before(".").uppercased()) { n += "_" }
    return n.take(150).trimEnd(". ")
}

/// One line of a code list: '2-558<TAB>Realme C85<TAB>26/9' (tools/whatsapp_fetch/helpers.py).
struct CodeItem: Hashable {
    var code: String
    var name: String = ""
    var date: String = ""

    func baseName(includeDate: Bool = false) -> String {
        var parts = [code, name]
        if includeDate && !date.isEmpty { parts.append(date.replacingOccurrences(of: "/", with: "-")) }
        return safeFileName(parts.filter { !$0.isEmpty }.joined(separator: " ")).ifEmpty(safeFileName(code)).ifEmpty("item")
    }
}

enum Codes {
    static let imageExts = [".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"]
    private static let dateRx = Rx(#"(?:^|\s)(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*$"#)

    /// Lines like '2-558<TAB>Realme C85<TAB>26/9' (tabs or spaces), in the shop's code style.
    static func parseCodeList(_ text: String) -> [CodeItem] {
        let r = ProductCode.reader()
        var items: [CodeItem] = []
        for raw in text.lines() {
            let line = raw.trimmed
            if line.isEmpty { continue }
            let (code, listed) = r.splitListLine(line)
            var rest = listed
            if !code.hasDigit { continue }
            rest = squeezeSpaces(rest.replacingOccurrences(of: "\t", with: " ")).trimmed
            var date = ""
            if let m = dateRx.find(rest) {
                date = m.group(1)
                rest = (rest as NSString).substring(to: m.start).trimmed
            }
            items.append(CodeItem(code: code, name: rest, date: date))
        }
        return items
    }

    /// Regex (valid in both ICU and JavaScript) matching a message about the code (ProductCode.codePattern).
    static func codePattern(_ code: String) -> String { ProductCode.codePattern(code) }

    private static let listDateRx = Rx(#"\s*(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\s*"#)

    /// '26/9' -> 26 Sep of this year (last year if that is in the future).
    static func listDate(_ text: String, now: Date = Date()) -> Date? {
        guard let m = listDateRx.matchEntire(text) else { return nil }
        let cal = Calendar.current
        let day = Int(m.group(1)) ?? 0
        let month = Int(m.group(2)) ?? 0
        let yearText = m.group(3)
        var year = Int(yearText) ?? cal.component(.year, from: now)
        if year < 100 { year += 2000 }
        let comps = DateComponents(year: year, month: month, day: day)
        guard cal.date(from: comps) != nil, comps.isValidDate(in: cal), var d = cal.date(from: comps) else { return nil }
        if yearText.isEmpty, let limit = cal.date(byAdding: .day, value: 1, to: now), d > limit {
            d = cal.date(byAdding: .year, value: -1, to: d) ?? d
        }
        return d
    }

    /// Oldest message time to read: lookbackDays ago, extended to a week before the oldest list date.
    static func scanSince(_ items: [CodeItem], lookbackDays: Int, now: Date = Date()) -> Date {
        let cal = Calendar.current
        var since = cal.date(byAdding: .day, value: -lookbackDays, to: now) ?? now
        for it in items {
            guard let d = listDate(it.date, now: now), let w = cal.date(byAdding: .day, value: -7, to: d) else { continue }
            if w < since { since = w }
        }
        return since
    }

    static func extFor(_ mimetype: String?, _ data: Data, filename: String? = nil) -> String {
        let mt = (mimetype ?? "").before(";").trimmed.lowercased()
        let byMime = ["image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                      "image/gif": ".gif", "image/heic": ".heic", "image/heif": ".heic", "image/bmp": ".bmp"]
        if let e = byMime[mt] { return e }
        let b = [UInt8](data.prefix(12))
        func starts(_ sig: [UInt8]) -> Bool { b.count >= sig.count && Array(b[0..<sig.count]) == sig }
        if starts([0xff, 0xd8, 0xff]) { return ".jpg" }
        if starts([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]) { return ".png" }
        if b.count >= 12 && String(bytes: b[0..<4], encoding: .ascii) == "RIFF" && String(bytes: b[8..<12], encoding: .ascii) == "WEBP" { return ".webp" }
        if b.count >= 4 && String(bytes: b[0..<4], encoding: .ascii) == "GIF8" { return ".gif" }
        if let f = filename, f.contains(".") {
            let suffix = "." + f.afterLast(".").lowercased()
            if imageExts.contains(suffix) { return suffix }
        }
        return ".jpg"
    }

    /// The file saved for this item by an earlier run, if any (names = files in the output folder).
    static func existingOutput(_ names: Set<String>, _ base: String) -> String? {
        for n in [base, "\(base) 1"] {
            for ext in imageExts where names.contains(n + ext) { return n + ext }
        }
        return nil
    }
}
