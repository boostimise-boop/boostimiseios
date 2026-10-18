import Foundation

/// Product codes in chat text and the codes list / result file (tools/model_detect/codes.py).
enum ProductCodes {
    // 5a-403, 5A-403, 5a - 403, 5a_403, 5a:403 ... and the codes typed in a chat: Core/ProductCode.swift
    static let codeRx = ProductCode.codeRx
    static let notDone: Set<String> = ["", "not found", "no image", "unknown"]

    static func norm(_ prefix: String, _ number: String) -> String { ProductCode.norm(prefix, number) }
    static func split(_ code: String) -> (String, Int) { ProductCode.split(code) }

    struct M {
        let start: Int
        let end: Int
    }

    /// The codes written in a chat message ("redmi 12 8+128" and "at 10:30" are not codes).
    static func codeMatches(_ text: String?) -> [M] {
        ProductCode.codeMatches(text).map { M(start: $0.start, end: $0.end) }
    }

    static func codesInText(_ text: String?) -> [String] { ProductCode.codesInText(text) }

    /// The text with the codes in it replaced by spaces.
    static func stripCodes(_ text: String?) -> String {
        var t = text ?? ""
        for m in codeMatches(t).reversed() {
            t = (t as NSString).replacingCharacters(in: NSRange(location: m.start, length: m.end - m.start), with: " ")
        }
        return t
    }

    /// A model counts as finished unless it is empty, a status word, or only a guess ("redmi ?").
    static func isDone(_ model: String?) -> Bool {
        let m = (model ?? "").trimmed.lowercased()
        return !notDone.contains(m) && !m.hasSuffix("?")
    }

    /// The codes list, kept line by line so the result has the same layout.
    final class CodeList {
        var lines: [(String, String?)] = []          // raw line, code
        var blockOrder: [String] = []                // prefixes in order
        var blocks: [String: [String]] = [:]         // prefix -> codes in order
        var label: [String: String] = [:]            // code -> spelling in the list

        init(_ text: String) {
            for raw in text.lines() {
                let s = raw.trimmed
                guard !s.isEmpty, let m = codeRx.find(s), m.start == 0 else {
                    lines.append((raw.trimEnd(" \t\r"), nil))
                    continue
                }
                let code = norm(m.group(1), m.group(2))
                lines.append((raw.trimEnd(" \t\r"), code))
                if label[code] == nil {
                    label[code] = (s as NSString).substring(to: m.end)
                    let p = m.group(1).lowercased()
                    if blocks[p] == nil { blockOrder.append(p) }
                    blocks[p, default: []].append(code)
                }
            }
        }

        var codes: [String] { blockOrder.flatMap { blocks[$0] ?? [] } }

        func prefixRange(_ prefix: String) -> (Int, Int) {
            let nums = (blocks[prefix] ?? []).map { split($0).1 }
            return (nums.min() ?? 0, nums.max() ?? 0)
        }

        /// The models already written in the list ("5a-403 samsung a70").
        func existing() -> [String: String] {
            var out: [String: String] = [:]
            for (raw, code) in lines {
                guard let code else { continue }
                let s = raw.trimmed
                guard let m = codeRx.find(s) else { continue }
                out[code] = (s as NSString).substring(from: m.end).trimmed
            }
            return out
        }

        /// "<code> <model>" for every code, keeping the list's order and blank lines.
        func write(_ models: [String: String]) -> String {
            lines.map { raw, code in
                guard let code else { return raw }
                return "\(label[code] ?? code) \(models[code] ?? "")".trimEnd()
            }.joined(separator: "\n") + "\n"
        }
    }
}

