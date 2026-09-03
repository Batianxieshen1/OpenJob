# OpenJob UI and Brand Integration - Final Acceptance and Rework Plan

> 中文执行摘要：本轮验收结论为“有条件通过”。现有品牌接入、桌面视觉、明暗主题和移动端岗位卡片方向均应保留；只需完成下列 5 项定向返工。P1 为外发消息去重测试回归及 390px 简历页主按钮裁切；P2 为移动岗位卡缺少“查看详情”以及简历/监测空状态行动项不完整；P3 为移动端顶部导航文字被截断。修复并满足本文复验清单后方可最终签收。

> Acceptance date: 2026-09-03  
> Reviewed revision: `main` at `6d61b35`  
> Test URL: `http://127.0.0.1:8686`  
> Decision: **Conditionally accepted. Complete the focused rework below before final sign-off or release.**

## Instructions for the implementation agent

The visual refresh and OpenJob brand integration are largely complete. Do not redesign the accepted UI or perform a broad refactor. Fix only the five issues below, preserve the current desktop design, responsive job cards, light/dark themes, and brand color system, and add targeted regression coverage.

After the work, report changed files, before/after screenshots at 390px, 1024px, and 1280px, and complete outputs from the build, UI acceptance script, and Python tests. Do not delete or skip tests and do not weaken assertions to make the suite pass.

## Acceptance summary

| Area | Result | Notes |
|---|---|---|
| OpenJob brand assets | Pass | Sidebar icon, favicon, and horizontal logos are integrated |
| Light and dark themes | Pass | Logo source switches with theme; dark surfaces are coherent |
| Seven core desktop pages | Pass | Pages load without browser console errors or warnings |
| Mobile job pool | Partial pass | Card layout works, but details action is missing |
| Mobile resume workspace | Fail | Primary action is clipped at 390px |
| Empty-state guidance | Partial pass | Secondary actions and logo treatment are incomplete |
| Frontend build | Pass | TypeScript and Vite build succeeded |
| UI acceptance script | Pass with gaps | `ALL-PASS`, but local overflow and action omissions are uncovered |
| Python suite | Fail | 465 passed, 1 repeatable failure |

Estimated readiness: **87/100**.

## Required rework

### ISSUE-001 - P1 / High: outgoing-message deduplication test fails consistently

Full-suite result: `465 passed, 1 failed in 327.75s`.

Failing test:

```text
tests/test_send_dedup_guard.py::SendGuardTests::test_send_greeting_once_skips_when_outgoing_exists
```

Failure:

```text
self.assertTrue(result.get("success"))
AssertionError: False is not true
```

The test also fails when rerun alone. This covers a sending-safety path and blocks release even though the main scope was UI.

Required implementation:

- Find why `send_greeting_once` reports failure when an outgoing message already exists.
- Preserve the contract: detecting an existing outgoing message skips the duplicate send and returns a successful result.
- Do not remove/skip the test or loosen its assertion.

Acceptance:

```powershell
python -m pytest -q tests/test_send_dedup_guard.py::SendGuardTests::test_send_greeting_once_skips_when_outgoing_exists
python -m pytest -q
```

Both commands must pass; expected full result is **466/466**.

### ISSUE-002 - P1 / High: resume primary action is clipped at 390px

Route: `/resume`

At 390px, the JD selector and primary action remain in one row. The document root is 390px wide, but `main` is approximately 458px wide. The button's right side and label are clipped.

Evidence: `.gstack/qa-reports/screenshots/resume-mobile-light-issue.png`

Required implementation:

- Stack the selector and action vertically at small widths, or use a layout preserving full content and hit areas.
- Do not hide the defect with `overflow-x: hidden`.
- Preserve the current tablet/desktop composition.

Acceptance:

- At 390x844 in Light and Dark, the complete button is visible and clickable.
- `main.scrollWidth <= main.clientWidth`; the document also has no horizontal overflow.
- No clipped text at 100% zoom.

### ISSUE-003 - P2 / Medium: mobile job cards have no visible View details action

Route: `/jobs`

The mobile card conversion works, but users cannot visibly open job details. Clicking the title does not open the existing dialog, and the footer mainly exposes the recycle action. This misses the planned card footer: `View details / Approve / Move to recycle bin / More`.

Evidence: `.gstack/qa-reports/screenshots/jobs-mobile-current.png`

Required implementation:

- Add a visible View details action to every mobile job card.
- Reuse the desktop detail dialog and data path; do not build a second details view.
- Make it keyboard-focusable with a clear accessible name.
- Add `aria-label` to icon-only actions.
- Low-frequency actions may move into More, but View details must remain direct.

Acceptance:

- Every 390px card has a direct View details action opening the correct job.
- The dialog scrolls and closes with Escape.
- The action is reachable with Tab and has an unambiguous accessible name.
- No new horizontal overflow.

### ISSUE-004 - P2 / Medium: resume and monitor empty states are incomplete

Routes: `/resume`, `/monitor`

Already present:

- `/resume`: three-step guidance and a go-to-job-pool action.
- `/monitor`: start-monitoring action.

Missing:

