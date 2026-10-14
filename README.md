# ABS Skill · AI Business System

[中文](#中文) · [English](#english)

## 中文

ABS Skill 让 Claude Code、Codex、WorkBuddy、OpenClaw 等 AI Agent 直接调用 ABS（AI 企业精益经营系统）：

- 查精益与经营方法：5S、标准作业、节拍与配员、快速换模、改善周、问题解决、日常管理与安灯、战略部署、价值流、TPM 与 OEE、拉动与看板、客户之声、价值销售、人才发展、并购整合等；
- 带出处回答，能取原文页核对；
- 学 ABS 课程；
- 一句话生成带时间规划的规划书；
- 对生产数据、文档或现场照片做业务诊断。

回答来自 ABS 的知识库（94 张工具卡、1,600 多条规则与参数，每条带出处），知识库建立在对丹纳赫业务系统（DBS）的深入研究之上，不是模型的通用印象。

### 安装

| 用在哪 | 命令 |
|---|---|
| 支持 Agent Skills 的大多数 Agent | `npx skills add hypergraphdev/abs-skills` |
| Claude Code | `/plugin marketplace add hypergraphdev/abs-skills`，再 `/plugin install abs-business-system@abs-skills` |
| OpenClaw | `openclaw skills install @hypergraphdev/abs-business-system` |
| Gemini CLI | `gemini extensions install https://github.com/hypergraphdev/abs-skills` |
| 手动 | 下载 <https://www.ucai.net/api/agent/skill.zip>，解压到 Agent 的技能目录 |

### 第一次使用

需要一个 ABS 账号和访问令牌：登录 <https://www.ucai.net>，在「个人设置 → 访问令牌」里生成一个，交给你的 Agent。
没有账号可以在 <https://www.ucai.net/trial> 申请试用。

### 它运行什么、发送什么、保存什么

- **运行**：Agent 决定使用这个技能时，运行 `scripts/abs.py`（只用 Python 标准库，不安装任何包）。
- **联网**：只通过 HTTPS 访问 `https://www.ucai.net/api/agent/v1/`（或你用 `--base-url`、`ABS_BASE_URL` 指定的 ABS 站点），不访问别的地址。
- **发送的内容**：你的访问令牌（放在请求头里）、检索词、问题、规划需求；只有 `diagnose` 会发送你指定的那个文件。
- **本机保存**：令牌和站点地址存在 `~/.config/abs-business-system/config.json`（Windows 在 `%APPDATA%`），文件权限只有你本人可读；`logout` 会删掉令牌。`page` 把取回的原文页存成当前目录下的 PNG。
- **读取的环境变量**：`ABS_TOKEN`、`ABS_BASE_URL`，设置了才读，作为 `login` 之外的另一种配置方式。
- **ABS 服务端**：用 ABS 的知识库作答，并调用第三方大模型服务生成文字；发给模型之前，会把你在 ABS 里登记过的对象名、产线名、人名换成代号。规划会存成你 ABS 账号里的草稿；诊断只在加 `--save` 时保存。

### 隐私

- 令牌等同于你本人：权限、可见范围、到期时间都跟着你的账号，只在 ABS 的 Agent 接口上有效，随时可以撤销。
- `diagnose` 会把你给的问题和文件发给 ABS 及其 AI 模型，涉及机密前请先确认。

完整的[隐私政策（Privacy Policy）](https://www.ucai.net/privacy.html)。

ABS 为独立产品，与丹纳赫无隶属、赞助或背书关系。

## English

The ABS Skill lets AI agents such as Claude Code, Codex, WorkBuddy and OpenClaw use ABS (AI Business System):

- look up lean and operational-excellence methods: 5S, standard work, takt and staffing, SMED, kaizen events,
  problem solving, daily management and andon, policy deployment, value streams, TPM and OEE, pull and kanban,
  VOC, value selling, talent development, M&A integration and more;
- answer with sources, and fetch the original page to check them;
- study ABS courses;
- turn one sentence into an execution plan with a schedule;
- diagnose operations data, documents or shop-floor photos.

Answers come from the ABS knowledge base (94 tool cards, 1,600+ rules and parameters, each with its source),
built on in-depth study of the Danaher Business System (DBS), not from the model's general knowledge.

### Install

| Where | Command |
|---|---|
| Most agents that support Agent Skills | `npx skills add hypergraphdev/abs-skills` |
| Claude Code | `/plugin marketplace add hypergraphdev/abs-skills`, then `/plugin install abs-business-system@abs-skills` |
| OpenClaw | `openclaw skills install @hypergraphdev/abs-business-system` |
| Gemini CLI | `gemini extensions install https://github.com/hypergraphdev/abs-skills` |
| Manual | download <https://www.ucai.net/api/agent/skill.zip> and unzip it into your agent's skills folder |

### First use

You need an ABS account and an access token: sign in at <https://www.ucai.net>, create one under
Personal Settings → Access tokens, and give it to your agent. No account yet? Request a trial at
<https://www.ucai.net/trial>.

### What it runs, sends and stores

- **Runs:** `scripts/abs.py` when your agent decides to use the skill. It uses the Python standard library only and
  installs nothing.
- **Network:** HTTPS to `https://www.ucai.net/api/agent/v1/` only (or the ABS site you set with `--base-url` or
  `ABS_BASE_URL`). No other destination.
- **Sends:** your access token (in the request header), search terms, questions and plan requests; only `diagnose`
  sends a file, and only the file you name.
- **Stores on your machine:** the token and site address in `~/.config/abs-business-system/config.json`
  (`%APPDATA%` on Windows), readable only by you; `logout` removes the token. `page` saves the fetched page as a PNG
  in the current folder.
- **Environment variables read:** `ABS_TOKEN` and `ABS_BASE_URL`, only if you set them, as an alternative to `login`.
- **ABS server side:** answers come from the ABS knowledge base, with a third-party large-language-model service
  writing the text. Before anything is sent to the model, ABS replaces the object, line and people names registered in your
  ABS account with placeholders. Plans are saved as drafts in your ABS account; diagnoses are saved only with `--save`.

### Privacy

- The token acts as you: same permissions, visibility and expiry as your account. It only works on the ABS agent
  API and can be revoked at any time.
- `diagnose` sends your question and file to ABS and its AI model. Check before sending anything confidential.

Read the full [Privacy Policy](https://www.ucai.net/privacy.html#en).

ABS is an independent product, not affiliated with, sponsored or endorsed by Danaher.

## License

[MIT-0](LICENSE)
