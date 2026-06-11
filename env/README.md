# Smart-Room Demo Environment (Member B)

Runnable vertical-slice testbed for the modular action system. Replaces the
earlier custom Flask mock with a **W3C-compliant node-wot servient** (advisor
§9.1) plus a **React** booking/control dashboard, so perception is exercised
against real Thing Descriptions and a real DOM.

```
docker compose -f env/docker-compose.yml up --build
```

| Service      | URL                              | Role                                   |
|--------------|----------------------------------|----------------------------------------|
| dashboard    | http://localhost:3000            | React booking UI (DOM backend surface) |
| wot_server   | http://localhost:8080/<thing>    | node-wot Things + TDs (WoT backend)    |
| control      | http://localhost:8081            | failure-injection control plane        |

## Devices (advisor §3.1 concrete device list)

`thermostat` (apikey-secured), `lights`, `projector`, `blinds` (basic-auth),
`occupancy` (read-only sensor — the physical-state source for the booked-vs-
occupied conflict in advisor §13.1). Their canonical TDs live in
[`config/wot_td/`](../config/wot_td) and are parsed **at runtime** by
`src/perception/td_affordance_parser.py` (no hard-coded endpoints).

## Perception & action surfaces

- **DOM** — the agent attaches an isolated Playwright context
  (`src/perception/browser_session.py`, the web analogue of PiP isolation) and
  runs the DOM Transducer over the live page → Page Affordance Model.
- **WoT** — `td_affordance_parser` ingests each TD's forms/href/method,
  `securityDefinitions`, and rate limits; `wot_executor` invokes them.
- **Visual** — `som_parser` overlays numbered marks on the screenshot; the VAM
  selects a `mark_id`, never a raw coordinate.

## Failure injection (Chaos Monkey, advisor §11.1)

WoT side (control plane on :8081):

```bash
curl -XPOST localhost:8081/failure -d '{"thing":"thermostat","type":"timeout","delay_ms":1500}'
curl -XPOST localhost:8081/failure -d '{"thing":"thermostat","type":"postcondition_mismatch"}'
curl -XPOST localhost:8081/failure -d '{"thing":"lights","type":"offline"}'
curl -XPOST localhost:8081/reset
```

DOM side (browser): `?fault=layout_shift,selector_mutation,stale_temperature`
in the dashboard URL, or `window.__injectFault("selector_mutation")` from
Playwright. See `scripts/inject_failures.py` for the driver that maps each
fault to the recovery tier it should trigger.

> Production note: node-wot is the canonical WoT backend. The control plane and
> dashboard fault hooks exist only for evaluation and are disabled by `/reset`.
