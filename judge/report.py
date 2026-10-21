#!/usr/bin/env python3
"""Morning report, in two steps run by .github/workflows/report.yml (from main).

  report.py bench   --engine <main othello> --bench bench --out bench.json
      Benchmarks main's engine. Runs with no credentials (the engine is code
      merged by the nightly loop, so it is treated as untrusted here too).

  report.py publish --repo owner/name --data <data checkout> --site <dir> --bench-json bench.json
      Collects judge results of nightly pull requests and builds the page.
      Never executes engine code. Judge reports uploaded by pull-request runs
      are UNTRUSTED: only whitelisted fields of expected types are kept, and
      whether a change was accepted is taken from GitHub (merged or not).
      env: GITHUB_TOKEN (read actions and pull requests)
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
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge as J  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))
HISTORY_FIELDS = ["date", "pr", "title", "merged", "result", "gate", "reason",
                  "midgame_ratio", "endgame_ratio", "elo", "elo_lo", "elo_hi", "pairs", "run_id"]
BENCH_FIELDS = ["date", "commit", "midgame_instructions", "endgame_instructions",
                "ffo_solved", "ffo_seconds"]
RESULTS = ("accepted", "rejected", "hold", "error")
GATES = ("none", "build", "endgame", "identical", "speed", "strength")
MAX_ARTIFACT = 2_000_000


# ------------------------------------------------------------------ helpers

class _NoAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Drop the Authorization header when a redirect leaves the original host
    (artifact downloads redirect to blob storage)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != \
                urllib.parse.urlsplit(req.full_url).netloc:
            for k in list(new.headers):
                if k.lower() == "authorization":
                    del new.headers[k]
            new.unredirected_hdrs.pop("Authorization", None)
        return new


_opener = urllib.request.build_opener(_NoAuthOnRedirect)


def api(path, token, raw=False, limit=MAX_ARTIFACT):
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    with _opener.open(req, timeout=60) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("response too large")
    return data if raw else json.loads(data)


def num(v, lo=-1e9, hi=1e9):
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= v <= hi
    return v if ok else ""


def clean_text(v, limit=120):
    if not isinstance(v, str):
        return ""
    v = re.sub(r"[\x00-\x1f<>&\"'`]", " ", v)[:limit].strip()
    if v[:1] in ("=", "+", "-", "@"):  # spreadsheet formula injection
        v = "'" + v
    return v


def pick(d, *keys):
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def sanitize(report):
    """Keep only expected fields of expected types from an untrusted report."""
    if not isinstance(report, dict):
        return {"result": "error", "gate": "", "reason": "unreadable report"}
    res, gate = report.get("result"), report.get("gate")
    out = {"result": res if isinstance(res, str) and res in RESULTS else "error",
           "gate": gate if isinstance(gate, str) and gate in GATES else "",
           "reason": clean_text(report.get("reason"))}
    out["midgame_ratio"] = num(pick(report, "speed", "parts", "midgame_search", "ratio"), 0, 100)
    out["endgame_ratio"] = num(pick(report, "speed", "parts", "endgame_solve", "ratio"), 0, 100)
    out["elo"] = num(pick(report, "strength", "elo"), -2000, 2000)
    e95 = pick(report, "strength", "elo_95")
    if isinstance(e95, list) and len(e95) == 2:
        out["elo_lo"], out["elo_hi"] = num(e95[0], -2000, 2000), num(e95[1], -2000, 2000)
    out["pairs"] = num(pick(report, "strength", "pairs"), 0, 10**6)
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


# ------------------------------------------------------------------ bench step

def benchmark_main(engine_path, bench, valgrind):
    engine = J.Engine(engine_path)
    row = {}
    try:
        if valgrind:
            mid = [r[0] for r in J.read_boards(os.path.join(bench, "positions", "midgame.txt"))][:60]
            end = [r[0] for r in J.read_boards(os.path.join(bench, "endgame", "exact.txt"))]
            end = sorted(end, key=lambda b: b.count("-"))[:40]
            row["midgame_instructions"] = J.instructions(valgrind, engine,
                                                         ["search", "--depth", "6"], mid, 1800)
            row["endgame_instructions"] = J.instructions(valgrind, engine, ["solve"], end, 1800)
    except Exception as e:  # noqa: BLE001 - a broken benchmark must not stop the report
        row["error"] = str(e)[:200]
    # FFO yardstick: problems solved correctly in order, within 120 s each.
    solved, total = 0, 0.0
    for board, _num, score, _moves, _empties in J.read_boards(
            os.path.join(bench, "ffo", "ffo40-59.txt")):
        t0 = time.time()
        try:
            out = subprocess.run([engine_path, "solve"], input=board + "\n",
                                 capture_output=True, text=True, timeout=120).stdout
            got = json.loads(out.splitlines()[0])["score"]
        except Exception:  # noqa: BLE001
            total += 120
            break
        total += time.time() - t0
        if got != int(score):
            break
        solved += 1
    row["ffo_solved"], row["ffo_seconds"] = solved, round(total, 2)
    return row


# ------------------------------------------------------------------ publish step

def read_report(repo, token, run_id):
    arts = api(f"/repos/{repo}/actions/runs/{run_id}/artifacts", token).get("artifacts", [])
    for a in arts if isinstance(arts, list) else []:
        if a.get("name") != "judge-report" or a.get("expired"):
            continue
        if not isinstance(a.get("size_in_bytes"), int) or a["size_in_bytes"] > MAX_ARTIFACT:
            return {"result": "error", "reason": "report too large"}
        blob = api(f"/repos/{repo}/actions/artifacts/{a['id']}/zip", token, raw=True)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            with z.open("report.json") as f:
                data = f.read(1_000_001)
        if len(data) > 1_000_000:
            return {"result": "error", "reason": "report too large"}
        return json.loads(data)
    return {"result": "error", "reason": "no report"}


def collect_runs(repo, token, seen_ids, since):
    """Rows for new completed Judge runs of nightly (auto/*) pull requests."""
    runs = api(f"/repos/{repo}/actions/workflows/judge.yml/runs?status=completed&per_page=50",
               token).get("workflow_runs", [])
    rows = []
    for run in runs:
        try:
            rid = str(run["id"])
            if (rid in seen_ids or run.get("event") != "pull_request"
                    or run.get("conclusion") == "cancelled"
                    or pick(run, "head_repository", "full_name") != repo
                    or not str(run.get("head_branch", "")).startswith("auto/")):
                continue
            created = dt.datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))
            if created < since:
                continue
            try:
                report = read_report(repo, token, rid)
            except Exception as e:  # noqa: BLE001
                report = {"result": "error", "reason": f"report unreadable: {type(e).__name__}"}
            row = {k: "" for k in HISTORY_FIELDS}
            row.update(sanitize(report))
            pr_num, title, merged = "", clean_text(run.get("display_title")), "no"
            prs = run.get("pull_requests") or []
            if prs:
                pr_num = int(prs[0]["number"])
                pr = api(f"/repos/{repo}/pulls/{pr_num}", token)
                title = clean_text(pr.get("title"))
                merged = "yes" if pr.get("merged_at") else "no"
            # Only GitHub decides whether a change went in.
            if row["result"] == "accepted" and merged != "yes":
                row["result"] = "rejected" if run.get("conclusion") == "failure" else "hold"
            row.update(date=created.astimezone(JST).strftime("%Y-%m-%d %H:%M"), pr=pr_num,
                       title=title, merged=merged, run_id=rid)
            rows.append(row)
        except Exception as e:  # noqa: BLE001 - one bad run must not stop the report
            print(f"skipping run: {type(e).__name__}: {e}", file=sys.stderr)
    return rows


def describe(r):
    name = r.get("title") or f"#{r.get('pr')}"
    if r.get("merged") == "yes":
        ratios = [float(x) for x in (r.get("midgame_ratio"), r.get("endgame_ratio")) if x != ""]
        if r.get("gate") == "speed" and ratios:
            return f"採用：{name}（命令数 {100 * (1 - min(ratios)):.1f}% 減）"
        if r.get("gate") == "strength" and r.get("elo") != "":
            return f"採用：{name}（{float(r['elo']):+.1f} Elo）"
        return f"取り込み：{name}"
    if r.get("result") == "hold":
        return f"保留：{name}"
    return f"却下：{name}（{r.get('reason') or '判定に不合格'}）"


def summary(new_rows, bench_today, bench_prev):
    if new_rows:
        line = " / ".join(describe(r) for r in new_rows)
    else:
        line = "昨夜は判定がありませんでした（夜間実行が動かなかった可能性があります）"
    ffo = f"FFO {bench_today.get('ffo_solved', 0)}問・{bench_today.get('ffo_seconds', '')}秒"
    if bench_prev and bench_prev.get("ffo_seconds") not in (None, ""):
        ffo += f"（前回 {bench_prev.get('ffo_solved')}問・{bench_prev.get('ffo_seconds')}秒）"
    return f"{line}。{ffo}"


def publish(args):
    token = os.environ["GITHUB_TOKEN"]
    hist_path = os.path.join(args.data, "history.csv")
    bench_path = os.path.join(args.data, "bench.csv")
    history = read_csv(hist_path, HISTORY_FIELDS)
    benches = read_csv(bench_path, BENCH_FIELDS)

    today = {k: "" for k in BENCH_FIELDS}
    try:
        with open(args.bench_json) as f:
            b = json.load(f)
        for k in ("midgame_instructions", "endgame_instructions", "ffo_solved"):
            today[k] = num(b.get(k), 0, 10**15) if isinstance(b, dict) else ""
        today["ffo_seconds"] = num(b.get("ffo_seconds"), 0, 10**6) if isinstance(b, dict) else ""
    except (OSError, ValueError):
        pass
    today.update(date=dt.datetime.now(JST).strftime("%Y-%m-%d"),
                 commit=re.sub(r"[^0-9a-f]", "", args.commit)[:12])

    seen = {r["run_id"] for r in history}
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)
    try:
        new_rows = collect_runs(args.repo, token, seen, since)
    except Exception as e:  # noqa: BLE001
        print(f"could not list judge runs: {e}", file=sys.stderr)
        new_rows = []
    cutoff = (dt.datetime.now(JST) - dt.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M")
    recent = [r for r in new_rows if r["date"] >= cutoff]
    history.extend(reversed(new_rows))
    prev = benches[-1] if benches else None
    benches.append(today)
    write_csv(hist_path, HISTORY_FIELDS, history)
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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench")
    b.add_argument("--engine", required=True)
    b.add_argument("--bench", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--valgrind", default=shutil.which("valgrind"))
    p = sub.add_parser("publish")
    p.add_argument("--repo", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--site", required=True)
    p.add_argument("--bench-json", required=True)
    p.add_argument("--commit", default="")
    args = ap.parse_args()
    if args.cmd == "bench":
        row = benchmark_main(os.path.abspath(args.engine), args.bench, args.valgrind)
        with open(args.out, "w") as f:
            json.dump(row, f)
        print(json.dumps(row))
    else:
        publish(args)


if __name__ == "__main__":
    main()
