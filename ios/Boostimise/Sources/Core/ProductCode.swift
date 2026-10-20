import Foundation

/// Product codes as people type them (core/codes.py). A shop writes its codes in one of three styles, its
/// shop setting "code_style" (see cleanStyle):
/// - prefix (the default), as in 2-558. All of these mean 2-152:
///     2-152  2:152  2_152  2+152  2;152  2&152  2:152+  2:152-  2+152-
///   and several codes of one list may be written together:
///     2-151+152  2-151-152  2-151+2-152  2:152+2-153  2+152-2+153  2-151 & 2-152
///   A code joined by + & : or ; must start its line ("redmi 12 8+128" is memory, "at 10:30" a time).
/// - letters: the shop's letters and a number, as in CC1023 or AB-12 (also cc 1023, CC-1023, CC1023+1024).
/// - keyword: # or a word and a number, as in #1023 or Order 1023 (also Order: #1023, #1023+1024). These
///   codes have no prefix: their key is the number alone.
/// reader() reads codes in the shop's style; setStyle(_:) changes it (Shop.swift calls it). The iOS workflow
/// checks this file against core/code_samples.json (ios/CodeSamplesCheck), like the other apps' readers.
/// The patterns also go to JavaScript (scanChat), which has no \x{...}: characters are written as they are.
enum ProductCode {
    // MARK: - the prefix style (2-558)
    static let prefix = #"\d{1,3}[A-Za-z]{0,2}"#
    static let dash = #"[-‐-―_]"#   // joins the two halves of a code anywhere
    static let loose = #"[+&:;]"#             // joins them only in a code that starts a line
    static let sep = "(?:" + dash + "|" + loose + ")"
    private static let end = #"(?![0-9A-Za-z])"#
    private static let start = #"(?<![0-9A-Za-z])"#
    // A number after a code that adds one more code of the same list. A lone digit followed by a joiner
    // starts a code of another list instead ("3-163+4-165" is 3-163 and 4-165).
    private static let moreNum = #"(?!\d[ \t]*"# + sep + #"[ \t]*\d)\d{1,6}"# + end

    /// What comes between two codes of a list whose prefix matches the regex `prefix`.
    private static func join(_ prefix: String) -> String {
        let head = prefix + #"\s*"# + sep + #"\s*"#
        return #"(?:[ \t]*[+&][ \t]*(?:"# + head + #")?|[ \t]*"# + dash + #"[ \t]*"# + head +
            "|" + dash + "(?:" + head + ")?)"
    }

