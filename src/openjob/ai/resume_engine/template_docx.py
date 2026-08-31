"""模板渲染 v9「只换正文」：结构化槽位写入，固定区永不触碰。

底稿 Markdown 与模板 docx 由提取器（resume_upload.docx_to_markdown）互逆生成：
- body 顶层段落 → 一行
- 表格行（跳过空格）→ " | " 连接的一行

针对 030 蓝标题底稿结构的固定区/可变区划分：
- 固定区（定制稿写了什么都不写入）：
  * 第一个表格（信息表：姓名/电话/邮箱/地址 + 照片）的全部格子
  * 单行单格表格中的栏目名称行（"教育经历""实习经历"…；"求职意向：…"除外）
  * bullet 行的 "•" 圆点格
- 可变区（只写这些）：
  * bullet 行的正文格
  * 项目行 / 教育行等多格正文行
  * "求职意向：…" 单格行（可按 JD 调整岗位方向）

渲染 = 行数一致时位置一一对应（上游 apply_changes 保证结构不变）；
行数波动时 difflib 兜底对齐，对不上的固定行保持原样。
全程不增删表格/行/段落 → 版式与页数天然稳定。
"""

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from docx import Document

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = "{%s}" % W_NS


def _normalize(text: str) -> str:
    s = str(text or "")
    for ch in ("•", " ", "　"):
        s = s.replace(ch, "")
    return s.lower()


def _element_lines(element) -> list[str]:
    """与 resume_upload._element_text 相同语义，但按换行拆成多行。"""
    parts: list[str] = []
    for node in element.iter():
        tag = node.tag.split("}")[-1]
        if tag == "t":
            parts.append(node.text or "")
        elif tag == "tab":
            parts.append(" ")
        elif tag in ("br", "cr"):
            parts.append("\n")
    import re

    joined = re.sub("[ \t]+", " ", "".join(parts)).strip()
    return joined.split("\n") if "\n" in joined else [joined]


def _set_para_text(p, text: str) -> None:
    """清空段落文字按单行新文字重写，继承首个带文字 run 的样式。"""
    runs = p.findall(W + "r")
    donor = None
    for run in runs:
        if run.findall(W + "t"):
            donor = run
            break
    for run in runs:
        p.remove(run)
    if donor is None:
        donor = p.makeelement(W + "r", {})
    r = donor
    for child in list(r):
        if child.tag.split("}")[-1] in ("t", "br"):
            r.remove(child)
    t = r.find(W + "t")
    if t is None:
        t = r.makeelement(W + "t", {})
        r.append(t)
    t.text = text
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    p.append(r)


@dataclass
class _Row:
    """模板中一个"提取行"：对应表格一行（或顶层段落），按非空格组织。"""

    cells: list[list[object]] = field(default_factory=list)  # 非空格的段落列表
    texts: list[str] = field(default_factory=list)  # 非空格文本
    fixed: bool = False  # 整行固定（信息表/栏目标题/顶层段落）
    fixed_cells: set[int] = field(default_factory=set)  # 行内固定格（"•" 圆点格）


def _cell_text(paras) -> str:
    lines: list[str] = []
    for p in paras:
        lines.extend(_element_lines(p))
    return " / ".join(t for t in lines if t.strip())


def _build_rows(doc) -> list[_Row]:
    """与提取器同构地建立模板行序列，并按结构特征标注固定区。"""
    rows: list[_Row] = []
    body = doc.element.body
    table_idx = -1
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag == "p":
            texts = [t for t in _element_lines(child) if t.strip()]
            if texts:
                rows.append(_Row(cells=[[child]], texts=texts, fixed=True))
        elif tag == "tbl":
            table_idx += 1
            is_info_table = table_idx == 0
            trs = child.findall(W + "tr")
            for tr in trs:
                visible: list[tuple[list[object], str]] = []
                for tc in tr.findall(W + "tc"):
                    paras = list(tc.findall(W + "p"))
                    text = _cell_text(paras)
                    if text.strip():
                        visible.append((paras, text))
                if not visible:
                    continue
                row = _Row(
                    cells=[paras for paras, _ in visible],
                    texts=[text for _, text in visible],
                )
                if is_info_table:
                    row.fixed = True
                elif len(trs) == 1 and len(visible) == 1:
                    # 单行单格：栏目标题固定；求职意向行允许按 JD 调整方向
                    row.fixed = not visible[0][1].startswith("求职意向")
                else:
                    for i, text in enumerate(row.texts):
                        if text.strip() == "•":
                            row.fixed_cells.add(i)
                rows.append(row)
    return rows


def _write_cell(paras, text: str) -> None:
    """写入 cell：新文字进第一个有文字的段落（无则首段），其余段落清空。"""
    target = None
    for p in paras:
        if "".join(node.text or "" for node in p.findall(".//" + W + "t")).strip():
            target = p
            break
    if target is None:
        target = paras[0]
    _set_para_text(target, text)
    for p in paras:
        if p is not target:
            _set_para_text(p, "")


def render_tailored_docx(template_path: Path, tailored_md: str, out_path: Path) -> Path:
    """把定制稿写回模板：固定区零接触，可变区只换文字，结构不增删。"""
    doc = Document(str(template_path))
    rows = _build_rows(doc)

    t_lines = [line.strip() for line in tailored_md.splitlines() if line.strip()]
    t_lines = [line.replace("\n", " | ") if "\n" in line else line for line in t_lines]

    def _apply_row(row: _Row, line: str) -> None:
        if row.fixed:
            return
        segs = [s.strip() for s in line.split(" | ")]
        if row.fixed_cells and segs and segs[0].strip() == "•":
            segs = segs[1:]
        writable = [paras for i, paras in enumerate(row.cells) if i not in row.fixed_cells]
        for paras, text in zip(writable, segs):
            if text:
                _write_cell(paras, text)

    if len(t_lines) == len(rows):
        for row, line in zip(rows, t_lines):
            _apply_row(row, line)
    else:
        # 兜底：行数波动时 difflib 对齐，模板独有行保持原样
        matcher = SequenceMatcher(
            None,
            [_normalize(line) for line in t_lines],
            [_normalize(" | ".join(row.texts)) for row in rows],
            autojunk=False,
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("equal", "replace"):
                pair = min(i2 - i1, j2 - j1)
                for k in range(pair):
                    _apply_row(rows[j1 + k], t_lines[i1 + k])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
