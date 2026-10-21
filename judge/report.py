#!/usr/bin/env python3
"""Morning report: collect judge results, benchmark main, build the graph page.

Run by .github/workflows/report.yml (from main). Reads judge reports uploaded by
pull-request runs as UNTRUSTED data: only whitelisted fields of the expected
types are kept, everything else is dropped.

usage: report.py --repo owner/name --data <data-branch-checkout> --site <out-dir>
                 --engine <main othello binary> --bench bench [--valgrind path]
env:   GITHUB_TOKEN (read actions/pulls)
"""

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge as J  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
HISTORY_FIELDS = ["date", "pr", "title", "merged", "result", "gate", "reason",
                  "midgame_ratio", "endgame_ratio", "elo", "elo_lo", "elo_hi", "pairs", "run_id"]
BENCH_FIELDS = ["date", "commit", "midgame_instructions", "endgame_instructions",
                "ffo_solved", "ffo_seconds"]
RESULTS = {"accepted", "rejected", "hold", "error"}
GATES = {"none", "endgame", "identical", "speed", "strength"}


def api(path, token, raw=False):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if raw else json.loads(data)


def num(v, lo=-1e9, hi=1e9):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi else ""


def clean_text(v, limit=120):
    if not isinstance(v, str):
        return ""
    v = re.sub(r"[\x00-\x1f<>&\"'`]", " ", v)
    return v[:limit].strip()


def sanitize(report):
    """Keep only expected fields of expected types from an untrusted report."""
    out = {"result": report.get("result") if report.get("result") in RESULTS else "error",
           "gate": report.get("gate") if report.get("gate") in GATES else "",
           "reason": clean_text(report.get("reason"))}
    parts = (report.get("speed") or {}).get("parts") or {}
    if isinstance(parts, dict):
        out["midgame_ratio"] = num((parts.get("midgame_search") or {}).get("ratio"), 0, 100)
        out["endgame_ratio"] = num((parts.get("endgame_solve") or {}).get("ratio"), 0, 100)
    st = report.get("strength") or {}
    if isinstance(st, dict):
        out["elo"] = num(st.get("elo"), -2000, 2000)
        e95 = st.get("elo_95")
        if isinstance(e95, list) and len(e95) == 2:
            out["elo_lo"], out["elo_hi"] = num(e95[0], -2000, 2000), num(e95[1], -2000, 2000)
        out["pairs"] = num(st.get("pairs"), 0, 10**6)
    return out


def read_csv(path, fields):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [{k: r.get(k, "") for k in fields} for r in csv.DictReader(f)]


