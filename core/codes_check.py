"""Checks the code readers against core/code_samples.json (see core/codes.py).

    python -m core.codes_check               this app's reader, and its patterns in JavaScript (Node)
    python -m core.codes_check --web DIR     also the website's lib/codes.ts; DIR is the website's folder,
                                             found by itself at ../../boostimise/frontend/boostimise
    python -m core.codes_check --sync-web    copies the samples to the website (lib/code_samples.json) first

Exits with 1 when a sample fails. packaging/build.py runs it before building. The Android app checks
the same file in its unit test (gradlew testDebugUnitTest), the iPhone app in the iOS workflow.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

from core import codes

SAMPLES = Path(__file__).with_name("code_samples.json")
ROOT = Path(__file__).resolve().parent.parent
WEB_GUESS = ROOT.parent.parent / "boostimise" / "frontend" / "boostimise"
WEB_COPY = Path("lib") / "code_samples.json"


def load(path: Path = SAMPLES) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check(samples: dict) -> list[str]:
    """The samples this app's reader gets wrong, one line each."""
    readers = {name: codes.Reader(style) for name, style in samples["styles"].items()}
    bad = []

    def expect(what, got, want):
        if got != want:
            bad.append(f"{what}: got {got!r}, want {want!r}")

    for s in samples["messages"]:
        expect(f"[{s['style']}] codes in {s['text']!r}", readers[s["style"]].codes_in_text(s["text"]), s["codes"])
    for s in samples["order_codes"]:
        expect(f"[{s['style']}] order codes of {s['text']!r}", readers[s["style"]].order_codes(s["text"]), s["codes"])
    for s in samples["list_lines"]:
        got = list(readers[s["style"]].split_list_line(s["line"]))
        expect(f"[{s['style']}] list line {s['line']!r}", got, [s["code"], s["rest"]])
    for s in samples["normalize"]:
        expect(f"[{s['style']}] key of {s['code']!r}", readers[s["style"]].normalize(s["code"]), s["key"])
    for s in samples["code_parts"]:
        expect(f"[{s['style']}] parts of {s['code']!r}", readers[s["style"]].code_parts(s["code"]), s["parts"])
    for s in samples["patterns"]:
        pattern = readers[s["style"]].code_pattern(s["code"])
        for text in s["match"]:
            if not re.search(pattern, text, re.I):
                bad.append(f"[{s['style']}] the pattern of {s['code']!r} does not match {text!r}")
        for text in s["no_match"]:
            if re.search(pattern, text, re.I):
                bad.append(f"[{s['style']}] the pattern of {s['code']!r} matches {text!r}")
    for s in samples["any_codes"]:
        r = readers[s["style"]]
        got = list(dict.fromkeys(codes.norm(p, n) for p, n in re.findall(r.js_any_code, s["text"], re.I)))
        expect(f"[{s['style']}] any codes in {s['text']!r}", got, s["codes"])
    for s in samples["sort"]:
        shuffled = list(s["codes"])
        random.Random(1).shuffle(shuffled)
        expect(f"[{s['style']}] sorted", sorted(shuffled, key=readers[s["style"]].sort_key), s["codes"])
    for s in samples["examples"]:
        expect(f"[{s['style']}] example", codes.example(samples["styles"][s["style"]]), s["example"])
    for s in samples["guess"]:
        style, problem = codes.guess_style(s["examples"])
        expect(f"guess {s['examples']!r}", style, s["style"])
        if style is None and not problem:
            bad.append(f"guess {s['examples']!r}: no style and no reason")
    for s in samples["clean_style"]:
        expect(f"clean {s['in']!r}", codes.clean_style(s["in"]), s["out"])
    return bad


