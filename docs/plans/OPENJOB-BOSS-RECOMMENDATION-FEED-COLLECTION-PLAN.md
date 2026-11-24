# BOSS 推荐页采集 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Each task has checkbox steps, exact files, tests, and an explicit checkpoint. Do not skip the discovery task because the authenticated BOSS 推荐页 DOM is the main external uncertainty.

**Goal:** 在不破坏现有搜索采集、去重、评分、定时采集和人工确认投递边界的前提下，为 BOSS 直聘增加 `https://www.zhipin.com/web/geek/recommend` 推荐页采集，并让推荐页岗位与搜索流进入同一套详情解析、过滤、去重、入库、AI 评分和任务进度管线。

**Architecture:** 推荐页不是第二套采集系统，也不通过新线程并发抢同一个 Chrome。它是现有 `BossCollector` 内的第二个采集来源阶段：默认先跑搜索流，再跑推荐流；两者共用一个浏览器 Tab、同一个安全额度/风控锁、同一个 `CollectorHooks`、同一个数据库写入器、同一个去重键和同一次自动评分。来源通过 `search` / `recommendation` 结构化记录，UI 显示为“搜索流”/“推荐页”。

**Tech Stack:** Python 3.10+、SQLite、Bottle、React + TypeScript、BOSS Browser Runtime、Chrome DOM `evaluate`、现有 `PageThrottle`、现有 `PlatformAccessGuard`、pytest、前端 `npm run build`、现有 UI acceptance。

**Plan date:** 2026-09-19  
**Workspace:** `C:\Users\暴龙战士wink\Desktop\agent\OpenJob`

---

## 0. 结论先行：是否值得做、怎样做才不会把架构做坏

### 0.1 结论

**建议做，价值明确，工作量属于“中等偏上”，不是只增加一段 JS。**

推荐页可以补足搜索关键词覆盖不到的岗位，尤其适合用户已经登录并被平台建立了兴趣画像的账号。它与 OpenJob 当前架构的契合度较高，因为项目已经具备：

- 平台专属采集器 `BossCollector`；
- 统一 `CollectorHooks`；
- `_SharedProcessor` 负责过滤、去重、保存和进度；
- `source_platform/source_job_id/source_keyword` 来源字段；
- `CollectionOrchestrator` 统一平台队列；
- 新岗位 ID 集合完成后统一 `auto_score`；
- BOSS 详情页、风险检测、页面额度和停止事件。

真正需要处理的难点有五个：

1. 推荐页 DOM 结构、懒加载方式和卡片字段必须用登录态 Chrome 做一次真实勘察；
2. 推荐页可能不是传统的 `page=2` 分页，而是滚动/动态加载，必须有明确终止条件；
3. 推荐岗位可能已经从搜索流入库，不能只标记一个覆盖式来源，否则会丢失“同时来自搜索和推荐”的信息；
4. 推荐页会增加 BOSS 页面访问和详情页访问，不能绕过现有风控预算；
5. “与搜索流并行”不能解释为两个线程同时操作一个 Chrome，否则会造成页面抢占、重复详情访问和风控风险。

### 0.2 对“并行”的准确解释

本计划将“并行”定义为：

> **同一次采集任务中同时支持搜索来源和推荐来源，二者共用同一个管线并分别显示进度；不是在两个线程或两个 Tab 中并发操作 BOSS。**

第一版固定采用：

```text
同一个 collect 任务
  ├─ 搜索流阶段：搜索组合 → 搜索列表 → 详情 → 共享处理器
  └─ 推荐页阶段：推荐页 → 滚动加载 → 详情 → 共享处理器
                         ↓
           同一套去重 / 过滤 / 入库 / 进度 / 评分
```

推荐页阶段默认在搜索流之后串行执行。后续如果真实采集证明必须交错执行，也只能在同一个采集线程和同一个浏览器互斥锁内做“阶段交错”，不能直接增加并发 Tab。

### 0.3 推荐的发布策略

- 新配置默认 `source_channels: ["search"]`，**推荐页默认关闭**；
- 首次实现先通过 fixture、模拟浏览器和小规模真实手工验收；
- 真实页面解析稳定后，用户可在采集窗口打开“推荐页”；
- 推荐页出现验证码、登录失效、请求拦截时沿用 BOSS 现有安全停机逻辑；
- 推荐页解析器无法确认页面结构时必须显式返回“推荐页解析不受支持/结构发生变化”，不能把空列表伪装成“没有推荐岗位”；
- 不增加任何自动发送、自动回复、自动投递能力；推荐页岗位仍必须经过现有人工确认链路。

---

# 1. 当前 OpenJob 架构盘点

## 1.1 真实调用链

```text
React CollectJobsDialog
    ↓ POST /api/workbench/task
Bottle server.py: api_workbench_task_start
    ↓ WorkbenchTaskRunner.start("collect", ...)
server.py: _execute_collect
    ↓ 注入 stop_event / progress callback / log callback
CollectionOrchestrator.run(options)
    ↓ 按 platform_order 串行执行
CollectorRegistry.get("boss") / 特殊构造 BossCollector
    ↓ BossCollector.collect(request, hooks)
Browser Runtime
    ↓ new_tab / navigate / wait_for_load / evaluate / scroll
BOSS 搜索列表 JS → BOSS 详情页 JS
    ↓ JobCandidate
_SharedProcessor.inspect / save
    ↓ job_identity_exists + 规则过滤 + insert_job_if_new
SQLite jobs
    ↓ unique_new_ids
score_jobs（仅 auto_score=true 且本轮新增岗位）
    ↓ Web progress / collection_runs / task log
前端岗位池与任务状态
```

## 1.2 关键模块职责

