import PhoneNumberKit
import SwiftUI

private struct Centered<Content: View>: View {
    @ViewBuilder var content: Content

    var body: some View {
        ScrollView {
            VStack(spacing: 16) {
                Image("LogoFull").resizable().scaledToFit().frame(width: 200)
                content
            }
            .padding(24)
            .padding(.top, 48)
            .frame(maxWidth: 480)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
    }
}

struct LoadingScreen: View {
    var body: some View {
        Centered { ProgressView() }
    }
}

struct SignInScreen: View {
    var body: some View {
        let acc = Account.shared
        Centered {
            Card {
                Text("Sign in to Boostimise").font(.system(size: 20, weight: .bold)).foregroundStyle(Brand.navy)
                    .frame(maxWidth: .infinity)
                Hint("Free account - no password to remember. The same account as on boostimise.com and the PC app.")
                if !acc.signedOutReason.isEmpty { Hint(acc.signedOutReason, Brand.error) }
                if acc.signInState == "error" { Hint(acc.signInMessage, Brand.error) }
                switch acc.signInState {
                case "waiting":
                    Hint("Choose your Google account, then you come back here by yourself.")
                    SecondaryButton("Cancel", full: true) { acc.cancelSignIn("") }
                case "working":
                    HStack(spacing: 8) {
                        ProgressView()
                        Text("Signing you in…").foregroundStyle(Brand.navy)
                    }
                default:
                    PrimaryButton("Continue with Google", full: true) { acc.signIn() }
                }
                let limits = acc.publicLimits
                Hint("Free account: " + (limits.user.map { "\($0) photo enhancements per day" } ?? "unlimited photo enhancements") + ".")
                HStack(spacing: 20) {
                    Link("Terms", destination: URL(string: Config.termsURL)!)
                    Link("Privacy", destination: URL(string: Config.privacyURL)!)
                }
                .font(.system(size: 14))
                .frame(maxWidth: .infinity)
            }
        }
    }
}

struct OnboardingScreen: View {
    var body: some View {
        let acc = Account.shared
        Centered {
            Card(title: "Add your mobile number", hint: "Once, like on the website. We use it only for your account.") {
                PhoneForm(country0: acc.status?.country, phone0: nil, askName: (acc.displayName() ?? "").trimmed.isEmpty) { _ in
                    acc.refresh()
                }
                LinkButton("Sign out") { acc.signOut() }
            }
        }
    }
}

struct BlockedScreen: View {
    var body: some View {
        let acc = Account.shared
        Centered {
            Card(title: "Account suspended",
                 hint: "Your Boostimise account has been suspended. Contact Cover Canvas BD if you think this is a mistake.") {
                PrimaryButton("Check again") { acc.refresh() }
                LinkButton("Sign out") { acc.signOut() }
            }
        }
    }
}

func flag(_ cc: String) -> String {
    guard cc.count == 2 else { return "🌐" }
    var s = ""
    for u in cc.uppercased().unicodeScalars {
        if let f = Unicode.Scalar(0x1F1E6 + u.value - 65) { s.unicodeScalars.append(f) }
    }
    return s
}

let phoneUtil = PhoneNumberKit()

/// The mobile number with its country code (core/account_widgets.py's PhoneForm).
struct PhoneForm: View {
    let country0: String?
    let phone0: String?
    var askName = false
    var submitLabel = "Continue"
    let onSaved: (String) -> Void

    @State private var country = "BD"
    @State private var phone = ""
    @State private var name = ""
    @State private var error = ""
    @State private var busy = false
    @State private var picking = false
    @State private var started = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if askName { LabeledField(label: "Your name", text: $name) }
            HStack(alignment: .bottom, spacing: 8) {
                Button { picking = true } label: {
                    Text("\(flag(country)) +\(phoneUtil.countryCode(for: country).map { String($0) } ?? "")")
                        .padding(.horizontal, 12).padding(.vertical, 10)
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Brand.border))
                }
                .buttonStyle(.plain)
                LabeledField(label: "Mobile number", text: $phone, keyboard: .phonePad)
            }
            if !error.isEmpty { Hint(error, Brand.error) }
            PrimaryButton(busy ? "Saving…" : submitLabel, full: true, enabled: !busy) { submit() }
        }
        .onAppear {
            if started { return }
            started = true
            let c0 = (country0 ?? "BD").uppercased()
            let c = phoneUtil.allCountries().contains(c0) ? c0 : "BD"
            country = c
            if let p0 = phone0, !p0.isEmpty, let p = try? phoneUtil.parse(p0, withRegion: c) {
                phone = (p.leadingZero ? "0" : "") + String(p.nationalNumber)
            }
        }
        .sheet(isPresented: $picking) { CountrySheet { country = $0 } }
    }

    private func submit() {
        error = ""
        guard let p = try? phoneUtil.parse(phone, withRegion: country) else {
            error = "Enter a valid mobile number for the selected country."
            return
        }
        let e164 = phoneUtil.format(p, toType: .e164)
        let national = (p.leadingZero ? "0" : "") + String(p.nationalNumber)
        busy = true
        Task {
            do {
                let res = try await Account.shared.rpc("save_my_profile", jsonOf(
                    ("p_country", phoneUtil.getRegionCode(of: p) ?? country), ("p_dial_code", "+\(p.countryCode)"),
                    ("p_national", national), ("p_e164", e164), ("p_full_name", askName ? name : nil)))
                onSaved((res as? String) ?? e164)
            } catch {
                self.error = Supabase.dbMessage(error, "Could not save your number. Please try again.")
            }
            busy = false
        }
    }
}

private struct Country: Identifiable {
    let id: String
    let name: String
}

private struct CountrySheet: View {
    let onPick: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var q = ""

    var body: some View {
        let all = phoneUtil.allCountries().filter { $0.count == 2 }
            .map { Country(id: $0, name: Locale.current.localizedString(forRegionCode: $0) ?? $0) }
            .sorted { $0.name < $1.name }
        let shown = all.filter { q.trimmed.isEmpty || $0.name.localizedCaseInsensitiveContains(q) || $0.id.caseInsensitiveCompare(q) == .orderedSame }
        NavigationStack {
            List(shown) { c in
                Button {
                    onPick(c.id)
                    dismiss()
                } label: {
                    Text("\(flag(c.id))  \(c.name)  +\(phoneUtil.countryCode(for: c.id).map { String($0) } ?? "")").foregroundStyle(Brand.navy)
                }
            }
            .searchable(text: $q, placement: .navigationBarDrawer(displayMode: .always), prompt: "Search")
            .navigationTitle("Country")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
    }
}
