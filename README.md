# AstrBot Pixiv 搜索插件

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/vmoranv-reborn/astrbot_plugin_pixiv_search)
[![文档](https://img.shields.io/badge/AstrBot-%E6%96%87%E6%A1%A3-blue)](https://astrbot.app)
[![aiohttp](https://img.shields.io/pypi/v/aiohttp.svg)](https://pypi.org/project/aiohttp/)

![:@astrbot_plugin_pixiv_search](https://count.getloli.com/get/@astrbot_plugin_pixiv_search?theme=booru-lewd)

这是一个为 [AstrBot](https://astrbot.app) 开发的 Pixiv 搜索插件，让你可以在聊天中轻松搜索和获取 Pixiv 插画作品。

## ✨ 核心特性

- 🎨 **多种搜索方式**: 支持标签搜索、用户搜索、作品详情查询
- 📚 **内容多样化**: 插画、小说、排行榜、推荐作品一应俱全
- 🧩 **Fanbox 支持**: 可查询 Fanbox 创作者、帖子详情与推荐创作者
- 🎬 **动图支持**: 自动识别并转换 Pixiv 动图（ugoira）为GIF格式
- 🔍 **高级搜索**: 深度搜索、与搜索、相关作品推荐
- 🛡️ **内容控制**: 灵活的 R18 内容过滤配置
- ⚙️ **高度可配置**: 返回数量、显示详情、AI 作品过滤、最低书签/阅读量/点赞阈值等
- 🔐 **安全管理**: 通过 WebUI 安全管理 API 凭据

## 🎯 主要功能

### 聊天短命令

- `/pixiv 搜图 <标签> [N张]` - 搜索插画；N 为 1-10，仅影响本次请求，省略时使用配置数量
- `/pixiv 订阅 <画师ID或主页链接>` - 订阅画师，支持 `https://www.pixiv.net/users/123456`
- `/pixiv 取消订阅 <画师ID或主页链接>` - 取消画师订阅
- `/pixiv 订阅列表` - 查看当前会话的画师订阅
- `/pixiv 暂停推送`、`/pixiv 恢复推送` - 暂停或恢复当前会话的随机标签和随机排行榜推送，不影响画师订阅
- `/pixiv 推送状态` - 查看随机推送队列状态
- `/pixiv 帮助` - 查看短命令帮助；完整帮助仍使用 `/pixiv_help`

例如：`/pixiv 搜图 white dress,初音ミク,-ntr 3张`。标签可包含空格，数量必须独立放在末尾。
仅首个词完整匹配上述中文命令时进行分派；其他 `/pixiv <标签>` 搜索及原有长命令保持兼容，旧式搜索不会解析末尾数量。

群内订阅、随机推送的添加、删除、暂停、恢复和强制执行由机器人管理员、群主或群管理员操作；私聊可以管理自己的订阅。全局配置和 Pixiv 账号偏好仅机器人管理员可修改，凭据通过管理面板设置。普通查询保持开放，并继续遵守 AstrBot 核心权限设置。

### 引用图片操作

在 OneBot 群聊中引用当前机器人发送的单作品图片消息，可使用以下命令，也可直接发送对应的完整短语：

- `/pixiv 查询作者` - 根据作品 ID 向 Pixiv 查询真实画师及主页，不采用引用中的作者文字
- `/pixiv 订阅画师` - 订阅该作品的画师，沿用订阅开关、机器人管理员、群主及群管理员权限检查
- `/pixiv 再来几张 [N张]` - 获取同画师的其他作品，默认 3 个，允许 1-10 个，仅本次生效；数量按作品计算，多页作品沿用原有发送规则

引用恢复通过 OneBot `get_msg` 重新读取原消息，核对消息 ID、当前机器人发送者和当前群号，并要求消息同时包含图片及完整的 `作品ID: ...` 行或官方 Pixiv 作品链接。仅复制文字、引用其他用户消息或嵌套引用均不能替代此校验。关闭详情显示时，新发送的图片仍保留最简作品 ID；此前没有作品标识的历史图片无法恢复。

本批支持直接图片消息，不展开合并转发、转发节点或嵌套引用。引用含多个作品的消息也不会自动选择第一个。请先使用 `/pixiv_specific <作品ID>` 获取单作品图片，再引用其消息。默认单条合并发送的搜索结果可能属于此限制，不能直接引用整个转发消息进行操作。其他适配器或无法完整返回原消息身份的 OneBot 实现会返回说明，不执行引用操作；裸短语在不受支持的适配器上不接管消息。

追加作品排除被引用作品、重复候选、当前群发送历史及当前会话中已发送、发送中和结果待确认的作品，继续使用 R18、R18G、AI、排除标签和互动阈值策略，以及现有图片质量、发送方式和合并转发配置。最多检查 5 页画师作品，受配置搜索深度限制；符合条件的候选不足时，返回数量可能少于请求数量。图片准备失败或发送结果不确定时，不会将对应作品宣称为送达。

### 发送结果核实

发送前保存 `pending`，普通超时或断线且无法证明受理结果时保存 `unknown`。这些待确认状态不会因时间或发送历史清理而自动过期，也不会自动补发；只有适配器提供明确受理证据时才按确认送达处理。

- `/pixiv 待核实` - 由有管理权限的账号查看当前会话待确认记录
- `/pixiv 确认送达 <作品ID>` - 核对实际消息后确认送达
- `/pixiv 允许重发 <作品ID>` - 核实未送达后解除重发限制，不会立即发送图片；误判可能导致重复发送

聊天确认不会解除仍为 `pending` 的记录，即使记录已经很旧也需要维护流程核实。操作只作用于当前会话，不能代替适配器消息记录的实际核查。

为避免作品离开最新查询窗口后被遗漏，同一会话存在高于订阅游标的待确认作品时，相关订阅暂不推进游标，可能同时暂停该会话内多个画师的后续通知；核实后再继续。随机推送和 LLM 搜图会把人工确认送达的记录纳入既有去重保留期，LLM 全部候选已缓存时的最早发送优先回退仍然保留。

LLM 插画与小说工具的过滤参数只能收紧当前内容策略，不能绕过配置中的 R18 限制。添加画师订阅时必须成功查询画师及最新作品；失败会提示未添加，避免恢复网络后误推历史作品。多个群订阅同一画师时，每轮只查询一次，各群仍独立维护发送游标。

### 搜索功能
- `/pixiv <标签>` - 标签搜索插画
- `/pixiv_deepsearch <标签>` - 深度搜索更多相关作品
- `/pixiv_and <标签>` - 与搜索(同时包含所有标签)
- `/pixiv_user_search <用户名>` - 搜索用户
- `/pixiv_novel <标签>` - 搜索小说
- `/pixiv_novel download <小说ID>` - 下载小说为 pdf 文件并用文件md5值进行加密

### 随机搜索功能
- `/pixiv_random_add <标签>` - 添加随机搜索标签
- `/pixiv_random_list` - 列出当前随机搜索标签
- `/pixiv_random_del <序号>` - 删除指定序号的随机搜索标签
- `/pixiv_random_suspend` - 暂停当前群聊的随机搜索
- `/pixiv_random_resume` - 恢复当前群聊的随机搜索
- `/pixiv_random_status` - 查看随机搜索队列状态
- `/pixiv_random_force` - 强制执行当前群聊的随机搜索（调试用）

### 随机排行榜功能
- `/pixiv_random_ranking_add <模式> [日期]` - 添加随机排行榜配置
- `/pixiv_random_ranking_del <序号>` - 删除指定序号的随机排行榜配置
- `/pixiv_random_ranking_list` - 查看当前群聊的随机排行榜配置列表

### 热度搜索
- `/pixiv_hot <标签> [时间范围] [页数]` - 按收藏数排序搜索（时间范围: day/week/month/all）

### Fanbox 功能
- `/pixiv_fanbox_creator <creatorId|pixiv用户ID|链接> [数量]` - 查看创作者和最近帖子
- `/pixiv_fanbox_post <postId|帖子链接>` - 查看帖子详情、图片和附件链接
- `/pixiv_fanbox_recommended [数量]` - 获取推荐创作者
- `/pixiv_fanbox_artist <关键词> [数量]` - 按 Nekohouse artists 搜索 Fanbox 创作者

### 配置管理
- `/pixiv_config show` - 显示当前配置
- `/pixiv_config <参数名>` - 查看指定参数值
- `/pixiv_config <参数名> <值>` - 设置参数值
- `/pixiv_config help` - 显示配置帮助

### 排除 tag
- `-<tag>` - 排除包含 `<tag>` 的插画（支持多个负面标签）
- 示例：`/pixiv 露露卡,光之美少女,-ntr,-futa`
- 同样支持随机搜索标签配置：`/pixiv_random_add 露露卡,光之美少女,-ntr,-futa`

### 内容获取
- `/pixiv_recommended` - 获取推荐作品
- `/pixiv_ranking [模式] [日期]` - 排行榜作品
- `/pixiv_trending_tags` - 获取趋势标签
- `/pixiv_illust_new [类型] [最大作品ID]` - 获取大家的新插画作品
- `/pixiv_novel_new [最大小说ID]` - 获取大家的新小说
- `/pixiv_novel_recommended` - 获取推荐小说

### 详情查询
- `/pixiv_specific <作品ID>` - 指定作品详情（支持动图）
- `/pixiv_user_detail <用户ID>` - 用户详细信息
- `/pixiv_related <作品ID>` - 相关作品推荐
- `/pixiv_novel_series <系列ID>` - 小说系列详情
- `/pixiv_showcase_article <特辑ID>` - 特辑详情

### 评论功能
- `/pixiv_illust_comments <作品ID> [偏移量]` - 获取作品评论
- `/pixiv_novel_comments <小说ID> [偏移量]` - 获取小说评论

### 特殊功能
- `/pixiv_ai_show_settings <设置>` - 设置是否展示AI生成作品

### 订阅功能
- `/pixiv_subscribe_add <画师ID>` - 订阅画师
- `/pixiv_subscribe_remove <画师ID>` - 取消订阅画师
- `/pixiv_subscribe_list` - 查看当前订阅列表

## 🚀 快速开始

### 前置条件

- Python >= 3.10
- 已部署的 AstrBot 实例 (v3.x+)
- 有效的 Pixiv 账号和 `refresh_token`

### 配置插件

1. 打开 AstrBot WebUI
2. 进入 `插件管理` -> 找到 Pixiv 搜索插件
3. 点击 `插件配置`，填写以下信息：
   - **Refresh Token**: 必填，用于 Pixiv API 认证
   - **Fanbox Session (可选)**: `FANBOXSESSID`，用于受限 Fanbox 内容
   - **Fanbox 数据源模式**: `auto`（默认，官方优先失败回退 Nekohouse）/`official`（仅官方）/`nekohouse`（仅归档）
   - **R18 过滤模式**: 过滤R18/允许R18/仅R18
   - **额外过滤R18G**: 在允许 R18 时也可单独拦截 R18G
   - **单条合并消息**: 搜索完成后仅发送一条合并转发消息（默认开启）
   - **返回图片数量**: 1-10张，默认1张
   - **AI作品显示**: 是否显示AI生成作品
   - **质量阈值过滤**: 可选设置最小书签数 / 阅读量 / 点赞数
   - **质量过滤**: 可选发送原画|大图|缩略图
   - **其他选项**: 详情显示、文件转发等

4. 保存配置

### 获取 Refresh Token

参考以下资源获取 Pixiv `refresh_token`:
- [pixivpy3 官方文档](https://pypi.org/project/pixivpy3/)
- [Pixiv OAuth 教程](https://gist.github.com/ZipFile/c9ebedb224406f4f11845ab700124362)

### 部署反代服务（中国大陆用户）

若无法使用代理且直连 Pixiv API 失败，可自建 Cloudflare Workers 反向代理：

**仓库地址**: [vmoranv/pixiv-proxy](https://github.com/vmoranv/pixiv-proxy)

**部署步骤**：

1. 登录 [Cloudflare Dashboard](https://dash.cloudflare.com)
2. 进入 **Workers & Pages** → **Create Application** → **Create Worker**
3. 将 [pixiv-proxy.js](https://raw.githubusercontent.com/vmoranv/pixiv-proxy/main/pixiv-proxy.js) 的代码粘贴到编辑器中
4. 点击 **Deploy** 部署
5. 部署完成后复制 Worker 域名（如 `xxx.workers.dev`）
6. 在插件配置中设置：
   - `api_proxy_host` = `xxx.workers.dev`（用于 API 访问）
   - 或 `image_proxy_host` = `xxx.workers.dev`（用于图片下载）

> **提示**: 建议绑定自定义域名以避免 `workers.dev` 域名被墙

## 📝 使用示例

```bash
# 基础搜索
/pixiv 初音ミク,VOCALOID
/pixiv 茉莉安,-ntr

# 下载小说
/pixiv_novel download 12345678

# 高级搜索  
/pixiv_deepsearch 原神,风景
/pixiv_and 初音ミク,可爱

# 获取推荐和排行榜
/pixiv_recommended
/pixiv_ranking daily

# 获取最新作品
/pixiv_illust_new
/pixiv_novel_new

# 小说相关
/pixiv_novel_recommended
/pixiv_novel_series 123456

# 评论功能
/pixiv_illust_comments 12345678
/pixiv_novel_comments 12345678

# 特殊功能
/pixiv_ai_show_settings true

# 用户相关
/pixiv_user_search 某个画师名
/pixiv_user_detail 123456

# 特辑功能
/pixiv_showcase_article 123456

# 获取帮助
/pixiv_help

# 订阅功能
/pixiv_subscribe_add 123456
/pixiv_subscribe_remove 123456
/pixiv_subscribe_list

# 随机搜索功能
/pixiv_random_add 风景
/pixiv_random_list
/pixiv_random_del 1
/pixiv_random_suspend
/pixiv_random_resume
/pixiv_random_status
/pixiv_random_force

# 随机排行榜功能
/pixiv_random_ranking_add day
/pixiv_random_ranking_add week 2023-05-01
/pixiv_random_ranking_del 1
/pixiv_random_ranking_list

# 热度搜索
/pixiv_hot 可莉 week 5

# Fanbox 功能
/pixiv_fanbox_creator harusono 5
/pixiv_fanbox_post 10451793
/pixiv_fanbox_recommended 8
/pixiv_fanbox_artist hannari 10

# 配置管理
/pixiv_config show
/pixiv_config r18_mode 仅 R18
/pixiv_config min_bookmarks 500
/pixiv_config min_views 5000
/pixiv_config random_search_min_interval 30
/pixiv_config random_search_quiet_hours_enabled true
/pixiv_config random_search_quiet_start 00:00
/pixiv_config random_search_quiet_end 08:00
```

## ⚙️ 配置选项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `refresh_token` | Pixiv API 认证令牌 | 必填 |
| `fanbox_sessid` | Fanbox 会话 Cookie（FANBOXSESSID，可选） | 留空 |
| `fanbox_cookie` | Fanbox 完整 Cookie（建议含 `cf_clearance` + `FANBOXSESSID`） | 留空 |
| `fanbox_user_agent` | Fanbox 请求 UA（建议与浏览器一致） | 留空 |
| `fanbox_data_source` | Fanbox 数据源：`auto`/`official`/`nekohouse` | auto |
| `return_count` | 每次搜索返回的图片数量 (1-10) | 1 |
| `r18_mode` | R18内容处理模式 | 过滤 R18 |
| `filter_r18g_only` | 是否额外过滤 R18G | false |
| `ai_filter_mode` | AI作品显示设置 | 显示 AI 作品 |
| `ai_detection_mode` | AI判定策略：`field_or_tag`/`field_only`/`tag_only` | field_or_tag |
| `automatic_push_excluded_tags` | 自动推送统一排除标签，使用逗号分隔；留空关闭 | NTR,悪堕ち |
| `min_bookmarks` | 过滤书签数小于该值的插画，0 表示关闭 | 0 |
| `min_views` | 过滤阅读量小于该值的插画，0 表示关闭 | 0 |
| `min_likes` | 过滤点赞数小于该值的插画，0 表示关闭；若 API 未返回点赞字段则自动忽略 | 0 |
| `deep_search_depth` | 深度搜索时搜索页数深度 (-1无限制, 0-50) | 3 |
| `show_details` | 是否在发送图片时附带详细信息 | true |
| `forward_threshold` | 是否启用消息转发功能 | false |
| `show_filter_result` | 是否显示过滤内容提示 | true |
| `single_response_mode` | 是否仅在搜索完成后发送一条合并消息 | true |
| `image_send_method` | 图片发送方式：`url`/`file`/`byte`（升级旧版本建议设为 `byte` 或 `file`） | url |
| `image_quality` | 默认发送的图片质量 (original/large/medium) | medium |
| `pil_compress_quality` | 本地 PIL 压缩百分比(1-100，仅file/byte生效，100为不压缩) | 100 |
| `pil_compress_target_kb` | 本地 PIL 目标大小KB(>0优先按大小压缩，仅file/byte生效) | 0 |
| `refresh_token_interval_minutes` | 自动刷新 Refresh Token 的间隔时间（分钟） | 180 |
| `subscription_enabled` | 是否启用订阅功能 | true |
| `subscription_force_forward` | 订阅消息是否强制使用合并转发（默认普通图片消息） | false |
| `subscription_check_interval_minutes` | 订阅更新检查间隔（分钟） | 30 |
| `proxy` | 网络代理地址，如 `http://127.0.0.1:7890` | 留空 |
| `image_proxy_host` | 图片反代服务器地址 | i.pixiv.re |
| `use_image_proxy` | 是否启用图片反代服务器（`url` 发送模式建议开启） | true |
| `random_search_min_interval` | 随机搜索最短间隔（分钟） | 60 |
| `random_search_max_interval` | 随机搜索最长间隔（分钟） | 120 |
| `random_search_quiet_hours_enabled` | 是否启用随机搜索静默时段，仅影响随机标签和随机排行榜 | false |
| `random_search_quiet_start` | 随机搜索静默时段开始时间，格式 `HH:MM` | 00:00 |
| `random_search_quiet_end` | 随机搜索静默时段结束时间，格式 `HH:MM` | 08:00 |
| `random_search_empty_retry_enabled` | 随机搜索筛空时是否扩大搜索范围并补抽其他来源 | true |
| `random_search_empty_retry_extra_depth` | 筛空时每轮额外搜索页数，最多扩大到 30 页 | 3 |
| `random_search_empty_retry_sources` | 筛空时额外补抽同群其他随机来源数量 | 1 |
| `random_sent_illust_retention_days` | 已发送作品保留天数 | 7 |

## 🔧 故障排除

**SSL 错误**: 如遇到 `SSLError`，请更新 DNS 解析设置。参考: [SSLError 解决方案](https://github.com/upbit/pixivpy/issues/244)

**模块未找到**: 重启 AstrBot 以确保依赖正确安装

**API 认证失败**: 检查 `refresh_token` 是否有效和正确配置

**无代理直连失败**: 若在中国大陆无法使用代理且 ByPassSniApi 模式失效，可自建 Cloudflare Workers 反向代理。详见 [vmoranv/pixiv-proxy](https://github.com/vmoranv/pixiv-proxy) 仓库，部署后在插件配置中设置 `api_proxy_host` 为你的 Worker 域名。

**Fanbox 帖子获取失败**: 可能触发 Cloudflare 或帖子受限。可先用 `/pixiv_fanbox_creator` 查看公开帖子，必要时配置 `fanbox_sessid`；若官方仍 403，建议同时配置 `fanbox_cookie`（完整 Cookie，含 `cf_clearance`）和 `fanbox_user_agent`（与浏览器一致）；也可切换 `fanbox_data_source=nekohouse` 仅走归档数据。

**Fanbox 配置缺省提示**: 当 `fanbox_sessid` 未配置且访问受限内容时，会自动返回 `data/helpmsg.json` 的 `pixiv_fanbox_sessid_missing` 提示。

## 📖 更多信息

- [AstrBot 官方文档](https://astrbot.app/)
- [插件开发指南](https://astrbot.app/develop/plugin.html)
- [问题反馈](https://github.com/vmoranv-reborn/astrbot_plugin_pixiv_search/issues)

## ⭐ 项目统计

<div align="center">

[![Star History Chart](https://api.star-history.com/svg?repos=vmoranv-reborn/astrbot_plugin_pixiv_search&type=Date)](https://star-history.com/#vmoranv-reborn/astrbot_plugin_pixiv_search&Date)

![Analytics](https://repobeats.axiom.co/api/embed/9e6727cd94536119069eebccfe45b505ac499470.svg "Repobeats analytics image")

</div>

## 📄 许可证

本项目遵循开源许可证，具体许可证信息请查看项目根目录下的 LICENSE 文件。

---

**注意**: 使用本插件需遵守 Pixiv 服务条款和相关法律法规。请合理使用 API 避免频繁请求。
