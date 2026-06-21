"""Static detection rules for skill-review.

Two rule sets, ported from the MaliciousAgentSkillsBench benchmark
(github.com/protectskills/MaliciousAgentSkillsBench), which accompanies a
USENIX Security 2026 paper:

- CODE_RULES   : malicious-code regexes from the static scanner's rules.yaml.
- INJECTION_RULES : the regex/keyword layer of the NOVA prompt-injection rules
  (the .nov files), used to flag malicious *instructions* in skill text. The
  benchmark's semantic-embedding and per-rule LLM layers are intentionally NOT
  ported here — those belong to the reasoning step a human/agent performs after
  reading this scanner's output.

Every rule is a dict: id, category, name, severity, confidence, patterns[].
Severity uses the analyzer's vocabulary: CRITICAL / HIGH / WARNING / INFO.
NOVA's "high"/"medium" are mapped to HIGH / WARNING so they score on the same
scale. All patterns are matched case-insensitively, line by line.

This is defensive security tooling: it surfaces candidate lines for review, not
verdicts. The regex layer is deliberately coarse and over-fires; that is why the
benchmark used it only as a candidate generator ahead of human/LLM triage.
"""

# Severity -> weight, exactly as in the benchmark's analyzer.py.
SEVERITY_WEIGHTS = {"CRITICAL": 10, "HIGH": 7, "WARNING": 4, "INFO": 1}

# ---------------------------------------------------------------------------
# Malicious-code rules (ported from rules.yaml).
# ---------------------------------------------------------------------------
CODE_RULES = [
    {
        "id": "NET001", "category": "network", "name": "External network request",
        "severity": "WARNING", "confidence": 0.7,
        "patterns": [
            # Source rules.yaml used `\b(curl|wget)\b\s+https?://`, which only
            # matches a bare `curl https://` — any flags in between (the real-world
            # case, e.g. `curl -fsSL https://`) defeat it. Broadened to allow
            # arguments before the URL so flagged invocations are caught too.
            r"\b(curl|wget)\b.*\s+https?://",
            r"\brequests\.(get|post|put|delete)\s*\(",
            r"\bfetch\s*\(\s*['\"]https?://",
        ],
    },
    {
        "id": "FILE001", "category": "file", "name": "Sensitive file access",
        "severity": "HIGH", "confidence": 0.8,
        "patterns": [
            r"(^|/)\.ssh/",
            r"(^|/)\.env(\.|$)",
            r"id_rsa|id_dsa|id_ed25519",
            r"credentials\.json|api_keys\.conf",
        ],
    },
    {
        "id": "FILE002", "category": "file", "name": "Dangerous file modification",
        "severity": "HIGH", "confidence": 0.8,
        "patterns": [
            r"rm\s+-rf\s+/",
            r"chmod\s+(777|a\+rwx)",
            r">\s+/(etc|usr|bin|sbin)/",
        ],
    },
    {
        "id": "CMD001", "category": "command", "name": "Dangerous shell command",
        "severity": "HIGH", "confidence": 0.8,
        "patterns": [
            r"\bsudo\b",
            r"rm\s+-rf\s+/",
            r"\bdd\s+.*of=/",
            # Source rules.yaml has /dev/(tcp|udp|null); `null` dropped here as a
            # deliberate precision fix — `2>/dev/null` is a benign idiom that
            # otherwise false-flags nearly every real skill. Reverse shells use
            # /dev/tcp|udp (also caught by INJ003).
            r">\s*/dev/(tcp|udp)",
        ],
    },
    {
        "id": "CMD002", "category": "command", "name": "Programmatic command execution",
        "severity": "WARNING", "confidence": 0.7,
        "patterns": [
            r"\bos\.system\s*\(",
            r"subprocess\.(call|run|Popen).*shell\s*=\s*True",
            r"\bexec\s*\(",
        ],
    },
    {
        "id": "INJ001", "category": "injection", "name": "Code execution primitive",
        "severity": "WARNING", "confidence": 0.7,
        "patterns": [
            r"\beval\s*\(",
            r"\bexec\s*\(",
            r"__import__\s*\(",
            r"\bcompile\s*\(",
        ],
    },
    {
        "id": "INJ003", "category": "injection", "name": "Backdoor / reverse shell",
        "severity": "CRITICAL", "confidence": 0.95,
        "patterns": [
            r"bash\s+-i\s+>&\s*/dev/tcp/",
            r"\b(nc|netcat)\b.*\s-e\s+/",
        ],
    },
    {
        "id": "DEP001", "category": "dependency", "name": "Global package install",
        "severity": "WARNING", "confidence": 0.6,
        "patterns": [
            r"npm\s+(install|i).*\s-g\b",
            r"yarn\s+global",
            r"pip\s+install.*--global",
        ],
    },
    {
        "id": "DEP002", "category": "dependency", "name": "Forced package reinstall",
        "severity": "INFO", "confidence": 0.6,
        "patterns": [
            r"pip\s+install.*(--upgrade|--force-reinstall)",
        ],
    },
]

