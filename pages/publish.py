#!/usr/bin/env python3
"""記録CSVをGitHub Pages用のJSONに変換し、gh-pagesブランチへpushする。

gh-pages ブランチは毎回コミットを1つに上書き（amend + force push）するので、
10分ごとに更新しても履歴は増えない。記録の保管はSDカードとGoogleドライブで行う。
"""
# ============================================================
# このプログラムの役割
#   公開サイト https://omoti12.github.io/temp-humidity-monitor/ のデータを更新する。
#   1. 記録（CSV）から、サイト用のデータ（JSON）を作る
#   2. 画面のファイル（pages/index.html など）と一緒に作業フォルダにそろえる
#   3. GitHub の gh-pages ブランチへ送る（push）→ GitHub Pages が公開する
#
# 作業フォルダ（/home/iot-user/pages-publish）の中身
#   index.html            … 画面（pages/index.html のコピー）
#   chart.umd.js          … グラフの部品
#   data/latest.json      … 最新値・記録のある日の一覧・警報の基準
#   data/2026-10-19.json  … その日の10分ごとのデータ
#   data/2026-10-19.csv   … その日のCSV（ダウンロード用）
#
# 動かし方
#   systemd のタイマー（pages-publish.timer）が10分ごとに自動で実行する
#   手動ですぐ公開するとき : python3 pages/publish.py
#   GitHub への書き込みには、ラズパイの鍵（Deploy key）を使う
# ============================================================
import csv
import fcntl
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

# 1つ上のフォルダにある alarm_settings.py（警報の設定）を使えるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alarm_settings  # noqa: E402

# ---- 設定値（環境変数で上書きできる）----
# このリポジトリのフォルダ（このファイルの1つ上の階層）
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.environ.get("LOG_DIR", "/home/iot-user/templog")
PUBLISH_DIR = os.environ.get("PUBLISH_DIR", "/home/iot-user/pages-publish")   # gh-pages 用の作業フォルダ
# 送り先のGitHubリポジトリ（SSH鍵で書き込む）
REMOTE = os.environ.get("PAGES_REMOTE", "git@github.com:omoti12/temp-humidity-monitor.git")
BRANCH = "gh-pages"    # GitHub Pages が公開するブランチ
# 同時に2つ動かないようにするための鍵ファイル（メモリ上に置く）
RUN_LOCK = os.environ.get("PUBLISH_LOCK", "/dev/shm/temp-monitor/publish.lock")
STEP_MIN = 10          # グラフの間隔（分）

SITE_FILES = {         # 公開するファイル: 公開先の名前 → このリポジトリ内の元ファイル
    "index.html": "pages/index.html",
    "chart.umd.js": "web/static/chart.umd.js",
    "chart.js-LICENSE.md": "web/static/chart.js-LICENSE.md",
    "hammer.min.js": "web/static/hammer.min.js",
    "hammerjs-LICENSE.md": "web/static/hammerjs-LICENSE.md",
    "chartjs-plugin-zoom.min.js": "web/static/chartjs-plugin-zoom.min.js",
    "chartjs-plugin-zoom-LICENSE.md": "web/static/chartjs-plugin-zoom-LICENSE.md",
}


# ============================================================
# 記録の読み込みと集計（web/app.py と同じ処理）
# ============================================================

def load(path):
    # CSVを読んで [(日時, 温度, 湿度, 状態), ...] のリストにする
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
        # 時刻を10分単位に切り捨てる（例: 10:37 → 10:30）。同じ箱に入れる
        key = ts.replace(minute=ts.minute - ts.minute % STEP_MIN, second=0)
        buckets.setdefault(key, []).append((t, h))
    out = []
    for k in sorted(buckets):
        ts = [v[0] for v in buckets[k]]
        hs = [v[1] for v in buckets[k]]
        # グラフ1点分のデータ（温度は平均・最高・最低、湿度は平均）
        out.append({
            "time": k.strftime("%H:%M"),
            "temp": round(sum(ts) / len(ts), 1),
            "temp_max": max(ts),
            "temp_min": min(ts),
            "hum": round(sum(hs) / len(hs), 1),
        })
    return out


def write_json(path, obj):
    # JSONファイルを書く（日本語はそのまま、余計な空白なしで小さく）
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


# ============================================================
# サイト一式を作る
# ============================================================

