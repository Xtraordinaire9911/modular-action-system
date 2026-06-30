# Runbook: Install Mainstream CUA/Web Envs and Run the Agent Visibly

Goal of this document: from a clean Windows machine, install the mainstream
research/industrial agent environments and **watch our action system drive
them in a visible browser**, step by step.

The one idea that ties everything together:

> Any environment that exposes a **URL** can be driven by our headed runner
> `scripts/run_agent_on_env.py`. It launches a visible Chromium (isolated
> Playwright context = CUA session isolation), perceives the page with the DOM
> Transducer, picks an affordance with the System-1 reflex policy, and clicks /
> types — one step at a time, with screenshots.

Environments are grouped by how heavy they are. Do **Tier A first** — it runs on
your laptop today. Tiers B/C need Docker / a VM and are documented honestly.

All commands are PowerShell, run from the repo root:
`d:\workspace\...\A-Modular-Action-System-Architecture`.

---

## 0. One-time prerequisites

```powershell
# 0.1 Python toolchain (uv manages its own Python + venv)
uv --version

# 0.2 Install the project + a real browser engine for headed runs
uv venv                      # creates .venv if you don't have one yet
uv run playwright install chromium

# 0.3 Sanity: the whole test suite must pass
uv run pytest -q
```

Optional (only for the heavier tiers):

- **Docker Desktop** (WebArena / VisualWebArena) — needs WSL2 + lots of disk.
- **Git** (cloning upstream repos) — `git --version`.
- **Node 20+** (only for the local smart-room dashboard) — `node --version`.

Disk/RAM reality check: MiniWoB++ ≈ tens of MB. WebArena site images ≈ tens of
GB. OSWorld VM ≈ tens of GB + virtualization. Plan accordingly.

---

## 1. Browse what's available

```powershell
uv run python scripts/external_envs.py list                 # all envs + roles
uv run python scripts/external_envs.py run miniwob_plusplus  # how to drive it
uv run python scripts/external_envs.py check --all           # what's installed
```

`bootstrap` prints install commands (dry-run); add `--execute` to actually run:

```powershell
uv run python scripts/external_envs.py bootstrap miniwob_plusplus            # dry-run
uv run python scripts/external_envs.py bootstrap miniwob_plusplus --execute  # do it
```

Everything third-party installs under `.external_envs/` (gitignored).

---

## TIER A — Runs on your laptop today, fully visual

### A1. Smart-room (fastest sanity check — already in the repo)

```powershell
docker compose -f env/docker-compose.yml up -d --build
uv run python run_demo.py --probe-env --live-agent --headed --step-delay 2 --pause-at-end
```

A visible browser prepares Room A end-to-end. Use this to confirm headed mode
works before touching external envs.

You can also drive the dashboard with the generic external runner:

```powershell
uv run python scripts/run_agent_on_env.py --url http://localhost:3000 --goal "Book Room" --headed --pause-at-end
```

### A2. MiniWoB++ (the research benchmark — lightweight, no Docker) ⭐ start here

**Install:**

```powershell
git clone https://github.com/Farama-Foundation/miniwob-plusplus.git .external_envs/miniwob-plusplus
uv pip install miniwob
uv run python -c "import miniwob; print('miniwob import ok')"
```

**▶ Run the full cross-environment fancy demo (recommended — best for live presentations):**

