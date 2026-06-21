# skill-review test corpus

A labeled fixture corpus + offline eval harness for the **static layer**
(`scan.py`) of skill-review. Run it:

```bash
python3 test/run_eval.py     # exit 0 = no regressions
```

No network, stdlib only. It measures the static pre-scan only — **not** the full
skill, whose layer-2/3 reasoning is the load-bearing part. A "gap" here is not a
bug in the skill; it is a class the static layer is not expected to catch alone.

## Why synthetic fixtures

Real confirmed-malicious skills get taken down fast, so a live corpus is not
maintainable. Like antivirus EICAR samples, we test recall against *synthetic*
samples — but grounded in the real attack-class distribution from
MaliciousAgentSkillsBench (USENIX Security 2026): RCE, behavior manipulation,
credential theft, external transmission, instruction override lead the list.
Fixtures are authored to exercise each class, including classes the static layer
is *expected to miss*, so the suite is not rigged to pass.

Every fixture file carries a `skill-review eval fixture` marker so it can never be
mistaken for a live attack.

## Layout

- `fixtures/malicious/<class>/` — one attack class each.
- `fixtures/benign/<name>/` — realistic safe skills that must stay non-serious.
- `run_eval.py` — runs `scan.py`, compares to expectations, gates on regressions.

## Tiers (expectations)

Malicious: `serious` (must be flagged serious — regression gate) · `gap-warning`
(only WARNING expected; needs layer 2) · `gap-missed` (static blind spot; needs
layer 2). Benign: `clean` (must stay non-serious — FP gate) · `known-fp` (benign
but the static layer flags it; only layer-2 reasoning clears it).

## Current results (static layer)

- **Recall (serious tier): 5/5** — reverse shell, credential theft, instruction
  override, hidden instructions, context manipulation all flagged serious.
- **Detected at any level: 8/10** — plus `rce-curl-bash` and `exfil-env` and
  `obfuscation` at WARNING only.
- **Precision: 5/5 benign clean, 0 false positives** (the `2>/dev/null`,
  legit-network, and pip-install cases all stay non-serious).
- **Documented gaps needing layer 2:**
  - `behavior-manipulation`, `hardcoded-token` — missed entirely (no keyword/
    code signature; pure intent problem).
  - `rce-curl-bash`, `exfil-env`, `obfuscation` — WARNING only. The ported
    ruleset weights network egress / base64 / curl-pipe-bash below HIGH, so the
    most common real class (RCE/exfil) does not reach a "serious" verdict on the
    static layer alone. This is *the* reason layer 2 is load-bearing.
  - `security-docs` — a benign skill that *discusses* injection is flagged by the
    static layer (it can't tell documentation from a directive).

## Precision on real skills (track 2)

Recall is measured on synthetic fixtures above; precision is measured on **real,
benign** skills (abundant and not taken down). `precision_scan.py` points the
scanner at a tree of real skills and reports the false-positive rate:

```bash
python3 test/precision_scan.py ~/.claude
```

Measured on 28 real installed skills: **12/28 (43%) were flagged "serious" — all
false positives.** This matches the benchmark's own finding that >95% of static
flags were not malicious. The drivers are the injection rules firing on skills
that legitimately *discuss* prompts/commands, e.g.:

- `## System Prompt Design` (a Markdown heading) → `NOVA-CM-HiddenInstructions`.
- "Create **new task**" → `NOVA-IO-NewSystemPrompt` (matches `new task`).
- MCP/agent docs tripping `NOVA-JB-*` on ordinary phrasing.

This is the empirical case for the skill's core rule: **the static "serious"
verdict is a candidate, never a conclusion — layer-2 reasoning (documentation vs.
directive) is mandatory.** It is not a regression; it is the measured noise floor
of the static layer.

## Findings this corpus surfaced (and fixes applied)

- **NET001 missed real `curl`/`wget`.** The source pattern only matched bare
  `curl https://`; flagged invocations (`curl -fsSL https://`) slipped through.
  Broadened to allow arguments before the URL. (Documented in `rules.py`.)
- **`NOVA-JB-RestrictionBypass` false-fires on "no rules" / "without rules"**
  phrasing in ordinary prose. Not changed (it is also a real jailbreak phrase —
  a precision/recall tradeoff best resolved by layer-2 reasoning), but noted here
  as a known false-positive source.
