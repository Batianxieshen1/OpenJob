# OpenJob 简历素材库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 OpenJob 中接入用户自行维护的 XLSX 简历素材库，使系统可以根据岗位 JD 从真实经历、奖项、学生工作、项目和技能证据中选择适合内容，用于生成可审阅、可追溯、不可虚构的定制简历。

**Architecture:** XLSX 是用户可替换的源文件，默认保存为 `data/resume_materials.xlsx`；上传成功后解析为带文件哈希的本地 JSON 索引 `data/resume_materials.index.json`，简历生成只读取已校验的索引。先由现有 JD parser 产出 `JdProfile`，再用本地关键词/方向匹配筛出候选素材，最后把候选素材交给现有 AI 改写阶段；AI 输出的每个改写块必须带 `material_ids`，服务端只允许引用候选素材，并把选中素材的完整快照写入简历版本，便于审计。素材只用于简历生成，不自动发送；现有人工审阅、导出和人工发送流程不变。

**Tech Stack:** Python 3.10、`openpyxl`、SQLite、Bottle、React 18、TypeScript、现有 `JdProfile`/`SectionChange`/简历版本 diff 流程、pytest。

---

## 产品合同

### XLSX 格式

只支持 `.xlsx`。模板只包含一个工作表，名称为 `素材库`，第 1 行必须是以下列名，列顺序固定但解析器允许用户调整顺序：

| 列名 | 必填 | 规则 |
|---|---:|---|
| `id` | 是 | 用户稳定维护的唯一 ID，只允许字母、数字、`_`、`-`，长度 1-64；更新文件时同一素材沿用同一 ID |
| `type` | 是 | `experience`、`project`、`award`、`student_work`、`campus_activity`、`skill_evidence`、`certification`、`other` 之一 |
| `title` | 是 | 经历/项目/奖项名称，1-120 字符 |
| `organization` | 否 | 公司、学校、组织或赛事主办方 |
| `role` | 否 | 职务、角色、担任身份 |
| `start_date` | 否 | `YYYY` 或 `YYYY-MM` |
| `end_date` | 否 | `YYYY`、`YYYY-MM` 或 `至今`；不得早于开始时间 |
| `description` | 是 | 原始事实描述，建议写完整动作、方法和结果；1-2000 字符 |
| `achievements` | 否 | 量化结果、名次、影响范围、产出物等，1-2000 字符 |
| `skills` | 否 | 逗号或中文顿号分隔的技能/工具/方法 |
| `keywords` | 否 | 用于匹配 JD 的关键词，逗号或中文顿号分隔 |
| `target_directions` | 否 | 适用方向，如 `产品,运营,数据分析`；空值表示不限方向 |
| `source` | 否 | 事实来源或核验备注，不写入简历，1-500 字符 |
| `resume_allowed` | 是 | `是`/`否`、`true`/`false`、`1`/`0`；只有允许时才能进入候选集 |
| `priority` | 否 | 整数 1-5，默认 3；5 最高 |
| `notes` | 否 | 仅供用户维护的备注，不发送给 AI |

约束：

- 首行空白、缺少必需列、重复 `id`、空 `id`、非法 `type`、非法日期、非法布尔值、非法 `priority`、超长文本都必须让整次上传失败；不得保存半解析结果。
- 至少有一条数据行；空行可忽略，但不能用空行绕过“至少一条素材”。
- 公式单元格只读取缓存值，不执行公式；读取结果为空时按空值处理。禁止宏文件、外部链接和 `.xls`。
- `source`、`notes`、被标记为 `resume_allowed=否` 的素材绝不进入 AI prompt。
- 素材库属于敏感个人数据：原始 XLSX、JSON 索引和简历版本中的素材快照都只保存在本机 `data/`，API 返回时不泄露绝对路径之外的无关本机信息。

### 生成合同

