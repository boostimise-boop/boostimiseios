import SwiftUI

// The look of the Windows app's tool pages (core/look.py) on the iPhone (the Android app's Look.kt):
//   ControlCard   the navy card: a progress ring, what is happening, the counts and the buttons
//   HeroButton    a button on it (start: sky blue; the others see-through white)
//   StatusRow     a result: its status square, the code as a chip, the name and the status in its colour
//   IconSquare    a card's icon on a soft square (Card in Widgets.swift draws the white cards)

/// An icon square's colours: soft background and icon.
enum Tint {
    case accent, success, warning, neutral

    var bg: Color {
        switch self {
        case .accent: return Brand.accentSoft
        case .success: return Brand.successSoft
        case .warning: return Brand.warningSoft
        case .neutral: return Brand.neutralSoft
        }
    }

    var fg: Color {
        switch self {
        case .accent: return Brand.accent
        case .success: return Brand.success
        case .warning: return Brand.warning
        case .neutral: return Brand.navy
        }
    }
}

struct IconSquare: View {
    let icon: String
    let tint: Tint
    var size: CGFloat = 36

    var body: some View {
        Image(systemName: icon)
            .font(.system(size: size * 0.42, weight: .semibold))
            .foregroundStyle(tint.fg)
            .frame(width: size, height: size)
            .background(tint.bg, in: RoundedRectangle(cornerRadius: size * 0.3))
    }
}

/// A result's kind ("" | working | ok | none | error | skipped): soft background, text, strong colour and icon.
struct KindLook {
    let soft: Color
    let text: Color
    let strong: Color
    let icon: String
}

func kindLook(_ kind: String) -> KindLook {
    switch kind {
    case "ok": return KindLook(soft: Brand.successSoft, text: Brand.successDark, strong: Brand.success, icon: "checkmark")
    case "working": return KindLook(soft: Brand.accentSoft, text: Brand.accentDark, strong: Brand.accent, icon: "wand.and.stars")
    case "none": return KindLook(soft: Brand.warningSoft, text: Brand.warningDark, strong: Brand.warning, icon: "exclamationmark.circle")
    case "error": return KindLook(soft: Brand.errorSoft, text: Brand.errorDark, strong: Brand.error, icon: "exclamationmark.circle")
    case "skipped": return KindLook(soft: Brand.neutralSoft, text: Brand.text2, strong: Brand.muted, icon: "minus")
    default: return KindLook(soft: Brand.bg, text: Brand.text2, strong: Brand.muted, icon: "clock")
    }
}

/// The kinds of a finished code (the ring's "done").
let DONE_KINDS: Set<String> = ["ok", "none", "error", "skipped"]

func plural(_ n: Int, _ word: String) -> String { "\(n) \(word)" + (n == 1 ? "" : "s") }

/// The control card's top line for a WhatsApp tool, and its colour.
@MainActor
func waKicker(_ running: Bool) -> (String, Color) {
    if running { return ("WORKING", Brand.live) }
    switch WaEngine.shared.phase {
    case .ready: return ("CONNECTED", Brand.sky)
    case .loading: return ("CONNECTING", Brand.sky)
    case .qr: return ("SCAN THE QR CODE", Brand.amberOnNavy)
    case .error: return ("WHATSAPP ERROR", Brand.redOnNavy)
    case .off: return ("NOT CONNECTED", Brand.onNavy)
    }
}

/// The ring: the share drawn (nil: busy, a quarter), the big text and the unit under it.
struct RingState {
    let share: Double?
    let text: String
    let unit: String
}

/// "4/6 codes"; "0" before there is anything; busy while running without a total yet.
func ringOf(_ done: Int, _ total: Int, _ unit: String, running: Bool = false) -> RingState {
    if total > 0 { return RingState(share: Double(done) / Double(total), text: "\(done)/\(total)", unit: unit) }
    if running { return RingState(share: nil, text: "…", unit: unit) }
    return RingState(share: 0, text: "0", unit: unit)
}

/// One count of the control card: a big number over a coloured dot and its name.
struct HeroCount: Identifiable {
    let label: String
    let value: Int
    let color: Color
    var id: String { label }
}

