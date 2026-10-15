#!/usr/bin/env python3
"""ABS Skill 本地统计页：代码状态、各平台上架情况、MCP 连通性、下载与访问量。

    python3 tools/dashboard/abs_dashboard.py               # 打开 http://127.0.0.1:8765
    python3 tools/dashboard/abs_dashboard.py --html a.html # 只写一份静态快照
    python3 tools/dashboard/abs_dashboard.py --json        # 打印原始数据

只用 Python 标准库。令牌从哪来（都不会显示在页面上）：
  GitHub  环境变量 GITHUB_TOKEN / GH_TOKEN，否则 `gh auth token`；访问量要对仓库有推送权限的令牌
  ABS     环境变量 ABS_TOKEN，否则 `abs.py login` 存下的令牌；没有就跳过 MCP 登录后的测试
GitHub 令牌只发给 api.github.com，ABS 令牌只发给 ABS 站点。
每次刷新会把计数存一份快照，GitHub 只给最近 14 天的访问量，存下来才能看更久。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
SKILL_DIR = ROOT / "skills" / "abs-business-system"
REPO = "hypergraphdev/abs-skills"
SKILL = "abs-business-system"
UA = "abs-dashboard/1.0"
EXPECTED_MCP_TOOLS = 11  # README：MCP 和技能能做的事一样，11 个工具


def load_abs_script():
    """复用 abs.py 的配置路径与站点地址，不另写一份。"""
    spec = importlib.util.spec_from_file_location("abs_cli", SKILL_DIR / "scripts" / "abs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ABS = load_abs_script()


# ---------------------------------------------------------------- 工具函数


class Resp:
    def __init__(self, status: int, headers: dict[str, str], body: bytes, ms: int, error: str = ""):
        self.status, self.headers, self.body, self.ms, self.error = status, headers, body, ms, error

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except ValueError:
            return None

    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def http(url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: Any = None,
         timeout: int = 20) -> Resp:
    """不因 HTTP 错误抛异常：状态码 0 表示没连上。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            return Resp(r.status, {k.lower(): v for k, v in r.headers.items()}, payload, ms(start))
    except urllib.error.HTTPError as e:
        return Resp(e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(), ms(start))
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return Resp(0, {}, b"", ms(start), f"连不上：{reason}")


def ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def item(name: str, status: str, summary: str, *, detail: str = "", url: str = "", latency: int | None = None,
         **extra: Any) -> dict[str, Any]:
    """status: ok 正常 · warn 注意 · fail 失败 · skip 跳过 · info 信息"""
    out = {"name": name, "status": status, "summary": summary, "detail": detail, "url": url, "ms": latency}
    out.update(extra)
    return out


def guarded(name: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as e:  # 一项检查出错不能拖垮整页
        return item(name, "fail", f"检查本身出错：{type(e).__name__}: {e}")


def git(*args: str, timeout: int = 15) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or f"git {' '.join(args)} 失败")
    return out.stdout.strip()


def frontmatter_version(text: str) -> str | None:
    head = text.split("\n---", 1)[0] if text.startswith("---") else ""
    m = re.search(r"^version:\s*(\S+)", head, re.M)
    return m.group(1) if m else None


def read_json_file(path: Path) -> tuple[Any, str]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as e:
        return None, str(e)


