# Yixin Runtime-Control TODO Plan

> 基线：`origin/develop@e7e785a`  
> 输入：当前代码审查、`Project Review - Team 2 - July 21.pdf`、2026-07-17 supervisor meeting notes  
> 目标：只安排尚未完成的 runtime/control/fusion/recovery/adaptation 工作；已完成项不重复包装为 TODO。

## 1. 最终验收目标

最终必须有一个真实 tracer-bullet task 满足：

```text
GoalSpec
-> observe live DOM/WoT/Visual outputs
-> update canonical CognitiveMap
-> fuse evidence / active perception if needed
-> plan one typed action
-> validate and execute
-> re-observe
-> verify expected effect
-> on failure actually retry/reroute/rollback
-> continue until completed/escalated/failed
-> persist transitions, recovery trace and measured metrics
```

验收不是“某个类存在”，而是同一次真实 episode 的 trace 能证明每一步确实发生。

## 2. 优先级与责任标记

| 标记 | 含义 |
|---|---|
| `Yixin` | 可直接在 runtime/control 模块完成 |
| `Shared` | Yixin 定义/接入 runtime 边界，但需要队友提供感知、执行、环境或 release 配合 |
| `External owner` | 不由 Yixin 实现，仅跟踪接口和集成阻塞 |

## 3. P0：先保证老师能看到正确版本

### P0-1 发布 verified develop 到 main

- **Owner**：Shared，repo owner/team lead 发起 release PR，Yixin提供 runtime evidence。
- **原因**：`main` 比 `develop` 少约 9,000 行；教师评价里的多项 “not in main” 在 release 层面仍可能重现。
- **动作**：
  - 从 `develop` 创建到 `main` 的 release PR。
  - CI 全绿后再 merge，不直接 push protected main。
  - PR 描述列出 planner、runtime integration、adaptation、tests 的具体 code/test evidence。
- **验收**：`main` commit 包含 PR #53–#55；clean clone tests 与 develop 一致。

### P0-2 建立 claim/evidence 状态页

- **状态（2026-07-21）**：已新增 `STATUS.md`，区分 implemented/partial/future，并列出 code/test/live evidence。
- **Owner**：Shared；Yixin负责 runtime 部分。
- **动作**：新增或更新 `STATUS.md`，每个 claim 标注 `implemented / partial / future`、code location、test/demo evidence。
- **必须纠正**：
  - README 的 `115 passing` 与当前分支说明。
  - browser isolation 不再称完整 PiP。
  - bounded planner 不称 LLM/NL planner。
  - recovery decision 不称 recovery executed。
  - synthetic metrics 不称 live measurement。
- **验收**：老师无需猜测应检查哪个 branch，也不会把 future claim 当现有实现。

## 4. P1：真正闭合 recovery execution loop

### P1-1 实现 episode-level control loop

- **状态（2026-07-21）**：runtime 实现完成；live tracer-bullet 验证归 P2-5/P2-6。
- **Owner**：Yixin。
- **目标文件**：`src/runtime/continuous_interaction_manager.py`，必要时新增 `src/runtime/control_loop.py`。
- **设计**：
  - 一次只 plan/execute 一个 primitive action。
  - 每一步后调用 observation provider 获取 fresh observation。
  - 每一步重新 update map、fuse、validate expected effect，再决定继续或 replan。
  - episode 有明确 `max_steps`、deadline、retry budget、per-backend budget 和 terminal state。
  - durable skill 和 no-durable-skill 共用同一个 step execution/recovery engine。
- **Bad cases**：executor exception、timeout、partial state mutation、stale observation、affordance disappearance、duplicate action、non-idempotent action、budget exhaustion、human cancellation。
- **验收测试**：
  - action 1 改变页面后，action 2 使用新 observation 而不是旧 PAM。
  - affordance 消失时 replan 或 escalate，不继续执行 stale id。
  - max-step/deadline 到达时稳定终止，不无限循环。

### P1-2 实际执行 RecoveryCascade 的选择

- **状态（2026-07-21）**：已完成。retry、reroute、rollback 都由 CIM 实际调用 executor，并在 re-observe/fusion/postcondition 后决定结果。
- **Owner**：Yixin。
- **目标文件**：CIM、`src/recovery/recovery_cascade.py`、runtime router。
- **动作语义**：
  - `retry`：同 backend 重新观察后重试；只对 transient/idempotent action 生效。
  - `reroute`：通过 `RecoveryRoutingContext` 排除已失败 backend，选择 alternative affordance/backend 并真实执行。
  - `rollback`：执行 `RollbackSpec` 或 checkpoint restore，随后 re-observe 并验证状态恢复。
  - `escalate_human`：进入 terminal/paused state，输出清楚的 evidence 和可恢复上下文。
- **安全约束**：unsafe、irreversible、blocking conflict 不得因为 recovery 绕过 gate。
- **验收测试**：
  - DOM selector failure 后 visual/alternative DOM 实际被调用，最终 state 可为 COMPLETED。
  - WoT timeout 后 retry 次数和 backoff 可观察。
  - postcondition failure 后 rollback executor 被调用，并验证 before state 恢复。
  - 所有 recovery attempt 都进入同一 trace。

### P1-3 修复 runtime state semantics

- **状态（2026-07-21）**：已完成。`RECOVERING` 仅为执行中的内部状态，terminal result 显式携带 recovery/final verification 字段。
- **Owner**：Yixin。
- **动作**：
  - precondition failure 不再无条件标 tier 4。
  - `RECOVERING` 只表示 recovery 正在执行；决定完但未执行不能伪装成 recovered。
  - 区分 `RECOVERY_SELECTED`、`RECOVERY_FAILED`、`ESCALATED` 或用 episode event 表达，不必无限扩 enum。
  - `RuntimeStepResult` 增加 `recovery_attempted`、`recovery_succeeded`、`final_outcome_verified` 等不可混淆字段。
- **验收**：metrics 只从实际结果计算，不从 tier label 推断成功。

## 5. P1：修复 fusion 输入真实性

### P1-4 修复 D3 execution provenance

- **状态（2026-07-21）**：已完成。delta 按显式 source 或 backend inference 写入 DOM/WoT/Visual channel，并记录 attempt/transition provenance。
- **Owner**：Yixin。
- **动作**：
  - `record_execution_result()` 依据 `backend_used` 写入对应 channel，或让 `ExecutionResult` 显式携带 observation source。
  - DOM delta 不得进入 `device_states/wot`；visual delta 不得伪装成 WoT。
  - 每条 write-back metadata 记录 backend、skill、attempt、transition id。
- **验收**：DOM/WoT/Visual 三种 executor delta 分别产生正确 source 的 StateAssertion；fusion 不会因错误 provenance 制造冲突。

### P1-5 修复 D4 confidence/timestamp ingestion

