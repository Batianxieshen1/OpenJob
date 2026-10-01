# OpenJob 公司形象增强方案：Logo + 公司简介（公司画像采集）

> 状态：**已实施（S0-S5 完成，2026-10-02）**，待 S6 真机验收（采集一轮 + 回填）
> 日期：2026-10-02
> 铁律核对：确认制 / 数据本地 / 风控不削弱 —— 全部满足，见 §8

## 0. S0 勘察结论（2026-10-02 实测，scripts/debug_company_profile.py）

- 详情页公司卡：`.sider-company .company-info img` 为 Logo（img.bosszhipin.com，带 `?x-oss-process=image/resize,w_120` 缩放参数）；公司主页链接是 **`/gongsi/<hash>.html`**（不是 `/company/`），需排除"查看全部职位"的 `/gongsi/job/` 变体。
- 详情页**没有**公司简介文案（`detail_intro_node: null`）→ 简介必须访问公司主页；主页简介位于 `h3"公司简介"` 所在 `.job-sec` 区块，textContent 尾部带"展开"字样需剥离。
- **页面内 fetch 图床被 CORS 拦截**（`Failed to fetch`，S0 实测），Logo 转存改为服务端带 Referer 直取（该图是页面本来就加载的公开静态资源，等价于浏览器行为，风险增量可忽略；实测 200/7KB/合法 JPEG）。
- 风险探测 JS（JS_DETECT_COLLECTION_RISK）期望内容选择器补充 `.job-sec`，使公司主页也能走同一套验证码/封禁双检；真实验证码页无 `.job-sec`，检测语义未被削弱。

## 1. 背景与目标

用户浏览岗位池时，无法第一眼识别"这家公司是什么"。目标：

1. 每条岗位展示**公司标志性 Logo**（BOSS 直聘页面上本身就有）；
2. 提供**可点击查看的公司简介**（BOSS 公司主页上的公司介绍文本）。

展示落点：岗位卡公司名旁加 Logo 头像；岗位详情弹窗加"公司简介"区块 + 跳转 BOSS 公司主页的外链。

## 2. 现状调查结论（本次只读调查的证据）

| # | 事实 | 证据位置 |
|---|------|----------|
| F1 | **每条新岗位都会打开详情页解析**（搜索流与推荐流共用同一条详情管线） | `src/openjob/collection/platforms/boss.py` `_run_detail_pipeline`（L330-387）→ `JS_EXTRACT_DETAIL`（L59-100） |
| F2 | 详情页左侧公司卡（`.sider-company`）含公司 Logo 图片与公司主页链接；当前提取 JS 没有采集它们 | `JS_EXTRACT_DETAIL` 现有字段清单 |
| F3 | 完整"公司简介"在 BOSS 公司主页（`/company/<id>.html`），不在职位详情页主结构中（详情页是否有短简介待 Step 0 验证） | 常识 + Step 0 消除不确定性 |
| F4 | 护栏体系按字符串 stage 记账，可直接新增 `company_page` 类别，自动获得日额度与 `platform_access_events` 审计 | `src/openjob/platform_safety.py` L36-52 `reserve(action, daily_limit)` |
| F5 | jobs 表已存公司维度字段（company_size / company_industry），公司级数据放岗位行是既有风格；迁移模式为 `_migrate_v2_N` + `SCHEMA_VERSION` | `src/openjob/db.py` L16、L97-119、L539 |
| F6 | `/api/jobs` 返回岗位整行，新列自动到达前端，后端 API 无需改动 | `src/openjob/web/server.py` L1145-1169 |
| F7 | 前端已有展示落点：岗位卡 `JobActionCard`、详情弹窗（岗位详情 modal）公司信息区 | `src/openjob/web/frontend/src/components/jobs/JobCards.tsx` L247、L326 |
| F8 | Web 服务为 Bottle + `_serve_static`，新增一条只读静态路由成本低 | `server.py` L133、L3870 |
| F9 | CLI 为 click 组命令，新增 `company-enrich` 子命令有现成范式（scrape/score 同款） | `src/openjob/main.py` L63-149 |
| F10 | 当前库中**有效岗位 982 条，去重公司 501 家**（美的 35 条、埃森哲 29 条、SHEIN 29 条）——公司级去重收益显著 | 2026-10-02 只读查询 `data/openjob.db`（mode=ro） |

