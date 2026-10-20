import SwiftUI

/// Shop settings (tools/account/shop.py): the shop's name, the reactions with what each one means, and
/// the WhatsApp chat of each code list. Kept in the account, so every Boostimise app uses them.
struct ShopScreen: View {
    private struct Row: Identifiable {
        let id = UUID()
        var a: String
        var b: String
    }

    @State private var name = ""
    @State private var reactions: [Row] = []
    @State private var chats: [Row] = []
    @State private var dirty = false
    @State private var error = ""

    var body: some View {
        let shop = Shop.shared
        let account = Account.shared
        Page {
            Hint(account.state == .ready && !account.offline
                 ? "Saved in your Boostimise account: the website and the Windows, Android and iPhone apps all use these settings."
                 : "Saved on this phone. They are shared with your other Boostimise apps once you are online.")
            Card(title: "Your shop") {
                TextField("Shop name", text: edit($name)).textFieldStyle(.roundedBorder)
            }
            CodeStyleSection()
            Card(title: "Reactions", hint: "The reactions Reaction List looks for, and what each one means to you " +
                 "(e.g. ❌ Cancelled). The meaning is written next to the reaction in the list.") {
                ForEach($reactions) { $r in
                    HStack {
                        TextField("Emoji", text: edit($r.a)).textFieldStyle(.roundedBorder).frame(width: 72)
                        TextField("What it means", text: edit($r.b)).textFieldStyle(.roundedBorder)
                        Button { reactions.removeAll { $0.id == r.id }; dirty = true } label: { Image(systemName: "xmark") }
                            .accessibilityLabel("Remove")
                    }
                }
                HStack {
                    SecondaryButton("Add reaction") { reactions.append(Row(a: "", b: "")); dirty = true }
                    LinkButton("Use ❌ 📵 😥") {
                        reactions = Shop.defaultReactions.map { Row(a: $0.emoji, b: $0.label) }
                        dirty = true
                    }
                }
            }
            Card(title: "WhatsApp chats", hint: "The chat where each code list's orders are posted, e.g. the 6- codes in " +
                 "“My Shop (6-)”. A list without a chat here uses the chat named like the list (“6-”).") {
                ForEach($chats) { $c in
                    HStack {
                        TextField("List", text: edit($c.a)).textFieldStyle(.roundedBorder).frame(width: 72)
                            .autocorrectionDisabled().textInputAutocapitalization(.never)
                        TextField("Chat name", text: edit($c.b)).textFieldStyle(.roundedBorder).autocorrectionDisabled()
                        Button { chats.removeAll { $0.id == c.id }; dirty = true } label: { Image(systemName: "xmark") }
                            .accessibilityLabel("Remove")
                    }
                }
                SecondaryButton("Add chat") { chats.append(Row(a: "", b: "")); dirty = true }
            }
            if !error.isEmpty { Hint(error, .red) }
            HStack {
                PrimaryButton("Save", enabled: dirty) { save() }
                SecondaryButton("Undo changes", enabled: dirty) { load() }
            }
        }
        .onAppear { load() }
        // shows what another app saved, unless something is being typed here
        .onChange(of: shop.version) { if !dirty { load() } }
    }

    /// A binding that marks the page as changed when it is typed in.
    private func edit(_ b: Binding<String>) -> Binding<String> {
        Binding(get: { b.wrappedValue }, set: { b.wrappedValue = $0; dirty = true })
    }

    private func load() {
        let shop = Shop.shared
        name = shop.shopName
        reactions = shop.reactions.map { Row(a: $0.emoji, b: $0.label) }
        chats = shop.chats.sorted { $0.key < $1.key }.map { Row(a: $0.key, b: $0.value) }
        dirty = false
        error = ""
    }

    private func save() {
        var problems: [String] = []
        var r: [ShopReaction] = []
        for row in reactions {
            if row.a.trimmed.isEmpty && row.b.trimmed.isEmpty { continue }
            let parts = ReactionEmojis.split(row.a)
            if parts.count != 1 || parts[0].unicodeScalars.allSatisfy({ $0.isASCII }) {
                problems.append("“\(row.a.trimmed.isEmpty ? row.b : row.a)”: put one emoji in the Emoji box.")
            } else {
                r.append(ShopReaction(emoji: parts[0], label: row.b.trimmed))
            }
        }
        if r.isEmpty { problems.append("Add at least one reaction.") }
        var c: [String: String] = [:]
        for row in chats {
            var prefix = row.a.trimmed.lowercased()
            while prefix.hasSuffix("-") { prefix.removeLast() }
            if prefix.isEmpty && row.b.trimmed.isEmpty { continue }
            if prefix.isEmpty || row.b.trimmed.isEmpty {
                problems.append("Each chat needs both the list (e.g. 6) and the chat's name.")
            } else {
                c[prefix] = row.b.trimmed
            }
        }
        if !problems.isEmpty {
            var seen = Set<String>()
            error = problems.filter { seen.insert($0).inserted }.joined(separator: "\n")
            return
        }
        Shop.shared.save(reactions: r, chats: c, name: name)
        load()
        toast("Shop settings saved")
    }
}