/// The navy control card. Its counts sit beside the status when the card is wide enough, else under it
/// in a grid as even as the width allows; the buttons wrap onto more lines when they don't fit.
struct ControlCard<Buttons: View>: View {
    let kicker: (String, Color)
    let title: String
    var sub = ""
    let ring: RingState
    var counts: [HeroCount] = []
    @ViewBuilder var buttons: Buttons

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if counts.isEmpty {
                status
            } else {
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 16) {
                        status
                        HStack(spacing: 8) { ForEach(counts) { CountTile(c: $0, fill: false) } }.fixedSize()
                    }
                    VStack(alignment: .leading, spacing: 16) {
                        status
                        EvenGrid().callAsFunction { ForEach(counts) { CountTile(c: $0) } }
                    }
                }
            }
            Flow(spacing: 8) { buttons }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Brand.navy, in: RoundedRectangle(cornerRadius: 22))
    }

    private var status: some View {
        HStack(spacing: 14) {
            RingView(ring: ring)
            VStack(alignment: .leading, spacing: 2) {
                Text(kicker.0).font(.system(size: 11, weight: .heavy)).kerning(1).foregroundStyle(kicker.1)
                Text(title).font(.system(size: 17, weight: .bold)).foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
                if !sub.isEmpty {
                    Text(sub).font(.system(size: 12.5)).foregroundStyle(Brand.onNavy).fixedSize(horizontal: false, vertical: true)
                }
            }
            // its ideal width decides whether the counts fit beside it (ViewThatFits)
            .frame(minWidth: 140, idealWidth: 240, maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct RingView: View {
    let ring: RingState

    var body: some View {
        let share = CGFloat(min(max(ring.share ?? 0.25, 0), 1))
        ZStack {
            Circle().stroke(Color.white.opacity(0.12), lineWidth: 7)
            if share > 0 {
                Circle().trim(from: 0, to: share)
                    .stroke(Brand.sky, style: StrokeStyle(lineWidth: 7, lineCap: .round))
                    .rotationEffect(.degrees(-90))
            }
            VStack(spacing: 0) {
                Text(ring.text).font(.system(size: ring.text.count <= 5 ? 16 : 12, weight: .heavy)).foregroundStyle(.white)
                    .lineLimit(1)
                Text(ring.unit).font(.system(size: 9.5)).foregroundStyle(Brand.onNavy).lineLimit(1)
            }
        }
        .padding(4.5)
        .frame(width: 64, height: 64)
    }
}

private struct CountTile: View {
    let c: HeroCount
    /// In the grid the tile fills its cell; beside the status it is as wide as its name (96 pt at least).
    var fill = true

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("\(c.value)").font(.system(size: 20, weight: .heavy)).foregroundStyle(.white)
            HStack(spacing: 5) {
                Circle().fill(c.color).frame(width: 7, height: 7)
                Text(c.label).font(.system(size: 12)).foregroundStyle(Brand.onNavy).lineLimit(1)
            }
        }
        .padding(.horizontal, 9)
        .padding(.vertical, 8)
        .frame(minWidth: fill ? nil : 96, maxWidth: fill ? .infinity : nil, alignment: .leading)
        .background(Color.white.opacity(0.07), in: RoundedRectangle(cornerRadius: 14))
    }
}

/// Tiles in rows of equal width: as many in a row as fit (each as wide as the widest tile needs, `minTile`
/// at least), the rows as even as they can be (4 tiles that don't fit in one row become 2 × 2, not 3 + 1).
struct EvenGrid: Layout {
    var minTile: CGFloat = 84
    var spacing: CGFloat = 8

    private func perRow(_ subviews: Subviews, _ width: CGFloat) -> Int {
        let n = subviews.count
        let need = max(minTile, subviews.map { $0.sizeThatFits(.unspecified).width }.max() ?? 0)
        let fit = max(1, Int((width + spacing) / (need + spacing)))
        if fit >= n { return max(n, 1) }
        let rows = (n + fit - 1) / fit
        return (n + rows - 1) / rows
    }

    private func tile(_ subviews: Subviews, _ width: CGFloat) -> (per: Int, width: CGFloat, height: CGFloat) {
        let per = perRow(subviews, width)
        let w = (width - CGFloat(per - 1) * spacing) / CGFloat(per)
        let h = subviews.map { $0.sizeThatFits(ProposedViewSize(width: w, height: nil)).height }.max() ?? 0
        return (per, w, h)
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? CGFloat(subviews.count) * (minTile + spacing) - spacing
        let t = tile(subviews, width)
        let rows = (subviews.count + t.per - 1) / t.per
        return CGSize(width: width, height: CGFloat(rows) * t.height + CGFloat(max(0, rows - 1)) * spacing)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let t = tile(subviews, bounds.width)
        for (i, s) in subviews.enumerated() {
            let x = bounds.minX + CGFloat(i % t.per) * (t.width + spacing)
            let y = bounds.minY + CGFloat(i / t.per) * (t.height + spacing)
            s.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(width: t.width, height: t.height))
        }
    }
}

