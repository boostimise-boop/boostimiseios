import SwiftUI

private let modeLabel = ["auto": "Auto", "face": "Face Enhance", "restore": "Photo Restore", "upscale": "Upscale"]

private func isActive(_ s: JSON) -> Bool {
    let exp = s.strOrNil("expires_at")
    return s.str("status") == "active" && (exp == nil || (parseISODate(exp).map { $0 > Date() } ?? true))
}

private func grouped(_ n: Int) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.locale = Locale(identifier: "en_US")
    return f.string(from: NSNumber(value: n)) ?? String(n)
}

struct DashboardScreen: View {
    @State private var stats: JSON = [:]
    @State private var recent: [JSON] = []
    @State private var subs: [JSON] = []
    @State private var profile: JSON?
    @State private var editPhone = false

    private func load() async {
        let acc = Account.shared
        guard let uid = acc.user?.id, !acc.offline else { return }
        do {
            stats = (try await acc.rpc("my_enhancement_stats", jsonOf(("p_days", 30))) as? JSON) ?? [:]
            recent = try await acc.select("enhancements", "select=id,file_name,mode,scale,input_width,input_height,output_width," +
                "output_height,status,processing_seconds,created_at&order=created_at.desc&limit=12")
            subs = try await acc.select("subscriptions", "select=id,plan_id,status,expires_at,plans(name)&order=created_at.desc&limit=5")
            profile = try await acc.select("profiles", "select=email,phone_e164,phone_country,created_at&id=eq.\(uid)").first
        } catch {
            toast("Dashboard: \(error.localizedDescription)")
        }
    }

    var body: some View {
        let acc = Account.shared
        let active = subs.first { isActive($0) }
        let q = acc.quota
        let limit = q?.limit ?? acc.limits.user
        let daily = stats.objs("daily")
        Page {
            Text("Hi, \(acc.firstName().ifEmpty("there"))").font(.system(size: 22, weight: .bold)).foregroundStyle(Brand.navy)
            Flow {
                Badge(active?.obj("plans")?.str("name") ?? acc.plan?.name ?? "Free account")
                if limit == nil { Badge("Unlimited daily enhancements") }
                Badge(acc.realtimeState == "subscribed" && !acc.offline ? "● Live" : "Offline")
            }
            HStack(spacing: 8) {
                Tile(title: "Today", value: stats.int("today"),
                     hint: limit == nil ? "No daily limit" : "\(q?.remaining ?? limit!) of \(limit!) left")
                Tile(title: "This week", value: stats.int("week"), hint: "Since Monday")
            }
            HStack(spacing: 8) {
                Tile(title: "This month", value: stats.int("month"), hint: dateText(Date(), "MMMM"))
                Tile(title: "All time", value: stats.int("total"), hint: "Enhancements")
            }
            let best = daily.map { $0.int("count") }.max() ?? 0
            Card(title: "Per day", hint: "Last 30 days" + (best > 0 ? " · best day \(best)" : "")) {
                if stats.int("total") == 0 { Hint("No enhancements yet - your daily numbers show here.") }
                else { Bars(values: daily.map { $0.int("count") }) }
            }
            Card(title: "Your plan") {
                Text(active?.obj("plans")?.str("name") ?? "Free account").font(.system(size: 15, weight: .semibold))
                let until = parseISODate(active?.strOrNil("expires_at")).map { " Active until \(dateText($0, "dd/MM/yyyy"))." } ?? ""
                Hint((limit == nil ? "Unlimited enhancements every day." : "\(limit!) enhancements per day.") + until)
                if let pending = subs.first(where: { $0.str("status") == "pending" }) {
                    Hint("\(pending.obj("plans")?.str("name") ?? pending.str("plan_id")) requested - waiting for approval")
                    LinkButton("Withdraw request") {
                        Task {
                            do {
                                _ = try await acc.rpc("cancel_my_subscription_request", jsonOf(("p_id", pending.str("id"))))
                                toast("Request withdrawn")
                                await load()
                            } catch {
                                toast("Could not cancel the request")
                            }
                        }
                    }
                }
                SecondaryButton(active != nil ? "Change" : "View plans") { Nav.shared.go(.plans) }
            }
            Card(title: "Account") {
                Hint("Email: \(profile?.strOrNil("email") ?? acc.user?.email ?? "")")
                HStack {
                    Hint("Mobile: \(profile?.strOrNil("phone_e164") ?? "—")")
                    LinkButton("Change") { editPhone = true }
                }
                if let since = parseISODate(profile?.strOrNil("created_at")) { Hint("Member since \(dateText(since, "MMMM yyyy"))") }
            }
            Card(title: "History", hint: "Your latest \(recent.count) photos") {
                if recent.isEmpty { Hint("Nothing yet.") }
                ForEach(Array(recent.enumerated()), id: \.offset) { _, e in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(e.str("file_name").ifEmpty("Photo")).font(.system(size: 14, weight: .medium)).lineLimit(1)
                        Hint(historyLine(e))
                    }
                    .padding(.vertical, 4)
                    Divider()
                }
            }
        }
        .task(id: acc.liveTick) { await load() }
        .sheet(isPresented: $editPhone) {
            NavigationStack {
                ScrollView {
                    PhoneForm(country0: profile?.strOrNil("phone_country"), phone0: profile?.strOrNil("phone_e164"), submitLabel: "Save") { _ in
                        toast("Mobile number updated")
                        editPhone = false
                        Task { await load() }
                    }
                    .padding(20)
                }
                .navigationTitle("Change mobile number")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { editPhone = false } } }
            }
        }
    }

    private func historyLine(_ e: JSON) -> String {
        var size = e.int("input_width") > 0 ? "\(e.int("input_width"))×\(e.int("input_height"))" : "-"
        if e.int("output_width") > 0 { size += " → \(e.int("output_width"))×\(e.int("output_height"))" }
        let when = parseISODate(e.str("created_at")).map { dateText($0, "dd/MM HH:mm") } ?? ""
        return "\(modeLabel[e.str("mode")] ?? "-") · \(e.int("scale"))× · \(size) · \(e.str("status")) · \(when)"
    }
}

