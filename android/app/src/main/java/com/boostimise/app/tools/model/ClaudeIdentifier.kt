package com.boostimise.app.tools.model

import android.util.Base64
import com.anthropic.client.AnthropicClient
import com.anthropic.client.okhttp.AnthropicOkHttpClient
import com.anthropic.core.JsonValue
import com.anthropic.errors.NotFoundException
import com.anthropic.errors.PermissionDeniedException
import com.anthropic.errors.UnauthorizedException
import com.anthropic.models.messages.Base64ImageSource
import com.anthropic.models.messages.ContentBlockParam
import com.anthropic.models.messages.ImageBlockParam
import com.anthropic.models.messages.JsonOutputFormat
import com.anthropic.models.messages.MessageCreateParams
import com.anthropic.models.messages.OutputConfig
import com.anthropic.models.messages.StopReason
import com.anthropic.models.messages.TextBlockParam
import com.boostimise.app.core.Http
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.time.Duration

/** The API refused the key or the model: identification is turned off for the rest of the run. */
class ClaudeKeyError(message: String) : Exception(message)

/** Identifies the phone model shown in photos with Claude (tools/model_detect/identifier.py). */
class ClaudeIdentifier(apiKey: String, private val model: String = DEFAULT_MODEL, private val effort: String = "high") {
    private val client: AnthropicClient = AnthropicOkHttpClient.builder()
        .apiKey(apiKey)
        .maxRetries(4)
        .timeout(Duration.ofSeconds(300))
        .build()

    // Server-side refusal fallback: a declined request is re-run on a suitable model (not on Haiku).
    private var useFallbacks = !model.startsWith("claude-haiku")

    companion object {
        const val DEFAULT_MODEL = "claude-opus-5-5"
        val MODELS = listOf("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
        val EFFORTS = listOf("low", "medium", "high", "xhigh", "max")

        private const val SYSTEM_PROMPT = """You identify smartphone models from photos for a mobile phone cover (case) shop in Bangladesh. The shop's WhatsApp groups post a product code together with photos of a phone or of its cover. Your answer is written next to that code in the shop's product list.

How to identify:
- Readable text wins: a model name printed on the phone, box, cover packaging or a screenshot, or written in the chat text that came with the photos. A model number (About-phone screenshot, box label, the small print on the phone's back) counts too: convert it to the name, e.g. SM-A065F is samsung a06.
- A camera watermark ("Shot on vivo V30", "AI QUAD CAMERA") names the phone that took the picture, not always the customer's phone: use it only when nothing else shows the model.
- Many photos are just the customer's pictures to print on the cover (people, places): ignore those.
- Otherwise use the rear camera module (number, size and arrangement of lenses, flash position, module shape), the phone's outline, buttons and ports, and the cutouts of the cover - a cover's cutouts show the camera layout of the phone it fits.
- If several models share the same design, pick the most likely one and lower the confidence.

Format of "model": lowercase, the short name used in Bangladeshi mobile shops, brand + model + variant. Examples: samsung a70, samsung a52s 5g, samsung s23 ultra, iphone xs max, iphone 13 pro, redmi note 7, redmi note 13 pro 5g, poco x3 pro, realme c55, oppo a57, vivo y21, infinix hot 40i, tecno spark 20, oneplus nord ce 3 lite, google pixel 7. Leave out "galaxy", "apple" and "xiaomi" when the series name is enough (redmi, poco). Add "5g" only when the photos or text show it is the 5G version. Never use the "/" character. If the photos do not show a phone or a phone cover, answer "unknown" with low confidence.
"reason" is a short note (at most 15 words) on what gave the model away."""

        private val SCHEMA: Map<String, JsonValue> = mapOf(
            "type" to JsonValue.from("object"),
            "properties" to JsonValue.from(mapOf(
                "model" to mapOf("type" to "string"),
                "confidence" to mapOf("type" to "string", "enum" to listOf("high", "medium", "low")),
                "reason" to mapOf("type" to "string"),
            )),
            "required" to JsonValue.from(listOf("model", "confidence", "reason")),
            "additionalProperties" to JsonValue.from(false),
        )
    }

    private fun params(content: List<ContentBlockParam>): MessageCreateParams {
        val format = JsonOutputFormat.builder()
            .schema(JsonOutputFormat.Schema.builder().putAllAdditionalProperties(SCHEMA).build())
            .build()
        val b = MessageCreateParams.builder()
            .model(model)
            .maxTokens(16000L)
            .system(SYSTEM_PROMPT)
            .addUserMessageOfBlockParams(content)
            .outputConfig(OutputConfig.builder().effort(OutputConfig.Effort.of(effort)).format(format).build())
        if (useFallbacks) {
            b.putAdditionalHeader("anthropic-beta", "server-side-fallback-2026-07-01")
            b.putAdditionalBodyProperty("fallbacks", JsonValue.from("default"))
        }
        return b.build()
    }

    /** images: JPEG bytes. */
    suspend fun identify(code: String, images: List<ByteArray>, context: String): Identification = withContext(Dispatchers.IO) {
        val content = mutableListOf<ContentBlockParam>()
        images.forEachIndexed { i, img ->
            content += ContentBlockParam.ofText(TextBlockParam.builder().text("Photo ${i + 1}:").build())
            content += ContentBlockParam.ofImage(ImageBlockParam.builder().source(
                Base64ImageSource.builder()
                    .data(Base64.encodeToString(img, Base64.NO_WRAP))
                    .mediaType(Base64ImageSource.MediaType.IMAGE_JPEG)
                    .build()).build())
        }
        content += ContentBlockParam.ofText(TextBlockParam.builder().text(
            "Product code: $code\n\nChat text posted with these photos (may be empty or unrelated):\n" +
                "${context.ifBlank { "(none)" }}\n\nWhich phone model is this?").build())

        val resp = try {
            client.messages().create(params(content))
        } catch (e: UnauthorizedException) {
            throw ClaudeKeyError(e.message ?: "API key refused")
        } catch (e: PermissionDeniedException) {
            throw ClaudeKeyError(e.message ?: "permission denied")
        } catch (e: NotFoundException) {
            throw ClaudeKeyError(e.message ?: "model not found")
        } catch (e: com.anthropic.errors.BadRequestException) {
            if (useFallbacks && (e.message ?: "").contains("fallback", ignoreCase = true)) {
                useFallbacks = false
                client.messages().create(params(content))
            } else throw e
        }
        val stop = resp.stopReason().orElse(null)
        if (stop == StopReason.REFUSAL) return@withContext Identification("unknown", "low", "request declined by the safety filter")
        val text = resp.content().firstNotNullOfOrNull { block -> block.text().orElse(null)?.text() }.orEmpty()
        if (stop == StopReason.MAX_TOKENS || text.isEmpty()) {
            return@withContext Identification("unknown", "low", "no answer (stop reason: $stop)")
        }
        val data = Http.parseJson(text) as? JSONObject ?: JSONObject()
        Identification(cleanModelName(data.optString("model")), data.optString("confidence", "low"), data.optString("reason").trim())
    }
}
