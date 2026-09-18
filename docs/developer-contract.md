# OpenJob 开发者契约（WP-S0）

> **文档性质**：本文件是 OpenJob 的**产品契约单一事实来源**索引。任何 Agent 或
> 贡献者开工前必读；状态、能力、来源、格式各有唯一权威定义，禁止在各处手抄第二份。
>
> - 版本：v1.0 ｜ 日期：2026-09-14 ｜ 对应工作包：WP-S0
> - 冲突裁决：本文与代码不一致时，以代码实测为准并回报修订本文。

---

## 1. 产品定位与不自动发送边界

OpenJob 是运行在用户本人电脑上的 AI 求职工作台：把重复劳动自动化，把所有判断和
对外动作留给人，**不替用户伪造事实，也不在用户不知情时替用户对外行动**。

- **确认铁律**：任何对外发送（招呼语/简历/回复）必须人工逐条放行；代码层面
  不存在也不得新增绕过路径。
- AI 的四条边界：AI 是岗位文本处理者、排序解释者、证据推荐者、草稿生成者；
  **不是**事实创造者、身份代言人、发送授权人、平台风控责任的替代者。

## 2. 常量单一来源

| 契约 | 唯一权威 | 前端/其他层如何对齐 |
| --- | --- | --- |
| 岗位状态标签与迁移白名单 | `openjob/contracts.py`（`JOB_STATUS_LABELS` / `JOB_STATUS_TRANSITIONS`） | 前端 `lib/status.ts` 由 `tests/test_contracts.py` 一致性断言守护；也可 `GET /api/contracts` 拉取 |
| 任务生命周期 / 计划采集运行状态 | `openjob/contracts.py`（`TASK_*` / `SCHEDULED_RUN_*`） | 同上 |
| 平台能力矩阵 | `openjob/collection/capabilities.py`（contracts 转出口） | 服务端强制；前端不自行判断能力 |
| 事实来源类型 | `openjob/contracts.py`（`FACT_SOURCE_TYPES_*`），执行细节在 `openjob/ai/fact_policy.py` | 三个生成器统一导入 |
| 招呼语事实状态 | `openjob/contracts.py::GREETING_FACT_STATUSES` | db 层校验非法值 |
| 模板/示例标记黑名单 | `openjob/ai/fact_policy.py::TEMPLATE_RESUME_MARKERS` | greeter / resume_engine / bases 全部从此导入，禁止本地再定义 |

CLI `tracker/status.py` 的标签是 contracts 的转出口，只补充 rich 样式。

## 3. 岗位状态机（迁移白名单）

合法迁移（`via="standard"`；同状态幂等重写始终允许）：

```
pending    → scored | filtered | ready | error | rejected      # rejected：岗位池人工放弃
scored     → ready | filtered | error | rejected
filtered   → ready | filtered | error | rejected    # 重评分可翻转；人工放行 → ready
ready      → approved | sent | rejected | skipped | error | filtered
           # 发送队列从 ready 直发（get_jobs_ready_to_send 含 ready/approved）；重评分可降级
approved   → ready | sent | error | rejected | skipped | stale  # approved→ready：招呼语生成后回到待发送
sent       → replied | resume_sent | needs_resume | follow_up_sent | rejected | stale
error      → sent | error | follow_up_sent   # 重试；或从聊天列表找回并发跟进
needs_resume / resume_sent / follow_up_sent → 回复/简历/拒绝等投后互转（见 contracts）
replied    → interview | hr_rejected | closed                # A2 投后终态
interview  → offer | hr_rejected | closed
offer      → closed
hr_rejected → closed
replied / rejected / skipped / closed      → closed 为唯一再出口的终态族
stale      → ready | approved                # B9 重激活
```

可视化版本（mermaid）见 `docs/state-machine.md`（A2）；投后流转的操作入口为
`POST /api/jobs/<id>/transition`（非法 409），回复回流字段见 A1（`replied_at`/
`reply_count`/`last_reply_snippet` + conversations 表）。

- 非法迁移抛 `IllegalJobStatusTransition`，**原状态保留**，API 返回 409 +
  `ILLEGAL_STATUS_TRANSITION`。禁止 `sent→pending`。
- 业务代码必须用 `db.transition_job_status(...)`；`db.update_job_status(...)` 是
  仅供迁移函数内部使用的裸写入器。