- **状态（2026-07-21）**：runtime contract/ingestion 已完成；各感知模块提供实测 confidence 仍属共享联调。
- **Owner**：Shared；Yixin定义 contract/ingestion，感知负责人提供真实值。
- **动作**：
  - 定义 `ObservedAssertion` 或 Observation channel payload：value、source、confidence、timestamp、provenance。
  - DOM 使用 selector/visibility confidence，WoT 使用 TD/transport/freshness confidence，Visual 使用 detector/mark confidence。
  - 无 confidence 时明确标记 default provenance，不悄悄声称“confidence weighted”。
  - 避免 `LiveRuntimeObservation.apply_to()` 后 `run_goal()` 再次 ingestion 同一 observation。
- **验收**：同一状态可在 tests 中呈现不同 confidence；fusion support 随真实输入变化。

## 6. P1：统一 map、fusion 和 router

### P1-6 选定 canonical CognitiveMap/EpistemicArbiter/Router

- **状态（2026-07-21）**：已在 `feature/C-009-unify-runtime-architecture` 完成，待 PR/merge。
- **Owner**：Shared；Yixin负责 runtime canonical proposal，规划负责人迁移其调用。
- **已实现**：
  - 保留 `src/runtime/cognitive_map.py` 作为 episode state source of truth。
  - 保留 `src/verification/conflict_detector.py` 作为唯一 runtime fusion implementation。
  - 将 planner scene graph 做成从 runtime map 导出的 view，而不是第二份 state store。
  - 以 `src/runtime/backend_router.py` 作为 canonical routing core；cost-aware 和 legacy API 由 `src/backend_router/router.py` 适配。
  - grounding confidence 保留为 PlanningGate 的独立门控，不再伪装成第二套 sensory arbiter。
- **验收结果**：生产代码只有一个 mutable map state、一套 fusion/arbiter core、一套 routing core；planner view 只读取每个 source 的最新 assertion；219 tests、ruff、black、mypy 和 pipeline smoke 均通过。

### P1-7 让 verifier 消费 fused state

- **状态（2026-07-21）**：已完成。accepted fusion 写入独立 `fused_state`，unqualified condition 优先读取它；blocking fusion 清空 accepted view。
- **Owner**：Yixin。
- **动作**：
  - 将 `FusionDecision.fused_states` 写入独立 `fused_state` view，保留原始 source assertions。
  - condition evaluator 明确支持 `fused.*` 或通过 resolver policy 获取 accepted state。
  - blocking conflict 时 fused value 只能作为候选 evidence，不能绕过 halt。
- **验收**：postcondition 不再由 predicate 作者随意选择 DOM/WoT channel 来“模拟 fusion”。

### P1-8 接入 System1ReflexLibrary 或删除对应 claim

- **状态（2026-07-21）**：已接入。CIM 在 gate 后消费 verified cache，失败时失效；live repeat case 已测得 cache hit、fast path 和 routing latency。
- **Owner**：Shared；Yixin负责 CIM fast-path 接口，执行负责人维护 reflex implementation。
- **动作**：让高 confidence cached affordance 经过 System-1 policy；low confidence/failed verification 回到 System-2/recovery。
- **验收**：真实 runtime trace 能测出 cache hit、fast-path latency 和 System-2 escalation；否则从架构和报告移除该 claim。

## 7. P2：Transition Ledger 与受控长期演化

### P2-1 Abstract state identity

- **状态（2026-07-21）**：runtime 实现完成。state fingerprint 归一化 URL/query/dynamic ids，stable affordance key 优先语义 identity，并过滤 overlay；live TD UUID 映射为稳定 title alias，同时保留原 id provenance。
- **Owner**：Shared；感知负责人提供 stable locator，Yixin消费 state id。
- **动作**：
  - 为 PAM/runtime snapshot 定义 `abstract_state_id`。
  - 归一化 URL 和 dynamic ids；stable affordance key 来自 selector/href/mark identity，不使用 positional counter。
  - demo overlay 节点必须过滤，避免污染 fingerprint。
- **验收**：selector mutation/layout shift 前后可判断“同一抽象任务状态”或“真实状态转移”。

### P2-2 TransitionRecord

- **状态（2026-07-21）**：已完成。每次实际 execute + re-observe + verify 追加 JSONL，并分别保留 executor success、postcondition result、recovery action 和 reversible result。
- **Owner**：Yixin。
- **建议 schema**：
  - task/episode/transition id
  - state id before/after
  - affordance stable key、backend、skill/goal、params
  - observation delta、latency、success、postcondition result
  - recovery tier/action、fault context、reversible result、timestamp
- **写入位置**：每次 execute + re-observe + verify 后，由 control loop 追加 JSONL。
- **验收**：trace 能回答“哪个状态、哪个 action、哪个 backend 导致了什么 verified transition”。

### P2-3 Event/experience linkage

- **状态（2026-07-21）**：已完成。RuntimeStepResult、failure event、transition 和 CompiledExperience 通过 transition/state/affordance ids 关联。
- **Owner**：Yixin。
- **动作**：给 RuntimeStepResult、recovery trace、StateAssertion 和 CompiledExperience 增加 transition id/state id/affordance key。
- **验收**：failure pattern 不再只按 `skill|backend|failure|context` 聚合，而能关联具体状态和已验证 delta。

### P2-4 Skill/spec proposal boundary

- **状态（2026-07-21）**：已完成 proposal boundary。只从跨 episode、重复、完整验证且无 recovery 污染的稳定长链生成 candidate JSON；默认 `auto_apply=false` 且要求 human review。
- **Owner**：Shared；Yixin提供 evidence/proposal，规划/skill owner批准语义。
- **规则**：
  - 单次失败只做 immediate recovery。
  - 跨 episode、稳定 context、重复成功 recovery 才能形成 policy proposal。
  - 只有重复、长链、稳定 pre/postcondition 的 transition sequence 才形成 candidate skill spec。
  - 不自动修改 skill semantics；必须 regression + safety + human review。
- **验收**：可以生成 candidate skill JSON，但默认不进入 production skill library。

## 8. P2：真实 tracer-bullet demo

### P2-5 一个统一入口

- **状态（2026-07-21）**：已完成 `python -m src.pipeline --live-demo`。真实 Docker/Playwright/TD/WoT 经 typed adapter 进入 CIM，并输出截图、episode report、transition/failure ledger、recovery report 和 live metrics。
- **Owner**：Yixin（runtime loop）+ environment/perception/effector owners（typed adapters）。
- **目标**：新增一个清晰命令，例如 `python -m src.pipeline --live-demo`，而不是让观众在多个脚本间拼接证据。
- **要求**：
  - 使用 Docker smart-room + Playwright。
  - 真实 DOM/PAM、TD/WoT、可用时的 Visual observation 进入 LiveRuntimeObservation。
  - 所有 action 和 recovery 都由 CIM/control loop 执行。
  - verifier 使用 re-observed/oracle evidence，不能只相信 HTTP success。
  - 输出 episode trace、transition ledger、screenshots、recovery report 和 measured metrics。

### P2-6 四个 demo case

