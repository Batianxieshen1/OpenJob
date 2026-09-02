# OpenJob UI 品牌 Logo 接入与第二轮体验收口执行计划

> **给执行 Agent 的直接实施文档**  
> 请在当前 OpenJob 项目中按本文执行。目标是增量优化，不要推翻已经完成的第二轮 UI。

## 0. 执行约束

1. 只修改前端 UI、样式、品牌资源接入和必要的无障碍代码，不改变后端业务协议。
2. 不删除已有功能，不改变真实投递、采集、监测的业务语义。
3. 不点击或调用真实投递动作进行测试。
4. 保留现有黑白灰工作台风格和钴蓝交互主色。
5. 品牌渐变只用于品牌识别和少量重点视觉，不要泛化到所有按钮、状态和图表。
6. 完成后必须执行 `npm run build`，并进行 1280×720、1024×768、390×844 的 light/dark 回归检查。

---

## 1. 现状验收结论

第二轮 UI 已基本完成，以下能力必须保留：

- 首页 Bento 布局；
- 首页任务入口、近 7 日趋势、最佳匹配、AI 用量等卡片；
- 投递确认分页；
- 投递详情弹窗；
- 监测执行 Tabs 和空状态；
- 配置页长页面；
- 移动端 BottomNav；
- 图表懒加载和分包。

本轮需要重点修复：

1. 移动端岗位池仍然使用超宽表格；
2. 深色首页“今日自动化”卡片反色过强；
3. 页面日期显示存在一天偏移风险；
4. 新 Logo 资源还没有真正接入；
5. `/stats`、`/monitor` 标题重复；
6. `/resume` 空状态过于空旷。

---

## 2. P1：移动端岗位池改为岗位卡片

### 2.1 问题

当前 `/jobs` 在 390px 下仍渲染宽度约 933px 的表格，在 1024px 下表格宽度也超过容器。虽然外层有横向滚动，但移动端无法同时看到职位、城市、薪资、评分、状态和操作。

### 2.2 实施要求

在移动断点下真正切换为 `JobCards`：

- `lg` 以下优先渲染卡片列表；
- 桌面端继续保留表格；
- 不要让用户在手机上横向拖动表格完成主要任务。

每张岗位卡片建议按以下层级：

```text
第一行：选择框 + 公司 + 匹配分
第二行：职位名称
第三行：城市 · 薪资 · 学历/岗位性质
第四行：来源平台 · 活跃度 · 采集时间
底部：查看详情 / 放行 / 移入回收站 / 更多
```

要求：

- 公司和职位最多两行，超出显示省略号；
- 评分使用当前钴蓝，不使用品牌渐变；
- 卡片操作按钮必须有可见文本或 `aria-label`；
- 批量选择后显示 sticky 顶部或底部工具栏；
- 筛选器继续使用移动端“筛选条件（展开）”折叠模式；
- 页面不能产生文档级横向溢出；
- BottomNav 不能遮挡最后一张岗位卡片。

### 2.3 验收标准

- 390px 下不显示超宽表格；
- 用户无需横向拖动即可看到岗位核心信息；
- 选择、查看详情、放行、回收站操作均可用；
- 1024px 下表格或卡片至少保证核心列可读。

---

## 3. P1：修复深色主题首页重点卡片

### 3.1 问题

`/` 的深色主题中，“今日自动化”卡片由于使用 `bg-ink`，实际变成接近浅色/白色的卡片，与周围深色卡片不一致。

### 3.2 实施要求

深色主题下改为以下方向之一：

- 深靛蓝背景；
- 深色表面 + 蓝紫边缘光；
- 深色卡片 + 提亮后的钴蓝主按钮。

不要使用纯白背景作为深色首页的默认重点卡片。

建议：

- 卡片背景：深色 `surface-strong`；
- 主按钮：保留 `#6082FF` 或当前 dark primary；
- 次要文字：使用可读的 `text-2`；
- 分隔线：使用 dark border；
- Light 主题样式不要被破坏。

### 3.3 验收标准

- `/ ?theme=dark` 首页所有核心卡片属于同一深色视觉体系；
- 自动化卡片仍然有视觉重点，但不再像白色浮层；
- 按钮、数字、发送时间和进度信息对比度足够。

