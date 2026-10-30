#!/usr/bin/env python3
"""
Push files to the GitLab redirect repository, in one commit, only if they changed.

    python arks/push_to_gitlab.py redirects.csv:redirects.csv arks/ark_master_list.csv:ark_master_list.csv

Each argument is LOCAL_PATH:PATH_IN_GITLAB. Settings come from environment
variables (settings.env and the GITLAB_TOKEN secret in the workflows):

    GITLAB_URL       https://git.uni.edu
    GITLAB_PROJECT   rod-library/redirects
    GITLAB_BRANCH    main
    GITLAB_TOKEN     project access token: Developer role, api scope

Uses GitLab's Commits API (POST /projects/:id/repository/commits), so several files
change in a single commit, and compares each file's SHA-256 with what GitLab has
first, so an unchanged file never makes an empty commit. Uses only the standard
library; the token is never printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    if not value:
        sys.exit(f"error: set {name}")
    return value


class GitLab:
    def __init__(self) -> None:
        self.url = env("GITLAB_URL").rstrip("/")
        self.project = env("GITLAB_PROJECT")
        self.branch = env("GITLAB_BRANCH", "main")
        self.token = env("GITLAB_TOKEN")
        self.api = f"{self.url}/api/v4/projects/{urllib.parse.quote(self.project, safe='')}"

    def request(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            self.api + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"PRIVATE-TOKEN": self.token, "Content-Type": "application/json",
                     "User-Agent": "rodlibrary-redirects-push"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            sys.exit(f"error: can't reach {self.url}: {exc.reason}. GitHub's servers need to "
                     "reach it over the internet (campus-only GitLab needs a self-hosted runner).")

    def remote_sha256(self, path: str) -> str | None:
        """SHA-256 of the file in GitLab, or None if it doesn't exist there."""
        code, data = self.request("GET", f"/repository/files/{urllib.parse.quote(path, safe='')}"
                                         f"?ref={urllib.parse.quote(self.branch, safe='')}")
        if code == 200:
            return data.get("content_sha256")
        if code == 404:
            return None
        explain(code, data, f"reading {path}")


def explain(code: int, data, doing: str) -> None:
    hints = {
        401: "GITLAB_TOKEN is wrong, expired or revoked",
        403: "the token can't write to this project: it needs the Developer role (or higher) "
             "and the api scope, and Developers must be allowed to push to the branch",
        404: "the project or branch doesn't exist, or the token can't see it: check "
             "GITLAB_PROJECT and GITLAB_BRANCH",
    }
    detail = data if isinstance(data, str) else json.dumps(data)
    sys.exit(f"error: GitLab answered HTTP {code} while {doing}. "
             f"{hints.get(code, 'See the message below.')}\n{detail[:500]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+", help="LOCAL_PATH:PATH_IN_GITLAB")
    ap.add_argument("--message", default="Update ARK redirects from GitHub (rodlibrary/Redirects)")
    ap.add_argument("--dry-run", action="store_true", help="report what would change, push nothing")
    args = ap.parse_args()

    gl = GitLab()
    actions, unchanged = [], []
    for spec in args.files:
        local, _, remote = spec.partition(":")
        remote = remote or os.path.basename(local)
        with open(local, "rb") as fh:
            content = fh.read()
        have = gl.remote_sha256(remote)
        if have == hashlib.sha256(content).hexdigest():
            unchanged.append(remote)
            continue
        actions.append({"action": "update" if have else "create", "file_path": remote,
                        "encoding": "base64", "content": base64.b64encode(content).decode()})

    for r in unchanged:
        print(f"{r}: unchanged in GitLab")
    if not actions:
        print("Nothing to push.")
        return 0
    for a in actions:
        print(f"{a['file_path']}: will {a['action']}")
    if args.dry_run:
        return 0

    message = args.message
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        message += f"\n\nFrom {os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/" \
                   f"{os.environ.get('GITHUB_REPOSITORY', '')}/commit/{sha}"
    code, data = gl.request("POST", "/repository/commits",
                            {"branch": gl.branch, "commit_message": message, "actions": actions})
    if code not in (200, 201):
        explain(code, data, "committing")
    print(f"Pushed {len(actions)} file(s) to {gl.project} ({gl.branch}): {data.get('web_url', '')}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"Pushed {', '.join(a['file_path'] for a in actions)} to "
                     f"[{gl.project}]({data.get('web_url', gl.url)}).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
