# BOSS 推荐流最终收尾与人工验收计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.完成每个 Batch 后暂停并报告，不要一次性跨过人工验收门槛。

**Goal:** 修复 BOSS 推荐流当前验收发现的数据一致性问题，并完成自动化质量门禁与真实登录态小规模端到端验收，使推荐页可以作为默认关闭、可控灰度的正式 Beta 功能使用。

**Architecture:** 保持现有设计：推荐页不是独立平台，不开启并发 Chrome，不建立第二套评分/投递链路。搜索流和推荐流共用 `BossCollector`、详情解析、`_SharedProcessor`、岗位身份去重、来源观察、页面额度、风控锁和统一 AI 评分；本计划只修复来源观察责任边界、删除清理、运行结果可观测性、UI 验收和真实浏览器验收。

**Tech Stack:** Python 3.10+、SQLite、pytest、Ruff、React/TypeScript、Vite、Puppeteer UI acceptance、OpenJob Browser Runtime、已登录 BOSS 专用 Chrome。

**Plan date:** 2026-09-19  
**Workspace:** `C:\Users\暴龙战士wink\Desktop\agent\OpenJob`

---

# 0. 当前验收结论与放行门槛

## 0.1 已确认通过

最近一次独立验收已经确认：

```text
推荐流定向测试：68 passed
Python 全量测试：704 passed
前端 npm run build：通过
Ruff：All checks passed
UI 基础验收：10 项 PASS
```

这些结果必须由 Agent 在本计划执行结束时重新运行并报告，不能直接引用旧结果。

## 0.2 当前必须修复的问题

### P1-A：新岗位来源观察被记录两次

当前新岗位可能经过：

```text
insert_job_if_new()
  → record_job_source_observation()

_SharedProcessor.save()
  → _record_observation()
```

第一次发现岗位时，`seen_count` 可能从合理的 `1` 变成 `2`。

### P1-B：永久删除岗位后来源观察成为孤儿记录

当前永久删除流程删除了：

```text
history
jobs
```

但没有显式删除：

```text
job_source_observations
```

因此岗位被永久删除后，来源观察仍然存在。

### P1-C：真实推荐页全链路还没有被本地数据库验证

当前代码和 fixture 测试通过，但本地数据库中尚未出现真实推荐来源记录：

```text
recommendation source observations = 0
recommendation collection runs = 0
```

必须使用用户已登录的专用 Chrome，用极小参数跑一次真实推荐页采集。

### P1-D：推荐页 UI acceptance 覆盖不足

当前 UI acceptance 已验证基础布局和定时采集，但没有强制验证：

- 推荐页开关存在；
- 推荐页默认关闭；
- 推荐页安全提示存在；
- 推荐页参数显示；
- 推荐页来源徽标；
- 推荐页来源进度。

### P2-E：非法来源通道不应静默回退

`source_channel` 如果出现未知值，不能静默转换为 `search`，否则来源数据可能被错误标记。

### P2-F：数据库迁移回填不能用全表 `COUNT(*)` 判断是否完成

迁移必须使用逐行幂等回填，避免出现部分回填后重启导致剩余岗位永远不回填。

## 0.3 推荐流默认策略

在本计划完成前后都必须保持：

```yaml
platforms:
  boss:
    source_channels: [search]
```

禁止因为测试通过就把推荐页改成默认开启。

---

# 1. 执行规则

## 1.1 开始前检查

执行 Agent 首先运行：

```powershell
cd C:\Users\暴龙战士wink\Desktop\agent\OpenJob
git status --short
git branch --show-current
git log -10 --oneline
python -m pytest -q tests/test_boss_recommend.py tests/test_boss_recommend_collector.py tests/test_job_source_observations.py tests/test_collection_safety_recommend.py
```

确认当前工作区有未提交文件时，不得使用以下命令覆盖用户文件：

```text
git reset --hard
git clean -fd
```

## 1.2 不允许修改的业务边界

本计划不允许：