- `profile.resume_materials_enabled` 默认 `true`；关闭时现有无素材库流程必须完全保持兼容。
- 生成前如果素材库文件不存在、索引不存在、哈希不一致或索引损坏，必须重新解析当前 XLSX；解析失败则简历生成失败并显示明确原因，不静默使用旧索引。
- 先用 `JdProfile.title`、`summary`、`keywords`、硬性/加分技能和现有岗位原文，与素材的 `title`、`description`、`achievements`、`skills`、`keywords`、`target_directions` 做确定性匹配；取最高分最多 8 条候选。
- AI 最多选 4 条素材，最多让 3 个简历变量行使用新增素材；优先选择 `resume_allowed=是`、方向命中、硬性技能命中、关键词命中和 priority 高的素材。
- AI 不得使用候选集之外的素材，不得创造数字、时间、公司、奖项级别、工具熟练度或成果；没有可靠素材时保留底稿原文。
- 由于现有 DOCX 模板按行位置写回，素材只允许融入现有可变正文行，不允许新增/删除/拆分/合并 Markdown 行、表格行或栏目。不能塞入现有行时宁可不使用。
- 每个使用新增事实的 `SectionChange` 必须带 `material_ids`；服务端验证 `material_ids` 是本次候选集子集，并允许的新增事实 token 必须来自底稿或选中素材快照。
- 生成完成后仍停留在 `review`；用户可以逐块采纳/撤销，之后才允许导出；任何投递/发送仍然必须人工确认。

## 文件地图

- Create: `src/openjob/resume_materials.py`，负责 XLSX 模板、解析、校验、哈希、原子保存、JSON 索引读写。
- Create: `src/openjob/ai/resume_engine/materials.py`，负责从已校验素材计算 JD 候选、构造脱敏 prompt 文本、校验 AI 选择。
- Create: `src/openjob/web/frontend/src/components/config/ResumeMaterials.tsx`，负责配置页素材库上传、状态、筛选预览、替换、清空和模板下载。
- Create: `tests/test_resume_materials.py`，覆盖 XLSX 解析、schema、安全、哈希和索引。
- Create: `tests/test_resume_materials_matching.py`，覆盖候选排序、方向/关键词命中和选择合同。
- Modify: `src/openjob/config.py`，增加素材库默认配置。
- Modify: `src/openjob/web/config_schema.json`，增加 `profile` 下素材库字段说明。
- Modify: `src/openjob/db.py`，为 `resumes` 增加素材库哈希、候选/选中素材快照字段的迁移和更新参数。
- Modify: `src/openjob/ai/resume_engine/models.py`，给 `SectionChange` 增加 `material_ids`。
- Modify: `src/openjob/ai/resume_engine/optimizer.py`，接收候选素材上下文，严格约束 AI 只能引用候选素材并允许可信素材事实通过校验。
- Modify: `src/openjob/ai/resume_engine/engine.py`，在 JD 解析后加载素材、筛候选、传入改写、落库快照。
- Modify: `src/openjob/web/server.py`，增加素材库 API，并在简历详情中返回素材审计字段。
- Modify: `src/openjob/web/frontend/src/pages/ConfigPage.tsx`，在个人信息区域挂载素材库组件。
- Modify: `src/openjob/web/frontend/src/pages/ResumePage.tsx`，显示本版本使用的素材及其 ID/标题/类型，并在详情加载时解析。
- Modify: `tests/test_resume_engine.py`、`tests/test_resume_bases.py`、`tests/test_web_api_routes.py`，增加回归断言。

## Task 1: 配置和 XLSX 领域模块

**Files:**
- Create: `src/openjob/resume_materials.py`
- Create: `tests/test_resume_materials.py`
- Modify: `src/openjob/config.py`
- Modify: `src/openjob/web/config_schema.json`

- [ ] **Step 1: 先写失败测试，锁定 schema 和默认路径**

在 `tests/test_resume_materials.py` 创建辅助函数 `build_workbook(rows, headers=DEFAULT_HEADERS)`，用 `openpyxl.Workbook()` 写入内存 `BytesIO`。先写以下测试：