def version_key(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def compare(found: str | None, local: str | None) -> str:
    if not found:
        return "warn"
    if found == local:
        return "ok"
    return "warn"


# ---------------------------------------------------------------- 令牌


def github_token() -> str:
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        if os.environ.get(key):
            return os.environ[key].strip()
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def abs_settings() -> tuple[str, str]:
    base, token = ABS.resolve(argparse.Namespace(base_url=None, token=None))
    return base or "https://www.ucai.net", token


def gh(path: str, token: str, timeout: int = 20) -> Resp:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return http(f"https://api.github.com{path}", headers=headers, timeout=timeout)


def raw(path: str) -> Resp:
    return http(f"https://raw.githubusercontent.com/{REPO}/main/{path}")


# ---------------------------------------------------------------- 1. 代码状态


def local_versions() -> dict[str, Any]:
    files = {
        "SKILL.md": SKILL_DIR / "SKILL.md",
        ".claude-plugin/plugin.json": ROOT / ".claude-plugin" / "plugin.json",
        "gemini-extension.json": ROOT / "gemini-extension.json",
        "scripts/abs.py": SKILL_DIR / "scripts" / "abs.py",
        "server.json": ROOT / "server.json",
    }
    skill_text = files["SKILL.md"].read_text(encoding="utf-8")
    plugin, _ = read_json_file(files[".claude-plugin/plugin.json"])
    gemini, _ = read_json_file(files["gemini-extension.json"])
    server, _ = read_json_file(files["server.json"])
    script = re.search(r'^VERSION = "([^"]+)"', files["scripts/abs.py"].read_text(encoding="utf-8"), re.M)
    return {
        "skill": frontmatter_version(skill_text),
        "rows": [
            {"file": "skills/abs-business-system/SKILL.md", "version": frontmatter_version(skill_text),
             "role": "技能版本（以它为准）", "group": "release"},
            {"file": ".claude-plugin/plugin.json", "version": (plugin or {}).get("version"),
             "role": "Claude Code 插件", "group": "release"},
            {"file": "gemini-extension.json", "version": (gemini or {}).get("version"),
             "role": "Gemini CLI 扩展", "group": "release"},
            {"file": "skills/abs-business-system/scripts/abs.py", "version": script.group(1) if script else None,
             "role": "脚本自己的版本，作 User-Agent 发给服务端，单独编号", "group": "own"},
            {"file": "server.json", "version": (server or {}).get("version"),
             "role": "MCP 注册表条目，单独编号", "group": "own"},
        ],
    }


def code_checks(versions: dict[str, Any]) -> list[dict[str, Any]]:
    checks = []
    release = [r for r in versions["rows"] if r["group"] == "release"]
    found = {r["version"] for r in release}
    if len(found) == 1 and None not in found:
        checks.append(item("发布版本一致", "ok", f"SKILL.md、plugin.json、gemini-extension.json 都是 {found.pop()}"))
    else:
        detail = "；".join(f"{r['file']} = {r['version']}" for r in release)
        checks.append(item("发布版本一致", "fail", "三处版本号不一样，发版前要一起改", detail=detail))

    bad = []
    for rel in (".claude-plugin/plugin.json", ".claude-plugin/marketplace.json", "gemini-extension.json",
                "server.json", "skills/abs-business-system/config.json"):
        _, err = read_json_file(ROOT / rel)
        if err:
            bad.append(f"{rel}: {err}")
    checks.append(item("JSON 清单可解析", "fail" if bad else "ok",
                       "有文件解析失败" if bad else "5 个 JSON 文件都能解析", detail="\n".join(bad)))

    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    name = re.search(r"^name:\s*(\S+)", text.split("\n---", 1)[0], re.M)
    market, _ = read_json_file(ROOT / ".claude-plugin" / "marketplace.json")
    plugin, _ = read_json_file(ROOT / ".claude-plugin" / "plugin.json")
    names = {
        "SKILL.md name": name.group(1) if name else None,
        "目录名": SKILL_DIR.name,
        "plugin.json name": (plugin or {}).get("name"),
        "marketplace 插件名": ((market or {}).get("plugins") or [{}])[0].get("name"),
    }
    same = len(set(names.values())) == 1
    checks.append(item("技能名一致", "ok" if same else "fail",
                       f"都是 {SKILL}" if same else "名字对不上，安装命令会找不到插件",
                       detail="；".join(f"{k} = {v}" for k, v in names.items())))
    has_desc = bool(re.search(r"^description:", text, re.M))
    checks.append(item("SKILL.md 头部", "ok" if has_desc and name else "fail",
                       "name 和 description 都在" if has_desc and name else "缺 name 或 description"))

    script = SKILL_DIR / "scripts" / "abs.py"
    try:
        compile(script.read_text(encoding="utf-8"), str(script), "exec")
        out = subprocess.run([sys.executable, "-I", str(script), "--version"], capture_output=True, text=True,
                             timeout=15)
        ok = out.returncode == 0
        checks.append(item("abs.py 能运行", "ok" if ok else "fail", out.stdout.strip() or out.stderr.strip()))
    except SyntaxError as e:
        checks.append(item("abs.py 能运行", "fail", f"语法错误：{e}"))
    return checks


def git_status() -> dict[str, Any]:
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    sha, subject, when = git("log", "-1", "--format=%h%x00%s%x00%cI").split("\x00")
    dirty = [line for line in git("status", "--porcelain").splitlines() if line.strip()]
    out: dict[str, Any] = {"branch": branch, "head": sha, "subject": subject, "date": when, "dirty": len(dirty),
                           "dirty_files": dirty[:20]}
    try:
        remote = git("ls-remote", "origin", "refs/heads/main", timeout=20).split()
        remote_sha = remote[0] if remote else ""
    except (RuntimeError, subprocess.SubprocessError) as e:
        out["sync"] = item("与远端 main 同步", "warn", f"查不到远端：{e}")
        return out
    out["remote_main"] = remote_sha[:7]
    head_full = git("rev-parse", "HEAD")
    if remote_sha == head_full:
        out["sync"] = item("与远端 main 同步", "ok", "本地 HEAD 就是远端 main")
        return out
    try:
        git("cat-file", "-e", f"{remote_sha}^{{commit}}")
    except RuntimeError:
        out["sync"] = item("与远端 main 同步", "warn", "远端 main 有本地没有的提交，先 git fetch origin main")
        return out
    behind, ahead = git("rev-list", "--left-right", "--count", f"{remote_sha}...HEAD").split()
    status = "info" if behind == "0" else "warn"
    out["sync"] = item("与远端 main 同步", status, f"比远端 main 领先 {ahead} 个提交、落后 {behind} 个")
    return out


def github_repo(token: str) -> dict[str, Any]:
    r = gh(f"/repos/{REPO}", token)
    if r.status != 200:
        return {"error": r.error or f"HTTP {r.status}"}
    d = r.json()
    pulls = gh(f"/repos/{REPO}/pulls?state=open&per_page=100", token)
    topics = d.get("topics") or []
    return {
        "stars": d.get("stargazers_count"),
        "forks": d.get("forks_count"),
        "watchers": d.get("subscribers_count"),
        "open_issues": (d.get("open_issues_count") or 0) - (len(pulls.json() or []) if pulls.status == 200 else 0),
        "open_prs": len(pulls.json() or []) if pulls.status == 200 else None,
        "pushed_at": d.get("pushed_at"),
        "default_branch": d.get("default_branch"),
        "topics": topics,
        "url": d.get("html_url"),
    }


# ---------------------------------------------------------------- 2. 平台


def platform_github(local: str) -> dict[str, Any]:
    r = raw("skills/abs-business-system/SKILL.md")
    url = f"https://github.com/{REPO}"
    if r.status != 200:
        return item("GitHub 仓库", "fail", r.error or f"main 分支取不到 SKILL.md（HTTP {r.status}）", url=url,
                    latency=r.ms)
    v = frontmatter_version(r.text())
    status = compare(v, local)
    note = "main 上的版本和本地一致" if status == "ok" else f"main 上是 {v}，本地是 {local}：本地改动还没合并或已过时"
    return item("GitHub 仓库", status, note, url=url, latency=r.ms, version=v,
                install="npx skills add hypergraphdev/abs-skills",
                detail="npx skills、Claude Code、Gemini CLI 都从 main 分支装，这一项不通别的都装不上。")


def platform_claude(local: str) -> dict[str, Any]:
    url = f"https://github.com/{REPO}/blob/main/.claude-plugin/marketplace.json"
    m, p = raw(".claude-plugin/marketplace.json"), raw(".claude-plugin/plugin.json")
    market, plugin = m.json(), p.json()
    if not market or not plugin:
        return item("Claude Code 插件市场", "fail", "main 上的 marketplace.json 或 plugin.json 取不到或解析失败",
                    url=url, latency=m.ms)
    names = [x.get("name") for x in market.get("plugins") or []]
    if SKILL not in names:
        return item("Claude Code 插件市场", "fail", f"marketplace.json 里没有 {SKILL}", url=url, latency=m.ms)
    v = plugin.get("version")
    return item("Claude Code 插件市场", compare(v, local),
                "能装" if v == local else f"main 上的 plugin.json 是 {v}，本地是 {local}", url=url,
                latency=m.ms, version=v,
                install=f"/plugin marketplace add {REPO} → /plugin install {SKILL}@abs-skills")


def platform_gemini(local: str, topics: list[str]) -> dict[str, Any]:
    r = raw("gemini-extension.json")
    ext = r.json()
    if not ext:
        return item("Gemini CLI 扩展", "fail", "main 上的 gemini-extension.json 取不到或解析失败",
                    url=f"https://github.com/{REPO}", latency=r.ms)
    v = ext.get("version")
    gallery = http("https://geminicli.com/extensions.json", timeout=30)
    listed = None
    if gallery.status == 200:
        listed = next((x for x in gallery.json() or [] if x.get("fullName") == REPO), None)
    if gallery.status != 200:
        where, status = f"扩展库列表取不到（{gallery.error or gallery.status}）", "warn"
    elif listed:
        where, status = f"已收录在 geminicli.com 扩展库，排名 {listed.get('rank')}", compare(v, local)
    else:
        topic = "仓库已有 gemini-cli-extension 主题" if "gemini-cli-extension" in topics else \
            "仓库还没有 gemini-cli-extension 主题"
        where, status = f"能装，但 geminicli.com 扩展库里还没有（{topic}）", "warn"
    return item("Gemini CLI 扩展", status, where, url="https://geminicli.com/extensions/", latency=r.ms, version=v,
                install=f"gemini extensions install https://github.com/{REPO}")


def platform_skills_sh() -> dict[str, Any]:
    url = f"https://www.skills.sh/{REPO}/{SKILL}"
    r = http(url)
    missing = r.status == 404 or "isn’t available" in r.text() or "isn't available" in r.text()
    if r.status == 0:
        return item("skills.sh 目录", "warn", r.error, url=url, latency=r.ms)
    if missing:
        return item("skills.sh 目录", "warn", "目录里还没有这个技能（npx skills add 照样能装）", url=url, latency=r.ms,
                    detail="skills.sh 是 npx skills 的公开目录，有人用 npx skills add 安装后才会出现。")
    return item("skills.sh 目录", "ok", "目录里能找到", url=url, latency=r.ms)


def platform_clawhub(local: str) -> dict[str, Any]:
    url = f"https://clawhub.ai/{REPO.split('/')[0]}/skills/{SKILL}"
    r = http(f"https://clawhub.ai/api/v1/skills/{SKILL}")
    d = r.json() or {}
    skill = d.get("skill") or {}
    owner = (d.get("owner") or {}).get("handle")
    if r.status != 200 or not skill:
        return item("OpenClaw · ClawHub", "fail", r.error or f"取不到（HTTP {r.status}）", url=url, latency=r.ms)
    if owner and owner != REPO.split("/")[0]:
        return item("OpenClaw · ClawHub", "fail", f"同名技能属于 {owner}，不是你的", url=url, latency=r.ms)
    v = (d.get("latestVersion") or {}).get("version") or (skill.get("tags") or {}).get("latest")
    stats = skill.get("stats") or {}
    status = compare(v, local)
    return item("OpenClaw · ClawHub", status, "已发布最新版" if status == "ok" else f"ClawHub 上是 {v}，本地是 {local}",
                url=url, latency=r.ms, version=v, stats=stats,
                install=f"openclaw skills install @{REPO.split('/')[0]}/{SKILL}")


def platform_abs_zip(local: str, base: str) -> dict[str, Any]:
    url = f"{base}/api/agent/skill.zip"
    r = http(url, timeout=30)
    if r.status != 200:
        return item("ABS 官网下载包", "fail", r.error or f"HTTP {r.status}", url=url, latency=r.ms)
    try:
        z = zipfile.ZipFile(io.BytesIO(r.body))
        name = next(n for n in z.namelist() if n.endswith("SKILL.md"))
        v = frontmatter_version(z.read(name).decode("utf-8"))
    except (zipfile.BadZipFile, StopIteration, UnicodeDecodeError) as e:
        return item("ABS 官网下载包", "fail", f"下载到了但打不开：{e}", url=url, latency=r.ms)
    status = compare(v, local)
    return item("ABS 官网下载包", status,
                f"skill.zip 可下载（{len(r.body) // 1024} KB）" if status == "ok" else f"skill.zip 里是 {v}，本地是 {local}",
                url=url, latency=r.ms, version=v, install="手动下载解压到 Agent 的技能目录")


def platform_registry(server_version: str | None) -> dict[str, Any]:
    url = "https://registry.modelcontextprotocol.io/v0.1/servers?search=net.ucai/abs"
    r = http(url, timeout=40)
    servers = (r.json() or {}).get("servers") or []
    entry = next((s for s in servers if (s.get("server") or {}).get("name") == "net.ucai/abs"), None)
    if r.status != 200 or not entry:
        msg = r.error or (f"HTTP {r.status}" if r.status != 200 else "注册表里查不到 net.ucai/abs")
        return item("MCP 官方注册表", "warn" if r.status == 0 else "fail", msg, url=url, latency=r.ms)
    meta = (entry.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
    v = entry["server"].get("version")
    state = meta.get("status")
    status = "ok" if state == "active" and v == server_version else "warn"
    summary = f"已上架（{state}）" if v == server_version else f"注册表上是 {v}，server.json 是 {server_version}"
    return item("MCP 官方注册表", status, summary, url=url, latency=r.ms, version=v,
                install="claude.ai / ChatGPT 等在「连接器」里加 MCP 地址")


# ---------------------------------------------------------------- 3. MCP 连通性


def mcp_post(endpoint: str, token: str, payload: dict[str, Any], session: str = "",
             protocol: str = "") -> tuple[Resp, Any]:
    headers = {"Accept": "application/json, text/event-stream", "Authorization": f"Bearer {token}"}
    if session:
        headers["Mcp-Session-Id"] = session
    if protocol:
        headers["MCP-Protocol-Version"] = protocol
    r = http(endpoint, method="POST", headers=headers, body=payload, timeout=30)
    if "text/event-stream" in r.headers.get("content-type", ""):
        for line in r.text().splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if msg.get("id") == payload.get("id"):
                    return r, msg
        return r, None
    return r, r.json()


def mcp_checks(base: str, token: str) -> list[dict[str, Any]]:
    endpoint = f"{base}/api/agent/mcp"
    out = []

    r = http(endpoint, method="OPTIONS", headers={"Origin": "https://claude.ai",
                                                  "Access-Control-Request-Method": "POST"})
    out.append(item("浏览器跨域预检", "ok" if r.status in (200, 204) else "fail",
                    f"HTTP {r.status}" if r.status else r.error, latency=r.ms, url=endpoint))

    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "abs-dashboard", "version": "1.0"}}}
    r = http(endpoint, method="POST", headers={"Accept": "application/json, text/event-stream"}, body=init)
    challenge = r.headers.get("www-authenticate", "")
    meta_url = (re.search(r'resource_metadata="([^"]+)"', challenge) or [None, None])[1]
    out.append(item("未登录时要求授权", "ok" if r.status == 401 and meta_url else "fail",
                    "返回 401 并指明授权元数据地址" if r.status == 401 and meta_url else
                    (r.error or f"HTTP {r.status}，没有给出 resource_metadata"),
                    latency=r.ms, detail=challenge))

    auth_server = None
    if meta_url:
        r = http(meta_url)
        d = r.json() or {}
        auth_server = (d.get("authorization_servers") or [None])[0]
        out.append(item("受保护资源元数据", "ok" if auth_server else "fail",
                        f"授权服务器 {auth_server}" if auth_server else f"HTTP {r.status}，缺 authorization_servers",
                        latency=r.ms, url=meta_url))
    if auth_server:
        url = f"{auth_server.rstrip('/')}/.well-known/oauth-authorization-server"
        r = http(url)
        d = r.json() or {}
        dcr = bool(d.get("registration_endpoint"))
        pkce = "S256" in (d.get("code_challenge_methods_supported") or [])
        good = r.status == 200 and dcr and pkce
        out.append(item("OAuth 授权服务器", "ok" if good else "warn",
                        "支持动态注册和 PKCE，claude.ai、ChatGPT 连接器能直接登录" if good else
                        f"HTTP {r.status}；动态注册 {'有' if dcr else '无'}；PKCE S256 {'有' if pkce else '无'}",
                        latency=r.ms, url=url))

    q = urllib.parse.urlencode({"q": "5S", "limit": 3, "lang": "zh"})
    r = http(f"{base}/api/agent/v1/search?{q}")
    hits = len((r.json() or {}).get("results") or [])
    out.append(item("技能接口 · 免登录检索", "ok" if r.status == 200 and hits else "fail",
                    f"返回 {hits} 条" if r.status == 200 else (r.error or f"HTTP {r.status}"), latency=r.ms))

    if not token:
        hint = "设置环境变量 ABS_TOKEN，或先运行 abs.py login <令牌>，再刷新"
        for name in ("技能接口 · 令牌身份", "MCP · 登录后握手", "MCP · 工具列表"):
            out.append(item(name, "skip", "没有 ABS 令牌，跳过", detail=hint))
        return out

    r = http(f"{base}/api/agent/v1/me", headers={"Authorization": f"Bearer {token}"})
    me = r.json() or {}
    user = me.get("user") or {}
    can = ", ".join(k for k, v in (me.get("can") or {}).items() if v)
    out.append(item("技能接口 · 令牌身份", "ok" if r.status == 200 else "fail",
                    f"{user.get('display_name')}（{user.get('role')}），可用：{can}" if r.status == 200 else
                    (r.error or f"HTTP {r.status}：令牌无效、被撤销或过期"), latency=r.ms))

    r, msg = mcp_post(endpoint, token, init)
    result = (msg or {}).get("result") or {}
    session = r.headers.get("mcp-session-id", "")
    protocol = result.get("protocolVersion", "")
    info = result.get("serverInfo") or {}
    if r.status != 200 or not result:
        out.append(item("MCP · 登录后握手", "fail", r.error or f"HTTP {r.status}", latency=r.ms,
                        detail=r.text()[:300]))
        return out
    out.append(item("MCP · 登录后握手", "ok",
                    f"{info.get('name')} {info.get('version') or ''}，协议 {protocol}".strip(), latency=r.ms))

    mcp_post(endpoint, token, {"jsonrpc": "2.0", "method": "notifications/initialized"}, session, protocol)
    r, msg = mcp_post(endpoint, token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session, protocol)
    tools = [t.get("name") for t in ((msg or {}).get("result") or {}).get("tools") or []]
    if not tools:
        out.append(item("MCP · 工具列表", "fail", r.error or f"HTTP {r.status}，没拿到工具", latency=r.ms))
    else:
        status = "ok" if len(tools) == EXPECTED_MCP_TOOLS else "warn"
        out.append(item("MCP · 工具列表", status,
                        f"{len(tools)} 个工具" + ("" if status == "ok" else f"（README 写的是 {EXPECTED_MCP_TOOLS} 个）"),
                        latency=r.ms, detail="、".join(tools), tools=len(tools)))
    if session:
        headers = {"Authorization": f"Bearer {token}", "Mcp-Session-Id": session}
        http(endpoint, method="DELETE", headers=headers, timeout=10)
    return out