- 开启推荐页默认采集；
- 并发打开两个 Chrome Tab 操作 BOSS；
- 点击推荐卡片内的“继续沟通”；
- 自动发送招呼语、简历或 HR 回复；
- 绕过 `PlatformAccessGuard`；
- 绕过人工确认队列；
- 因为测试方便而关闭风控锁；
- 把 `recommendation` 伪装成 `search`；
- 把非法 source channel 静默吞掉；
- 使用真实岗位 HTML、Cookie、联系方式或个人数据提交到 Git。

## 1.3 每个 Batch 必须报告

```markdown
## Batch 名称

### 修改文件
- ...

### 数据库变化
- ...

### 测试命令与真实结果
```text
...
```

### 人工验收是否需要执行
- 不需要 / 需要

### 已知问题
- ...

### Commit
- ...
```

---

# 2. Batch A：修复来源观察重复计数

## 目标

每次逻辑观察只增加一次 `seen_count`：

```text
第一次新岗位从搜索发现：seen_count = 1
第二次同来源重新发现：seen_count = 2
第一次从推荐页发现同一岗位：新增 recommendation observation，seen_count = 1
```

## 文件

- Modify: `src/openjob/db.py`
- Modify: `src/openjob/collection/orchestrator.py`
- Test: `tests/test_job_source_observations.py`
- Test: `tests/test_collection_orchestrator.py`

## 推荐实现

来源观察必须只有一个明确责任层。推荐采用：

```text
insert_job_if_new()
  只执行 jobs INSERT OR IGNORE

_SharedProcessor.inspect/save()
  负责每个采集候选的 source observation
```

从 `insert_job_if_new()` 移除或禁用隐式 `record_job_source_observation()` 调用。保留 `record_job_source_observation()` 公共函数，供 `_SharedProcessor` 明确调用。

如果为了兼容历史调用必须保留数据库层行为，则必须增加显式参数：

```python
insert_job_if_new(
    conn,
    job,
    record_source_observation=False,
)
```

默认值必须避免重复写入，不能让旧调用和 `_SharedProcessor` 同时自动记录。

## 步骤

- [ ] **Step A1：先新增失败测试**

在 `tests/test_job_source_observations.py` 增加：

```python
def test_first_new_job_observation_seen_count_is_one():
    # 使用临时 SQLite
    # 通过 CollectionOrchestrator 写入一个新岗位
    # 查询 job_source_observations
    # 断言首次观察 seen_count == 1
    ...
```

测试不能只直接调用 `record_job_source_observation()`，必须走真实的采集共享处理流程，覆盖 `insert_job_if_new()` 与 `_SharedProcessor.save()` 的边界。

- [ ] **Step A2：运行失败测试**

```powershell
python -m pytest -q tests/test_job_source_observations.py::test_first_new_job_observation_seen_count_is_one
```

预期：在修复前能够复现 `seen_count == 2` 或等价失败。

- [ ] **Step A3：修改唯一责任层**

实现后必须保证：

- 新岗位首次保存只创建一条 observation；
- duplicate 岗位只记录一次 observation；
- `save()` 不因 `insert_job_if_new()` 的返回值再次重复记录；
- `collection_run_id` 正确记录最近一次采集运行；
- 岗位状态、评分、招呼语和简历字段不被来源观察修改。

- [ ] **Step A4：增加完整场景测试**

至少覆盖：

```text
新 search 岗位：search seen_count=1
同一 search 岗位第二次采集：search seen_count=2
同一岗位第一次从 recommendation 发现：新增 recommendation seen_count=1
同一岗位再次从 recommendation 发现：recommendation seen_count=2
同一岗位 search + recommendation：jobs 只有一行，observations 有两条
```

- [ ] **Step A5：运行定向测试**

```powershell
python -m pytest -q tests/test_job_source_observations.py tests/test_collection_orchestrator.py
```

- [ ] **Step A6：提交**

```powershell
git add src/openjob/db.py src/openjob/collection/orchestrator.py tests/test_job_source_observations.py tests/test_collection_orchestrator.py
git commit -m "fix(collection): count each source observation once"
```

## Batch A 验收标准

```text
首次新增岗位 source observation.seen_count == 1
所有来源观察重复计数测试通过
原有跨来源去重测试仍通过
```

---

# 3. Batch B：修复永久删除孤儿来源记录

