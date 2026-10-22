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
            FetchControl(t: t)
            Card(title: "Codes", hint: codesHint, icon: "list.bullet", tint: .neutral) {
                CodeListBox(text: Binding(get: { t.codeText }, set: { t.codeText = $0; t.parse() }))
                if !t.rows.isEmpty { Hint(plural(t.rows.count, "code")) }
            }
            Card(title: "Settings", hint: "Where to look and how to name the photos", icon: "slider.horizontal.3", tint: .warning) {
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
            if !t.rows.isEmpty {
                Card(title: "Results", hint: "Saved in \(Storage.displayPath(t.folderName))", icon: "photo.on.rectangle", tint: .success) {
                    VStack(spacing: 0) {
                        ForEach(t.rows) { r in
                            StatusRow(kind: r.kind, code: r.item.code,
                                      name: [r.item.name, r.item.date].filter { !$0.isEmpty }.joined(separator: "  "), status: r.text) {
                                if !t.running && ["none", "error", ""].contains(r.kind) { LinkButton("Retry") { t.start(only: [r]) } }
                            }
                        }
                    }
                    LinkButton("Open in Files") { Storage.openInFiles(t.folderName) }
                }
            }
            LogBox(log: t.log)
        }
    }
}

/// The control card of Fetch by Code and Fetch & Enhance: the code being read, the counts, Start / Stop.
private struct FetchControl: View {
    let t: FetchTool

    var body: some View {
        let n = t.rows.count
        let kinds = t.rows.map { $0.kind }
        let done = kinds.filter { DONE_KINDS.contains($0) }.count
        let working = t.rows.first { $0.kind == "working" }
        let title: String = {
            if t.running, let w = working { return [w.item.code, w.item.name].filter { !$0.isEmpty }.joined(separator: " ") }
            if t.running { return "Starting…" }
            if n == 0 { return "Paste your code list" }
            return done > 0 ? "\(done) of \(plural(n, "code")) done" : "\(plural(n, "code")) ready"
        }()
        let workingText = working?.text ?? ""
        ControlCard(kicker: waKicker(t.running), title: title,
                    sub: t.running ? (workingText.isEmpty ? t.activity : workingText) : t.summary,
                    ring: ringOf(done, n, "codes", running: t.running),
                    counts: [HeroCount(label: "Saved", value: kinds.filter { $0 == "ok" }.count, color: Brand.live),
                             HeroCount(label: "In progress", value: kinds.filter { $0 == "working" }.count, color: Brand.sky),
                             HeroCount(label: "Skipped", value: kinds.filter { $0 == "skipped" }.count, color: Brand.onNavy),
                             HeroCount(label: "No photos", value: kinds.filter { $0 == "none" || $0 == "error" }.count,
                                       color: Brand.amberOnNavy)]) {
            if t.running {
                HeroButton("Stop", icon: "stop.fill", outline: true) { t.stop() }
            } else {
                HeroButton("Start", icon: "play.fill", primary: true) { t.start() }
            }
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
        let label = t.choice.label
        let title: String = {
            if t.running { return "Codes reacted \(label)" }
            if t.results.isEmpty { return "List the codes reacted \(label)" }
            return "\(plural(t.results.count, "code")) reacted \(label)"
        }()
        let sub: String = t.running ? (t.activity.isEmpty ? "Reading the chats…" : t.activity) : t.summary
        let share: Double = t.running ? t.progress : (t.results.isEmpty ? 0 : 1)
        Page {
            WaBanner()
            ControlCard(kicker: waKicker(t.running), title: title, sub: sub,
                        ring: RingState(share: share, text: "\(t.results.count)", unit: "codes")) {
                if t.running {
                    HeroButton("Stop", icon: "stop.fill", outline: true) { t.stop() }
                } else {
                    HeroButton(t.withPhotos ? "Fetch list + photos" : "Fetch list", icon: "play.fill", primary: true) { t.start() }
                }
            }
            Card(title: "Reaction", hint: "The codes whose message got it are listed", icon: "face.smiling", tint: .warning) {
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
            Card(title: "Codes", hint: "Optional: leave empty to list every code that got the reaction in the chats read.",
                 icon: "list.bullet", tint: .neutral) {
                CodeListBox(text: $t.codeText)
            }
            Card(title: "Settings", icon: "slider.horizontal.3") {
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
            if !t.results.isEmpty {
                Card(title: plural(t.results.count, "code"), hint: "Reacted \(label)", icon: "checkmark.circle", tint: .success) {
                    Flow(spacing: 8) {
                        PrimaryButton("Copy list") { copyText(t.formatList()) }
                        SecondaryButton("Save .txt") { saveText("Reaction list \(dateText(Date(), "dd-MM-yyyy")).txt", t.formatList()) }
                    }
                    VStack(spacing: 0) {
                        ForEach(Array(t.results.enumerated()), id: \.element.id) { n, f in
                            let emojis = f.react(t.shown)
                            let labelled = f.react(t.shown, t.labels)
                            StatusRow(kind: "ok", code: f.code, name: f.name, status: f.phones.joined(separator: ", "),
                                      badge: emojis.isEmpty ? "\(n + 1)" : emojis,
                                      more: [labelled != emojis ? labelled : "", f.chats.joined(separator: ", "),
                                             f.photos.isEmpty ? "" : "Photos: \(f.photos)"])
                        }
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