---

## 4. P1：统一日期和时区口径

### 4.1 问题

本次验收基准日期为 **2026 年 9 月 2 日**，页面显示 **2026 年 9 月 3 日星期四**。请确认这是否由时区或 mock 数据导致。

### 4.2 实施要求

1. 明确产品统一采用：浏览器本地时区、后端时区，或固定工作区时区。
2. Header 时钟、首页日期、近 7 日趋势、发送窗口必须使用同一口径。
3. mock 数据不要固定到未来日期，除非页面明确标记为演示数据。
4. 跨午夜、不同系统时区和夏令时场景必须至少测试一次。
5. 如果后端才是最终时间源，应由后端返回当前时间和时区信息。

### 4.3 验收标准

- 页面日期与产品规定的当前日期一致；
- 趋势最后一天、首页“今天”、发送窗口使用同一天；
- 不再出现日期文字与实际任务数据错位。

---

## 5. P2：接入品牌 Icon 和 Logo

### 5.1 品牌资源

```text
/brand/openjob-icon.svg
/brand/openjob-logo.svg
/brand/openjob-logo-dark.svg
```

对应文件位于：

```text
src/openjob/web/frontend/public/brand/openjob-icon.svg
src/openjob/web/frontend/public/brand/openjob-logo.svg
src/openjob/web/frontend/public/brand/openjob-logo-dark.svg
```

### 5.2 品牌视觉规则

图标颜色：

- `#1467F8` 蓝；
- `#7257E8` 紫；
- `#F47E9C` 粉；
- `#FFD176` 黄色节点。

Logo 文字：

- 浅色版：`Open` 深色，`Job` 为紫粉橙渐变；
- 深色版：`Open` 白色，`Job` 为浅紫粉橙渐变。

形状语义是纸飞机/飞行箭头，与求职投递和自动化推进高度匹配。

### 5.3 Sidebar

将当前 Sidebar 左上角纯文字 `OJ` 替换为 Icon：

```tsx
<img src="/brand/openjob-icon.svg" alt="OpenJob" />
```

要求：

- 保留现有 44×44 左上角容器尺寸；
- Icon 建议 28–32px；
- 浅色、深色主题都要清晰；
- 不要把横向 Logo 压缩进窄侧栏；
- Hover 只做轻微缩放或阴影，不做大幅旋转。

### 5.4 Favicon

在前端 HTML 入口加入：

```html
<link rel="icon" type="image/svg+xml" href="/brand/openjob-icon.svg" />
```

Favicon 只使用方形 Icon，不使用横向 Logo。

### 5.5 横向 Logo

完整横向 Logo 只用于宽阔品牌区域：

- 欢迎页/登录页；
- `/config` 个人信息区域；
- `/resume` 无简历空状态；
- `/monitor` 无任务空状态；
- 关于 OpenJob 或首次使用引导。

建议宽度：

- 常规品牌区域：140–180px；
- 欢迎页：160–220px；
- 移动端顶部不要放完整横向 Logo。

手动主题切换时，必须基于应用主题状态切换资源：

- light：`openjob-logo.svg`；
- dark：`openjob-logo-dark.svg`。

不要只依赖系统 `prefers-color-scheme`，否则手动主题和系统主题可能不一致。

### 5.6 品牌组件封装

建议建立：

- `BrandMark`：Icon；
- `BrandLogo`：横向 Logo；
- `BrandLockup`：Icon + Logo + 副标题组合。

统一资源映射：

```ts
export const brandAssets = {
  icon: '/brand/openjob-icon.svg',
  logoLight: '/brand/openjob-logo.svg',
  logoDark: '/brand/openjob-logo-dark.svg',
} as const
```

不要在各页面散落字符串路径。

### 5.7 小尺寸和无障碍

- 16px 下完整 Icon 可能丢失白色内部线条和黄色节点；如有需要，准备简化 Icon；
- 外部 `<img>` 必须设置明确 `alt`；
- 装饰性重复 Icon 使用 `alt=""`；
- 如果未来改成 inline SVG，避免多个 `id="title"`、`id="flight"` 冲突；
- 高对比度模式和打印场景建议提供单色降级版本。