## 目标

永久删除岗位时同步删除来源观察：

```text
删除前：jobs=1, observations=1
删除后：jobs=0, observations=0
```

软删除不能删除来源观察，因为回收站恢复后仍需要来源信息。

## 文件

- Modify: `src/openjob/db.py`
- Test: `tests/test_job_source_observations.py`
- Test: `tests/test_recycle_bin.py`
- Test: `tests/test_web_api_routes.py`

## 步骤

- [ ] **Step B1：先新增失败测试**

增加：

```python
def test_permanent_delete_removes_source_observations():
    ...
```

测试流程：

1. 创建临时数据库；
2. 插入一条 jobs；
3. 插入对应 job_source_observations；
4. 执行 soft delete；
5. 确认 observation 仍然存在；
6. 执行 permanent delete；
7. 确认 jobs 和 observation 都不存在。

- [ ] **Step B2：运行失败测试**

```powershell
python -m pytest -q tests/test_job_source_observations.py::test_permanent_delete_removes_source_observations
```

- [ ] **Step B3：在同一事务内显式删除**

`permanent_delete_jobs()` 的事务顺序使用：

```python
with conn:
    conn.execute("DELETE FROM history WHERE job_id IN (...) ", ids)
    conn.execute("DELETE FROM job_source_observations WHERE job_id IN (...) ", ids)
    conn.execute("DELETE FROM jobs WHERE id IN (...) AND deleted_at IS NOT NULL", ids)
```

删除数量校验失败时必须整体回滚。

不要求本批次依赖 SQLite 外键级联；显式删除是主要保证。

- [ ] **Step B4：增加回收站回归测试**

确认：

- soft delete 保留 observations；
- restore 后 observations 仍可查询；
- permanent delete 同时删除 jobs/history/observations；
- 有发送历史的受保护岗位仍然不能永久删除；
- 批量删除部分失败时 observation 不被误删。

- [ ] **Step B5：提交**

```powershell
git add src/openjob/db.py tests/test_job_source_observations.py tests/test_recycle_bin.py tests/test_web_api_routes.py
git commit -m "fix(db): remove source observations with permanent job deletion"
```

---

# 4. Batch C：修复来源通道校验和数据库迁移幂等性

## 目标

让来源数据宁可报错，也不能悄悄标错；让 v8 → v9 迁移可以被中断后重跑。

## 文件

- Modify: `src/openjob/db.py`
- Modify: `src/openjob/collection/models.py`
- Modify: `src/openjob/collection/orchestrator.py`
- Test: `tests/test_job_source_observations.py`
- Test: `tests/test_collection_orchestrator.py`

## C1：非法 source channel 拒绝

当前非法值不能自动 fallback 为 `search`。

推荐实现：

```python
from openjob.collection.models import COLLECTION_CHANNEL_LABELS

channel = str(job.get("source_channel") or "").strip()
if not channel:
    channel = "search"  # 仅兼容旧数据/旧调用未传字段
if channel not in COLLECTION_CHANNEL_LABELS:
    raise ValueError(f"非法来源通道：{channel}")
```

兼容规则：

- 缺失或空字符串可以按旧行为视为 `search`；
- 明确提供未知非空值必须抛错；
- `boss` 允许 `search` 和 `recommendation`；
- 智联和 51job 不允许 `recommendation`；
- 错误不能写入 jobs，也不能写入 observation。

### C1 测试

- [ ] 空 source channel 兼容旧调用；
- [ ] `source_channel="recommend"` 抛 `ValueError`；
- [ ] `source_channel="boss_recommend"` 抛 `ValueError`；
- [ ] 非 BOSS recommendation 被 options 校验拒绝；
- [ ] 异常发生后 jobs 和 observations 都没有脏数据。

## C2：迁移逐行幂等回填

将 `_migrate_v2_9()` 的：

```python
SELECT COUNT(*)
if count == 0:
    INSERT ... SELECT ...
```

改为：

