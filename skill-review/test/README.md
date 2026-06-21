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

Every fixture file carries exactly one `SKILL-REVIEW-FIXTURE` marker line so it
can never be mistaken for a live attack. The fixture *content* is otherwise
blind: it reads like a real skill and carries no NOTE/expected-outcome prose. All
rationale and ground-truth labels live in `manifest.py`, not in the fixtures —
this is what lets the layer-2 eval (below) review each fixture without leaking the
answer.

## Labels: `manifest.py` (single source of truth)

`manifest.py` exports `MALICIOUS` and `BENIGN` dicts mapping each fixture to its
static tier/expectation plus a free-text `note` (its rationale / expected
outcome). Both `run_eval.py` (static) and `run_layer2_eval.py` (reasoning) import
their ground truth from here, so there is one place to edit when adding a fixture.

## Layout

- `fixtures/malicious/<class>/` — one attack class each.
- `fixtures/benign/<name>/` — realistic safe skills that must stay non-serious.
- `manifest.py` — ground-truth labels + per-fixture rationale (single source).
- `run_eval.py` — runs `scan.py`, compares to expectations, gates on regressions.
- `run_layer2_eval.py` — blind reasoning eval (dev tool; uses the `claude` CLI).
- `layer2_blind.py` — blinding + prompt helpers for the layer-2 eval.
- `precision_scan.py` — static false-positive rate on a tree of real skills.

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

## Layer-2 (reasoning) eval — `run_layer2_eval.py`

The static layer is a noisy candidate generator. The load-bearing part of
skill-review is **layer 2**: an agent reading a skill and judging whether its
behavior matches its stated intent. This eval measures exactly that, against the
same labeled corpus.

**What it measures**

- **Recall** — every `malicious/*` fixture should come back *serious*
  (`REVIEW_NEEDED` / `NOT_RECOMMENDED` / `DO_NOT_USE`), including the classes the
  static layer only warns on (`rce-curl-bash`, `exfil-env`, `obfuscation`) or
  misses entirely (`hardcoded-token`, `behavior-manipulation`).
- **Precision** — every `benign/*` fixture should come back non-serious (`SAFE`).
  It specifically calls out whether layer 2 **CLEARS** `security-docs` and
  `weather-client` — the two cases the static layer false-flags (a skill that
  *discusses* injection; a skill whose network use *is* its stated purpose).

**Blinding mechanism.** The reviewing agent must not be able to tell a fixture is
a test or see its label. For each fixture the harness:

1. copies it to a fresh system-temp dir with a **neutral name**
   (`skill-under-review-<n>`), never the fixture's real name or tier;
2. **strips every line containing the sentinel** `SKILL-REVIEW-FIXTURE`;
3. builds a neutral reviewer prompt — the layer-2/3 methodology (read live from
   the skill's `SKILL.md`) plus the file contents presented as "a skill a user is
   considering installing", with no mention of tests, fixtures, or expected
   answers. Ground-truth labels stay in `manifest.py` and are used only for
   scoring.

**Dependency (dev-only).** Unlike the shipped skill (Python 3.6+, stdlib only),
this eval invokes the **`claude` CLI** (`claude -p ... --output-format json`) as
the reviewer. It is a dev tool, not part of the skill. If the `claude` binary is
absent or a call fails/times out, the affected fixture is recorded as
`skipped (reviewer unavailable)` and the run continues — it never crashes.

**How to run**

```bash
# 1. Verify the blinding offline first (no API calls):
python3 test/run_layer2_eval.py --dry-run          # one neutralized prompt
python3 test/run_layer2_eval.py --dry-run --all    # every fixture's prompt
#    prove no answer leaks:
python3 test/run_layer2_eval.py --dry-run --all | grep -c SKILL-REVIEW-FIXTURE  # -> 0

# 2. Then run it live (reviewer is non-deterministic):
python3 test/run_layer2_eval.py                    # one run per fixture
python3 test/run_layer2_eval.py --runs 3           # majority-vote over 3 runs
python3 test/run_layer2_eval.py --timeout 180      # per-call timeout (s)
```

The layer-2 eval is a dev/eval tool, **not** a deterministic gate: the reviewer
is an LLM, so results can vary run to run. Use `--runs N` to majority-vote and
reduce noise. The deterministic regression gate is `run_eval.py` (static).

**Measured result (reviewer: `claude` CLI 2.1.x, 1 run/fixture):**

- **Recall 10/10** — every malicious fixture returned `DO_NOT_USE`, *including*
  the five the static layer only warns on or misses (`rce-curl-bash`, `exfil-env`,
  `obfuscation`, `hardcoded-token`, `behavior-manipulation`). The reviewer's
  `primary_concern` named the actual issue each time (the reverse-shell host, the
  hidden HTML-comment directive, decode-and-exec, etc.).
- **Precision 6/6** — every benign fixture returned `SAFE`, and both static-layer
  false-positives were **CLEARED**: `security-docs` (SAFE — documents injection,
  isn't one) and `weather-client` (SAFE — network use is the stated purpose).

So on this corpus the reasoning layer caught everything the regex missed *and*
cleared everything the regex false-flagged (16/16) — the empirical case that
layer 2, not the static scan, is the real detector.

**Honest caveats.** (1) n=1 per fixture; for a robust figure use `--runs 3-5`.
(2) These are curated synthetic samples with relatively clear-cut attacks; an
adaptive adversary crafting obfuscation or behavior-manipulation specifically to
fool an LLM reviewer is a harder, untested case. (3) Results depend on the
reviewer model.

**Precision on real data (the clincher).** Running the same layer-2 reviewer over
the **12 real installed skills the static layer false-flagged** (track-2's 43%)
cleared **12/12** to `SAFE` — every static false positive correctly reasoned away
("documentation-only skill", "read-only advisory", "network use is the stated
purpose"). So on this real sample the 43% static FP rate drops to **0% residual**
after layer-2. Caveat: these are trusted installed skills (no adversarial
benign-looking-malicious case), n=1, reviewer-model-dependent — but it confirms
the precision win holds on real data, not just synthetic fixtures.

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
