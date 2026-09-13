"""Isolated browser smoke test for the resume-material XLSX import wizard.

Run through the webapp-testing with_server helper; the service itself receives
the same temporary runtime path defined in QA_RUNTIME.
"""

from __future__ import annotations

import os
from pathlib import Path

from openpyxl import Workbook
from playwright.sync_api import sync_playwright


QA_RUNTIME = Path(os.environ["OPENJOB_QA_RUNTIME"])
FIXTURE = QA_RUNTIME / "arbitrary-materials.xlsx"


def create_fixture() -> None:
    QA_RUNTIME.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "我的经历"
    sheet.append(["这是说明文字，不是表头"])
    sheet.append([])
    sheet.append(["类别", "名称", "工作内容", "备注"])
    sheet.append(["实习经历", "产品实习生", "负责用户研究与产品迭代，输出完整分析报告。", "仅供自己核对"])
    sheet.append(["奖项", "全国竞赛一等奖", "在全国大学生竞赛中获得一等奖，负责核心方案设计。", "奖状编号 01"])
    sheet.append(["学生工作", "学生会部长", "组织校园活动并协调二十名志愿者，完成活动复盘。", "任期记录"])
    sheet.append(["实习经历", "缺少描述的记录", "", "此行应显示错误"])

    hidden = workbook.create_sheet("隐藏经历")
    hidden.append(["类别", "名称", "工作内容"])
    hidden.append(["项目经历", "隐藏项目", "完成一个可验证的项目交付并整理项目成果。"])
    hidden.sheet_state = "hidden"
    workbook.save(FIXTURE)


def main() -> None:
    create_fixture()
    console_errors: list[str] = []
    material_responses: list[tuple[int, str]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("response", lambda response: material_responses.append((response.status, response.url)) if "/materials/" in response.url else None)
        page.goto("http://127.0.0.1:8765/config")
        page.wait_for_load_state("networkidle")

        # Config page also has a standard-template uploader. The first XLSX
        # input belongs to the smart-import wizard under test.
        upload = page.locator('input[type="file"][accept=".xlsx"]').first
        upload.set_input_files(str(FIXTURE))
        page.get_by_text("我的经历", exact=True).wait_for(timeout=15000)
        try:
            page.get_by_text("3 有效 · 1 错误 · 0 已排除", exact=True).wait_for(timeout=15000)
        except Exception:
            print("material responses:", material_responses)
            print("page text:", page.locator("body").inner_text().encode("ascii", "backslashreplace").decode())
            raise

        assert page.get_by_text("产品实习生", exact=True).is_visible()
        assert page.get_by_text("全国竞赛一等奖", exact=True).is_visible()
        assert page.get_by_text("student_work", exact=True).is_visible()
        assert page.get_by_text("缺少事实描述", exact=True).is_visible()
        assert page.get_by_role("cell", name="4", exact=True).is_visible(), "预览必须显示真实 Excel 行号"

        # Hidden sheets are initially skipped but may be explicitly enabled.
        hidden_card = page.locator("section").filter(has_text="隐藏经历")
        hidden_switch = hidden_card.get_by_role("switch")
        assert hidden_switch.get_attribute("aria-checked") == "false", hidden_switch.evaluate("node => node.outerHTML")
        hidden_switch.click()
        page.get_by_text("隐藏项目", exact=True).wait_for(timeout=15000)

        # Fix the invalid current record through the row-level exclusion control.
        page.get_by_label("排除第 7 行").check()
        page.get_by_text("3 有效 · 0 错误 · 1 已排除", exact=True).wait_for(timeout=15000)
        hidden_card.get_by_text("1 有效 · 0 错误 · 0 已排除", exact=True).wait_for(timeout=15000)

        # Mobile viewport must not create document-level horizontal scrolling.
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(300)
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        assert not overflow, "窄屏出现页面级水平滚动；表格应仅在自身容器横向滚动"
        page.screenshot(path=str(QA_RUNTIME / "material-import-mobile.png"), full_page=True)

        # Cancel must delete the staged session and return the wizard to idle.
        page.set_viewport_size({"width": 1280, "height": 900})
        page.get_by_role("button", name="取消", exact=True).click()
        page.get_by_role("button", name="导入自己的 Excel（智能识别）", exact=True).wait_for(timeout=10000)
        status = page.request.get("http://127.0.0.1:8765/api/resume/materials/status")
        assert status.ok
        assert status.json().get("import_in_progress") is False
        assert not console_errors, f"浏览器控制台错误：{console_errors}"
        browser.close()

    print(f"browser QA passed; screenshot: {QA_RUNTIME / 'material-import-mobile.png'}")


if __name__ == "__main__":
    main()