- `/resume`: Upload resume action.
- `/monitor`: Configure monitoring rules action.
- Both empty states: restrained horizontal OpenJob logo treatment.

Evidence:

```text
.gstack/qa-reports/screenshots/resume-desktop-missing-actions.png
.gstack/qa-reports/screenshots/monitor-desktop-missing-actions.png
```

Required implementation:

- Reuse a real upload/import flow. If none exists, do not create a dead button; link to a functioning setup/import destination or clearly state setup is required.
- Configure monitoring rules must navigate to the real config page or relevant anchor.
- Add a restrained 96-128px horizontal logo, visually subordinate to the heading and actions.
- Use `/brand/openjob-logo.svg` in Light and `/brand/openjob-logo-dark.svg` in Dark.
- Use `alt="OpenJob"`, or empty alt if adjacent text already announces the brand.

Acceptance:

- Primary and secondary actions are visible and reach functioning destinations.
- No dead placeholder controls.
- Logo source follows the theme.
- At 390px, actions stack cleanly with complete labels.

### ISSUE-005 - P3 / Low: fourth top-navigation item is partially cut off on mobile

At 390px, the scrolling top navigation initially shows only part of its fourth item. A complete BottomNav already exists, creating duplicated navigation and a visibly clipped label.

Evidence:

```text
.gstack/qa-reports/screenshots/home-mobile-light-current.png
.gstack/qa-reports/screenshots/resume-mobile-light-issue.png
```

Preferred implementation:

- Hide duplicate top-level navigation on small screens and retain the page title, contextual actions, and BottomNav.
- If the top strip has distinct secondary-navigation meaning, scroll the active item fully into view, never ellipsize it, and add a subtle edge fade/scroll affordance.

Acceptance:

- No half-visible navigation label in the initial 390px viewport.
- Current location remains clear.
- No page-level horizontal overflow.

## Accepted areas - preserve these

- Sidebar and favicon use `/brand/openjob-icon.svg`.
- Config uses `/brand/openjob-logo.svg` in Light and `/brand/openjob-logo-dark.svg` in Dark.
- Brand assets return `200 image/svg+xml`; horizontal logo accessibility and theme switching work.
- The dark home automation card uses a coherent deep-indigo surface.
- Brand gradients do not override semantic status colors.
- `/jobs` uses cards at 390px and shows title, score, city, salary, status, platform, activity, and time.
- `/`, `/jobs`, and `/confirm` have no document-level overflow at tested mobile/desktop widths.
- Existing dialog scroll and Escape behavior work.
- `/`, `/jobs`, `/confirm`, `/resume`, `/stats`, `/monitor`, and `/config` loaded at the tested 390x844, 1024x768, and 1280x720 combinations without browser console errors or warnings.
- Displayed date matched `2026-09-03` and the Asia/Shanghai timezone.

## Verification already performed

Frontend build:

```powershell
cd src/openjob/web/frontend
npm run build
```

Result: successful; TypeScript and Vite passed, 2162 modules transformed.

UI acceptance:

```powershell
cd src/openjob/web/frontend
node scripts/ui-acceptance.cjs
```

Result: `ALL-PASS`. Extend the script to catch the `/resume` local `main` overflow and verify a functional mobile View details action. Passing the old script alone is insufficient.

Python suite:

```powershell
python -m pytest -q
```

Result: `465 passed, 1 failed in 327.75s`.

A non-blocking `pytest-asyncio` warning notes that `asyncio_default_fixture_loop_scope` is not explicitly configured. Clean it up separately to avoid future default-behavior drift.

## Rework boundaries

- Do not redesign accepted visual foundations or replace the supplied SVG assets.
- Do not change the brand palette or semantic status colors.
- Do not broadly refactor routing, data access, or the desktop table.
- Do not perform real submissions, scraping, or live monitoring as test actions.
- Limit changes to the defects above and targeted regression tests.

## Final re-verification checklist

- [ ] Deduplication guard test passes; full Python suite is 466/466.
- [ ] `/resume` selector and primary action are fully visible at 390px.
- [ ] `/resume` satisfies `main.scrollWidth <= main.clientWidth`.
- [ ] Every mobile `/jobs` card has an accessible View details action.
- [ ] Details dialog opens, scrolls, and closes with Escape.
- [ ] `/resume` exposes a real Upload resume path.
- [ ] `/monitor` exposes a real Configure monitoring rules path.
- [ ] Empty-state logos switch correctly between Light and Dark.
- [ ] Mobile top navigation has no partially visible label.
- [ ] 390px, 1024px, and 1280px have no document-level overflow.
- [ ] Light and Dark show no visual regression.
- [ ] Browser console has no errors or warnings.
- [ ] `npm run build` passes.
- [ ] `node scripts/ui-acceptance.cjs` passes with new regression coverage.
- [ ] Before/after screenshots and complete test outputs are supplied.

## Sign-off decision

The project now has a coherent, branded UI foundation. Its desktop experience, theme integration, and mobile job-card direction are sound, so a redesign is unnecessary. Fix the safety-test regression, severe mobile clipping defect, and three interaction/plan omissions above. Until then, the status remains **conditionally accepted and not recommended for release**.