## 3. 总体设计

### 3.1 数据模型（jobs 表新增 4 列，schema v10 → v11）

```
company_logo_path  TEXT  本地相对路径，如 assets/logos/<sha1前16>.png（落盘 data/ 下）
company_logo_url   TEXT  BOSS 图床原始 URL（留档排查用）
company_intro      TEXT  公司简介文本（清洗 + 截断 ≤2000 字符）
company_intro_url  TEXT  BOSS 公司主页 URL（详情弹窗"查看公司主页"外链）
```

设计取舍：
- **Logo 存本地文件而不是热链 URL**：本地页面直连 BOSS 图床可能被 Referer 防盗链 403，且图床 URL 会随时间失效；采集时通过**页面上下文 fetch**（自带会话 Cookie）转存 base64 → 写入 `data/assets/logos/`，每公司一份（按规范化公司名 hash 去重）。
- **简介按岗位行冗余存储**而非单独 companies 表：与 F5 的现有风格一致，前端零 join；去重发生在**采集时刻**（每公司至多访问一次），不在存储层。
- 已知限制：公司名按规范化精确匹配去重（去空格/括号后缀），"美的"与"美的集团"视为两家，不做模糊归并（避免误合并引入脏数据）。

### 3.2 采集设计（两条路径）

**路径 A（零成本顺路）：Logo + 公司主页链接**
- `JS_EXTRACT_DETAIL` 增补两个字段：详情页 `.sider-company` 里的 Logo URL 和公司主页链接。详情页本来就逐条打开（F1），**不新增任何页面访问**。
- 保存岗位前，用页面上下文 fetch 下载 Logo 转存本地。下载失败仅记事件告警，**不阻塞岗位入库**。

**路径 B（受控访问）：公司简介**
- 首次遇到某家新公司时（本轮内存 dict 去重），访问其公司主页一次：
  - `guard.reserve("company_page", daily_limit=collection.company_daily_page_limit)`（新护栏类别，默认 30/天）
  - 复用同款 `confirm_risk` 双检 + `PageThrottle` 延时（2-5s × multiplier）+ 验证码/封禁冷却机制
- 解析公司简介 → 写入本轮该公司后续所有岗位；失败则简介留空，等待回填兜底。
- Step 0 若发现详情页本身携带短简介，则路径 A 顺路抓短简介，路径 B 只负责补全/回填，进一步摊薄访问量。

**降级总原则**：Logo/简介是"锦上添花"字段，任何环节失败都不影响岗位主数据入库，也不触发重试风暴（每岗位/每公司至多尝试一次）。

### 3.3 存量数据回填（`openjob company-enrich`）

- 新 CLI 子命令（F9 范式）：`openjob company-enrich [--dry-run] [--limit N]`
- 目标集：`SELECT DISTINCT company FROM jobs WHERE deleted_at IS NULL AND company_intro IS NULL`
- 逐家访问公司主页（同护栏/同延时/同风险检测），UPDATE 匹配的全部岗位行（含历史岗位）。
- **默认 dry-run**：只列出公司清单与预计访问次数，不访问任何页面（确认制）。
- 现状规模（F10）：501 家公司。按默认日额度 30/天约需 17 天；允许 `--limit` 分段跑（如每天 50-80 家，约 7-10 天），或偶尔用 150/天（与现有 detail_page 日额度同级）分 4 天跑完。单次会话预计 5-25 分钟，可随时 Ctrl+C 中断、下次续跑（intro IS NULL 即断点）。

### 3.4 前端与静态服务

- 岗位卡（`JobActionCard` / `JobsTable` 行）：公司名前加 20px Logo 头像；缺失时回退为"公司首字 + 主题色圆底"（两主题下均可读）。
- 详情弹窗：公司信息区加 Logo；新增"公司简介"可折叠区块（有简介才显示），附"在 BOSS 查看公司主页"外链。
- 新 Bottle 只读路由 `/company-logos/<filename>` → `data/assets/logos/`（路径净化防目录穿越，仅 GET）。**避开 `/assets/` 前缀**以防与 Vite 构建产物路径混淆。
- 前端 `Job` 类型补 4 个可选字段；API 端零改动（F6）。

### 3.5 配置项（`collection` 节，单一事实源）