# Runs the patterns this app hands to the WhatsApp script in JavaScript's own regex engine.
_JS_CHECK = r"""
const t = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const bad = [];
for (const p of t.patterns) {
  const re = new RegExp(p.pattern, 'i');
  for (const s of p.match) if (!re.test(s)) bad.push(`[${p.style}] the pattern of ${p.code} does not match ${s} in JavaScript`);
  for (const s of p.no_match) if (re.test(s)) bad.push(`[${p.style}] the pattern of ${p.code} matches ${s} in JavaScript`);
}
for (const a of t.any) {
  const keys = [...a.text.matchAll(new RegExp(a.source, 'gi'))].map((m) => m[1] ? `${m[1].toLowerCase()}-${parseInt(m[2], 10)}` : `${parseInt(m[2], 10)}`);
  const got = JSON.stringify([...new Set(keys)]);
  if (got !== JSON.stringify(a.codes)) bad.push(`[${a.style}] any codes in ${a.text} in JavaScript: got ${got}`);
}
for (const c of t.chat) {
  // as Rename by Match reads a message (tools/match_renamer/engine.py, P.info)
  const keys = [];
  for (const m of c.text.matchAll(new RegExp(c.source, 'gim'))) {
    const key = (n) => (m[1] ? m[1].toLowerCase() + '-' : '') + parseInt(n, 10);
    keys.push(key(m[2]));
    for (const x of m[3].matchAll(new RegExp(c.more.split('__P__').join(m[1]), 'gi'))) keys.push(key(x[1]));
  }
  const got = JSON.stringify([...new Set(keys)]);
  if (got !== JSON.stringify(c.codes)) bad.push(`[${c.style}] codes in ${JSON.stringify(c.text)} with js_chat_code: got ${got}`);
}
for (const c of t.compile) { try { new RegExp(c.source, c.flags); } catch (e) { bad.push(`[${c.style}] ${c.name} is not valid JavaScript: ${e.message}`); } }
console.log(JSON.stringify(bad));
"""


def check_js(samples: dict) -> list[str] | None:
    """The samples whose patterns JavaScript reads differently (None when Node is not installed)."""
    node = shutil.which("node")
    if not node:
        return None
    readers = {name: codes.Reader(style) for name, style in samples["styles"].items()}
    data = {
        "patterns": [{**s, "pattern": readers[s["style"]].code_pattern(s["code"])} for s in samples["patterns"]],
        "any": [{**s, "source": readers[s["style"]].js_any_code} for s in samples["any_codes"]],
        "chat": [{**s, "source": readers[s["style"]].js_chat_code, "more": readers[s["style"]].js_more_item}
                 for s in samples["messages"] if s["text"] == s["text"].translate(codes.BN_DIGITS)],
        "compile": [{"style": n, "name": name, "source": getattr(r, name), "flags": flags}
                    for n, r in readers.items()
                    for name, flags in (("js_chat_code", "gim"), ("js_more_item", "gi"), ("js_any_code", "gi"),
                                        ("js_code_parts", "gi"))],
    }
    out = subprocess.run([node, "-e", _JS_CHECK], input=json.dumps(data), capture_output=True, text=True,
                         encoding="utf-8")
    if out.returncode != 0:
        return [f"node failed: {out.stderr.strip()}"]
    return json.loads(out.stdout)


def check_web(web: Path) -> list[str]:
    """Runs the website's own check of lib/codes.ts, after making sure its copy of the samples is this one."""
    copy = web / WEB_COPY
    if not copy.exists() or copy.read_bytes() != SAMPLES.read_bytes():
        return [f"{copy} is not the same as {SAMPLES}: run python -m core.codes_check --sync-web"]
    node = shutil.which("node")
    if not node:
        return ["node is not installed, so the website's reader was not checked"]
    out = subprocess.run([node, "--disable-warning=MODULE_TYPELESS_PACKAGE_JSON", "scripts/check-codes.mjs"], cwd=web, capture_output=True, text=True, encoding="utf-8")
    lines = [l for l in (out.stdout + out.stderr).splitlines() if l.strip()]
    return [] if out.returncode == 0 else lines or [f"scripts/check-codes.mjs exited with {out.returncode}"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--web", type=Path, help="the website's folder (to check its lib/codes.ts too)")
    ap.add_argument("--sync-web", action="store_true", help="copy the samples to the website first")
    args = ap.parse_args(argv)
    samples = load()
    failed = False

    def report(name, bad):
        nonlocal failed
        if bad is None:
            print(f"skip  {name} (Node is not installed)")
            return
        print(f"{'ok  ' if not bad else 'FAIL'}  {name}")
        for line in bad:
            print("      " + line)
        failed |= bool(bad)

    report("core/codes.py", check(samples))
    report("core/codes.py patterns in JavaScript", check_js(samples))
    web = args.web or (WEB_GUESS if (WEB_GUESS / "lib" / "codes.ts").exists() else None)
    if args.sync_web:
        if not web:
            sys.exit("The website's folder was not found: pass --web DIR.")
        shutil.copyfile(SAMPLES, web / WEB_COPY)
        print(f"copied the samples to {web / WEB_COPY}")
    if web:
        report(f"website lib/codes.ts ({web})", check_web(web))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
