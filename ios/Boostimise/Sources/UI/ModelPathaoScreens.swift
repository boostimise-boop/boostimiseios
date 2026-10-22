import SwiftUI
import WebKit
import UniformTypeIdentifiers

struct ModelDetectScreen: View {
    var body: some View {
        @Bindable var t = Tools.modelDetect
        let groups = t.groups()
        let total = groups.reduce(0) { $0 + $1.1.count }
        let kinds: [String] = t.results.map { modelKind($0) }
        let found = kinds.filter { $0 == "ok" || $0 == "none" }.count
        let title: String = {
            if t.running { return "Finding the phone models of \(plural(total, "code"))" }
            if !t.results.isEmpty { return "\(found) of \(plural(t.results.count, "code")) have a model" }
            return total == 0 ? "Load the code list" : "\(plural(total, "code")) ready"
        }()
        let done: Int = t.running || !t.results.isEmpty ? Int((t.progress * Double(total)).rounded()) : 0
        Page {
            WaBanner()
            ControlCard(kicker: waKicker(t.running), title: title, sub: t.running ? t.activity : "",
                        ring: ringOf(done, total, "codes", running: t.running),
                        counts: [HeroCount(label: "Found", value: kinds.filter { $0 == "ok" }.count, color: Brand.live),
                                 HeroCount(label: "Unsure", value: kinds.filter { $0 == "none" }.count, color: Brand.amberOnNavy),
                                 HeroCount(label: "Not found", value: kinds.filter { $0 == "error" }.count, color: Brand.redOnNavy)]) {
                if t.running {
                    HeroButton("Stop", icon: "stop.fill", outline: true) { t.stop() }
                } else {
                    HeroButton("Start", icon: "play.fill", primary: true) { t.start() }
                }
            }
            Card(title: "Code list", hint: "Your product codes list. The models are written next to the codes.",
                 icon: "list.bullet", tint: .neutral) {
                CodeListBox(text: $t.codeText, placeholder: "5a-401\n5a-402\n5a-403 samsung a70")
                if !groups.isEmpty {
                    Hint("\(plural(total, "code")) in \(groups.count) group(s)")
                }
            }
            if !groups.isEmpty {
                Card(title: "Chat of each code group", hint: "A chat whose name contains the group code (e.g. \"(5a-)\") is chosen by itself.",
                     icon: "bubble.left.and.bubble.right", tint: .success) {
                    ForEach(groups.indices, id: \.self) { i in
                        let p = groups[i].0
                        let codes = groups[i].1
                        SingleChatPicker(label: (p.isEmpty ? "Orders" : "Group \(p)-") + "  (\(codes.count) codes, \(codes.first ?? "") … \(codes.last ?? ""))",
                                         chat: t.chatFor[p]) { t.chatFor[p] = $0 }
                    }
                }
            }
            Card(title: "Settings", icon: "slider.horizontal.3", tint: .warning) {
                SelectField(label: "In the chat", options: [("below", "Code first, photos below it (until the next code)"),
                                                           ("above", "Photos first, code below them (back to the previous code)")],
                            value: $t.mode)
                HStack(spacing: 8) {
                    NumberField(label: "Photos per code", value: $t.maxImages, range: 1...10)
                    NumberField(label: "Look back", value: $t.lookbackDays, range: 1...365, suffix: "days")
                }
                SelectField(label: "Identify phones with", options: [
                    ("text", "Model names written in the chat or in the photos (free)"),
                    ("claude", "Written names first, else ask Claude (paid, your API key)"),
                    ("none", "Don't identify - only save the photos"),
                ], value: $t.engine)
                if t.engine == "claude" {
                    LabeledField(label: "Claude API key", text: $t.apiKey, secure: true)
                    SelectField(label: "Model", options: ClaudeIdentifier.models.map { ($0, $0) }, value: $t.model)
                    SelectField(label: "Effort", options: ClaudeIdentifier.efforts.map { ($0, $0) }, value: $t.effort)
                }
                CheckRow("Save the photos (Files › Boostimise › Model Detect › <code>)", $t.savePhotos)
                CheckRow("Skip codes that already have a model in the list", $t.skipDone)
            }
            if !t.results.isEmpty {
                Card(title: "Results", hint: "The models are written next to the codes", icon: "iphone", tint: .accent) {
                    Flow(spacing: 8) {
                        PrimaryButton("Copy list with models") { copyText(t.resultText()) }
                        SecondaryButton("Save .txt") { saveText("productcodes models.txt", t.resultText()) }
                    }
                    VStack(spacing: 0) {
                        ForEach(t.results) { r in
                            StatusRow(kind: modelKind(r), code: r.code, name: r.model.ifEmpty("—"),
                                      status: [r.confidence.isEmpty ? "" : "\(r.confidence) confidence",
                                               r.images > 0 ? plural(r.images, "photo") : ""].filter { !$0.isEmpty }.joined(separator: " · "),
                                      more: [r.note])
                        }
                    }
                }
            }
            LogBox(log: t.log)
        }
        .onAppear { t.autoMatch(WaEngine.shared.chats) }
        .onChange(of: groups.map { $0.0 }) { _, _ in t.autoMatch(WaEngine.shared.chats) }
        .onChange(of: WaEngine.shared.chats) { _, c in t.autoMatch(c) }
    }
}