```sql
INSERT OR IGNORE INTO job_source_observations (
    job_id,
    source_platform,
    source_channel,
    source_label,
    source_keyword,
    source_city,
    source_city_code
)
SELECT
    id,
    COALESCE(source_platform, 'boss'),
    'search',
    '搜索流',
    COALESCE(source_keyword, ''),
    COALESCE(city, ''),
    COALESCE(source_city_code, '')
FROM jobs;
```

如果 SQLite 约束环境不适合 `INSERT OR IGNORE`，使用 `WHERE NOT EXISTS`，但必须达到相同效果：

- 已回填岗位不重复；
- 只回填缺失岗位；
- 部分回填后重新启动会补齐剩余岗位；
- 不会把旧岗位猜成 recommendation；
- 不覆盖已有 recommendation observation。

### C2 测试

- [ ] 创建 3 个 jobs；只预插入 1 个 observation；执行迁移；断言最终 3 个 jobs 都有 search observation；
- [ ] 重复执行迁移；断言 observation 数量不增加；
- [ ] 已有 recommendation observation 的岗位不会被覆盖；
- [ ] v8 数据库升级到 v9 时数据完整。

- [ ] **Step C3：运行测试并提交**

```powershell
python -m pytest -q tests/test_job_source_observations.py tests/test_collection_orchestrator.py tests/test_collection_runs.py

git add src/openjob/db.py src/openjob/collection/models.py src/openjob/collection/orchestrator.py tests/test_job_source_observations.py tests/test_collection_orchestrator.py tests/test_collection_runs.py
git commit -m "fix(collection): validate source channels and make provenance migration resumable"
```

---

# 5. Batch D：修复推荐页运行结果和进度持久化

## 目标

推荐页正常完成、解析失败、风控停止或用户停止时，`collection_runs` 和 API 都必须保留推荐来源阶段信息。

## 文件

- Modify: `src/openjob/collection/platforms/boss.py`
- Modify: `src/openjob/collection/orchestrator.py`
- Modify: `src/openjob/collection_run_store.py`（如需要）
- Test: `tests/test_boss_recommend_collector.py`
- Test: `tests/test_collection_runs.py`
- Test: `tests/test_web_api_routes.py`

## 必须检查的路径

推荐页以下退出路径都必须记录 `source_results.recommendation`：

- 正常分页结束；
- 达到分页上限；
- 达到卡片上限；
- 连续无新增；
- parser unsupported；
- 推荐页打开失败；
- 验证码/登录失效/拦截；
- 用户 stop_event；
- 详情页连续失败；
- 推荐页安全额度到达。

当前实现中，推荐阶段存在“提前 return 时 `source_results` 可能未带回最终结果”的风险。请用模拟浏览器逐条验证，不要只看正常路径。

## 验收字段

`collection_runs.platform_states_json.boss` 至少保留：

```json
{
  "current_source": "recommendation",
  "sources": {
    "search": {},
    "recommendation": {}
  },
  "source_results": {
    "recommendation": {
      "status": "completed_with_shortage",
      "reason_code": "recommendation_parser_unsupported",
      "message": "..."
    }
  }
}
```

- [ ] 为所有提前返回路径补测试；
- [ ] 确保 `source_results` 不被 finally 中的默认 completed 覆盖；
- [ ] 确保 blocked/stopped 也有可读 reason code；
- [ ] API `/api/collection/runs/<run_id>` 能读到来源阶段；
- [ ] 前端刷新后仍能显示来源阶段，不只依赖内存 task。

运行：

```powershell
python -m pytest -q tests/test_boss_recommend_collector.py tests/test_collection_runs.py tests/test_web_api_routes.py
```

提交：

```powershell
git add src/openjob/collection/platforms/boss.py src/openjob/collection/orchestrator.py src/openjob/collection_run_store.py tests/test_boss_recommend_collector.py tests/test_collection_runs.py tests/test_web_api_routes.py
git commit -m "fix(collection): persist recommendation source outcomes on every exit"
```

---

# 6. Batch E：补齐推荐页 UI 自动化验收

## 文件

- Modify: `src/openjob/web/frontend/scripts/ui-acceptance.cjs`
- Modify: `src/openjob/web/frontend/src/components/dashboard/CollectJobsDialog.tsx` only if selectors/accessible labels不足
- Modify: `src/openjob/web/frontend/src/components/dashboard/JobsTable.tsx` only if source badge selector不足
- Test: frontend build and UI acceptance

