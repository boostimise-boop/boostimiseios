import SwiftUI
import UniformTypeIdentifiers
import WebKit

/// A scrolling page of cards.
struct Page<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) { content }
                .padding(.horizontal, 16)
                .padding(.vertical, 12)
                .frame(maxWidth: 720, alignment: .leading)
                .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
    }
}

/// A white card with an optional title and hint (the Android app's Section).
struct Card<Content: View>: View {
    var title: String? = nil
    var hint: String? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title { Text(title).font(.system(size: 16, weight: .semibold)).foregroundStyle(Brand.navy) }
            if let hint { Hint(hint) }
            content
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.surface, in: RoundedRectangle(cornerRadius: 14))
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Brand.border, lineWidth: 1))
    }
}

struct Hint: View {
    let text: String
    var color: Color = Brand.text2

    init(_ text: String, _ color: Color = Brand.text2) {
        self.text = text
        self.color = color
    }

    var body: some View {
        Text(text).font(.system(size: 13)).foregroundStyle(color).fixedSize(horizontal: false, vertical: true)
    }
}

struct CheckRow: View {
    let label: String
    @Binding var isOn: Bool

    init(_ label: String, _ isOn: Binding<Bool>) {
        self.label = label
        _isOn = isOn
    }

    var body: some View {
        Toggle(isOn: $isOn) { Text(label).font(.system(size: 14)) }.tint(Brand.accent)
    }
}

/// A whole number with a label and unit ("Look back 10 days").
struct NumberField: View {
    let label: String
    @Binding var value: Int
    let range: ClosedRange<Int>
    var suffix = ""
    @State private var text = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 12)).foregroundStyle(Brand.text2)
            HStack {
                TextField("", text: $text)
                    .keyboardType(.numberPad)
                    .onChange(of: text) { _, t in
                        let digits = String(t.filter { $0.isASCII && $0.isNumber }.prefix(4))
                        if digits != t { text = digits }
                        if let v = Int(digits) { value = min(max(v, range.lowerBound), range.upperBound) }
                    }
                if !suffix.isEmpty { Text(suffix).foregroundStyle(Brand.muted) }
            }
            .padding(10)
            .background(RoundedRectangle(cornerRadius: 8).stroke(Brand.border))
        }
        .onAppear { text = String(value) }
        .onChange(of: value) { _, v in if Int(text) != v { text = String(v) } }
    }
}

/// Field-like box that opens a menu or a sheet.
struct FieldBox: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 12)).foregroundStyle(Brand.text2)
            HStack {
                Text(value).font(.system(size: 15)).foregroundStyle(Brand.navy).multilineTextAlignment(.leading)
                Spacer(minLength: 4)
                Image(systemName: "chevron.down").font(.system(size: 12)).foregroundStyle(Brand.muted)
            }
            .padding(10)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border))
        }
    }
}

/// A drop-down choice.
struct SelectField<T: Hashable>: View {
    let label: String
    let options: [(T, String)]
    @Binding var value: T

    var body: some View {
        Menu {
            ForEach(Array(options.enumerated()), id: \.offset) { _, o in
                Button { value = o.0 } label: {
                    if o.0 == value { Label(o.1, systemImage: "checkmark") } else { Text(o.1) }
                }
            }
        } label: {
            FieldBox(label: label, value: options.first { $0.0 == value }?.1 ?? "")
        }
        .buttonStyle(.plain)
    }
}

/// A choice chip (the selected one filled).
struct Chip: View {
    let label: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(label).font(.system(size: 14, weight: .medium))
                .padding(.horizontal, 14).padding(.vertical, 8)
                .foregroundStyle(selected ? Color.white : Brand.accent)
                .background(selected ? Brand.accent : Color.white, in: Capsule())
                .overlay(Capsule().stroke(selected ? Brand.accent : Brand.border))
        }
        .buttonStyle(.plain)
    }
}

/// Lays children out in rows, wrapping like text; the children of a row share its middle line
/// (a borderless link beside a bordered button).
struct FlowLayout: Layout {
    var spacing: CGFloat = 6

    /// The children and their sizes, row by row, for a width.
    private func lines(_ subviews: Subviews, width maxW: CGFloat) -> [[(view: LayoutSubview, size: CGSize)]] {
        var lines: [[(view: LayoutSubview, size: CGSize)]] = [[]]
        var x: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > 0 && x + size.width > maxW {
                lines.append([])
                x = 0
            }
            lines[lines.count - 1].append((view: s, size: size))
            x += size.width + spacing
        }
        return lines
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = self.lines(subviews, width: proposal.width ?? .infinity)
        var width: CGFloat = 0, height: CGFloat = 0
        for (i, row) in rows.enumerated() {
            width = max(width, row.reduce(0) { $0 + $1.size.width } + spacing * CGFloat(max(0, row.count - 1)))
            height += (row.map { $0.size.height }.max() ?? 0) + (i > 0 ? spacing : 0)
        }
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in self.lines(subviews, width: bounds.width) {
            let rowH = row.map { $0.size.height }.max() ?? 0
            var x = bounds.minX
            for (view, size) in row {
                view.place(at: CGPoint(x: x, y: y + (rowH - size.height) / 2), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += rowH + spacing
        }
    }
}

/// Wrapping row of chips / badges.
struct Flow<Content: View>: View {
    var spacing: CGFloat = 6
    @ViewBuilder var content: Content