---

## 6. P2：清理页面标题层级

### `/stats`

保留内容区唯一 `h1`“市场分析”，Header 改为：

- 更新时间；
- 当前筛选范围；
- 或简短面包屑。

### `/monitor`

保留内容区唯一 `h1`“监测执行”，Header 不再重复显示完全相同的标题。

### 验收标准

- 每个页面首屏只有一个主要页面标题；
- DOM 标题层级保持合理；
- 不影响顶部导航和返回工作台功能。

---

## 7. P2：增强简历和监测空状态

### `/resume`

在当前空状态基础上增加轻量三步引导：

1. 上传简历；
2. 选择目标岗位；
3. 生成并导出定制简历。

添加按钮：

- “上传简历”；
- “去岗位池选择岗位”。

### `/monitor`

在空状态中增加：

- “去配置监测规则”；
- “立即运行一轮监测”。

注意：CTA 只负责跳转或打开配置，不得绕过用户确认直接执行高风险动作。

---

## 8. 品牌 Token

如果项目使用 Tailwind RGB Token，新增独立品牌 Token，不覆盖现有交互 Token：

```css
:root {
  --brand-blue: 20 103 248;
  --brand-purple: 114 87 232;
  --brand-pink: 244 126 156;
  --brand-orange: 255 169 104;
  --brand-yellow: 255 209 118;
}
```

规则：

- 交互主色继续使用当前 cobalt blue；
- 品牌蓝可用于 Logo 和品牌装饰；
- 品牌渐变只用于品牌或重点视觉；
- 成功、警告、失败状态继续使用语义色；
- 图表不要全部使用 Logo 渐变。

---

## 9. 回归验收清单

### 构建

- [ ] `npm run build` 通过；
- [ ] TypeScript 无错误；
- [ ] 页面无新增控制台错误或警告。

### 桌面端

- [ ] 1280×720 首页 light；
- [ ] 1280×720 首页 dark；
- [ ] 1280×720 岗位池；
- [ ] 1280×720 投递确认；
- [ ] 1280×720 市场分析；
- [ ] 1280×720 监测执行；
- [ ] 1280×720 配置。

### 中等视口

- [ ] 1024×768 首页 Bento 堆叠合理；
- [ ] 1024×768 岗位池核心信息可读；
- [ ] 1024×768 Header 不拥挤；
- [ ] 没有文档级横向溢出。

### 移动端

- [ ] 390×844 显示 BottomNav；
- [ ] 390×844 Sidebar 隐藏；
- [ ] 390×844 岗位池使用 JobCards；
- [ ] 390×844 无横向拖动表格要求；
- [ ] 390×844 Hero 标题自然换行；
- [ ] BottomNav 不遮挡内容。

### 品牌

- [ ] Sidebar 显示 `openjob-icon.svg`；
- [ ] Favicon 已接入；
- [ ] light 使用 `openjob-logo.svg`；
- [ ] dark 使用 `openjob-logo-dark.svg`；
- [ ] Logo 具备正确 alt；
- [ ] 没有用品牌渐变替代业务状态色；
- [ ] Logo 在 24px、32px、96px 尺寸下仍可识别。

### 交互

- [ ] 投递详情弹窗支持关闭按钮；
- [ ] 支持 Esc 关闭；
- [ ] 弹窗内容可以独立滚动；
- [ ] 不触发真实投递动作；
- [ ] 所有图标按钮有 aria-label 或可见文本；
- [ ] 保留 `prefers-reduced-motion` 支持。

---

## 10. 推荐提交顺序

建议拆成以下提交，方便回滚和验收：

1. `fix: normalize theme card contrast`
2. `fix: switch jobs pool to mobile cards`
3. `fix: normalize date and timezone source`
4. `feat: integrate OpenJob brand icon and favicon`
5. `feat: integrate light and dark wordmark`
6. `polish: clean page title hierarchy and empty states`
7. `test: add responsive and visual regression checks`

最终目标：

> **保持第二轮 UI 的布局成果，只修复移动端核心体验、深色主题一致性、日期口径和品牌落位。**
