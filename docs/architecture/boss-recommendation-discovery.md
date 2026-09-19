# BOSS 推荐页 DOM 勘察记录（Batch A / Task 0）

> 勘察日期：2026-09-19 ｜ 方式：用户已登录的专用 Chrome（CDP 9222）+ OpenJob Browser Runtime，**只读**（未点击任何沟通/投递按钮，未读取 Cookie，未调用私有接口）
> 原始勘察记录（含真实岗位数据，不入 Git）：`data/recon/recommendation-recon-raw.json`
> 脱敏 fixture：`tests/fixtures/boss/recommendation-cards-basic.json`、`recommendation-page-states.json`
> 勘察脚本：`scripts/recon_recommend_feed.py`（只读，可复跑）

## 1. 结论速览

| 事项 | 结论 |
| --- | --- |
| 页面 URL | `https://www.zhipin.com/web/geek/recommend`（登录态直接可访问） |
| 登录要求 | 需要 BOSS 登录态；未登录重定向到登录页 |
| 加载方式 | **传统分页**（`?page=N`，实测 1..20 页，每页 20 卡），**不是无限滚动** |
| 滚动行为 | 实测 3 轮窗口滚动 detail_link_count 恒为 20——滚动不加载新内容 |
| 卡片结构 | `li.item-boss`（Vue 渲染 data-v-*），容器 `.sider-recommend-main` |
| 岗位身份 | `a[href*="/job_detail/"]`——与搜索页同源，身份去重天然兼容 |
| 与搜索流重叠 | 首屏 20 卡中清能互联、中泓在线、盈峰集团均已在本地库——**去重是核心场景**（实证） |
| 风险页 | 无验证码/登录墙/403；沿用现有 `JS_DETECT_COLLECTION_RISK` 判据 |

## 2. 权威卡片 DOM（实测 outerHTML 脱敏版）

```html
<li class="item-boss">
 <div class="item-content">
  <div class="info-header">
    <div class="btns"><a ka="personal_added_continue_<jobid>" class="btn btn-startchat">继续沟通</a></div>
    <div class="img-box"><img src="…avatar…"><h3 class="name"><span>HR姓名</span><span class="gray ml-12">HR</span></h3></div>
  </div>
  <div class="info-primary flex-reverse info-primary-noninterview">
    <div class="company-info">
      <div class="text"><b><a href="/gongsi/…">公司名</a></b>
        <p class="gray"><span>行业</span><span>融资</span><span>规模</span></p></div>
    </div>
    <div class="job-info width-500">
      <div class="job-name"><div class="flex">
        <a class="name" href="/job_detail/<id>.html?securityId=…&ka=personal_added_job_…">
          <span class="job-name-text">职位名</span>
          <span class="location">[<em>广州·天河区·石牌</em>]</span>
        </a></div></div>
      <p class="gray"> 180-200元/天 <span>在校/应届</span> <span>本科</span></p>
    </div>
  </div>
 </div>
</li>
```

## 3. 已确认 selectors（解析器依据）

| 字段 | selector / 取法 | 备注 |
| --- | --- | --- |
| 卡片容器 | `li.item-boss`（列表容器 `.sider-recommend-main`） | fallback：`[class*="recommend"] li` |
| 岗位链接（强身份） | `a[href*="/job_detail/"]` | 归一化：`new URL(href, location.origin).pathname`，**丢弃 securityId/ka 等 query** |
| 职位名 | `.job-name-text` | |
| 地点 | `.location`（内含 `em`，文本带方括号） | |
| 公司 | `.company-info .text b a` | |
| 公司标签 | `.company-info p.gray` 的 span 列表 | 行业/融资/规模 |
| HR 名/头衔 | `.info-header h3.name` 首个 span / `span.gray` | |
| **薪资** | `.job-info > p.gray` 的**直接文本节点**（如 `180-200元/天`） | 无独立 class；解析取 p.gray innerText 首段 |
| 经验/学历 | 同一 `p.gray` 的两个 `span`（如 在校/应届、本科） | |
| 分页 | `.options-pages`；当前页 `a.selected`；目标页 URL `?page=N` | 页码可见 1..6,…,20 |

**终止条件（分页制）**：`?page=N` 返回 0 个 `/job_detail/` 链接或出现"暂无更多/没有更多"→ `is_end_of_feed`；分页条 total_pages 上限；`max_pages`（契约里的 `recommendation_max_scrolls` 语义实际为最大分页轮次）。

## 4. 重要安全发现

1. **卡片内嵌"继续沟通"按钮**（`a.btn.btn-startchat`，`ka="personal_added_continue_*"`）——解析器只读 `href` 链接，**绝不查询或点击该按钮**；
2. **推荐页大量展示已沟通岗位**——与搜索流的身份去重（`(source_platform, source_job_id)`）是本功能的核心价值，非锦上添花；
3. 详情链接带会话性 `securityId` 与 `ka=personal_added_job_*` 参数——只取 pathname 入库；
4. 无需读取 Cookie/localStorage；薪资等缺失字段由详情页补齐（复用现有 `JS_EXTRACT_DETAIL`）。

## 5. 对计划书的两处实证修正

1. **§6.4 有限滚动 → 有限分页**：真实推荐页是分页制，滚动不加载内容。`recommendation_max_scrolls` 接口名保留（减少契约改动），语义改为最大分页轮次；`recommendation_same_result_limit` 语义为连续空页/重复页上限。
2. **§4 Step 0.2 预期滚动新增 → 不存在**：fixture 的滚动记录如实记录"3 轮无增长"，parser 设计按分页驱动。

## 6. fixture ↔ 实测映射

| fixture 内容 | 实测来源 |
| --- | --- |
| 完整卡片字段（title/salary/experience/education/company/hr/location） | `li.item-boss` outerHTML 逐字段提取 |
| 缺薪资字段卡片 | 实测卡片 `p.gray` 薪资为空时的形态（详情页补齐） |
| 重复链接卡片 | 同页同 URL 去重需求（归一化后同 pathname） |
| 分页第 2 页结构 | `?page=2` 实测（20 卡、`a.selected`、页码 1..20） |
| 滚动无增长 | 3 轮滚动探针记录 |
| 登录失效/风险页 | 沿用 BOSS 现有判据；实弹验收时复核 |

## 7. 未决事项（Batch B 实现时处理）

- 薪资/经验/学历在**列表页允许为空**，详情页补齐——与计划书 §6.3 一致；
- 推荐页分页 `?page=N` 的安全上限：契约 `recommendation_max_scrolls` 默认 4（即最多 4 页 ≈ 80 卡/轮），`daily_recommendation_page_limit: 10`；
- 登录失效与风险页的精确判别并入现有 `JS_DETECT_COLLECTION_RISK`，expected-content 需同时认可 `li.item-boss` 卡片。
