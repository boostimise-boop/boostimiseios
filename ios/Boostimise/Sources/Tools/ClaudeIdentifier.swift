import Foundation

/// The API refused the key or the model: identification is turned off for the rest of the run.
struct ClaudeKeyError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Identifies the phone model shown in photos with Claude (tools/model_detect/identifier.py), over
/// the Messages API's HTTP interface (there is no Anthropic SDK for Swift).
final class ClaudeIdentifier {
    static let defaultModel = "claude-opus-5-5"
    static let models = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
    static let efforts = ["low", "medium", "high", "xhigh", "max"]

    private let apiKey: String
    private let model: String
    private let effort: String
    // Server-side refusal fallback: a declined request is re-run on a suitable model (not on Haiku).
    private var useFallbacks: Bool

    init(apiKey: String, model: String = ClaudeIdentifier.defaultModel, effort: String = "high") {
        self.apiKey = apiKey
        self.model = model
        self.effort = effort
        useFallbacks = !model.hasPrefix("claude-haiku")
    }

    private static let systemPrompt = """
You identify smartphone models from photos for a mobile phone cover (case) shop in Bangladesh. The shop's WhatsApp groups post a product code together with photos of a phone or of its cover. Your answer is written next to that code in the shop's product list.

How to identify:
- Readable text wins: a model name printed on the phone, box, cover packaging or a screenshot, or written in the chat text that came with the photos. A model number (About-phone screenshot, box label, the small print on the phone's back) counts too: convert it to the name, e.g. SM-A065F is samsung a06.
- A camera watermark ("Shot on vivo V30", "AI QUAD CAMERA") names the phone that took the picture, not always the customer's phone: use it only when nothing else shows the model.
- Many photos are just the customer's pictures to print on the cover (people, places): ignore those.
- Otherwise use the rear camera module (number, size and arrangement of lenses, flash position, module shape), the phone's outline, buttons and ports, and the cutouts of the cover - a cover's cutouts show the camera layout of the phone it fits.
- If several models share the same design, pick the most likely one and lower the confidence.

Format of "model": lowercase, the short name used in Bangladeshi mobile shops, brand + model + variant. Examples: samsung a70, samsung a52s 5g, samsung s23 ultra, iphone xs max, iphone 13 pro, redmi note 7, redmi note 13 pro 5g, poco x3 pro, realme c55, oppo a57, vivo y21, infinix hot 40i, tecno spark 20, oneplus nord ce 3 lite, google pixel 7. Leave out "galaxy", "apple" and "xiaomi" when the series name is enough (redmi, poco). Add "5g" only when the photos or text show it is the 5G version. Never use the "/" character. If the photos do not show a phone or a phone cover, answer "unknown" with low confidence.
"reason" is a short note (at most 15 words) on what gave the model away.
"""

    private static let schema: JSON = [
        "type": "object",
        "properties": [
            "model": ["type": "string"],
            "confidence": ["type": "string", "enum": ["high", "medium", "low"]] as JSON,
            "reason": ["type": "string"],
        ] as JSON,
        "required": ["model", "confidence", "reason"],
        "additionalProperties": false,
    ]

    private func request(_ content: [JSON]) -> URLRequest {
        var body: JSON = [
            "model": model,
            "max_tokens": 16000,
            "system": Self.systemPrompt,
            "messages": [["role": "user", "content": content] as JSON],
            "output_config": ["effort": effort, "format": ["type": "json_schema", "schema": Self.schema] as JSON] as JSON,
        ]
        var headers = ["x-api-key": apiKey, "anthropic-version": "2023-06-01"]
        if useFallbacks {
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            body["fallbacks"] = "default"
        }
        var r = Http.request("https://api.anthropic.com/v1/messages", method: "POST", headers: headers, json: body)
        r.timeoutInterval = 300
        return r
    }

    /// Sends the request, retrying rate limits, overload and network errors a few times.
    private func send(_ content: [JSON]) async throws -> JSON {
        var attempt = 0
        while true {
            do {
                let (_, text) = try await Http.send(request(content))
                return (JSONTools.parse(text) as? JSON) ?? [:]
            } catch let e as HttpError {
                if [401, 403, 404].contains(e.status) { throw ClaudeKeyError(message: e.message) }
                if e.status == 400 && useFallbacks && e.message.lowercased().contains("fallback") {
                    useFallbacks = false
                    continue
                }
                let retryable = e.status == 0 || e.status == 408 || e.status == 409 || e.status == 429 || e.status >= 500
                if !retryable || attempt >= 4 { throw e }
                attempt += 1
                try await Task.sleep(nanoseconds: UInt64(pow(2.0, Double(attempt))) * 1_000_000_000)
            }
        }
    }

    /// images: JPEG bytes.
    func identify(code: String, images: [Data], context: String) async throws -> Identification {
        var content: [JSON] = []
        for (i, img) in images.enumerated() {
            content.append(["type": "text", "text": "Photo \(i + 1):"])
            content.append(["type": "image",
                            "source": ["type": "base64", "media_type": "image/jpeg", "data": img.base64EncodedString()] as JSON])
        }
        let ctx = context.trimmed.isEmpty ? "(none)" : context
        content.append(["type": "text", "text": "Product code: \(code)\n\nChat text posted with these photos (may be empty or unrelated):\n" +
            "\(ctx)\n\nWhich phone model is this?"])

        let resp = try await send(content)
        let stop = resp.str("stop_reason")
        if stop == "refusal" { return Identification(model: "unknown", confidence: "low", reason: "request declined by the safety filter") }
        let text = resp.objs("content").first { $0.str("type") == "text" }?.str("text") ?? ""
        if stop == "max_tokens" || text.isEmpty {
            return Identification(model: "unknown", confidence: "low", reason: "no answer (stop reason: \(stop))")
        }
        let data = (JSONTools.parse(text) as? JSON) ?? [:]
        return Identification(model: cleanModelName(data.str("model")), confidence: data.str("confidence", "low").ifEmpty("low"),
                              reason: data.str("reason").trimmed)
    }
}
