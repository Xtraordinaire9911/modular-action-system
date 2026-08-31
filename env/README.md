# Smart-Room Demo Environment

Runnable vertical-slice testbed for the modular action system. It combines a
node-wot servient with a React booking/control dashboard, so perception is
exercised against real Thing Descriptions and a real DOM.

```bash
docker compose -f env/docker-compose.yml up --build
```

| Service | URL | Role |
| --- | --- | --- |
| dashboard | http://localhost:3000 | React booking UI, the DOM backend surface |
| wot_server | http://localhost:8080/<thing> | node-wot Things and TDs, the WoT backend |
| control | http://localhost:8081 | failure-injection control plane |

## Devices

The live node-wot servient exposes `thermostat`, `lights`, `projector`,
`blinds`, and `occupancy` with `nosec`. This is intentional: the Thingweb HTTP
binding used by this demo only exposes `nosec` Things reliably. The parser and
evaluator still cover `apikey` and `basic` security schemes through
`config/wot_td/` fixtures and unit tests.

The important demo property remains true: endpoints are not hard-coded into the
agent. `src/perception/td_affordance_parser.py` parses TD forms, hrefs, methods,
schemas, rate limits, and security metadata at runtime.

## Perception And Action Surfaces

- DOM: the agent attaches an isolated Playwright context
  (`src/perception/browser_session.py`) and runs the DOM Transducer over the live
  page to produce a Page Affordance Model.
- WoT: `td_affordance_parser` ingests TD forms/href/method/security/rate-limit
  metadata; `wot_executor` invokes the resulting affordances.
- Visual: `som_parser` overlays numbered marks on a screenshot; the VAM selects
  a `mark_id`, never a raw coordinate.

## Failure Injection

The complete five-scene live campaign, including the Runtime entrypoint and
independent acceptance oracles, is documented in
[`SMART_ROOM_FIVE_RECOVERY_DEMO.md`](../SMART_ROOM_FIVE_RECOVERY_DEMO.md).

WoT side, via the control plane on port 8081:

```bash
curl -XPOST localhost:8081/failure -d '{"thing":"thermostat","type":"timeout","delay_ms":1500}'
curl -XPOST localhost:8081/failure -d '{"thing":"thermostat","type":"postcondition_mismatch"}'
curl -XPOST localhost:8081/failure -d '{"thing":"lights","type":"offline"}'
curl -XPOST localhost:8081/reset
```

DOM side:

```text
http://localhost:3000/?fault=layout_shift,selector_mutation,stale_temperature
http://localhost:3000/?fault=stale_temperature&stale_offset=-1.5&source_reliability={"dom":0.55,"wot":0.85}
```

or from Playwright:

```javascript
window.__injectFault("selector_mutation")
```

See `scripts/inject_failures.py` for the mapping from fault type to expected
recovery tier.

Fine-grained WoT-side ambiguous fusion hooks:

```bash
curl -XPOST localhost:8081/failure \
  -H 'Content-Type: application/json' \
  -d '{"thing":"thermostat","type":"timeout","read_delay_ms":450,"source_reliability":{"dom":0.6,"wot":0.9}}'

curl -XPOST localhost:8081/failure \
  -H 'Content-Type: application/json' \
  -d '{"thing":"thermostat","type":"offline","drop_probability":0.7,"source_reliability":{"dom":0.65,"wot":0.35}}'
```