def write_csv(path, fields, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def collect_runs(repo, token, seen_ids, since):
    """New completed Judge runs (newest first) as history rows."""
    runs = api(f"/repos/{repo}/actions/workflows/judge.yml/runs?status=completed&per_page=50",
               token).get("workflow_runs", [])
    rows = []
    for run in runs:
        rid = str(run["id"])
        if rid in seen_ids or run.get("event") != "pull_request":
            continue
        created = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
        if created < since:
            continue
        report = {"result": "error", "reason": "no report"}
        arts = api(f"/repos/{repo}/actions/runs/{rid}/artifacts", token).get("artifacts", [])
        for a in arts:
            if a.get("name") == "judge-report" and not a.get("expired"):
                blob = api(f"/repos/{repo}/actions/artifacts/{a['id']}/zip", token, raw=True)
                with zipfile.ZipFile(io.BytesIO(blob)) as z:
                    info = z.getinfo("report.json")
                    if info.file_size < 1_000_000:
                        try:
                            report = json.loads(z.read("report.json"))
                        except ValueError:
                            pass
        if run.get("conclusion") == "cancelled":
            continue
        pr_num, title, merged = "", clean_text(run.get("display_title")), ""
        prs = run.get("pull_requests") or []
        if prs:
            pr_num = prs[0]["number"]
            pr = api(f"/repos/{repo}/pulls/{pr_num}", token)
            title = clean_text(pr.get("title"))
            merged = "yes" if pr.get("merged_at") else "no"
        row = {k: "" for k in HISTORY_FIELDS}
        row.update(sanitize(report) if isinstance(report, dict) else {"result": "error"})
        row.update(date=created.astimezone(JST).strftime("%Y-%m-%d %H:%M"), pr=pr_num,
                   title=title, merged=merged, run_id=rid)
        rows.append(row)
    return rows


def benchmark_main(engine, bench, valgrind):
    row = {}
    if valgrind:
        mid = [r[0] for r in J.read_boards(os.path.join(bench, "positions", "midgame.txt"))][:60]
        end = [r[0] for r in J.read_boards(os.path.join(bench, "endgame", "exact.txt"))]
        end = sorted(end, key=lambda b: b.count("-"))[:40]
        row["midgame_instructions"] = J.instructions(valgrind, [engine, "search", "--depth", "6"],
                                                     mid, 1800)
        row["endgame_instructions"] = J.instructions(valgrind, [engine, "solve"], end, 1800)
    # FFO yardstick: problems solved correctly within 120 s each, and total time.
    solved, total = 0, 0.0
    for board, _num, score, _moves, _empties in J.read_boards(
            os.path.join(bench, "ffo", "ffo40-59.txt")):
        t0 = time.time()
        try:
            out = subprocess.run([engine, "solve"], input=board + "\n", capture_output=True,
                                 text=True, timeout=120).stdout
            got = json.loads(out.splitlines()[0])["score"]
        except (subprocess.TimeoutExpired, ValueError, IndexError, KeyError):
            total += 120
            break  # problems are ordered roughly by difficulty
        total += time.time() - t0
        if got == int(score):
            solved += 1
        else:
            break
    row["ffo_solved"], row["ffo_seconds"] = solved, round(total, 2)
    return row


def summary(new_rows, bench_today, bench_prev):
    if not new_rows:
        line = "昨夜は判定がありませんでした（夜間実行が動かなかった可能性があります）"
    else:
        parts = []
        for r in new_rows:
            name = r["title"] or f"#{r['pr']}"
            if r["result"] == "accepted":
                if r["gate"] == "speed":
                    best = min(x for x in (r["midgame_ratio"], r["endgame_ratio"]) if x != "")
                    parts.append(f"採用：{name}（命令数 {100 * (1 - float(best)):.1f}% 減）")
                elif r["gate"] == "strength":
                    parts.append(f"採用：{name}（{r['elo']:+} Elo）")
                else:
                    parts.append(f"取り込み：{name}")
            elif r["result"] == "hold":
                parts.append(f"保留：{name}")
            else:
                parts.append(f"却下：{name}（{r['reason']}）")
        line = " / ".join(parts)
    ffo = f"FFO {bench_today.get('ffo_solved', 0)}問・{bench_today.get('ffo_seconds', '')}秒"
    if bench_prev and bench_prev.get("ffo_seconds"):
        ffo += f"（前回 {bench_prev['ffo_solved']}問・{bench_prev['ffo_seconds']}秒）"
    return f"{line}。{ffo}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--site", required=True)
    ap.add_argument("--engine", required=True)
    ap.add_argument("--bench", required=True)
    ap.add_argument("--commit", default="")
    ap.add_argument("--valgrind", default=shutil.which("valgrind"))
    args = ap.parse_args()
    token = os.environ["GITHUB_TOKEN"]

    hist_path = os.path.join(args.data, "history.csv")
    bench_path = os.path.join(args.data, "bench.csv")
    history = read_csv(hist_path, HISTORY_FIELDS)
    benches = read_csv(bench_path, BENCH_FIELDS)
    seen = {r["run_id"] for r in history}
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    new_rows = collect_runs(args.repo, token, seen, since)
    recent = [r for r in new_rows if r["date"] >= (dt.datetime.now(JST) - dt.timedelta(hours=24))
              .strftime("%Y-%m-%d %H:%M")]
    history.extend(reversed(new_rows))
    write_csv(hist_path, HISTORY_FIELDS, history)

    today = {k: "" for k in BENCH_FIELDS}
    today.update(benchmark_main(args.engine, args.bench, args.valgrind))
    today.update(date=dt.datetime.now(JST).strftime("%Y-%m-%d"), commit=args.commit[:12])
    prev = benches[-1] if benches else None
    benches.append(today)
    write_csv(bench_path, BENCH_FIELDS, benches)

    latest = {"date": today["date"], "summary": summary(recent, today, prev),
              "nightly_results": len(recent)}
    os.makedirs(args.site, exist_ok=True)
    for name in ("history.csv", "bench.csv"):
        shutil.copy(os.path.join(args.data, name), os.path.join(args.site, name))
    with open(os.path.join(args.site, "latest.json"), "w") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "site", "index.html"),
                os.path.join(args.site, "index.html"))
    print(latest["summary"])


if __name__ == "__main__":
    main()
