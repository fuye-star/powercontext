# Qoder 开发记录

> 提交前请用你自己的 Qoder 会话补全本文件，并附上截图。评审会核对「开发过程」与「仓库代码」是否对应。

## 账号信息

| 项 | 值 |
| --- | --- |
| Qoder 账号 UID | 请填写（Qoder 客户端 → 个人中心可查看） |
| 使用方式 | Qoder（桌面端 / IDE 插件，按实际填写） |
| 开发时间 | 请填写 |

## 会话记录

| # | 阶段 | 给 Qoder 的指令（原文或摘要） | Qoder 产出 | 截图文件 |
| --- | --- | --- | --- | --- |
| 1 | 需求拆解 | 见下方「提示词 1」 | 功能清单、命令设计 | `screenshots/01-requirements.png` |
| 2 | 数据层 | 见下方「提示词 2」 | `coach/db.py`（seekdb 建表、ngram 全文索引、检索 SQL） | `screenshots/02-seekdb.png` |
| 3 | 记忆层 | 见下方「提示词 3」 | `coach/memory.py`（PowerContext 写入与画像聚合） | `screenshots/03-powercontext.png` |
| 4 | CLI 与调试 | 见下方「提示词 4」 | `coach/cli.py`，联调 `ask/review/quiz/stats` | `screenshots/04-cli.png` |
| 5 | 网站发布 | 见下方「提示词 5」 | `site/index.html`，通过 Qoder Sites 发布 | `screenshots/05-sites.png` |

## 提示词（可复现）

**提示词 1：需求拆解**

```text
我要做一个复习教练 CLI：笔记存在 seekdb 里，支持全文检索；用户的复习记录存在 PowerContext 里，
形成薄弱主题画像；检索和出题时优先薄弱主题。请拆解成功能清单和 CLI 命令，不要写代码。
```

**提示词 2：seekdb 数据层**

```text
用 Python + pymysql 实现 coach/db.py：
1) 连接 seekdb（MySQL 协议，环境变量 SEEKDB_HOST/PORT/USER/PASSWORD/DATABASE）；
2) 建 notes 表（id,title,topic,source,content），建 ngram 全文索引；
3) 提供 replace_notes / count / topics / get / search（MATCH AGAINST 排序，失败回退 LIKE）。
```

**提示词 3：PowerContext 记忆层**

```text
用 powercontext 的 Python SDK 实现 coach/memory.py：
每个用户一个 Scope，把复习记录（note_id, topic, correct, 时间）写成记忆；
聚合出正确率、薄弱主题（正确率 < 0.6）和最近查询；每次 CLI 命令都是新进程，必须能读回。
```

**提示词 4：CLI 与调试**

```text
实现 coach/cli.py，命令：init / notes / ask / quiz / review / stats。
ask 检索后按薄弱主题给结果提权；quiz 优先从薄弱主题的笔记里挖空拉丁术语；
review 写入记忆并打印该主题正确率；stats 打印画像。运行 python -m coach init 验证。
```

**提示词 5：Qoder Sites 网站**

```text
用 site/index.html 做一个单页项目网站：介绍痛点、功能表、真实运行输出、技术分工、仓库链接。
然后在 Qoder 中通过 Sites 发布，可见范围设为「任何人都可访问」。
```

## 说明

- 截图请保留 Qoder 界面中的会话标题、时间与文件变更面板，便于核对；
- 若开发过程中有 Qoder 自动修改/调试的记录（如报错修复），也请补一行；
- 项目网站发布后，请把 Sites 地址补充到提交信息中。
