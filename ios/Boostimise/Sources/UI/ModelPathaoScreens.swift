import SwiftUI
import WebKit
import UniformTypeIdentifiers

struct ModelDetectScreen: View {
    var body: some View {
        @Bindable var t = Tools.modelDetect
        let groups = t.groups()
        Page {
            WaBanner()
            Card(title: "1  Code list", hint: "Your product codes list. The models are written next to the codes.") {
                CodeListBox(text: $t.codeText, placeholder: "5a-401\n5a-402\n5a-403 samsung a70")
                if !groups.isEmpty {
                    Hint("\(groups.reduce(0) { $0 + $1.1.count }) codes in \(groups.count) group(s)")
                }
            }
            if !groups.isEmpty {
                Card(title: "2  Chat of each code group", hint: "A chat whose name contains the group code (e.g. \"(5a-)\") is chosen by itself.") {
                    ForEach(groups.indices, id: \.self) { i in
                        let p = groups[i].0
                        let codes = groups[i].1
                        SingleChatPicker(label: (p.isEmpty ? "Orders" : "Group \(p)-") + "  (\(codes.count) codes, \(codes.first ?? "") … \(codes.last ?? ""))",
                                         chat: t.chatFor[p]) { t.chatFor[p] = $0 }
                    }
                }
            }
            Card(title: "3  Settings") {
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
            Card {
                RunBar(running: t.running, progress: t.progress, activity: t.activity, summary: "",
                       onStart: { t.start() }, onStop: { t.stop() })
            }
            if !t.results.isEmpty {
                Card(title: "Results") {
                    HStack(spacing: 8) {
                        PrimaryButton("Copy list with models") { copyText(t.resultText()) }
                        SecondaryButton("Save .txt") { saveText("productcodes models.txt", t.resultText()) }
                    }
                    ForEach(t.results) { r in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 8) {
                                StatusDot(kind: !r.done ? "working"
                                          : (r.model.isEmpty || r.model == "NOT FOUND" || r.model == "NO IMAGE" || r.confidence == "low") ? "none" : "ok")
                                Text(r.code).font(.system(size: 15, weight: .semibold))
                                Text(r.model.ifEmpty("—")).frame(maxWidth: .infinity, alignment: .leading)
                                if !r.confidence.isEmpty { Text(r.confidence).font(.system(size: 12)).foregroundStyle(Brand.muted) }
                            }
                            if !r.note.isEmpty {
                                Text(r.note + (r.images > 0 ? " · \(r.images) photo(s)" : "")).font(.system(size: 12)).foregroundStyle(Brand.text2)
                            }
                        }
                        .padding(.vertical, 4)
                        Divider()
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
                VStack(alignment: .leading, spacing: 8) {
                    WaBanner()
                    HStack(spacing: 8) {
                        SecondaryButton("Open list") { importing = true }
                        SecondaryButton("Paste list") { if let p = pasteText() { t.pasteList(p) } }
                        LinkButton("Settings") { settings = true }
                    }
                    if !t.listName.isEmpty {
                        Hint("\(t.listName): \(t.items.count) codes, " +
                             "\(t.items.filter { $0.status == PathaoTool.SAVED || $0.status == PathaoTool.SAVED_EARLIER || $0.status == PathaoTool.IN_PATHAO }.count) saved")
                    }
                    Text(t.state).font(.system(size: 13)).foregroundStyle(Brand.navy)
                    if !t.attention.isEmpty { Hint(t.attention, Brand.error) }
                    // which codes of the list already have a Pathao order (by Merchant Order ID); the phone is written in the list
                    SecondaryButton(t.checking ? "Checking Pathao…" : "Check Pathao", enabled: !t.checking && !t.items.isEmpty) { t.checkPathao() }
                    HStack(spacing: 8) {
                        if t.running { SecondaryButton("Pause") { t.pause() } } else { PrimaryButton("Start") { t.start() } }
                        SecondaryButton("Saved → Next", enabled: t.cur != nil) { t.markSaved() }
                        LinkButton("Skip") { t.skip() }
                    }
                    if let item = t.cur, let o = item.order {
                        CurrentOrder(item: item, order: o).id(item.id.uuidString + o.code + o.phone)
                    }
                    HStack(spacing: 16) {
                        LinkButton(showList ? "Hide list" : "Show list") { showList.toggle() }
                        LinkButton(showLog ? "Hide log" : "Show log") { showLog.toggle() }
                        if t.listName == "pasted list" { LinkButton("Copy list") { copyText(t.listText) } }
                    }
                    if showList {
                        ForEach(t.items) { it in
                            HStack {
                                Text(it.code).font(.system(size: 14, weight: .medium)).frame(width: 90, alignment: .leading)
                                Text(it.status).font(.system(size: 13)).foregroundStyle(Brand.text2)
                                Spacer()
                                if !t.running && [PathaoTool.NOT_FOUND, PathaoTool.SKIPPED, PathaoTool.PENDING].contains(it.status) {
                                    LinkButton("Do now") { t.reread(it) }
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
        VStack(alignment: .leading, spacing: 6) {
            Text("\(item.code) · \(item.status)").font(.system(size: 15, weight: .semibold)).foregroundStyle(Brand.navy)
            if !item.raw.isEmpty { Text(item.raw.take(400)).font(.system(size: 12)).foregroundStyle(Brand.text2) }
            HStack(spacing: 6) {
                LabeledField(label: "Name", text: $name)
                LabeledField(label: "Amount", text: $amount, keyboard: .numberPad).frame(width: 100)
            }
            HStack(spacing: 6) {
                LabeledField(label: "Phone", text: $phone, keyboard: .phonePad)
                LabeledField(label: "Phone 2", text: $phone2, keyboard: .phonePad)
            }
            LabeledField(label: "Address", text: $address)
            HStack(spacing: 12) {
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
        .padding(8)
        .background(Brand.accentSoft, in: RoundedRectangle(cornerRadius: 10))
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
