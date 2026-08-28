# Talk sections, one looping GIF each

Cut from `artifacts/llm_demo/llm-vs-rules-smartroom.mp4` (the recorded run, 20 Aug)
by `scripts/make_talk_gifs.py`. Each one loops, so it plays for as long as that
part of the talk takes. 960 px wide at 10 fps; regenerate smaller with
`python scripts/make_talk_gifs.py 720 8`.

**The GIFs themselves are not in git** — 10 MB of frames derived from an mp4 that
already is. Run `python scripts/make_talk_gifs.py` to get them back; it takes
about a minute.

Every figure named below is on screen in that GIF — nothing here is a claim that
has to be taken on trust while it plays.

Same panel in all four scenes: the **rules** run in the left column and the
**model** in the right, on the same sentence at the same time; the **text
oracle** sits under them; the **vision model** below that, with the image bytes
it was actually sent; and running totals along the bottom.

---

## 1 — `1-control-rules-and-model-agree.gif` (26 s)

**Said:** "book room A at 14:00" · **surface:** dashboard (DOM)

The control, and the place to introduce the layout. One of the thirteen patterns
matches, so the rules produce `room_booked` on their own; the model, given the
identical sentence, produces the same goal. **On a sentence written to match a
keyword, the model earns nothing** — say that plainly here, because it is what
makes the next three scenes mean something.

Worth pointing at while it runs: the raw JSON in the right column is the model's
actual reply, with its latency and the provider's own token counts, not a
paraphrase.

## 2 — `2-model-interprets-what-rules-cannot.gif` (24 s)

**Said:** "I need somewhere to present at 15:00, room B please" · **surface:** dashboard (DOM)

Same intent, no keyword. The left column ends at **0 of 13 patterns matched — no
goal**; the right returns `room_booked {"room": "B", "time": "15:00"}`.

The number to give here is the measured one, not this single case: over nine
requests phrased this way the rules score **0/9** and the model **9/9**, with no
regression on the two the rules already handled and 4/4 correct refusals
(`scripts/eval_model_value.py`, `artifacts/model_value/model_value_report.json`).

## 3 — `3-goal-the-page-cannot-reach-wot.gif` (27 s)

**Said:** "it's too cold, put it at 22 please" · **surface:** device (WoT)

The one that leaves the browser. No control on the dashboard can set a
temperature, so the target is resolved from the **Thing Descriptions discovered
at runtime** on `:8082` — `thermostat.targetTemperature` — written over HTTP, and
then *waited on*: the setpoint changes at once, the measured temperature ramps,
and the dashboard only shows it afterwards.

Two things to say over it: nothing in the code names that endpoint (a test
asserts no binding contains a URL or a port), and **commanded is not measured** —
the agent waits for the room to report having arrived, which is what makes a dead
lamp or a jammed motor detectable at all.

## 4 — `4-dashboard-lies-vision-catches-it.gif` (24 s)

**Said:** "hold room C for me at 16:00" · **surface:** dashboard (DOM)

The decisive one. The booking goes through, then the confirmation is painted over
on screen while staying in the DOM. The text oracle reports **`'booked: room c'
found — goal reached`**. The crop handed to the vision model is blank, it answers
**false at confidence 1.00** — "the image is blank and shows no text or details" —
and the run ends in **CONFLICT** instead of a success.

The line that lands: *every* text-based check in this repository passes here,
including the one the agent normally trusts. Only looking catches it.

## 5 — `5-run-complete-scoreboard.gif` (3 s)

**rules 1/4 · model 4/4 · 1 false success caught · 8 model calls.**

Short on purpose — it is a closing card, not a scene. The totals were
accumulating in the footer throughout the run, so they are a tally the audience
watched being counted rather than a number produced at the end.
