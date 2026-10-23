#!/usr/bin/env python3
"""CI preflight for the canjasonwu/UniXNU fork.

Checks, before a workflow run is triggered:
  * repository and Actions are enabled and dispatchable-looking,
  * the fork policy warning (GitHub disables workflows on forks to save compute),
  * no run of the same workflow is already queued or running,
  * the current head commit has not already been built by that workflow,
  * the run rate stays inside a budget (runs per hour and minimum gap).

Default is check-only. Pass --dispatch to trigger the workflow after the checks
pass. A dispatch that GitHub rejects with 422 (for example "Actions has been
disabled for this repository") is reported with the exact recovery step instead
of crashing.

Exit codes: 0 ok, 2 blocked by a check, 3 dispatch failed.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

OWNER = "canjasonwu"
REPO = "UniXNU"
API = "https://api.github.com"
TOKEN_ENV = "GH_TOKEN"
TOKEN_PATHS = [
    r"C:\Users\Jason Wu\dsh\fork-add\tools\.gh-token",
]
DEFAULT_WORKFLOW = ".github/workflows/build-fuxi-kernel.yml"


def find_token():
    value = os.environ.get(TOKEN_ENV, "").strip()
    if value:
        return value
    for path in TOKEN_PATHS:
        p = Path(path)
        if p.is_file():
            return p.read_text(encoding="utf-8").strip()
    sys.exit("no token: set GH_TOKEN or create " + TOKEN_PATHS[0])


class Api:
    def __init__(self, token):
        self.token = token

    def call(self, path, method="GET", payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(API + path, data=data, method=method)
        request.add_header("Authorization", "Bearer " + self.token)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        last = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read().decode("utf-8", "replace")
                    return response.status, (json.loads(body) if body.strip() else None)
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", "replace")
                try:
                    return error.code, json.loads(body)
                except ValueError:
                    return error.code, body
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                last = error
                time.sleep(3 + attempt * 3)
        return 0, {"error": "network unavailable: %s" % last}


def parse_time(value):
    return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")) - time.timezone


def human(seconds):
    if seconds < 0:
        return "0s"
    minutes, seconds = divmod(int(seconds), 60)
    return "%dm%02ds" % (minutes, seconds) if minutes else "%ds" % seconds


def main():
    parser = argparse.ArgumentParser(description="Preflight checks before dispatching a workflow.")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW,
                        help="workflow file name or path (default: %(default)s)")
    parser.add_argument("--dispatch", action="store_true", help="dispatch after all checks pass")
    parser.add_argument("--force", action="store_true", help="ignore duplicate/rate checks (not repo state)")
    parser.add_argument("--allow-same-sha", action="store_true",
                        help="allow a dispatch when the head commit was already built")
    parser.add_argument("--max-runs-per-hour", type=int, default=3,
                        help="budget: runs of this workflow in the last 60 minutes (default: %(default)s)")
    parser.add_argument("--min-gap-min", type=float, default=8.0,
                        help="budget: minutes since the last run (default: %(default)s)")
    args = parser.parse_args()

    api = Api(find_token())
    problems = []
    warnings = []

    def ok(message):
        print("OK    " + message)

    def warn(message):
        warnings.append(message)
        print("WARN  " + message)

    def block(message):
        problems.append(message)
        print("BLOCK " + message)

    status, repo = api.call("/repos/%s/%s" % (OWNER, REPO))
    if status != 200:
        print("BLOCK cannot read repository: HTTP %s %s" % (status, str(repo.get("error") if isinstance(repo, dict) else repo)[:200]))
        return 2
    ok("repository %s/%s reachable" % (OWNER, REPO))
    if repo.get("archived") or repo.get("disabled"):
        block("repository is archived or disabled")
    if repo.get("fork"):
        warn("this is a fork: GitHub may disable its workflows to save compute; "
             "a 422 at dispatch means re-enable them on the Actions tab")

    status, permissions = api.call("/repos/%s/%s/actions/permissions" % (OWNER, REPO))
    if status != 200:
        warn("cannot read Actions permissions: HTTP %s" % status)
    elif not permissions.get("enabled"):
        block("Actions are disabled for this repository (permissions.enabled=false)")
    else:
        ok("Actions permissions report enabled (allowed_actions=%s)" % permissions.get("allowed_actions"))

    status, workflows = api.call("/repos/%s/%s/actions/workflows" % (OWNER, REPO))
    if status != 200:
        block("cannot list workflows: HTTP %s" % status)
        target = None
    else:
        want = args.workflow.replace("\\", "/")
        target = None
        for item in workflows.get("workflows", []):
            path = item.get("path", "")
            if path == want or path.endswith("/" + want.split("/")[-1]) or item.get("name") == args.workflow:
                target = item
                break
        if target is None:
            block("workflow not found on main: %s" % args.workflow)
        elif target.get("state") != "active":
            block("workflow %s is not active (state=%s)" % (target.get("path"), target.get("state")))
        else:
            ok("workflow %s active (id %s)" % (target.get("path"), target.get("id")))

    status, branch = api.call("/repos/%s/%s/branches/main" % (OWNER, REPO))
    if status != 200:
        block("cannot read main branch: HTTP %s" % status)
        head_sha = None
    else:
        head_sha = branch["commit"]["sha"]
        ok("main head is %s (%s)" % (head_sha[:10], branch["commit"]["commit"]["message"].splitlines()[0][:60]))

    if target is not None:
        run_path = "/repos/%s/%s/actions/workflows/%s/runs?per_page=30" % (OWNER, REPO, target["id"])
        status, runs = api.call(run_path)
        if status != 200:
            block("cannot list runs: HTTP %s" % status)
        else:
            items = runs.get("workflow_runs", [])
            active = [r for r in items if r.get("status") in ("queued", "in_progress", "waiting", "pending")]
            if active and not args.force:
                block("a run of this workflow is already %s: %s" % (active[0]["status"], active[0]["html_url"]))
            else:
                ok("no queued or running run of this workflow")
            if items:
                last = items[0]
                ok("last run %s: %s/%s at %s on %s" % (
                    last["id"], last["status"], last.get("conclusion") or "-",
                    last["created_at"], last["head_sha"][:10]))
                if head_sha and last.get("head_sha") == head_sha and not (args.allow_same_sha or args.force):
                    block("the current head %s was already built by this workflow; "
                          "push a change or pass --allow-same-sha" % head_sha[:10])
                age = time.time() - parse_time(last["created_at"])
                if age < args.min_gap_min * 60 and not args.force:
                    block("last run started %s ago (minimum gap %.0f min)" % (human(age), args.min_gap_min))
                hour = [r for r in items if time.time() - parse_time(r["created_at"]) < 3600]
                if len(hour) >= args.max_runs_per_hour and not args.force:
                    block("%d runs of this workflow in the last hour (budget %d)" % (len(hour), args.max_runs_per_hour))
                else:
                    ok("rate budget: %d run(s) in the last hour, %d allowed" % (len(hour), args.max_runs_per_hour))

    if problems:
        print("\nPREFLIGHT BLOCKED (%d problem(s)); nothing was dispatched." % len(problems))
        return 2
    if warnings:
        print("\nPREFLIGHT OK with %d warning(s)." % len(warnings))
    else:
        print("\nPREFLIGHT OK.")
    if not args.dispatch:
        print("check-only: pass --dispatch to trigger %s" % args.workflow)
        return 0

    status, body = api.call(
        "/repos/%s/%s/actions/workflows/%s/dispatches" % (OWNER, REPO, args.workflow),
        method="POST", payload={"ref": "main"})
    if status in (204, 201, 200):
        print("DISPATCHED %s on main" % args.workflow)
        return 0
    print("DISPATCH FAILED: HTTP %s %s" % (status, str(body)[:300]))
    if status == 422 and "disabled" in str(body).lower():
        print("recovery: open https://github.com/%s/%s/actions and press "
              "'I understand my workflows, go ahead and re-enable them'." % (OWNER, REPO))
    return 3


if __name__ == "__main__":
    sys.exit(main())