```python
def test_parse_valid_workbook_normalizes_rows_and_dates():
    content = build_workbook([{
        "id": "exp_001", "type": "experience", "title": "内容运营",
        "organization": "某公司", "role": "实习生", "start_date": "2024-03",
        "end_date": "2024-08", "description": "策划公众号专题并复盘数据",
        "achievements": "阅读量提升 30%", "skills": "内容策划, 数据分析",
        "keywords": "公众号,增长", "target_directions": "运营",
        "resume_allowed": "是", "priority": 5,
    }])
    library = parse_workbook(content, filename="素材.xlsx")
    assert library.count == 1
    assert library.items[0]["id"] == "exp_001"
    assert library.items[0]["resume_allowed"] is True
    assert library.items[0]["start_date"] == "2024-03"
    assert library.items[0]["skills"] == ["内容策划", "数据分析"]

def test_parse_rejects_missing_columns_duplicate_ids_and_invalid_values():
    with pytest.raises(MaterialLibraryError, match="缺少必需列"):
        parse_workbook(build_workbook([], headers=["id", "title"]), filename="素材.xlsx")
    rows = [{"id": "same", "type": "award", "title": "奖项", "description": "事实", "resume_allowed": "是"}]
    with pytest.raises(MaterialLibraryError, match="重复 id"):
        parse_workbook(build_workbook(rows + rows), filename="素材.xlsx")
    bad = [{"id": "x", "type": "unknown", "title": "x", "description": "事实", "resume_allowed": "是"}]
    with pytest.raises(MaterialLibraryError, match="type"):
        parse_workbook(build_workbook(bad), filename="素材.xlsx")

def test_parse_rejects_empty_workbook_and_invalid_extension():
    with pytest.raises(MaterialLibraryError, match="至少一条"):
        parse_workbook(build_workbook([]), filename="素材.xlsx")
    with pytest.raises(MaterialLibraryError, match="xlsx"):
        parse_workbook(b"not-xlsx", filename="素材.xls")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_materials.py -q`

Expected: FAIL，因为 `parse_workbook`、`MaterialLibraryError` 和 `DEFAULT_HEADERS` 尚未存在。

- [ ] **Step 3: 实现解析器和配置默认值**

在 `src/openjob/resume_materials.py` 定义：

```python
DEFAULT_HEADERS = [
    "id", "type", "title", "organization", "role", "start_date", "end_date",
    "description", "achievements", "skills", "keywords", "target_directions",
    "source", "resume_allowed", "priority", "notes",
]
MATERIAL_TYPES = {"experience", "project", "award", "student_work", "campus_activity", "skill_evidence", "certification", "other"}
MAX_XLSX_BYTES = 10 * 1024 * 1024

class MaterialLibraryError(ValueError):
    pass

@dataclass(frozen=True)
class MaterialLibrary:
    items: list[dict]
    source_name: str
    sha256: str
    updated_at: str

    @property
    def count(self) -> int:
        return len(self.items)

def parse_workbook(content: bytes, *, filename: str) -> MaterialLibrary: ...
def load_index(index_path: Path) -> MaterialLibrary: ...
def save_library_atomically(library: MaterialLibrary, content: bytes, *, xlsx_path: Path, index_path: Path) -> None: ...
def build_template_workbook() -> bytes: ...
def split_cell_list(value: object) -> list[str]: ...
```

实现要求：校验扩展名、字节大小、ZIP/XLSX 可读性、工作表名称、首行列名、必填文本、类型、日期、布尔值和优先级；使用 `data_only=True, read_only=True`；所有文本 `str(value or "").strip()`；空行跳过；返回 SHA-256。`save_library_atomically` 必须在同一目录写临时 `.tmp` 文件、flush + `os.fsync` 后 `os.replace`，且只有解析成功后才替换原 XLSX/JSON。

在 `config.py` 的 `DEFAULTS["profile"]` 增加：

```python
"resume_materials_enabled": True,
"resume_materials_path": "./data/resume_materials.xlsx",
```

在 `config_schema.json` 的 `profile.fields` 增加同名 switch/text 字段，描述“本地简历素材库，仅用于定制简历，不会自动发送”。

- [ ] **Step 4: 运行单元测试和格式检查**

Run: `pytest tests/test_resume_materials.py -q`

Expected: 所有解析/校验测试 PASS；再运行 `ruff check src/openjob/resume_materials.py`，Expected: 无错误。

- [ ] **Step 5: Commit**

```bash
git add src/openjob/resume_materials.py src/openjob/config.py src/openjob/web/config_schema.json tests/test_resume_materials.py
git commit -m "feat: add local xlsx resume material library"
```

## Task 2: 素材候选匹配和 AI 输出合同

**Files:**
- Create: `src/openjob/ai/resume_engine/materials.py`
- Modify: `src/openjob/ai/resume_engine/models.py`
- Create: `tests/test_resume_materials_matching.py`