- **状态（2026-07-21）**：四个必需 case 全部由 live evidence checks 通过；另增加真实 System-1 repeat/cache-hit case。

| Case | 要证明的能力 | 预期白盒证据 |
|---|---|---|
| Normal structured goal | observe-first bounded zero-shot | scanned affordances -> plan -> actual actions -> verified goal |
| WoT timeout/offline | retry/reroute execution | failed backend -> selected alternative -> alternative actually called -> final verification |
| postcondition mismatch | false-success detection + rollback | executor success -> oracle/postcondition fail -> rollback/restore -> re-observe |
| DOM/WoT conflict | fusion + active perception | source assertions -> fused support/conflict -> probe -> resolved continue or halt/escalate |

- **验收**：每个 case 的 success/failure 由 trace 和 final observation计算，不手写 acceptance booleans。

### P2-7 Demo ownership边界

- Yixin 不重写 dashboard、TD parser、DOM executor 或 VLM。
- Yixin 负责 adapter contract、runtime orchestration、recovery、trace 和 final evidence。
- 环境/感知/执行 blocker 必须以接口 issue 分配给对应 owner，不能在 demo script 中永久手动绕过。

## 9. P2：指标与评估诚信

### P2-8 修正 RUR 和 runtime metrics

- **状态（2026-07-21）**：已完成。旧 `RUR` 更名为 `RecoveryTriggerRate`；TSR 与 verified rollback recovery 分开；live metrics 从 episode/transition records 派生并携带 data source 和 episode ids。
- **Owner**：Yixin负责 recovery/fusion metrics，评估负责人共同确认 protocol。
- **动作**：
  - 将当前 `triggered / total` 重命名为 RecoveryTriggerRate，或按 protocol 重写 RUR。
  - 明确定义 recovery success：最终状态经独立 verifier 通过且未 human escalate。
  - 从 actual episode/transition records生成 metrics。
  - synthetic white-box demo 输出必须带 `data_source=synthetic`。
- **验收**：每个数字能追溯到 episode ids；不再由固定 TaskOutcome 构造最终论文结果。

### P2-9 Baseline 与 ablation

- **状态（2026-07-21）**：已完成 live seeded baseline harness。`--live-ablation` 在相同 normal/timeout episodes 下比较 full、no-recovery、DOM-only、WoT-only，并单独输出 live ablation report/ledger。
- **Owner**：Shared。
- **至少需要**：full、no-recovery、DOM-only、WoT-only；Visual-only 只有在真实 visual path 可用后再声称。
- **指标**：TSR、verified recovery success、false-positive rate、conflict false-halt/miss rate、latency、unsafe action rate。
- **验收**：相同 seeded episodes 下比较，脚本 replay 和 agentic path 分开报告。

## 10. P3：Fusion v2，满足 gate 后才启动

### P3-1 Crawl gate

- **状态（2026-07-21）**：runtime/live evidence gate 已满足；main release 仍由 repo owner 负责。未越级引入 Bayesian model。
- **Owner**：Yixin。
- **前置条件**：P0 release、P1 recovery loop、D3/D4、一个 genuine live trace 全部完成。
- **未满足时**：不启动 Bayesian/factor graph，只保留 calibrated heuristic。

### P3-2 Walk：校准现有 heuristic

- **状态（2026-07-21）**：初始 live campaign 已完成。clean、layout shift、selector mutation、stale、WoT timeout/offline、postcondition mismatch 共 7 个 oracle-labeled trials；输出 threshold curve、source confusion、false halt/miss 和 fusion latency，当前推荐 threshold=1.0。
- **Owner**：Yixin（模型/分析）+ environment/evaluation owner（campaign）。
- **动作**：在 clean、selector mutation、layout shift、stale、timeout、offline、postcondition mismatch 下收集 channel observation 与 oracle ground truth。
- **输出**：source confusion/calibration table、threshold ROC、false halt/missed conflict/detection latency。

### P3-3 Run：discrete Bayesian fusion（可选）

- **状态（2026-07-21）**：未激活，按 gate 选择 fallback。7 个初始 live trials 不足以估计可信 channel likelihood；不制造无数据支撑的 Bayesian claim。
- **Owner**：Yixin。
- **只在 Walk 数据足够时实现**：每个 entity/attribute 一个 latent state，TD schema 提供合法 state/range，channel likelihood 来自实测，action/postcondition 作为 transition factor。
- **验收**：posterior 被 condition evaluator 和 CIM 真正消费；与 calibrated heuristic 做相同 seeded episodes 对比。

### P3-4 Fallback

如果数据不足或时间不足，交付经过校准和评估的 heuristic fusion。它比没有数据支撑的复杂概率图更符合老师的验收要求。

- **状态（2026-07-21）**：已交付 required-source-aware calibrated heuristic，并保留 `--fusion-calibration` 用于后续扩充 campaign 后重新选阈值。

## 11. 明确不进入本计划的实现

以下内容不是 Yixin 的待开发模块：

- 任意自然语言理解、LLM task decomposition 的语义实现；只保留 `GoalSpec -> planner interface`。
- DOM transducer、TD parser、real SoM/VLM 内部实现。
- React/node-wot/MiniWoB/WebArena 环境开发。
- 完整 Windows UFO2 Picture-in-Picture/RDP substrate。
- 其他成员的报告或 benchmark 结论。

如果这些模块未提供所需 contract，Yixin 的动作是提交接口 blocker 和 mock contract test，不是把全部实现偷偷塞进 runtime。

## 12. 建议实施顺序

| 顺序 | Work package | 完成标志 |
|---|---|---|
| 1 | P0-1/P0-2 release + claims | main/develop 版本一致，STATUS 可审计 |
| 2 | P1-4/P1-5 provenance/confidence | fusion 输入可信 |
| 3 | P1-1/P1-2/P1-3 recovery execution loop | retry/reroute/rollback 真实执行 |
| 4 | P1-7 fused-state consumption（P1-6 已在 feature/C-009 完成） | verifier 消费单一 runtime truth |
| 5 | P2-5/P2-6 live tracer bullet | 一个命令展示完整真实闭环 |
| 6 | P2-1/P2-2/P2-3 transition ledger | 跨 snapshot 可追踪 |
| 7 | P2-8/P2-9 real metrics/baselines | claim 有 episode evidence |
| 8 | P2-4 bounded skill proposal | trace 可形成受控长期候选 |
| 9 | P3 fusion calibration/probabilistic upgrade | 满足 gate 后按数据决定 |

## 13. 每次完成后的维护规则

每完成一个 work package：

1. 在本文件对应项下补充 commit/PR、tests、demo artifact 和 remaining limitation。
2. 同步更新 `current_codebase_full_analysis.md` 中该缺陷的状态。
3. 运行 full pytest、ruff、black、mypy。
4. 对 live claim 附一条可重放命令和一份由真实 trace 派生的 artifact。
5. 不把本地 docx、meeting notes、generated artifacts 或外部 env clone 混入代码 PR。

