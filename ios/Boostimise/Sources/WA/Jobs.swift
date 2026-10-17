import Foundation
import Observation
import UIKit
import UserNotifications

/// Which tools are working right now (a green dot on their menu item, like the Windows app's
/// sidebar). iOS has no foreground service: while a tool works the screen stays on, and if you
/// switch to another app a notification reminds you to come back (iOS pauses apps in the background).
@MainActor @Observable
final class Jobs {
    static let shared = Jobs()

    private(set) var busy: [String: String] = [:]   // tool key -> what it is doing
    @ObservationIgnored private var background: UIBackgroundTaskIdentifier = .invalid
    @ObservationIgnored private var askedNotifications = false

    private init() {}

    func begin(_ tool: String, _ what: String) {
        busy[tool] = what
        update()
        if !askedNotifications {
            askedNotifications = true
            UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
    }

    func progress(_ tool: String, _ what: String) {
        if busy[tool] != nil { busy[tool] = what }
    }

    func end(_ tool: String) {
        busy[tool] = nil
        update()
    }

    func isBusy(_ tool: String) -> Bool { busy[tool] != nil }

    private func update() {
        UIApplication.shared.isIdleTimerDisabled = !busy.isEmpty
        if busy.isEmpty { endBackground() }
    }

    /// The app went to the background: ask iOS for the little time it gives, and say why to come back.
    func enteredBackground() {
        guard !busy.isEmpty else { return }
        if background == .invalid {
            background = UIApplication.shared.beginBackgroundTask(withName: "tools") { [weak self] in
                Task { @MainActor in self?.endBackground() }
            }
        }
        let content = UNMutableNotificationContent()
        content.title = "Boostimise is paused"
        content.body = (busy.values.first ?? "A tool is working") + " - open Boostimise so it can finish."
        let req = UNNotificationRequest(identifier: "tools-paused", content: content,
                                        trigger: UNTimeIntervalNotificationTrigger(timeInterval: 20, repeats: false))
        UNUserNotificationCenter.current().add(req)
    }

    func enteredForeground() {
        UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ["tools-paused"])
        UNUserNotificationCenter.current().removeDeliveredNotifications(withIdentifiers: ["tools-paused"])
        endBackground()
    }

    private func endBackground() {
        if background != .invalid {
            UIApplication.shared.endBackgroundTask(background)
            background = .invalid
        }
    }
}