## UI acceptance 必须新增

### E1：推荐页默认关闭

打开采集弹窗，断言：

- “推荐页” checkbox 存在；
- 默认未勾选；
- 默认来源包含“搜索流”。

### E2：推荐页配置显示

勾选推荐页后断言出现：

```text
推荐页来自 BOSS 个性化推荐
不会自动发送招呼语、简历或回复
分页轮次
最大卡片数
连续无新增上限
```

### E3：只推荐页模式

取消搜索流并勾选推荐页后断言：

- 关键词和城市不再被前端强制判定为必填；
- 页面出现“推荐页不依赖搜索关键词”的说明；
- 开始按钮仍然是“开始采集”，不能出现“自动投递”。

### E4：来源徽标

使用 mock API 或测试 fixture 返回：

```json
{
  "source_channel": "recommendation",
  "source_labels": ["推荐页"]
}
```

断言岗位列表出现“推荐页”。

再返回：

```json
{
  "source_channels": ["search", "recommendation"],
  "source_labels": ["搜索流", "推荐页"]
}
```

断言岗位列表出现“搜索流 + 推荐页”或两个等价来源标识。

### E5：进度来源状态

用 mock workbench response 返回 `sources.recommendation`，断言页面出现：

```text
推荐页
扫描
新增
重复
```

## UI 执行命令

```powershell
cd C:\Users\暴龙战士wink\Desktop\agent\OpenJob\src\openjob\web\frontend
npm run build
node scripts/ui-acceptance.cjs
cd ..\..\..\..
```

提交：

```powershell
git add src/openjob/web/frontend/scripts/ui-acceptance.cjs src/openjob/web/frontend/src
 git commit -m "test(ui): cover BOSS recommendation source controls and badges"
```

---

# 7. Batch F：自动化质量门禁

## 必须执行

```powershell
cd C:\Users\暴龙战士wink\Desktop\agent\OpenJob
python -m pytest -q
python -m ruff check src/ --select=E9,F63,F7,F82
cd src\openjob\web\frontend
npm ci
npm run build
node scripts/ui-acceptance.cjs
cd ..\..\..\..
```

## 放行标准

- Python 全量测试零失败；
- Ruff 零错误；
- 前端构建成功；
- UI acceptance 全部 PASS；
- 不允许 `continue-on-error` 掩盖失败；
- Agent 报告真实测试数量、耗时和失败日志摘要；
- 不得只报告推荐流新增测试通过。

---

# 8. Batch G：真实推荐页小规模人工端到端验收

> 这是必须由用户或用户明确授权的 Agent 在真实已登录 BOSS 专用 Chrome 中执行的部分。纯模拟测试不能替代本批次。

## 8.1 人工验收前准备

用户准备：

- 已登录 BOSS 的专用 Chrome；
- Chrome Browser Runtime/CDP 已连接；
- OpenJob 工作台已启动；
- 不使用日常主 Chrome Profile；
- 确认当前不是验证码或风险锁状态；
- 确认当前推荐页功能仍然默认关闭。

人工验收只能使用推荐页小参数：

```text
来源：只勾选推荐页
分页轮次：1
最大卡片数：3
连续无新增：1
AI 评分：关闭
```

不要在第一次人工验收中：

- 勾选搜索流和推荐页同时跑多页；
- 开启 AI 评分；
- 点击任何“发送”“确认投递”“继续沟通”按钮；
- 把推荐页直接用于批量投递。

## 8.2 人工操作步骤

### Step G1：打开专用 Chrome 和 OpenJob

确认：

```text
OpenJob 工作台可访问
Chrome 已连接
BOSS 推荐页可以打开
```

### Step G2：打开采集弹窗

确认：

- 推荐页选项存在；
- 默认没有勾选推荐页；
- 默认搜索流仍然存在；
- 提示文字明确写着不会自动发送内容。

### Step G3：只勾选推荐页

确认：