## 14. 明天汇报主线与下一步执行计划

### 14.1 明天报告主线

明天不要按文件列表汇报。主线应围绕老师 Week 9 的核心批评：

```text
The repository used to contain separate components.
Now develop has a bounded structured-goal action runtime.
```

最重要的一句话：

```text
The current develop branch can run a structured goal through observe -> map -> fuse -> plan -> act -> re-observe -> verify -> recover.
```

准确边界：

- 已实现：structured `GoalSpec` runtime、canonical `CognitiveMap`、rule-first fusion、primitive planning、backend routing、fresh verification、retry/reroute/rollback、transition ledger、initial live evidence。
- 不声称：unrestricted natural-language agent、完整 VAM/VLM grounding、完整 PiP desktop isolation、统计上充分验证的 Bayesian fusion。

### 14.2 Report 最应该说的 5 点

| 顺序 | 要说什么 | 为什么要说 |
|---|---|---|
| 1 | Week 9 批评的是“组件没有组成 agent runtime” | 先回应老师最核心的评价，不从细节开始 |
| 2 | 当前 develop 已经有 structured-goal closed loop | 直接回答“现在 agent runtime 是否存在” |
| 3 | executor success 不再等于 action success | 证明 verification 是 empirical，不是脚本成功 |
| 4 | recovery 会真实执行 retry/reroute/rollback | 证明 recovery 不是 tier label |
| 5 | live/demo/metrics 已有 tracer-bullet，但统计规模仍不足 | 主动承认边界，避免过度 claim |

### 14.3 明天建议展示的两个 case

| Case | 展示目标 | 一句话解释 |
|---|---|---|
| False success / expected-effect failure | 证明 success 语义被修正 | executor 返回 success，但 fresh observation 没有看到 declared effect，runtime 进入 postcondition failure/recovery |
| DOM/WoT sensory conflict | 证明 fusion 和 active perception 不是静态 gate | DOM/WoT 冲突会 block System 1；新一致证据或 required source 恢复后旧 conflict 被 resolved |

不建议明天重点展示 Bayesian、VAM 或大型 benchmark。原因：当前证据不足，容易把主线带偏。

### 14.4 你明天可以直接说的英文短稿

```text
The main change after the Week-9 review is that the runtime is now composed.
We no longer only have separate perception, routing, verification, and recovery modules.
The current develop branch supports a bounded structured-goal loop:
observe, update the CognitiveMap, fuse evidence, plan a typed primitive action,
execute through the selected backend, re-observe, verify the declared effect,
and then continue, replan, recover, or escalate.

Two recent correctness fixes are important.
First, executor success is not treated as action success.
Every non-empty primitive expected effect must be verified after fresh observation and fusion.
If the effect is missing, the runtime records a postcondition failure and triggers recovery.

Second, conflict status now follows current evidence.
New agreeing evidence resolves an old conflict, restored required sources clear missing-source conflicts,
and stale evidence can be rejected through an absolute assertion-age limit.

The current boundary is structured GoalSpec and explicit affordances.
We do not claim unrestricted natural-language intent recognition, full visual grounding,
or statistically validated Bayesian fusion yet.
```

### 14.5 后续纳入个人计划的 TODO

当前阶段不再以增加新的架构模块或 handcrafted demo 为目标。下一阶段主线是把已经闭合的 runtime 变成经过外部任务、重复实验和独立 oracle 验证的 action-system runtime。

