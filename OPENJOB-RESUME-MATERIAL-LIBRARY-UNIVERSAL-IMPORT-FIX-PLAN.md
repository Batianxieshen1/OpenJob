# OpenJob 任意 Excel 智能导入验收修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` (recommended) or `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复当前智能导入的完整性、安全性和交互缺口，使其他用户从 GitHub 拉取 OpenJob 后，能够上传自己的常见 `.xlsx` 文件，完成 Sheet/表头/字段/类型/错误行修正，并让确认后的素材可靠参与 JD 匹配与简历优化。

**Architecture:** 保留现有“暂存会话 → 分析/预览 → 用户确认 → 严格转换 → 正式库”的两阶段信任边界。后端作为唯一可信源，负责会话校验、文件 hash、资源上限、映射 schema、逐行验证和正式库串行写入；前端只编辑导入配置并展示后端重新计算的预览，不能把浏览器中的预览数据当作确认依据。

**Tech Stack:** Python 3.11+、Bottle、openpyxl、pytest/unittest、React 18、TypeScript、Vite、Tailwind CSS、Lucide React、Puppeteer Core。

---

## 0. 执行规则和完成定义

本计划是对提交 `3683c32`（`feat: 任意 Excel 智能导入——分析→映射→预览→确认全流程`）的验收修复。不要重写已有素材库、JD 匹配或简历生成系统；只修复本计划列出的导入链路问题。

### 必须保留的现有能力

- 标准模板上传继续可用；
- 常见中文/英文列名自动映射继续可用；
- 多 Sheet 分析继续可用；
- 未确认会话绝不影响正式素材库；
- `source`、`notes` 和 `resume_allowed=false` 继续不进入 AI；
- 已确认素材继续参与 JD 排序、候选素材 Prompt、`material_ids` 校验和审计；
- 最多 8 条候选、最多选择 4 条、最多 3 个素材支持的改写块等现有硬限制不得放宽；
- 不得回退或覆盖工作区中与本计划无关的用户修改。

### “任意 Excel”的准确边界

完成后产品文案使用“任意常见 `.xlsx` 智能导入”，不宣称支持所有 Excel 格式：

- 支持 `.xlsx`；
- 本阶段不支持旧版 `.xls`，应提示用户转换成 `.xlsx`；
- 支持多个可见/隐藏 Sheet、表头不在第一行、中文别名、行级类别列；
- 支持用户修正表头行、列映射、Sheet 是否导入、Sheet 类型和错误行排除；
- 暂不要求完整理解任意合并单元格或任意多层表头，但必须允许用户通过指定表头行完成恢复；
- 超过资源上限时必须拒绝整个导入并说明原因，绝不能静默截断。

### 完成门槛

- 本文所有复选框完成；
- 新增测试先失败、实现后通过；
- 完整测试 `522+` 全部通过；
- `npm run build` 通过；
- 浏览器完成桌面、窄屏、浅色、深色验收；
- 250 行素材不得只导入 199 行；
- 暂存文件被替换或确认请求缺少 hash 时必须拒绝；
- 混合类别 Sheet 必须保留每行真实类型；
- 点击取消后暂存目录消失，状态不再显示导入中；
- 导入中存在未排除的坏行时，不得静默丢弃后继续写正式库。

---

## 1. 文件结构

### 后端

- Modify: `src/openjob/resume_materials_import.py`
  - 导入限制、会话序列化、Sheet 扫描、类型归一化、配置校验、预览和严格确认。
- Modify: `src/openjob/web/server.py`
  - analyze/preview/confirm/cancel/status API、TTL 清理、hash 复核、素材库写锁、受控错误响应。
- Modify: `src/openjob/resume_materials.py`
  - 复用标准字段与类型常量；保留现有正式库保存格式。

### 前端

- Modify: `src/openjob/web/frontend/src/components/config/MaterialImportWizard.tsx`
  - 管理向导阶段、导入配置、取消、二次确认和完成摘要。
- Create: `src/openjob/web/frontend/src/components/config/material-import/types.ts`
  - API DTO、Sheet 配置、行预览、警告类型。
- Create: `src/openjob/web/frontend/src/components/config/material-import/ImportSheetPanel.tsx`
  - Sheet 开关、表头行、类型模式和字段映射。
- Create: `src/openjob/web/frontend/src/components/config/material-import/ImportPreviewTable.tsx`
  - 行状态、错误信息、排除选择和分页。
- Modify: `src/openjob/web/frontend/src/components/config/ResumeMaterials.tsx`
  - 必要时刷新正式库状态；不要复制向导状态。
- Modify: `src/openjob/web/frontend/scripts/ui-acceptance.cjs`
  - 增加智能导入的浏览器回归测试。

### 测试和文档

- Modify: `tests/test_resume_materials_import.py`
- Modify: `tests/test_web_api_routes.py`
- Modify: `README.md`
- Regenerate only: `src/openjob/web/frontend/dist/**`
  - 只能由 `npm run build` 生成，不得手工编辑压缩产物。