private struct Badge: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text).font(.system(size: 12, weight: .medium)).foregroundStyle(Brand.accentDark)
            .padding(.horizontal, 10).padding(.vertical, 4)
            .background(Brand.accentSoft, in: Capsule())
    }
}

private struct Tile: View {
    let title: String
    let value: Int
    let hint: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title).font(.system(size: 12)).foregroundStyle(Brand.text2)
            Text(grouped(value)).font(.system(size: 24, weight: .bold)).foregroundStyle(Brand.navy)
            Text(hint).font(.system(size: 11)).foregroundStyle(Brand.muted).lineLimit(1)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(Brand.border))
    }
}

private struct Bars: View {
    let values: [Int]

    var body: some View {
        let top = max(values.max() ?? 0, 1)
        GeometryReader { geo in
            let n = max(values.count, 1)
            let slot = geo.size.width / CGFloat(n)
            HStack(alignment: .bottom, spacing: 0) {
                ForEach(Array(values.enumerated()), id: \.offset) { _, v in
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Brand.accent)
                        .frame(width: slot * 0.7, height: geo.size.height * CGFloat(v) / CGFloat(top))
                        .frame(width: slot, height: geo.size.height, alignment: .bottom)
                }
            }
        }
        .frame(height: 140)
    }
}

private let interval = ["month": "/ month", "year": "/ year", "lifetime": "one-time"]
private let symbols = ["USD": "$", "EUR": "€", "GBP": "£", "BDT": "৳", "INR": "₹", "JPY": "¥", "AUD": "A$", "CAD": "CA$"]

private func price(_ cents: Int, _ currency: String) -> String {
    let amount = Double(cents) / 100
    let f = NumberFormatter()
    f.numberStyle = .decimal
    f.locale = Locale(identifier: "en_US")
    f.minimumFractionDigits = cents % 100 != 0 ? 2 : 0
    f.maximumFractionDigits = cents % 100 != 0 ? 2 : 0
    let text = f.string(from: NSNumber(value: amount)) ?? String(amount)
    if let s = symbols[currency.uppercased()] { return s + text }
    return String(format: "%.2f %@", amount, currency)
}

struct PlansScreen: View {
    @State private var plans: [JSON] = []
    @State private var subs: [JSON] = []
    @State private var request: JSON?
    @State private var note = ""
    @State private var busy = false