- [ ] **Step 1: 先写失败测试**

测试必须固定以下公开接口：

```python
def test_rank_materials_prefers_direction_and_hard_skill_hits():
    jd = JdProfile.from_payload({
        "title": "数据分析实习生", "summary": "负责业务分析",
        "keywords": ["SQL", "Python", "看板"],
        "hard_requirements": [{"skill": "SQL", "weight": 5, "required": True, "source_evidence": "要求 SQL"}],
    })
    items = [
        {"id": "weak", "type": "experience", "title": "社团宣传", "description": "撰写文案", "keywords": ["运营"], "target_directions": ["运营"], "resume_allowed": True, "priority": 3},
        {"id": "strong", "type": "project", "title": "业务数据看板", "description": "使用 SQL 和 Python 完成数据清洗与看板", "keywords": ["SQL", "Python", "看板"], "target_directions": ["数据分析"], "resume_allowed": True, "priority": 3},
    ]
    result = rank_materials(items, jd, limit=8)
    assert result[0].material["id"] == "strong"
    assert result[0].reasons

def test_rank_materials_excludes_forbidden_items_and_limits_candidates():
    items = [{"id": str(i), "type": "project", "title": f"项目{i}", "description": "SQL", "resume_allowed": i != 2, "priority": 3} for i in range(12)]
    result = rank_materials(items, _jd("数据分析"), limit=8)
    assert len(result) == 8
    assert all(item.material["id"] != "2" for item in result)

def test_validate_material_selection_rejects_unknown_or_non_candidate_ids():
    candidates = [Candidate(material={"id": "m1"}, score=10, reasons=["关键词命中"])]
    assert validate_material_ids(["m1"], candidates) == ["m1"]
    with pytest.raises(ValueError, match="候选素材"):
        validate_material_ids(["m2"], candidates)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_materials_matching.py -q`

Expected: FAIL，因为 `rank_materials`、`Candidate`、`validate_material_ids` 尚未存在。

- [ ] **Step 3: 实现确定性匹配模块**

定义：

```python
@dataclass(frozen=True)
class Candidate:
    material: dict
    score: int
    reasons: list[str]

def rank_materials(items: list[dict], jd: JdProfile, *, limit: int = 8) -> list[Candidate]: ...
def validate_material_ids(ids: list[str], candidates: list[Candidate]) -> list[str]: ...
def material_prompt(candidates: list[Candidate]) -> str: ...
```

评分规则固定为：硬性技能命中 +25，JD keyword 命中 +12，岗位标题/概述命中 +10，方向命中 +18，素材 title/description/achievements 命中 +8，priority 每级 +1；同分按 priority 降序、原始行序升序。所有比较统一大小写并兼容中英文逗号、顿号、斜杠。候选 prompt 只包含 `id/type/title/organization/role/date/description/achievements/skills/keywords/target_directions`，不包含 `source/notes`。

- [ ] **Step 4: 扩展 `SectionChange` 的 `material_ids`**

在 `models.py` 给 dataclass 增加 `material_ids: list[str] = field(default_factory=list)`；`from_payload` 要求它是字符串数组，去空白、去重，非法类型直接 `ValueError`。保持旧调用 `SectionChange(..., risk="")` 兼容。新增测试断言 JSON 输出带 `material_ids`。

- [ ] **Step 5: 运行匹配和模型测试**

Run: `pytest tests/test_resume_materials_matching.py tests/test_resume_engine.py -q`

Expected: 新测试和旧简历模型测试全部 PASS。

- [ ] **Step 6: Commit**

```bash
git add src/openjob/ai/resume_engine/materials.py src/openjob/ai/resume_engine/models.py tests/test_resume_materials_matching.py
git commit -m "feat: rank resume materials for jd"
```

## Task 3: 数据库审计字段和生成链路接入

**Files:**
- Modify: `src/openjob/db.py`
- Modify: `src/openjob/ai/resume_engine/engine.py`
- Modify: `src/openjob/ai/resume_engine/optimizer.py`
- Modify: `tests/test_resume_engine.py`
- Modify: `tests/test_resume_bases.py`

- [ ] **Step 1: 先写失败回归测试**

增加以下测试：

