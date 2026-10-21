import Observation
import SwiftUI
import UIKit

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255, green: Double((hex >> 8) & 0xff) / 255, blue: Double(hex & 0xff) / 255)
    }
}

// The logo's colours (core/theme.py): navy "B", blue photo and sparkles.
enum Brand {
    static let navy = Color(hex: 0x0F1D3A)
    static let sky = Color(hex: 0x1EA0FF)
    static let accent = Color(hex: 0x0A6CF0)
    static let accentSoft = Color(hex: 0xE8F2FF)
    static let accentDark = Color(hex: 0x0A46A8)
    static let bg = Color(hex: 0xF3F6FB)
    static let surface = Color.white
    static let border = Color(hex: 0xE1E7F0)
    static let text2 = Color(hex: 0x53607A)
    static let muted = Color(hex: 0x8B96AB)
    static let success = Color(hex: 0x16A34A)
    static let warning = Color(hex: 0xC27803)
    static let error = Color(hex: 0xDC2626)
    static let idle = Color(hex: 0xD0D6E0)
}

/// The tools live for the whole app, so a tool keeps working while you use another one.
@MainActor
enum Tools {
    static let enhancer = EnhancerTool()
    static let fetchByCode = FetchTool(key: "fetch_by_code", title: "Fetch by Code")
    static let fetchEnhance = FetchEnhanceTool()
    static let reactionList = ReactionListTool()
    static let modelDetect = ModelDetectTool()
    static let pathao = PathaoTool()
}

@MainActor @Observable
final class Nav {
    static let shared = Nav()

    enum Screen: CaseIterable, Identifiable {
        case enhancer, fetchCode, fetchEnhance, reactionList, modelDetect, pathao, whatsapp, dashboard, plans, shop

        var id: Self { self }

        var title: String {
            switch self {
            case .enhancer: return "Photo Enhancer"
            case .fetchCode: return "Fetch by Code"
            case .fetchEnhance: return "Fetch & Enhance"
            case .reactionList: return "Reaction List"
            case .modelDetect: return "Model Detect"
            case .pathao: return "Pathao Auto-fill"
            case .whatsapp: return "WhatsApp"
            case .dashboard: return "Dashboard"
            case .plans: return "Plans"
            case .shop: return "Shop settings"
            }
        }

        var icon: String {
            switch self {
            case .enhancer: return "sparkles"
            case .fetchCode: return "photo.on.rectangle"
            case .fetchEnhance: return "wand.and.stars"
            case .reactionList: return "list.bullet"
            case .modelDetect: return "iphone"
            case .pathao: return "shippingbox"
            case .whatsapp: return "bubble.left.and.bubble.right"
            case .dashboard: return "square.grid.2x2"
            case .plans: return "crown"
            case .shop: return "storefront"
            }
        }

        var busyKey: String? {
            switch self {
            case .enhancer: return "enhancer"
            case .fetchCode: return "fetch_by_code"
            case .fetchEnhance: return "fetch_enhance"
            case .reactionList: return "reaction_list"
            case .modelDetect: return "model_detect"
            case .pathao: return "pathao"
            default: return nil
            }
        }
    }

    private(set) var screen: Screen = .dashboard

    func go(_ s: Screen) { screen = s }
}

/// Short messages at the bottom of the screen (Android's toasts).
@MainActor @Observable
final class Toaster {
    static let shared = Toaster()
    private(set) var text = ""
    @ObservationIgnored private var hide: Task<Void, Never>?

    func show(_ t: String) {
        text = t
        hide?.cancel()
        hide = Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if !Task.isCancelled { text = "" }
        }
    }
}

@MainActor
func toast(_ text: String) { Toaster.shared.show(text) }

@MainActor
func copyText(_ text: String) {
    UIPasteboard.general.string = text
    toast("Copied")
}

@MainActor
func pasteText() -> String? { UIPasteboard.general.string }

/// Saves a text file in Files › Boostimise.
@MainActor
func saveText(_ name: String, _ text: String) {
    do {
        try Storage.save("", safeFileName(name), Data(text.utf8))
        toast("Saved to Files › Boostimise › \(name)")
    } catch {
        toast("Could not save: \(error.localizedDescription)")
    }
}
