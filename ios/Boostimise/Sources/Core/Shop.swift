import Foundation
import Observation

/// One reaction of the shop: the emoji and what it means to the seller ("Cancelled"), or "".
struct ShopReaction: Equatable {
    var emoji: String
    var label: String
}

/// The seller's shop settings (core/shop.py, android/.../core/Shop.kt), shared by the website and the
/// Windows, Android and iPhone apps through the account: the Reaction List reactions and what each one
/// means, the WhatsApp chat of each code prefix, and the code style with its orders chat (kept as they
/// come).
///
/// The phone keeps a copy so the tools work signed out and offline. Signed in, the account's row in
/// shop_profiles counts: no row yet -> this phone's settings are uploaded; this phone's first sync with
/// the account -> the row is kept and chats it doesn't have are added from the phone; after that the
/// row wins, except over a change made here that could not be uploaded yet.
@MainActor @Observable
final class Shop {
    static let shared = Shop()

    static let defaultReactions = [ShopReaction(emoji: "❌", label: ""), ShopReaction(emoji: "📵", label: ""),
                                   ShopReaction(emoji: "😥", label: "")]
    static let maxReactions = 12

    private(set) var settings: JSON
    private(set) var shopName: String
    /// Bumped on every change, for screens that copy the settings into their own state.
    private(set) var version = 0

    @ObservationIgnored private let prefs = Storage.prefs("shop")
    @ObservationIgnored private var syncedUser: String?
    @ObservationIgnored private var dirty: Bool
    @ObservationIgnored private var syncing = false
    @ObservationIgnored private var syncedThisRun: String?

    private init() {
        if let saved = JSONTools.parse(prefs.string("settings")) as? JSON {
            settings = Shop.clean(saved)
            shopName = prefs.string("shop_name")
            syncedUser = prefs.stringOrNil("synced_user")
            dirty = prefs.bool("dirty", false)
        } else {
            // Pathao Auto-fill's chat names from before the shop settings start this phone's copy
            let old = JSONTools.parse(Storage.prefs("pathao_autofill").string("chat_names")) as? JSON ?? [:]
            settings = Shop.clean(["chats": old])
            shopName = ""
            dirty = false
        }
        applyCodeStyle()
    }

    // MARK: - reading

    var reactions: [ShopReaction] { Shop.reactionsOf(settings) }
    var chats: [String: String] { Shop.chatsOf(settings) }
    func chatFor(_ prefix: String) -> String { Shop.chatName(chats, settings.str("orders_chat"), prefix) }
    func get(_ key: String) -> Any? { settings[key] }

    // MARK: - changing

    /// Keeps the change on this phone and uploads it when signed in. `other`: more settings keys
    /// (code_style, orders_chat …); nil removes one.
    func save(reactions: [ShopReaction]? = nil, chats: [String: String]? = nil, name: String? = nil,
              other: [String: Any?] = [:]) {
        var s = settings
        if let reactions { s["reactions"] = reactions.map { ["emoji": $0.emoji, "label": $0.label] } }
        if let chats { s["chats"] = chats }
        for (k, v) in other { if let v { s[k] = v } else { s.removeValue(forKey: k) } }
        let new = Shop.clean(s)
        let newName = name.map { String($0.trimmingCharacters(in: .whitespaces).prefix(80)) } ?? shopName
        if Shop.same(new, settings) && newName == shopName { return }
        settings = new
        shopName = newName
        dirty = true
        changed()
        upload()
    }

    func set(_ key: String, _ value: Any?) { save(other: [key: value]) }

    // MARK: - the account's copy

    private func uid() -> String? {
        let a = Account.shared
        return a.state == .ready && !a.offline ? a.user?.id : nil
    }

    /// Account says it is ready: sync once per account and app run (later changes come by Realtime).
    func onAccountReady() {
        guard let id = uid(), syncedThisRun != id else { return }
        sync()
    }

