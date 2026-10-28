# 魔搭社区 Skill 上传材料

本题要求把**自己编写的 Skill** 上传到魔搭社区（ModelScope），提交 Skill 的网址或编号。
本目录提供可直接上传的 Skill 包与上传脚本。

## 1. Skill 内容

`around-me-explorer/SKILL.md`：基于高德地图 MCP 的周边地点推荐助手。

- 适用场景：用户给出起点（经纬度或地名）和一句话需求，希望推荐附近地点并知道步行怎么走；
- 能力：`maps_geo` 解析起点 → `maps_around_search` 周边搜索 → `maps_direction_walking` 逐个查步行路线 → 按时间上限筛选、按距离排序 → 输出最多 3 个推荐（名称、地址、步行距离/时间、理由）；
- 规则：步行时间只取路线规划结果，不编造地点/距离/营业状态，结果不足或调用失败时如实说明。

SKILL.md 带有魔搭要求的前置元数据：

```yaml
---
name: around-me-explorer
version: 1.0.0
description: 基于高德地图 MCP 的周边地点推荐助手。……
---
```

## 2. 打包

```bash
python scripts/publish_skill.py --dry-run
# → modelscope/dist/around-me-explorer.zip
```

压缩包根目录只包含一个 `SKILL.md`（符合魔搭 Skills Center 的打包要求），体积约 2.7 KB。

## 3. 上传到魔搭社区

### 方式 A：网页上传（最简单，需要你自己的魔搭账号）

1. 登录 <https://www.modelscope.cn>；
2. 进入 **Skills** 频道（<https://www.modelscope.cn/skills>），选择「创建 / 发布 Skill」；
3. 上传 `dist/around-me-explorer.zip`，填写显示名称、简介、分类（建议「其他」或「开发者工具」）、标签、许可证；
4. 发布后打开 Skill 详情页，复制页面 URL（形如 `https://www.modelscope.cn/skills/@你的用户名/around-me-explorer`）。

### 方式 B：用脚本走官方 OpenAPI（需要 Access Token）

1. 在 <https://www.modelscope.cn/my/myaccesstoken> 创建 Access Token；
2. 设置环境变量并执行：

```bash
export MODELSCOPE_API_KEY=ms-xxxxxxxx          # 你的 Access Token
export MODELSCOPE_OWNER=your-modelscope-name   # 你的魔搭用户名
python scripts/publish_skill.py
```

脚本依次执行官方两步接口：

```text
POST /openapi/v1/files/upload   (multipart, type=skill, file=zip)  -> data.id
POST /openapi/v1/skills         (JSON: owner, skill_name, display_name, description,
                                  skill_file=data.id, category, license, tags, source_url)
```

成功后会打印：

```json
{ "skill_id": "@your-name/around-me-explorer", "url": "https://www.modelscope.cn/skills/@your-name/around-me-explorer" }
```

> `owner` 与 `skill_name`（小写字母、数字、连字符）创建后不可修改；如果同名 Skill 已存在，请改 `--name`。

### 方式 C：curl 手动执行

```bash
curl -X POST "https://www.modelscope.cn/openapi/v1/files/upload" \
  -H "Authorization: Bearer $MODELSCOPE_API_KEY" \
  -F "file=@dist/around-me-explorer.zip" -F "type=skill"

curl -X POST "https://www.modelscope.cn/openapi/v1/skills" \
  -H "Authorization: Bearer $MODELSCOPE_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "owner": "your-modelscope-name",
    "skill_name": "around-me-explorer",
    "display_name": "周边去哪儿：基于高德的地点推荐助手",
    "description": "使用高德 MCP 的周边地点推荐 Skill：解析起点、周边搜索、查询步行路线、按时间筛选并给出推荐理由。",
    "skill_file": "<上一步返回的 data.id>",
    "category": "other",
    "license": "MIT License",
    "tags": ["amap", "poi", "walking-route", "recommendation"],
    "source_url": "https://github.com/fuye-star/powercontext"
  }'
```

## 4. 发布后自检

- 打开 Skill 详情页，确认 `SKILL.md` 内容可查看（标题、适用场景、工具表、执行步骤、示例）；
- 页面 URL 能直接定位到该 Skill（不是个人主页或账号页）；
- 记录并提交以下任一信息：
  - **网址**：`https://www.modelscope.cn/skills/@<owner>/<skill-name>`
  - **编号**：详情页显示的 Skill ID（`@<owner>/<skill-name>`）

## 5. 说明

- 账号、Access Token 必须使用参赛者本人的魔搭账号；本目录不包含任何凭据。
- Skill 内容为原创；高德工具名与参数按官方 `@amap/amap-maps-mcp-server` 的真实定义编写。
- 如果评审要求可运行的 Skill 安装方式，Skill 详情页会提供 `npx skills add` / `modelscope skills add` / `curl install.sh` 三种安装命令。