struct PathaoScreen: View {
    @State private var settings = false
    @State private var showList = false
    @State private var showLog = false
    @State private var importing = false

    var body: some View {
        let t = Tools.pathao
        VStack(spacing: 0) {
            // the controls: at most about half of the screen, the Pathao page below
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    WaBanner()
                    PathaoControl(t: t)
                    // the buttons wrap on a narrow screen instead of being squeezed
                    Flow(spacing: 8) {
                        SecondaryButton("Open list") { importing = true }
                        SecondaryButton("Paste list") { if let p = pasteText() { t.pasteList(p) } }
                        LinkButton("Settings") { settings = true }
                    }
                    if let item = t.cur, let o = item.order {
                        CurrentOrder(item: item, order: o).id(item.id.uuidString + o.code + o.phone)
                    }
                    Flow(spacing: 16) {
                        LinkButton(showList ? "Hide list" : "Show list") { showList.toggle() }
                        LinkButton(showLog ? "Hide log" : "Show log") { showLog.toggle() }
                        if t.listName == "pasted list" { LinkButton("Copy list") { copyText(t.listText) } }
                    }
                    if showList {
                        Card(title: "Codes", hint: t.listName.isEmpty ? nil : t.listName, icon: "list.bullet", tint: .neutral) {
                            VStack(spacing: 0) {
                                ForEach(t.items) { it in
                                    StatusRow(kind: pathaoKind(it.status), code: it.code, status: it.status) {
                                        if !t.running && [PathaoTool.NOT_FOUND, PathaoTool.SKIPPED, PathaoTool.PENDING].contains(it.status) {
                                            LinkButton("Do now") { t.reread(it) }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    if showLog {
                        ForEach(Array(t.log.lines.suffix(60).enumerated()), id: \.offset) { _, l in
                            Text(l).font(.system(size: 11)).foregroundStyle(Brand.navy)
                        }
                    }
                }
                .padding(12)
            }
            .frame(maxHeight: 400)
            .fixedSize(horizontal: false, vertical: true)
            Divider()
            WebViewHost { t.view() }
        }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.plainText, .text]) { r in
            if case .success(let url) = r {
                do { try t.openList(url) } catch { toast("Could not open the list") }
            }
        }
        .sheet(isPresented: $settings) { PathaoSettings(t: t) }
    }
}

/// A Model Detect result as a row kind: found ok, unsure "none" (amber), not found "error" (red).
private func modelKind(_ r: ModelDetectTool.Result) -> String {
    if !r.done { return "working" }
    if r.model.isEmpty || r.model == "NOT FOUND" || r.model == "NO IMAGE" { return "error" }
    return r.confidence == "low" ? "none" : "ok"
}

/// A Pathao code's status as a row kind (tools/pathao_autofill ROW_KINDS).
private func pathaoKind(_ status: String) -> String {
    switch status {
    case PathaoTool.FETCHING, PathaoTool.FILLED: return "working"
    case PathaoTool.SAVED, PathaoTool.SAVED_EARLIER, PathaoTool.IN_PATHAO: return "ok"
    case PathaoTool.NOT_FOUND: return "none"
    case PathaoTool.SKIPPED: return "skipped"
    default: return ""
    }
}

/// Pathao's control card: what to do next, the saved / to do / not found counts and the run's buttons.
private struct PathaoControl: View {
    let t: PathaoTool

    var body: some View {
        let kinds = t.items.map { pathaoKind($0.status) }
        let saved = kinds.filter { $0 == "ok" }.count
        let kicker: (String, Color) = {
            if !t.attention.isEmpty { return ("NEEDS YOU", Brand.amberOnNavy) }
            if t.checking { return ("CHECKING PATHAO", Brand.sky) }
            if t.waiting { return ("CHECK PATHAO AND SAVE", Brand.sky) }
            if t.running { return ("WORKING", Brand.live) }
            if !t.items.isEmpty && saved == t.items.count { return ("DONE", Brand.live) }
            return ("READY", Brand.sky)
        }()
        let sub: String = t.cur.map { "Current order: \($0.code)" } ?? (t.listName.isEmpty ? "" : "List: \(t.listName)")
        ControlCard(kicker: kicker, title: t.attention.isEmpty ? t.state : t.attention, sub: sub,
                    ring: ringOf(saved, t.items.count, "saved"),
                    counts: [HeroCount(label: "Saved", value: saved, color: Brand.live),
                             HeroCount(label: "To do", value: kinds.filter { $0 == "" || $0 == "working" }.count, color: Brand.sky),
                             HeroCount(label: "Not found", value: kinds.filter { $0 == "none" }.count, color: Brand.redOnNavy)]) {
            if t.running {
                HeroButton("Pause", icon: "pause.fill") { t.pause() }
            } else {
                HeroButton("Start", icon: "play.fill", primary: true) { t.start() }
            }
            HeroButton("Saved → Next", icon: "checkmark.circle", enabled: t.cur != nil) { t.markSaved() }
            HeroButton("Skip", icon: "forward.end") { t.skip() }
            // which codes of the list already have a Pathao order (by Merchant Order ID); the phone is written in the list
            HeroButton(t.checking ? "Checking Pathao…" : "Check Pathao", icon: "magnifyingglass",
                       enabled: !t.checking && !t.items.isEmpty) { t.checkPathao() }
        }
    }
}

private struct CurrentOrder: View {
    let item: PathaoTool.Item
    let order: OrderParser.Order
    @State private var name = ""
    @State private var phone = ""
    @State private var phone2 = ""
    @State private var amount = ""
    @State private var address = ""
    @State private var loaded = false

    var body: some View {
        Card(title: "Current order", hint: "\(item.code) · \(item.status)", icon: "shippingbox", tint: .accent) {
            if !item.raw.isEmpty {
                Text(item.raw.take(400)).font(.system(size: 12)).foregroundStyle(Brand.text2)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Brand.bg, in: RoundedRectangle(cornerRadius: 12))
            }
            HStack(spacing: 6) {
                LabeledField(label: "Name", text: $name)
                LabeledField(label: "Amount", text: $amount, keyboard: .numberPad).frame(width: 100)
            }
            HStack(spacing: 6) {
                LabeledField(label: "Phone", text: $phone, keyboard: .phonePad)
                LabeledField(label: "Phone 2", text: $phone2, keyboard: .phonePad)
            }
            LabeledField(label: "Address", text: $address)
            Flow(spacing: 12) {
                PrimaryButton("Fill again") {
                    var o = order
                    o.name = name
                    o.phone = phone
                    o.phone2 = phone2
                    o.amount = amount
                    o.address = address
                    Tools.pathao.refill(o)
                }
                LinkButton("Read WhatsApp again") { Tools.pathao.reread(item) }
            }
        }
        .onAppear {
            if loaded { return }
            loaded = true
            name = order.name
            phone = order.phone
            phone2 = order.phone2
            amount = order.amount
            address = order.address
        }
    }
}

private struct PathaoSettings: View {
    let t: PathaoTool
    @Environment(\.dismiss) private var dismiss
    @State private var newPrefix = ""