```python
def test_material_facts_can_be_used_only_when_selected_and_are_audited():
    # patch parse_jd/analyze_match/rewrite_sections，rewrite 返回带 material_ids=["m1"] 的 change
    # 配置 resume_materials_enabled=True，并准备一个含 m1 的已校验 library/index
    # 断言生成成功；resumes.material_library_sha256 非空；material_selection_json 中有 m1 的完整快照

def test_material_generation_rejects_ai_reference_to_non_candidate():
    # rewrite 返回 material_ids=["not-a-candidate"]
    # 断言 result.ok is False，版本 status=failed，错误包含“候选素材”

def test_materials_disabled_preserves_existing_pipeline():
    # resume_materials_enabled=False，patch 旧的 rewrite_sections 签名可正常生成 review 版本
    # 断言不调用素材读取，不影响旧 diff 和 base_resume_id
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_resume_engine.py -k material -q`

Expected: FAIL，因为数据库列、素材加载和 engine 参数尚未实现。

- [ ] **Step 3: 增加 SQLite 迁移**

在 `_init_tables` 末尾新增 `_migrate_v2_5(conn)`。迁移以 `PRAGMA table_info(resumes)` 判断并按需执行：

```sql
ALTER TABLE resumes ADD COLUMN material_library_sha256 TEXT;
ALTER TABLE resumes ADD COLUMN material_selection_json TEXT;
ALTER TABLE resumes ADD COLUMN material_candidates_json TEXT;
```

扩展 `update_resume_version(..., material_library_sha256=None, material_selection_json=None, material_candidates_json=None)`，把字段加入现有 assignments；`None` 仍表示不修改。旧数据库打开时自动迁移，不删除旧数据。

- [ ] **Step 4: 接入 engine 的素材阶段**

在 `generate_resume` 中，底稿校验通过、JD parse 完成后：

1. 读取配置 `profile.resume_materials_enabled`；关闭则设 `library=None, candidates=[]`。
2. 启用时调用 `load_or_refresh_library(materials_path, index_path)`；路径相对项目根目录解析，默认使用 `DATA_DIR` 对应的 `./data`，不可把路径解析到项目目录之外后再静默写入。
3. 调用 `rank_materials(library.items, jd, limit=8)`；把候选的最小字段快照写入 `material_candidates_json`。
4. 把 `candidates` 传给 `rewrite_sections(base_resume, jd, match, config, candidates=candidates)`。
5. 逐个 change 调 `validate_material_ids(change.material_ids, candidates)`；任何非法 ID 直接失败。
6. 将实际被 change 引用的素材完整字段快照（不含 `source`、`notes`）去重后写入 `material_selection_json`，同时写 `material_library_sha256`。
7. `diff_json` 中保留每个 change 的 `material_ids`，后续人工 PATCH 时继续保留。

素材文件不存在时：如果启用，返回“未找到简历素材库，请在配置页上传 .xlsx”；如果文件存在但解析失败，返回解析器原始中文错误；不允许回退到旧索引，因为会掩盖用户更新失败。

- [ ] **Step 5: 扩展 optimizer 的 prompt 和可信事实校验**

把 `rewrite_sections` 改为兼容签名：

```python
def rewrite_sections(resume_md, jd, match, config, *, candidates=None) -> RewriteResult:
```

当 `candidates` 为空时维持旧 prompt；有候选时在用户 prompt 末尾加入 `material_prompt(candidates)`，并明确要求：

```json
{"changes":[{"section":"个人优势","before":"底稿原文片段","after":"只基于底稿和素材改写的同一行","reason":"对齐 JD 的具体要求","risk":"","material_ids":["m1"]}]}
```

规则：只允许现有正文行的文字替换；新增素材事实必须带对应 ID；未使用素材时 `material_ids` 为空；不把 source/notes 放入输出。`RewriteResult.from_payload` 仍允许旧测试的 change 缺省 `material_ids` 并默认为空。

在 `validate_assembled` 增加可选参数 `trusted_material_text: str = ""`，并把该文本拆出的事实 token 与底稿 token 合并作为允许集合；默认空字符串时旧行为不变。engine 在汇总校验前传入已选素材的 `title/organization/role/date/description/achievements/skills` 拼接文本。只放宽“事实确实存在于选中素材”的情况，不能关闭占位符、结构、长度、固定区和行数校验。

