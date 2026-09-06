# OpenJob 简历素材库加固修复计划

> **给执行 Agent：** 请直接按本计划修复已有功能。目标是加固边界，不重写主流程、不删除真实素材、不改变人工审阅和人工投递边界。完成后执行文末全部测试并报告结果。

## 1. 本次修复目标

验收已经确认素材库主流程可用，但存在以下缺口：

1. 配置/API 支持 `profile.resume_materials_path`，简历生成引擎却固定读取默认文件，可能出现数据源不一致。
2. Prompt 要求 AI 最多选择 4 条素材，但服务端没有硬限制。
3. 计划要求最多 3 个简历变量行使用新增素材，但服务端没有硬限制。
4. 用户直接修改 XLSX 后，状态 API 仍可能显示旧索引有效，不提示刷新。
5. 启用素材库但尚未上传时，当前实际会兼容旧流程，需要把这个行为明确写进 UI。
6. `scripts/convert_user_materials.py` 使用当前电脑的硬编码路径，不可复用。

只修复上述问题及必要测试，不做无关重构。

## 2. 固定产品决策

### 2.1 未上传时保持兼容旧流程

保留当前行为：`resume_materials_enabled=true` 但素材库 XLSX 不存在时，不阻断旧的简历生成流程，继续只使用底稿原文；生成记录中的素材审计字段必须保持为空，不能伪装成使用了素材库。

如果 XLSX 文件存在但解析失败、索引损坏且无法从当前 XLSX 重建，必须明确失败，不能静默使用旧索引。

同步修改配置页文案，明确显示：

> 素材库未上传时，定制简历会继续使用底稿原文，不会使用额外素材。上传后才会进行素材匹配和审计。

### 2.2 素材路径只能位于项目 data 目录

允许默认路径 `./data/resume_materials.xlsx`，也允许项目 `data/` 内的自定义文件名或子目录；禁止读取、写入或删除 `data/` 之外的路径。越界路径必须返回清晰错误，不能静默切换到另一个文件。

---

## 3. Task 1：统一 API 与 engine 的素材路径

### 3.1 新增公共解析函数

在：

```text
src/openjob/resume_materials.py
```

新增公共函数（名称可保持一致）：

```python
def resolve_library_paths(config: dict, data_dir: Path) -> tuple[Path, Path]:
    """解析并校验素材 XLSX 与索引路径，二者必须位于 data_dir 内。"""
```

实现要求：

- 从 `config["profile"]["resume_materials_path"]` 读取配置；
- 未配置时使用 `./data/resume_materials.xlsx`；
- 相对路径按照 `data_dir.parent`（项目根目录）解析，不按照当前进程工作目录解析；
- 绝对路径也允许输入，但最终必须落在 `data_dir.resolve()` 内；
- 索引路径由 XLSX 路径推导为 `<stem>.index.json`；
- 使用 `Path.resolve()` + `Path.relative_to()` 做目录边界检查，不使用简单字符串前缀；
- 越界时抛出清晰异常，例如：`简历素材库路径必须位于项目 data 目录内`。

### 3.2 替换所有调用

以下位置必须使用同一公共函数：

- `src/openjob/web/server.py` 的 `_materials_paths()`；
- `src/openjob/ai/resume_engine/engine.py` 素材加载阶段；
- 刷新接口；
- 删除接口。

删除时再次确认 XLSX 和 index 均位于当前 `DATA_DIR` 内。`set_base_dir()` 后 API 与 engine 必须指向同一个临时 `data/`。

### 3.3 必须新增的路径测试

- 默认配置下 API 与 engine 指向同一文件；
- `./data/custom-materials.xlsx` 下 API 与 engine 都使用自定义文件；
- 自定义索引名称为 `custom-materials.index.json`；
- `../outside.xlsx` 被拒绝；
- Windows 绝对路径越界被拒绝；
- 删除接口不能删除 `data/` 之外的文件。

---

## 4. Task 2：增加服务端选择数量硬限制

在：

```text
src/openjob/ai/resume_engine/materials.py
```

定义：

```python
MAX_SELECTED_MATERIALS = 4
MAX_MATERIAL_BACKED_CHANGES = 3
```

### 4.1 素材总数限制

更新 `validate_material_ids()` 或新增专用校验函数：

- 先去空白、去重；
- 仍然校验每个 ID 必须属于本次候选集；
- 去重后超过 4 条时抛出 `ValueError`；
- 不要静默截断；
- 错误信息明确，例如：`本次改写最多只能引用 4 条素材`。