    func sync() {
        guard let id = uid(), !syncing else { return }
        syncing = true
        Task {
            let row: JSON?
            do {
                row = try await Account.shared.select("shop_profiles", "select=shop_name,settings,updated_at&user_id=eq.\(id)").first
            } catch {
                print("Shop: could not read the shop settings: \(error)")
                syncing = false
                return
            }
            syncing = false
            guard uid() == id else { return }
            syncedThisRun = id
            guard let row, !dirty else {
                if row == nil, let other = syncedUser, other != id {
                    settings = Shop.clean([:])  // another account's settings were on this phone
                    shopName = ""
                    changed()
                }
                upload()
                return
            }
            let server = Shop.clean(row.obj("settings") ?? [:])
            if syncedUser == nil {  // this phone's first sync: keep its chats the row doesn't have
                var merged = server
                merged["chats"] = Shop.chatsOf(settings).merging(Shop.chatsOf(server)) { _, s in s }
                merged = Shop.clean(merged)
                if !Shop.same(merged, server) {
                    settings = merged
                    shopName = row.strOrNil("shop_name") ?? shopName
                    dirty = true
                    changed()
                    upload()
                    return
                }
            }
            adopt(row, id)
        }
    }

    private func upload() {
        guard let id = uid() else { return }
        let sent = settings
        let name: Any? = shopName.isEmpty ? nil : shopName
        Task {
            let row: JSON?
            do {
                row = try await Account.shared.rpc("save_my_shop", jsonOf(("p_settings", sent), ("p_shop_name", name))) as? JSON
            } catch {
                print("Shop: could not save the shop settings: \(error)")
                return
            }
            guard uid() == id else { return }
            if Shop.same(sent, settings) { dirty = false }
            if let row, !dirty { adopt(row, id) } else {
                syncedUser = id
                write()
            }
        }
    }

    private func adopt(_ row: JSON, _ id: String) {
        let new = Shop.clean(row.obj("settings") ?? [:])
        let name = row.str("shop_name")
        let differs = !Shop.same(new, settings) || name != shopName
        settings = new
        shopName = name
        syncedUser = id
        dirty = false
        if differs { changed() } else { write() }
    }

    private func changed() {
        write()
        version += 1
        applyCodeStyle()
    }

    /// The tools read codes in the shop's style (settings["code_style"]; missing = prefix style). Called when
    /// the settings load and on every change.
    private func applyCodeStyle() {
        ProductCode.setStyle(settings["code_style"] as? JSON)
    }

    private func write() {
        prefs.set("settings", JSONTools.string(settings))
        prefs.set("shop_name", shopName)
        prefs.set("synced_user", syncedUser)
        prefs.set("dirty", dirty)
    }

    // MARK: - helpers

    static func chatName(_ chats: [String: String], _ ordersChat: String, _ prefix: String) -> String {
        let p = prefix.lowercased()
        if p.isEmpty { return ordersChat.trimmingCharacters(in: .whitespaces) }
        return (chats[p] ?? "\(p)-").trimmingCharacters(in: .whitespaces)
    }

    static func reactionsOf(_ s: JSON) -> [ShopReaction] {
        s.objs("reactions").map { ShopReaction(emoji: $0.str("emoji"), label: $0.str("label")) }
    }

    static func chatsOf(_ s: JSON) -> [String: String] {
        (s.obj("chats") ?? [:]).reduce(into: [:]) { out, kv in out[kv.key] = (kv.value as? String) ?? "" }
    }

    /// Reactions each once and not empty; chats with lower-case prefixes, without "<prefix>-" names.
    static func clean(_ s: JSON) -> JSON {
        var out = s
        if s["reactions"] != nil {
            var seen = Set<String>()
            out["reactions"] = Array(s.objs("reactions").compactMap { r -> JSON? in
                let e = r.str("emoji").trimmingCharacters(in: .whitespaces)
                guard !e.isEmpty, seen.insert(e).inserted else { return nil }
                return ["emoji": e, "label": String(r.str("label").trimmingCharacters(in: .whitespaces).prefix(40))]
            }.prefix(maxReactions))
        } else {
            out["reactions"] = defaultReactions.map { ["emoji": $0.emoji, "label": $0.label] }
        }
        var chats: [String: String] = [:]
        for (k, v) in s.obj("chats") ?? [:] {
            let p = k.trimmingCharacters(in: .whitespaces).lowercased()
            let n = ((v as? String) ?? "").trimmingCharacters(in: .whitespaces)
            if !p.isEmpty, !n.isEmpty, n != "\(p)-" { chats[p] = String(n.prefix(120)) }
        }
        out["chats"] = chats
        return out
    }

    private static func same(_ a: JSON, _ b: JSON) -> Bool { (a as NSDictionary).isEqual(to: b) }
}
