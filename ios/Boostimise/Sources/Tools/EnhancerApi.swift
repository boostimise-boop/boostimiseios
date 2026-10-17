import Foundation

struct EnhanceSettings: Equatable {
    var mode = "auto"          // auto | face | restore | upscale
    var scale = 2              // 2 | 4
    var strength = 0.8
    var faceEnhance = true
    var faceModel = "auto"     // auto | codeformer | gfpgan | compare
    var fidelity: Double? = nil   // nil = adaptive
    var denoise: Double? = nil    // nil = automatic
    var sharpen: Double? = nil    // nil = automatic
    var colorEnhance = true
    var quality = 95
}

/// Settings each mode starts from (the user can still change everything).
func presetFor(_ mode: String, _ s: EnhanceSettings) -> EnhanceSettings {
    var o = s
    o.mode = mode
    switch mode {
    case "upscale":
        o.faceEnhance = false; o.colorEnhance = false; o.strength = 1.0; o.sharpen = nil; o.denoise = nil
    case "face":
        o.faceEnhance = true; o.colorEnhance = false; o.strength = 0.8
    case "restore":
        o.faceEnhance = true; o.colorEnhance = true; o.strength = 0.9
    default:
        o.faceEnhance = true; o.colorEnhance = true; o.strength = 0.8
    }
    return o
}

/// Client for the AI enhancement server (bmapi.boostimise.com) - tools/photo_enhancer/api.py.
enum EnhancerApi {
    static let base = Config.apiBase
    static let accepted = ["jpg", "jpeg", "png", "webp"]

    static func contentType(_ name: String) -> String {
        switch (name as NSString).pathExtension.lowercased() {
        case "png": return "image/png"
        case "webp": return "image/webp"
        default: return "image/jpeg"
        }
    }

    static func health() async throws -> JSON? {
        try await Http.getJson("\(base)/api/health") as? JSON
    }

    static func analyze(_ file: URL, _ name: String, scale: Int) async throws -> JSON? {
        var m = Multipart()
        m.field("scale", String(scale))
        m.file("image", filename: name, type: contentType(name), content: try Data(contentsOf: file))
        var req = Http.request("\(base)/api/analyze", method: "POST")
        req.setValue(m.contentType, forHTTPHeaderField: "Content-Type")
        let body = m.finish()
        return JSONTools.parse(try await Http.upload(req, body: body, onProgress: nil).1) as? JSON
    }

    static func jobs(_ ids: [String]) async throws -> JSON? {
        let q = ids.joined(separator: ",").addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? ""
        return try await Http.getJson("\(base)/api/jobs?ids=\(q)") as? JSON
    }

    static func cancel(_ jobId: String) async {
        _ = try? await Http.send(Http.request("\(base)/api/job/\(jobId)", method: "DELETE"))
    }

    static func resultURL(_ job: JSON, _ kind: String) -> String {
        base + (job.obj("urls")?.str(kind) ?? "")
    }

    private static func num(_ v: Double) -> String { String(format: "%.2f", locale: Locale(identifier: "en_US_POSIX"), v) }

    /// Uploads the photo with its settings; returns the job id.
    static func submit(_ file: URL, _ name: String, _ s: EnhanceSettings, onUpload: @escaping (Double) -> Void) async throws -> String {
        var m = Multipart()
        m.field("mode", s.mode)
        m.field("scale", String(s.scale))
        m.field("strength", num(s.strength))
        m.field("face_enhance", s.faceEnhance ? "true" : "false")
        m.field("face_model", s.faceModel)
        m.field("color_enhance", s.colorEnhance ? "true" : "false")
        m.field("quality", String(s.quality))
        // omitted fields = "automatic" on the server
        if let f = s.fidelity { m.field("fidelity", num(f)) }
        if let d = s.denoise { m.field("denoise", num(d)) }
        if let sh = s.sharpen { m.field("sharpen", num(sh)) }
        m.file("image", filename: name, type: contentType(name), content: try Data(contentsOf: file))
        var req = Http.request("\(base)/api/enhance", method: "POST")
        req.setValue(m.contentType, forHTTPHeaderField: "Content-Type")
        let body = m.finish()
        let (status, text) = try await Http.upload(req, body: body, onProgress: onUpload)
        let data = (JSONTools.parse(text) as? JSON) ?? [:]
        if status != 202 || data.str("job_id").isEmpty {
            throw HttpError(status: status, code: "http_error", message: "Upload failed (\(status))", body: data)
        }
        return data.str("job_id")
    }

    /// Downloads a GET (or POST form) into a temporary file; returns it and the file name the server gives.
    static func download(_ url: String, fallback: String, form: [String: String]? = nil) async throws -> (URL, String) {
        var req = Http.request(url, method: form == nil ? "GET" : "POST")
        if let form {
            req.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
            let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
            req.httpBody = form.map { "\($0.key)=\($0.value.addingPercentEncoding(withAllowedCharacters: allowed) ?? "")" }
                .joined(separator: "&").data(using: .utf8)
        }
        let result: (URL, URLResponse)
        do {
            result = try await Http.session.download(for: Http.prepare(req))
        } catch {
            throw Http.transport(error)
        }
        let (tmp, resp) = result
        let http = resp as? HTTPURLResponse
        let status = http?.statusCode ?? 0
        if !(200..<300).contains(status) {
            throw Http.errorFrom(status, try? String(contentsOf: tmp, encoding: .utf8))
        }
        let cd = http?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        var name = fallback
        if let m = Rx(#"filename\*\s*=\s*[^']*''([^;]+)"#, ignoreCase: true).find(cd) {
            name = m.group(1).removingPercentEncoding ?? m.group(1)
        } else if let m = Rx(#"filename\s*=\s*"?([^";]+)"?"#, ignoreCase: true).find(cd) {
            name = m.group(1)
        }
        // the temporary file is deleted when this returns: keep a copy
        let keep = Storage.cacheFolder("downloads").appendingPathComponent(UUID().uuidString)
        try FileManager.default.moveItem(at: tmp, to: keep)
        return (keep, safeFileName(name).ifEmpty(fallback))
    }
}
