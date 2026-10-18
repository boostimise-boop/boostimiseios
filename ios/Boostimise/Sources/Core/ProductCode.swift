import Foundation

/// Product codes as people type them (core/codes.py). All of these mean 2-152:
///   2-152  2:152  2_152  2+152  2;152  2&152  2:152+  2:152-  2+152-
/// and several codes of one list may be written together:
///   2-151+152  2-151-152  2-151+2-152  2:152+2-153  2+152-2+153  2-151 & 2-152
/// A code joined by + & : or ; must start its line ("redmi 12 8+128" is memory, "at 10:30" a time).
/// \uXXXX: codePattern also goes to JavaScript (scanChat), which has no \x{...}.
enum ProductCode {
    static let prefix = #"\d{1,3}[A-Za-z]{0,2}"#
    static let dash = #"[-‐-―_]"#   // joins the two halves of a code anywhere
    static let loose = #"[+&:;]"#             // joins them only in a code that starts a line
    static let sep = "(?:" + dash + "|" + loose + ")"
    private static let end = #"(?![0-9A-Za-z])"#
    // A number after a code that adds one more code of the same list. A lone digit followed by a joiner
    // starts a code of another list instead ("3-163+4-165" is 3-163 and 4-165).
    private static let moreNum = #"(?!\d[ \t]*"# + sep + #"[ \t]*\d)\d{1,6}"# + end

    /// What comes between two codes of a list whose prefix matches the regex `prefix`.
    private static func join(_ prefix: String) -> String {
        let head = prefix + #"\s*"# + sep + #"\s*"#
        return #"(?:[ \t]*[+&][ \t]*(?:"# + head + #")?|[ \t]*"# + dash + #"[ \t]*"# + head +
            "|" + dash + "(?:" + head + ")?)"
    }

    /// One code alone, as in a code list or a result file: group 1 prefix, group 2 number.
    static let code = #"(?<![0-9A-Za-z])("# + prefix + #")\s*"# + sep + #"\s*(\d{1,6})"# + end
    static let codeRx = Rx(code)
    /// A code in a chat with the codes that follow it: 1 prefix, 2 joiner, 3 number, 4 the rest of the list.
    static let chatCode = #"(?<![0-9A-Za-z])("# + prefix + #")\s*("# + sep + #")\s*(\d{1,6})"# + end +
        "((?:" + join(#"\1"#) + moreNum + ")*)"
    static let chatCodeRx = Rx(chatCode, ignoreCase: true)
    private static let listCodeRx = Rx(#"\s*("# + prefix + #")\s*"# + sep + #"\s*(\d{1,6})[\s+\-]*"#)
    private static let alnum = Rx("[0-9A-Za-z]")
    private static let lock = NSLock()
    private static var moreRxs: [String: Rx] = [:]

    static func norm(_ prefix: String, _ number: String) -> String { "\(prefix.lowercased())-\(Int(number) ?? 0)" }
    static func split(_ code: String) -> (String, Int) { (code.beforeLast("-"), Int(code.afterLast("-")) ?? 0) }

    /// The numbers in group 4 of chatCode: "+2-153-154" -> [153, 154].
    static func moreNumbers(_ prefix: String, _ more: String) -> [String] {
        lock.lock()
        let rx = moreRxs[prefix.lowercased()] ?? Rx(join(Rx.escape(prefix)) + "(" + moreNum + ")", ignoreCase: true)
        moreRxs[prefix.lowercased()] = rx
        lock.unlock()
        return rx.findAll(more).map { $0.group(1) }
    }

    /// The codes of one chatCode match: "2-151-152" -> [2-151, 2-152].
    static func matchCodes(_ m: Rx.Match) -> [String] {
        var out: [String] = []
        for n in [m.group(3)] + moreNumbers(m.group(1), m.group(4)) {
            let c = norm(m.group(1), n)
            if !out.contains(c) { out.append(c) }
        }
        return out
    }

    /// The codes written in a chat message, as chatCodeRx matches.
    static func codeMatches(_ text: String?) -> [Rx.Match] {
        let t = text ?? ""
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

    static func codesInText(_ text: String?) -> [String] {
        var out: [String] = []
        for m in codeMatches(bnToAscii(text ?? "")) {
            for c in matchCodes(m) where !out.contains(c) { out.append(c) }
        }
        return out
    }

    /// A code typed in a code list, written the usual way: "2:152+" -> "2-152", "5A_380" -> "5A-380".
    static func listCode(_ token: String) -> String {
        guard let m = listCodeRx.matchEntire(bnToAscii(token)) else { return token.trimmed }
        return m.group(1) + "-" + m.group(2)
    }

    private static let jsSpecial = Rx(#"([.*+?^${}()|\[\]\\/])"#)
    private static func jsEscape(_ s: String) -> String { jsSpecial.replace(s) { "\\" + $0.value } }

    /// Regex (valid in both ICU and JavaScript) matching a message that is about `code`:
    /// '2-152' matches '2 - 152', '2:152+', '2_152', '2-151-152', '2+151-2+152', '2-151+152', not '12-152'.
    static func codePattern(_ code0: String) -> String {
        let code = listCode(code0)
        let s = #"\s*"# + sep + #"\s*"#
        let parts = Rx(s).split(code).filter { !$0.isEmpty }.map { jsEscape($0) }
        guard parts.count == 2, parts[1].isAllDigits, let n = Int(parts[1]) else {
            return #"(?<![0-9A-Za-z])(?:"# + parts.joined(separator: s) + ")" + end
        }
        let p = parts[0]
        var last = "0*\(n)"
        let one = p + s + last
        if n < 10 { last += #"(?![ \t]*"# + sep + #"[ \t]*\d)"# }   // "3-163+4-165" is not about 3-4
        let inList = p + s + #"\d{1,6}"# + end + "(?:" + join(p) + moreNum + ")*?" + join(p) + last
        return #"(?<![0-9A-Za-z])(?:"# + one + "|" + inList + ")" + end
    }
}
