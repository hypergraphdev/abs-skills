# ABS Skill 本地统计页

维护者用的工具，不属于技能本身，安装技能时用不到它。

```bash
python3 tools/dashboard/abs_dashboard.py               # 打开 http://127.0.0.1:8765，点「刷新」重新检查
python3 tools/dashboard/abs_dashboard.py --html a.html # 写一份静态快照，可以发给别人看
python3 tools/dashboard/abs_dashboard.py --json        # 打印原始数据
```

只用 Python 3.8+ 标准库，不装任何包。服务只监听本机。一次检查要 10 到 30 秒，最慢的是 MCP 官方注册表。

## 看什么

| 板块 | 内容 |
|---|---|
| 1 代码状态 | 分支、最新提交、未提交的文件、和远端 main 是否同步；三处发布版本号是否一致、JSON 能否解析、技能名是否对得上、`abs.py` 能否运行；GitHub 上未合并的 PR 和 Issue |
| 2 哪些平台可用 | GitHub 仓库（npx skills、Claude Code、Gemini 都从这里装）、Claude Code 插件市场、Gemini CLI 扩展与扩展库收录、skills.sh 目录、ClawHub（OpenClaw）、ABS 官网 skill.zip、MCP 官方注册表；每项的线上版本和本地对比 |
| 3 MCP 连通性 | 跨域预检、未登录时的 401 和授权元数据、OAuth 动态注册与 PKCE、免登录检索；有 ABS 令牌时再测令牌身份、MCP 握手和工具数 |
| 4 下载与访问量 | ClawHub 下载、安装和收藏；GitHub 14 天克隆、浏览和来源；Star 与下载趋势 |

拿不到的数会在页面上写明原因，不会补一个估计值：

- ABS 官网 skill.zip 的下载量和 MCP 连接数，只记在 ABS 服务端；
- skills.sh 的安装数，目录收录后才有；
- Claude Code 和 Gemini 的安装数，两边都不公布。

## 令牌

令牌只用来请求，不会显示在页面上，也不会写进快照。

- **GitHub**：读环境变量 `GITHUB_TOKEN` 或 `GH_TOKEN`，没有就用 `gh auth token`。只发给 api.github.com。看访问量需要对仓库有推送权限；没有令牌时，其余各项照样能检查。
- **ABS**：读环境变量 `ABS_TOKEN`，没有就用 `abs.py login` 存下的令牌。只发给 ABS 站点。没有令牌就跳过 MCP 登录后的三项测试。

## 快照

每次刷新会追加一条计数快照到 `~/.local/state/abs-dashboard/`（Windows 在 `%LOCALAPPDATA%\abs-dashboard\`），存的是：

- `history.jsonl`：ClawHub 下载数和 Star 等计数；
- `traffic.json`：GitHub 每日克隆和浏览，按日期合并。

GitHub 只给最近 14 天的访问量，存下来就能看更久，页面最多显示 90 天。所以隔几天打开一次刷新，趋势图才有内容。`--no-save` 不记快照。
