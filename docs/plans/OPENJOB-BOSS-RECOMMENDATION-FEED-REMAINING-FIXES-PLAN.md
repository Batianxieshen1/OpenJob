# BOSS 推荐流剩余问题修复与最终放行计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task.完成每个 Batch 后暂停并报告，不要一次性跳过人工验收。

**Goal:** 修复 BOSS 推荐流当前剩余的混合来源进度错误、旧脚本绕过新管线、最大卡片数语义不清等问题，并通过自动化门禁和一次最新代码的真实 Chrome 小规模复验。

**Architecture:** 不改变推荐流总体架构。搜索流与推荐流继续共用同一个 `BossCollector`、同一个 Chrome 任务、同一个详情解析器、同一个 `_SharedProcessor`、同一套岗位身份去重/来源观察/风控/页面额度和人工投递边界。此次只做收尾修复，不新增第二套采集或投递系统。

**Tech Stack:** Python 3.10+、SQLite、pytest、Ruff、React/TypeScript、Vite、Puppeteer UI acceptance、OpenJob Browser Runtime、BOSS 专用 Chrome。

**Plan date:** 2026-09-20  
**Workspace:** `C:\Users\暴龙战士wink\Desktop\agent\OpenJob`

---

# 0. 验收基线与当前问题

## 0.1 已通过的基线

最近一次验收已经获得：

```text
定向推荐流测试：85 passed, 69 deselected
Python 全量测试：720 passed
Ruff：All checks passed
前端 npm run build：成功
UI acceptance：18 项通过
数据库 schema：9
真实 recommendation observation：15 条
```

Agent 执行时必须重新运行并记录新的真实结果，不能直接引用旧数字。

## 0.2 P1：混合来源进度被错误覆盖

真实混合采集记录中出现了：

```json
{
  "sources": {
    "search": {
      "status": "completed_with_shortage",
      "reason_code": "recommendation_scroll_limit"
    },
    "recommendation": {
      "status": "completed_with_shortage",
      "reason_code": "recommendation_scroll_limit"
    }
  },
  "source_results": {
    "search": {
      "status": "completed",
      "reason_code": "search_exhausted"
    },
    "recommendation": {
      "status": "completed_with_shortage",
      "reason_code": "recommendation_scroll_limit"
    }
  }
}
```

搜索流本身已经成功结束，但 `sources.search` 被推荐页最终状态覆盖。

问题位置：

```text
src/openjob/collection/orchestrator.py
```

当前逻辑对所有 `request_channels` 使用同一个平台级 `result.status/result.reason_code` 调用 `finish_source()`，导致搜索和推荐来源状态混淆。

## 0.3 P1：旧版 `scripts/collect_recommended.py` 绕过新管线

文件：

```text
scripts/collect_recommended.py
```

该脚本目前：

- 自己打开推荐页；
- 自己解析卡片；
- 自己调用 `insert_job()`；
- 写入 `source_keyword="推荐页"`；
- 没有写入 `source_channel="recommendation"`；
- 不经过 `CollectionOrchestrator`；
- 不经过统一 `job_source_observations`；
- 不经过统一推荐页额度和任务进度；
- 可能将推荐岗位错误记录为 `source_channel=search`。

已经通过临时数据库复现：

```text
source_channel = search
source_keyword = 推荐页
observation_count = 0
```

这与新推荐流产品规则冲突。

## 0.4 P2：最大卡片数的界面语义不准确

当前实现为了避免推荐页首屏全部是重复岗位时无法处理新候选，把 `recommendation_max_cards` 解释为：

> 最多处理多少个通过初步筛选的候选岗位。

因此真实运行中可能出现：

```text
配置最大卡片数：5
实际扫描卡片：15
实际处理候选：0 或不超过 5
```

这是当前实现的有意行为，但 UI 仍然显示“最大卡片数”，容易让用户误解。

本计划采用低风险修复：

- 保留当前处理逻辑；
- 不在本批次新增复杂的双上限配置；
- 将 UI、配置说明、README 和产品指南统一改为“最多处理候选数”；
- 明确说明重复岗位可能继续被扫描，但不会占用详情处理候选额度；
- BOSS 每日页面额度和详情页额度仍然是最终安全边界。

---

# 1. 执行规则

## 1.1 不可违反的边界

禁止：

- 将推荐页设置为默认开启；
- 并发操作两个 Chrome Tab；
- 点击“继续沟通”；
- 自动发送招呼语、简历或 HR 回复；
- 绕过 `PlatformAccessGuard`；
- 关闭事实校验或人工确认；
- 用 `source_keyword="推荐页"` 代替 `source_channel="recommendation"`；
- 使用 `git reset --hard` 或 `git clean -fd` 覆盖工作区；
- 删除用户的真实配置、数据库、简历、素材库或 Chrome 数据。