- 搜索流取消；
- 推荐页勾选；
- 关键词和城市可以留空；
- 高级参数为 1 / 3 / 1；
- AI 评分关闭；
- 点击“开始采集”。

### Step G4：观察浏览器行为

必须人工观察：

- 是否打开 `https://www.zhipin.com/web/geek/recommend`；
- 是否没有打开搜索 URL；
- 是否没有打开私有 API；
- 是否没有点击“继续沟通”；
- 是否没有发送招呼语；
- 是否没有发送简历；
- 推荐页分页最多打开 1 页；
- 详情页最多打开不超过 3 个候选岗位；
- 任务完成后 Tab 被关闭或回收。

### Step G5：观察工作台进度

确认推荐页来源状态显示：

```text
推荐页
扫描数量
新增数量
重复数量
解析失败数量
```

如果推荐页没有岗位，也必须显示明确原因，而不是只显示“完成 0 条”。

### Step G6：检查数据库/API

在工作台任务结束后执行：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/openjob.db'); print(c.execute(\"SELECT source_channel, COUNT(*) FROM job_source_observations GROUP BY source_channel\").fetchall()); c.close()"
```

期望看到 recommendation 行，或者如果本轮全部重复，至少看到已有岗位的 recommendation observation。

再访问：

```text
http://127.0.0.1:8686/api/collection/runs
```

确认最近一次运行的 options/platform_states 中包含：

```text
recommendation
current_source
sources.recommendation
source_results.recommendation
```

### Step G7：检查岗位来源徽标

在岗位池中确认：

- 推荐页新岗位显示“推荐页”；
- 同时被搜索流和推荐页发现的岗位显示“搜索流 + 推荐页”；
- 不会把推荐页显示成某个搜索关键词；
- 不会因为缺少来源字段导致岗位卡片崩溃。

### Step G8：确认没有发送副作用

人工确认以下数据没有新增：

- `history` 中没有新的 `sent`；
- `history` 中没有新的 `approved`；
- 没有启动 `deliver` task；
- 没有打开 BOSS 聊天页；
- 没有点击“继续沟通”。

可以使用：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/openjob.db'); print(c.execute(\"SELECT action, COUNT(*) FROM history WHERE created_at >= datetime('now','-10 minutes') GROUP BY action\").fetchall()); c.close()"
```

## 8.3 人工验收记录模板

用户或执行 Agent 必须记录：

```markdown
## BOSS 推荐页真实端到端验收

日期：2026-09-19
Chrome Profile：专用 Profile / 未使用主 Profile
OpenJob 版本：...
BOSS 页面：已登录 / 未登录

### 配置
- 来源：推荐页
- 最大分页：1
- 最大卡片：3
- 连续无新增：1
- AI 评分：关闭

### 结果
- 推荐页是否打开：通过 / 失败
- 是否打开搜索页：是 / 否
- 采集卡片数：...
- 新增岗位数：...
- 重复岗位数：...
- 详情成功数：...
- 解析失败数：...
- recommendation observation 数：...
- collection run 是否记录 recommendation：通过 / 失败
- 来源徽标：通过 / 失败
- 是否发送招呼语：否
- 是否发送简历：否
- 是否打开聊天页：否
- 是否出现验证码/风控：否 / 是，说明...

### 结论
- 通过 / 不通过
- 证据截图或脱敏日志：...
```

---

# 9. Batch H：真实灰度验收（人工确认通过后才能执行）

只有 Batch G 通过后，才允许进行第二轮小规模灰度：

```text
来源：搜索流 + 推荐页
搜索最大页数：1
推荐最大分页：1
推荐最大卡片：5
连续无新增：1
AI 评分：关闭
```

人工确认：

- 搜索流和推荐页仍然是同一个采集任务；
- 两个来源不是两个并发 Chrome 任务；
- 同一岗位只有一行 jobs；
- 同一岗位可以有两个 source observations；
- AI 关闭时没有 AI 请求；
- 任务完成后没有发送行为；
- BOSS 页面访问额度记录合理；
- recommendation 不会覆盖 search 的 `source_keyword`；
- 推荐页来源不会影响投递确认安全门槛。

---

# 10. 最终放行标准

推荐流只有同时满足以下条件才可以标记为“完成”：

