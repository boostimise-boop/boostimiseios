"""Identify the phone model with a vision AI that runs on this PC through Ollama
(free, offline, no API key). Install Ollama from https://ollama.com, then:  ollama pull qwen2.5vl:7b
"""
import base64
import io
import json
import re
import socket
import urllib.error
import urllib.request

from PIL import Image

from .identifier import SCHEMA, SYSTEM_PROMPT, Identification, clean_model_name

OLLAMA_URL = "http://localhost:11434"
LOCAL_MODELS = ["qwen2.5vl:7b", "qwen2.5vl:3b", "gemma3:4b", "llama3.2-vision"]
DEFAULT_LOCAL_MODEL = "qwen2.5vl:7b"
_ONE_IMAGE_MODELS = ("llama3.2-vision",)   # these accept a single image per message


class LocalAIError(Exception):
    """Ollama is not usable (not running, model missing, out of memory...)."""


def _shrink(jpeg, max_side=768):
    im = Image.open(io.BytesIO(jpeg))
    im.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode("ascii")


class LocalIdentifier:
    def __init__(self, model=DEFAULT_LOCAL_MODEL, url=OLLAMA_URL):
        self.model = model.strip() or DEFAULT_LOCAL_MODEL
        self.url = url.rstrip("/")

    def _call(self, path, payload=None, timeout=10):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.url + path, data=data,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            try:
                body = json.loads(body).get("error", body)
            except ValueError:
                pass
            raise LocalAIError(f"Ollama error: {body}") from None
        except urllib.error.URLError as e:
            if isinstance(e.reason, (socket.timeout, TimeoutError)):
                raise TimeoutError("the local AI took too long") from None
            raise LocalAIError("Ollama is not running. Install it from https://ollama.com "
                               "(or: winget install Ollama.Ollama) and start it.") from None

    def check(self):
        """Raise LocalAIError with a clear message if Ollama or the model is missing."""
        tags = self._call("/api/tags")
        names = {m.get("name", "") for m in tags.get("models", [])}
        want = self.model if ":" in self.model else self.model + ":latest"
        if want not in names and self.model not in names:
            raise LocalAIError(f"The AI model '{self.model}' is not downloaded yet. "
                               f"Open a terminal and run:  ollama pull {self.model}")

    def identify(self, code, images, context=""):
        if self.model.startswith(_ONE_IMAGE_MODELS):
            images = images[:1]
        prompt = (f"Product code: {code}\n\n"
                  f"Chat text posted with these photos (may be empty or unrelated):\n{context or '(none)'}\n\n"
                  f"Which phone model is shown in the photos? Answer with JSON only.")
        resp = self._call("/api/chat", {
            "model": self.model,
            "stream": False,
            "format": SCHEMA,
            "keep_alive": "30m",
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt, "images": [_shrink(i) for i in images]},
            ],
        }, timeout=900)
        text = (resp.get("message") or {}).get("content", "")
        try:
            data = json.loads(text)
        except ValueError:
            m = re.search(r"\{.*\}", text, re.S)
            try:
                data = json.loads(m.group(0)) if m else {}
            except ValueError:
                data = {}
        if not data.get("model"):
            return Identification("unknown", "low", f"unreadable answer: {text[:80]}")
        conf = data.get("confidence", "low")
        return Identification(clean_model_name(data.get("model")),
                              conf if conf in ("high", "medium", "low") else "low",
                              (data.get("reason") or "").strip())