# ---------------------------------------------------------------- 4. 下载与访问量


def github_traffic(token: str) -> dict[str, Any]:
    if not token:
        return {"error": "没有 GitHub 令牌：设置 GITHUB_TOKEN，或先 gh auth login"}
    views, clones = gh(f"/repos/{REPO}/traffic/views", token), gh(f"/repos/{REPO}/traffic/clones", token)
    if views.status != 200 or clones.status != 200:
        code = views.status if views.status != 200 else clones.status
        why = "令牌没有这个仓库的推送权限" if code in (401, 403, 404) else (views.error or f"HTTP {code}")
        return {"error": f"访问量取不到：{why}"}
    refs = gh(f"/repos/{REPO}/traffic/popular/referrers", token)
    v, c = views.json(), clones.json()
    return {
        "views": {"count": v.get("count"), "uniques": v.get("uniques"), "days": day_rows(v.get("views"))},
        "clones": {"count": c.get("count"), "uniques": c.get("uniques"), "days": day_rows(c.get("clones"))},
        "referrers": refs.json() if refs.status == 200 else [],
    }


def day_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [{"date": r["timestamp"][:10], "count": r.get("count", 0), "uniques": r.get("uniques", 0)}
            for r in rows or []]


def state_dir() -> Path:
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"])
    else:
        root = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = root / "abs-dashboard"
    path.mkdir(parents=True, exist_ok=True)
    return path


