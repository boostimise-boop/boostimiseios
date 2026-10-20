import SwiftUI

/// The shop's code style on the Shop screen (core/code_style_widgets.py): pick one of the three styles, or
/// type a few of your codes and let the app pick; paste a message to see the codes it finds. Saved in the
/// shop settings ("code_style", and "orders_chat" for codes without a prefix).
struct CodeStyleSection: View {
    private static let styles = [
        ("prefix", "Prefix and number, like 2-558"),
        ("letters", "Letters and number, like CC1023 or AB-12"),
        ("keyword", "# or a word and number, like #1023"),
    ]
    private static let seps = [("", "CC1023"), ("-", "CC-1023"), (" ", "CC 1023")]

    @State private var kind = "prefix"
    @State private var letters = ""
    @State private var sep = ""
    @State private var keywords = ""
    @State private var ordersChat = ""
    @State private var examples = ""
    @State private var guessNote = ""
    @State private var guessBad = false
    @State private var sample = ""
    @State private var error = ""
    @State private var dirty = false

    var body: some View {
        let style = typed()
        Card(title: "Code style", hint: "How your product codes are written in WhatsApp.") {
            SelectField(label: "Style", options: Self.styles, value: edit($kind))
            if kind == "letters" {
                HStack {
                    TextField("Your letters, e.g. CC, AB", text: edit($letters)).textFieldStyle(.roundedBorder)
                        .autocorrectionDisabled().textInputAutocapitalization(.characters)
                    SelectField(label: "Written", options: Self.seps, value: edit($sep)).frame(width: 130)
                }
                Hint("Each letters' codes are in the WhatsApp chat set for those letters below (e.g. CC), like a list of prefix codes.")
            }
            if kind == "keyword" {
                TextField("Your # or word, e.g. #, Order", text: edit($keywords)).textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                TextField("Orders chat (WhatsApp chat name)", text: edit($ordersChat)).textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled()
                Hint("These codes have no prefix, so all of them are looked for in this one chat.")
            }
            Divider()
            Text("Not sure? Type two or three of your codes").font(.system(size: 14, weight: .medium))
            HStack {
                TextField("e.g. CC1023, CC1050", text: $examples).textFieldStyle(.roundedBorder)
                    .autocorrectionDisabled().textInputAutocapitalization(.never)
                SecondaryButton("Pick") { pick() }
            }
            if !guessNote.isEmpty { Hint(guessNote, guessBad ? .red : Brand.text2) }
            TextField("Try it: paste a message from WhatsApp", text: $sample, axis: .vertical).lineLimit(2...6)
                .textFieldStyle(.roundedBorder).autocorrectionDisabled()
            Hint(found(style))
            if !error.isEmpty { Hint(error, .red) }
            HStack {
                PrimaryButton("Save code style", enabled: dirty) { save(style) }
                SecondaryButton("Undo", enabled: dirty) { load() }
            }
        }
        .onAppear { load() }
        // shows what another app saved, unless something is being changed here
        .onChange(of: Shop.shared.version) { if !dirty { load() } }
    }

    /// A binding that marks the section as changed when it is used.
    private func edit<T>(_ b: Binding<T>) -> Binding<T> {
        Binding(get: { b.wrappedValue }, set: { b.wrappedValue = $0; dirty = true })
    }

    private func words(_ text: String) -> [String] {
        text.replacingOccurrences(of: ",", with: " ").split(whereSeparator: { $0.isWhitespace }).map(String.init)
    }

    private func show(_ s: ProductCode.Style) {
        kind = s.kind
        if s.kind == "letters" {
            letters = s.letters.joined(separator: ", ")
            sep = s.sep
        }
        if s.kind == "keyword" { keywords = s.keywords.joined(separator: ", ") }
    }

    private func load() {
        show(ProductCode.cleanStyle(Shop.shared.get("code_style")))
        ordersChat = Shop.shared.get("orders_chat") as? String ?? ""
        dirty = false
        error = ""
    }

    /// The style as shown (the prefix style while it is not complete).
    private func typed() -> ProductCode.Style {
        switch kind {
        case "letters": return ProductCode.cleanStyle(ProductCode.Style(kind: "letters", letters: words(letters), sep: sep).json)
        case "keyword": return ProductCode.cleanStyle(ProductCode.Style(kind: "keyword", keywords: words(keywords)).json)
        default: return ProductCode.defaultStyle
        }
    }

    private func found(_ style: ProductCode.Style) -> String {
        if sample.trimmed.isEmpty { return "Codes will look like \(ProductCode.example(style))." }
        let r = ProductCode.Reader(style)
        let codes = r.codesInText(sample).map { key -> String in
            let (p, n) = ProductCode.split(key)
            return r.written(p, String(n))
        }
        return codes.isEmpty ? "No codes found. A code in this style looks like \(ProductCode.example(style))."
            : "Codes found: \(codes.joined(separator: ", "))"
    }

    private func pick() {
        let (style, problem) = ProductCode.guessStyle(examples)
        if let style {
            show(style)
            dirty = true
            guessNote = "\(ProductCode.describe(style)). Save it if this is right."
            guessBad = false
        } else {
            guessNote = problem ?? ""
            guessBad = true
        }
    }

    private func isLetters(_ w: String) -> Bool { !w.isEmpty && w.allSatisfy { $0.isASCII && $0.isLetter } }

    private func problems() -> [String] {
        var out: [String] = []
        if kind == "letters" {
            if let bad = words(letters).first(where: { $0.count > 4 || !isLetters($0) }) {
                out.append("Letters are 1 to 4 letters A-Z, not “\(bad)”.")
            } else if words(letters).isEmpty {
                out.append("Type your letters, e.g. CC.")
            }
        }
        if kind == "keyword" {
            if let bad = words(keywords).first(where: { $0 != "#" && !($0.count <= 12 && isLetters($0)) }) {
                out.append("Type # or one word of the letters A-Z, not “\(bad)”.")
            } else if words(keywords).isEmpty {
                out.append("Type # or the word before your numbers, e.g. Order.")
            }
            if ordersChat.trimmed.isEmpty { out.append("Type the name of the WhatsApp chat your orders are in.") }
        }
        return out
    }

    private func save(_ style: ProductCode.Style) {
        let p = problems()
        if !p.isEmpty {
            error = p.joined(separator: "\n")
            return
        }
        let chat = ordersChat.trimmed
        let codeStyle: Any? = style == ProductCode.defaultStyle ? nil : style.json
        let orders: Any? = chat.isEmpty ? nil : chat
        Shop.shared.save(other: ["code_style": codeStyle, "orders_chat": orders])
        load()
        toast("Code style saved: \(ProductCode.describe(style))")
    }
}
