import SwiftUI

/// Above the WhatsApp Web page: its state and how to link this phone.
struct WhatsAppHeader: View {
    @State private var help = false
    @State private var forget = false
    @State private var details: String?

    var body: some View {
        let wa = WaEngine.shared
        VStack(alignment: .leading, spacing: 6) {
            WaBanner()
            if wa.phase == .qr {
                Hint("Below, tap \"Log in with phone number\", enter your WhatsApp number and you get an 8-letter code. " +
                     "Then in WhatsApp on your phone: Settings › Linked devices › Link a device › \"Link with phone number instead\", and type the code.")
            }
            HStack(spacing: 14) {
                SecondaryButton("Reload") { wa.reload() }
                LinkButton("Details") { Task { details = await wa.diagnostics() } }
                LinkButton("Help") { help = true }
                LinkButton("Log out") { forget = true }
            }
        }
        .padding(12)
        .alert("WhatsApp in Boostimise", isPresented: $help) {
            Button("OK", role: .cancel) {}
        } message: {
            Text("All tools share this one WhatsApp Web, linked once to your WhatsApp like a PC. It counts as one of WhatsApp's " +
                 "linked devices next to your PC's. Pinch to zoom the page. Keep Boostimise open while a tool reads chats: " +
                 "iOS pauses apps in the background.")
        }
        .alert("Log out of WhatsApp Web?", isPresented: $forget) {
            Button("Log out", role: .destructive) { wa.forgetLogin() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Every tool will need WhatsApp linked again.")
        }
        .sheet(isPresented: Binding(get: { details != nil }, set: { if !$0 { details = nil } })) {
            NavigationStack {
                ScrollView {
                    Text(details ?? "").font(.system(size: 11, design: .monospaced)).textSelection(.enabled).padding()
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .navigationTitle("WhatsApp Web details")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Close") { details = nil } }
                    ToolbarItem(placement: .primaryAction) {
                        Menu("More") {
                            Button("Copy") { copyText(details ?? "") }
                            Button("Clear cache & reload") {
                                wa.reload(clearCache: true)
                                details = nil
                            }
                        }
                    }
                }
            }
        }
    }
}

struct FetchScreen: View {
    let t: FetchTool
    var codesHint = "Lines like \"2-558  Realme C85  26/9\" - the photos are saved as \"2-558 Realme C85.jpg\"."
    /// More settings (Fetch & Enhance's), shown before the folder.
    var extra: AnyView? = nil

    var body: some View {
        @Bindable var t = self.t
        Page {
            WaBanner()
            Card(title: "1  Codes", hint: codesHint) {
                CodeListBox(text: Binding(get: { t.codeText }, set: { t.codeText = $0; t.parse() }))
                if !t.rows.isEmpty { Hint("\(t.rows.count) codes") }
            }
            Card(title: "2  Settings") {
                ChatFilterPicker(filter: $t.chat)
                CheckRow("A chat for each list (6-, 5a- …)", Binding(get: { t.perList }, set: {
                    t.perList = $0
                    if $0 { t.autoFillListChats() }
                }))
                if t.perList { ListChats(t: t) }
                SelectField(label: "Take", options: NEIGHBOUR_MODES, value: $t.neighbors)
                HStack(spacing: 8) {
                    NumberField(label: "Album gap", value: $t.gapMinutes, range: 1...240, suffix: "min")
                    NumberField(label: "Look back", value: $t.lookbackDays, range: 1...365, suffix: "days")
                }
                CheckRow("Add the date to names (26/9 → 26-9)", $t.includeDate)
                CheckRow("Skip codes that are already saved", $t.skipExisting)
                if let extra { extra }
                LabeledField(label: "Save in Files › Boostimise ›", text: $t.folder)
            }
            Card(title: "3  Run") {
                RunBar(running: t.running, progress: t.progress, activity: t.activity, summary: t.summary,
                       onStart: { t.start() }, onStop: { t.stop() })
            }
            if !t.rows.isEmpty {
                Card(title: "Results") {
                    ForEach(t.rows) { r in
                        HStack(spacing: 8) {
                            StatusDot(kind: r.kind)
                            VStack(alignment: .leading, spacing: 2) {
                                Text([r.item.code, r.item.name, r.item.date].filter { !$0.isEmpty }.joined(separator: "  "))
                                    .font(.system(size: 14, weight: .medium)).foregroundStyle(Brand.navy)
                                if !r.text.isEmpty { Text(r.text).font(.system(size: 12)).foregroundStyle(Brand.text2) }
                            }
                            Spacer()
                            if !t.running && ["none", "error", ""].contains(r.kind) { LinkButton("Retry") { t.start(only: [r]) } }
                        }
                    }
                    HStack {
                        Hint("Saved in \(Storage.displayPath(t.folderName))")
                        Spacer()
                        LinkButton("Open") { Storage.openInFiles(t.folderName) }
                    }
                }
            }
            LogBox(log: t.log)
        }
    }
}

/// One chat picker per list in the code list, filled in from chat names ("My Shop (6-)").
private struct ListChats: View {
    let t: FetchTool

