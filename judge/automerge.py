#!/usr/bin/env python3
"""Merge a judged nightly pull request (called by .github/workflows/automerge.yml).

Conditions, all required:
  - exactly one open pull request from HEAD_BRANCH (auto/*) in this repository,
    whose head is still HEAD_SHA (no pushes since the judge ran);
  - protected paths and dependency allow-list pass (re-run here from main);
  - every required CI job on HEAD_SHA concluded 'success';
  - it was judged against the current main, and in at most MAX_ATTEMPTS runs.
Merges with squash, pinned to HEAD_SHA. Exits non-zero (and does not merge)
otherwise. Branch protection rules on main still apply to the merge.
"""

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REQUIRED_CHECKS = {
    "fmt, clippy, tests, deep perft",
    "judge self-test",
    "judge (endgame, identical, speed or strength)",
}
HERE = os.path.dirname(os.path.abspath(__file__))
MAX_ATTEMPTS = 3


def api(method, path, body=None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['REPO']}{path}",
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read() or b"{}")


def fail(msg):
    print(f"not merging: {msg}")
    sys.exit(1)


def main():
    head_sha = os.environ["HEAD_SHA"]
    branch = os.environ["HEAD_BRANCH"]
    owner = os.environ["REPO"].split("/")[0]
    if not branch.startswith("auto/"):
        fail("not an auto/ branch")

    prs = api("GET", f"/pulls?state=open&head={owner}:{branch}")
    if not prs:
        print("no open pull request (already merged or closed)")
        return
    if len(prs) != 1:
        fail(f"expected one open pull request for {branch}, found {len(prs)}")
    pr = prs[0]
    if pr["head"]["sha"] != head_sha:
        fail("the pull request changed after it was judged")
    if pr["base"]["ref"] != "main":
        fail("base is not main")

    # Re-run the guard from main against the judged commit.
    subprocess.run(["git", "fetch", "--no-tags", "origin",
                    f"+refs/pull/{pr['number']}/head:refs/remotes/pr/head"], check=True)
    if subprocess.run(["git", "rev-parse", "refs/remotes/pr/head"], capture_output=True,
                      text=True, check=True).stdout.strip() != head_sha:
        fail("fetched head does not match")
    base_sha = pr["base"]["sha"]
    # The judged commit must contain the current main (this checkout), so the
    # judge compared against the engine that is actually being replaced.
    if subprocess.run(["git", "merge-base", "--is-ancestor", "HEAD", head_sha]).returncode != 0:
        fail("main has moved since this pull request was judged; update the branch to re-judge")

    # Repeated attempts give a neutral change repeated chances to pass the SPRT.
    runs = api("GET", f"/actions/workflows/judge.yml/runs?branch={urllib.parse.quote(branch)}&per_page=20")
    attempts = [r for r in runs.get("workflow_runs", [])
                if r.get("event") == "pull_request" and r.get("conclusion") in ("success", "failure")]
    if len(attempts) > MAX_ATTEMPTS:
        fail(f"{len(attempts)} judged attempts on this branch (limit {MAX_ATTEMPTS})")

    for cmd in (
        ["bash", f"{HERE}/check_protected.sh", base_sha, head_sha, f"{HERE}/protected-paths.txt"],
        ["bash", f"{HERE}/check_source.sh", head_sha],
        ["bash", f"{HERE}/check_deps.sh", head_sha, f"{HERE}/allowed-deps.txt",
         f"{HERE}/allowed-local-crates.txt"],
    ):
        if subprocess.run(cmd).returncode != 0:
            fail("guard check failed")

    runs = api("GET", f"/commits/{head_sha}/check-runs?per_page=100").get("check_runs", [])
    latest = {}
    for r in runs:  # keep the most recent run of each name
        if r["name"] not in latest or r["started_at"] > latest[r["name"]]["started_at"]:
            latest[r["name"]] = r
    for name in REQUIRED_CHECKS:
        r = latest.get(name)
        if not r or r["status"] != "completed":
            # The other workflow will trigger this again when it finishes.
            print(f"waiting for check '{name}'")
            return
        if r["conclusion"] != "success":
            fail(f"check '{name}' did not pass")

    try:
        res = api("PUT", f"/pulls/{pr['number']}/merge",
                  {"merge_method": "squash", "sha": head_sha,
                   "commit_title": f"{pr['title']} (#{pr['number']})"})
    except urllib.error.HTTPError as e:
        fail(f"merge refused: {e.code} {e.read().decode()[:300]}")
    print(f"merged #{pr['number']}: {res.get('sha')}")
    try:
        api("DELETE", f"/git/refs/heads/{branch}")
    except urllib.error.HTTPError as e:
        print(f"branch not deleted: {e.code}")


if __name__ == "__main__":
    main()