---

## 2. 后端契约

### 2.1 资源上限

在 `resume_materials_import.py` 定义并集中使用：

```python
MAX_IMPORT_SHEETS = 50
MAX_IMPORT_ROWS_PER_SHEET = 5_000
MAX_IMPORT_TOTAL_ROWS = 20_000
MAX_IMPORT_COLUMNS = 100
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
IMPORT_SESSION_TTL_SECONDS = 24 * 60 * 60
```

规则：

- 保留现有 10 MB 上传文件限制；
- 使用 `zipfile.ZipFile` 检查所有 ZIP entry 的总解压尺寸，不读取宏或执行公式；
- 扫描每个 Sheet 时最多读取到“上限 + 1 个非空数据行”；发现第 5001 行后立即拒绝整个分析；
- 总行数、Sheet 数或列数超限同样返回 400；
- 不再使用 `max_row=200`；
- 错误必须包含实际检测值和允许上限；
- 超限时不得创建可确认的会话，也不得修改正式库。

### 2.2 会话模型

`ImportSession` 增加 `created_at`，并区分内部存储和公开响应：

```python
@dataclass
class ImportSession:
    import_id: str
    filename: str
    source_sha256: str
    staged_path: Path | None
    created_at: str
    sheets: list[dict]
    warnings: list[str]

    def to_public_dict(self) -> dict:
        return {
            "import_id": self.import_id,
            "filename": self.filename,
            "source_sha256": self.source_sha256,
            "created_at": self.created_at,
            "sheets": self.sheets,
            "warnings": self.warnings,
        }

    def to_storage_dict(self) -> dict:
        payload = self.to_public_dict()
        payload["staged_path"] = str(self.staged_path) if self.staged_path else ""
        return payload
```

要求：

- `analysis.json` 使用 `to_storage_dict()`；
- analyze/preview/confirm 的响应使用 `to_public_dict()` 或专用 DTO；
- 浏览器响应中不得出现服务器绝对路径；
- 对旧会话缺少 `created_at` 的情况按文件 mtime 判断是否过期；
- `import_id` 只接受 32 位小写十六进制 UUID hex。

### 2.3 预览 API

新增：

```text
POST /api/resume/materials/preview
```

请求：

```json
{
  "import_id": "32位hex",
  "source_sha256": "64位完整sha256",
  "sheet": {
    "name": "全部经历",
    "include": true,
    "header_row": 3,
    "field_mapping": {
      "类别": "type",
      "名称": "title",
      "工作内容": "description"
    },
    "type_override": null,
    "excluded_rows": [12]
  },
  "defaults": {
    "resume_allowed": true,
    "priority": 3
  },
  "page": 1,
  "page_size": 50
}
```

响应：

```json
{
  "success": true,
  "sheet": {
    "name": "全部经历",
    "columns": ["类别", "名称", "工作内容"],
    "mapping": [],
    "total_rows": 250,
    "valid_rows": 248,
    "invalid_rows": 2,
    "excluded_rows": 1
  },
  "rows": [
    {
      "excel_row": 4,
      "generated_id": "imp_all_4a91c6e72f",
      "type": "experience",
      "title": "运营实习",
      "description": "整理活动数据并建立周报，覆盖 12 个渠道。",
      "status": "valid",
      "issues": [],
      "excluded": false
    }
  ],
  "page": 1,
  "page_size": 50,
  "total_pages": 5
}
```

要求：

- 预览必须重新读取服务端暂存文件；
- 必须复核 hash；
- `header_row` 改变且客户端没有提交 mapping 时，返回该行列名和新的建议 mapping；
- `excel_row` 使用真实 Excel 行号，不再使用“第几个非空数据行”的模糊编号；
- 预览和确认调用同一个纯函数完成映射、类型归一化、ID 生成和逐行验证；
- 前端传回的 `rows`、`status`、`generated_id` 均不可信，确认时重新计算。

### 2.4 类型优先级

`type_override` 只在用户明确选择“整张 Sheet 统一类型”时传固定枚举。默认传 `null`。

每行类型按以下顺序解析：

1. 非空且合法的显式 `type_override`；
2. 行内映射到 `type` 的标准英文值或中文类别别名；
3. 分析阶段的 Sheet 类型推断；
4. `other`。

合法类型复用 `resume_materials.MATERIAL_TYPES`：

```text
experience, project, award, student_work,
campus_activity, skill_evidence, certification, other
```

行内未知类型不能静默变成另一种类型：预览返回 warning，并回退到 Sheet 推断或 `other`。

### 2.5 确认请求校验

服务端必须拒绝以下请求：