| 文件 | 当前职责 | 推荐页改造原则 |
|---|---|---|
| `src/openjob/collection/models.py` | 请求、候选岗位、进度和结果数据契约 | 添加来源通道字段，保持旧调用兼容 |
| `src/openjob/collection/base.py` | `CollectorHooks` 和采集错误类型 | 不另建推荐专用保存回调 |
| `src/openjob/collection/orchestrator.py` | 平台顺序、共享过滤/保存器、采集运行状态、自动评分 | 不新增第二套评分或入库路径 |
| `src/openjob/collection/platforms/boss.py` | BOSS 搜索、详情、风控、页面额度和节流 | 增加推荐来源阶段，共享详情和安全逻辑 |
| `src/openjob/collection/registry.py` | 平台 collector 注册 | 推荐页属于 BOSS，不新增 `boss_recommend` 平台 |
| `src/openjob/db.py` | jobs 表、source identity、迁移和查询 | 增加来源通道和多来源观察记录 |
| `src/openjob/platform_safety.py` | 平台全局页面额度、阶段额度、风险锁 | 推荐页计入全局额度并有独立列表页额度 |
| `src/openjob/web/tasks.py` | 单任务互斥、停止、后台执行 | 推荐页不得绕过任务互斥 |
| `src/openjob/web/server.py` | 采集 API、任务、工作台状态、岗位响应 | 返回来源通道/标签和推荐阶段进度 |
| `src/openjob/web/frontend/src/components/dashboard/CollectJobsDialog.tsx` | 采集配置弹窗 | BOSS 增加推荐页开关和成本提示 |
| `src/openjob/web/frontend/src/pages/ConfigPage.tsx` | 全局配置页 | 增加推荐页默认配置和安全额度说明 |
| `tests/test_boss_collector.py` | BOSS collector 测试 | 增加推荐页导航、滚动、解析和停止测试 |
| `tests/test_collection_orchestrator.py` | 共用处理和运行队列测试 | 增加跨来源去重、一次评分、来源观察测试 |
| `tests/test_collection_safety.py` | 页面额度和风险锁测试 | 增加推荐页独立额度和全局额度测试 |
| `src/openjob/web/frontend/scripts/ui-acceptance.cjs` | UI 验收 | 增加推荐页配置与来源徽标验收 |

## 1.3 当前实现的重要约束

### 约束 A：当前平台采集队列是串行的

`CollectionOrchestrator.run()` 按 `platform_order` 循环，BOSS 使用一个 collector 和一个数据库连接。`CollectJobsDialog` 也明确告诉用户“平台串行采集”。推荐页不能默认开新的线程或另一个 collector 同时操作相同 Chrome。

### 约束 B：当前 BOSS 采集器已经拥有通用详情流程

搜索列表只负责抽取职位卡片，之后：

1. `generate_boss_job_id(url)` 生成岗位身份；
2. `on_list_candidate` 先查重和职位/公司过滤；
3. 通过后才打开详情页；
4. 详情页执行 `JS_EXTRACT_DETAIL`；
5. `on_candidate` 进入 JD 过滤和入库。

推荐页应该尽量只新增“推荐卡片列表 → `JobCandidate`”这一段，不要复制详情解析和保存流程。

### 约束 C：当前去重身份是平台 + source_job_id

`jobs` 有 `source_platform/source_job_id/source_keyword/source_city_code`。`idx_jobs_source_identity` 对 `(source_platform, source_job_id)` 建唯一索引。推荐页岗位只要能提取同一个 `/job_detail/...` 身份，就可以和搜索流天然去重。

### 约束 D：当前自动评分只处理本轮新增岗位

`CollectionOrchestrator` 在所有平台/来源阶段结束后，对 `unique_new_ids` 调用一次 `score_jobs`。推荐页重复命中已有岗位时不应再次调用 AI；推荐页新岗位才加入同一个 `unique_new_ids` 集合。

### 约束 E：当前任务已经具备持久化运行记录

`collection_runs` 会保存 options、platform_states、collected_job_ids、current_platform、stop_reason 和 error。推荐页的阶段进度应存入现有 `platform_states_json`，不要为推荐页再建一套孤立的任务表。

---

# 2. 功能边界

## 2.1 本次必须做

- BOSS `web/geek/recommend` 页面访问；
- 推荐页列表卡片 DOM 解析；
- 推荐页滚动/动态加载的有限轮次控制；
- 推荐卡片与现有 BOSS 详情页解析衔接；
- 推荐岗位进入现有职位/公司/JD 过滤；
- 推荐岗位与搜索岗位共用 BOSS 岗位身份去重；
- 推荐岗位的新岗位进入现有自动评分集合；
- 推荐来源记录为结构化 `recommendation`，界面显示“推荐页”；
- 同一岗位同时来自搜索和推荐时，保留两个来源，不覆盖历史来源；
- 推荐页列表访问计入独立推荐页额度和 BOSS 全局页面额度；
- 推荐页发生风险时复用 BOSS 风险锁和停机策略；
- 采集弹窗可选择是否采集推荐页；
- 任务进度显示搜索流和推荐页两个阶段；
- 岗位列表/详情显示来源徽标；
- 定时采集可明确继承或关闭推荐页，默认关闭；
- 单元测试、集成测试、数据库迁移测试、前端构建和 UI 验收。

## 2.2 本次明确不做

- 不创建 `BossRecommendationCollector` 作为第二个平台；
- 不创建第二套去重、评分、入库或任务系统；
- 不使用私有 API、逆向接口或绕过登录/验证码；
- 不把推荐页岗位自动发送招呼语、简历或回复；
- 不让推荐页绕过 `PlatformAccessGuard`、每日额度或风险锁；
- 不在两个线程中同时操作同一个 Chrome；
- 不默认永久开启推荐页；
- 不把推荐页来源写入 `source_keyword`，因为“推荐页”不是搜索关键词；
- 不因为推荐页没有抓到岗位就静默返回“采集成功”；
- 不在没有真实 DOM fixture 的情况下声称“已兼容推荐页”。

---

# 3. 推荐的数据模型设计

## 3.1 来源通道常量

在 `src/openjob/collection/models.py` 或现有项目契约单一来源模块中定义：

```python
from typing import Literal

CollectionChannel = Literal["search", "recommendation"]

COLLECTION_CHANNEL_LABELS: dict[str, str] = {
    "search": "搜索流",
    "recommendation": "推荐页",
}
SUPPORTED_BOSS_CHANNELS = frozenset(COLLECTION_CHANNEL_LABELS)
```

要求：后端、前端、测试使用同一套英文枚举和中文标签；`source_keyword` 只保存真实搜索关键词；推荐页使用 `source_channel="recommendation"`、`source_keyword=""`；不把中文展示文案直接作为数据库枚举值。

## 3.2 `JobCandidate` 扩展

在现有 `JobCandidate` 末尾增加默认字段，保证旧测试的构造方式仍然有效：

```python
@dataclass
class JobCandidate:
    # existing fields ...
    source_keyword: str = ""
    source_channel: str = "search"

    @property
    def source_label(self) -> str:
        return COLLECTION_CHANNEL_LABELS.get(self.source_channel, self.source_channel)
```

`as_job_record()` 新增 `source_channel`。BOSS 只能是 `search` 或 `recommendation`，其他平台当前只能是 `search`，非法值不得写入数据库。

## 3.3 为什么不能只加一个 jobs.source_channel

只在 `jobs` 表上放一个 `source_channel` 会产生这个问题：

```text
第一次从搜索流发现岗位 → source_channel=search
之后推荐页再次发现同一岗位 → 如果覆盖，搜索来源丢失；如果不覆盖，推荐来源看不到
```