/// A button on the control card: `primary` is sky blue (Start), `outline` a white outline (Stop).
struct HeroButton: View {
    let title: String
    var icon: String? = nil
    var primary = false
    var outline = false
    var enabled = true
    let action: () -> Void

    init(_ title: String, icon: String? = nil, primary: Bool = false, outline: Bool = false, enabled: Bool = true,
         action: @escaping () -> Void) {
        self.title = title
        self.icon = icon
        self.primary = primary
        self.outline = outline
        self.enabled = enabled
        self.action = action
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let icon { Image(systemName: icon).font(.system(size: 13, weight: .bold)) }
                Text(title).font(.system(size: 14, weight: primary ? .heavy : .bold)).lineLimit(1)
            }
            .padding(.horizontal, 16)
            .frame(minHeight: 40)
            .foregroundStyle(enabled ? (primary ? Brand.navy : Color.white) : Color(hex: 0x6F86AB))
            .background(background, in: RoundedRectangle(cornerRadius: 12))
            .overlay {
                if outline { RoundedRectangle(cornerRadius: 12).stroke(Color.white.opacity(enabled ? 0.3 : 0.14)) }
            }
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }

    private var background: Color {
        if primary { return enabled ? Brand.sky : Color(hex: 0x1D4F86) }
        if outline { return .clear }
        return Color.white.opacity(enabled ? 0.10 : 0.05)
    }
}

/// A code as a small grey chip in a monospaced font.
struct CodeChip: View {
    let code: String

    var body: some View {
        Text(code).font(.system(size: 12, weight: .bold, design: .monospaced)).foregroundStyle(Brand.accentDark)
            .lineLimit(1)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Brand.track, in: RoundedRectangle(cornerRadius: 6))
            .fixedSize()
    }
}

/// A result as a row: the status square of its `kind` (`badge`, an emoji, in place of the icon), the code
/// chip and the name, the status in the kind's colour, then any `more` lines and the `trailing` action.
struct StatusRow<Trailing: View>: View {
    let kind: String
    let code: String
    var name = ""
    var status = ""
    var badge: String? = nil
    var more: [String] = []
    @ViewBuilder var trailing: Trailing

    var body: some View {
        let k = kindLook(kind)
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                Group {
                    if let badge {
                        // two emojis or more get smaller to fit the square
                        Text(badge).font(.system(size: badge.count > 1 ? 11 : 15)).lineLimit(1)
                    } else {
                        Image(systemName: k.icon).font(.system(size: 15, weight: .semibold)).foregroundStyle(k.strong)
                    }
                }
                .frame(width: 36, height: 36)
                .background(k.soft, in: RoundedRectangle(cornerRadius: 11))
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 8) {
                        if !code.isEmpty { CodeChip(code: code) }
                        if !name.isEmpty {
                            Text(name).font(.system(size: 14, weight: .semibold)).foregroundStyle(Brand.navy).lineLimit(2)
                        }
                    }
                    if !status.isEmpty {
                        Text(status).font(.system(size: 12.5, weight: .medium)).foregroundStyle(k.text)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    ForEach(Array(more.filter { !$0.isEmpty }.enumerated()), id: \.offset) { _, m in
                        Text(m).font(.system(size: 12)).foregroundStyle(Brand.text2).fixedSize(horizontal: false, vertical: true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                trailing
            }
            .padding(.vertical, 8)
            Rectangle().fill(Brand.neutralSoft).frame(height: 1)
        }
    }
}

extension StatusRow where Trailing == EmptyView {
    init(kind: String, code: String, name: String = "", status: String = "", badge: String? = nil, more: [String] = []) {
        self.init(kind: kind, code: code, name: name, status: status, badge: badge, more: more) { EmptyView() }
    }
}