    var body: some View {
        let lists = t.lists()
        Group {
            if lists.isEmpty {
                Hint("Paste the code list above: each list (6-, 5a- …) then gets its chat here.")
            } else {
                ForEach(lists.indices, id: \.self) { i in
                    let list = lists[i].0
                    let count = lists[i].1
                    ListChatPicker(label: "\(list) list · \(count) code" + (count > 1 ? "s" : ""), chat: t.listChats[list],
                                   fallback: "No \"(\(list))\" chat: searched in \(t.chat.label)") { t.listChats[list] = $0 }
                }
                Hint("Filled in from chat names with \"(6-)\", \"(5a-)\" …. Tap one to choose another chat. " +
                     "Each list's codes are searched only in its chat.")
            }
        }
        .onAppear { t.autoFillListChats() }
        .onChange(of: WaEngine.shared.chats) { _, _ in t.autoFillListChats() }
        .onChange(of: lists.map { $0.0 }) { _, _ in t.autoFillListChats() }
    }
}

struct ReactionListScreen: View {
    var body: some View {
        @Bindable var t = Tools.reactionList
        Page {
            WaBanner()
            Card(title: "Reaction") {
                Flow {
                    ForEach(t.choices, id: \.self) { c in Chip(label: c.label, selected: c.key == t.choice.key) { t.choiceKey = c.key } }
                }
                LabeledField(label: "Custom reactions", text: $t.customText, placeholder: "Type your emojis, e.g. 👍 ❤️ 🔥")
                    .disabled(t.running)
                    .onChange(of: t.customText) { _, _ in
                        if !t.customText.trimmed.isEmpty { t.choiceKey = CUSTOM }
                    }
                Hint("😥 also counts 😢 (WhatsApp's built-in sad reaction). Skin tones count as one reaction (👍🏻 = 👍).")
            }
            Card(title: "Codes (optional)", hint: "Leave empty to list every code that got the reaction in the chats read.") {
                CodeListBox(text: $t.codeText)
            }
            Card(title: "Settings") {
                ChatFilterPicker(filter: $t.chat)
                NumberField(label: "Look back", value: $t.lookbackDays, range: 1...365, suffix: "days")
                Text("Fetch").font(.system(size: 12)).foregroundStyle(Brand.text2)
                Flow {
                    Chip(label: "List only", selected: !t.withPhotos) { if !t.running { t.withPhotos = false } }
                    Chip(label: "List + photos", selected: t.withPhotos) { if !t.running { t.withPhotos = true } }
                }
                if t.withPhotos {
                    LabeledField(label: "Save in Files › Boostimise ›", text: $t.folder)
                        .disabled(t.running)
                    Hint("Saves each listed code's reacted photos as \"2-558 Realme C85.jpg\". A code whose message has the " +
                         "reaction gets all its photos; otherwise only the photos with the reaction.")
                }
            }
            Card {
                RunBar(running: t.running, progress: t.progress, activity: t.activity, summary: t.summary,
                       startLabel: t.withPhotos ? "Fetch list + photos" : "Fetch list",
                       onStart: { t.start() }, onStop: { t.stop() })
            }
            if !t.results.isEmpty {
                Card(title: "\(t.results.count) codes") {
                    HStack(spacing: 8) {
                        PrimaryButton("Copy list") { copyText(t.formatList()) }
                        SecondaryButton("Save .txt") { saveText("Reaction list \(dateText(Date(), "dd-MM-yyyy")).txt", t.formatList()) }
                    }
                    ForEach(Array(t.results.enumerated()), id: \.element.id) { n, f in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(n + 1). \(f.code) \(f.name) \(f.react(t.shown, t.labels))").font(.system(size: 15, weight: .medium))
                                .foregroundStyle(Brand.navy)
                            if !f.phones.isEmpty { Text(f.phones.joined(separator: ", ")).font(.system(size: 13)) }
                            Text(f.chats.joined(separator: ", ")).font(.system(size: 12)).foregroundStyle(Brand.muted)
                            if !f.photos.isEmpty { Text("Photos: \(f.photos)").font(.system(size: 12)).foregroundStyle(Brand.text2) }
                        }
                        .padding(6)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(n % 2 == 0 ? Brand.bg : Brand.surface)
                    }
                    if t.results.contains(where: { !$0.photos.isEmpty }) {
                        HStack {
                            Hint("Photos saved in \(Storage.displayPath(t.folderName))")
                            Spacer()
                            LinkButton("Open") { Storage.openInFiles(t.folderName) }
                        }
                    }
                }
            }
            LogBox(log: t.log)
        }
    }
}

struct FetchEnhanceScreen: View {
    let t = Tools.fetchEnhance

    var body: some View {
        @Bindable var t = self.t
        FetchScreen(t: t.fetch,
                    codesHint: "Lines like \"2-558  Realme C85\" - each code's photos of people are enhanced and saved as " +
                        "\"2-558 Realme C85.jpg\"; 2 or more go into a folder \"2-558 Realme C85\" as img1.jpg, img2.jpg …",
                    extra: AnyView(VStack(alignment: .leading, spacing: 10) {
                        SelectField(label: "Enhance", options: ENHANCE_SCALES, value: $t.scale)
                        CheckRow("Only photos of people (skip phones, screenshots …)", $t.peopleOnly)
                        CheckRow("Add the (note) from the WhatsApp caption", $t.addNote)
                        Hint("Each photo is enhanced on the Boostimise server like in the Photo Enhancer, and counts in your daily " +
                             "enhancements. The names come from the code list straight away (the Windows app can also check them " +
                             "with Rename by Match).")
                    }))
        .alert(t.denied == "blocked" ? "Account suspended" : "Daily limit reached",
               isPresented: Binding(get: { !t.denied.isEmpty }, set: { if !$0 { t.denied = "" } })) {
            if t.denied == "limit" { Button("See plans") { t.denied = ""; Nav.shared.go(.plans) } }
            Button("Close", role: .cancel) { t.denied = "" }
        } message: {
            let q = Account.shared.quota
            Text(t.denied == "blocked" ? "Your account has been suspended."
                 : "You've used today's enhancements" + ((q?.resetsAt ?? "").isEmpty ? "" : " - more in \(formatResetIn(q!.resetsAt))") +
                   ". A paid plan gives you more every day.")
        }
    }
}