在 `diff` 序列化、`reassemble_from_diff` 和 Web PATCH 清洗时完整保留 `material_ids`；PATCH 时重新验证 ID 必须属于该版本 `material_selection_json`，防止浏览器伪造任意素材引用。

- [ ] **Step 6: 运行引擎全量测试**

Run: `pytest tests/test_resume_engine.py tests/test_resume_bases.py tests/test_batch1_features.py -q`

Expected: 所有旧测试和素材新增测试 PASS；重点确认旧 `rewrite_sections("简历正文", jd, match, {})` 调用仍可用。

- [ ] **Step 7: Commit**

```bash
git add src/openjob/db.py src/openjob/ai/resume_engine/engine.py src/openjob/ai/resume_engine/optimizer.py tests/test_resume_engine.py tests/test_resume_bases.py
git commit -m "feat: use audited materials in tailored resumes"
```

## Task 4: Web API、文件安全和模板下载

**Files:**
- Modify: `src/openjob/web/server.py`
- Modify: `tests/test_web_api_routes.py`
- Modify: `tests/test_resume_upload_safety.py`

- [ ] **Step 1: 先写 API 失败测试**

在现有 WSGI 测试辅助函数中增加 multipart XLSX 构造器和以下测试：

```python
def test_materials_upload_status_preview_and_replace():
    # POST /api/resume/materials/upload
    # GET /api/resume/materials/status 断言 filename、count、sha256、updated_at、valid=True
    # GET /api/resume/materials?type=award&q=一等奖 只返回匹配项
    # 再上传第二个合法文件，断言 count 和 sha256 更新且旧索引不残留

def test_materials_upload_rejects_bad_workbook_without_replacing_existing_file():
    # 先上传合法文件，再上传缺列文件
    # 断言 400、错误说明缺少必需列，随后 status 仍是第一次文件的 hash/count

def test_materials_template_and_delete():
    # GET /api/resume/materials/template 断言 200、Content-Type 为 xlsx、文件可由 openpyxl 打开
    # DELETE /api/resume/materials 断言 success=True，随后 status.valid=False 且列表为空
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_web_api_routes.py -k material -q`

Expected: FAIL，因为素材路由尚未注册。

- [ ] **Step 3: 实现 API 路由**

在 `server.py` 的简历 API 区域加入：

- `GET /api/resume/materials/status`：返回 `{valid, enabled, filename, count, sha256, updated_at, errors}`；无文件时 `valid=false`、`errors=[]`。
- `GET /api/resume/materials`：参数 `type`、`q`、`limit`，只返回已校验索引中的素材；响应中的每条素材不包含 `source`、`notes`，并返回 `total`。
- `POST /api/resume/materials/upload`：只接收字段 `file`；读取后限制 10MB，调用 `parse_workbook`，成功才调用原子保存；成功响应包含 `success, filename, count, sha256`。
- `POST /api/resume/materials/refresh`：重新读取当前 `resume_materials_path`，哈希变化或索引缺失时重建；失败返回 400 且不删除原文件。
- `GET /api/resume/materials/template`：调用 `build_template_workbook()`，返回可直接填写的 `.xlsx` 下载，文件名 `openjob-resume-materials-template.xlsx`。
- `DELETE /api/resume/materials`：只删除由当前配置解析出的、且位于 `DATA_DIR` 内的标准 XLSX 和 JSON 索引，并把 `profile.resume_materials_path` 写为空；不能根据请求传入的任意路径删除文件。

所有路由使用现有 `_json_response` 和 `ResumeUploadError` 风格；解析错误 400，文件系统/序列化异常 500 并返回可读错误；成功上传后不要把素材正文写进 config.yaml。`server.set_base_dir()` 后 API、engine、索引路径必须指向同一个临时 `data/`。

- [ ] **Step 4: 接入简历生成 API 的审计返回**

`GET /api/resume/version/<resume_id>` 保持旧字段，同时返回数据库新增字段；`POST /api/resume/generate` 保持现有请求体不变，只让素材由配置决定。禁止增加“自动投递”参数或绕过人工确认的分支。

- [ ] **Step 5: 运行 API 和安全测试**

Run: `pytest tests/test_web_api_routes.py tests/test_resume_upload_safety.py -q`

Expected: API 上传、替换、损坏文件保护、模板下载和删除测试 PASS，原有简历上传安全测试不回归。