因此本计划采用：

1. `jobs.source_channel`：保存岗位的主来源/首次来源，用于兼容旧查询和快速显示；
2. `job_source_observations`：保存所有被观察到的来源，支持同一岗位同时显示“搜索流 + 推荐页”。

## 3.4 数据库迁移 v2.9

修改 `src/openjob/db.py`：`SCHEMA_VERSION = 9`，`_MIGRATIONS` 末尾追加 `_migrate_v2_9`。旧库迁移前沿用现有自动备份逻辑，不能重建 jobs 表或删除旧数据。

迁移新增：

```sql
ALTER TABLE jobs ADD COLUMN source_channel TEXT NOT NULL DEFAULT 'search';

CREATE TABLE IF NOT EXISTS job_source_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    source_platform TEXT NOT NULL,
    source_channel TEXT NOT NULL,
    source_label TEXT NOT NULL,
    source_keyword TEXT NOT NULL DEFAULT '',
    source_city TEXT NOT NULL DEFAULT '',
    source_city_code TEXT NOT NULL DEFAULT '',
    collection_run_id TEXT NOT NULL DEFAULT '',
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    seen_count INTEGER NOT NULL DEFAULT 1,
    UNIQUE(job_id, source_platform, source_channel, source_keyword, source_city),
    FOREIGN KEY (job_id) REFERENCES jobs(id)
);

CREATE INDEX IF NOT EXISTS idx_job_source_observations_job
    ON job_source_observations(job_id);

CREATE INDEX IF NOT EXISTS idx_job_source_observations_channel
    ON job_source_observations(source_platform, source_channel, last_seen_at);
```

迁移回填：现有岗位的 `source_channel` 统一为 `search`；每个旧岗位创建一条 search observation，使用已有平台、关键词、城市、城市编码；重复迁移不产生重复记录；不能把旧岗位猜成推荐来源。

## 3.5 数据库函数

在 `src/openjob/db.py` 增加：

```python
def record_job_source_observation(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    source_platform: str,
    source_channel: str,
    source_keyword: str = "",
    source_city: str = "",
    source_city_code: str = "",
    collection_run_id: str = "",
) -> None:
    """Insert or update one logical source observation without changing job status."""


def get_job_source_summaries(
    conn: sqlite3.Connection,
    job_ids: list[str],
) -> dict[str, dict[str, object]]:
    """Return channel labels and latest observation metadata keyed by job id."""
```

重复观察更新 `last_seen_at`、`seen_count` 和最近运行 ID；不改变岗位状态、评分、招呼语或简历字段。来源观察失败只记录 warning，不把已保存岗位误标为采集失败。

## 3.6 `insert_job_if_new` 规则

INSERT 时写入 `source_channel`；重复插入不覆盖主来源；新旧岗位都记录 observation；推荐页的 `source_keyword` 为空是正常行为；保持 `(source_platform, source_job_id)` 唯一去重不变。

---
# 4. BOSS 推荐页 DOM 勘察任务（必须先做）

这是第一个实现任务。没有登录态真实 DOM，不能直接猜选择器。

## Task 0: 记录真实推荐页结构并建立脱敏 fixture

**Files:**

- Create: `docs/architecture/boss-recommendation-discovery.md`
- Create: `tests/fixtures/boss/recommendation-cards-basic.json`
- Create: `tests/fixtures/boss/recommendation-page-states.json`
- Modify: 生产代码：本任务第一检查点不修改

- [ ] **Step 0.1: 记录当前测试基线**

运行：

```powershell
python -m pytest -q tests/test_boss_collector.py tests/test_collection_orchestrator.py tests/test_collection_runs.py tests/test_collection_safety.py tests/test_web_api_routes.py -k "collect or collection or boss or source or platform"
```

Agent 必须使用自己的真实输出作为基线。当前工作区在 2026-09-19 曾验证到 `32 passed, 66 deselected`，这只是参考，不得直接写死为永久事实。

- [ ] **Step 0.2: 使用用户已经登录的专用 Chrome 打开推荐页**

只允许通过 OpenJob Browser Runtime 或用户明确提供的专用 Chrome 会话访问：

```text
https://www.zhipin.com/web/geek/recommend
```

记录：最终 URL、是否需要登录、验证码/安全验证信号、首屏卡片数、滚动后新增情况、是否有“加载更多/换一批/下一页”、职位详情链接格式、职位/薪资/公司/地点/经验/学历选择器、无岗位/登录失效/风险页信号。

禁止：提交 Cookie、完整 HTML、联系人隐私或真实岗位数据到 Git；逆向调用未公开接口；自动点击验证码；把平台默认招呼语作为岗位字段。

- [ ] **Step 0.3: 生成脱敏 JSON fixture**

fixture 只保留 parser 所需字段，例如：

```json
{
  "cards": [
    {
      "title": "数据分析实习生",
      "salary": "150-200元/天",
      "experience": "在校生",
      "education": "本科",
      "company": "示例科技公司",
      "location": "杭州·西湖区",
      "url": "/job_detail/sanitized-recommend-id.html"
    }
  ],
  "page_state": {
    "has_expected_content": true,
    "is_end_of_feed": false,
    "risk": null
  }
}
```

至少覆盖：一个完整卡片、一个缺少非必填字段的卡片、一个重复链接、首屏、滚动新增、无新增、结束状态和登录失效状态。真实姓名、公司、ID、联系人和 URL 中的隐私必须替换。

- [ ] **Step 0.4: 完成 discovery 文档**

`docs/architecture/boss-recommendation-discovery.md` 至少包含：发现日期、页面 URL、登录要求、已确认 selectors、fallback 顺序、滚动/分页行为、终止条件、风险页信号、未确认字段的安全处理、fixture 映射和结论。

- [ ] **Step 0.5: 提交 discovery checkpoint**

```powershell
git add docs/architecture/boss-recommendation-discovery.md tests/fixtures/boss
git commit -m "docs(boss): record recommendation feed DOM discovery"
```

验收：Agent 能指出真实 DOM 证据，而不是只说“推荐页大概率和搜索页一样”。

---

# 5. 采集请求和配置契约

## Task 1: 扩展 BOSS 来源配置，不影响旧配置

**Files:**

- Modify: `src/openjob/config.py`
- Modify: `src/openjob/collection/models.py`
- Modify: `src/openjob/collection/orchestrator.py`
- Modify: `config.example.yaml`
- Modify: `src/openjob/web/config_schema.json`
- Test: `tests/test_collection_orchestrator.py`
- Test: `tests/test_web_config_api.py`
- Test: `tests/test_scheduled_collection.py`

