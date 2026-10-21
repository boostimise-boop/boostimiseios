import SwiftUI
import WebKit

/// The signed-in app: a navy top bar with the menu, the screen, and WhatsApp Web behind it.
struct ShellView: View {
    @State private var drawer = false

    var body: some View {
        let nav = Nav.shared
        let wa = nav.screen == .whatsapp
        ZStack(alignment: .leading) {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    Button { withAnimation(.easeOut(duration: 0.2)) { drawer = true } } label: {
                        Image(systemName: "line.3.horizontal").font(.system(size: 20, weight: .medium)).foregroundStyle(.white)
                            .frame(width: 40, height: 40)
                    }
                    Text(nav.screen.title).font(.system(size: 19, weight: .semibold)).foregroundStyle(.white)
                    Spacer()
                    if !Jobs.shared.busy.isEmpty {
                        Circle().fill(Brand.success).frame(width: 9, height: 9)
                    }
                }
                .padding(.horizontal, 8)
                .frame(height: 52)
                .background(Brand.navy.ignoresSafeArea(edges: .top))
                UpdateBanner()
                if wa { WhatsAppHeader().background(Brand.bg) }
                ZStack {
                    // WhatsApp Web stays on screen all the time (behind the tools), so iOS keeps it running.
                    WebViewHost { WaEngine.shared.view() }
                    if !wa {
                        screen(nav.screen)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .background(Brand.bg)
                    }
                }
            }
            if drawer {
                Color.black.opacity(0.35).ignoresSafeArea()
                    .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { drawer = false } }
                    .transition(.opacity)
                DrawerView { s in
                    nav.go(s)
                    withAnimation(.easeOut(duration: 0.2)) { drawer = false }
                }
                .transition(.move(edge: .leading))
            }
        }
        .task { WaEngine.shared.open() }
    }

    @ViewBuilder
    private func screen(_ s: Nav.Screen) -> some View {
        switch s {
        case .enhancer: EnhancerScreen()
        case .fetchCode: FetchScreen(t: Tools.fetchByCode)
        case .fetchEnhance: FetchEnhanceScreen()
        case .reactionList: ReactionListScreen()
        case .modelDetect: ModelDetectScreen()
        case .pathao: PathaoScreen()
        case .dashboard: DashboardScreen()
        case .plans: PlansScreen()
        case .shop: ShopScreen()
        case .whatsapp: EmptyView()
        }
    }
}

struct DrawerView: View {
    let onPick: (Nav.Screen) -> Void

    var body: some View {
        let acc = Account.shared
        VStack(alignment: .leading, spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 10) {
                        Image("Logo").resizable().frame(width: 36, height: 36)
                        (Text("Boost").foregroundColor(Brand.navy) + Text("imise").foregroundColor(Brand.accent))
                            .font(.system(size: 20, weight: .bold))
                    }
                    .padding(20)
                    ForEach(Nav.Screen.allCases) { s in
                        if s == .whatsapp || s == .dashboard { Divider().padding(.vertical, 6) }
                        item(s)
                    }
                    Divider().padding(.vertical, 6)
                    HStack(spacing: 10) {
                        AsyncImage(url: URL(string: acc.displayAvatar() ?? "")) { img in img.resizable().scaledToFill() } placeholder: {
                            Brand.accentSoft
                        }
                        .frame(width: 32, height: 32).clipShape(Circle())
                        VStack(alignment: .leading) {
                            Text(acc.displayName() ?? "").font(.system(size: 14, weight: .semibold)).foregroundStyle(Brand.navy)
                            Text(acc.user?.email ?? "").font(.system(size: 12)).foregroundStyle(Brand.muted)
                        }
                    }
                    .padding(.horizontal, 24).padding(.vertical, 8)
                    Button { acc.signOut() } label: {
                        Label("Sign out", systemImage: "rectangle.portrait.and.arrow.right")
                            .font(.system(size: 15)).foregroundStyle(Brand.navy)
                            .padding(.horizontal, 24).padding(.vertical, 12)
                    }
                    Spacer(minLength: 12)
                }
            }
        }
        .frame(width: 290)
        .frame(maxHeight: .infinity)
        .background(Color.white.ignoresSafeArea())
    }

    private func item(_ s: Nav.Screen) -> some View {
        let selected = Nav.shared.screen == s
        return Button { onPick(s) } label: {
            HStack(spacing: 14) {
                Image(systemName: s.icon).frame(width: 24)
                Text(s.title).font(.system(size: 15, weight: selected ? .semibold : .regular))
                Spacer()
                if let k = s.busyKey, Jobs.shared.isBusy(k) { Circle().fill(Brand.success).frame(width: 9, height: 9) }
                if s == .whatsapp {
                    Circle().fill(WaEngine.shared.isReady ? Brand.success : Brand.warning).frame(width: 9, height: 9)
                }
            }
            .foregroundStyle(selected ? Brand.accentDark : Brand.navy)
            .padding(.horizontal, 16).padding(.vertical, 12)
            .background(selected ? Brand.accentSoft : Color.clear, in: Capsule())
            .padding(.horizontal, 12)
        }
        .buttonStyle(.plain)
    }
}