| Priority | TODO | 状态/责任 | 验收条件 | 下一步文件 |
|---|---|---|---|---|
| P0 | Recovery transition linkage 收尾 | `[Yixin - 代码已完成，待发布验收]` | clean checkout 中可从一个 episode 追溯 `failed transition -> recovery decision -> executed transition -> fresh verification`；PR/CI 通过并保存 live JSON evidence | `src/runtime/episode.py`, `src/runtime/continuous_interaction_manager.py`, `tests/test_runtime_episode_recovery.py` |
| P0 | Claim/evidence 同步 | `[Yixin - runtime 部分已更新，live evidence 已生成，团队/发布待同步]` | `STATUS.md`、README、报告和发布分支对 implemented/partial/future 的表述一致；所有 live claim 均带可重放命令和 episode/artifact id | `STATUS.md`, `README.md`, runtime report, `artifacts/live_runtime_demo_y_runtime_evidence/` |
| P1 | Reroute equivalence | `[Yixin - 代码已完成，已全量测试]` | alternative affordance 必须在 action type、expected effect、parameter binding、safety、reversibility、idempotency 上兼容；不再只按语义 key 或名称相似 reroute | `src/runtime/continuous_interaction_manager.py`, `tests/test_runtime_goal_episode.py` |
| P1 | Ledger-derived metrics | `[Yixin - 代码已完成，已全量测试]` | 从 transition ledger 自动生成 primitive/action/recovery metric rows；分别报告 expected-effect success、task success、recovery trigger/success、retry/reroute/rollback、false success 和 latency；区分 measured zero 与 not measured | `evaluation/metrics_aggregator.py`, `tests/test_metrics_aggregator.py` |
| P1 | Unified runtime episode entrypoint | `[Yixin - 代码已完成，已全量测试]` | smoke pipeline、adaptation demo、external web runtime planner、MiniWoB/scripted benchmark demo 均通过统一 episode runner 产出 episode id、transition ledger 和 metrics；agentic GoalSpec path 与 scripted solver envelope 明确分表 | `src/runtime/episode_runner.py`, `src/benchmarks/runtime_web_adapter.py`, `src/benchmarks/scripted_runtime.py`, `scripts/run_agent_on_env.py`, `scripts/run_miniwob.py`, `scripts/run_miniwob_demo.py`, `scripts/run_fancy_demo.py`, `tests/test_runtime_episode_runner.py` |
| P1 | System-1 repeated latency | `[Yixin - 代码+live evidence 已完成]` | 在重复 episode 中报告 warm-up、cache-hit rate、routing latency、total episode latency 和 amortized latency，并关联 episode ids | `evaluation/live_runtime_demo.py`, `src/runtime/episode.py`, `tests/test_live_runtime_demo.py`, `artifacts/live_runtime_demo_y_runtime_evidence/episode_report.json` |
| P2 | Smart-room repeated fusion/recovery campaign | `[Yixin - 30×7 live evidence 已完成，后续可做独立 rerun/holdout]` + `[Shared - environment/reset/fault API]` | 现有 7 个 condition 每个至少 30 个独立 trial，总量至少 210；使用 deterministic seeds、每次 reset evidence、独立 oracle、唯一 episode id 和可重放配置 | `evaluation/live_fusion_campaign.py`, `evaluation/live_fusion_calibration.py`, `evaluation/fusion_calibration.py`, `src/pipeline.py`, `tests/test_live_fusion_campaign.py`, `artifacts/live_fusion_campaign_full/fusion_campaign_summary.json` |
| P2 | Calibration / locked holdout | `[Yixin - 初始 20/10 split 已完成，后续可补跨 seed 方差/置信区间]` | calibration set 选择并锁定 threshold；holdout 禁止继续调参；报告 precision、recall、false halt、miss、balanced accuracy、detection latency、downstream TSR/recovery 和跨 seed 方差/置信区间 | `evaluation/fusion_holdout.py`, `evaluation/fusion_calibration.py`, `tests/test_fusion_holdout.py`, `artifacts/live_fusion_holdout/fusion_holdout_report.json` |
| P2 | MiniWoB++ generalization study | `[Yixin - runtime contract/failure analysis]` + `[Shared - environment/affordance adapter]` | 任务必须走与 smart-room 相同的 `GoalSpec -> affordance -> primitive -> execute -> verify` runtime path；agentic 与 task-specific scripted solver 分表；输出按 failure taxonomy 聚合的 bottleneck report | `src/benchmarks/`, `scripts/run_miniwob.py`, `evaluation/` |
| P2 | Open-web failure coverage gap | `[Yixin - 机制边界梳理 + failure taxonomy]` + `[Shared - mock/open-web env cases]` | 不把 smart-room controlled faults 等同于真实开放网页覆盖；补充 overlay、session expiry、autocomplete/async validation、DOM-vs-visual disagreement、optimistic UI/backend mismatch 等 failure cases；每个 case 必须输出 observation/action/verification/recovery ledger，并标注是 mechanism coverage、mock evidence 还是真实 open-web evidence | `src/benchmarks/`, `env/mock_envs/`, `evaluation/`, `artifacts/open_web_failure_suite/` |
| Conditional | Bayesian fusion comparator | `[Yixin - shadow-mode strategy + independent stability report 已完成，未替换 production gate]` | 只有 repeated calibration + locked holdout 数据支持时才比较 posterior；shadow mode 同时记录 production rule-first 与 Bayesian decision，但 production gate 不变；initial 与 independent rerun holdout 均为正向后，下一步仍需 integration design review，而非直接替换 | `evaluation/bayesian_fusion_comparator.py`, `evaluation/fusion_shadow_strategies.py`, `evaluation/fusion_ablation_report.py`, `evaluation/bayesian_shadow_stability.py`, `tests/test_bayesian_fusion_comparator.py`, `tests/test_fusion_shadow_strategies.py`, `tests/test_fusion_ablation_report.py`, `tests/test_bayesian_shadow_stability.py`, `artifacts/bayesian_shadow_stability/bayesian_shadow_stability_report.json` |
| Conditional | Ambiguous/noisy fusion stress | `[Yixin - synthetic stress 已完成，live ambiguous cases 待设计]` | 构造弱 stale、延迟恢复、低可靠 DOM、部分缺失 WoT 等模糊 evidence；若 Bayesian 在 synthetic 上有增益，再设计 live ambiguous benchmark，不直接改 production gate | `evaluation/noisy_fusion_stress.py`, `tests/test_noisy_fusion_stress.py`, `artifacts/noisy_fusion_stress/noisy_fusion_stress_report.json` |
| Conditional | Live ambiguous fusion profiles | `[Yixin - initial + independent rerun 30×4 live evidence 与 20/10 locked holdout 已完成，production gate 未替换]` | 将 weak stale、delayed recovery、low-reliability DOM、partial missing WoT 映射到细粒度 live fault API；记录 profile、seed、episode id、fault mapping 和 shadow comparator summary；按 profile 做 calibration/holdout split；initial 与 rerun 均保持 Bayesian shadow 正向；不改变 production gate | `evaluation/live_ambiguous_fusion_campaign.py`, `evaluation/live_ambiguous_fusion_holdout.py`, `tests/test_live_ambiguous_fusion_campaign.py`, `tests/test_live_ambiguous_fusion_holdout.py`, `env/node_wot_server/server.js`, `env/react_dashboard/src/App.jsx`, `artifacts/live_ambiguous_fusion_full/live_ambiguous_fusion_summary.json`, `artifacts/live_ambiguous_fusion_rerun/live_ambiguous_fusion_summary.json`, `artifacts/live_ambiguous_fusion_holdout/live_ambiguous_fusion_holdout_report.json`, `artifacts/live_ambiguous_fusion_rerun_holdout/live_ambiguous_fusion_holdout_report.json` |

### 14.5.1 Live evidence 记录

- **时间**：2026-08-05
- **分支**：`feature/Y-runtime-evidence-and-benchmarks`
- **运行命令**：
  `python -m src.pipeline --live-demo --output-dir artifacts/live_runtime_demo_y_runtime_evidence --dashboard-url http://127.0.0.1:13000 --wot-base-url http://127.0.0.1:18080 --control-url http://127.0.0.1:18081 --thing-directory-url http://127.0.0.1:18082/things`
- **环境说明**：默认 8080 被既有 `app` 容器占用，因此复用健康的 `smartroom_tmp` 映射端口 13000/18080/18081/18082。
- **核心 artifact**：
  - `artifacts/live_runtime_demo_y_runtime_evidence/episode_report.json`
  - `artifacts/live_runtime_demo_y_runtime_evidence/transition_ledger.jsonl`
  - `artifacts/live_runtime_demo_y_runtime_evidence/failure_ledger.jsonl`
  - `artifacts/live_runtime_demo_y_runtime_evidence/recovery_report.json`
  - `artifacts/live_runtime_demo_y_runtime_evidence/measured_metrics.json`
- **验收摘要**：
  - `all_evidence_checks_passed=true`
  - case count = 5
  - transition records = 12
  - failure records = 2
  - System-1 warmup/repeat episode ids = `episode-e690b487d55a`, `episode-725c06a69888`
  - System-1 cache-hit rate = 0.5
  - System-1 total transition latency = 8.961 ms
  - System-1 amortized transition latency = 4.48 ms

### 14.5.2 P2 repeated campaign 脚手架记录

- **时间**：2026-08-05
- **代码入口**：
  - `evaluation/live_fusion_campaign.py`
  - `src.pipeline.run_fusion_campaign_pipeline`
  - CLI: `python -m src.pipeline --fusion-campaign-dry-run --repetitions 30`
  - CLI: `python -m src.pipeline --fusion-campaign --repetitions 30`
- **协议能力**：
  - 自动生成 7 condition × N repetitions 的 deterministic campaign plan。
  - 每个 trial 带唯一 `episode_id` 和唯一 `seed`。
  - live runner 每个 trial 前执行 reset，并把 `reset_evidence_id` 写入 trial。
  - independent oracle 以 fault-injection scenario label 记录为 `oracle_source=fault-injection-label`。
  - summary 输出 per-condition counts、precision、recall、false halt、miss、balanced accuracy、mean detection latency 和 replay config。
