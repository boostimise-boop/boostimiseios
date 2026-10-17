import Foundation

/// JSON as Foundation gives it ([String: Any], [Any], NSNumber, NSNull), with org.json-style readers
/// so the code reads like the Android app's.
typealias JSON = [String: Any]

enum JSONTools {
    static func parse(_ text: String?) -> Any? {
        guard let t = text, !t.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              let d = t.data(using: .utf8) else { return nil }
        return try? JSONSerialization.jsonObject(with: d, options: [.fragmentsAllowed])
    }

    static func parse(data: Data) -> Any? {
        try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
    }

    /// JSON text of a value ("null" for nil).
    static func string(_ value: Any?) -> String {
        guard let v = value else { return "null" }
        if let d = try? JSONSerialization.data(withJSONObject: v, options: [.fragmentsAllowed]),
           let s = String(data: d, encoding: .utf8) { return s }
        return "null"
    }

    /// A JavaScript / JSON string literal.
    static func quote(_ s: String) -> String { string(s) }
}

/// {"a": 1, "b": null} from pairs; nil becomes JSON null.
func jsonOf(_ pairs: (String, Any?)...) -> JSON {
    var o = JSON()
    for (k, v) in pairs { o[k] = v ?? NSNull() }
    return o
}

extension Dictionary where Key == String, Value == Any {
    private func value(_ k: String) -> Any? {
        guard let v = self[k], !(v is NSNull) else { return nil }
        return v
    }

    /// The text of a field; "" when it is missing or null.
    func str(_ k: String, _ def: String = "") -> String {
        guard let v = value(k) else { return def }
        if let s = v as? String { return s }
        if let n = v as? NSNumber { return n.stringValue }
        return JSONTools.string(v)
    }

    func strOrNil(_ k: String) -> String? { value(k) == nil ? nil : str(k) }

    func intOrNil(_ k: String) -> Int? {
        guard let v = value(k) else { return nil }
        if let n = v as? NSNumber { return n.intValue }
        if let s = v as? String { return Int(s) ?? Double(s).map { Int($0) } }
        return nil
    }

    func int(_ k: String, _ def: Int = 0) -> Int { intOrNil(k) ?? def }

    func doubleOrNil(_ k: String) -> Double? {
        guard let v = value(k) else { return nil }
        if let n = v as? NSNumber { return n.doubleValue }
        if let s = v as? String { return Double(s) }
        return nil
    }

    func double(_ k: String, _ def: Double = 0) -> Double { doubleOrNil(k) ?? def }

    func bool(_ k: String, _ def: Bool = false) -> Bool {
        guard let v = value(k) else { return def }
        if let n = v as? NSNumber { return n.boolValue }
        if let s = v as? String { return s.lowercased() == "true" }
        return def
    }

    func obj(_ k: String) -> JSON? { self[k] as? JSON }
    func arr(_ k: String) -> [Any]? { self[k] as? [Any] }
    func objs(_ k: String) -> [JSON] { (self[k] as? [Any])?.compactMap { $0 as? JSON } ?? [] }
    func strs(_ k: String) -> [String] {
        (self[k] as? [Any])?.map { ($0 as? String) ?? (($0 as? NSNumber)?.stringValue ?? "") } ?? []
    }
    func has(_ k: String) -> Bool { self[k] != nil }
    func isNull(_ k: String) -> Bool { self[k] == nil || self[k] is NSNull }
}

func objects(_ value: Any?) -> [JSON] { (value as? [Any])?.compactMap { $0 as? JSON } ?? [] }
