# Turning on the real vision model, cheaply

The agent's visual check works without any model — it reports `unavailable` and
the run verifies from the DOM alone. This is how to give it a real vision model,
and what it costs.

---

## 1. Which provider, and why

| provider | vision? | input price | verdict |
| --- | --- | --- | --- |
| **Alibaba Model Studio — `qwen-vl-plus`** | yes | **$0.137 / M tokens** | **use this** |
| Zhipu — `glm-4v-flash` | yes | very low | fallback |
| OpenAI — `gpt-4o-mini` | yes | $0.15 / M, but bills ~36,800 tokens per image | fallback |
| Anthropic — `claude-sonnet-5` | yes | $2.00 / M | last resort, ~15× the cost |
| **DeepSeek** | **no** | — | **cannot be used at all** |

**DeepSeek is out on capability, not price.** Its API accepts text only, so it
cannot answer a question about a screenshot at any price. That is worth stating
because it is the first provider anyone reaches for on cost.

**Alibaba wins on two measured facts**, not on preference:

- `qwen-vl-plus` bills input at about **a fifteenth** of Claude Sonnet's rate.
- A new Model Studio account in the **Singapore (International) region** gets
  **1,000,000 input + 1,000,000 output tokens free, valid 90 days**, with no
  spending required.

Its endpoint is OpenAI-compatible, so this repository needed **no new vendor
code** — `OpenAIVisionClient` already accepts a `base_url`.

## 2. What it will actually cost

Measured on this repository, not estimated:

| | tokens per vision call |
| --- | --- |
| full-page screenshot (the naive way) | ~1,334 image + ~170 text |
| **the region the goal names** (what the code does) | **~27 image + ~170 text** |

A 49× reduction, measured: the cart region is 240×80 and 2 KB; the full page is
1280×800 and 111 KB. Sending the region is also the *better question* — the model
is shown the cart instead of having to find it.

So one vision call ≈ **200 input + 50 output tokens**:

```
qwen-vl-plus:  200 × $0.137/M  +  50 × $0.410/M  ≈  $0.000048  ≈  €0.00004
```

| scenario | vision calls | cost |
| --- | --- | --- |
| one episode | 1 | €0.00004 |
| `--suite` (7 utterances, 6 reach a browser) | 6 | €0.0003 |
| 500 episodes of iteration over three weeks | 500 | **€0.02** |
| the free grant, in episodes | ~5,000 | **€0** |

**The three-week budget of €9 is not the constraint.** The free grant alone covers
roughly 5,000 episodes; if it expired tomorrow the whole schedule would still be
a few cents. Three spend guards make a runaway loop impossible:

- **one paid call per episode** (`max_calls`), so the runtime observing several
  times does not bill several times;
- **identical pixels + identical question are never billed twice** (cached by
  screenshot digest);
- an exhausted ceiling is reported as `budget_exhausted`, not silently skipped.

## 3. Getting a key — step by step

You need a phone number and an email. **No payment method is required for the
free grant**, but the console may still ask you to complete account verification.

1. Go to **https://www.alibabacloud.com** and click **Free Account** (top right).
   Sign up with your email. This is the *International* site — the `.cn` site is
   a separate account system and its free grant does not apply here.
2. Verify your email and phone number when prompted.
3. Once signed in, open **Model Studio**:
   **https://modelstudio.console.alibabacloud.com**
4. Top right, make sure the region says **Singapore**. If it says anything else,
   switch it. *The free grant only exists in Singapore.*
5. Click **Activate** / **Get Started** if it asks you to enable Model Studio.
   This is the moment the 90-day clock starts.
6. In the left sidebar open **API Keys** (sometimes under *API Reference* or your
   account menu), then **Create API Key**.
7. Copy the key. It looks like `sk-` followed by a long string. **You will not be
   shown it again** — if you lose it, delete it and make a new one.

If you want to check the grant: Model Studio console → **Billing** or **Resource
Package**, look for the free token quota and its expiry.

## 4. Giving the key to the project — safely

**Do not paste the key into a chat, a terminal, a commit, or a message.** Anything
you paste it into is a place it now has to be rotated from.

Instead, create one file in the repository root:

```
.env.local
```

with exactly one line in it:

```
DASHSCOPE_API_KEY=sk-your-key-here
```

That is all. `.gitignore` already excludes `.env.local`, and
`src/config/secrets.py` reads only an allowlist of known names out of it, sets
them in the process environment, and returns **only the names** — never the
values — so a key cannot end up in a log line.

**How to create the file on Windows without a text editor mangling it:**

1. Open the project folder in VS Code.
2. Right-click in the file list → **New File** → name it `.env.local` exactly
   (with the leading dot, no `.txt`).
3. Paste the one line above and save.

Then check it took effect — this prints the *name*, never the key:

```bash
python -c "from src.config.secrets import configured_key_names; print(configured_key_names())"
```

Expected: `['DASHSCOPE_API_KEY']`

## 5. Running it

```bash
python scripts/run_intent_episode.py
```

With the key configured the run prints something like:

```
  runtime   : state=completed verified=True (goal completed)
  vision    : qwen-vl-plus answered True at confidence 0.91 on screenshot 3f2a1c...
              the cart lists one item
  cart.holds_item observed: False -> True
  result    : GOAL REACHED
```

Without it, the same run prints `vision : not used (unavailable: no vision client
configured)` and still reaches the goal from DOM evidence alone. Both are correct
outcomes; the run says which one happened.

Every call, billed or not, is appended to `artifacts/vlm_observer/calls.jsonl`,
so the spend is auditable after the fact.

## 6. Overriding the provider

To use a different OpenAI-compatible endpoint without touching code, put these in
`.env.local`:

```
VLM_API_KEY=sk-...
VLM_MODEL=qwen-vl-max
VLM_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

The same three exist for the text (intent) path: `LLM_API_KEY`, `LLM_MODEL`,
`LLM_BASE_URL`.

## 7. If a call fails

| message | meaning |
| --- | --- |
| `unavailable: no vision client configured` | no key found — check `.env.local` spelling and location |
| `credit balance is too low` | the account has no credit or grant. **No charge is incurred** — the request is refused before the model runs |
| `budget_exhausted` | the per-run ceiling was hit. Not an error; raise `max_calls` if you meant to |
| `low_confidence` | the model answered but was unsure, so its answer was not used as evidence. What it said is still recorded |

None of these fail the run. The visual check is a second source, so its absence
degrades the episode to DOM-only verification rather than breaking it.

## Sources

- Alibaba Model Studio free quota for new users:
  https://www.alibabacloud.com/help/en/model-studio/new-free-quota
- OpenAI-compatible endpoint and base URL:
  https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope
- Model pricing: https://help.aliyun.com/zh/model-studio/model-pricing
- DeepSeek is text-only (no image input in the public API), confirmed August 2026.
