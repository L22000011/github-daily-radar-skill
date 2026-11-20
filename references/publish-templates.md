# 短视频发布文案、评论区链接与账号主页模板

每次运行出片通过 12 项 QA 后，按以下结构分开交付给创作者手动复制发布：

---

## 一、发布正文短文案模板（控制在一屏以内）

```text
今日 GitHub 热榜这 <N> 个开源神器冲太猛了🔥
<用一句话分别点名 #1~#3 最吸睛核心亮点，不超过 2 行>

👉 5 个项目的完整 GitHub 地址我放评论区置顶了，自取直接部署！
你最想先试哪一个？评论区聊聊👇

#GitHub #开源项目 #AI工具 #程序员 #效率神器 #每日GitHub
```

> 撰稿原则：基于本集 `facts.md` 真实亮点写 1-2 句 hook + 引导看置顶评论，不要在正文里堆满长网址（容易被平台折叠或限流）。

---

## 二、评论区置顶 GitHub 仓库链接模板（独立区块，方便直接复制置顶）

严格从本集 `<out_dir>/facts.md` 或 `trending_report.json` 读取精确的 `owner/repo`、增量 Star 与协议，禁止手打篡改：

```text
本期 5 个开源项目直达（可直接搜仓库名或复制地址）：
1. <owner/repo_1>（+<delta_1> Stars · 一句话用途）
https://github.com/<owner/repo_1>

2. <owner/repo_2>（+<delta_2> Stars · 一句话用途）
https://github.com/<owner/repo_2>

3. <owner/repo_3>（+<delta_3> Stars · 一句话用途）
https://github.com/<owner/repo_3>

4. <owner/repo_4>（+<delta_4> Stars · 一句话用途）
https://github.com/<owner/repo_4>

5. <owner/repo_5>（+<delta_5> Stars · 一句话用途）
https://github.com/<owner/repo_5>
```

---

## 三、简洁账号主页简介参考

### 极简硬核版（推荐）
```text
每天 2 分钟，盘点 GitHub 最值得部署的 AI 与提效开源项目。
只看真数据与真实演示 · 每期项目链接放置顶评论
```

### 亲和极客版
```text
每日严选 GitHub 热榜硬核开源项目，拆解核心原理与落地场景。
不吹不黑，直给 GitHub 仓库直达链接 👇
```