### Step 1.1：请求字段

在现有 `PlatformCollectionRequest` 末尾增加默认字段，保证旧测试的构造方式继续工作：

```python
source_channels: list[str] = field(default_factory=lambda: ["search"])
recommendation_max_scrolls: int = 4
recommendation_max_cards: int = 50
recommendation_same_result_limit: int = 2
```

### Step 1.2：规范化后的 BOSS options

`normalize_collection_options()` 对 BOSS 产生：

```python
{
    "keywords": ["数据分析实习"],
    "cities": ["杭州"],
    "city_codes": {"杭州": "101210100"},
    "max_pages": 2,
    "sort": "newest",
    "recruitment_filter": "campus",
    "source_channels": ["search", "recommendation"],
    "recommendation_max_scrolls": 4,
    "recommendation_max_cards": 50,
    "recommendation_same_result_limit": 2,
}
```

规则：未配置时使用 `["search"]`；通道去重并保持顺序；推荐页只允许 BOSS；默认执行顺序为 search 再 recommendation；滚动轮次范围 1-10；最大卡片范围 1-200；连续无新增轮次范围 1-5。

### Step 1.3：校验条件

`validate_collection_options()` 使用：

```text
至少选择一个来源通道
选择 search → 至少一个关键词和一个城市
只选择 recommendation → 允许关键词/城市为空，依赖账号推荐流
非 BOSS 选择 recommendation → 400
```

原有的平台、城市编码、页数、招聘类型和 `auto_score` 校验继续保留。

### Step 1.4：配置默认值

`src/openjob/config.py` 增加：

```python
"daily_recommendation_page_limit": 10,
```

BOSS 默认增加：

```python
"source_channels": ["search"],
"recommendation_max_scrolls": 4,
"recommendation_max_cards": 50,
"recommendation_same_result_limit": 2,
```

推荐页默认关闭，因为会增加页面访问和详情解析时间，必须由用户显式启用。

### Step 1.5：示例配置

`config.example.yaml`：

```yaml
platforms:
  boss:
    enabled: true
    source_channels: [search]  # 可选 search / recommendation；推荐页默认关闭
    recommendation_max_scrolls: 4
    recommendation_max_cards: 50
    recommendation_same_result_limit: 2
```

`collection` 增加：

```yaml
daily_recommendation_page_limit: 10  # 推荐流页面加载次数上限；仍受全局页面上限约束
```

### Step 1.6：配置测试

覆盖：

1. 旧 config 没有 `source_channels` 时规范化为 `["search"]`；
2. BOSS 只选 recommendation 可以通过；
3. 智联/51job 选 recommendation 被拒绝；
4. 重复 source channel 被去重或拒绝；
5. search 缺关键词/城市报错；
6. 只选 recommendation 不要求虚假的关键词；
7. 推荐参数越界被拒绝；
8. `auto_score=false` 仍可采集推荐岗位但不评分。

### Step 1.7：提交

```powershell
git add src/openjob/config.py src/openjob/collection/models.py src/openjob/collection/orchestrator.py config.example.yaml src/openjob/web/config_schema.json tests/test_collection_orchestrator.py tests/test_web_config_api.py tests/test_scheduled_collection.py
git commit -m "feat(collection): add BOSS recommendation source options"
```

---

# 6. 推荐页解析器和有限滚动驱动

## Task 2: 新增推荐页专用解析模块

**Files:**

- Create: `src/openjob/collection/platforms/boss_recommend.py`
- Modify: `src/openjob/collection/platforms/boss.py`
- Test: `tests/test_boss_collector.py`
- Create: `tests/test_boss_recommend.py`
- Use: `tests/fixtures/boss/recommendation-cards-basic.json`

### 6.1 模块接口

`boss_recommend.py` 提供：

```python
RECOMMEND_URL = "https://www.zhipin.com/web/geek/recommend"
RECOMMENDATION_CHANNEL = "recommendation"
JS_EXTRACT_RECOMMEND_LIST: str

@dataclass(frozen=True)
class RecommendationBatch:
    cards: list[dict[str, str]]
    has_expected_content: bool
    is_end_of_feed: bool
    page_marker: str
    risk: str | None = None
    risk_evidence: str = ""


def normalize_recommendation_card(raw: object) -> dict[str, str] | None:
    """Normalize one browser JSON card; return None for unusable cards."""


def recommendation_should_continue(
    *,
    scroll_round: int,
    max_scrolls: int,
    same_result_rounds: int,
    same_result_limit: int,
    is_end_of_feed: bool,
) -> bool:
    """Return whether another bounded scroll round is allowed."""
```

### 6.2 JS 解析器要求

JS 必须返回 JSON 字符串，不能返回 DOM 节点。使用 discovery 确认的推荐卡片 selector；以包含 `/job_detail/` 的职位链接作为强身份信号；用 `closest()` 找卡片容器；提取标题、薪资、经验、学历、公司、地点；用 `new URL(href, location.origin).pathname` 统一 URL；页面内按 canonical URL 去重。

无职位详情 URL 的元素不能作为岗位卡片。缺少公司/JD可先作为列表候选，详情页再补。无卡片且有“无更多推荐/暂无推荐”等结束信号时返回 `is_end_of_feed=true`；无卡片且没有结束信号时返回 `has_expected_content=false`，由上层报解析异常，不能伪装为空结果。

JS 不允许读取 Cookie/localStorage、调用私有 API、自动点击验证码或把平台默认招呼语作为岗位字段。

### 6.3 卡片归一化

必需字段只有：`url`（非空且包含 `/job_detail/`）和 `title`。公司、薪资、经验、学历、地点允许为空，详情页负责补齐。无效卡片丢弃并计入 `parse_failed`，不能生成伪岗位 ID。

### 6.4 有限滚动规则

```text
加载 recommend URL
→ 等待 document.readyState=complete
→ 做一次风险确认
→ evaluate 提取当前卡片
→ 记录本轮 canonical job IDs
→ 达到 max_cards 就结束
→ is_end_of_feed 就结束
→ 连续 same_result_limit 轮没有新 ID 就结束
→ scroll 预设距离
→ 等待操作间隔
→ 检查 stop_event
→ 进入下一轮
```

默认：`max_scrolls=4`、`max_cards=50`、`same_result_limit=2`。禁止无限滚动、无限等待和无限重试。

### 6.5 推荐页结果状态