    var body: some View {
        FlowLayout(spacing: spacing).callAsFunction { content }
    }
}

struct PrimaryButton: View {
    let title: String
    var full = false
    var enabled = true
    let action: () -> Void

    init(_ title: String, full: Bool = false, enabled: Bool = true, action: @escaping () -> Void) {
        self.title = title
        self.full = full
        self.enabled = enabled
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 15, weight: .semibold)).frame(maxWidth: full ? .infinity : nil)
        }
        .buttonStyle(.borderedProminent)
        .tint(Brand.accent)
        .disabled(!enabled)
    }
}

struct SecondaryButton: View {
    let title: String
    var full = false
    var enabled = true
    let action: () -> Void

    init(_ title: String, full: Bool = false, enabled: Bool = true, action: @escaping () -> Void) {
        self.title = title
        self.full = full
        self.enabled = enabled
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            Text(title).font(.system(size: 15, weight: .medium)).frame(maxWidth: full ? .infinity : nil)
        }
        .buttonStyle(.bordered)
        .tint(Brand.accent)
        .disabled(!enabled)
    }
}

struct LinkButton: View {
    let title: String
    var color: Color = Brand.accent
    let action: () -> Void

    init(_ title: String, color: Color = Brand.accent, action: @escaping () -> Void) {
        self.title = title
        self.color = color
        self.action = action
    }

    var body: some View {
        Button(title, action: action).font(.system(size: 15, weight: .medium)).foregroundStyle(color).buttonStyle(.borderless)
    }
}

/// The pasted code list, or a .txt file opened from the Files app.
struct CodeListBox: View {
    @Binding var text: String
    var placeholder = "2-558\tRealme C85\t26/9\n2-559\tSamsung A70"
    @State private var importing = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Code list (one per line)").font(.system(size: 12)).foregroundStyle(Brand.text2)
            ZStack(alignment: .topLeading) {
                TextEditor(text: $text)
                    .font(.system(size: 14, design: .monospaced))
                    .autocorrectionDisabled()
                    .textInputAutocapitalization(.never)
                    .frame(minHeight: 120, maxHeight: 260)
                    .scrollContentBackground(.hidden)
                if text.isEmpty {
                    Text(placeholder).font(.system(size: 14, design: .monospaced)).foregroundStyle(Brand.muted)
                        .padding(.horizontal, 5).padding(.vertical, 8).allowsHitTesting(false)
                }
            }
            .padding(6)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border))
            HStack(spacing: 8) {
                SecondaryButton("Open .txt") { importing = true }
                SecondaryButton("Paste") { if let p = pasteText() { text = p } }
                if !text.isEmpty { LinkButton("Clear") { text = "" } }
            }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.plainText, .text, .commaSeparatedText]) { result in
            if case .success(let url) = result {
                do { text = try Storage.readText(url) } catch { toast("Could not read the file") }
            }
        }
    }
}

/// Pick a chat (or, with `filters`, all chats / all groups / a name to search for).
struct ChatPickerSheet: View {
    let filters: Bool
    let onPick: (ChatFilter) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var q = ""

    var body: some View {
        let wa = WaEngine.shared
        let chats = Array(wa.chats.filter { q.trimmed.isEmpty || $0.title.localizedCaseInsensitiveContains(q) }.prefix(300))
        NavigationStack {
            List {
                if wa.chats.isEmpty {
                    Hint("Open WhatsApp first (the WhatsApp page in the menu) to load your chats.")
                }
                if filters {
                    Section {
                        row("All chats") { pick(ChatFilter()) }
                        row("All groups") { pick(ChatFilter(groupsOnly: true)) }
                        if !q.trimmed.isEmpty { row("Chats whose name contains \"\(q)\"") { pick(ChatFilter(title: q)) } }
                    }
                }
                Section {
                    ForEach(chats) { c in
                        row(c.title, tag: c.isGroup ? "group" : nil) { pick(ChatFilter(id: c.id, title: c.title)) }
                    }
                }
            }
            .searchable(text: $q, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search chats")
            .navigationTitle("Choose a chat")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }

    private func pick(_ f: ChatFilter) {
        onPick(f)
        dismiss()
    }

    private func row(_ title: String, tag: String? = nil, _ action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack {
                Text(title).foregroundStyle(Brand.navy)
                Spacer()
                if let tag { Text(tag).font(.system(size: 12)).foregroundStyle(Brand.muted) }
            }
        }
    }
}

struct ChatFilterPicker: View {
    @Binding var filter: ChatFilter
    @State private var open = false