# ---------------------------------------------------------------------------
# Malicious-instruction rules (regex/keyword layer of the NOVA .nov files).
# nova "high" -> HIGH, nova "medium" -> WARNING.
# ---------------------------------------------------------------------------
INJECTION_RULES = [
    # --- instruction_override.nov ---
    {
        "id": "NOVA-IO-IgnorePrevious", "category": "instruction_override",
        "name": "Ignore/override previous instructions", "severity": "HIGH",
        "confidence": 0.7,
        "patterns": [
            r"ignore\s+(all\s+)?(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules?|guidelines?|directives?|context)",
            r"forget\s+(all\s+)?(previous|prior|above|earlier|your)\s+(instructions?|prompts?|rules?|training|guidelines?)",
            r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|context|rules?)",
            r"override\s+(your\s+|all\s+)?(instructions?|programming|directives?|rules?|guidelines?)",
            r"replace\s+(your\s+)?(instructions?|prompt|guidelines?|rules?)\s+with",
            r"(do\s+)?not\s+follow\s+(your\s+)?(previous|original|initial)\s+(instructions?|rules?|guidelines?)",
        ],
    },
    {
        "id": "NOVA-IO-NewSystemPrompt", "category": "instruction_override",
        "name": "Fake/new system prompt injection", "severity": "HIGH",
        "confidence": 0.65,
        "patterns": [
            r"(new|updated|revised|real)\s+system\s+prompt",
            r"system\s+prompt\s*[:\=]",
            r"(this\s+is\s+)?(your\s+)?new\s+(instructions?|task|objective|mission|directive)",
            r"from\s+now\s+on[,\s]+(you\s+)?(will|must|should|are)",
        ],
    },
    {
        "id": "NOVA-IO-FakeDelimiters", "category": "instruction_override",
        "name": "Fake end-of-prompt / system delimiters", "severity": "HIGH",
        "confidence": 0.6,
        "patterns": [
            r"end\s+(of\s+)?(system\s+)?(prompt|instructions?)\b.*\b(new|begin|start)",
            r"\[\/?(system|SYSTEM)\]|\{\/?(system|SYSTEM)\}|<\/?(system)\s*>",
            r"={3,}\s*(end|new|begin|start)\s*(of\s+)?(prompt|instructions?|system)",
            r"---+\s*(system|instructions?|prompt)\s*(start|end|override)",
            r"\[\[SYSTEM\]\]|\(\(SYSTEM\)\)|\{\{SYSTEM\}\}",
        ],
    },
    {
        "id": "NOVA-IO-PriorityManipulation", "category": "instruction_override",
        "name": "Priority manipulation", "severity": "WARNING", "confidence": 0.55,
        "patterns": [
            r"(highest|top|maximum|critical)\s+priority\s*(:|instruction|override)?",
            r"priority\s*(level)?\s*[:=]\s*(1|one|highest|critical|urgent)",
            r"this\s+(instruction|command|task|directive)\s+(takes?\s+)?priority\s+over",
            r"(more\s+)?important\s+than\s+(your\s+)?(original|previous|other)\s+(instructions?|rules?)",
        ],
    },
    {
        "id": "NOVA-IO-ResetContext", "category": "instruction_override",
        "name": "Reset / clear context", "severity": "HIGH", "confidence": 0.65,
        "patterns": [
            r"reset\s+(your\s+)?(context|memory|instructions?|state|conversation)",
            r"clear\s+(your\s+)?(memory|context|instructions?|history|state)",
            r"wipe\s+(your\s+)?(memory|context|slate)",
            r"start\s+(fresh|over|anew)\s+(without|ignoring|from\s+scratch)",
        ],
    },
    # --- context_manipulation.nov ---
    {
        "id": "NOVA-CM-FalseAuthority", "category": "context_manipulation",
        "name": "False authority claim", "severity": "HIGH", "confidence": 0.7,
        "patterns": [
            r"(anthropic|openai|claude\s*team|google|meta)\s+(says?|requires?|demands?|orders?|instructs?)",
            r"(official|authorized|verified)\s+(instruction|message|update|command)\s+from\s+(anthropic|openai|claude)",
            r"(system\s+)?(administrator|admin|root|superuser)\s+(message|override|instruction|command)",
            r"developer\s+(mode|override|access)\s+(enabled|activated|granted)",
            r"maintenance\s+(mode|override)\s*(enabled|active)",
            r"(internal|debug|test)\s+mode\s+(enabled|activated|on)",
        ],
    },
    {
        "id": "NOVA-CM-HiddenInstructions", "category": "context_manipulation",
        "name": "Instructions hidden in comments", "severity": "HIGH",
        "confidence": 0.7,
        "patterns": [
            r"<!--\s*(ignore|disregard|override|system|instruction|prompt|forget)",
            r"\/\*\s*(ignore|disregard|override|system|instruction|prompt|forget)",
            r"\/\/\s*(ignore|system|override|instruction|execute)",
            r"#\s*(system|instruction|hidden|secret|execute)\s*(prompt|instruction|command|override)",
        ],
    },
    {
        "id": "NOVA-CM-FakeStructure", "category": "context_manipulation",
        "name": "Fake system message structure", "severity": "HIGH",
        "confidence": 0.6,
        "patterns": [
            r"\"\s*(system|role|instruction|prompt)\s*\"\s*:\s*\"",
            r"\{\"role\"\s*:\s*\"system\"",
            r"\{\"type\"\s*:\s*\"(instruction|command|system)\"",
            r"<system[^>]*>|<instruction[^>]*>|<override[^>]*>",
            r"<hidden>|<invisible>|<secret>",
            r"\[INST\]|\[\/INST\]|\[SYS\]|\[\/SYS\]",
            r"\[hidden\]|\[invisible\]|\[system\]|\[admin\]",
        ],
    },
    {
        "id": "NOVA-CM-FalseClaims", "category": "context_manipulation",
        "name": "False prior-conversation claims", "severity": "HIGH",
        "confidence": 0.65,
        "patterns": [
            r"previous\s+conversation\s+(summary|context|history)\s*:",
            r"user\s+(profile|context|role)\s*[:\s].*\b(admin|root|developer|superuser)",
            r"in\s+our\s+(last|previous)\s+conversation\s+you\s+(agreed|said|confirmed)",
            r"you\s+(previously|earlier|already)\s+(said|agreed|confirmed|promised)\s+that",
        ],
    },
    {
        "id": "NOVA-CM-BoundaryManipulation", "category": "context_manipulation",
        "name": "Instruction boundary manipulation", "severity": "HIGH",
        "confidence": 0.6,
        "patterns": [
            r"ignore\s+everything\s+(before|after|above|below)\s+this\s+(line|point|marker)",
            r"(real|actual|true)\s+(instruction|prompt|task|command)\s*(starts?|begins?)?\s*(here|below|now)",
            r"the\s+above\s+(was|is)\s+(just\s+)?(a\s+)?(test|joke|fake|distraction)",
        ],
    },
    {
        "id": "NOVA-CM-PromptExtraction", "category": "context_manipulation",
        "name": "System prompt extraction", "severity": "HIGH", "confidence": 0.7,
        "patterns": [
            r"(show|reveal|tell|display|print|output)\s+(me\s+)?(your\s+)?(full\s+)?(system\s+)?prompt",
            r"(what|list|show)\s+(are\s+)?(your\s+)?(system\s+)?(instructions?|guidelines?|rules?|directives?)",
            r"repeat\s+(your\s+)?(system\s+)?(prompt|instructions?)\s+(back|verbatim|exactly)",
            r"what\s+(were\s+)?you\s+told\s+(to\s+do|at\s+the\s+(start|beginning))",
        ],
    },
    # --- encoding_obfuscation.nov ---
    {
        "id": "NOVA-EN-Base64", "category": "encoding",
        "name": "Base64 payload + decode/execute", "severity": "WARNING",
        "confidence": 0.6,
        "patterns": [
            r"decode\s+(this\s+|the\s+)?base64",
            r"base64\s*(encoded|string|payload)?\s*[:\s]\s*[A-Za-z0-9+\/]{20,}={0,2}",
            r"execute\s+(the\s+)?decoded\s+(base64|content|string)",
        ],
    },
    {
        "id": "NOVA-EN-HexUnicode", "category": "encoding",
        "name": "Hex / Unicode encoded payload", "severity": "WARNING",
        "confidence": 0.55,
        "patterns": [
            r"decode\s+(this\s+|the\s+)?hex(adecimal)?",
            r"\\x[0-9a-fA-F]{2}(\\x[0-9a-fA-F]{2}){5,}",
            r"0x[0-9a-fA-F]{2}(\s*,?\s*0x[0-9a-fA-F]{2}){5,}",
            r"hex\s*(string|encoded|code)?\s*[:=]\s*[0-9a-fA-F\s]{12,}",
            r"\\u[0-9a-fA-F]{4}(\\u[0-9a-fA-F]{4}){3,}",
        ],
    },
    {
        "id": "NOVA-EN-InvisibleChars", "category": "encoding",
        "name": "Invisible / zero-width characters", "severity": "HIGH",
        "confidence": 0.7,
        "patterns": [
            r"[​‌‍﻿­]{2,}",
            r"[⁠᠎ -‏]{3,}",
            r"[͏ᅟᅠ឴឵]{2,}",
        ],
    },
    {
        "id": "NOVA-EN-Leetspeak", "category": "encoding",
        "name": "Leetspeak obfuscation of injection keywords",
        "severity": "WARNING", "confidence": 0.55,
        "patterns": [
            r"\b(1gn0r3|f0rg3t|d1sr3g4rd|0v3rr1d3)\b",
            r"\b(syst3m|pr0mpt|1nstruct10n[s5]?)\b",
            r"\b(j41lbr34k|byp4ss|h4ck)\b",
            r"[1!][gG9][nN][0oO][rR][3eE]\s+[pP][rR][3eE][vV][1!][0oO][uU][sS5]",
        ],
    },
    {
        "id": "NOVA-EN-Homoglyphs", "category": "encoding",
        "name": "Homoglyph lookalike characters", "severity": "HIGH",
        "confidence": 0.6,
        "patterns": [
            r"[аеорсухі]{3,}",
            r"[αεορντ]{2,}",
        ],
    },
    {
        "id": "NOVA-EN-OtherMethods", "category": "encoding",
        "name": "Other encodings (ROT13, reverse, acrostic, URL)",
        "severity": "WARNING", "confidence": 0.5,
        "patterns": [
            r"decode\s+(this\s+)?(rot13|rot-13|caesar|cipher)",
            r"reverse\s+(this|the\s+following)\s+(text|string|message)\s+and\s+(execute|follow)",
            r"read\s+(the\s+)?(first|last|nth)\s+(letter|character)\s+of\s+each",
            r"url\s*(encoded|encoding)\s*[:=]",
        ],
    },
    # --- roleplay_jailbreak.nov ---
    {
        "id": "NOVA-JB-DAN", "category": "jailbreak",
        "name": "DAN / unrestricted-mode jailbreak", "severity": "HIGH",
        "confidence": 0.7,
        "patterns": [
            r"you\s+are\s+(now\s+)?DAN\b",
            r"DAN\s+(mode|persona|character|version)",
            r"\b(do|does)\s+anything\s+now\b",
            r"jailbreak(ed)?\s+(mode|prompt|version|enabled)",
            r"DEVELOPER\s+MODE\s+(ENABLED|ACTIVATED|ON)",
            r"(enable|activate|enter)\s+(jailbreak|unrestricted|god)\s*mode",
        ],
    },
    {
        "id": "NOVA-JB-PersonaSwitching", "category": "jailbreak",
        "name": "Persona switching to bypass restrictions",
        "severity": "WARNING", "confidence": 0.5,
        "patterns": [
            r"pretend\s+(to\s+be|you\s+are|you're|that\s+you)",
            r"act\s+as\s+(if\s+you\s+(are|were)|an?)\b",
            r"roleplay\s+as\s+(an?\s+)?(unrestricted|unfiltered|uncensored|evil)",
            r"you\s+are\s+(now\s+)?(a|an)\s+(different|new|unrestricted|unfiltered|uncensored)",
            r"(immerse|embody)\s+(yourself\s+)?in\s+the\s+(role|character|persona)",
        ],
    },
    {
        "id": "NOVA-JB-RestrictionBypass", "category": "jailbreak",
        "name": "Disable / bypass restrictions", "severity": "HIGH",
        "confidence": 0.7,
        "patterns": [
            r"(without|ignore|bypass|disable|remove)\s+(your\s+)?(restrictions?|filters?|safeguards?|limitations?|guardrails?)",
            r"(remove|turn\s+off|disable|deactivate)\s+(your\s+)?(ethical|safety|content)\s+(guidelines?|filters?|restrictions?)",
            r"\b(no|without|free\s+from)\s+(rules?|restrictions?|limitations?|boundaries|constraints)",
            r"unrestricted\s+(mode|access|version|output)",
            r"uncensored\s+(mode|version|response|output)",
        ],
    },
    {
        "id": "NOVA-JB-EvilTwin", "category": "jailbreak",
        "name": "Evil twin / split personality", "severity": "HIGH",
        "confidence": 0.6,
        "patterns": [
            r"(two|dual|split|multiple)\s+(personalities?|modes?|personas?|sides?)",
            r"(evil|shadow|dark|unrestricted|uncensored)\s+(twin|version|mode|side|alter\s*ego)",
            r"your\s+(evil|dark|shadow|unrestricted)\s+(side|self|version)",
        ],
    },
    {
        "id": "NOVA-JB-HypotheticalBypass", "category": "jailbreak",
        "name": "Hypothetical / fictional framing bypass",
        "severity": "WARNING", "confidence": 0.5,
        "patterns": [
            r"in\s+a\s+(hypothetical|fictional|imaginary)\s+(world|scenario|situation)\s+where\s+(you|AI|there)",
            r"if\s+you\s+(were|had|could)\s+(be\s+)?(?:no|without|free\s+from)\s+(restrictions?|rules?|limitations?)",
            r"for\s+(educational|research|fiction|creative)\s+purposes?\s+only",
        ],
    },
]

ALL_RULES = CODE_RULES + INJECTION_RULES
