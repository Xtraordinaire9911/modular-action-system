# External CUA / Web Benchmark Environments

This repo keeps the existing smart-room WoT demo as a fast local smoke test, but real CUA claims should be demonstrated against external benchmark environments.

The supported external env manifest is:

```text
env/external_benchmarks.yaml
```

Third-party checkouts are installed under:

```text
.external_envs/
```

That directory is intentionally gitignored.

## Supported Environments

- `playwright`: browser execution substrate and isolated CUA session layer.
- `browser_use`: reference web-agent framework.
- `miniwob_plusplus`: lightweight System 1 reflex benchmark.
- `webarena`: realistic self-hostable web benchmark.
- `visualwebarena`: multimodal visually grounded web benchmark.
- `osworld`: desktop/OS-level CUA benchmark.
- `skyvern`: industrial web automation reference.
- `lavague`: natural-language-to-browser-action reference.

## Commands

List supported envs:

```powershell
uv run python scripts/external_envs.py list
```

Dry-run install commands:

```powershell
uv run python scripts/external_envs.py bootstrap webarena visualwebarena osworld
```

Actually clone/bootstrap selected envs:

```powershell
uv run python scripts/external_envs.py bootstrap miniwob_plusplus webarena visualwebarena --execute
```

Check which external envs are present:

```powershell
uv run python scripts/external_envs.py check miniwob_plusplus webarena visualwebarena
```

Print how to visibly drive the agent on an env:

```powershell
uv run python scripts/external_envs.py run miniwob_plusplus webarena
```

> Full copy-paste setup + visual-run instructions (per environment, Windows/uv):
> see [`env/RUNBOOK_external_envs.md`](RUNBOOK_external_envs.md). The unifying
> path is `scripts/run_agent_on_env.py --url <any task/site URL> --headed`.

## Practical Demo Order

For a reliable live presentation:

1. Keep the local smart-room demo as the fast smoke test:

```powershell
docker compose -f env/docker-compose.yml up -d --build
uv run python run_demo.py --probe-env --live-agent --headed --step-delay 2 --pause-at-end
```

2. Show external env readiness:

```powershell
uv run python scripts/external_envs.py list
uv run python scripts/external_envs.py check miniwob_plusplus webarena visualwebarena osworld
```

3. If the upstream env has already been cloned, open or run its own official start script from `.external_envs/<env>`.

## Why Not Vendor These Envs?

WebArena, VisualWebArena, and OSWorld are real benchmark projects with their own Docker images, datasets, VM/cloud requirements, and setup scripts. Vendoring them into this repository would make merges fragile and bloat the project. The stable integration point is a manifest + bootstrap/check layer.

