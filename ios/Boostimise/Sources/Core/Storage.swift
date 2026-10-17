import Foundation
import Photos
import UIKit

/// Settings of one tool (UserDefaults keys "<name>.<key>"), like Android's SharedPreferences.
struct Prefs {
    let name: String
    private var d: UserDefaults { .standard }
    private func k(_ key: String) -> String { "\(name).\(key)" }

    func string(_ key: String, _ def: String = "") -> String { d.string(forKey: k(key)) ?? def }
    func stringOrNil(_ key: String) -> String? { d.string(forKey: k(key)) }
    func int(_ key: String, _ def: Int) -> Int { d.object(forKey: k(key)) == nil ? def : d.integer(forKey: k(key)) }
    func bool(_ key: String, _ def: Bool) -> Bool { d.object(forKey: k(key)) == nil ? def : d.bool(forKey: k(key)) }
    func stringSet(_ key: String) -> Set<String> { Set(d.stringArray(forKey: k(key)) ?? []) }
    func data(_ key: String) -> Data? { d.data(forKey: k(key)) }

    func set(_ key: String, _ value: Any?) {
        if let v = value { d.set(v, forKey: k(key)) } else { d.removeObject(forKey: k(key)) }
    }

    func setStringSet(_ key: String, _ value: Set<String>) { d.set(Array(value).sorted(), forKey: k(key)) }
}

/// Where the tools put their files: the app's Documents folder, which the Files app shows as
/// On My iPhone › Boostimise › <tool>. Enhanced photos also go to the photo library.
enum Storage {
    static let documents: URL = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
    static let caches: URL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]

    static func prefs(_ name: String) -> Prefs { Prefs(name: name) }

    /// Documents/<sub> (made when missing).
    static func folder(_ sub: String) -> URL {
        let clean = sub.split(separator: "/").map { safeFileName(String($0)) }.filter { !$0.isEmpty }.joined(separator: "/")
        let url = clean.isEmpty ? documents : documents.appendingPathComponent(clean, isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// How a folder is found in the Files app.
    static func displayPath(_ sub: String) -> String {
        "Files › On My iPhone › Boostimise" + (sub.isEmpty ? "" : " › " + sub.split(separator: "/").joined(separator: " › "))
    }

    /// Opens the folder in the Files app.
    @MainActor
    static func openInFiles(_ sub: String) {
        let path = folder(sub).path
        if let url = URL(string: "shareddocuments://" + (path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? path)) {
            UIApplication.shared.open(url)
        }
    }

    static func namesIn(_ sub: String) -> Set<String> {
        Set((try? FileManager.default.contentsOfDirectory(atPath: folder(sub).path)) ?? [])
    }

    static func delete(_ sub: String, _ name: String) {
        try? FileManager.default.removeItem(at: folder(sub).appendingPathComponent(name))
    }

    @discardableResult
    static func save(_ sub: String, _ name: String, _ data: Data) throws -> URL {
        let url = folder(sub).appendingPathComponent(name)
        try data.write(to: url, options: .atomic)
        return url
    }

    /// Moves a downloaded file into Documents/<sub>/<name> (replacing one of that name).
    @discardableResult
    static func move(_ file: URL, _ sub: String, _ name: String) throws -> URL {
        let url = folder(sub).appendingPathComponent(name)
        try? FileManager.default.removeItem(at: url)
        try FileManager.default.moveItem(at: file, to: url)
        return url
    }

    static func cacheFolder(_ dir: String) -> URL {
        let url = caches.appendingPathComponent(dir, isDirectory: true)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// A free file name in a cache folder ("photo.jpg", "photo (2).jpg", …).
    static func freeCacheFile(_ dir: String, _ name0: String) -> URL {
        let folder = cacheFolder(dir)
        let name = safeFileName(name0).ifEmpty("photo.jpg")
        var file = folder.appendingPathComponent(name)
        var n = 2
        let stem = (name as NSString).deletingPathExtension
        let ext = (name as NSString).pathExtension.ifEmpty("jpg")
        while FileManager.default.fileExists(atPath: file.path) {
            file = folder.appendingPathComponent("\(stem) (\(n)).\(ext)")
            n += 1
        }
        return file
    }

    /// Reads a text file picked in the Files app (its access is only granted while reading).
    static func readText(_ url: URL) throws -> String {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: url)
        var text = String(data: data, encoding: .utf8) ?? String(decoding: data, as: UTF8.self)
        if text.hasPrefix("\u{feff}") { text.removeFirst() }
        return text
    }

    static func writeText(_ url: URL, _ text: String) throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        try Data(text.utf8).write(to: url, options: .atomic)
    }

    /// Adds a photo file to the photo library ("Boostimise" album not needed: Recents shows it).
    static func saveToPhotos(_ file: URL) async -> Bool {
        let status = await PHPhotoLibrary.requestAuthorization(for: .addOnly)
        guard status == .authorized || status == .limited else { return false }
        do {
            try await PHPhotoLibrary.shared().performChanges {
                PHAssetCreationRequest.forAsset().addResource(with: .photo, fileURL: file, options: nil)
            }
            return true
        } catch {
            return false
        }
    }
}
