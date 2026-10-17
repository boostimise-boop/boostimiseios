import Foundation

/// A regular expression with the behaviour the Android app's (Java) patterns rely on: ICU syntax
/// (lookbehind, named groups, \p{L}, Unicode \w \d), UTF-16 offsets, find-from-index that still
/// sees the text before it, and whole-string matching.
final class Rx {
    let pattern: String
    let regex: NSRegularExpression
    private let options: NSRegularExpression.Options
    private lazy var whole: NSRegularExpression = try! NSRegularExpression(pattern: "(?:" + pattern + ")\\z", options: options)

    init(_ pattern: String, ignoreCase: Bool = false) {
        self.pattern = pattern
        var o: NSRegularExpression.Options = []
        if ignoreCase { o.insert(.caseInsensitive) }
        options = o
        regex = try! NSRegularExpression(pattern: pattern, options: o)
    }

    struct Match {
        let result: NSTextCheckingResult
        let ns: NSString

        var range: NSRange { result.range }
        var start: Int { result.range.location }
        var end: Int { result.range.location + result.range.length }
        var value: String { ns.substring(with: result.range) }

        func range(_ i: Int) -> NSRange {
            i < result.numberOfRanges ? result.range(at: i) : NSRange(location: NSNotFound, length: 0)
        }

        func groupOrNil(_ i: Int) -> String? {
            let r = range(i)
            return r.location == NSNotFound ? nil : ns.substring(with: r)
        }

        /// The group's text ("" when it did not take part).
        func group(_ i: Int) -> String { groupOrNil(i) ?? "" }

        func namedRange(_ name: String) -> NSRange { result.range(withName: name) }

        func named(_ name: String) -> String {
            let r = result.range(withName: name)
            return r.location == NSNotFound ? "" : ns.substring(with: r)
        }
    }

    /// The first match at or after `from` (UTF-16 offset); lookbehinds still see the text before it.
    func find(_ s: String, from: Int = 0) -> Match? {
        let ns = s as NSString
        guard from <= ns.length else { return nil }
        let opts: NSRegularExpression.MatchingOptions = from > 0 ? [.withTransparentBounds, .withoutAnchoringBounds] : []
        guard let r = regex.firstMatch(in: s, options: opts, range: NSRange(location: from, length: ns.length - from)) else { return nil }
        return Match(result: r, ns: ns)
    }

    func findAll(_ s: String) -> [Match] {
        let ns = s as NSString
        return regex.matches(in: s, range: NSRange(location: 0, length: ns.length)).map { Match(result: $0, ns: ns) }
    }

    func contains(_ s: String) -> Bool { find(s) != nil }

    /// Java's matches(): the whole text matches.
    func matchEntire(_ s: String) -> Match? {
        let ns = s as NSString
        guard let r = whole.firstMatch(in: s, options: [.anchored], range: NSRange(location: 0, length: ns.length)) else { return nil }
        return Match(result: r, ns: ns)
    }

    func matches(_ s: String) -> Bool { matchEntire(s) != nil }

    /// Replaces every match; `template` uses $1-style references (like Java's replaceAll).
    func replace(_ s: String, _ template: String) -> String {
        let ns = s as NSString
        return regex.stringByReplacingMatches(in: s, range: NSRange(location: 0, length: ns.length), withTemplate: template)
    }

    /// Replaces every match with what `transform` returns (taken literally).
    func replace(_ s: String, _ transform: (Match) -> String) -> String {
        let ns = s as NSString
        let out = NSMutableString(string: s)
        for m in findAll(s).reversed() {
            out.replaceCharacters(in: m.range, with: transform(Match(result: m.result, ns: ns)))
        }
        return out as String
    }

    func replaceFirst(_ s: String, _ replacement: String) -> String {
        guard let m = find(s) else { return s }
        return (s as NSString).replacingCharacters(in: m.range, with: replacement)
    }

    /// Kotlin's split: the pieces between the matches.
    func split(_ s: String) -> [String] {
        let ns = s as NSString
        var out: [String] = []
        var last = 0
        for m in findAll(s) {
            if m.range.length == 0 && m.start == last && m.start != 0 { continue }
            out.append(ns.substring(with: NSRange(location: last, length: m.start - last)))
            last = m.end
        }
        out.append(ns.substring(from: last))
        return out
    }

    static func escape(_ s: String) -> String { NSRegularExpression.escapedPattern(for: s) }
}

extension String {
    /// Kotlin's lines(): splits on \r\n, \n and \r.
    func lines() -> [String] {
        split(omittingEmptySubsequences: false, whereSeparator: { $0.isNewline }).map(String.init)
    }

    /// Kotlin's trim { it in chars }.
    func trimming(_ chars: String) -> String {
        let set = Set(chars)
        var s = Substring(self)
        while let f = s.first, set.contains(f) { s = s.dropFirst() }
        while let l = s.last, set.contains(l) { s = s.dropLast() }
        return String(s)
    }

    func trimEnd(_ chars: String = " \t") -> String {
        let set = Set(chars)
        var s = Substring(self)
        while let l = s.last, set.contains(l) { s = s.dropLast() }
        return String(s)
    }

    var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }

    /// Kotlin's substringBefore / substringAfter(Last).
    func before(_ sep: String) -> String {
        guard let r = range(of: sep) else { return self }
        return String(self[..<r.lowerBound])
    }

    func after(_ sep: String) -> String {
        guard let r = range(of: sep) else { return self }
        return String(self[r.upperBound...])
    }

    func beforeLast(_ sep: String) -> String {
        guard let r = range(of: sep, options: .backwards) else { return self }
        return String(self[..<r.lowerBound])
    }

    func afterLast(_ sep: String, _ missing: String? = nil) -> String {
        guard let r = range(of: sep, options: .backwards) else { return missing ?? self }
        return String(self[r.upperBound...])
    }

    func replacingFirst(_ target: String, with replacement: String) -> String {
        guard !target.isEmpty, let r = range(of: target) else { return self }
        return replacingCharacters(in: r, with: replacement)
    }

    func take(_ n: Int) -> String { String(prefix(max(0, n))) }

    /// "" -> nil-safe first character test helpers
    var hasDigit: Bool { contains(where: { $0.isNumber }) }
    var isAllDigits: Bool { !isEmpty && allSatisfy { $0.isNumber } }
}

/// Collapses runs of whitespace into one space.
func squeezeSpaces(_ s: String) -> String { spacesRx.replace(s, " ") }

let spacesRx = Rx(#"\s+"#)