- [ ] **Step 6: Commit**

```bash
git add src/openjob/web/server.py tests/test_web_api_routes.py tests/test_resume_upload_safety.py
git commit -m "feat: add resume materials web api"
```

## Task 5: 配置页素材库 UI

**Files:**
- Create: `src/openjob/web/frontend/src/components/config/ResumeMaterials.tsx`
- Modify: `src/openjob/web/frontend/src/pages/ConfigPage.tsx`

- [ ] **Step 1: 实现状态和交互，不改现有视觉系统**

`ResumeMaterials.tsx` 使用现有 `Button`、`Input`、`Card` 风格和 `lucide-react` 图标，提供：

- 当前状态：已启用/已关闭、文件名、素材数量、更新时间、短 hash；无文件/解析失败时显示明确错误。
- 上传/替换 `.xlsx`：上传中禁用按钮；成功后刷新 status 和预览；失败显示后端错误。
- “下载模板”按钮：打开 `/api/resume/materials/template`。
- “刷新索引”按钮：调用 refresh；如果文件被外部修改，显示新的 count/hash。
- “清空素材库”按钮：二次确认后 DELETE，不能误删简历底稿。
- 预览筛选：type 下拉、关键词输入、列表展示 `id/title/type/organization/role/date/description/achievements/skills/target_directions/priority`；不显示 source/notes。
- 开关绑定 `config.profile.resume_materials_enabled`，沿用 `useConfig` 的保存/未保存状态；上传文件本身立即保存到本地，开关随配置页“保存”提交。

组件不得把完整素材正文塞进复杂弹窗；保持现有配置页紧凑的模块布局，移动端允许换行，按钮使用图标+文字，所有异步状态都有可见反馈。

- [ ] **Step 2: 在 ConfigPage 挂载组件**

在 `个人信息` 的 `BaseResumes` 后加入 `<ResumeMaterials config={config} updateConfig={updateConfig} />`。组件只通过 API 读写素材，不把 XLSX 二进制放入 React state。沿用当前 `/config?section=profile` 自动展开逻辑。

- [ ] **Step 3: 构建前端**

Run: `cd src/openjob/web/frontend; npm run build`

Expected: `tsc && vite build` 成功，无 TypeScript 错误；确认生成 `src/openjob/web/frontend/dist`。

- [ ] **Step 4: Commit**

```bash
git add src/openjob/web/frontend/src/components/config/ResumeMaterials.tsx src/openjob/web/frontend/src/pages/ConfigPage.tsx
git commit -m "feat: add resume materials config panel"
```

## Task 6: 简历工作台素材审计展示和人工 PATCH 防护

**Files:**
- Modify: `src/openjob/web/frontend/src/pages/ResumePage.tsx`
- Modify: `src/openjob/web/server.py`
- Modify: `tests/test_web_api_routes.py`

- [ ] **Step 1: 先写 PATCH 防护测试**

测试一个已有 `material_selection_json=[m1]` 的 review 版本：提交带 `material_ids=["m2"]` 的 diff，断言 400 且错误包含“本版本选中素材”；再提交原始 `m1`，断言成功且 material_ids 保留。

- [ ] **Step 2: 实现服务端审计校验**

在 `api_resume_version_patch` 读取 `material_selection_json`，建立允许 ID 集；每个 diff item 的 `material_ids` 必须是字符串数组且是允许 ID 子集。重新合成和 `validate_assembled` 成功后，把完整 diff JSON 写回。空数组合法，旧版本没有审计字段时只允许空数组，保持旧版本可编辑但不允许新增未经审计素材。

- [ ] **Step 3: 在 ResumePage 显示素材来源**

在 `VersionDetail` 增加 `material_library_sha256`、`material_selection_json`、`material_candidates_json`。解析后显示一个紧凑的“本版本素材”区域：素材 ID、标题、类型、适用方向和被引用次数；每个 diff 块若有 `material_ids`，显示“来源：m1 · 标题”。解析失败时显示“素材审计数据不可读”，不影响旧版本正文预览。

- [ ] **Step 4: 运行前端和 API 测试**

Run: `pytest tests/test_web_api_routes.py tests/test_resume_engine.py -q`