- 人工平台外发送（智联/51job 手动标记，`via="manual_external"`）允许从
  `MANUAL_EXTERNAL_SEND_SOURCES` 进入 `sent`，并以 history 动作 `manual_sent`
  记录台账，不伪造自动发送。
- `stale` 由 WP-B9 引入写入路径；当前仅预留白名单。

## 4. 任务生命周期

目标状态机（contracts.TASK_LIFECYCLE_TRANSITIONS）：

```
created → preflight → running → completed | stopped | failed
                        │
                        ├→ stopping（请求停止的过渡态）
                        └→ pausing → paused（可恢复）
重启收敛：无法确认的 running → interrupted（不伪装成功）
```

- workbench 任务：已实现 running/stopping/completed/failed/stopped；终态快照含
  `created_at / finished_at / progress / metrics / error / stop_reason / error_count`。
  任务不持久化，进程重启即消失（无幽灵任务）。
- 计划采集运行（持久化）：重启收敛 `running → interrupted`（原因
  "工作台重启，上一轮计划任务无法确认完成"）。
- 评分运行（持久化，可恢复）：重启收敛 `pending/running → paused`（剩余岗位可续跑）。
- `preflight` / `paused` 状态位留给 WP-C7 接入。

## 5. API 响应格式

新增接口一律使用标准封套（存量接口渐进接入）：

```json
{ "success": true,  "data": {...}, "error": null, "request_id": "uuid" }
{ "success": false, "data": null,
  "error": { "code": "ILLEGAL_STATUS_TRANSITION", "message": "…可读原因…",
             "retryable": false, "details": {} },
  "request_id": "uuid" }
```

实现：`web/server.py::_api_envelope / _api_error`。已接入：`GET /api/contracts`、
`POST /api/jobs/<id>/greeting`（编辑再校验）、全部非法状态迁移 409。

## 6. 数据目录契约

- `data/` 全家（openjob.db、备份、resumes/、素材库 xlsx+索引、导入暂存、日志）
  只属运行时：**不入 git、不上传任何服务器**。
- `config.yaml` 只属运行时；API Key 只存本地 config.yaml，绝不硬编码。
- 运行时路径统一由 `web/server.set_base_dir` 解析；模块内默认 `./data`（CLI）。

## 7. 平台能力矩阵

以 `collection/capabilities.py` 为唯一来源（当前值）：

| 平台 | collect | score | greet | deliver | monitor |
| --- | --- | --- | --- | --- | --- |
| boss | ✅ | ✅ | ✅ | ✅ | ✅ |
| zhilian | ✅ | ✅ | ✅ | ❌ 只读 | ❌ |
| 51job | ✅ | ✅ | ✅ | ❌ 只读 | ❌ |

智联/前程无忧的 deliver/monitor 在真实账号验收并显式启用前保持锁定。

## 8. 事实源白名单（WP-S1，详见 docs 内 fact_policy 模块注释）

- **allowed**：`verified_base_resume` / `verified_resume_material` / `user_confirmed_fact`
- **forbidden**：`example_resume`（resume.md、resume.example.md、示例数据）、
  `job_description`（JD 只提供匹配目标）、`ai_score_reason`、`generated_resume`
  （岗位版本产物不可反哺）、`platform_default_greeting`、`unconfirmed_import`、
  `user_preference`（身份性描述）
- 三个生成器（招呼语 / 定制简历 / 回复草稿 A3）共用同一策略与黑名单；
  生成文本必须通过 `fact_policy.validate_generated_text` 才能落为 verified；
  发送边界逐条复检（`send_blocked_fact_recheck`）。
- JD 是不可信文本：进入 LLM 前必须经 `sanitize_untrusted_text` 中性化与包裹。
- 生成物隔离：定制简历只关联岗位/底稿/素材 fact_id，绝不写回事实库
  （base_resumes / resume_materials 无任何生成回写路径）。
- 用户编辑必须再校验（Web `POST /api/jobs/<id>/greeting`、CLI confirm 编辑分支）；
  校验失败返回具体缺口，让用户补证据，而不是绕过。

## 9. 兼容与迁移原则

- 每次 schema 变更有版本号、幂等、可回滚（详见 WP-C4 迁移纪律）。
- 新字段可空或有安全默认；发送状态不因版本回滚重新变成可发送。
- 存量无溯源的 verified 招呼语：发送边界做文本级黑名单检查（不整批拒发），
  新写入一律携带完整溯源清单。