Spans MiniWoB++ academic tasks **and** three WebArena-style local mock environments
(shopping, email, forum) in a single browser session. Each step is animated with a
periwinkle (#8383ff) arrow cursor, a glowing trail, and an element highlight.
Prints a colour-coded M1 cross-environment generalisation score table at the end.

```powershell
uv run python scripts/run_fancy_demo.py --headed --step-delay 1.3
# Mock envs only (no MiniWoB++ clone needed):
uv run python scripts/run_fancy_demo.py --headed --step-delay 1.3 --skip-miniwob
```

- `--step-delay 1.3` — pause between visible actions (adjust to taste).
- `--skip-miniwob` — skip MiniWoB++ group; mock envs need only `playwright install chromium`.
- `--headed` — opens a real Chromium window (default; `--headless` to suppress).
- `--pause-between-groups` — waits for Enter between env groups (good for narrated demos).
- `--pause-at-end` — hold browser open after all tasks complete.

**▶ MiniWoB++ only (six curated tasks, no mock envs):**

```powershell
uv run python scripts/run_miniwob_demo.py --step-delay 1.4 --pause-between --headed
```

- `--step-delay 1.4` — pause 1.4 s between visible actions (adjust to taste).
- `--pause-between` — waits for Enter between tasks (good for live demos).
- `--headed` — opens a real Chromium window (default; use `--headless` to suppress).
- `--tasks enter-text login-user` — run only specific task stems (default: all 6).

A per-task success table is printed at the end. Per-step screenshots land in
`eval_outputs/external_runs/<timestamp>/`.

**Run OUR agent on a single task (lower-level, generic runner):**

```powershell
# See which tasks exist (filenames you can target)
Get-ChildItem .external_envs/miniwob-plusplus/miniwob/html/miniwob | Select-Object -First 30 Name

# Serve the task dir on an auto-picked FREE port and drive it in one shot.
# --serve avoids the Windows reserved-port error (WinError 10013) you can hit
# with a manual `python -m http.server 8000` when Docker/Hyper-V reserve ports.
uv run python scripts/run_agent_on_env.py --serve .external_envs/miniwob-plusplus/miniwob/html --path miniwob/click-button.html --goal "click button ONE" --headed --pause-at-end
```

You will see Chromium open, the agent enumerate affordances, then click.

> Manual two-terminal alternative (only if you prefer it): serve with a port the
> OS allows, e.g. `uv run python -m http.server 8123 --directory .external_envs/miniwob-plusplus/miniwob/html`,
> then `--url http://127.0.0.1:8123/miniwob/click-button.html`. Check which ports
> Windows reserves with `netsh interface ipv4 show excludedportrange protocol=tcp`.

Useful flags: `--value "Label=text"` to type into an input, `--success-text done`
to declare success when that text appears, `--max-steps N`, `--headless`.

**Official visual sanity (their own renderer, no DOM stack):**

```powershell
uv run python -c "import gymnasium, miniwob; e=gymnasium.make('miniwob/click-test-v1', render_mode='human'); e.reset(); import time; time.sleep(8); e.close()"
```

### A3. Any live website (industrial-style demo)

The runner works on real sites too — great for showing generalization:

```powershell
uv run python scripts/run_agent_on_env.py --url "https://www.saucedemo.com/" --goal "login" --value "Username=standard_user" --value "Password=secret_sauce" --headed --pause-at-end
```

(Pick any page; provide `--value Label=...` for the inputs you want filled and a
`--goal` that overlaps the button label.)

---

## TIER B — WebArena / VisualWebArena (Docker, heavy, self-hosted)

These are **realistic** web benchmarks: you self-host full website containers,
then evaluate. Full setup is large; do this on a workstation/server, not a thin
laptop.

**Install the harness:**

```powershell
uv run python scripts/external_envs.py bootstrap webarena --execute
# clones .external_envs/webarena and installs its Python requirements
```

**Host a site + run the agent visibly:**

1. Follow the upstream README section "Environment Setup" in
   `.external_envs/webarena` to `docker load` and `docker run` a site image.
   Example outcome: the Shopping site served at `http://localhost:7770`.
2. Point the headed runner at that URL:

```powershell
uv run python scripts/run_agent_on_env.py --url "http://localhost:7770" --goal "search product" --headed --pause-at-end
```

VisualWebArena is the same flow (`bootstrap visualwebarena --execute`, host its
sites, point the runner at the site URL). Its tasks are image-grounded, which is
exactly where our Set-of-Marks visual fallback is relevant.

> Note: WebArena's *own* evaluation harness runs headless for scoring. Our headed
> runner is the **visualization / qualitative demo** layer on top of the same
> hosted sites. For official scores, use their harness; for "watch it act", use
> ours.

---

## TIER C — OSWorld (desktop/OS-level, VM, heaviest)

OSWorld evaluates on a **real desktop OS inside a VM**, so visualization is the
VM viewer / VNC, not a web browser.

```powershell
uv run python scripts/external_envs.py bootstrap osworld --execute
```

Then follow upstream docs in `.external_envs/OSWorld` to provision the desktop
image (VMware/VirtualBox/Docker provider) and connect via its viewer/VNC to
watch actions. This needs virtualization + tens of GB and is out of scope for a
quick laptop demo — included for completeness and future work.

---

## Reference frameworks (run side-by-side, optional)

Not benchmarks, but useful baselines you can also run visually on live sites:

```powershell
uv run python scripts/external_envs.py bootstrap browser_use skyvern lavague --execute
```

Start each per its own README and compare its behavior against our agent on the
same page.

---

## Where results go + cross-env metric

- Per-step screenshots: `eval_outputs/external_runs/<timestamp>/`.
- Aggregate multiple runs into the generalization metric (M1 = per-env / overall
  Task Success Rate) with `evaluation/cross_env_eval.py` (`aggregate` / `write`),
  which emits `eval_outputs/cross_env/cross_env_results.json`.

---

## Troubleshooting

- **`Python was not found` / Microsoft Store alias** — always prefix with `uv run`
  (e.g. `uv run python ...`); don't call bare `python`.
- **Browser doesn't appear** — you passed `--headless`, or Chromium isn't
  installed: `uv run playwright install chromium`.
- **`uv pip install` says no environment** — run `uv venv` once first.
- **`ruff`/`black`/`pytest` "program not found"** — replacing `.venv` drops the
  dev tools; restore them with `uv pip install -e ".[dev]"`.
- **`WinError 10013` on `python -m http.server 8000`** — that port is in a
  Windows reserved range (Docker/Hyper-V/WinNAT). Use the runner's `--serve`
  (auto free port), or pick a port outside the ranges shown by
  `netsh interface ipv4 show excludedportrange protocol=tcp`.
- **`ModuleNotFoundError: No module named 'src'`** — fixed: scripts bootstrap
  the repo root onto `sys.path`; re-pull if you still see it.
- **Port already in use** (8000/7770/3000) — pick another port and update the URL.
- **Agent clicks the wrong control** — tune `--goal` so its words overlap the
  target's label, or use `--value "Label=text"` to address inputs explicitly.
- **MiniWoB HTML path differs** — list the folder (step A2.2) and use a real
  filename from `.external_envs/miniwob-plusplus/miniwob/html/miniwob`.
```
