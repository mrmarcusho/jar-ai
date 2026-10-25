# 6. Privacy & redaction — the cloud boundary

**Job:** make sure your personal details never leave the machine in readable
form. The cloud gets the *shape* of your data, not your identity.

**Files:**
- `core/privacy/redactor.py` — the scrubbing engine
- `core/privacy/profile.py` — your household identity (the "known names")
- `core/privacy/cloud_boundary.py` — the scrub step applied to search results
- `core/privacy/recognizers.py` — the PII detection patterns

## The mental model: tokenize → send → detokenize

```
"Pay rent to Jamie Lee, acct 1234567"
        │  (still on your machine)
        ▼  redact()
"Pay rent to [PARTNER], acct [BANK_ACCOUNT_1]"
        │
        ▼  sent to the cloud — Claude only ever sees this
Claude reasons over the tokens and replies using them
        │
        ▼  detokenize()  (back on your machine)
"...pay rent to Jamie Lee..."   ← what you actually see
```

The mapping from real value → token lives only in a **session-scoped token
store** on your machine. The cloud never receives the key.

## How the scrubber finds PII (three passes)

1. **Known names first.** Your household profile (your name, aliases, partner,
   street, city) is pre-loaded, so those always tokenize the same way. Also
   catches peer-to-peer payment names (Venmo, Zelle, Cash App, "Pay to the order of…").
2. **AI detection (Presidio + spaCy).** Catches the rest — other people's names,
   SSNs, phone numbers, emails, account/routing numbers, addresses, dates of birth.
3. **Regex sweep.** A final deterministic pass for structured PII the NER might
   miss (email formats, SSN-shaped numbers, ZIP codes, IP addresses).

This belt-and-suspenders approach means a miss in one pass is usually caught by
another.

## Where redaction happens

- **During extraction.** Statement text is scrubbed before being sent to Claude.
  (See [Extraction](01-extraction.md).) Statements are ALL-CAPS, so the engine
  lowercases for the NER step to read better.
- **During chat search.** Email and message excerpts are scrubbed right before
  they cross the network, and the final answer is un-scrubbed before display.
  (See [Chat & tools](05-chat-and-tools.md).) Chat text keeps its natural casing.

## Why your raw data is mostly safe by default

- **Emails and messages are never sent to the cloud during ingestion.** They're
  read, embedded into numbers locally, and stored on-device. Only small,
  redacted *excerpts* are ever sent — and only when a chat question pulls them up.
- The Presidio model is warmed up at server startup so the first redaction isn't slow.