- 缺少或格式错误的完整 `source_sha256`；
- 客户端 hash、`analysis.json` hash、当前 `source.xlsx` 重新计算的 hash 三者不一致；
- Sheet 不在分析会话中；
- `header_row` 不在工作表实际范围内；
- 源列不在所选表头行中；
- 目标字段不属于 `DEFAULT_HEADERS`；
- `type_override` 不属于 `MATERIAL_TYPES` 且不是 `null`/空；
- `resume_allowed` 不是布尔值；
- `priority` 不是 1 到 5 的整数；
- `excluded_rows` 不是该 Sheet 中合法的真实 Excel 行号；
- 同一个 Sheet 重复提交；
- 没有任何包含且可导入的 Sheet。

允许把多个文本源列合并到 `description`、`achievements`、`skills`、`keywords` 等字段；但 `id`、`type`、`resume_allowed`、`priority` 只能映射一次。错误返回 400，并给出字段级中文信息。

### 2.6 坏行处理

以下行标记为 invalid：

- 缺少标题；
- 缺少事实描述；
- 用户 ID 非法且无法生成安全 ID；
- ID 与本次其他未排除行重复；
- 其他无法通过正式库严格 parser 的内容。

确认规则：

- 示例行标记为 `example`，前端默认勾选排除并明确展示；
- invalid 行必须由用户在 UI 中排除，或修正映射后变为 valid；
- 如果仍有未排除 invalid 行，confirm 返回 422，包含 `invalid_rows` 摘要；
- 不允许后端默默丢弃坏行并返回成功；
- 用户主动排除的行进入 warnings/audit，并计入准确的 `excluded_rows`；
- 至少保留一条 valid 且未排除的素材才允许覆盖正式库。

### 2.7 会话清理

新增纯函数和受控清理入口：

```python
def cleanup_expired_import_sessions(imports_dir: Path, *, now: datetime | None = None) -> int:
    root = imports_dir.resolve()
    if not root.exists():
        return 0

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    removed = 0
    import_id_pattern = re.compile(r"^[0-9a-f]{32}$")
    for child in root.iterdir():
        if not child.is_dir() or not import_id_pattern.fullmatch(child.name):
            continue

        resolved = child.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue

        analysis_path = resolved / "analysis.json"
        created_at: datetime | None = None
        try:
            data = json.loads(analysis_path.read_text(encoding="utf-8"))
            raw_created_at = str(data.get("created_at") or "")
            if raw_created_at:
                created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            created_at = None

        if created_at is None:
            timestamp_source = analysis_path if analysis_path.exists() else resolved
            created_at = datetime.fromtimestamp(timestamp_source.stat().st_mtime, timezone.utc)
        elif created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        if (current - created_at.astimezone(timezone.utc)).total_seconds() < IMPORT_SESSION_TTL_SECONDS:
            continue

        shutil.rmtree(resolved)
        removed += 1

    return removed
```

同时在模块顶部增加 `shutil`、`datetime`、`timezone` 所需 import。测试必须覆盖符号链接或解析后越出 `imports_dir` 的目录不会被删除。

要求：

- 只遍历 `resume_material_imports` 的直接子目录；
- 删除前用 `resolve()` + `relative_to()` 再次验证边界；
- 非法目录名不得作为会话，但也不能越界删除；
- analyze、preview、confirm、cancel、status 入口调用清理；
- 显式取消立即删除当前会话；
- 成功确认立即删除当前会话；
- 可修正的 400/422 确认错误保留会话，等待用户修正或 TTL；
- 损坏/过期会话返回明确 400/404，并安全清理；
- `import_in_progress` 只统计未过期、结构完整的会话。

### 2.8 正式库并发写入

在 `server.py` 增加：

```python
material_library_lock = Lock()
```

锁必须覆盖正式库的最终变更：

- confirm 的 `commit_import`；
- 兼容 upload 的 `save_library_atomically`；
- refresh；
- delete。

文件解析和预览可在锁外完成，缩短锁时间。不要复用 `job_mutation_lock`，两个领域的锁应独立。

---

## 3. 分任务执行

### Task 1：补齐资源限制并消除 200 行静默截断

**Files:**
- Modify: `src/openjob/resume_materials_import.py`
- Test: `tests/test_resume_materials_import.py`
- Test: `tests/test_web_api_routes.py`

- [ ] **Step 1：先添加失败测试**

新增以下测试，每个测试都使用 `tmp_path` 隔离数据：

- `test_analyze_and_normalize_all_250_rows_without_truncation`：用 openpyxl 创建表头和 250 条有效素材，调用 analyze 后断言 `row_count == 250`；用分析结果生成确认 payload，normalize/commit 后断言正式库 `count == 250`，且首尾标题均存在。
- `test_over_row_limit_is_rejected_instead_of_partially_imported`：创建 5001 条非空数据，断言 analyze 抛出 `MaterialLibraryError`，错误同时包含实际行数和上限 5000。
- `test_over_column_limit_is_rejected`：创建 101 个非空列，断言 analyze 抛出 `MaterialLibraryError`，错误同时包含实际列数和上限 100。
- `test_xlsx_zip_expansion_limit_is_rejected`：构造压缩后低于 10 MB、ZIP entry 声明总解压尺寸超过 100 MB 的 XLSX，断言在调用 openpyxl 前被拒绝。
- `test_over_limit_analysis_does_not_create_session_or_replace_library`：先写入一份可读取的正式库并记录 bytes/hash，再分析超限文件；断言 imports 根目录没有新增 32 位会话目录，正式 XLSX 和索引 bytes/hash 均未变化。