- **已生成 artifact**：
  - 30×7 dry-run plan: `artifacts/live_fusion_campaign_plan/fusion_campaign_plan.json`
  - 30×7 dry-run summary: `artifacts/live_fusion_campaign_plan/fusion_campaign_summary.json`
  - 1×7 live smoke summary: `artifacts/live_fusion_campaign_smoke/fusion_campaign_summary.json`
  - 30×7 live full plan: `artifacts/live_fusion_campaign_full/fusion_campaign_plan.json`
  - 30×7 live full summary: `artifacts/live_fusion_campaign_full/fusion_campaign_summary.json`
- **当前验收边界**：
  - 脚手架、1×7 live smoke 和 30×7 live full campaign 已验收。
  - 30×7 full campaign 摘要：trial count = 210；每个 condition = 30；unique episode ids = true；unique seeds = true；reset evidence complete = true；independent oracle complete = true。
  - 初始 30×7 指标：precision = 1.0；recall = 1.0；false halt rate = 0.0；miss rate = 0.0；balanced accuracy = 1.0；mean detection latency = 0.136 ms。
  - publication-grade claim 仍建议再做独立 rerun / locked holdout，避免只基于同一环境同一脚本的一次 campaign 过度表述。

### 14.5.3 Locked holdout 记录

- **时间**：2026-08-05
- **输入**：`artifacts/live_fusion_campaign_full/fusion_campaign_summary.json`
- **输出**：`artifacts/live_fusion_holdout/fusion_holdout_report.json`
- **运行命令**：
  `python -m src.pipeline --fusion-holdout --campaign-summary artifacts/live_fusion_campaign_full/fusion_campaign_summary.json --output-dir artifacts/live_fusion_holdout --calibration-repetitions 20 --holdout-repetitions 10`
- **协议**：
  - 每个 condition 前 20 个 repetition 作为 calibration。
  - 每个 condition 后 10 个 repetition 作为 locked holdout。
  - threshold 只在 calibration 上选择，holdout 直接复用 locked threshold，不再调参。
- **结果**：
  - calibration trials = 140
  - holdout trials = 70
  - locked threshold = 1.0
  - holdout precision = 1.0
  - holdout recall = 1.0
  - holdout false halt rate = 0.0
  - holdout miss rate = 0.0
  - holdout balanced accuracy = 1.0
  - holdout mean detection latency = 0.130 ms
- **Bayesian fusion 结论**：
  - 当前 rule-first fusion 在 holdout 上仍为满分，因此 Bayesian fusion 不能直接 claim 为必要改进。
  - 下一步如果做 Bayesian，应作为 `experimental comparator`，加入 ambiguous/noisy source cases 后比较 posterior 与 locked heuristic，而不是替换 production gate。

### 14.5.4 Bayesian fusion comparator 记录

- **时间**：2026-08-05
- **输入**：`artifacts/live_fusion_holdout/fusion_holdout_report.json`
- **输出**：`artifacts/bayesian_fusion_comparator/bayesian_fusion_comparator_report.json`
- **运行命令**：
  `python -m src.pipeline --bayesian-fusion-comparator --holdout-report artifacts/live_fusion_holdout/fusion_holdout_report.json --output-dir artifacts/bayesian_fusion_comparator --posterior-threshold 0.5`
- **实现边界**：
  - Bayesian 只作为 `experimental_comparator`。
  - production default 仍为 `rule_first_locked_threshold`。
  - comparator 不接入 CIM，不改变 runtime gate。
  - holdout 不用于调参；模型只从 calibration threshold/operating point 派生 posterior 形状。
- **结果**：
  - Bayesian holdout precision = 1.0
  - Bayesian holdout recall = 1.0
  - Bayesian false halt rate = 0.0
  - Bayesian miss rate = 0.0
  - Bayesian balanced accuracy = 1.0
  - Rule-first balanced accuracy = 1.0
  - delta = 0.0
  - recommendation = `keep_rule_first_default`
- **结论**：
  - 目前 Bayesian 与 locked rule-first 打平，没有证明能改进现有 gate。
  - 如果后续要让 Bayesian 有意义，需要新增 ambiguous/noisy source cases，例如弱 stale、延迟但最终返回、source reliability 漂移、三源冲突或部分缺失 evidence。

### 14.5.5 Ambiguous/noisy fusion stress 记录

- **时间**：2026-08-05
- **输出**：`artifacts/noisy_fusion_stress/noisy_fusion_stress_report.json`
- **运行命令**：
  `python -m src.pipeline --noisy-fusion-stress --repetitions 30 --seed-start 3000 --output-dir artifacts/noisy_fusion_stress --posterior-threshold 0.5`
- **性质**：synthetic stress，不是 live evidence，不接入 production gate。
- **stress conditions**：
  - `weak_stale_signal`
  - `delayed_wot_recovery`
  - `low_reliability_dom`
  - `partial_missing_wot`
- **结果**：
  - trials = 120
  - rule-first balanced accuracy = 0.5
  - rule-first recall = 0.0
  - rule-first miss rate = 1.0
  - Bayesian balanced accuracy = 1.0
  - Bayesian recall = 1.0
  - Bayesian false halt rate = 0.0
  - Bayesian miss rate = 0.0
  - delta = 0.5
- **结论**：
  - Synthetic noisy stress 说明 Bayesian posterior 在模糊 source reliability / staleness / missing-source 特征下有潜在价值。
  - 这只支持下一步设计 live ambiguous cases；不能 claim Bayesian 已优于 production live gate。

### 14.5.6 Live ambiguous fusion profile 记录

- **时间**：2026-08-05
- **代码入口**：
  - `evaluation/live_ambiguous_fusion_campaign.py`
  - CLI: `python -m src.pipeline --live-ambiguous-fusion-dry-run --repetitions 30`
  - CLI: `python -m src.pipeline --live-ambiguous-fusion --repetitions 1`
- **已生成 artifact**：
  - 30×4 dry-run plan: `artifacts/live_ambiguous_fusion_plan/live_ambiguous_fusion_plan.json`
  - 30×4 dry-run summary: `artifacts/live_ambiguous_fusion_plan/live_ambiguous_fusion_summary.json`
  - 1×4 live smoke summary: `artifacts/live_ambiguous_fusion_smoke/live_ambiguous_fusion_summary.json`
  - 1×4 fine-grained live smoke summary: `artifacts/live_ambiguous_fusion_fine_smoke/live_ambiguous_fusion_summary.json`
  - 30×4 fine-grained live full summary: `artifacts/live_ambiguous_fusion_full/live_ambiguous_fusion_summary.json`
- **profile mapping**：
  - `weak_stale_signal` -> current dashboard `stale_temperature`
  - `delayed_wot_recovery` -> current WoT `timeout` with short request timeout
  - `low_reliability_dom` -> current dashboard `layout_shift`
  - `partial_missing_wot` -> current WoT `offline`
- **1×4 live smoke result**：
  - trial count = 4
  - profile counts = 1 each
  - rule-first balanced accuracy = 1.0
  - Bayesian balanced accuracy = 1.0
  - delta = 0.0
  - recommendation = `keep_rule_first_default`