## 自动化标准

- [ ] 首次新增岗位 observation 的 `seen_count == 1`；
- [ ] 重复观察计数符合预期；
- [ ] 永久删除会清理 observations；
- [ ] 软删除/恢复不会丢 observations；
- [ ] 非法 source channel 不会静默回退；
- [ ] v8→v9 迁移支持部分回填后恢复；
- [ ] 推荐页所有退出路径都持久化来源结果；
- [ ] 推荐页 UI acceptance 全部通过；
- [ ] Python 全量测试通过；
- [ ] 前端构建通过；
- [ ] Ruff 通过。

## 人工标准

- [ ] 已登录专用 Chrome 真实推荐页成功打开；
- [ ] 推荐页只读解析，没有点击继续沟通；
- [ ] 推荐页没有打开搜索 URL（只推荐页模式）；
- [ ] 推荐页分页上限生效；
- [ ] 详情页可以复用并完成解析，或失败原因清楚；
- [ ] 推荐来源写入数据库；
- [ ] 来源徽标显示正确；
- [ ] 推荐页不会自动评分（如果关闭 AI）；
- [ ] 推荐页不会自动发送任何内容；
- [ ] 没有触发不必要的验证码或风险锁；
- [ ] 混合来源小规模运行通过。

---

# 11. 明确告诉 Agent：哪些事情必须人工验收

下面这些不能只用 pytest 或 mock 测试替代：

## 必须人工确认 1：真实推荐页 DOM

必须确认真实登录态页面仍然是：

```text
https://www.zhipin.com/web/geek/recommend
```

并且当前页面仍然存在：

```text
li.item-boss
a[href*="/job_detail/"]
```

## 必须人工确认 2：推荐页分页行为

必须真实确认：

- `?page=2` 能进入下一页；
- 最大分页参数有效；
- 普通滚动不会被误认为分页加载；
- 末页能正确结束；
- 不会无限打开页面。

## 必须人工确认 3：详情页兼容性

必须真实确认推荐页链接打开详情页后：

- 职位标题正确；
- 公司正确；
- JD 正确；
- 详情 URL 没有把 `securityId` 和 `ka` 写入岗位身份；
- 不会把推荐页卡片内容误当个人事实。

## 必须人工确认 4：安全边界

必须观察：

- 不点击继续沟通；
- 不打开聊天页；
- 不发送招呼语；
- 不发送简历；
- 不自动回复；
- 不绕过人工确认；
- 不读取或输出 Cookie。

## 必须人工确认 5：来源显示

必须在真实岗位池中确认：

- 推荐页岗位显示“推荐页”；
- 双来源岗位显示“搜索流 + 推荐页”；
- 推荐页不会冒充搜索关键词；
- 来源缺失时 UI 不崩溃。

## 必须人工确认 6：任务结束和清理

必须确认：

- 推荐任务完成后不会继续后台分页；
- Tab 会关闭或被正常回收；
- 工作台可以继续进行普通搜索采集；
- 任务历史可以查询；
- 重启工作台后不会重复执行同一计划时间槽。

## 必须人工确认 7：风险和额度

必须确认：

- 推荐页访问会计入 BOSS 额度；
- 达到推荐页额度后停止；
- 风险页出现时任务停止；
- 不会继续跳到其他 BOSS 来源；
- 普通采集任务仍能看到风险锁状态。

---

# 12. 最终建议

执行顺序必须是：

```text
Batch A 来源观察计数
→ Batch B 删除清理
→ Batch C 来源校验和迁移
→ Batch D 运行结果持久化
→ Batch E UI acceptance
→ Batch F 自动化门禁
→ Batch G 真实推荐页小规模人工验收
→ Batch H 搜索 + 推荐混合灰度
```

在 Batch G 通过之前，推荐页保持关闭：

```yaml
source_channels: [search]
```

在 Batch H 通过之前，不建议在每日定时采集中加入推荐页。

最终产品承诺必须保持：

> **推荐页只扩大岗位发现范围，不改变岗位事实，不改变用户人工投递控制权，也不允许推荐页采集绕过 OpenJob 的安全边界。**
