# A Modular Action System Architecture

TUM Praktikum Automatic Agents — Topic Area 5.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
pytest --tb=short -q
```

## Repository layout

```
src/
  contracts/      shared dataclasses (SkillTuple, Affordance, ExecutionResult, …)
  planner/        LLM + deterministic planner wrappers              [Member A]
  skill_library/  CRUD registry for SkillTuple objects             [Member A]
  perception/     DOM transducer, WoT TD parser, SoM parser        [Member B]
  effectors/      DOM / WoT / Visual executors, System-1 reflexes  [Member B]
  backend_router/ confidence scoring and routing logic             [Member B+C]
  vam/            VAM adapter and recovery payload                 [Member B]
  runtime/        Cognitive Map, Continuous Interaction Manager    [Runtime]
  verification/   pre/postcondition checkers, conflict detector    [Runtime]
  recovery/       four-tier recovery cascade                       [Runtime]
  safety/         rate limiter, unsafe-action detector             [Runtime]
config/
  default.yaml    runtime knobs (lambdas, timeouts)
  skills_seed.json  initial five smart-room skills
  wot_td/         W3C Thing Description JSON-LD stubs
tests/
env/
  react_dashboard/   React booking UI              [Member B]
  node_wot_server/   node-wot device server        [Member B]
```

## Branch model

`main` — production-ready only, merged via PR with CI green  
`develop` — integration branch, feature branches merge here  
`feature/<id>-<slug>` — one feature per branch, branched from develop