- **当前边界**：
  - 细粒度 smart-room fault API 已扩展：`stale_offset`、`read_delay_ms`、`drop_probability`、`source_reliability` metadata。
  - 1×4 fine smoke 结果：rule-first balanced accuracy = 0.833；Bayesian balanced accuracy = 1.0；delta = 0.167；production gate unchanged。
  - 30×4 fine-grained live full 结果：trial count = 120；每个 profile = 30；unique episode ids = true；reset evidence complete = true。
  - Rule-first balanced accuracy = 0.833；recall = 0.667；miss rate = 0.333。
  - Bayesian comparator balanced accuracy = 1.0；recall = 1.0；miss rate = 0.0；false halt rate = 0.0；delta = 0.167。
  - Production gate 仍未替换；locked holdout 与 shadow ablation 已在 14.5.7 完成；下一步若要接入 runtime，必须先做 independent rerun/review。

### 14.5.7 Bayesian shadow-mode / live ambiguous holdout / ablation 记录

- **时间**：2026-08-06
- **代码入口**：
  - `evaluation/fusion_shadow_strategies.py`
  - `evaluation/live_ambiguous_fusion_holdout.py`
  - `evaluation/fusion_ablation_report.py`
  - `evaluation/bayesian_shadow_stability.py`
  - CLI: `python -m src.pipeline --live-ambiguous-fusion-holdout --live-ambiguous-summary artifacts/live_ambiguous_fusion_full/live_ambiguous_fusion_summary.json --output-dir artifacts/live_ambiguous_fusion_holdout --calibration-repetitions 20 --holdout-repetitions 10 --posterior-threshold 0.5`
  - CLI: `python -m src.pipeline --fusion-ablation-report --holdout-report artifacts/live_ambiguous_fusion_holdout/live_ambiguous_fusion_holdout_report.json --output-dir artifacts/bayesian_vs_rule_first_ablation`
  - CLI: `python -m src.pipeline --bayesian-shadow-stability --holdout-reports artifacts/live_ambiguous_fusion_holdout/live_ambiguous_fusion_holdout_report.json artifacts/live_ambiguous_fusion_rerun_holdout/live_ambiguous_fusion_holdout_report.json --output-dir artifacts/bayesian_shadow_stability`
- **已生成 artifact**：
  - `artifacts/live_ambiguous_fusion_holdout/live_ambiguous_fusion_holdout_report.json`
  - `artifacts/bayesian_vs_rule_first_ablation/bayesian_vs_rule_first_ablation_report.json`
  - `artifacts/live_ambiguous_fusion_rerun/live_ambiguous_fusion_summary.json`
  - `artifacts/live_ambiguous_fusion_rerun_holdout/live_ambiguous_fusion_holdout_report.json`
  - `artifacts/bayesian_shadow_stability/bayesian_shadow_stability_report.json`
- **实现边界**：
  - Production strategy 仍为 `rule_first_locked_threshold`。
  - Bayesian 作为 `bayesian_feature_shadow` 同步计算 blocking posterior，并写入 shadow decisions。
  - `production_gate_changed=false`；没有接入 CIM/verifier 作为默认 gate。
  - Shadow promotion 条件写入 ablation boundary：必须经过 independent live ambiguous rerun、locked holdout、false-halt/miss review 和 CIM/verifier integration review。
- **20/10 per-profile holdout 结果**：
  - calibration trials = 80
  - holdout trials = 40
  - holdout profile counts = 10 each
  - Rule-first balanced accuracy = 0.833；recall = 0.667；miss rate = 0.333；false halt rate = 0.0。
  - Bayesian shadow balanced accuracy = 1.0；recall = 1.0；miss rate = 0.0；false halt rate = 0.0。
  - delta = 0.167
  - recommendation = `consider_shadow_to_gate_promotion_after_independent_rerun`
- **Independent rerun 结果**：
  - rerun seed_start = 5300
  - rerun trials = 120；每个 profile = 30
  - rerun holdout trials = 40；每个 profile = 10
  - Rerun rule-first balanced accuracy = 0.833；recall = 0.667；miss rate = 0.333；false halt rate = 0.0。
  - Rerun Bayesian shadow balanced accuracy = 1.0；recall = 1.0；miss rate = 0.0；false halt rate = 0.0。
  - Rerun delta = 0.167
- **Stability report 结论**：
  - compared holdouts = 2
  - total holdout trials = 80
  - min balanced accuracy delta = 0.167
  - max Bayesian false halt rate = 0.0
  - max Bayesian miss rate = 0.0
  - promotion preconditions 全部通过：positive delta、miss-rate non-regression、false-halt non-regression、profile counts complete、production gate unchanged。
  - recommendation = `ready_for_integration_design_review`
  - 仍不表示已经替换 production gate；下一步是 configurable CIM/verifier integration design，而不是直接默认启用。

### 14.6 Planner 职责边界

Yixin 负责的是 **structured-goal action-system planner**，而不是 open-ended natural-language agent。

Planner 应负责：

1. 接收结构化 `GoalSpec`、当前 `CognitiveMap` 和 sanitized runtime affordances。
2. 在显式 affordance contract 内进行 bounded task decomposition。
3. 输出 backend-agnostic、typed primitive actions，并为非空动作声明 expected effect。
4. 在参数绑定缺失、affordance 不足、冲突未解决或计划无法安全验证时输出 clarify/escalate。
5. 在 fresh observation、环境状态变化或 recovery 完成后支持 replan。
6. 生成的 plan 必须经过 plan validator；不能输出 raw selector、raw JavaScript、未声明 endpoint 或绕过 safety gate 的指令。

Planner 不应负责：

| 能力 | 所属边界 |
|---|---|
| 任意自然语言解释和用户意图识别 | upstream `NL/LLM -> GoalSpec` 层；不属于 Yixin runtime 实现 |
| backend selection | `RuntimeBackendRouter`；planner 保持 backend-agnostic |
| retry/reroute/rollback/escalation 策略 | `RecoveryCascade`；planner 可在 recovery 后重新调用，但不拥有 recovery policy |
| DOM selector、TD endpoint、visual mark 生成 | perception/affordance/effector owner |
| 自动修改 durable skill 或 production policy | trace-driven adaptation 只生成 review-gated candidate proposal |

对老师的统一表述：

```text
The planner owns bounded task decomposition over structured goals and typed affordances.
Natural-language interpretation remains upstream, backend selection remains in the router,
and runtime recovery remains in the recovery cascade. The planner may be invoked again
after recovery, but it does not own the recovery policy.
```

### 14.7 两类 benchmark 的分工和研究目标

#### A. Smart-room controlled benchmark

Smart-room 用于验证项目独有的 multi-source fusion、independent verification 和 recovery correctness。

Yixin 负责：

