#!/usr/bin/env python3
"""記録CSVをGitHub Pages用のJSONに変換し、gh-pagesブランチへpushする。

gh-pages ブランチは毎回コミットを1つに上書き（amend + force push）するので、
10分ごとに更新しても履歴は増えない。記録の保管はSDカードとGoogleドライブで行う。
"""
import csv
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.environ.get("LOG_DIR", "/home/pi/templog")
PUBLISH_DIR = os.environ.get("PUBLISH_DIR", "/home/pi/pages-publish")   # gh-pages 用の作業フォルダ
REMOTE = os.environ.get("PAGES_REMOTE", "git@github.com:omoti12/temp-humidity-monitor.git")
BRANCH = "gh-pages"
STEP_MIN = 10          # グラフの間隔（分）
TEMP_HIGH = 25.0       # sht31_logger.py と同じ値にする
TEMP_LOW = 21.0

SITE_FILES = {         # 公開するファイル: 公開先の名前 → このリポジトリ内の元ファイル
    "index.html": "pages/index.html",
    "chart.umd.js": "web/static/chart.umd.js",
    "chart.js-LICENSE.md": "web/static/chart.js-LICENSE.md",
}


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((datetime.strptime(r["日時"], "%Y-%m-%d %H:%M:%S"),
                             float(r["温度(℃)"]), float(r["湿度(%)"]), r.get("状態") or ""))
            except (KeyError, ValueError):
                continue    # 書き込み途中の行などは飛ばす
    return rows


def every_10min(rows):
    """1分ごとの記録を10分単位にまとめ、平均・最高・最低を出す。"""
    buckets = {}
    for ts, t, h, _ in rows:
        key = ts.replace(minute=ts.minute - ts.minute % STEP_MIN, second=0)
        buckets.setdefault(key, []).append((t, h))
    out = []
    for k in sorted(buckets):
        ts = [v[0] for v in buckets[k]]
        hs = [v[1] for v in buckets[k]]
        out.append({
            "time": k.strftime("%H:%M"),
            "temp": round(sum(ts) / len(ts), 1),
            "temp_max": max(ts),
            "temp_min": min(ts),
            "hum": round(sum(hs) / len(hs), 1),
        })
    return out


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def build():
    """PUBLISH_DIR にサイト一式を作る。変更のあった日のデータだけ作り直す。"""
    data_dir = os.path.join(PUBLISH_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    for dst, src in SITE_FILES.items():
        shutil.copyfile(os.path.join(REPO_DIR, src), os.path.join(PUBLISH_DIR, dst))
    open(os.path.join(PUBLISH_DIR, ".nojekyll"), "w").close()

    days = sorted(f[:-4] for f in os.listdir(LOG_DIR)
                  if f.endswith(".csv") and len(f) == len("2026-01-01.csv"))
    latest = None
    for day in days:
        src = os.path.join(LOG_DIR, day + ".csv")
        out = os.path.join(data_dir, day + ".json")
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            continue
        rows = load(src)
        write_json(out, {"day": day, "points": every_10min(rows)})
        shutil.copyfile(src, os.path.join(data_dir, day + ".csv"))

    for day in reversed(days):      # 一番新しい記録を「現在値」にする
        rows = load(os.path.join(LOG_DIR, day + ".csv"))
        if rows:
            ts, t, h, st = rows[-1]
            latest = {"time": ts.astimezone().isoformat(), "temp": t, "hum": h,
                      "status": st or "正常"}
            break

    write_json(os.path.join(data_dir, "latest.json"), {
        "updated": datetime.now().astimezone().isoformat(),
        "latest": latest,
        "days": days,
        "high": TEMP_HIGH,
        "low": TEMP_LOW,
    })


def git(*args):
    return subprocess.run(["git", "-C", PUBLISH_DIR,
                           "-c", "user.name=temp-monitor", "-c", "user.email=temp-monitor@localhost",
                           *args], check=True, capture_output=True, text=True)


def publish():
    if not os.path.isdir(os.path.join(PUBLISH_DIR, ".git")):
        os.makedirs(PUBLISH_DIR, exist_ok=True)
        git("init", "-q", "-b", BRANCH)
        git("remote", "add", "origin", REMOTE)
    build()
    git("add", "-A")
    has_commit = subprocess.run(["git", "-C", PUBLISH_DIR, "rev-parse", "-q", "--verify", "HEAD"],
                                capture_output=True).returncode == 0
    msg = f"データ更新 {datetime.now():%Y-%m-%d %H:%M}"
    git("commit", "-q", "--amend", "-m", msg) if has_commit else git("commit", "-q", "-m", msg)
    git("push", "-q", "-f", "origin", BRANCH)
    print(f"公開しました: {msg}")


if __name__ == "__main__":
    try:
        publish()
    except subprocess.CalledProcessError as e:
        sys.exit(f"git の実行に失敗しました: {' '.join(e.cmd[-3:])}\n{e.stderr}")