250 行测试必须创建 250 条不同的真实测试行，而不是重复引用同一个 row 对象；确认结果必须断言 `count == 250`。

- [ ] **Step 2：运行测试并确认旧代码失败**

```powershell
python -m pytest tests/test_resume_materials_import.py tests/test_web_api_routes.py -q
```

预期：250 行测试在旧实现中只得到 199 行。

- [ ] **Step 3：实现集中式资源校验和完整扫描**

移除 analyze/normalize 中所有 `max_row=200`，使用同一套安全迭代函数。达到上限时抛出 `MaterialLibraryError`，不得返回部分数据。

- [ ] **Step 4：运行相关测试**

```powershell
python -m pytest tests/test_resume_materials_import.py tests/test_web_api_routes.py -q
```

预期：全部通过。

- [ ] **Step 5：提交（仓库流程允许时）**

```powershell
git add src/openjob/resume_materials_import.py tests/test_resume_materials_import.py tests/test_web_api_routes.py
git commit -m "fix: prevent silent truncation in material imports"
```

### Task 2：修复会话 hash、安全序列化和服务端 schema 校验

**Files:**
- Modify: `src/openjob/resume_materials_import.py`
- Modify: `src/openjob/web/server.py`
- Test: `tests/test_web_api_routes.py`

- [ ] **Step 1：先添加失败测试**

新增以下 API 测试：

- `test_analyze_response_does_not_expose_staged_path`：上传有效 XLSX，递归检查 JSON 响应的所有 dict/list，断言不存在 `staged_path`，也不存在 `BASE_DIR` 的绝对路径文本。
- `test_confirm_requires_full_source_sha256`：分别发送缺少 hash、空 hash、63 位 hash 和非十六进制 hash，断言均返回 400，正式库未变化。
- `test_confirm_recomputes_hash_after_staged_file_is_replaced`：分析文件 A 后，用结构合法但内容不同的文件 B 覆盖会话 `source.xlsx`，携带 A 的完整 hash 确认，断言返回 400 且错误包含“文件已变化”，正式库未变化。
- `test_confirm_rejects_sheet_not_present_in_analysis`：在 payload 中加入分析结果没有的 Sheet 名，断言 400，不允许以 warning 跳过。
- `test_confirm_rejects_unknown_target_field`：把一个源列映射为 `unknown_field`，断言 400，并返回具体字段名。
- `test_confirm_rejects_source_column_not_in_selected_header`：映射不存在于指定表头行的源列，断言 400，并返回 Sheet 与源列名。
- `test_confirm_rejects_duplicate_sheet_config`：payload 对同一 Sheet 提交两份配置，断言 400。
- `test_confirm_rejects_invalid_type_override_and_defaults`：参数化覆盖非法类型、`priority=0`、`priority=6`、非整数 priority 和非布尔 `resume_allowed`，断言均为 400。
- `test_corrupt_analysis_json_returns_controlled_error`：把 `analysis.json` 写成非法 JSON，调用 preview 和 confirm，断言响应为 400 或 404、包含可操作错误，且日志/响应没有 traceback。
- `test_invalid_confirm_preserves_existing_official_library`：先记录正式 XLSX 与索引 bytes/hash，再执行上述任一非法确认，断言两份文件均逐字节不变。

篡改测试必须用另一个结构仍合法的 XLSX 覆盖暂存 `source.xlsx`，然后携带旧 hash 确认，预期 400 且正式库不变。

- [ ] **Step 2：确认失败**

```powershell
python -m pytest tests/test_web_api_routes.py -k "material and (hash or staged or mapping or corrupt)" -q
```

- [ ] **Step 3：实现内部/公开 DTO、三方 hash 比较和 schema 校验**

确认时必须按固定顺序：读取受信任 `analysis.json` → 读取暂存 bytes → 重算 hash → 校验请求配置 → 生成预览/归一化结果 → 严格 parser → 获取写锁 → 写正式库。

- [ ] **Step 4：运行测试并确认错误响应是 400/404/422，不是 500**

```powershell
python -m pytest tests/test_web_api_routes.py -q
```

- [ ] **Step 5：提交（仓库流程允许时）**

```powershell
git add src/openjob/resume_materials_import.py src/openjob/web/server.py tests/test_web_api_routes.py
git commit -m "fix: harden material import sessions and confirmation"
```