    // The prefix style's patterns. Tools read codes with reader(), which has these for the shop's style.
    /// One code alone, as in a code list or a result file: group 1 prefix, group 2 number.
    static let code = start + "(" + prefix + #")\s*"# + sep + #"\s*(\d{1,6})"# + end
    static let codeRx = Rx(code)
    /// A code in a chat with the codes that follow it: 1 prefix, 2 joiner, 3 number, 4 the rest of the list.
    static let chatCode = start + "(" + prefix + #")\s*("# + sep + #")\s*(\d{1,6})"# + end +
        "((?:" + join(#"\1"#) + moreNum + ")*)"
    static let chatCodeRx = Rx(chatCode, ignoreCase: true)

    // MARK: - the letters (CC1023) and keyword (#1023) styles
    private static let lettersGap = #"[ \t]*"# + dash + #"?[ \t]*"#          // CC1023, CC 1023, CC-1023
    private static let keywordGap = #"[ \t]*(?:[-‐-―_:.#][ \t]*){0,2}"#   // #1023, Order: #1023
    private static let number = #"\d{1,6}"# + end

    /// What comes between two codes of a list in these styles: "+1024", " & CC1024", "+#1024".
    private static func listJoin(_ head: String) -> String { #"[ \t]*[+&][ \t]*(?:"# + head + ")?" }

    // The words an order message may put before its code: "Order: 2-558", "কোড 2-558".
    private static let orderStart = #"^\W*(?:(?:order|code|id|অর্ডার|কোড)\W*)?"#
    private static let alnum = Rx("[0-9A-Za-z]")
    private static let markup = Rx(#"[*~`]|(?<!\d)_|_(?!\d)"#)

    private static let jsSpecial = Rx(#"([.*+?^${}()|\[\]\\/])"#)
    private static func jsEscape(_ s: String) -> String { jsSpecial.replace(s) { "\\" + $0.value } }

    private static func cleanLine(_ line: String) -> String {
        var l = bnToAscii(line).replacingOccurrences(of: "\u{00a0}", with: " ")
        l = markup.replace(l, "")   // WhatsApp *bold* / _italic_ markers, not "2_152"
        return squeezeSpaces(l).trimmed
    }

    private static func isAsciiLetters(_ s: String) -> Bool { !s.isEmpty && s.allSatisfy { $0.isASCII && $0.isLetter } }

    // MARK: - the style setting

    /// One code style: kind "prefix", "letters" (with letters and sep) or "keyword" (with keywords).
    struct Style: Equatable {
        var kind: String
        var letters: [String] = []
        var sep: String = ""
        var keywords: [String] = []

        var json: JSON {
            switch kind {
            case "letters": return ["kind": kind, "letters": letters, "sep": sep]
            case "keyword": return ["kind": kind, "keywords": keywords]
            default: return ["kind": "prefix"]
            }
        }
    }

    static let defaultStyle = Style(kind: "prefix")
    private static let maxLetters = 12
    private static let maxKeywords = 6

    /// A valid style from the shop setting; the prefix style for anything else.
    /// {"kind": "letters", "letters": ["CC", "AB"], "sep": ""}: sep is how a code is written ("", "-" or " ").
    /// {"kind": "keyword", "keywords": ["#", "Order"]}: # or words of A-Z; the first one is how a code is written.
    static func cleanStyle(_ value: Any?) -> Style {
        guard let o = value as? JSON else { return defaultStyle }
        switch o["kind"] as? String ?? "" {
        case "letters":
            var letters: [String] = []
            for item in o["letters"] as? [Any] ?? [] {
                let l = "\(item)".trimmed.uppercased()
                if (1...4).contains(l.count) && isAsciiLetters(l) && !letters.contains(l) { letters.append(l) }
            }
            if !letters.isEmpty {
                let s = o["sep"] as? String ?? ""
                return Style(kind: "letters", letters: Array(letters.prefix(maxLetters)), sep: ["", "-", " "].contains(s) ? s : "")
            }
        case "keyword":
            var words: [String] = []
            for item in o["keywords"] as? [Any] ?? [] {
                let w = "\(item)".trimmed
                if (w == "#" || (w.count <= 12 && isAsciiLetters(w))) && !words.contains(where: { $0.lowercased() == w.lowercased() }) {
                    words.append(w)
                }
            }
            if !words.isEmpty { return Style(kind: "keyword", keywords: Array(words.prefix(maxKeywords))) }
        default: break
        }
        return defaultStyle
    }

    /// How a code of the style looks: "2-558", "CC1023", "#1023".
    static func example(_ style: Style) -> String {
        let r = Reader(style)
        return r.kind == "prefix" ? r.written("2", "558") : r.written(r.prefixes().first ?? "", "1023")
    }

    /// "Prefix and number, like 2-558", "Letters and number (CC, AB), like CC1023", "# and number, like #1023".
    static func describe(_ style: Style) -> String {
        switch style.kind {
        case "letters": return "Letters and number (\(style.letters.joined(separator: ", "))), like \(example(style))"
        case "keyword": return "\(style.keywords.joined(separator: " or ")) and number, like \(example(style))"
        default: return "Prefix and number, like \(example(style))"
        }
    }

    private static let guessPrefix = Rx("(" + prefix + #")\s*"# + sep + #"\s*(\d{1,6})"#)
    private static let guess = Rx(#"(#|[A-Za-z]{1,12})("# + keywordGap + #")(\d{1,6})"#)
    private static let dashRx = Rx(dash)
    private static let kindNames = ["prefix": "prefix and number", "letters": "letters and number",
                                    "keyword": "# or a word and number"]

    /// The style of a few codes typed one per line or with commas: (style, nil), or (nil, what is wrong).
    /// "2-558, 3-12" is the prefix style, "CC1023, AB-12" letters, "#1023" or "Order 1023" keyword.
    static func guessStyle(_ text: String?) -> (Style?, String?) {
        let examples = bnToAscii(text ?? "").split(whereSeparator: { $0 == "\n" || $0 == "," })
            .map { String($0).trimmed }.filter { !$0.isEmpty }
        if examples.isEmpty { return (nil, "Type two or three of your codes.") }
        var kinds: [(String, String)] = []
        func note(_ kind: String, _ ex: String) { if !kinds.contains(where: { $0.0 == kind }) { kinds.append((kind, ex)) } }
        var letters: [String] = []
        var seps: [String] = []
        var keywords: [String] = []
        for ex in examples {
            if guessPrefix.matches(ex) {
                note("prefix", ex)
                continue
            }
            guard let m = guess.matchEntire(ex) else {
                return (nil, "“\(ex)” is not a code this app can read: a code is letters, a prefix, # or a word, then a number.")
            }
            let word = m.group(1)
            let gap = m.group(2)
            let mark = gap.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "\t", with: "")
            let upper = word.contains(where: { $0.isLetter }) && word == word.uppercased()
            if word != "#" && word.count <= 4 && (mark.isEmpty || dashRx.matches(mark)) && (!gap.contains(" ") || upper) {
                note("letters", ex)
                if !letters.contains(word.uppercased()) { letters.append(word.uppercased()) }
                seps.append(!mark.isEmpty ? "-" : !gap.isEmpty ? " " : "")
            } else {
                note("keyword", ex)
                if !keywords.contains(where: { $0.lowercased() == word.lowercased() }) { keywords.append(word) }
            }
        }
        if kinds.count > 1 {
            let shown = kinds.map { "\($0.1) is \(kindNames[$0.0] ?? $0.0)" }.joined(separator: ", ")
            return (nil, "These codes are written in different styles (\(shown)). Type codes of one style.")
        }
        switch kinds[0].0 {
        case "letters":
            var sep = ""
            for s in ["", "-", " "] where seps.filter({ $0 == s }).count > seps.filter({ $0 == sep }).count { sep = s }
            return (cleanStyle(Style(kind: "letters", letters: letters, sep: sep).json), nil)
        case "keyword":
            return (cleanStyle(Style(kind: "keyword", keywords: keywords).json), nil)
        default:
            return (defaultStyle, nil)
        }
    }

    // MARK: - the reader

    /// Reads the codes of one style (core/codes.py's Reader). code: one code alone, as in a code list (group 1
    /// prefix, 2 number). chatCode: a code in a chat with the codes after it (1 prefix, 2 joiner, 3 number, 4
    /// the rest). jsAnyCode / jsCodeParts: what the WhatsApp script tells lists from single codes with, and
    /// counts per prefix (scanPatterns). A keyword-style code has no prefix: its group 1 is always empty.
    final class Reader {
        let style: Style
        let kind: String
        let code: String
        let chatCode: String
        let jsAnyCode: String
        let jsCodeParts: String
        let codeRx: Rx
        let chatCodeRx: Rx
        private let head: String
        private let listRx: Rx
        private let lineRx: Rx
        private let orderRx: Rx
        private let lock = NSLock()
        private var more: [String: Rx] = [:]

        init(_ style: Style = defaultStyle) {
            self.style = style
            kind = style.kind
            let core: String
            switch kind {
            case "letters":
                let letters = "(?:" + style.letters.sorted { $0.count > $1.count }.joined(separator: "|") + ")"
                core = "(" + letters + ")" + lettersGap + #"(\d{1,6})"#
                head = ""
                chatCode = start + "(" + letters + ")(" + lettersGap + #")(\d{1,6})"# + end +
                    "((?:" + listJoin(#"\1"# + lettersGap) + number + ")*)"
                jsAnyCode = start + core + end
                jsCodeParts = jsAnyCode
                code = start + core + end
            case "keyword":
                let words = "(?:" + style.keywords.sorted { $0.count > $1.count }.map { jsEscape($0) }.joined(separator: "|") + ")"
                head = words + keywordGap
                core = "(?:" + head + ")?()" + #"(\d{1,6})"#   // in a code list the keyword may be left out
                chatCode = start + words + "()(" + keywordGap + #")(\d{1,6})"# + end +
                    "((?:" + listJoin(head) + number + ")*)"
                jsAnyCode = start + words + "()" + keywordGap + #"(\d{1,6})"# + end
                jsCodeParts = jsAnyCode
                code = start + core + end
            default:
                core = "(" + prefix + #")\s*"# + sep + #"\s*(\d{1,6})"#
                head = ""
                code = ProductCode.code
                chatCode = ProductCode.chatCode
                jsAnyCode = start + #"(\d{1,3}[A-Za-z]?)\s*-\s*(\d{2,4})"# + end
                jsCodeParts = start + "(" + prefix + #")\s*"# + dash + #"\s*(\d{1,6})"# + end
            }
            codeRx = Rx(code, ignoreCase: true)
            chatCodeRx = Rx(chatCode, ignoreCase: true)
            listRx = Rx(#"\s*"# + core + #"[\s+\-]*"#, ignoreCase: true)
            lineRx = Rx(#"^\s*"# + core + #"[+\-]*(?=\s|$)"#, ignoreCase: true)
            orderRx = Rx(orderStart + chatCode, ignoreCase: true)
        }

        // MARK: one code

        /// The letters of the letters style; [] for the other styles.
        func prefixes() -> [String] { kind == "letters" ? style.letters : [] }

        /// How a code is written in lists and file names: "2-558", "CC1023", "AB-12", "#1023", "Order 1023".
        func written(_ prefix: String, _ number: String) -> String {
            switch kind {
            case "letters":
                return (style.letters.first(where: { $0.lowercased() == prefix.lowercased() }) ?? prefix.uppercased()) + style.sep + number
            case "keyword":
                let w = style.keywords[0]
                return w + (w == "#" ? "" : " ") + number
            default:
                return prefix + "-" + number
            }
        }

        /// A code typed in a code list, written the usual way: "2:152+" -> "2-152", "cc 1023" -> "CC1023".
        func listCode(_ token: String?) -> String {
            guard let m = listRx.matchEntire(bnToAscii(token ?? "")) else { return (token ?? "").trimmed }
            return written(m.group(1), m.group(2))
        }

        /// The key of a code: "2 - 558", "2:558+" -> "2-558", "CC1023" -> "cc-1023", "#1023" -> "1023".
        func normalize(_ code: String?) -> String {
            guard let m = listRx.matchEntire(bnToAscii(code ?? "")) else { return (code ?? "").trimmed.lowercased() }
            return norm(m.group(1), m.group(2))
        }

        /// (prefix in lower case, number) of "4a-205", ("", 1023) of "#1023"; nil for anything else.
        func codeParts(_ code: String?) -> (String, Int)? {
            guard let m = listRx.matchEntire(bnToAscii(code ?? "")), let n = Int(m.group(2)) else { return nil }
            return (m.group(1).lowercased(), n)
        }

        /// 2-558 < 2-560 < 3-12 < 5a-380; cc-12 < cc-1023; 12 < 1023.
        func sortKey(_ code: String?) -> (Int, String, Int) {
            guard let m = listRx.matchEntire(bnToAscii(code ?? "")) else { return (1_000_000, code ?? "", 0) }
            let p = m.group(1)
            let digits = String(p.prefix(while: { $0.isASCII && $0.isNumber }))
            return (Int(digits) ?? 0, String(p.dropFirst(digits.count)).lowercased(), Int(m.group(2)) ?? 0)
        }

        func before(_ a: String, _ b: String) -> Bool { sortKey(a) < sortKey(b) }

        /// A code-list line -> (code, the rest): "2-558<TAB>Realme C85" -> ("2-558", "Realme C85"). A line that
        /// does not start with a code gives its first word as the code, as written.
        func splitListLine(_ line0: String?) -> (String, String) {
            let line = (line0 ?? "").trimmed
            if let m = lineRx.find(bnToAscii(line)) {
                return (written(m.group(1), m.group(2)), (line as NSString).substring(from: m.end).trimmed)
            }
            let first = line.replacingFirst("\t", with: " ")
            guard first.contains(" ") else { return (listCode(first), "") }
            return (listCode(first.before(" ")), first.after(" ").trimmed)
        }

        // MARK: codes in a message

        /// The numbers in group 4 of chatCode: "+2-153-154" -> [153, 154].
        func moreNumbers(_ prefix: String, _ text: String) -> [String] {
            if text.isEmpty { return [] }
            lock.lock()
            let key = prefix.lowercased()
            let rx = more[key] ?? Rx(kind == "prefix" ? join(jsEscape(prefix)) + "(" + moreNum + ")"
                : listJoin(kind == "letters" ? jsEscape(prefix) + lettersGap : head) + "(" + number + ")", ignoreCase: true)
            more[key] = rx
            lock.unlock()
            return rx.findAll(text).map { $0.group(1) }
        }

        /// The codes of one chatCode match: "2-151-152" -> [2-151, 2-152].
        func matchCodes(_ m: Rx.Match) -> [String] {
            var out: [String] = []
            for n in [m.group(3)] + moreNumbers(m.group(1), m.group(4)) {
                let c = norm(m.group(1), n)
                if !out.contains(c) { out.append(c) }
            }
            return out
        }

        /// The chatCode matches in a message (a code joined by + & : or ; must start its line).
        func codeMatches(_ text: String?) -> [Rx.Match] {
            let t = text ?? ""
            if kind != "prefix" { return chatCodeRx.findAll(t) }
            let ns = t as NSString
            var out: [Rx.Match] = []
            var pos = 0
            while pos <= ns.length, let m = chatCodeRx.find(t, from: pos) {
                let nl = ns.range(of: "\n", options: .backwards, range: NSRange(location: 0, length: m.start))
                let lineStart = nl.location == NSNotFound ? 0 : nl.location + 1
                let line = ns.substring(with: NSRange(location: lineStart, length: m.start - lineStart))
                if ["+", "&", ":", ";"].contains(m.group(2)) && alnum.contains(line) {
                    let p = m.range(1)
                    pos = p.location + p.length   // "redmi 12 8+128", "at 10:30": not a code
                    continue
                }
                out.append(m)
                pos = m.end
            }
            return out
        }

        /// The keys of the codes written in a message: "2-151+152 and 3-12" -> [2-151, 2-152, 3-12].
        func codesInText(_ text: String?) -> [String] {
            var out: [String] = []
            for m in codeMatches(bnToAscii(text ?? "")) {
                for c in matchCodes(m) where !out.contains(c) { out.append(c) }
            }
            return out
        }

        /// The codes an order message starts with (one of its first three lines): ["6-872"], ["3-163", "3-164"].
        func orderCodes(_ text: String?) -> [String] {
            for line in (text ?? "").lines().map({ cleanLine($0) }).filter({ !$0.isEmpty }).prefix(3) {
                if let m = orderLine(line) { return matchCodes(m) }
            }
            return []
        }

        /// The chatCode match a cleaned order-message line starts with ("Order: 3-163+164"), or nil.
        func orderLine(_ line: String) -> Rx.Match? { orderRx.find(line) }

        /// Regex (valid in both ICU and JavaScript) matching a message that is about `code0`:
        /// '2-152' matches '2 - 152', '2:152+', '2_152', '2-151-152', '2+151-2+152', '2-151+152', not '12-152';
        /// 'CC1023' matches 'cc 1023' and 'CC1022+1023'.
        func codePattern(_ code0: String?) -> String {
            if kind == "prefix" { return prefixCodePattern(code0 ?? "") }
            guard let m = listRx.matchEntire(bnToAscii(code0 ?? "")), let n = Int(m.group(2)) else {
                return start + "(?:" + jsEscape((code0 ?? "").trimmed) + ")" + end
            }
            let h = kind == "letters" ? jsEscape(m.group(1)) + lettersGap : head
            let last = "0*\(n)"
            let j = listJoin(h)
            let inList = h + #"\d{1,6}"# + end + "(?:" + j + number + ")*?" + j + last
            return start + "(?:" + h + last + "|" + inList + ")" + end
        }
    }

    // MARK: - helpers that do not depend on the style

    /// The key of a code: ("2", "0558") -> "2-558", ("5A", "380") -> "5a-380", ("", "1023") -> "1023".
    static func norm(_ prefix: String, _ number: String) -> String {
        (prefix.isEmpty ? "" : prefix.lowercased() + "-") + String(Int(number) ?? 0)
    }

    /// A key back to its parts: "2-558" -> (2, 558), "cc-1023" -> (cc, 1023), "1023" -> ("", 1023).
    static func split(_ code: String) -> (String, Int) {
        (code.contains("-") ? code.beforeLast("-") : "", Int(code.afterLast("-")) ?? 0)
    }

    private static let prefixListCode = Rx(#"\s*("# + prefix + #")\s*"# + sep + #"\s*(\d{1,6})[\s+\-]*"#)

    private static func prefixCodePattern(_ code0: String) -> String {
        var code = code0.trimmed
        if let m = prefixListCode.matchEntire(bnToAscii(code0)) { code = m.group(1) + "-" + m.group(2) }
        let s = #"\s*"# + sep + #"\s*"#
        let parts = Rx(s).split(code).filter { !$0.isEmpty }.map { jsEscape($0) }
        guard parts.count == 2, parts[1].isAllDigits, let n = Int(parts[1]) else {
            return start + "(?:" + parts.joined(separator: s) + ")" + end
        }
        let p = parts[0]
        var last = "0*\(n)"
        let one = p + s + last
        if n < 10 { last += #"(?![ \t]*"# + sep + #"[ \t]*\d)"# }   // "3-163+4-165" is not about 3-4
        let inList = p + s + #"\d{1,6}"# + end + "(?:" + join(p) + moreNum + ")*?" + join(p) + last
        return start + "(?:" + one + "|" + inList + ")" + end
    }

    // MARK: - the shop's style

    private static let currentLock = NSLock()
    private static var current = Reader(defaultStyle)

    /// The reader of the shop's code style. A job that runs for a while keeps the one it started with.
    static func reader() -> Reader {
        currentLock.lock()
        defer { currentLock.unlock() }
        return current
    }

    /// Reads codes in this style from now on (the shop setting "code_style"; nil is the prefix style).
    static func setStyle(_ value: JSON?) {
        let s = cleanStyle(value)
        currentLock.lock()
        if s != current.style { current = Reader(s) }
        currentLock.unlock()
    }

    /// What wa_bridge.js's scanChat reads codes with in the shop's style (opts.anyCode, opts.codeParts).
    static func scanPatterns(_ opts: JSON) -> JSON {
        let r = reader()
        var o = opts
        o["anyCode"] = r.jsAnyCode
        o["codeParts"] = r.jsCodeParts
        return o
    }

    // The shop's style, for the tools that read codes without holding a reader.
    static func codeMatches(_ text: String?) -> [Rx.Match] { reader().codeMatches(text) }
    static func codesInText(_ text: String?) -> [String] { reader().codesInText(text) }
    static func matchCodes(_ m: Rx.Match) -> [String] { reader().matchCodes(m) }
    static func orderCodes(_ text: String?) -> [String] { reader().orderCodes(text) }
    static func listCode(_ token: String) -> String { reader().listCode(token) }
    static func normalize(_ code: String?) -> String { reader().normalize(code) }
    static func codePattern(_ code: String) -> String { reader().codePattern(code) }
}