### 4.2 使用素材的改写块限制

engine 在所有 `rewrite.changes` 完成 ID 校验后，统计 `material_ids` 非空的 `SectionChange` 数量。超过 3 个时生成失败，错误信息为：

```text
本次改写最多只能让 3 个简历变量行使用新增素材
```

统计单位是改写块/简历变量行，不是素材 ID 数量。

### 4.3 PATCH 也必须限制

修改：

```text
src/openjob/web/server.py
```

`PATCH /api/resume/version/<resume_id>` 必须继续只允许引用该版本 `material_selection_json` 中的 ID，并且：

- 完整 diff 中不同素材总数最多 4 条；
- 有素材 ID 的 diff 项最多 3 个；
- 超限时返回 400；
- 失败时数据库中的旧 diff、正文和状态保持不变。

旧版本没有素材审计字段时，仍允许编辑旧 diff，但不能借 PATCH 新增未经审计的素材 ID。

### 4.4 必须新增的数量测试

- 4 条合法 ID通过；
- 5 条合法候选 ID被拒绝；
- 重复后恰好 4 条通过，去重后 5 条拒绝；
- 4 个改写块各引用 1 条素材时失败；
- 3 个改写块共引用 4 条素材时通过；
- 非候选 ID仍被拒绝；
- PATCH 超限时数据库不发生变化。

---

## 5. Task 3：检测 XLSX 与索引是否过期

### 5.1 后端状态 API

修改：

```text
src/openjob/web/server.py
```

`GET /api/resume/materials/status` 在 XLSX 和 index 都存在且 index 可读时，计算当前 XLSX 的 SHA-256，并返回以下新增字段：

```json
{
  "valid": true,
  "stale": true,
  "filename": "resume_materials.xlsx",
  "count": 21,
  "sha256": "索引中的哈希前12位",
  "source_sha256": "当前XLSX哈希前12位",
  "updated_at": "...",
  "errors": []
}
```

规则：

- 文件和 index 都不存在：`valid=false`、`stale=false`、`errors=[]`；
- index 损坏：`valid=false`，返回明确错误；
- 文件存在、index 存在且哈希一致：`valid=true`、`stale=false`；
- 文件存在、index 存在但哈希不一致：`valid=true`、`stale=true`；
- 状态接口不返回素材完整正文；
- 普通列表接口继续只读 index，不隐式修改文件；
- 简历生成继续通过 `load_or_refresh_library()` 自动检测并重建 index。

### 5.2 前端状态提示

修改：

```text
src/openjob/web/frontend/src/components/config/ResumeMaterials.tsx
```

要求：

- `StatusPayload` 增加 `stale?: boolean` 和 `source_sha256?: string`；
- `stale=true` 时显示：`检测到 XLSX 源文件已更新，当前预览是旧索引，请点击“刷新索引”。`；
- 刷新按钮在 stale 时保持可用，并有适度视觉提示；
- 刷新成功后重新拉取状态和列表；
- 刷新失败保留原素材库和原列表，不伪装成已同步；
- 状态行区分“已同步 / 索引过期 / 解析失败 / 未上传”。

### 5.3 必须新增的过期测试

- 合法上传后 `stale=false`；
- 只替换 XLSX、不替换 index 后 `stale=true`；
- refresh 后 `stale=false` 且条数/hash 更新；
- 外部修改成非法 XLSX 后 refresh 返回 400，原合法数据不被破坏；
- 前端 TypeScript 构建通过。

---

## 6. Task 4：同步 UI 文案

不改变“未上传时兼容旧流程”的行为，只让用户看懂开关和状态。

素材库区域应明确说明：

```text
启用后，生成定制简历时会根据 JD 匹配本地素材并记录引用来源。
素材库未上传时，系统会继续使用底稿原文，不会阻断生成。
关闭后，生成流程只使用简历底稿。
素材只保存在本机，不会自动发送。
```

检查浅色/深色模式以及窄屏换行，不得出现文字溢出或按钮被挤出容器。

---

## 7. Task 5：把转换脚本改为可复用 CLI

修改：

```text
scripts/convert_user_materials.py
```

删除当前电脑专用的硬编码 `SRC` 和 `OUT`，改用 `argparse`：