| 情况 | 状态 | reason_code | 处理 |
|---|---|---|---|
| 正常结束 | completed | recommendation_feed_exhausted | 继续后续任务 |
| 达到滚动上限 | completed_with_shortage | recommendation_scroll_limit | 显示部分结果 |
| 连续没有新岗位 | completed_with_shortage | recommendation_no_new_cards | 安全停止 |
| 结构无法确认 | completed_with_shortage | recommendation_parser_unsupported | 显示解析异常 |
| 少量详情失败 | completed_with_shortage | recommendation_detail_shortage | 保留已成功岗位 |
| 验证码/登录/拦截 | blocked | 现有 risk code | 沿用 BOSS 全局停机 |
| 用户停止 | stopped | user_stopped | 终止任务 |

“解析不支持”不能返回 completed 且 counts 全为 0。

### 6.6 执行步骤

- [ ] 先写纯函数失败测试：正常卡片、缺 URL、相对/绝对 URL、重复 URL、结束信号、无新增、max scroll、unsupported；
- [ ] 实现推荐页 JS 和 Python 归一化；
- [ ] 用模拟 `BossBrowser` 测试 `new_tab(RECOMMEND_URL)`、多轮 `evaluate`、有限 `scroll`、共享 detail parser 和单次 `close_tab`；
- [ ] 将现有搜索循环提取为 `_collect_search_source()`，保持搜索行为不变，再增加 `_collect_recommendation_source()`；
- [ ] 推荐候选显式设置 `source_channel="recommendation"`；搜索候选显式设置 `source_channel="search"`；
- [ ] 提交：

```powershell
git add src/openjob/collection/platforms/boss.py src/openjob/collection/platforms/boss_recommend.py tests/test_boss_collector.py tests/test_boss_recommend.py tests/fixtures/boss
git commit -m "feat(boss): parse recommendation feed with bounded scrolling"
```

---
# 7. 共用去重、来源观察、过滤和评分管线

## Task 3: 将推荐来源接入 `_SharedProcessor`

**Files:**

- Modify: `src/openjob/collection/orchestrator.py`
- Modify: `src/openjob/collection/models.py`
- Modify: `src/openjob/db.py`
- Modify: `src/openjob/collection_run_store.py` only if result serialization needs a typed helper
- Test: `tests/test_collection_orchestrator.py`
- Test: `tests/test_collection_runs.py`
- Create: `tests/test_job_source_observations.py`

### 7.1 共享处理顺序

`_SharedProcessor.inspect(candidate)` 必须维持当前顺序：

```text
seen += 1
→ 记录当前来源上下文
→ 查 source_platform + source_job_id
→ 已存在：duplicate += 1，记录来源观察，跳过详情页
→ 职位名过滤
→ 公司过滤
→ 允许进入详情页
```

`_SharedProcessor.save(candidate)` 必须维持：

```text
JD 过滤
→ insert_job_if_new
→ 新岗位加入 processor.new_job_ids
→ 新旧岗位都记录 source observation
→ 返回 stop_event 结果
```

duplicate 也要记录 observation，否则推荐页命中已有搜索岗位时 UI 看不到推荐来源。被职位名/公司/JD过滤的对象不写入 jobs，也不写 observation。新岗位主来源取首次入库来源。推荐页再次命中搜索岗位不得修改 score、status、greeting、resume_id。

### 7.2 评分规则

保持当前行为：

```python
unique_new_ids = list(dict.fromkeys(all_new_ids))
if auto_score and unique_new_ids and not stopped:
    score_jobs(..., job_ids=unique_new_ids, force_rescore=False)
```

必须测试：搜索新岗位 + 推荐新岗位只评分一次；搜索先入库、推荐重复不重复评分；推荐先入库、搜索重复不重复评分；同一推荐页多轮重复卡片只入库一次；`auto_score=false` 不调用评分；评分失败保留岗位和 observation。

### 7.3 进度结构

保持旧客户端读取 `progress.platforms.boss` 的能力，同时增加来源子状态：

```json
{
  "platforms": {
    "boss": {
      "status": "running",
      "seen": 14,
      "new": 6,
      "duplicate": 8,
      "sources": {
        "search": {
          "label": "搜索流",
          "status": "completed",
          "seen": 8,
          "new": 4,
          "duplicate": 4,
          "parse_failed": 0
        },
        "recommendation": {
          "label": "推荐页",
          "status": "running",
          "seen": 6,
          "new": 2,
          "duplicate": 4,
          "scroll_round": 2,
          "max_scrolls": 4,
          "parse_failed": 0
        }
      }
    }
  }
}
```

老字段是所有来源聚合值；`sources` 是新增字段；collection run 持久化完整 `sources`；新增 `current_source`，保留 `current_platform`；推荐阶段 counts 不能覆盖搜索阶段 counts。

### 7.4 运行结果

给 `PlatformCollectionResult` 增加可选字段，保持旧构造可用：

```python
source_results: dict[str, dict[str, Any]] = field(default_factory=dict)
```

至少包含 status、reason_code、message、seen、new、duplicate、filtered、parse_failed、save_failed。

### 7.5 执行步骤

- [ ] 先写跨来源去重失败测试：两个 URL 相同的候选只产生一行 jobs、两条 observation、一组 source labels、一次评分；
- [ ] 实现共享来源记录和 `progress.sources`，不复制 `_SharedProcessor.inspect/save`；
- [ ] 验证搜索流测试完全不回归：

```powershell
python -m pytest -q tests/test_boss_collector.py tests/test_collection_orchestrator.py tests/test_collection_runs.py
```

- [ ] 提交：

```powershell
git add src/openjob/collection/orchestrator.py src/openjob/collection/models.py src/openjob/db.py src/openjob/collection_run_store.py tests/test_collection_orchestrator.py tests/test_collection_runs.py tests/test_job_source_observations.py
git commit -m "feat(collection): share dedup and scoring across BOSS sources"
```

---

# 8. BOSS 页面安全额度和风险控制

## Task 4: 推荐页纳入现有安全预算

**Files:**

- Modify: `src/openjob/collection/platforms/boss.py`
- Modify: `src/openjob/config.py`
- Modify: `config.example.yaml`
- Modify: `src/openjob/web/config_schema.json`
- Modify: `src/openjob/platform_safety.py` only if helpers需要抽取
- Test: `tests/test_collection_safety.py`
- Test: `tests/test_boss_collector.py`

### 8.1 页面预算

新增：

```yaml
collection:
  daily_search_page_limit: 60
  daily_recommendation_page_limit: 10
  daily_detail_page_limit: 150
```

含义：search 只统计搜索列表页；recommendation 统计推荐 feed 页面加载/重新导航；detail 继续统计两个来源共用的详情页；`safety.daily_platform_page_limit` 统计 BOSS 所有阶段的全局页面访问。滚动必须受 `max_scrolls` 和 `max_cards` 限制；需要重新导航时每次都必须 `reserve("recommendation_page")`。