Expected: PATCH 审计测试和现有版本审阅测试 PASS；再运行 `cd src/openjob/web/frontend; npm run build`，Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/openjob/web/server.py src/openjob/web/frontend/src/pages/ResumePage.tsx tests/test_web_api_routes.py
git commit -m "feat: show audited resume material sources"
```

## Task 7: 全量回归和浏览器验收

**Files:**
- Modify: `README.md`，补充素材库使用说明、列名表和安全边界。
- Test: all existing Python and frontend tests.

- [ ] **Step 1: 编写文档说明**

在 README 增加“简历素材库”小节，明确：使用配置页下载模板；填写 `素材库` 工作表；`id` 必须稳定；上传后可刷新/替换；`resume_allowed=否` 不会被 AI 使用；生成后仍需人工审阅和手动发送；素材保存在本地，不上传公开仓库。

- [ ] **Step 2: 运行 Python 全量测试**

Run: `pytest -q`

Expected: 全部 PASS；若失败，只修复本功能引入的回归，不删除或跳过既有测试。

- [ ] **Step 3: 运行前端构建**

Run: `cd src/openjob/web/frontend; npm run build`

Expected: `tsc && vite build` PASS。

- [ ] **Step 4: 做真实浏览器验收**

从项目根目录启动：`openjob web`，访问 `http://127.0.0.1:8686/config`，验收以下流程：

1. 下载模板并用 Excel 填写至少一条 experience、一条 award、一条 student_work。
2. 上传合法文件，状态显示文件名、条数和 hash；刷新页面后状态仍存在。
3. 上传缺列或损坏 XLSX，页面显示明确错误，原来的合法库仍可用。
4. 用一个 JD 生成简历，确认 diff 块显示素材来源；确认实时预览没有新增行、没有破坏固定信息。
5. 撤销一个素材改写块并保存，确认该素材内容从预览中消失。
6. 导出 Word，确认排版没有新增表格行；确认“标记已发送”仍是独立人工动作。
7. 关闭素材库开关再次生成，确认流程与改动前一致。

- [ ] **Step 5: 检查敏感数据和产物**

Run: `git status --short; git diff --check; rg -n "resume_materials|material_selection|material_ids" src tests README.md`

Expected: `data/resume_materials.xlsx`、`data/resume_materials.index.json`、真实简历和生成简历没有被加入 Git；`git diff --check` 无空白错误；没有把 API key、source 私密备注或 notes 送入 AI prompt。

- [ ] **Step 6: Final commit**

```bash
git add README.md
git commit -m "docs: document resume material library"
```

## 验收标准

- [ ] 合法 XLSX 可上传、替换、刷新、预览、下载模板和清空；非法文件不会替换旧库。
- [ ] 用户更新 XLSX 后，索引通过 SHA-256 检测到变化并重新解析；不存在或损坏时明确失败。
- [ ] 候选素材最多 8 条，AI 最多选 4 条，且只能选择候选集内 `resume_allowed=是` 的 ID。
- [ ] 每个新增素材事实都能通过 `material_ids` 追溯到版本中的素材快照；未选中的素材不会被使用。
- [ ] 事实校验仍阻止 AI 虚构数字、日期、组织、奖项和技能；底稿与选中素材之外的事实会让版本失败。
- [ ] 现有一页结构、行数、表格写回规则、人工审阅、Word/PDF 导出和人工发送流程不被破坏。
- [ ] 关闭素材库后，旧的无素材库简历生成测试和行为全部保持不变。
- [ ] 原始 XLSX、索引和素材快照只留在本地数据目录；没有新增自动投递或自动发送入口。

## 计划自检

- 需求覆盖：XLSX 不定期更新、经历/奖项/学生工作素材、JD 选择、简历生成、UI 管理、审计、防虚构、人工投递边界均已落到任务和测试。
- 占位符扫描：本计划不使用 `TBD`、`TODO` 或“以后补充”等未定义实现；所有核心函数、路径、字段和 API 名称已固定。
- 类型一致性：`material_ids` 在 `SectionChange`、diff JSON、engine、PATCH API 和前端展示中统一；素材审计字段统一为 `material_library_sha256`、`material_selection_json`、`material_candidates_json`。
- 回归策略：先写失败测试，再实现，再运行局部测试、全量测试和浏览器验收；旧流程通过 `resume_materials_enabled=False` 进行明确回归。