```bash
python scripts/convert_user_materials.py \
  --input "C:\path\简历素材库.xlsx" \
  --output "data\resume_materials.xlsx"
```

参数：

- `--input`：必填；
- `--output`：必填；
- `--force`：目标已存在时允许覆盖，默认拒绝覆盖；
- `--dry-run`：只解析并输出统计，不写文件。

保持三张中文工作表兼容，并增加明确错误：

- 缺少 `📋 经历`、`🏆 奖项`、`📜 证明` 时指出缺失表；
- 缺少表头时指出具体列名；
- 输出前检查 ID 唯一、必填字段、日期、文本长度、类型和 `resume_allowed`；
- 缺少描述时至少在命令行统计并警告，推荐直接失败要求用户补齐事实；
- 不修改源文件；
- 失败退出码非 0；
- 输出先写临时文件，成功后再替换目标。

必须测试：dry-run 不写文件、默认不覆盖、force 可覆盖、缺表失败、重复 ID 失败、中文空格路径正常、输出可被 `parse_workbook()` 重新解析。

---

## 8. 数据质量检查（只读，不擅自修改）

对当前 `data/resume_materials.xlsx` 做一次只读检查并报告：

- 是否存在“详见成果”等自动兜底文本；
- `description` 与 `achievements` 是否大量重复；
- 技能中的括号、顿号、斜杠是否被错误拆分；
- 项目、经历、奖项、证书分类是否准确；
- 时间、数字、组织和角色是否准确；
- `resume_allowed`、`priority` 是否合理。

发现个人素材内容问题时，只列为“需用户确认”，不要把内容硬编码进代码，也不要自动删改原始素材。

---

## 9. 兼容性与安全验收

修复后必须确认：

- 关闭素材库开关时旧简历生成行为不变；
- 未上传素材库时生成可以兼容完成，且没有虚假素材审计；
- `source`、`notes` 不进入素材 API 预览和 AI prompt；
- 原始 XLSX、index 和简历版本素材快照只在本地 data 目录；
- 不新增自动投递、自动发送、自动回复或绕过人工确认的入口；
- 非法上传不会覆盖已有合法库；
- 删除接口不能越界删除文件；
- 真实素材、个人简历和生成结果不加入 Git。

## 10. 执行顺序

1. 先补失败测试：路径、数量、stale；
2. 实现公共路径解析并替换 API/engine/刷新/删除调用；
3. 实现 4 条素材和 3 个改写块的服务端硬限制；
4. 实现状态 hash 检测和前端 stale 提示；
5. 同步未上传状态文案；
6. 转换脚本 CLI 化并测试；
7. 跑全量测试和前端构建；
8. 做浏览器验收和敏感数据检查。

## 11. 验收命令

项目根目录执行：

```bash
python -m pytest -q
```

预期全部通过，不允许删除、跳过或放宽既有测试。

```bash
cd src/openjob/web/frontend
npm run build
```

预期 TypeScript 和 Vite 构建通过。

```bash
rg -n "resume_materials_path|resolve_library_paths|MAX_SELECTED_MATERIALS|MAX_MATERIAL_BACKED_CHANGES|stale|source_sha256|material_ids|source|notes" src tests README.md scripts
```

```bash
git status --short
git diff --check
```

浏览器访问 `http://127.0.0.1:8686/config`，确认：

1. 合法库显示“已同步”、文件名、条数、更新时间和 hash；
2. 外部修改 XLSX 后显示“索引过期”，并提示刷新；
3. 刷新后 hash/条数更新并恢复“已同步”；
4. 非法上传显示错误且旧库保留；
5. 未上传、已关闭两种状态文案清楚；
6. 预览不显示 `source`、`notes`；
7. 浅色/深色/窄屏无溢出；
8. 简历工作台的素材审计仍可查看，超限 PATCH 被拒绝；
9. 投递/发送仍需人工确认。

## 12. 完成报告格式

```text
## 实施结果
- 修改文件：...
- 新增测试：...
- 是否修改真实素材数据：是/否；如是说明原因

## 验证结果
- python -m pytest -q：...
- npm run build：...
- 浏览器验收：...

## 已修复问题
- [x] API/engine 素材路径统一
- [x] 服务端最多 4 条素材
- [x] 最多 3 个使用素材的改写块
- [x] stale 索引检测与 UI 提示
- [x] 未上传状态文案
- [x] 转换脚本 CLI 化

## 未解决或需用户确认
- ...
```