### 8.2 风控信号

`JS_DETECT_COLLECTION_RISK` 的 expected-content 判断必须同时识别搜索卡片、推荐卡片、详情 JD和已确认的职位详情链接。没有预期内容但出现验证码、登录失效、403、拦截、操作频繁时，沿用现有 risk kind，记录 `collection_<kind>`、锁定平台并停止本轮。

parser unsupported 不是风控，不得创建 risk lock。

### 8.3 安全测试

增加：推荐页额度到达时不再打开下一次；全局额度优先；详情额度跨来源共享；推荐 risk 触发后不进入 BOSS 后续来源；parser unsupported 不建锁；锁可跨连接保留；stop_event 在滚动和详情等待中立即生效。

### 8.4 执行步骤

- [ ] 写失败测试：

```powershell
python -m pytest -q tests/test_collection_safety.py tests/test_boss_collector.py -k "recommend or limit or risk or stop"
```

- [ ] 实现独立推荐页 reserve 和共享 detail reserve；
- [ ] 运行安全测试并提交：

```powershell
git add src/openjob/collection/platforms/boss.py src/openjob/config.py config.example.yaml src/openjob/web/config_schema.json src/openjob/platform_safety.py tests/test_collection_safety.py tests/test_boss_collector.py
git commit -m "security(boss): budget recommendation feed page access"
```

---

# 9. Web API、岗位来源展示和采集配置 UI

## Task 5: 把推荐页能力变成用户可理解的工作流

**Files:**

- Modify: `src/openjob/web/server.py`
- Modify: `src/openjob/web/frontend/src/components/dashboard/CollectJobsDialog.tsx`
- Modify: `src/openjob/web/frontend/src/components/dashboard/JobsTable.tsx`
- Modify: `src/openjob/web/frontend/src/components/jobs/JobCards.tsx`
- Modify: `src/openjob/web/frontend/src/pages/JobsPoolPage.tsx`
- Modify: `src/openjob/web/frontend/src/pages/ConfigPage.tsx`
- Modify: `src/openjob/web/frontend/src/hooks/useDashboard.ts` if types need extension
- Modify: `src/openjob/web/frontend/src/lib/status.ts` if source labels are centralized there
- Test: `tests/test_web_api_routes.py`
- Test: `tests/test_web_config_api.py`

### 9.1 采集弹窗

BOSS 卡片增加“采集来源”：

```text
☑ 搜索流
☐ 推荐页
```

推荐页旁显示：

```text
推荐页来自 BOSS 个性化推荐，不依赖搜索关键词；会增加页面访问和详情解析时间，仍受 BOSS 每日安全额度限制。推荐页只采集，不会自动发送招呼语、简历或回复。
```

默认只选搜索流。取消搜索、只选推荐页时，关键词/城市字段非必填并显示说明。推荐页高级参数显示安全默认值。不在智联/51job显示推荐开关。

### 9.2 任务进度

在 BOSS 卡片中显示：

```text
BOSS 直聘
  搜索流：扫描 10 · 新增 4 · 重复 6 · 已完成
  推荐页：扫描 8 · 新增 3 · 重复 5 · 滚动 2/4
```

必须区分：解析不支持、达到滚动上限、没有新增、风险停止和正常结束；不能用一个 platform-level completed 掩盖推荐阶段失败。

### 9.3 岗位来源徽标

岗位列表、卡片和详情显示：`搜索流`、`推荐页`或`搜索流 + 推荐页`。旧 API 没有新字段时回退到 `source_channel` 或 `source_platform`，不能崩溃。

### 9.4 API 返回

`/api/jobs`、`/api/jobs/<job_id>`、工作台待处理列表和采集运行详情至少返回：

```json
{
  "source_platform": "boss",
  "source_channel": "search",
  "source_labels": ["搜索流", "推荐页"],
  "source_channels": ["search", "recommendation"],
  "source_observations": [
    {
      "channel": "recommendation",
      "label": "推荐页",
      "keyword": "",
      "city": "杭州",
      "last_seen_at": "..."
    }
  ]
}
```

禁止返回 Cookie、AI Key、浏览器目标 ID和页面完整 HTML。

### 9.5 配置页

`ConfigPage.tsx` 增加推荐页默认开关、最大滚动轮次、最大推荐卡片数、连续无新增轮次、BOSS 推荐页每日列表访问上限和详情压力提示。必须沿用现有 `updateConfig()` 保存流程，不能在前端直接写 YAML。

### 9.6 执行步骤

- [ ] 先写 API 响应测试：source labels、多来源 observation、旧字段兼容、非法平台 recommendation、collection run source_results；
- [ ] 修改 UI 类型和组件，新增字段设为可选；
- [ ] `ui-acceptance.cjs` 增加：采集弹窗出现“推荐页”、提示包含“不会自动发送”、岗位池显示来源徽标、390px无横向溢出、推荐默认关闭；
- [ ] 构建并提交：

```powershell
cd src/openjob/web/frontend
npm run build
cd ../../../..
git add src/openjob/web/server.py src/openjob/web/frontend/src tests/test_web_api_routes.py tests/test_web_config_api.py
git commit -m "feat(web): expose BOSS recommendation source and progress"
```

---
# 10. 定时采集与任务恢复兼容

## Task 6: 让推荐来源和工作台内定时采集一致

**Files:**

- Modify: `src/openjob/web/scheduled_collection.py`
- Modify: `src/openjob/config.py`
- Modify: `config.example.yaml`
- Modify: `src/openjob/web/server.py`
- Test: `tests/test_scheduled_collection.py`

### 10.1 计划选项构造

当前 `_build_options()` 从 `normalize_collection_options(config)` 拿到平台配置，再覆写 `max_pages`。改造后：

- 保留 `source_channels`；
- 如果 `include_recommendation=false`，从计划 options 移除 recommendation；
- 如果 `include_recommendation=true`，只在 BOSS options 中保留 recommendation；
- 计划任务的 `auto_score` 固定为 true，沿用现有规则；
- 推荐页失败不能触发发送器，因为 scheduler 只能启动 `collect` 模式。

建议新增安全默认：

```yaml
collection_schedule:
  include_recommendation: false
  recommendation_max_scrolls: 2
```

### 10.2 测试

增加：旧 schedule 没有 `include_recommendation` 时为 false；开启后 BOSS options 同时含 search/recommendation；只选 recommendation 时没有关键词也可通过；parser unsupported 被记录为 shortage/skip 而不是假成功；时间槽不重复；推荐阶段没有 deliver/monitor；BOSS 风控锁沿用现有跳过说明。

### 10.3 执行步骤

- [ ] 写失败测试：