### Task 3：实现统一预览、行级验证和混合类型解析

**Files:**
- Modify: `src/openjob/resume_materials_import.py`
- Modify: `src/openjob/web/server.py`
- Test: `tests/test_resume_materials_import.py`
- Test: `tests/test_web_api_routes.py`

- [ ] **Step 1：先添加失败测试**

新增以下单元/API 测试：

- `test_mixed_type_sheet_preserves_each_row_type_without_override`：同一 Sheet 写入 `实习经历`、`奖项`、`学生工作` 三行，提交 `type_override=null`，断言结果类型依次为 `experience`、`award`、`student_work`。
- `test_explicit_sheet_type_override_applies_to_every_row`：复用混合类别文件，显式提交 `type_override="project"`，断言三行全部为 `project`。
- `test_preview_and_confirm_generate_same_ids_and_types`：先获取完整 preview，再 confirm；按 `excel_row` 对齐，断言每条未排除有效行的 ID 和 type 与正式库完全一致。
- `test_preview_recomputes_columns_after_header_row_change`：工作簿第 1 行是说明、第 3 行是表头；先用错误表头行预览，再改为 3，断言响应 columns、mapping 和行状态来自第 3 行。
- `test_excluded_rows_use_real_excel_row_numbers`：在数据间插入空行，排除 preview 返回的 `excel_row`；断言被排除的是对应工作表物理行，而不是压缩后的数据序号。
- `test_confirm_rejects_unexcluded_invalid_rows`：创建一条缺 title 和一条缺 description 的行，不排除即 confirm，断言 422、`invalid_rows` 精确列出 Excel 行号和原因，正式库不变。
- `test_confirm_accepts_when_user_explicitly_excludes_invalid_rows`：将上个测试返回的物理行号加入 `excluded_rows` 后重试，断言 200，正式库只包含有效行。
- `test_confirm_reports_exact_excluded_row_count`：同时排除一条 example、一条 invalid 和一条用户主动排除的 valid 行，断言响应按约定返回准确总数和分类明细，不从 warnings 数量推算。

混合类型测试至少包含 `实习经历`、`奖项`、`学生工作` 三行，预期分别映射成 `experience`、`award`、`student_work`。

- [ ] **Step 2：运行测试并确认失败**

```powershell
python -m pytest tests/test_resume_materials_import.py tests/test_web_api_routes.py -k "preview or mixed or excluded or invalid" -q
```

- [ ] **Step 3：抽取预览和确认共用的纯函数**

实现以下数据边界和共用接口：

```python
@dataclass(frozen=True)
class ImportRowValidation:
    excel_row: int
    generated_id: str
    material: dict
    status: str
    issues: tuple[str, ...]
    excluded: bool


@dataclass(frozen=True)
class SheetValidationResult:
    name: str
    columns: tuple[str, ...]
    mapping: tuple[dict, ...]
    rows: tuple[ImportRowValidation, ...]

    @property
    def valid_rows(self) -> tuple[ImportRowValidation, ...]:
        return tuple(row for row in self.rows if row.status == "valid" and not row.excluded)

    @property
    def invalid_rows(self) -> tuple[ImportRowValidation, ...]:
        return tuple(row for row in self.rows if row.status == "invalid" and not row.excluded)


def validate_import_sheet(
    source_content: bytes,
    *,
    session: ImportSession,
    sheet_config: dict,
    defaults: dict,
) -> SheetValidationResult:
    """读取一个 Sheet，并返回 preview 与 confirm 共用的逐行验证结果。"""
    validated_config = validate_sheet_config(session, sheet_config, defaults)
    sheet_rows = read_configured_sheet_rows(
        source_content,
        sheet_name=validated_config.name,
        header_row=validated_config.header_row,
    )
    return normalize_and_validate_sheet_rows(
        sheet_rows,
        config=validated_config,
        defaults=defaults,
    )
```

`validate_sheet_config` 必须完成 Task 2 定义的白名单/schema 校验；`read_configured_sheet_rows` 必须返回原始 Excel 行号并复用 Task 1 的资源限制；`normalize_and_validate_sheet_rows` 必须承载现有 `_map_row_values`、类型优先级、稳定 ID、example 检测和严格字段校验。三个函数都在 `resume_materials_import.py` 中定义并分别单测。`SheetValidationResult` 同时向 preview 和 confirm 提供行状态；不得复制两套类型和 ID 逻辑。

- [ ] **Step 4：实现 preview API 与 422 坏行阻断**

确认响应的统计必须使用结构化计数，不得通过统计 warning 字符串推算。

- [ ] **Step 5：运行测试**

```powershell
python -m pytest tests/test_resume_materials_import.py tests/test_web_api_routes.py -q
```

- [ ] **Step 6：提交（仓库流程允许时）**

```powershell
git add src/openjob/resume_materials_import.py src/openjob/web/server.py tests/test_resume_materials_import.py tests/test_web_api_routes.py
git commit -m "feat: add trustworthy material import preview validation"
```

