# 复习教练（Review Coach）

> 越用越懂你的复习教练：笔记交给 **seekdb** 检索，薄弱点交给 **PowerContext** 记忆。

## 解决什么问题

备考时笔记散落在多个 Markdown 文件里，复习时只记得“好像在哪篇写过”，却定位不到具体段落；做错的题下次还是错，因为没有一个地方记住“你总在哪个主题上翻车”。

复习教练把两件事拆开做：

- **seekdb**：笔记的存储与全文检索（ngram 全文索引 + SQL 排序），问一句就能找到相关段落和出处；
- **PowerContext**：记住每个用户的复习记录（对/错、主题、时间）与最近查询，形成薄弱主题画像；
- **两者结合**：检索结果按薄弱主题提权，出题优先挑薄弱主题，复习记录跨进程持久化——每用一次，下一次就更懂你。

## 功能

| 命令 | 作用 | 技术点 |
| --- | --- | --- |
| `python -m coach init` | 建库建表、导入 `data/notes/*.md` | seekdb 建库、建全文索引 |
| `python -m coach ask alice "混合检索怎么融合两路结果"` | 检索笔记并输出出处 | seekdb `MATCH ... AGAINST` 排序 |
| `python -m coach review alice note-3 --wrong` | 记录一次复习结果 | PowerContext 写入记忆 |
| `python -m coach stats alice` | 查看记忆画像（正确率、薄弱主题、最近查询） | PowerContext 读取记忆（新进程） |
| `python -m coach quiz alice` | 按薄弱主题出填空题 | 记忆 + seekdb 取题 |
| `python -m coach notes` | 查看库里的笔记与主题分布 | seekdb 查询 |

## 运行说明

### 1. 环境

- Python 3.11+
- Docker（起 seekdb server）
- PowerContext：本仓库源码（`pip install -e ".[builtin]"`）

### 2. 启动 seekdb

```bash
docker run -d --name seekdb -p 2881:2881 oceanbase/seekdb
docker logs -f seekdb     # 等到 "SeekDB SQL server is accepting connections"
```

### 3. 安装依赖

在仓库根目录：

```bash
pip install -e ".[builtin]"
pip install -r qoder/review-coach/requirements.txt
```

### 4. 配置与初始化

```bash
cd qoder/review-coach
export SEEKDB_HOST=127.0.0.1
export SEEKDB_PORT=2881
export SEEKDB_USER=root
export SEEKDB_PASSWORD=
export SEEKDB_DATABASE=review_coach

python -m coach init
# {"database": "review_coach", "notes": 6, "store": "127.0.0.1"}
```

## 验证样例（可直接照抄执行）

```bash
# 1. 检索：seekdb 全文检索返回相关段落与出处
python -m coach ask alice "混合检索怎么融合两路结果"
# 预期：首条为 note-3《混合检索与 RRF》（主题 检索），带 score 与来源 data/notes/note-3.md

# 2. 连续两次答错同一主题，写入 PowerContext
python -m coach review alice note-3 --wrong
# {"user":"alice","note":"note-3","topic":"检索","correct":false,"topic_accuracy":0.0,"weak_topics":["检索"]}
python -m coach review alice note-3 --wrong

# 3. 新进程读取记忆：薄弱主题出现在画像里
python -m coach stats alice
# 预期：weak_topics 含 "检索"，recent_queries 含刚才的查询

# 4. 检索时按薄弱主题提权
python -m coach ask alice "向量检索"
# 预期：note-3 标记 "← 薄弱主题，优先复习"，排在向量笔记之前

# 5. 按薄弱主题出题
python -m coach quiz alice
# 预期：出题范围 检索（来自 PowerContext 的薄弱主题），挖空 RRF
```

技术作用核对点：

- seekdb：`ask` 的排序分数来自 `MATCH(content) AGAINST(...)`，索引为 `CREATE FULLTEXT INDEX ... WITH PARSER ngram`；`SELECT` 直接可查 `notes` 表；
- PowerContext：`review` 写入的 JSON 记忆，`stats`/`ask`/`quiz` 在新的 Python 进程里读出并影响排序与出题，证明记忆被复用而非进程内变量。

## 目录

```text
qoder/review-coach/
  coach/
    db.py       # seekdb：建库建表、全文索引、检索
    memory.py   # PowerContext：复习记录与薄弱主题画像
    cli.py      # init / ask / review / stats / quiz
  data/notes/   # 6 篇示例笔记（可替换成自己的笔记）
  site/         # Qoder Sites 项目网站源码
  QODER_LOG.md  # Qoder 开发记录模板（截图 + UID）
  requirements.txt
```

## Qoder 开发与发布

- 开发记录：见 `QODER_LOG.md`，按模板补充 Qoder 会话截图、生成的关键文件与 Qoder 账号 UID；
- 项目网站：`site/` 为静态站点源码，在 Qoder 中通过 **Sites** 创建站点并发布，可见范围设为「任何人都可访问」。

## 说明

- 示例笔记与代码为原创；seekdb 使用官方 `oceanbase/seekdb` 镜像，PowerContext 使用本仓库开源实现。
- 配置使用占位值，不包含任何真实凭据。