    var body: some View {
        @Bindable var t = self.t
        NavigationStack {
            Form {
                Section {
                    ForEach($t.chatNames) { $c in
                        HStack {
                            Text("\(c.prefix)-").frame(width: 44, alignment: .leading).foregroundStyle(Brand.text2)
                            TextField("Chat name", text: $c.name).autocorrectionDisabled()
                        }
                    }
                    HStack {
                        TextField("Add group (e.g. 7)", text: $newPrefix).autocorrectionDisabled().textInputAutocapitalization(.never)
                        Button("Add") {
                            let p = newPrefix.trimmed.lowercased()
                            if !p.isEmpty {
                                t.chatNames.append(PathaoTool.ChatName(prefix: p, name: "\(p)-"))
                                newPrefix = ""
                            }
                        }
                    }
                } header: {
                    Text("The WhatsApp chat of each code group")
                } footer: {
                    Text("The same as in Shop settings and your other Boostimise apps.")
                }
                Section {
                    NumberField(label: "Read order messages of the last", value: $t.lookbackDays, range: 1...365, suffix: "days")
                    CheckRow("Start from the bottom of the list", $t.startFromBottom)
                    CheckRow("After a save, write the phone next to the code in the list", $t.writePhone)
                }
            }
            .navigationTitle("Pathao Auto-fill settings")
            .onAppear { t.loadChatNames() }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        t.saveSettings()
                        dismiss()
                    }
                }
            }
        }
    }
}