### Task 4：实现 TTL、显式取消和正式库写锁

**Files:**
- Modify: `src/openjob/resume_materials_import.py`
- Modify: `src/openjob/web/server.py`
- Test: `tests/test_resume_materials_import.py`
- Test: `tests/test_web_api_routes.py`

- [ ] **Step 1：先添加失败测试**

新增以下单元/API 测试：

- `test_cleanup_removes_only_expired_sessions_inside_import_root`：在 imports 根目录创建一个过期会话、一个相邻哨兵文件和一个解析后越界的链接；执行清理后只允许过期会话消失，哨兵与边界外目标必须保留。
- `test_cleanup_keeps_active_sessions`：创建 `created_at` 距当前时间 23 小时 59 分的有效会话，断言返回删除数为 0，目录与两个会话文件仍存在。
- `test_status_ignores_and_cleans_expired_or_corrupt_sessions`：并存 active、expired、corrupt 会话；调用 status 后断言只统计 active，expired 被清理，corrupt 返回受控 warning 或被安全清理，不能触发 500。
- `test_cancel_cannot_escape_import_root`：参数化提交 `../`、绝对路径、符号链接目标和非 32 位小写 hex ID，断言 400/404，imports 根目录外的哨兵保持不变。
- `test_failed_correctable_confirm_keeps_session`：制造未排除 invalid 行得到 422，断言会话目录仍存在，随后排除该行可继续确认。
- `test_successful_confirm_removes_session`：有效确认返回 200 后，断言会话目录及 `source.xlsx`、`analysis.json` 均不存在。
- `test_material_mutations_share_dedicated_lock`：用可观测的 fake lock 替换 `material_library_lock`，分别调用 confirm、兼容 upload、refresh、delete，断言每个正式库变更入口均在 lock context 内执行；analyze 和 preview 不应获取该锁。

边界测试需要在 imports 目录旁创建哨兵文件，并断言清理后仍存在。

- [ ] **Step 2：运行并确认失败**

```powershell
python -m pytest tests/test_resume_materials_import.py tests/test_web_api_routes.py -k "cleanup or expired or cancel or lock" -q
```

- [ ] **Step 3：实现 TTL 清理和写锁**

不得用字符串前缀判断路径；所有递归删除必须在同一个 Python/PowerShell 进程中完成 `resolve()` + `relative_to()` 校验。

- [ ] **Step 4：运行测试**

```powershell
python -m pytest tests/test_resume_materials_import.py tests/test_web_api_routes.py -q
```

- [ ] **Step 5：提交（仓库流程允许时）**

```powershell
git add src/openjob/resume_materials_import.py src/openjob/web/server.py tests/test_resume_materials_import.py tests/test_web_api_routes.py
git commit -m "fix: clean stale imports and serialize material writes"
```

### Task 5：补全导入向导的可修正交互

**Files:**
- Modify: `src/openjob/web/frontend/src/components/config/MaterialImportWizard.tsx`
- Create: `src/openjob/web/frontend/src/components/config/material-import/types.ts`
- Create: `src/openjob/web/frontend/src/components/config/material-import/ImportSheetPanel.tsx`
- Create: `src/openjob/web/frontend/src/components/config/material-import/ImportPreviewTable.tsx`
- Modify: `src/openjob/web/frontend/src/components/config/ResumeMaterials.tsx`

- [ ] **Step 1：建立类型安全 DTO**

至少定义：

```typescript
export type MaterialType =
  | 'experience' | 'project' | 'award' | 'student_work'
  | 'campus_activity' | 'skill_evidence' | 'certification' | 'other'

export interface ImportSheetConfig {
  name: string
  include: boolean
  header_row: number | null
  field_mapping: Record<string, string>
  type_override: MaterialType | null
  excluded_rows: number[]
}

export interface ImportPreviewRow {
  excel_row: number
  generated_id: string
  type: MaterialType
  title: string
  description: string
  status: 'valid' | 'invalid' | 'example'
  issues: string[]
  excluded: boolean
}
```

- [ ] **Step 2：实现 Sheet 控制**

每个 Sheet 面板必须提供：

- 导入开关；
- 隐藏状态提示及手动包含能力；
- 表头行数字输入；
- “使用每行类别/自动推断”与“整张 Sheet 统一类型”的模式；
- 固定枚举类型下拉；
- 列映射、置信度、推断原因、最多 3 个样例值；
- 显示总行、有效行、错误行和排除行数量。

无法自动识别表头时不能只显示“将被跳过”；应显示表头行输入，用户指定后请求 preview 重新生成列和映射。

- [ ] **Step 3：实现全局默认值**

在确认区提供：

- `resume_allowed` 开关，默认开启；
- `priority` 1 到 5 的 select/stepper，默认 3。

不要再硬编码：

```typescript
defaults: { resume_allowed: true, priority: 3 }
```