```yaml
collection:
  company_daily_page_limit: 30   # 公司主页日访问额度（新护栏类别）
  company_intro_max_chars: 2000  # 简介截断上限
  enrich_logo: true              # Logo 顺路采集开关
  enrich_intro: true             # 简介采集开关
```

同步更新 `config.example.yaml`；若 `web/config_schema.json` 暴露 collection 键则一并补（避免 R4 教训的两源漂移）。

## 4. 风控影响评估（关键）

| 场景 | 新增页面访问 | 对照现有额度 |
|------|--------------|--------------|
| 日常采集（含 9:30 定时） | Logo 零新增；简介 = 每轮**新公司数**，通常 < 30 | detail_page 现为 150/天，search_page 60/天 |
| 存量回填（一次性） | 501 家，按 `--limit` 分段 | 独立 `company_page` 日额度兜底，分段可控 |

- 公司主页与职位详情是**同类只读页面**，风险等级相当；总增量 ≤ 现有 detail 额度的 1/5。
- 全部走既有的双检确认、冷却锁、连续失败熔断；`company_page` 的每次访问都会进 `platform_access_events` 审计。

## 5. 实施步骤（已全部完成，2026-10-02）

| 步骤 | 内容 | 状态 |
|------|------|------|
| S0 | Dry-run 勘察：gongsi 链接/简介区块/logo 服务端转存全部确认（§0） | ✅ |
| S1 | DB v11 迁移（4 列）+ `insert_job_if_new` + `update_company_profile`/`companies_missing_intro` | ✅ |
| S2 | 采集器：详情 JS 增补、Logo 转存（服务端）、公司主页简介管线、公司级去重缓存、风险双检提炼为 `_inspect_risk_once`/`_confirm_risk` 共用 | ✅ |
| S3 | `openjob company-enrich`（默认 dry-run；`--apply/--limit/--daily-limit`；Ctrl+C 断点续跑） | ✅ |
| S4 | `/company-logos/` 静态路由（文件名白名单防穿越）+ 前端 `CompanyAvatar` + 详情弹窗简介区块（卡片/表格/弹窗三处接入，双主题） | ✅ |
| S5 | 测试 18 个新增（单测/DB/采集器集成/回填/路由穿越）全绿；全量 792 通过（另有 1 个时间敏感 flaky 测试单跑通过，与本功能无关）；前端构建通过 | ✅ |
| S6 | 真机验收：采集一轮小关键词看新岗位画像 → `company-enrich` dry-run → `--apply` 分段回填 501 家 → 岗位池人工验收 | ⏳ 待执行 |

实施偏差说明（相对原设计）：Logo 转存由"页面上下文 fetch"改为"服务端带 Referer 直取"（S0 实测 CORS 拦截页面内 fetch，见 §0）。

## 6. 已知不确定点与对策

| 不确定点 | 对策 |
|----------|------|
| BOSS 页面 DOM 具体选择器 / 初始状态字段与预期不符 | S0 一次 dry-run 消除；解析失败走降级不丢主数据 |
| 详情页是否自带短简介 | S0 判定；有则路径 A 顺路，无则仅路径 B |
| BOSS 改版导致解析静默失效 | 降级不重试；在 diagnostics 增加缺字段率统计，防止数据质量静默腐化 |
| 同公司多名称变体 | 明确不模糊归并（宁缺毋脏），文档记录限制 |

## 7. 测试与验收清单

- [ ] 迁移 v10→v11 幂等 + 旧库自动备份路径触发
- [ ] `insert_job_if_new` 新列写入 / 旧字段缺省不崩
- [ ] 简介清洗与 2000 字符截断
- [ ] 公司名规范化去重（本轮缓存命中不二次访问）
- [ ] Logo 下载失败 → 岗位照常入库 + 事件告警
- [ ] 公司页触发 captcha → 冷却锁生效且本轮岗位已保存部分不回滚
- [ ] `/company-logos/` 路径穿越防护 + 404
- [ ] 前端：有/无 Logo × 有/无简介 四态渲染（双主题）
- [ ] `company-enrich --dry-run` 零页面访问、零写入

## 8. 铁律核对

- **确认制**：回填默认 dry-run；功能有独立开关；S0 与 S6 均需用户在场陪跑。
- **数据本地**：Logo 落盘 `data/`，简介存本地库，前端不热链外部资源。
- **风控不削弱**：只新增护栏类别与日额度，不动任何现有额度/延时；降级路径不存在重试风暴。