```powershell
python -m pytest -q tests/test_scheduled_collection.py -k "recommend or source or schedule"
```

- [ ] 实现 options 继承和状态展示；
- [ ] 提交：

```powershell
git add src/openjob/web/scheduled_collection.py src/openjob/config.py config.example.yaml src/openjob/web/server.py tests/test_scheduled_collection.py
git commit -m "feat(schedule): optionally include BOSS recommendation feed"
```

---

# 11. 测试矩阵

## 11.1 Parser 单元测试

文件：`tests/test_boss_recommend.py`

必须覆盖：正常卡片、相对/绝对 URL、没有详情 URL、同页重复 URL、缺少非必填字段、无卡片+结束信号、无卡片+无结束信号、连续无新增、max_scrolls、非法 source_channel。

## 11.2 BOSS collector 集成测试

文件：`tests/test_boss_collector.py`

必须覆盖：

- search only 行为不变；
- recommendation only 不访问 search URL；
- search + recommendation 共用一个 worker tab；
- recommendation 复用 detail parser；
- 详情页首次失败仍按现有规则重试一次；
- recommendation list page reserve 使用 recommendation action；
- detail reserve 使用 shared daily detail limit；
- stop_event 在滚动前后、详情前后均可终止；
- recommendation risk 触发 blocked；
- parser unsupported 不创建 risk lock；
- close tab 在所有退出路径只执行一次。

## 11.3 Orchestrator/DB 测试

文件：`tests/test_collection_orchestrator.py`、`tests/test_collection_runs.py`、`tests/test_job_source_observations.py`

必须覆盖：

- 跨来源相同 `source_job_id` 只生成一条 jobs；
- 两条 source observation 都存在；
- first source 作为 primary source；
- duplicate 来源不改变 status/score/greeting；
- auto score 只收集 unique new IDs；
- collection run 的 source progress 可序列化/读取；
- 旧 collection run 没有 sources 字段时前端可读取；
- v8 数据库升级到 v9；
- v9 迁移幂等；
- 旧岗位默认 search 来源；
- 删除岗位时来源观察符合现有删除语义。

## 11.4 安全测试

文件：`tests/test_collection_safety.py`

覆盖：推荐页独立额度、全局 BOSS 额度、共享详情额度、风险锁、登录失效、验证码、拦截、连续页面失败、不支持 DOM、无无限重试、不进入 deliver/monitor。

## 11.5 Web/API 测试

文件：`tests/test_web_api_routes.py`、`tests/test_web_config_api.py`

覆盖：options source_channels 保存/读取；推荐 labels；collection detail 的 source_results；非 BOSS recommendation 返回 400；发送 API 不因推荐来源获得额外能力；API 不泄露 HTML/Cookie/API Key/敏感路径。

## 11.6 前端验证

```powershell
cd src/openjob/web/frontend
npm ci
npm run build
node scripts/ui-acceptance.cjs
cd ../../../..
```

失败必须让 CI 红灯，不能用 `continue-on-error` 隐藏。

---

# 12. 失败处理与可观测性

## 12.1 稳定 reason code

```text
recommendation_feed_exhausted
recommendation_scroll_limit
recommendation_no_new_cards
recommendation_parser_unsupported
recommendation_page_open_failed
recommendation_list_parse_failed
recommendation_detail_shortage
recommendation_daily_page_limit
```

现有通用错误继续使用：

```text
captcha
login_required
blocked
rate_limit
browser_disconnected
user_stopped
persistent_risk_lock
```

## 12.2 任务日志

日志示例：

```text
BOSS 搜索流开始：关键词=数据分析实习，城市=杭州，第 1/2 页
BOSS 搜索流完成：扫描 12，新增 5，重复 7
BOSS 推荐页开始：最大滚动 4，最大卡片 50
BOSS 推荐页第 1 轮：扫描 8，新增 3，重复 5
BOSS 推荐页连续 2 轮没有新增岗位，安全停止推荐页滚动
本轮采集完成：新增 8，自动评分 8 个
```

不得把完整卡片 HTML、Cookie、个人简历或 AI Key 写入日志。

## 12.3 结果语义

- 搜索成功、推荐解析失败：总体 `completed_with_shortage`，并显示推荐页失败原因；
- 搜索和推荐都成功：总体 `completed`；
- 推荐风控阻断：沿用 BOSS blocked/停止语义；
- 用户停止：总体 `stopped`；
- 自动评分失败：总体 `completed_with_errors`，但岗位和来源保留。

---

# 13. 性能和风控预算评估

推荐页带来页面加载、滚动等待和推荐新岗位详情页三类成本。默认不要使用大参数：

```text
推荐页列表加载次数：10/日
每轮最大滚动：4
每轮最大卡片：50
连续无新增：2
详情页：继续与搜索共用 150/日上限
```

真实用户第一次使用时建议设置“搜索 1 页 + 推荐 2 轮 + 推荐卡片 20”，稳定后再增加。不要一次打开几十页。

推荐页采集不能调用 AI 识别 DOM，也不能先对每个推荐卡片调用 AI。DOM解析、去重、页面状态和安全边界必须确定性实现；AI只在统一的新岗位集合阶段评分。

### 复杂度评估

| 工作项 | 复杂度 | 原因 |
|---|---:|---|
| URL/配置/字段扩展 | 低 | 现有 options 可扩展 |
| 推荐页 JS 解析 | 中 | DOM 和懒加载需要真实勘察 |
| 滚动终止控制 | 中 | 需要防无限循环和动态重复 |
| 共享详情/去重 | 低-中 | 现有 pipeline 可复用 |
| 多来源追踪 | 中 | 需要迁移和 observation 表 |
| 安全额度 | 中 | 推荐列表和详情预算不同 |
| UI 配置/进度 | 中 | 当前按平台，需要来源层 |
| 测试/fixture/CI | 中 | 需要无登录态回归 |
| 总体 | 中等偏上 | 预计 2-4 个短迭代，不是一次小修 |

---

# 14. 分批执行顺序和检查点

不要一次性把所有任务改完。另一个 Agent 必须按下列顺序执行：

## Batch A：发现和契约

1. Task 0 DOM discovery；
2. Task 1 request/config contract；
3. 回归搜索流测试；
4. 停止并报告。

完成门槛：有真实 DOM fixture、配置字段明确、旧搜索流程测试通过。

## Batch B：解析器和 collector

1. Task 2 parser；
2. BOSS collector 推荐阶段；
3. 模拟浏览器测试；
4. 停止并报告。

完成门槛：有限滚动、无无限重试、详情页共用、失败原因可解释。

## Batch C：来源追踪和共享管线

