import SwiftUI

@main
struct BoostimiseApp: App {
    init() {
        Account.shared.start()
        AppGuard.shared.start()
    }

    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        ZStack(alignment: .bottom) {
            Brand.bg.ignoresSafeArea()
            // a version / iPhone that may not run shows the update screen, signed in or not
            if AppGuard.shared.state == .locked {
                UpdateScreen()
            } else {
                switch Account.shared.state {
                case .loading: LoadingScreen()
                case .signedOut: SignInScreen()
                case .onboarding: OnboardingScreen()
                case .blocked: BlockedScreen()
                case .ready: ShellView()
                }
            }
            if !Toaster.shared.text.isEmpty {
                Text(Toaster.shared.text)
                    .font(.system(size: 14))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(Color.black.opacity(0.82), in: RoundedRectangle(cornerRadius: 12))
                    .padding(.horizontal, 24).padding(.bottom, 28)
                    .transition(.opacity)
                    .allowsHitTesting(false)
            }
        }
        .animation(.easeInOut(duration: 0.2), value: Toaster.shared.text)
        .preferredColorScheme(.light)
        .tint(Brand.accent)
        .onOpenURL { url in
            // photos opened in Boostimise from Files / other apps go to the Photo Enhancer
            if url.isFileURL {
                Tools.enhancer.add(urls: [url])
                Nav.shared.go(.enhancer)
            }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .background:
                Jobs.shared.enteredBackground()
                AppGuard.shared.onBackground()
            case .active:
                Jobs.shared.enteredForeground()
                AppGuard.shared.onForeground()  // checks again when the last check is older than a minute
            default: break
            }
        }
    }
}