    private func load() async {
        let acc = Account.shared
        guard acc.user != nil, !acc.offline else { return }
        do {
            plans = try await acc.select("plans",
                "select=id,name,description,price_cents,currency,billing_interval,daily_limit,features&is_active=eq.true&order=sort_order")
            subs = try await acc.select("subscriptions", "select=plan_id,status,expires_at&status=in.(active,pending)")
        } catch {}
    }

    var body: some View {
        let acc = Account.shared
        let userLimit = acc.limits.user
        let activePlan = subs.first { isActive($0) }?.str("plan_id")
        let pendingPlan = subs.first { $0.str("status") == "pending" }?.str("plan_id")
        Page {
            Text("Simple pricing").font(.system(size: 26, weight: .bold)).foregroundStyle(Brand.navy)
            Hint("Every Boostimise account is free with Google sign-in. Enhance " +
                 (userLimit == nil ? "without a daily limit." : "more every day with a paid plan."))
            Card(title: "Free account", hint: "Sign in with Google") {
                Text("Free").font(.system(size: 26, weight: .bold))
                ForEach([userLimit == nil ? "Unlimited photos every day" : "\(userLimit!) photos per day", "AI Upscale 2× / 4×",
                         "Face & photo restoration", "Daily, weekly & monthly stats", "Enhancement history"], id: \.self) { Hint("✓ \($0)") }
                SecondaryButton(activePlan != nil ? "Included" : "Current plan", enabled: false) {}
            }
            ForEach(Array(plans.enumerated()), id: \.offset) { _, p in
                let id = p.str("id")
                Card(title: p.str("name"), hint: p.strOrNil("description")?.nilIfEmpty) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(price(p.int("price_cents"), p.str("currency", "USD").ifEmpty("USD"))).font(.system(size: 26, weight: .bold))
                        Text(interval[p.str("billing_interval")] ?? "").foregroundStyle(Brand.muted)
                    }
                    Hint("✓ " + (p.isNull("daily_limit") ? "Unlimited photos every day" : "\(p.int("daily_limit")) photos per day"))
                    ForEach(p.strs("features"), id: \.self) { Hint("✓ \($0)") }
                    if activePlan == id {
                        SecondaryButton("Current plan", enabled: false) {}
                    } else if pendingPlan == id {
                        SecondaryButton("Requested - awaiting approval", enabled: false) {}
                    } else {
                        PrimaryButton("Get \(p.str("name"))", full: true, enabled: pendingPlan == nil) {
                            note = ""
                            request = p
                        }
                    }
                }
            }
            Hint("Daily enhancements reset every day at midnight (\(acc.limits.timezone))." +
                 (plans.isEmpty ? "" : " Paid plans are activated by our team once your payment is confirmed."))
        }
        .task(id: acc.liveTick) { await load() }
        .sheet(isPresented: Binding(get: { request != nil }, set: { if !$0 { request = nil } })) {
            if let p = request { requestSheet(p) }
        }
    }

    private func requestSheet(_ p: JSON) -> some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 12) {
                Hint("\(price(p.int("price_cents"), p.str("currency", "USD").ifEmpty("USD"))) \(interval[p.str("billing_interval")] ?? ""). " +
                     "Our team activates your plan as soon as your payment is confirmed - you'll see it on your dashboard.")
                LabeledField(label: "Payment reference or message (optional)", text: $note, placeholder: "e.g. transaction ID")
                PrimaryButton(busy ? "Sending…" : "Send request", full: true, enabled: !busy) {
                    busy = true
                    Task {
                        do {
                            let n = String(note.trimmed.prefix(500))
                            _ = try await Account.shared.rpc("request_my_subscription", jsonOf(("p_plan_id", p.str("id")), ("p_note", n.nilIfEmpty)))
                            toast("Request sent - we'll activate your plan once payment is confirmed.")
                            request = nil
                            await load()
                        } catch {
                            toast(Supabase.dbMessage(error, "Could not send your request"))
                        }
                        busy = false
                    }
                }
                Spacer()
            }
            .padding(20)
            .navigationTitle("Request \(p.str("name"))")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { request = nil } } }
        }
    }
}
