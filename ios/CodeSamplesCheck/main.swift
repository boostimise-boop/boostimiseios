// Checks Core/ProductCode.swift against core/code_samples.json: the sample messages and the codes each code
// style must find in them, the same file the Windows app, the Android app and the website are checked
// against. The iOS workflow builds it with the reader's files and runs it before building the app:
//   swiftc -o check-codes Boostimise/Sources/Core/{Regex,Codes,JSON,ProductCode}.swift CodeSamplesCheck/*.swift
//   ./check-codes ../core/code_samples.json
import Foundation

let path = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "core/code_samples.json"
guard let data = FileManager.default.contents(atPath: path),
      let samples = (try? JSONSerialization.jsonObject(with: data)) as? JSON else {
    print("Cannot read \(path)")
    exit(2)
}

/// JSON text with sorted keys, so two values can be compared.
func canon(_ v: Any?) -> String {
    let d = try? JSONSerialization.data(withJSONObject: v ?? NSNull(), options: [.sortedKeys, .fragmentsAllowed])
    return d.flatMap { String(data: $0, encoding: .utf8) } ?? "?"
}

func list(_ key: String) -> [JSON] { samples[key] as? [JSON] ?? [] }

let styles = samples["styles"] as? JSON ?? [:]
var readers: [String: ProductCode.Reader] = [:]
for (name, s) in styles { readers[name] = ProductCode.Reader(ProductCode.cleanStyle(s)) }
var bad: [String] = []

func expect(_ what: String, _ got: Any?, _ want: Any?) {
    if canon(got) != canon(want) { bad.append("\(what): got \(canon(got)), want \(canon(want))") }
}

for s in list("messages") {
    expect("[\(s.str("style"))] codes in \(s.str("text"))", readers[s.str("style")]!.codesInText(s.str("text")), s["codes"])
}
for s in list("order_codes") {
    expect("[\(s.str("style"))] order codes of \(s.str("text"))", readers[s.str("style")]!.orderCodes(s.str("text")), s["codes"])
}
for s in list("list_lines") {
    let (code, rest) = readers[s.str("style")]!.splitListLine(s.str("line"))
    expect("[\(s.str("style"))] list line \(s.str("line"))", [code, rest], [s.str("code"), s.str("rest")])
}
for s in list("normalize") {
    expect("[\(s.str("style"))] key of \(s.str("code"))", readers[s.str("style")]!.normalize(s.str("code")), s["key"])
}
for s in list("code_parts") {
    let parts = readers[s.str("style")]!.codeParts(s.str("code"))
    let got: JSON = parts.map { ["prefix": $0.0, "number": $0.1] } ?? [:]
    expect("[\(s.str("style"))] parts of \(s.str("code"))", got, s["parts"])
}
for s in list("patterns") {
    let code = s.str("code")
    let rx = Rx(readers[s.str("style")]!.codePattern(code), ignoreCase: true)
    for t in s["match"] as? [String] ?? [] where !rx.contains(t) { bad.append("[\(s.str("style"))] the pattern of \(code) does not match \(t)") }
    for t in s["no_match"] as? [String] ?? [] where rx.contains(t) { bad.append("[\(s.str("style"))] the pattern of \(code) matches \(t)") }
}
for s in list("any_codes") {
    var keys: [String] = []
    for m in Rx(readers[s.str("style")]!.jsAnyCode, ignoreCase: true).findAll(s.str("text")) {
        let k = ProductCode.norm(m.group(1), m.group(2))
        if !keys.contains(k) { keys.append(k) }
    }
    expect("[\(s.str("style"))] any codes in \(s.str("text"))", keys, s["codes"])
}
for s in list("sort") {
    let r = readers[s.str("style")]!
    let want = s["codes"] as? [String] ?? []
    expect("[\(s.str("style"))] sorted", want.reversed().sorted(by: r.before), want)
}
for s in list("examples") {
    expect("[\(s.str("style"))] example", ProductCode.example(ProductCode.cleanStyle(styles[s.str("style")])), s["example"])
}
for s in list("guess") {
    let (style, problem) = ProductCode.guessStyle(s.str("examples"))
    expect("guess \(s.str("examples"))", style?.json, s["style"])
    if style == nil && (problem ?? "").isEmpty { bad.append("guess \(s.str("examples")): no style and no reason") }
}
for s in list("clean_style") {
    expect("clean \(canon(s["in"]))", ProductCode.cleanStyle(s["in"]).json, s["out"])
}

if bad.isEmpty {
    print("ProductCode.swift reads every sample right.")
} else {
    print("ProductCode.swift reads these samples wrong:")
    for b in bad { print("  " + b) }
    exit(1)
}
