#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""通过 GitHub REST API 上传仓库并开启 Pages。

为什么不用 git push：本环境 github.com 的 git 协议被 reset，
但 api.github.com 可达，因此走 Git Data API 一次性提交全部文件。

Token 只从环境变量读取，不接受命令行参数，避免出现在进程列表与历史记录中。

用法：
    $env:GITHUB_TOKEN = "ghp_xxx"
    python scripts/upload-github.py --repo zrcut-cnc --public

环境变量：
    GITHUB_TOKEN   必需，需 repo 权限（classic）或 Contents+Administration 写权限（fine-grained）

退出码：
    0 成功；1 失败
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = "https://api.github.com"


class GitHubError(RuntimeError):
    """GitHub API 调用失败。"""


def api(
    method: str,
    path: str,
    token: str,
    body: dict | None = None,
    *,
    expect: tuple[int, ...] = (200, 201, 204),
    retries: int = 5,
    timeout: float = 90.0,
) -> dict:
    """调用 GitHub REST API，带指数退避重试。

    Args:
        method: HTTP 方法。
        path: 以 / 开头的路径。
        token: 访问令牌。
        body: 请求体。
        expect: 可接受的状态码。
        retries: 网络类失败的最大重试次数。
        timeout: 单次请求超时秒数。

    Returns:
        解析后的响应体；204 返回空字典。

    Raises:
        GitHubError: 状态码不在 expect 内，或重试耗尽。
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_exc: Exception | None = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "zrcut-uploader",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code >= 500 or exc.code == 429:
                last_exc = GitHubError(f"{method} {path} -> HTTP {exc.code}: {detail[:300]}")
                if attempt < retries:
                    wait = min(2 ** attempt, 15)
                    print(f"       HTTP {exc.code}，{wait}s 后重试（{attempt + 1}/{retries}）")
                    time.sleep(wait)
                    continue
            raise GitHubError(f"{method} {path} -> HTTP {exc.code}: {detail[:400]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = GitHubError(f"{method} {path} -> 网络错误: {exc}")
            if attempt < retries:
                wait = min(2 ** attempt, 15)
                print(f"       网络超时，{wait}s 后重试（{attempt + 1}/{retries}）")
                time.sleep(wait)
                continue
            raise last_exc from exc

    raise last_exc or GitHubError(f"{method} {path} -> 未知失败")


def git(*args: str) -> str:
    """执行 git 命令。"""
    out = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    if out.returncode != 0:
        raise GitHubError(f"git {' '.join(args)} 失败: {out.stderr[:300]}")
    return out.stdout.strip()


def tracked_files() -> list[Path]:
    """列出 git 跟踪的全部文件。

    使用 core.quotepath=false 关闭八进制转义，并以 NUL 分隔，
    保证中文文件名与含空格文件名都能被正确还原。
    """
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z"],
        cwd=ROOT,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise GitHubError(
            f"git ls-files 失败: {proc.stderr.decode('utf-8', 'replace')[:300]}"
        )
    raw = proc.stdout.decode("utf-8", "replace")
    names = [n for n in raw.split("\x00") if n]
    return [ROOT / n for n in names]


def main() -> int:
    """主入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="zrcut-cnc", help="仓库名")
    parser.add_argument("--public", action="store_true", help="创建公开仓库")
    parser.add_argument("--no-pages", action="store_true", help="不开启 GitHub Pages")
    parser.add_argument("--pages-path", default="/docs", help="Pages 源目录")
    parser.add_argument("--branch", default="main", help="主分支名")
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        # 支持从暂存文件读取（供凭据管理器提取后使用，避免在进程列表暴露）
        token_file = os.environ.get("GITHUB_TOKEN_FILE", "").strip()
        if token_file and os.path.exists(token_file):
            token = open(token_file, encoding="utf-8").read().strip()
    if not token:
        print("[错误] 未设置 GITHUB_TOKEN 环境变量。", file=sys.stderr)
        print('       用法: $env:GITHUB_TOKEN = "ghp_xxx"; python scripts/upload-github.py', file=sys.stderr)
        print('       或:   $env:GITHUB_TOKEN_FILE = "路径"; python scripts/upload-github.py', file=sys.stderr)
        return 1

    try:
        # 1) 校验身份
        me = api("GET", "/user", token)
        login = me["login"]
        print(f"[1/6] 已认证: {login}")

        # 2) 创建仓库（已存在则复用）
        try:
            repo = api(
                "POST", "/user/repos", token,
                {
                    "name": args.repo,
                    "description": "智刃工艺 ZR-CUT：CNC 切削工艺参数智能推荐系统（工业级可运行实现）",
                    "private": not args.public,
                    "has_issues": True,
                    "has_wiki": False,
                    "auto_init": False,
                },
            )
            print(f"[2/6] 仓库已创建: {repo['full_name']}")
        except GitHubError as exc:
            if "422" in str(exc) and "already exists" in str(exc):
                repo = api("GET", f"/repos/{login}/{args.repo}", token)
                print(f"[2/6] 仓库已存在，复用: {repo['full_name']}")
            else:
                raise

        owner = repo["owner"]["login"]
        full = f"{owner}/{args.repo}"

        # 空仓库无法直接创建 blob，必须先用 Contents API 建立初始提交
        base_tree_sha = None
        base_commit_sha = None
        try:
            ref = api("GET", f"/repos/{full}/git/ref/heads/{args.branch}", token)
            base_commit_sha = ref["object"]["sha"]
            base_commit = api("GET", f"/repos/{full}/git/commits/{base_commit_sha}", token)
            base_tree_sha = base_commit["tree"]["sha"]
            print(f"[3/7] 仓库已有提交，基于 {base_commit_sha[:12]} 追加")
        except GitHubError:
            print(f"[3/7] 仓库为空，先用初始 README 建立首次提交")
            seed = (
                "# zrcut-cnc\n\n"
                "智刃工艺 ZR-CUT · CNC 切削工艺参数智能推荐系统\n"
            ).encode("utf-8")
            api(
                "PUT", f"/repos/{full}/contents/README.md", token,
                {
                    "message": "chore: 初始化仓库",
                    "content": base64.b64encode(seed).decode("ascii"),
                    "branch": args.branch,
                },
            )
            ref = api("GET", f"/repos/{full}/git/ref/heads/{args.branch}", token)
            base_commit_sha = ref["object"]["sha"]
            base_commit = api("GET", f"/repos/{full}/git/commits/{base_commit_sha}", token)
            base_tree_sha = base_commit["tree"]["sha"]

        # 4) 逐个文件创建 blob
        files = tracked_files()
        print(f"[4/7] 上传 {len(files)} 个文件…")
        entries = []
        for i, path in enumerate(files, 1):
            rel = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            blob = api(
                "POST", f"/repos/{full}/git/blobs", token,
                {
                    "content": base64.b64encode(content).decode("ascii"),
                    "encoding": "base64",
                },
            )
            entries.append(
                {"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]}
            )
            if i % 10 == 0 or i == len(files):
                print(f"       {i}/{len(files)} {rel}")

        # 5) 创建 tree（基于初始 tree，保留其内容）
        tree_body: dict = {"tree": entries}
        if base_tree_sha:
            tree_body["base_tree"] = base_tree_sha
        tree = api("POST", f"/repos/{full}/git/trees", token, tree_body)
        print(f"[5/7] tree 已创建: {tree['sha'][:12]}")

        # 6) 创建 commit 并更新分支引用
        commit_msg = git("log", "-1", "--format=%B") or "feat: 智刃工艺 ZR-CUT v1.0.0"
        commit_body: dict = {"message": commit_msg, "tree": tree["sha"]}
        if base_commit_sha:
            commit_body["parents"] = [base_commit_sha]
        commit = api("POST", f"/repos/{full}/git/commits", token, commit_body)
        print(f"[6/7] commit 已创建: {commit['sha'][:12]}")

        api(
            "PATCH", f"/repos/{full}/git/refs/heads/{args.branch}", token,
            {"sha": commit["sha"], "force": True},
        )
        try:
            api("PATCH", f"/repos/{full}", token, {"default_branch": args.branch})
        except GitHubError:
            pass
        print(f"       分支 {args.branch} 已指向 {commit['sha'][:12]}")

        # 7) 开启 Pages
        pages_url = None
        if not args.no_pages:
            try:
                api(
                    "POST", f"/repos/{full}/pages", token,
                    {
                        "source": {
                            "branch": args.branch,
                            "path": args.pages_path,
                        }
                    },
                )
                print(f"[7/7] GitHub Pages 已开启，源: {args.branch} {args.pages_path}")
            except GitHubError as exc:
                if "409" in str(exc):
                    print("[7/7] Pages 已存在，跳过创建")
                else:
                    print(f"[7/7] 开启 Pages 失败（可稍后在 Settings 手动开启）: {exc}")
            for _ in range(20):
                try:
                    info = api("GET", f"/repos/{full}/pages", token)
                    if info.get("html_url"):
                        pages_url = info["html_url"]
                        break
                except GitHubError:
                    pass
                time.sleep(2)

        # 汇总
        print()
        print("=" * 70)
        print("上传完成")
        print("=" * 70)
        print(f"仓库地址   https://github.com/{full}")
        if pages_url:
            print(f"演示链接   {pages_url}")
        else:
            pages = f"https://{owner}.github.io/{args.repo}/"
            print(f"演示链接   {pages}")
            print("           （Pages 首次构建约需 1-2 分钟）")
        print(f"动作地址   https://github.com/{full}/actions")
        print("=" * 70)
        return 0

    except GitHubError as exc:
        print(f"[失败] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