- [ ] **Step 4：实现行级预览和排除**

预览表至少显示：

- Excel 行号；
- 生成/保留的 ID；
- 类型；
- 标题；
- 描述摘要；
- 状态和原因；
- 排除复选框。

要求：

- 支持 50 行分页；
- 支持只看错误行；
- 支持“排除当前页错误行”和“排除全部错误行”；
- mapping、header 或类型模式改变后重新请求服务端 preview；
- 使用 250ms 至 400ms debounce，旧请求用 `AbortController` 取消；
- API 请求期间保持稳定尺寸，不让布局跳动；
- 表格在窄屏横向滚动，页面本身不得横向溢出。

- [ ] **Step 5：修复类型 payload**

默认必须发送：

```typescript
type_override: null
```

只有用户明确选择统一类型时才发送具体类型。删除当前的：

```typescript
type_override: s.inferred_type || 'other'
```

- [ ] **Step 6：实现真实取消**

取消、重新选择文件或关闭当前导入状态时：

```typescript
await fetch(`/api/resume/materials/import/${importId}`, { method: 'DELETE' })
```

删除成功后再 reset。本地 reset 不得冒充服务器取消；删除失败时显示可重试错误。

- [ ] **Step 7：增加二次确认**

沿用项目现有 `window.confirm` 风格即可，不引入新 UI 库：

```typescript
const accepted = window.confirm(
  `确认用 ${validCount} 条素材替换当前正式素材库吗？已排除 ${excludedCount} 条，未排除错误行必须先处理。`,
)
if (!accepted) return
```

- [ ] **Step 8：修复完成摘要**

完成页使用后端结构化字段：

- `count`；
- `excluded_rows`；
- `warnings`；
- `sha256`；
- 可展开的排除/警告详情。

删除当前错误统计：

```typescript
excluded: (data.warnings || []).length
```

- [ ] **Step 9：构建前端**

```powershell
Set-Location src/openjob/web/frontend
npm run build
```

预期：TypeScript 与 Vite build 均成功。

- [ ] **Step 10：提交（仓库流程允许时）**

```powershell
git add src/openjob/web/frontend/src src/openjob/web/frontend/dist
git commit -m "feat: complete the material import correction workflow"
```

### Task 6：补文档和浏览器验收

**Files:**
- Modify: `README.md`
- Modify: `src/openjob/web/frontend/scripts/ui-acceptance.cjs`
- Regenerate: `src/openjob/web/frontend/dist/**`

- [ ] **Step 1：更新 README 素材库章节**

必须明确写出两条路径：

1. 新用户可下载标准模板填写后上传；
2. 已有自己的 `.xlsx` 时，可直接智能导入，再检查 Sheet、表头、映射、类型和错误行。

README 同时说明：

- `title` 和 `description` 是最终必需字段；
- `.xls` 暂不支持；
- 资料保存在本地 `data/`；
- 确认前不会替换正式库；
- 用户主动排除的行不会进入简历优化；
- 确认后的素材会参与 JD 匹配和简历优化；
- `source`、`notes` 不发送给 AI。

- [ ] **Step 2：为 UI 验收准备临时测试工作簿**

测试文件必须包含：

- 一个表头位于第 3 行的可见 Sheet；
- 一个隐藏 Sheet；
- 一个同时包含实习、奖项、学生工作的混合类别 Sheet；
- 至少一条缺标题或缺描述的坏行；
- 250 条以上的正常数据测试文件；
- 自定义中文列名。

测试数据只能是虚构数据，不使用用户真实简历。

- [ ] **Step 3：扩展 Puppeteer 验收脚本**

在 `ui-acceptance.cjs` 增加断言：

```text
PASS material-import-sheet-toggle
PASS material-import-hidden-sheet-can-include
PASS material-import-header-row-editable
PASS material-import-type-mode-editable
PASS material-import-invalid-row-visible
PASS material-import-row-exclusion
PASS material-import-second-confirmation
PASS material-import-cancel-cleans-session
PASS material-import-250-rows-not-truncated
PASS material-import-mobile-no-page-overflow
PASS material-import-dark-theme-readable
PASS material-import-no-console-errors
```

不要在脚本中读取真实 `config.yaml` 或真实 `data/`；启动临时 `BASE_DIR` 隔离验收数据。

- [ ] **Step 4：执行桌面和移动端验收**

视口至少覆盖：

```text
1280 × 900，light
1280 × 900，dark
390 × 844，light
390 × 844，dark
```

检查：

- 页面无横向溢出；
- Sheet 面板没有卡片嵌套导致的拥挤；
- 表格自身可横向滚动；
- 文案不遮挡按钮；
- loading 不改变控件尺寸；
- 低置信度使用 warning 色，不使用 danger 色；
- 键盘能操作 input/select/checkbox/button；
- 所有图标按钮有 `aria-label` 或 tooltip；
- 控制台无 error；
- API 无意外 500。