    var body: some View {
        Button { open = true } label: { FieldBox(label: "Search in", value: filter.label) }
            .buttonStyle(.plain)
            .sheet(isPresented: $open) { ChatPickerSheet(filters: true) { filter = $0 } }
    }
}

struct SingleChatPicker: View {
    let label: String
    let chat: WaChat?
    let onPick: (WaChat) -> Void
    @State private var open = false

    var body: some View {
        Button { open = true } label: { FieldBox(label: label, value: chat?.title ?? "Choose…") }
            .buttonStyle(.plain)
            .sheet(isPresented: $open) {
                ChatPickerSheet(filters: false) { f in
                    if let c = WaEngine.shared.chats.first(where: { $0.id == f.id }) { onPick(c) }
                }
            }
    }
}

/// The chat of one list of codes ("6- list"); `fallback` is shown when it has none.
struct ListChatPicker: View {
    let label: String
    let chat: ChatFilter?
    let fallback: String
    let onPick: (ChatFilter) -> Void
    @State private var open = false

    var body: some View {
        Button { open = true } label: { FieldBox(label: label, value: chat?.title ?? fallback) }
            .buttonStyle(.plain)
            .sheet(isPresented: $open) { ChatPickerSheet(filters: false, onPick: onPick) }
    }
}

/// The WhatsApp state, and a way to the WhatsApp page when it is not ready.
struct WaBanner: View {
    var body: some View {
        let wa = WaEngine.shared
        let color: Color = {
            switch wa.phase {
            case .ready: return Brand.success
            case .qr: return Brand.warning
            case .error: return Brand.error
            default: return Brand.accent
            }
        }()
        HStack(spacing: 8) {
            Circle().fill(color).frame(width: 10, height: 10)
            Text(wa.phase == .off ? "WhatsApp is not open"
                 : "WhatsApp: \(wa.message)" + (wa.holder.isEmpty ? "" : " · \(wa.holder) is using it"))
                .font(.system(size: 13)).foregroundStyle(Brand.navy)
                .frame(maxWidth: .infinity, alignment: .leading)
            if wa.phase != .ready {
                LinkButton("Open") {
                    wa.open()
                    Nav.shared.go(.whatsapp)
                }
            }
        }
        .padding(10)
        .background(Brand.accentSoft, in: RoundedRectangle(cornerRadius: 10))
    }
}

struct RunBar: View {
    let running: Bool
    let progress: Double
    let activity: String
    let summary: String
    var startLabel = "Start"
    let onStart: () -> Void
    let onStop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if running { SecondaryButton("Stop", action: onStop) } else { PrimaryButton(startLabel, action: onStart) }
            if running { ProgressView(value: min(max(progress, 0), 1)).tint(Brand.accent) }
            if !activity.isEmpty { Hint(activity) }
            if !summary.isEmpty { Text(summary).font(.system(size: 14, weight: .medium)).foregroundStyle(Brand.navy) }
        }
    }
}

struct LogBox: View {
    let log: ToolLog
    @State private var open = false

    var body: some View {
        Card {
            Button { open.toggle() } label: {
                HStack {
                    Text("Log (\(log.lines.count))").font(.system(size: 15, weight: .semibold)).foregroundStyle(Brand.navy)
                    Spacer()
                    Text(open ? "Hide" : "Show").foregroundStyle(Brand.accent)
                }
            }
            .buttonStyle(.plain)
            if open {
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(log.lines.suffix(200).enumerated()), id: \.offset) { _, l in
                            Text(l).font(.system(size: 11, design: .monospaced)).foregroundStyle(Brand.navy)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                    }
                }
                .frame(maxHeight: 320)
                HStack {
                    LinkButton("Copy log") { copyText(log.lines.joined(separator: "\n")) }
                    LinkButton("Clear") { log.clear() }
                }
            }
        }
    }
}

struct StatusDot: View {
    let kind: String

    var body: some View {
        let c: Color = {
            switch kind {
            case "ok": return Brand.success
            case "working": return Brand.accent
            case "none": return Brand.warning
            case "error": return Brand.error
            case "skipped": return Brand.muted
            default: return Brand.idle
            }
        }()
        Circle().fill(c).frame(width: 10, height: 10)
    }
}

/// A text field with a small label above it.
struct LabeledField: View {
    let label: String
    @Binding var text: String
    var placeholder = ""
    var secure = false
    var keyboard: UIKeyboardType = .default

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label).font(.system(size: 12)).foregroundStyle(Brand.text2)
            Group {
                if secure { SecureField(placeholder, text: $text) } else { TextField(placeholder, text: $text) }
            }
            .keyboardType(keyboard)
            .autocorrectionDisabled()
            .textInputAutocapitalization(.never)
            .padding(10)
            .background(Color.white, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border))
        }
    }
}

/// Shows a web view that lives outside SwiftUI (WhatsApp Web, Pathao).
struct WebViewHost: UIViewRepresentable {
    let make: @MainActor () -> WKWebView

    func makeUIView(context: Context) -> WKWebView { make() }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