## 1.2 每个 Batch 的报告格式

```markdown
## Batch 名称

### 修改文件
- ...

### 修复内容
- ...

### 数据库/配置变化
- ...

### 测试命令与真实结果
```text
...
```

### 是否需要人工验收
- 不需要 / 需要

### 已知问题
- ...

### Commit
- ...
```

---

# 2. Batch A：修复混合来源进度状态

## 目标

确保 `sources` 与 `source_results` 对每个来源一致：

```text
search：只显示搜索流自己的结果
recommendation：只显示推荐页自己的结果
```

混合采集时必须得到类似：

```json
{
  "sources": {
    "search": {
      "status": "completed",
      "reason_code": "search_exhausted"
    },
    "recommendation": {
      "status": "completed_with_shortage",
      "reason_code": "recommendation_scroll_limit"
    }
  }
}
```

## 文件

- Modify: `src/openjob/collection/orchestrator.py`
- Test: `tests/test_collection_orchestrator.py`
- Test: `tests/test_boss_recommend_collector.py`
- Test: `tests/test_collection_runs.py`

## 实现规则

在 `CollectionOrchestrator.run()` 的平台结果处理处，不要对所有 source channel 直接使用平台级 `result.status/result.reason_code`。

应优先读取：

```python
source_result = (result.source_results or {}).get(channel)
```

推荐逻辑：

```python
for channel in request_channels:
    source_result = (result.source_results or {}).get(channel)
    if source_result:
        source_status = str(source_result.get("status") or result.status)
        source_reason = str(source_result.get("reason_code") or "")
    else:
        # 兼容智联/51job和没有来源结果旧 collector
        source_status = result.status
        source_reason = result.reason_code if result.status != "completed" else ""
    processor.finish_source(
        channel,
        status=source_status,
        reason_code=source_reason,
    )
```

注意：

- `source_results.search` 应该对应搜索阶段的真实状态；
- `source_results.recommendation` 应该对应推荐阶段的真实状态；
- 如果推荐阶段 blocked/stopped，推荐阶段保持 blocked/stopped；
- 搜索阶段已经完成时不能被推荐阶段改写；
- 总体平台状态仍可为 `completed_with_shortage`，但来源子状态必须独立准确；
- 不修改 `source_results` 的真实结果，只修复 `sources` 聚合状态。

## 步骤

- [ ] 新增混合来源回归测试：搜索成功、推荐分页上限；
- [ ] 断言 `sources.search.status == completed`；
- [ ] 断言 `sources.search.reason_code == search_exhausted`；
- [ ] 断言 `sources.recommendation.status == completed_with_shortage`；
- [ ] 断言 `sources.recommendation.reason_code == recommendation_scroll_limit`；
- [ ] 新增搜索成功、推荐 parser unsupported 的测试；
- [ ] 新增搜索成功、推荐 blocked 的测试；
- [ ] 新增搜索成功、推荐 user_stopped 的测试；
- [ ] 验证 `collection_runs.platform_states_json` 与内存 task progress 一致；
- [ ] 运行：

```powershell
python -m pytest -q tests/test_collection_orchestrator.py tests/test_boss_recommend_collector.py tests/test_collection_runs.py
```

- [ ] 提交：

```powershell
git add src/openjob/collection/orchestrator.py tests/test_collection_orchestrator.py tests/test_boss_recommend_collector.py tests/test_collection_runs.py
git commit -m "fix(collection): preserve per-source progress in mixed runs"
```

---

# 3. Batch B：处理旧版推荐采集脚本

## 目标

仓库中不能继续保留一条会错误写入推荐来源的采集路径。

## 推荐方案：将旧脚本改成安全兼容入口

不要继续维护旧版独立解析和直接入库逻辑。

将：

```text
scripts/collect_recommended.py
```

改为兼容入口，行为二选一，优先选择方案 1。

### 方案 1：转发到统一工作台管线

脚本只负责：

1. 加载 `config.yaml`；
2. 检查 Chrome/BOSS 登录态；
3. 调用 `CollectionOrchestrator`；
4. 使用：

```python
{
    "platform_order": ["boss"],
    "auto_score": False,
    "platforms": {
        "boss": {
            "keywords": [],
            "cities": [],
            "city_codes": {},
            "max_pages": 1,
            "sort": "default",
            "recruitment_filter": "",
            "source_channels": ["recommendation"],
            "recommendation_max_scrolls": 1,
            "recommendation_max_cards": 20,
            "recommendation_same_result_limit": 2,
        }
    },
}
```