- [ ] **Step 5：运行最终构建**

```powershell
Set-Location src/openjob/web/frontend
npm run build
Set-Location ../../../..
```

- [ ] **Step 6：提交（仓库流程允许时）**

```powershell
git add README.md src/openjob/web/frontend/scripts/ui-acceptance.cjs src/openjob/web/frontend/dist
git commit -m "docs: document and verify universal xlsx material imports"
```

---

## 4. 最终测试矩阵

### 后端定向测试

```powershell
python -m pytest tests/test_resume_materials_import.py -q
python -m pytest tests/test_resume_materials.py tests/test_resume_materials_matching.py -q
python -m pytest tests/test_web_api_routes.py -q
python -m pytest tests/test_resume_engine.py -q
```

预期：全部通过。

### 完整测试

```powershell
python -m pytest -q
```

预期：不少于当前基线 522 个测试，全部通过。

### 前端构建

```powershell
Set-Location src/openjob/web/frontend
npm run build
```

预期：`tsc` 和 `vite build` 成功，无 TypeScript 错误。

### 静态检查

```powershell
Set-Location ../../../..
git diff --check
git status --short
```

预期：

- `git diff --check` 无 trailing whitespace 和 EOF 空白错误；
- `git status` 只包含本计划相关文件；
- 不得出现测试产生的真实素材、临时会话、数据库或个人简历文件。

---

## 5. 必须通过的验收场景

### 场景 A：其他 GitHub 用户的常见 Excel

1. 上传表头位于第 3 行的中文 `.xlsx`；
2. 系统自动分析，不修改正式库；
3. 用户修正表头行和字段映射；
4. 预览显示生成 ID、类型、标题和状态；
5. 二次确认后写入正式库；
6. 素材列表能检索到新素材；
7. JD 匹配候选能引用这些素材。

### 场景 B：混合类别 Sheet

1. 同一 Sheet 放入实习、奖项、学生工作；
2. 类型模式保持“使用每行类别”；
3. 预览分别显示 `experience`、`award`、`student_work`；
4. 确认后正式索引仍保持三种类型，不得全部变成 `other` 或同一类型。

### 场景 C：坏行不静默丢失

1. Excel 中加入缺标题和缺描述的行；
2. 预览明确显示真实 Excel 行号和错误原因；
3. 未排除时 confirm 返回 422，正式库保持不变；
4. 用户主动排除后 confirm 成功；
5. 完成页显示准确排除数量和原因。

### 场景 D：250 行完整导入

1. 上传 250 条有效素材；
2. 分析显示 250 条；
3. 分页能访问后续行；
4. 确认后正式库 count 为 250；
5. 不出现 199 行截断。

### 场景 E：完整性和隐私

1. 分析后替换暂存 `source.xlsx`；
2. confirm 必须因 hash 不一致拒绝；
3. 不传 hash 也必须拒绝；
4. analyze 响应不包含 `staged_path`；
5. 正式库和旧索引保持不变。

### 场景 F：取消和过期

1. 分析后点击取消；
2. DELETE API 成功；
3. 暂存目录被删除；
4. status 返回 `import_in_progress=false`；
5. 构造超过 24 小时的会话，调用 status 后自动清理；
6. 正式素材库不受影响。

---

## 6. 禁止事项

- 禁止继续使用 `max_row=200` 或任何无提示截断；
- 禁止让前端默认发送推断类型作为 `type_override`；
- 禁止只比较客户端 hash 和 `analysis.json` hash；
- 禁止让 `source_sha256` 变成可选；
- 禁止把 `staged_path` 返回浏览器；
- 禁止把 warning 数量当作排除行数量；
- 禁止确认时自动丢弃用户未明确排除的坏行；
- 禁止使用字符串拼接或字符串前缀判断递归删除边界；
- 禁止手工编辑 `frontend/dist` 压缩文件；
- 禁止为了本功能放宽 AI 素材数量、隐私过滤或人工审核限制；
- 禁止把用户真实简历、测试 XLSX、`config.yaml` 密钥、`data/` 数据提交到 Git；
- 禁止顺手进行无关 UI 重构或全仓格式化。

---

## 7. Agent 最终交付格式

完成后必须输出：

1. 修改文件清单；
2. 每个 P1/P2 验收问题对应的修复说明；
3. 新增测试名称与结果；
4. 完整测试的实际通过数量；
5. 前端 build 结果；
6. 四个视口的浏览器验收结果与截图路径；
7. 250 行导入的实际 count；
8. 混合类型导入的实际类型结果；
9. hash 篡改、缺少 hash、取消和 TTL 的实际 API 结果；
10. 尚未实现的边界能力，尤其是 `.xls`、复杂合并/多层表头，不得把它们描述成已支持。

只有当上述证据全部提供，才可以声明“任意常见 `.xlsx` 智能导入修复完成”。