def remember(snapshot: dict[str, Any], traffic: dict[str, Any]) -> dict[str, Any]:
    """计数快照追加到 history.jsonl；GitHub 每日访问量按日期合并进 traffic.json，突破 14 天的窗口。"""
    folder = state_dir()
    with (folder / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
    history = []
    for line in (folder / "history.jsonl").read_text(encoding="utf-8").splitlines():
        try:
            history.append(json.loads(line))
        except ValueError:
            continue

    store_path = folder / "traffic.json"
    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        store = {}
    for kind in ("views", "clones"):
        days = store.setdefault(kind, {})
        for row in (traffic.get(kind) or {}).get("days") or []:
            days[row["date"]] = {"count": row["count"], "uniques": row["uniques"]}  # 当天数字会涨，用最新的
    if traffic.get("views"):
        store_path.write_text(json.dumps(store, ensure_ascii=False, indent=1), encoding="utf-8")
    series = {kind: [{"date": d, **v} for d, v in sorted((store.get(kind) or {}).items())][-90:]
              for kind in ("views", "clones")}
    return {"snapshots": history[-200:], "daily": series, "folder": str(folder)}


# ---------------------------------------------------------------- 汇总


def collect(save: bool = True) -> dict[str, Any]:
    started = time.monotonic()
    versions = local_versions()
    local = versions["skill"]
    server_version = next(r["version"] for r in versions["rows"] if r["file"] == "server.json")
    gh_token = github_token()
    base, abs_token = abs_settings()

    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        jobs = {
            "git": pool.submit(guarded, "git", git_status),
            "repo": pool.submit(guarded, "repo", lambda: github_repo(gh_token)),
            "traffic": pool.submit(guarded, "traffic", lambda: github_traffic(gh_token)),
            "mcp": pool.submit(guarded, "MCP", lambda: mcp_checks(base, abs_token)),
            "p_github": pool.submit(guarded, "GitHub 仓库", lambda: platform_github(local)),
            "p_claude": pool.submit(guarded, "Claude Code 插件市场", lambda: platform_claude(local)),
            "p_skills": pool.submit(guarded, "skills.sh 目录", platform_skills_sh),
            "p_clawhub": pool.submit(guarded, "OpenClaw · ClawHub", lambda: platform_clawhub(local)),
            "p_zip": pool.submit(guarded, "ABS 官网下载包", lambda: platform_abs_zip(local, base)),
            "p_registry": pool.submit(guarded, "MCP 官方注册表", lambda: platform_registry(server_version)),
        }
        done = {k: f.result() for k, f in jobs.items()}
        repo = done["repo"] if "error" not in done["repo"] and "status" not in done["repo"] else {}
        gemini = guarded("Gemini CLI 扩展", lambda: platform_gemini(local, repo.get("topics") or []))

    mcp = done["mcp"] if isinstance(done["mcp"], list) else [done["mcp"]]
    platforms = [done["p_github"], done["p_claude"], gemini, done["p_skills"], done["p_clawhub"], done["p_zip"],
                 done["p_registry"]]
    clawhub = done["p_clawhub"].get("stats") or {}
    traffic = done["traffic"] if "status" not in done["traffic"] else {"error": done["traffic"]["summary"]}
    git_info = done["git"] if "status" not in done["git"] else {"error": done["git"]["summary"]}
    tools = next((c.get("tools") for c in mcp if c.get("tools")), None)

    now = dt.datetime.now(dt.timezone.utc).astimezone()
    snapshot = {
        "t": now.isoformat(timespec="seconds"),
        "clawhub_downloads": clawhub.get("downloads"),
        "clawhub_installs": clawhub.get("installs"),
        "clawhub_stars": clawhub.get("stars"),
        "github_stars": repo.get("stars"),
        "github_forks": repo.get("forks"),
        "github_clones_14d": (traffic.get("clones") or {}).get("count"),
        "github_views_14d": (traffic.get("views") or {}).get("count"),
    }
    history = remember(snapshot, traffic) if save else {"snapshots": [snapshot], "daily": {}, "folder": ""}

    return {
        "generated_at": snapshot["t"],
        "took_ms": ms(started),
        "repo": REPO,
        "local_version": local,
        "tokens": {"github": bool(gh_token), "abs": bool(abs_token)},
        "abs_site": base,
        "code": {"git": git_info, "versions": versions["rows"], "checks": code_checks(versions), "github": repo,
                 "github_error": done["repo"].get("error") or done["repo"].get("summary")
                 if not repo else None},
        "platforms": platforms,
        "mcp": {"endpoint": f"{base}/api/agent/mcp", "checks": mcp, "tools": tools},
        "traffic": {
            "clawhub": clawhub,
            "github": traffic,
            "history": history,
            "unavailable": [
                {"name": "ABS 官网 skill.zip 下载量、MCP 连接数",
                 "why": "只记在 ABS 服务端，没有公开接口；要看得在 ucai.net 后台加统计接口。"},
                {"name": "skills.sh 安装数", "why": "目录收录这个技能之后才有。"},
                {"name": "Claude Code 插件、Gemini 扩展安装数", "why": "两边都不对外公布安装数，只能从 GitHub 克隆数侧面看。"},
            ],
        },
    }


# ---------------------------------------------------------------- 页面与服务


def page(data: dict[str, Any] | None = None) -> bytes:
    html = (HERE / "index.html").read_text(encoding="utf-8")
    if data is not None:
        blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
        html = html.replace("<!--DATA-->", f"<script>window.__ABS_STATUS__ = {blob};</script>", 1)
    return html.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    lock = threading.Lock()
    cache: tuple[float, bytes] = (0.0, b"")

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.reply(200, "text/html; charset=utf-8", page())
        elif path == "/api/status":
            with Handler.lock:  # 同时点两次刷新只跑一遍
                if time.monotonic() - Handler.cache[0] > 5:
                    body = json.dumps(collect(), ensure_ascii=False).encode("utf-8")
                    Handler.cache = (time.monotonic(), body)
                body = Handler.cache[1]
            self.reply(200, "application/json; charset=utf-8", body)
        else:
            self.reply(404, "text/plain; charset=utf-8", "没有这个页面".encode("utf-8"))

    def reply(self, code: int, kind: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write(f"[{self.log_date_time_string()}] {fmt % args}\n")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ABS Skill 本地统计页")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p.add_argument("--html", metavar="PATH", help="写一份静态快照 HTML 后退出")
    p.add_argument("--json", action="store_true", help="打印原始数据后退出")
    p.add_argument("--no-save", action="store_true", help="这次不记快照")
    args = p.parse_args(argv)

    if args.json or args.html:
        data = collect(save=not args.no_save)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        if args.html:
            Path(args.html).write_bytes(page(data))
            print(f"已写入 {Path(args.html).resolve()}", file=sys.stderr)
        return 0

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # 只监听本机
    url = f"http://127.0.0.1:{args.port}/"
    print(f"ABS 统计页：{url}（Ctrl+C 退出）", file=sys.stderr)
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
