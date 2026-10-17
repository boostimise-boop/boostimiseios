import Foundation

/// A request that failed: status 0 = no answer (offline, timeout), else the HTTP status.
/// `code` / `message` come from the JSON error body when there is one.
struct HttpError: LocalizedError {
    let status: Int
    let code: String
    let message: String
    var body: Any? = nil
    var errorDescription: String? { message }
}

enum Http {
    static let session: URLSession = {
        let c = URLSessionConfiguration.default
        c.timeoutIntervalForRequest = 120
        c.timeoutIntervalForResource = 60 * 60
        return URLSession(configuration: c)
    }()

    /// Reads the error out of the body: {"error": {"code", "message"}} (enhancement server),
    /// {"code", "message"} (Supabase REST), {"error", "error_description"} / {"msg"} (Supabase Auth).
    static func errorFrom(_ status: Int, _ text: String?) -> HttpError {
        var code = "http_error"
        var message = "Request failed (\(status))"
        let body = JSONTools.parse(text)
        if let b = body as? JSON {
            if let err = b.obj("error") {
                code = err.str("code").ifEmpty(code)
                message = err.str("message").ifEmpty(message)
            } else {
                let errStr = (b["error"] as? String) ?? ""
                code = b.str("error_code").ifEmpty(b.str("code")).ifEmpty(errStr).ifEmpty(code)
                message = b.str("message").ifEmpty(b.str("msg")).ifEmpty(b.str("error_description"))
                    .ifEmpty(errStr).ifEmpty(message)
            }
        }
        return HttpError(status: status, code: code, message: message, body: body)
    }

    static func prepare(_ request: URLRequest) -> URLRequest {
        var r = request
        if r.value(forHTTPHeaderField: "User-Agent") == nil { r.setValue(Config.userAgent, forHTTPHeaderField: "User-Agent") }
        if r.value(forHTTPHeaderField: "Accept") == nil { r.setValue("application/json, */*", forHTTPHeaderField: "Accept") }
        return r
    }

    static func request(_ url: String, method: String = "GET", headers: [String: String] = [:], json: Any? = nil) -> URLRequest {
        var r = URLRequest(url: URL(string: url)!)
        r.httpMethod = method
        for (k, v) in headers { r.setValue(v, forHTTPHeaderField: k) }
        if let body = json {
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
            r.httpBody = JSONTools.string(body).data(using: .utf8)
        }
        return r
    }

    /// Turns URLSession's errors into HttpError(0) (cancellation stays cancellation).
    static func transport(_ error: Error) -> Error {
        if error is CancellationError { return error }
        if let u = error as? URLError, u.code == .cancelled { return CancellationError() }
        if error is HttpError { return error }
        return HttpError(status: 0, code: "network", message: error.localizedDescription)
    }

    /// One request; returns status and body text, throws HttpError for a non-2xx answer or no answer.
    @discardableResult
    static func send(_ request: URLRequest) async throws -> (Int, String) {
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: prepare(request))
        } catch {
            throw transport(error)
        }
        let (data, resp) = result
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let text = String(data: data, encoding: .utf8) ?? ""
        if !(200..<300).contains(status) { throw errorFrom(status, text) }
        return (status, text)
    }

    static func getJson(_ url: String, headers: [String: String] = [:]) async throws -> Any? {
        JSONTools.parse(try await send(request(url, headers: headers)).1)
    }

    static func bytes(_ url: String) async throws -> Data {
        let result: (Data, URLResponse)
        do {
            result = try await session.data(for: prepare(request(url)))
        } catch {
            throw transport(error)
        }
        let (data, resp) = result
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if !(200..<300).contains(status) { throw errorFrom(status, String(data: data, encoding: .utf8)) }
        return data
    }

    /// Uploads a body with progress (0...1).
    static func upload(_ request: URLRequest, body: Data, onProgress: ((Double) -> Void)?) async throws -> (Int, String) {
        let delegate = UploadProgress(onProgress)
        let result: (Data, URLResponse)
        do {
            result = try await session.upload(for: prepare(request), from: body, delegate: delegate)
        } catch {
            throw transport(error)
        }
        let (data, resp) = result
        let status = (resp as? HTTPURLResponse)?.statusCode ?? 0
        let text = String(data: data, encoding: .utf8) ?? ""
        if !(200..<300).contains(status) { throw errorFrom(status, text) }
        return (status, text)
    }
}

final class UploadProgress: NSObject, URLSessionTaskDelegate {
    private let onProgress: ((Double) -> Void)?
    private var last = Date.distantPast

    init(_ onProgress: ((Double) -> Void)?) {
        self.onProgress = onProgress
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didSendBodyData bytesSent: Int64,
                    totalBytesSent: Int64, totalBytesExpectedToSend: Int64) {
        guard let cb = onProgress, totalBytesExpectedToSend > 0 else { return }
        let now = Date()
        if now.timeIntervalSince(last) < 0.08 && totalBytesSent < totalBytesExpectedToSend { return }
        last = now
        let f = Double(totalBytesSent) / Double(totalBytesExpectedToSend)
        DispatchQueue.main.async { cb(f) }
    }
}

/// multipart/form-data body.
struct Multipart {
    let boundary = "Boundary-\(UUID().uuidString)"
    private(set) var data = Data()

    var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    mutating func field(_ name: String, _ value: String) {
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n")
    }

    mutating func file(_ name: String, filename: String, type: String, content: Data) {
        let safe = filename.replacingOccurrences(of: "\"", with: "'")
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"; filename=\"\(safe)\"\r\nContent-Type: \(type)\r\n\r\n")
        data.append(content)
        append("\r\n")
    }

    mutating func finish() -> Data {
        append("--\(boundary)--\r\n")
        return data
    }

    private mutating func append(_ s: String) {
        data.append(s.data(using: .utf8)!)
    }
}

extension String {
    func ifEmpty(_ other: @autoclosure () -> String) -> String { isEmpty ? other() : self }
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