/// Model numbers -> the name the shop uses (tools/model_detect/model_codes.py): "SM-A065F" ->
/// "samsung a06", "V2111" -> "vivo y21". Samsung numbers follow a pattern; other brands use the
/// phone list phone_models.tsv.
enum ModelNumbers {
    private static let samsung: [String: String] = [
        "A013": "a01 core", "A022": "a02", "A025": "a02s", "A032": "a03 core", "A042": "a04e",
        "A102": "a10e", "A127": "a12", "A137": "a13", "A202": "a20e", "A260": "a2 core",
        "A320": "a3 2017", "A510": "a5 2016", "A520": "a5 2017", "A530": "a8 2018", "A600": "a6",
        "A605": "a6 plus", "A710": "a7 2016", "A720": "a7 2017", "A730": "a8 plus 2018", "A750": "a7 2018",
        "A908": "a90 5g", "A910": "a9 pro", "A920": "a9 2018",
        "M013": "m01 core", "M017": "m01s", "M022": "m02", "M025": "m02s", "M127": "m12", "F127": "f12",
        "J100": "j1", "J105": "j1 mini", "J106": "j1 mini prime", "J110": "j1 ace", "J120": "j1 2016",
        "J200": "j2", "J210": "j2 2016", "J250": "j2 pro", "J260": "j2 core", "J320": "j3 2016",
        "J330": "j3 pro", "J337": "j3 2018", "J400": "j4", "J410": "j4 core", "J415": "j4 plus",
        "J500": "j5", "J510": "j5 2016", "J530": "j5 pro", "J600": "j6", "J610": "j6 plus", "J700": "j7",
        "J701": "j7 core", "J710": "j7 2016", "J720": "j7 duo", "J730": "j7 pro", "J737": "j7 2018",
        "J810": "j8",
        "G355": "core 2", "G360": "core prime", "G361": "core prime", "G530": "grand prime",
        "G531": "grand prime", "G532": "j2 prime", "G550": "on5", "G570": "j5 prime", "G600": "on7",
        "G610": "j7 prime", "G611": "j7 prime 2", "G615": "j7 max", "G720": "grand max",
        "G770": "s10 lite", "G780": "s20 fe", "G781": "s20 fe 5g", "G900": "s5", "G920": "s6",
        "G925": "s6 edge", "G928": "s6 edge plus", "G930": "s7", "G935": "s7 edge", "G950": "s8",
        "G955": "s8 plus", "G960": "s9", "G965": "s9 plus", "G970": "s10e", "G973": "s10",
        "G975": "s10 plus", "G977": "s10 5g", "G980": "s20", "G981": "s20 5g", "G985": "s20 plus",
        "G986": "s20 plus 5g", "G988": "s20 ultra", "G990": "s21 fe", "G991": "s21", "G996": "s21 plus",
        "G998": "s21 ultra",
        "N900": "note 3", "N910": "note 4", "N920": "note 5", "N930": "note 7", "N935": "note fe",
        "N950": "note 8", "N960": "note 9", "N970": "note 10", "N971": "note 10 5g", "N975": "note 10 plus",
        "N976": "note 10 plus 5g", "N770": "note 10 lite", "N980": "note 20", "N981": "note 20 5g",
        "N985": "note 20 ultra", "N986": "note 20 ultra 5g",
        "F700": "z flip", "F711": "z flip 3", "F721": "z flip 4", "F731": "z flip 5", "F741": "z flip 6",
        "F916": "z fold 2", "F926": "z fold 3", "F936": "z fold 4", "F946": "z fold 5", "F956": "z fold 6",
    ]
    private static let samsungRx = Rx(#"(?<![A-Z0-9])SM[-_ ]?([AMFJGNS])([0-9O]{3})[0-9]?[A-Z]{0,3}(?![A-Z0-9])"#)
    private static let samsungKind: [Character: String] = ["5": "", "6": " 5g", "7": "s", "8": "s 5g"]
    private static let sVariant: [Character: String] = ["1": "", "6": " plus", "7": " edge", "8": " ultra"]

    static func samsungName(_ letter: String, _ digits0: String) -> String? {
        let digits = digits0.replacingOccurrences(of: "O", with: "0")
        if let n = samsung[letter + digits] { return "samsung \(n)" }
        let d = Array(digits)
        guard d.count == 3 else { return nil }
        if "AMF".contains(letter), let kind = samsungKind[d[2]] {
            return "samsung \(letter.lowercased())\(String(d[0...1]))\(kind)"
        }
        if letter == "S", d[0] == "9", let v = sVariant[d[2]], let n = d[1].wholeNumberValue {
            return "samsung s\(22 + n)\(v)"
        }
        if letter == "S", d[0] == "7", d[2] == "1", let n = d[1].wholeNumberValue {
            return "samsung s\(22 + n) fe"
        }
        return nil
    }

    private static let table: [String: (String, String)] = {
        var t: [String: (String, String)] = [:]
        t.reserveCapacity(12000)
        guard let url = Bundle.main.url(forResource: "phone_models", withExtension: "tsv"),
              let text = try? String(contentsOf: url, encoding: .utf8) else { return t }
        for line in text.split(whereSeparator: { $0.isNewline }) {
            let p = line.split(separator: "\t", omittingEmptySubsequences: false)
            if p.count == 3 { t[String(p[0])] = (String(p[1]), String(p[2])) }
        }
        return t
    }()

    private static let notKeyChars = Rx(#"[^A-Z0-9]"#)
    private static func key(_ text: String) -> String { notKeyChars.replace(text.uppercased(), "") }

    private static func variants(_ k: String) -> [String] {
        var out = [k]
        if k.contains("O") { out.append(k.replacingOccurrences(of: "O", with: "0")) }
        if k.contains("I") { out.append(k.replacingOccurrences(of: "I", with: "1")) }
        return out
    }

    /// The shop name for one model number, or nil. Short numbers only count with their brand in `text`.
    static func lookup(_ token: String, _ text: String = "") -> String? {
        let k = key(token)
        if k.count < 3 || table.isEmpty { return nil }
        let low = text.lowercased()
        for v in variants(k) {
            guard let hit = table[v] else { continue }
            if v.count >= 5 || low.contains(hit.0) { return hit.1 }
        }
        return nil
    }

    private static let tokenRx = Rx(#"[A-Za-z0-9][A-Za-z0-9_-]{2,16}"#)

    /// Shop names of the model numbers written in a text, best first.
    static func namesFromCodes(_ text0: String) -> [String] {
        let text = ProductCodes.stripCodes(text0)
        let up = text.uppercased()
        var out: [String] = []
        for m in samsungRx.findAll(up) {
            if let n = lookup(m.value, text) ?? samsungName(m.group(1), m.group(2)) { out.append(n) }
        }
        for m in tokenRx.findAll(text) {
            let tok = m.value
            if tok.hasDigit && (tok.contains(where: { $0.isLetter && $0.isASCII }) || key(tok).count == 4) {
                if let n = lookup(tok, text) { out.append(n) }
            }
        }
        var seen = Set<String>()
        return out.filter { seen.insert($0).inserted }
    }

    private static let codeShape: [String: Rx] = [
        "vivo": Rx(#"v\d{4}[a-z]?|\d{4}"#), "oppo": Rx(#"cph\d{4}"#), "realme": Rx(#"rmx\d{4}"#),
        "infinix": Rx(#"x\d{3,4}[a-z]?"#), "tecno": Rx(#"[a-z]{2}\d[a-z]?"#), "itel": Rx(#"[a-z]\d{3,4}[a-z]?|it\d{4}"#),
        "xiaomi": Rx(#"\d{4,}[a-z0-9]*|m\d{4}\w+"#), "redmi": Rx(#"\d{4,}[a-z0-9]*|m\d{4}\w+"#),
        "poco": Rx(#"\d{4,}[a-z0-9]*|m\d{4}\w+"#),
    ]

    /// (name, known): true = a model number in the phone list, false = one that is not, nil = a plain name.
    static func resolve(_ name: String, _ text: String = "") -> (String, Bool?) {
        let words = name.components(separatedBy: " ")
        if words.count != 2 { return (name, nil) }
        if let hit = lookup(words[1], "\(text) \(words[0])") { return (hit, true) }
        if let shape = codeShape[words[0]], shape.matches(words[1]) { return (name, false) }
        return (name, nil)
    }
}
