"""Identify the phone model shown in a set of photos with Claude (Anthropic API)."""
import base64
import json
import re
from typing import NamedTuple

import anthropic

DEFAULT_MODEL = "claude-opus-5"
MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]

# Models that accept the server-side refusal fallback (re-runs a declined request on another model)
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-fable-5")
# Models without the effort setting
_NO_EFFORT_PREFIXES = ("claude-haiku-4-5",)

SYSTEM_PROMPT = """You identify smartphone models from photos for a mobile phone cover (case) shop in Bangladesh. \
The shop's WhatsApp groups post a product code together with photos of a phone or of its cover. \
Your answer is written next to that code in the shop's product list.

How to identify:
- Readable text wins: a model name printed on the phone, box, cover packaging or a screenshot, \
or written in the chat text that came with the photos. A model number (About-phone screenshot, box label, \
the small print on the phone's back) counts too: convert it to the name, e.g. SM-A065F is samsung a06.
- A camera watermark ("Shot on vivo V30", "AI QUAD CAMERA") names the phone that took the picture, \
not always the customer's phone: use it only when nothing else shows the model.
- Many photos are just the customer's pictures to print on the cover (people, places): ignore those.
- Otherwise use the rear camera module (number, size and arrangement of lenses, flash position, module shape), \
the phone's outline, buttons and ports, and the cutouts of the cover - a cover's cutouts show the camera \
layout of the phone it fits.
- If several models share the same design, pick the most likely one and lower the confidence.

Format of "model": lowercase, the short name used in Bangladeshi mobile shops, brand + model + variant. \
Examples: samsung a70, samsung a52s 5g, samsung s23 ultra, iphone xs max, iphone 13 pro, redmi note 7, \
redmi note 13 pro 5g, poco x3 pro, realme c55, oppo a57, vivo y21, infinix hot 40i, tecno spark 20, \
oneplus nord ce 3 lite, google pixel 7. Leave out "galaxy", "apple" and "xiaomi" when the series name is \
enough (redmi, poco). Add "5g" only when the photos or text show it is the 5G version. Never use the "/" character. \
If the photos do not show a phone or a phone cover, answer "unknown" with low confidence.
"reason" is a short note (at most 15 words) on what gave the model away."""

SCHEMA = {
    "type": "object",
    "properties": {
        "model": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "reason": {"type": "string"},
    },
    "required": ["model", "confidence", "reason"],
    "additionalProperties": False,
}


class Identification(NamedTuple):
    model: str
    confidence: str
    reason: str


def clean_model_name(name):
    name = (name or "").strip().lower().replace("/", " & ")
    name = re.sub(r"\s+", " ", name).strip(" .,:;-\"'")
    # shop style: "samsung a70", "iphone xs max", "redmi note 7"
    name = re.sub(r"^apple\s+(?=iphone)", "", name)
    name = re.sub(r"^samsung\s+galaxy\s+", "samsung ", name)
    name = re.sub(r"^galaxy\s+", "samsung ", name)
    name = re.sub(r"^xiaomi\s+(?=(redmi|poco)\b)", "", name)
    name = re.sub(r"(?<=[a-z0-9])\+(?=\s|$)", " plus", name)   # "s9+" -> "s9 plus", as the shop writes it
    return name or "unknown"


class ModelIdentifier:
    def __init__(self, api_key=None, model=DEFAULT_MODEL, effort="high"):
        # api_key=None -> the SDK reads ANTHROPIC_API_KEY (or an `ant auth login` profile)
        self.client = anthropic.Anthropic(api_key=api_key or None, max_retries=4, timeout=300)
        self.model = model.strip()
        self.effort = effort
        self.use_fallbacks = self.model.startswith(_FALLBACK_PREFIXES)
        self.use_effort = not self.model.startswith(_NO_EFFORT_PREFIXES)

    def _request(self, content):
        output_config = {"format": {"type": "json_schema", "schema": SCHEMA}}
        if self.use_effort and self.effort:
            output_config["effort"] = self.effort
        kwargs = dict(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config=output_config,
        )
        if self.use_fallbacks:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        return self.client.beta.messages.create(**kwargs)

    def identify(self, code, images, context=""):
        """images: list of JPEG bytes. Returns Identification."""
        content = []
        for i, img in enumerate(images, 1):
            content.append({"type": "text", "text": f"Photo {i}:"})
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg",
                           "data": base64.standard_b64encode(img).decode("ascii")},
            })
        content.append({
            "type": "text",
            "text": f"Product code: {code}\n\n"
                    f"Chat text posted with these photos (may be empty or unrelated):\n{context or '(none)'}\n\n"
                    f"Which phone model is this?",
        })

        for _ in range(3):
            try:
                resp = self._request(content)
                break
            except anthropic.BadRequestError as e:
                msg = str(e).lower()
                if self.use_fallbacks and "fallback" in msg:
                    self.use_fallbacks = False
                elif self.use_effort and "effort" in msg:
                    self.use_effort = False
                else:
                    raise

        if resp.stop_reason == "refusal":
            return Identification("unknown", "low", "request declined by the safety filter")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        if resp.stop_reason == "max_tokens" or not text:
            return Identification("unknown", "low", f"no answer (stop reason: {resp.stop_reason})")
        data = json.loads(text)
        return Identification(clean_model_name(data.get("model")),
                              data.get("confidence", "low"),
                              (data.get("reason") or "").strip())
