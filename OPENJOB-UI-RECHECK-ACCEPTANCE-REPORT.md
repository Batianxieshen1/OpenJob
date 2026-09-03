# OpenJob UI 二次复验报告

> 复验日期：2026-09-03  
> 复验版本：`main` / `0979605`  
> 复验地址：`http://127.0.0.1:8686`  
> 结论：**验收通过，5 项定向返工全部完成。**

## 1. 总体结论

上轮报告中的两个 P1 问题均已修复，移动端岗位详情、空状态行动入口、横向品牌 Logo 和重复顶部导航也已经完成。前端构建、UI 自动验收、完整 Python 测试和真实浏览器矩阵均通过，当前版本可以进入签收或发布流程。

`/monitor` 的横向 OpenJob Logo 已在桌面端接入，Dark 模式使用 `/brand/openjob-logo-dark.svg`，显示宽度约 110px；390px 移动端隐藏该辅助品牌元素以控制纵向长度，属于合理响应式取舍。

复验健康度：**100 / 100**。

## 2. 上轮问题复验结果

| 编号 | 上轮问题 | 复验结果 | 证据 |
|---|---|---|---|
| ISSUE-001 | 外发消息去重测试失败 | 通过 | 定向测试通过；完整测试 466/466 |
| ISSUE-002 | 390px 简历页主按钮裁切 | 通过 | 按钮宽 358px，左右均在 390px 视口内；`main` 390/390 |
| ISSUE-003 | 移动岗位卡缺少查看详情 | 通过 | 15 张卡片均有入口，详情弹窗可打开并用 Esc 关闭 |
| ISSUE-004 | 简历/监测空状态入口与 Logo 不完整 | 通过 | 两个真实入口和桌面端横向 Logo 均完成，主题资源正确 |
| ISSUE-005 | 移动顶部导航文字被截断 | 通过 | 390px 顶部重复导航已隐藏，仅保留完整 BottomNav |

## 3. 关键交互复验

### 简历工作台 `/resume`

- 390px 主按钮完整显示：`left=16`、`right=374`、`width=358`；
- `main.scrollWidth=390`、`main.clientWidth=390`；
- 文档宽度 390/390，无横向溢出；
- “上传简历底稿”是真实按钮，点击后进入 `/config`；
- 横向 Logo 在 Dark 使用 `/brand/openjob-logo-dark.svg`，宽约 120px；
- 切换到 Light 后自动变为 `/brand/openjob-logo.svg`；
- 移动端顶部重复导航已经隐藏。

截图：

```text
.gstack/qa-reports/screenshots/recheck-resume-mobile.png
```

### 岗位池 `/jobs`

- 390px 无横向溢出；
- 当前数据下 15 张移动卡片均显示“查看详情”；
- 第一张卡片入口为原生 `button`；
- 可访问名称包含具体岗位名称；
- 点击后打开正确岗位详情；
- 弹窗在移动视口内可见并允许内部滚动；
- `Esc` 可以关闭弹窗；
- 浏览器控制台无错误和警告。

截图：

```text
.gstack/qa-reports/screenshots/recheck-jobs-mobile.png
.gstack/qa-reports/screenshots/recheck-job-dialog-mobile.png
```

### 监测执行 `/monitor`

- “配置监测规则”入口存在；
- 点击后进入真实 `/config` 页面；
- “启动一轮监测”主操作保留；
- 390px 文档宽度 390/390，无横向溢出；
- 浏览器控制台无错误和警告。

## 4. 自动化与构建证据

### 前端构建

```powershell
cd src/openjob/web/frontend
npm run build
```

结果：成功。

```text
2162 modules transformed
✓ built in 4.91s
```

### UI 验收脚本

```powershell
cd src/openjob/web/frontend
node scripts/ui-acceptance.cjs
```

结果：`ALL-PASS`。

新增覆盖也已通过：

- `resume-390-no-overflow`
- `resume-390-primary-action-visible`
- `jobs-390-view-details-action`，检测到 15 个入口
- `jobs-390-detail-dialog-opens-in-viewport`
- `modal-visible-at-scroll`
- `modal-esc-close`

### Python 完整测试

```powershell
python -m pytest -q
```

结果：

```text
466 passed in 285.04s (0:04:45)
```

原失败用例单独执行同样通过：

```text
tests/test_send_dedup_guard.py::SendGuardTests::test_send_greeting_once_skips_when_outgoing_exists
1 passed
```

仍存在非阻断的 `pytest-asyncio` 配置弃用警告：`asyncio_default_fixture_loop_scope` 未显式设置。它不属于 UI 返工失败，也不影响本次 466 项测试结果，建议后续作为工程维护任务处理。

## 5. 浏览器矩阵

以下 7 个页面：

```text
/
/jobs
/confirm
/resume
/stats
/monitor
/config
```

已在以下 5 组环境中复验，共 35 个组合：

```text
390×844 Dark
390×844 Light
1024×768 Light
1280×720 Dark
1280×720 Light
```

35/35 均满足：

- 页面正常加载；
- 实际主题与目标主题一致；
- 文档及 `main` 没有横向溢出；
- 没有 HTTP 4xx/5xx 资源失败；
- 浏览器控制台没有 error 或 warning；
- 页面内容非空。

## 6. 最终签收意见

本轮定向返工有效，没有发现新的阻断性回归。品牌、响应式布局、明暗主题、岗位详情交互和发送安全测试均达到签收条件。

最终状态：**验收通过，可以发布。** 上轮 5 项定向返工均已完成，没有遗留阻断项或计划漏项。