5. 输出统一运行 ID、来源统计和失败原因；
6. 不允许直接调用 `insert_job()`；
7. 不允许生成伪造 JD；
8. 不允许写入 `source_keyword="推荐页"`；
9. 不允许绕过推荐页额度或风控。

如果需要保留 `--城市` 参数，必须在统一 pipeline 的候选过滤层实现，不能在脚本中手工拼接岗位记录。若本批次不实现城市过滤，应明确废弃该参数并提示用户使用工作台配置。

### 方案 2：安全废弃

如果不准备维护 CLI 入口，将脚本改为：

```text
打印：该脚本已废弃，请使用 OpenJob 工作台的“推荐页”采集来源
退出码：2
不打开 Chrome
不写数据库
```

不能让旧脚本继续执行错误逻辑。

## 步骤

- [ ] 先增加回归测试，确认旧脚本路径不会直接写入 `source_keyword="推荐页"`；
- [ ] 选择方案 1 或方案 2，并在报告中说明选择理由；
- [ ] 删除旧的 `EXTRACT_JS` 和直接 `insert_job()` 逻辑，或者完全迁移到统一 orchestrator；
- [ ] 更新脚本顶部用法说明；
- [ ] 更新 `README.md` 或 `docs/product-guide.md`，说明唯一推荐页入口；
- [ ] 测试：

```powershell
python -m pytest -q tests/test_boss_recommend.py tests/test_boss_recommend_collector.py tests/test_collection_orchestrator.py
```

- [ ] 提交：

```powershell
git add scripts/collect_recommended.py README.md docs/product-guide.md tests
git commit -m "fix(tools): route legacy recommendation script through safe pipeline"
```

---

# 4. Batch C：统一“最大卡片数”产品语义

## 目标

避免用户看到“最大卡片数 5”，实际扫描 15 张卡片后产生误解。

本批次不新增第二个复杂配置字段，保留当前安全实现，只修正文案和测试语义。

## 文件

- Modify: `src/openjob/web/frontend/src/components/dashboard/CollectJobsDialog.tsx`
- Modify: `src/openjob/web/frontend/src/pages/ConfigPage.tsx`
- Modify: `config.example.yaml`
- Modify: `README.md`
- Modify: `docs/product-guide.md`
- Test: `src/openjob/web/frontend/scripts/ui-acceptance.cjs`

## 统一文案

将用户可见的：

```text
最大卡片数
```

改成：

```text
最多处理候选数
```

增加说明：

```text
重复岗位不会占用详情处理候选额度，因此实际扫描卡片数可能高于此值；页面访问和详情页仍受 BOSS 安全额度限制。
```

如果保留后端字段名：

```text
recommendation_max_cards
```

可以继续保留，不要求本批次做数据库/配置字段重命名，以避免破坏旧配置。

## 测试要求

- [ ] UI acceptance 断言出现“最多处理候选数”；
- [ ] UI acceptance 断言出现重复岗位说明；
- [ ] 后端测试明确 `recommendation_max_cards` 限制的是通过 `on_list_candidate` 的处理候选数；
- [ ] 测试重复卡片较多时不会无限分页；
- [ ] 测试详情页访问仍受 `daily_detail_page_limit`；
- [ ] 更新配置示例和产品文档；
- [ ] 提交：

```powershell
git add src/openjob/web/frontend/src config.example.yaml README.md docs/product-guide.md
 git commit -m "docs(ui): clarify recommendation candidate limit semantics"
```

---

# 5. Batch D：自动化质量门禁

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

放行条件：

- Python 全量测试零失败；
- Ruff 零错误；
- 前端构建成功；
- UI acceptance 全部通过；
- Agent 报告新的真实测试数量和耗时；
- 没有用 `continue-on-error` 隐藏失败。

---

# 6. Batch E：最新代码真实人工复验

必须重启 OpenJob Web 工作台，确保加载的是最新代码，不能只复用旧进程。

## 6.1 复验前配置

保持：

```yaml
platforms:
  boss:
    source_channels: [search]
```

然后在采集窗口临时设置：

```text
来源：搜索流 + 推荐页
搜索最大页数：1
推荐最大分页：1
最多处理候选数：5
连续无新增：1
AI 评分：关闭
```

## 6.2 人工观察

确认：

- 搜索页和推荐页使用同一个采集任务；
- 没有并发抢占两个 Chrome Tab；
- 搜索流正常完成时显示 `搜索流：已完成`；
- 推荐页达到分页上限时只显示 `推荐页：completed_with_shortage`；
- 搜索流不能显示推荐页的 reason code；
- 推荐岗位仍然显示推荐页来源徽标；
- 双来源岗位显示“搜索流 + 推荐页”；
- 不点击“继续沟通”；
- 不打开聊天页；
- 不发送招呼语；
- 不发送简历；
- 不启动 `deliver`。