- 定义 scenario/fault/seed/reset/oracle/episode schema。
- 保证 DOM、WoT、Visual/runtime evidence 带 source、confidence、timestamp 和 provenance。
- 让所有任务通过 CIM 的 observe-plan-act-reobserve-verify-recover 路径执行。
- 从 transition ledger 派生 fusion、verification、recovery 和 latency metrics。
- 维护 calibration/holdout protocol，并分析 false halt、miss 和 downstream task impact。

Shared/environment owner 负责：

- 可重复的环境 reset 和 session isolation。
- seeded fault injection。
- 与 runtime CognitiveMap 分离的 oracle/control-plane state。
- React/node-wot/Visual environment 本身的实现与维护。

#### B. MiniWoB++ generalization study

MiniWoB++ 用于定位 planner/affordance/runtime 在外部 web task 上的泛化瓶颈，不再以增加几个手写成功 demo 为目标。

正式评估要求：

1. 覆盖 click、text entry、form、multi-step 和 dynamic target 等不同任务类型。
2. 使用同一 structured GoalSpec、affordance、primitive、verification 和 trace contract。
3. 禁止把 per-task solver、benchmark keyword rule 或 authored success boolean 混入 agentic 结果。
4. scripted replay 只能单独作为 upper bound 或环境 smoke test。
5. 每个失败必须归入以下 taxonomy 之一：
   - GoalSpec information gap；
   - affordance extraction/grounding failure；
   - planner binding/decomposition failure；
   - backend routing/execution failure；
   - expected-effect/final verification failure；
   - stale state/conflict handling failure；
   - recovery policy/budget failure。

最终输出不是“又支持了多少个 demo”，而是一个可追溯的 generalization bottleneck report，用于决定下一轮应该改 planner contract、affordance quality、verification 还是 recovery。

#### C. Open-web failure coverage gap

真实开放网页中的 conflict/failure 不会总是呈现为 smart-room 里的标准 `DOM/WoT conflict`。当前代码库已经有 observe-act-reobserve-verify、postcondition checking、stale evidence rejection、required-source-aware fusion、recovery 和 ledger 这些机制，但 evidence 主要来自 smart-room controlled faults。下一阶段不能把这些 controlled faults 直接 claim 成完整 open-web 覆盖。

需要补充的 open-web failure classes：

1. **Optimistic UI / backend mismatch**：页面显示“成功”或状态已更新，但 backend/API/network confirmation 失败或没有发生。
2. **Visible but ineffective affordance**：按钮或输入框在 DOM 中存在且可见，但点击/输入后没有 expected effect。
3. **DOM vs screenshot/OCR disagreement**：DOM tree、视觉截图、OCR/SoM/VAM 对页面状态或目标位置给出不一致解释。
4. **Async stale state**：页面异步刷新、缓存、动画或延迟导致 agent 读到旧状态。
5. **Overlay/modal obstruction**：DOM affordance 存在，但被弹窗、cookie banner、loading layer 或 disabled overlay 遮挡，实际不可操作。
6. **A/B layout or selector drift**：页面结构、selector、label 或位置变化导致旧 grounding 误读。
7. **Session/auth expiry**：登录过期、权限失效或残留旧页面内容导致 action/verification 失真。
8. **Autocomplete / async validation mutation**：表单 autocomplete、前端 validation 或 server-side normalization 改写最终值。

覆盖分级必须在 artifact 中显式标注：

| Coverage level | 含义 | 当前状态 |
|---|---|---|
| mechanism coverage | runtime 有能力检测 expected-effect 缺失、stale evidence、source conflict 或 recovery failure | 部分已有 |
| controlled/mock evidence | 在 smart-room 或本地 mock web env 中通过 seeded case 可重复触发 | smart-room 部分已有，open-web mock 待补 |
| real open-web evidence | 在 MiniWoB++/WebArena-style/真实网页任务中自然或半自然触发，并由 ledger 记录 | 当前不足，不能 claim |

Yixin 侧下一步责任：

- 定义 open-web failure taxonomy 与 artifact schema。
- 确保每个 case 走统一 `RuntimeEpisodeRunner` 或同等 episode envelope。
- 每个 case 记录 `episode_id`、observation snapshot、selected affordance、primitive action、expected effect、postcondition result、recovery decision 和 final verification。
- 将 agentic runtime path 与 scripted/environment smoke path 分表。
- 输出 `open_web_failure_suite_report.json`，明确哪些 failure class 已覆盖、哪些只是 mechanism-ready、哪些仍未覆盖。

Shared/environment owner 侧下一步责任：

- 在 MiniWoB++ 或 WebArena-style mock env 中提供可复现页面/任务。
- 提供 session reset、oracle state 或独立 success checker。
- 对 overlay、session expiry、autocomplete/validation、DOM-vs-visual disagreement 等 case 提供环境触发方式。

### 14.8 下一阶段最终产出

| Deliverable | 内容 | 完成标志 |
|---|---|---|
| Planner responsibility specification | 输入、输出、职责和非职责边界 | 报告、README、代码 contract 的表述一致 |
| Smart-room repeated campaign | seeded faults、reset、independent oracle、repeated trials、holdout | 至少 210 个可追溯 episodes 和统计报告 |
| MiniWoB++ failure report | 统一 agentic runtime path 和 failure taxonomy | 能用 evidence 说明主要 generalization bottleneck |
| Open-web failure coverage report | overlay、session expiry、async validation、DOM-vs-visual disagreement、optimistic UI/backend mismatch 等 coverage gap | 每类 case 标注 mechanism/mock/real-open-web coverage level，并带 episode ledger |
| Runtime comparison table | full/no-recovery、backend ablation、smart-room/MiniWoB++ | 指标均由 episode/transition ledger 派生 |

下一阶段对老师的简要表述：

```text
My next responsibility is to move the runtime from tracer-bullet evidence to repeatable evaluation.
I will keep the planner bounded to structured GoalSpecs and typed affordances, while natural-language
interpretation, backend selection, and recovery policy remain separate components. I will evaluate the
same runtime path on MiniWoB++ to identify generalization bottlenecks, and expand the smart-room environment
into a seeded, oracle-labeled repeated campaign for fusion and recovery. The main deliverables will be
ledger-derived metrics, calibration/holdout results, and an explicit failure taxonomy. For open-web claims,
I will separately track whether each failure class is only mechanism-supported, covered by controlled/mock evidence,
or supported by real open-environment evidence, rather than treating smart-room faults as complete open-web coverage.
```

### 14.9 展示材料最小组合

明天只需要三页或三段：

1. **Runtime flow diagram**：`GoalSpec -> Observe -> CognitiveMap -> Fusion -> Planner -> Executor -> Fresh Observation -> Verification -> Recovery`
2. **Two evidence cases**：false success case + DOM/WoT conflict case
3. **Boundary and next work**：structured runtime 已完成；visual grounding、statistical campaign、Bayesian fusion 是后续

如果只能展示一个 demo，优先展示 false-success 或 conflict case，而不是 normal happy path。happy path 说明能跑，failure path 才说明 runtime 有控制能力。
