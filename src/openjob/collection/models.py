"""Data contracts shared by all job collection platforms."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any, Literal

from openjob.collection.text import clean_job_description


PlatformId = Literal["boss", "zhilian", "51job"]


def classify_recruitment_type(title: str = "", experience: str = "", jd: str = "", salary: str = "") -> str:
    """Classify jobs the way job seekers read them: 实习 vs 正式岗.

    判断优先级（筛选语义是“只有实习 / 只有正式岗”）：
    1. 标题含“实习” → 实习（最强信号）；
    2. 薪资为日薪格式（如 200-250元/天）→ 实习；
    3. 薪资为月薪格式（K/千/万）或明确 社招/全职/管培生/经验年限 → 正式岗
       （面向应届/校招的正式岗同样归这里，不只看“应届”字样）；
    4. 无薪资信号时，校招/应届/毕业生/实习生 字样 → 实习（学生轨保守归实习侧）；
    5. 其余 → unknown。
    """
    title_text = str(title or "")
    text = " ".join(str(value or "") for value in (title, experience, jd, salary))
    if "实习" in title_text:
        return "campus"
    if re.search(r"\d+\s*(?:[-–~至]\s*\d+\s*)?元\s*/\s*天", text):
        return "campus"
    if re.search(r"\d+(?:\.\d+)?\s*[kK](?!\w)", text) or "千元" in text or re.search(r"\d+(?:\.\d+)?\s*万", text):
        return "experienced"
    if any(marker in text for marker in ("社招", "社会招聘", "全职", "管培生")):
        return "experienced"
    if re.search(r"\d+\s*(?:[-–~至]\s*\d+\s*)?年(?:以上|及以上)?(?:工作)?经验", text):
        return "experienced"
    if re.fullmatch(r"\s*\d+\s*(?:[-–~至]\s*\d+\s*)?年(?:以上|及以上)?\s*", str(experience or "")):
        return "experienced"
    if any(marker in text for marker in ("校招", "校园招聘", "应届", "毕业生", "实习")):
        return "campus"
    return "unknown"


@dataclass(frozen=True)
class PlatformCollectionRequest:
    platform: PlatformId
    keywords: list[str]
    cities: list[str]
    city_codes: dict[str, str]
    max_pages: int = 3
    sort: str = "default"
    recruitment_filter: str = ""


@dataclass
class JobCandidate:
    """A platform-neutral job candidate emitted by a collector."""

    platform: PlatformId
    source_job_id: str
    title: str
    company: str
    salary: str = ""
    city: str = ""
    city_code: str = ""
    experience: str = ""
    education: str = ""
    recruitment_type: str = "unknown"
    jd: str = ""
    hr_name: str = ""
    hr_title: str = ""
    hr_active: str = ""
    company_size: str = ""
    company_industry: str = ""
    url: str = ""
    source_keyword: str = ""

    @property
    def storage_id(self) -> str:
        if self.platform != "boss":
            return f"{self.platform}:{self.source_job_id}"
        return self.source_job_id

    def as_job_record(self) -> dict[str, Any]:
        return {
            "id": self.storage_id,
            "title": self.title,
            "company": self.company,
            "salary": self.salary,
            "city": self.city,
            "source_city_code": self.city_code,
            "experience": self.experience,
            "education": self.education,
            "recruitment_type": classify_recruitment_type(self.title, self.experience, self.jd, self.salary),
            "jd": clean_job_description(self.jd),
            "hr_name": self.hr_name,
            "hr_title": self.hr_title,
            "hr_active": self.hr_active,
            "company_size": self.company_size,
            "company_industry": self.company_industry,
            "url": self.url,
            "source_platform": self.platform,
            "source_job_id": self.source_job_id,
            "source_keyword": self.source_keyword,
        }


@dataclass
class CollectionProgress:
    run_id: str
    platform: PlatformId
    platform_index: int
    platform_total: int
    phase: str
    target: int | None
    seen: int = 0
    new: int = 0
    duplicate: int = 0
    filtered: int = 0
    parse_failed: int = 0
    save_failed: int = 0
    keyword: str = ""
    city: str = ""
    page: int = 0
    max_pages: int = 0
    reason_code: str = ""
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def percent(self) -> int | None:
        if self.target is None or self.target <= 0:
            return None
        return min(100, int(self.new * 100 / self.target))


@dataclass
class PlatformCollectionResult:
    platform: PlatformId
    status: str
    reason_code: str = ""
    message: str = ""
    new_job_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    error: str = ""