## 6.3 数据验收

任务完成后执行：

```powershell
python -c "import sqlite3; c=sqlite3.connect('data/openjob.db'); print(c.execute(\"SELECT source_channel, COUNT(*) FROM job_source_observations GROUP BY source_channel\").fetchall()); c.close()"
```

再执行：

```powershell
python -c "import sqlite3, json; c=sqlite3.connect('data/openjob.db'); c.row_factory=sqlite3.Row; r=c.execute(\"SELECT platform_states_json FROM collection_runs ORDER BY created_at DESC LIMIT 1\").fetchone(); print(json.dumps(json.loads(r['platform_states_json']), ensure_ascii=False, indent=2)); c.close()"
```

重点检查：

```text
sources.search.status == completed
sources.search.reason_code == search_exhausted
sources.recommendation.status == completed_with_shortage
sources.recommendation.reason_code == recommendation_scroll_limit
```

## 6.4 人工验收失败处理

如果出现：

```text
搜索流状态被推荐页覆盖
推荐页来源没有入库
任务自动发送
来源徽标错误
旧脚本仍能写入错误来源
```

立即停止推荐页灰度，回到：

```yaml
source_channels: [search]
```

并报告具体 task ID、run ID、错误截图和数据库记录。

---

# 7. Batch F：发布前工作区清理

## 目标

避免把验收临时产物误提交到正式代码。

当前工作区可能包含：

```text
.coverage
ui-acceptance-screenshots/*.png
OPENJOB-*.md
```

处理规则：

- 不使用 `git clean -fd`；
- 不删除用户明确需要的计划文档；
- `.coverage` 如果只是本次测试生成，可以删除或加入 `.gitignore`；
- UI 截图如果是 CI 产物，保留并明确用途；如果只是本地验收产物，不提交；
- 运行 `git status --short`，报告所有剩余未提交文件；
- 不要把 `config.yaml`、`data/openjob.db`、简历、素材库、Chrome Profile 加入提交。

---

# 8. 最终验收清单

## 代码与测试

- [ ] 混合来源 `sources` 状态与 `source_results` 按来源一致；
- [ ] 搜索流不会被推荐流 reason code 覆盖；
- [ ] 旧推荐脚本不再绕过统一 pipeline；
- [ ] 不再写入 `source_keyword="推荐页"`；
- [ ] 推荐页来源都有 `source_channel="recommendation"`；
- [ ] “最多处理候选数”文案和行为一致；
- [ ] Python 全量测试通过；
- [ ] Ruff 通过；
- [ ] 前端构建通过；
- [ ] UI acceptance 通过。

## 数据与安全

- [ ] 推荐页真实记录可查询；
- [ ] search/recommendation 双来源可同时存在；
- [ ] 默认配置仍为 `source_channels: [search]`；
- [ ] 推荐页不自动评分（关闭时）；
- [ ] 推荐页不自动发送任何内容；
- [ ] 页面额度和详情额度仍有效；
- [ ] 风控锁仍有效；
- [ ] 没有并发操作同一个 Chrome。

## 人工验收

- [ ] 使用最新代码重启工作台；
- [ ] 使用已登录专用 Chrome；
- [ ] 搜索 + 推荐小规模混合采集；
- [ ] 搜索流状态准确；
- [ ] 推荐页状态准确；
- [ ] 来源徽标准确；
- [ ] 没有发送副作用；
- [ ] 任务和数据库记录一致。

---

# 9. Agent 最终报告模板

```markdown
## 推荐流剩余问题修复报告

### 修改文件
- ...

### 混合来源状态修复
- search 状态：...
- recommendation 状态：...
- 是否还存在跨来源覆盖：是 / 否

### 旧脚本处理
- 方案：统一管线 / 安全废弃
- 是否还能写入 source_keyword=推荐页：否
- 是否还能绕过来源 observation：否

### 最大候选数语义
- UI 文案：...
- 配置字段：...
- 实际行为：...

### 测试结果
```text
python -m pytest -q
python -m ruff check ...
npm run build
node scripts/ui-acceptance.cjs
```

### 真实 Chrome 验收
- task_id：...
- run_id：...
- 来源配置：...
- search 结果：...
- recommendation 结果：...
- recommendation observation：...
- 是否发送任何内容：否

### 未提交文件
- ...

### Commit
- ...

### 结论
- 通过 / 暂缓，原因：...
```

最终只有在 Batch E 真实复验通过后，才能把推荐页视为完整 Beta 功能；在此之前保持：

```yaml
source_channels: [search]
```