1. Task 3 DB migration；
2. `_SharedProcessor` 来源观察；
3. 跨来源去重；
4. 自动评分唯一 ID；
5. 停止并报告。

完成门槛：同一岗位两来源只有一个 jobs 行、两个来源观察、评分只发生一次。

## Batch D：安全额度

1. Task 4 推荐页额度；
2. 风控和停机；
3. 安全测试。

完成门槛：推荐页无法绕过 BOSS 全局页面上限、详情上限和风险锁。

## Batch E：Web/UI

1. Task 5 采集弹窗；
2. 岗位来源徽标；
3. 运行进度；
4. API 类型和错误展示；
5. 构建/UI acceptance。

完成门槛：用户能看懂推荐页是否开启、抓了多少、为什么跳过、岗位来自哪里。

## Batch F：定时采集和最终质量门禁

1. Task 6 定时采集继承；
2. 全量 pytest；
3. npm ci/build；
4. UI acceptance；
5. 文档和 CHANGELOG；
6. 最终人工验收。

---

# 15. 最终验收清单

## 15.1 功能

- [ ] 搜索流行为与改造前一致；
- [ ] 可单独选择搜索流；
- [ ] 可选择搜索流 + 推荐页；
- [ ] 可只选择推荐页；
- [ ] 推荐页使用真实确认过的 DOM selector；
- [ ] 推荐页滚动有上限；
- [ ] 连续无新增会停止；
- [ ] 无岗位和解析失败可区分；
- [ ] 详情沿用现有解析；
- [ ] 通过现有职位/公司/JD过滤；
- [ ] 与搜索岗位按 BOSS identity 去重；
- [ ] 同一岗位显示两个来源；
- [ ] 新岗位进入统一评分；
- [ ] 重复岗位不重复评分；
- [ ] `auto_score=false` 不调用评分；
- [ ] 不产生发送动作。

## 15.2 安全

- [ ] 推荐页计入独立额度；
- [ ] 推荐页计入 BOSS 全局额度；
- [ ] 详情页共享额度；
- [ ] 验证码/登录失效/拦截停止；
- [ ] parser unsupported 不伪装成功；
- [ ] 无无限滚动和无限重试；
- [ ] 无并发抢占同一 Chrome；
- [ ] 不读取 Cookie/localStorage；
- [ ] 不调用未公开接口；
- [ ] 不绕过人工确认投递。

## 15.3 数据

- [ ] v8 可迁移到 v9；
- [ ] 迁移前自动备份；
- [ ] 迁移幂等；
- [ ] 旧岗位默认搜索来源；
- [ ] 新岗位有 primary source；
- [ ] 多来源岗位有两个 observation；
- [ ] observation不改变岗位状态、评分和招呼语；
- [ ] 删除/回收站流程不破坏来源表；
- [ ] API 返回来源 label；
- [ ] 缺字段时前端仍兼容。

## 15.4 测试

```powershell
python -m pytest -q
cd src/openjob/web/frontend
npm ci
npm run build
node scripts/ui-acceptance.cjs
cd ../../../..
```

Agent 必须报告真实测试数量和耗时，不得沿用旧数字；失败不得被 `continue-on-error` 隐藏。

## 15.5 文档

至少更新：

- `README.md`：推荐页是什么、如何开启、消耗什么额度；
- `docs/product-guide.md`：来源选择和故障处理；
- `docs/risk-guide.md`：推荐页同样受 BOSS 安全预算；
- `config.example.yaml`：字段和默认关闭；
- `CHANGELOG.md`：能力、默认行为、限制；
- `docs/architecture/boss-recommendation-discovery.md`：真实 DOM 证据。

---

# 16. 回滚方案

## 16.1 代码回滚

推荐功能通过 `source_channels` 默认关闭。parser 在真实页面异常时把 BOSS `source_channels` 改回 `[search]`；搜索流重构必须保留旧 fixture；不要用 `git reset --hard` 覆盖用户未提交的个人配置或数据库；各 Batch 使用独立 commit。

## 16.2 数据库回滚

v2.9 迁移前由现有自动备份逻辑备份。不提供危险的降级迁移。发现问题时可以暂时停止使用 observation 表，但不删除已写入数据；修复后继续从 v9 读取。`jobs.source_channel` 默认 search，旧业务仍可运行。

## 16.3 需要立即关闭推荐页的情况

- BOSS 风控信号异常增加；
- 推荐解析器把非职位元素识别为岗位；
- 同一岗位重复入库或重复评分；
- 来源标签与详情不一致；
- collection run 无法结束；
- 页面访问量超过预期。

---

# 17. Agent 每批报告模板

```markdown
## Batch 名称

### 本批目标
- ...

### 实际修改文件
- `path/to/file`: ...

### 推荐页 DOM 证据
- URL：...
- 已确认 selector：...
- 滚动行为：...
- 结束条件：...

### 数据库/配置变化
- schema version：...
- migration：...
- 默认是否开启：...

### 安全影响
- 页面额度：...
- 风控处理：...
- 是否有发送路径变化：必须明确“没有”或具体说明

### 测试命令和真实结果
```text
command
result
```

### 手工验收
- [ ] ...

### 已知问题
- ...

### 回滚方式
- ...

### Commit
- `<真实 commit hash>`

### 下一批前是否需要人工决定
- 不需要 / 需要，说明原因
```

---

# 18. 最终意见

这个功能值得做，但不要把它当成“再写一个列表解析器”。正确的工程目标是：

> **给 BOSS 增加一个受控的第二岗位来源，同时保持 OpenJob 只有一条可信的岗位处理流水线。**

最关键的三条不可妥协原则：

1. **不做真正的浏览器并行。** 同一个 Chrome 会话仍然串行，推荐页只是同一次任务中的第二来源阶段；
2. **不覆盖来源。** 同一岗位来自搜索和推荐时保留两个来源观察，不能简单把 `source_keyword` 改成“推荐页”；
3. **不降低安全边界。** 推荐页照样受页面额度、详情额度、风险锁、任务互斥和人工发送确认保护。

如果另一个 Agent 只实现了 `JS_EXTRACT_RECOMMEND_LIST`，但没有完成来源追踪、跨来源去重、额度、进度、测试和文档，那么这只能算“页面抓到了几条数据”，不能算 OpenJob 的完整产品能力。

完成后用户应能清楚回答：这条岗位来自哪里、是否被另一个来源入库、推荐页抓了多少/重复多少/新增多少、为什么停止、消耗了什么额度、评分是否只执行一次，以及是否仍需人工确认才能发送。

最终产品行为必须保持：

> **推荐页只扩大岗位发现范围，不改变事实来源，不改变人工投递控制权，也不允许自动化越过用户确认。**
