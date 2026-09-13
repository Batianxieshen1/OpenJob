"""多简历底稿：按岗位自动挑选最接近的底稿。

选稿策略（确定性，不烧 AI）：
1. 只有 0/1 份底稿 → 直接用（0 份返回安全阻断，不读取示例文件）；
2. 多份 → 方向标签/名称命中岗位文本优先；否则比较“简历文本 vs 岗位 JD 文本”
   的字符 bigram 重叠度（Jaccard），取最高；
3. 平手/都低 → is_default 的那份；再没有就用最新一份。
"""

import re
from dataclasses import dataclass

import sqlite3

from openjob.ai.fact_policy import TEMPLATE_RESUME_MARKERS

_PUNCT = re.compile(r"[\s\d\W]+", re.UNICODE)


@dataclass
class BaseSelection:
    base: dict | None  # 选中的真实底稿行（含 content_md）；None = 未配置真实底稿
    reason: str  # 人类可读的选稿理由，工作台展示用


def _bigrams(text: str) -> set[str]:
    cleaned = _PUNCT.sub("", text.lower())
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)} if len(cleaned) > 1 else {cleaned}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _is_repository_example_path(path) -> bool:
    """Only reject the repository's example files, not a user upload named resume.md."""
    from pathlib import Path

    try:
        resolved = Path(path).resolve()
        project_root = Path(__file__).resolve().parents[3]
        return resolved in {project_root / "resume.md", project_root / "resume.example.md"}
    except (OSError, RuntimeError, ValueError):
        return False


def _fallback_base(config: dict) -> tuple[str, str]:
    """读取用户明确上传的非模板简历；永不读取项目示例文件。"""
    from pathlib import Path

    raw = str((config.get("profile") or {}).get("resume_path") or "").strip()
    if not raw:
        raise RuntimeError("未配置真实简历底稿：请在配置页上传至少一份真实底稿")
    path = Path(raw)
    if _is_repository_example_path(path):
        raise RuntimeError("已拒绝项目示例简历 resume.md/resume.example.md，必须上传真实简历底稿")
    if not path.exists():
        raise RuntimeError(f"真实简历底稿不存在：{path}")
    text = path.read_text(encoding="utf-8")
    if any(marker in text for marker in TEMPLATE_RESUME_MARKERS):
        raise RuntimeError("简历包含示例/占位信息，已拒绝作为事实底稿")
    return text, f"用户上传的真实简历文件 {path.name}"


def select_base_for_job(
    conn: sqlite3.Connection,
    job_text: str,
    config: dict,
) -> BaseSelection:
    """为岗位挑选最接近的底稿；job_text = 岗位标题 + JD。"""
    rows = conn.execute(
        "SELECT id, name, direction, content_md, is_default, created_at FROM base_resumes ORDER BY created_at DESC"
    ).fetchall()
    bases = [dict(r) for r in rows]

    if not bases:
        try:
            text, source = _fallback_base(config)
        except RuntimeError as exc:
            return BaseSelection(base=None, reason=str(exc))
        return BaseSelection(base={"id": None, "name": "用户上传底稿", "content_md": text}, reason=source)

    if len(bases) == 1:
        only = bases[0]
        return BaseSelection(base=only, reason=f"唯一底稿「{only['name']}」")

    job_bigrams = _bigrams(job_text)
    job_lower = job_text.lower()

    scored: list[tuple[float, float, dict, str]] = []
    for base in bases:
        direction_hit = 0.0
        # 方向标签与名称分别比对：岗位文本包含任一即算命中
        label_parts = [
            str(base.get("direction") or "").strip().lower(),
            str(base.get("name") or "").strip().lower(),
        ]
        if any(part and part in job_lower for part in label_parts):
            direction_hit = 1.0
        overlap = _jaccard(_bigrams(base["content_md"]), job_bigrams)
        reason = f"方向标签命中" if direction_hit else f"文本重叠度 {overlap:.2f}"
        scored.append((direction_hit, overlap, base, reason))

    # 方向命中优先；再按重叠度；再按 is_default；再按最新
    scored.sort(key=lambda item: (item[0], item[1], item[2].get("is_default") or 0), reverse=True)
    best_hit, best_overlap, best, best_reason = scored[0]
    if best_hit == 0 and best_overlap < 0.05:
        defaults = [b for b in bases if b.get("is_default")]
        chosen = defaults[0] if defaults else bases[0]
        return BaseSelection(base=chosen, reason=f"各底稿匹配度均低，使用默认「{chosen['name']}」")
    return BaseSelection(base=best, reason=f"按{best_reason}选中「{best['name']}」")