def build():
    """PUBLISH_DIR にサイト一式を作る。変更のあった日のデータだけ作り直す。"""
    data_dir = os.path.join(PUBLISH_DIR, "data")
    os.makedirs(data_dir, exist_ok=True)
    # 画面のファイルをコピーする（pages/index.html を直したら、ここで反映される）
    for dst, src in SITE_FILES.items():
        shutil.copyfile(os.path.join(REPO_DIR, src), os.path.join(PUBLISH_DIR, dst))
    # GitHub Pages の変換機能（Jekyll）を使わず、ファイルをそのまま公開させるための印
    open(os.path.join(PUBLISH_DIR, ".nojekyll"), "w").close()

    # 記録のある日の一覧（"2026-10-19" のような名前のCSVだけを対象にする）
    days = sorted(f[:-4] for f in os.listdir(LOG_DIR)
                  if f.endswith(".csv") and len(f) == len("2026-01-01.csv"))
    latest = None
    for day in days:
        src = os.path.join(LOG_DIR, day + ".csv")
        out = os.path.join(data_dir, day + ".json")
        # CSVが前回から変わっていない日は作り直さない（ふだん作り直すのは今日の分だけ）
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

    # サイトが最初に読むファイル。ラズパイの現在値の窓口に届かないときは、この値を表示する
    write_json(os.path.join(data_dir, "latest.json"), {
        "updated": datetime.now().astimezone().isoformat(),
        "latest": latest,
        "days": days,
        # グラフに引く警報・注意の線（高温警報・高温注意・低温注意・低温警報）
        "thresholds": alarm_settings.thresholds(),
    })


# ============================================================
# GitHub へ送る
# ============================================================

def git(*args):
    # 作業フォルダで git コマンドを実行する。失敗したらエラーで止まる（check=True）
    return subprocess.run(["git", "-C", PUBLISH_DIR,
                           "-c", "user.name=temp-monitor", "-c", "user.email=temp-monitor@localhost",
                           *args], check=True, capture_output=True, text=True)


def publish():
    # 同時に2つ動かないようにする（自動の公開と手動の公開が重なったときは、先の方が終わるまで待つ）
    # この鍵はプログラムが終われば（途中で止まっても）自動で外れる
    os.makedirs(os.path.dirname(RUN_LOCK), exist_ok=True)
    lock = open(RUN_LOCK, "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    # 鍵を持っているので、ほかに git は動いていない。それでも git の「使用中」の札（index.lock）が
    # 残っていれば、前回の途中で電源が切れたときの残りものなので消す
    stale = os.path.join(PUBLISH_DIR, ".git", "index.lock")
    if os.path.exists(stale):
        os.remove(stale)
        print("前回の途中で止まったときの残りもの（index.lock）を消しました")
    # 初回だけ: 作業フォルダを git のフォルダとして準備し、送り先を登録する
    if not os.path.isdir(os.path.join(PUBLISH_DIR, ".git")):
        os.makedirs(PUBLISH_DIR, exist_ok=True)
        git("init", "-q", "-b", BRANCH)
        git("remote", "add", "origin", REMOTE)
    # amend で置き換えた古い版が作業フォルダに溜まらないよう、履歴の記録を残さない
    git("config", "core.logAllRefUpdates", "false")
    build()
    git("add", "-A")
    # すでにコミットがあれば、それを上書き（--amend）。なければ最初のコミットを作る
    has_commit = subprocess.run(["git", "-C", PUBLISH_DIR, "rev-parse", "-q", "--verify", "HEAD"],
                                capture_output=True).returncode == 0
    msg = f"データ更新 {datetime.now():%Y-%m-%d %H:%M}"
    git("commit", "-q", "--amend", "-m", msg) if has_commit else git("commit", "-q", "-m", msg)
    # 上書きしたので、強制（-f）で送る。GitHub 側の履歴は常に1つになる
    git("push", "-q", "-f", "origin", BRANCH)
    print(f"公開しました: {msg}")
    now = datetime.now()
    if now.hour == 3 and now.minute < 10:     # 1日1回、使わなくなった古い版を消す
        git("reflog", "expire", "--expire=now", "--all")
        git("gc", "-q", "--prune=now")
        print("古いデータを掃除しました")


# このファイルを直接実行したときの入口
# git が失敗したら（ネットが切れている、鍵が使えない など）、原因を表示して終わる
if __name__ == "__main__":
    try:
        publish()
    except subprocess.CalledProcessError as e:
        sys.exit(f"git の実行に失敗しました: {' '.join(e.cmd[-3:])}\n{e.stderr}")
