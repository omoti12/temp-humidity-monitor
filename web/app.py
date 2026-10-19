#!/usr/bin/env python3
"""温湿度モニター Webサイト: sht31_logger.py が書いたCSVを読んで表示する。"""
# ============================================================
# このプログラムの役割
#   ラズパイの中で動く小さなWebサーバー（Flask）。
#   1. ラズパイ内のサイト（http://ラズパイのIP:8000）を表示する
#        画面は templates/index.html、グラフの部品は static/chart.umd.js
#   2. 現在値の窓口 /api/now を提供する
#        公開サイト（GitHub Pages）は5秒ごとにここを読んで現在値を表示する
#        インターネットからは Tailscale Funnel 経由で /api/now だけが見える
#
# URLの一覧
#   /                 … サイトの画面
#   /api/data?day=日付 … その日の10分ごとのデータと最新値（JSON）
#   /api/now          … 現在値（JSON）。ログイン不要
#   /download?day=日付 … その日のCSVをダウンロード
#
# 動かし方
#   systemd（temp-web.service）が自動で起動する
#   手動で動かすとき : python3 web/app.py
# ============================================================
import csv
import hmac
import json
import os
from datetime import datetime, timedelta

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

# ---- 設定値（環境変数で上書きできる。temp-web.service の Environment= で設定）----
LOG_DIR = os.environ.get("LOG_DIR", "/home/iot-user/templog")
NOW_PATH = os.environ.get("NOW_PATH", "/dev/shm/temp-monitor/now.json")   # sht31_logger.py と同じ場所
STEP_MIN = 10          # グラフの間隔（分）
TEMP_HIGH = 30.0       # sht31_logger.py と同じ値にする
TEMP_LOW = 21.0

# ログイン用。環境変数 WEB_USER / WEB_PASSWORD で設定する（未設定ならログインなし）
WEB_USER = os.environ.get("WEB_USER", "")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

# Webサーバー本体
app = Flask(__name__)


@app.before_request
def require_login():
    # どのページを開くときも、最初にここを通る（パスワードの確認）
    # 現在値だけは公開サイト（GitHub Pages）から読めるようにログイン不要にする
    if not WEB_PASSWORD or request.path == "/api/now":
        return None
    # ブラウザが送ってきたユーザー名・パスワードを確認する
    # （hmac.compare_digest は、比べる時間から中身を推測されにくい比べ方）
    auth = request.authorization
    if (auth and hmac.compare_digest(auth.username or "", WEB_USER)
            and hmac.compare_digest(auth.password or "", WEB_PASSWORD)):
        return None
    # 合っていなければ、ブラウザにログイン画面を出させる
    return Response("ログインが必要です", 401,
                    {"WWW-Authenticate": 'Basic realm="temp-monitor", charset="UTF-8"'})


# ============================================================
# 記録（CSV）の読み込みと集計
# ============================================================

def csv_path(day):
    # その日のCSVファイルの場所（例: /home/iot-user/templog/2026-10-19.csv）
    return os.path.join(LOG_DIR, day.strftime("%Y-%m-%d") + ".csv")


def load(day):
    # その日のCSVを読んで [(日時, 温度, 湿度, 状態), ...] のリストにする
    path = csv_path(day)
    if not os.path.exists(path):
        return []
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


def status(t):
    # 状態の判定（CSVに状態が書かれていない古い記録のときだけ使う）
    if t >= TEMP_HIGH:
        return "高温"
    if t <= TEMP_LOW:
        return "低温"
    return "正常"


def parse_day(s):
    # URLの ?day=2026-10-19 を日付に変換する。なければ今日、形がおかしければエラー（400）
    if not s:
        return datetime.now()
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        abort(400)


# ============================================================
# ページとAPI
# ============================================================

@app.route("/")
def index():
    # サイトの画面（templates/index.html）を返す
    return render_template("index.html", step=STEP_MIN)


def latest_reading(rows, day):
    """一番新しい記録。今日の分がまだなければ前日の最後の値を使う。"""
    src = rows or load(day - timedelta(days=1))
    if not src:
        return None
    ts, t, h, st = src[-1]
    return {"time": ts.strftime("%Y-%m-%d %H:%M"), "iso": ts.astimezone().isoformat(),
            "temp": t, "hum": h,
            "status": st or status(t),   # ロガーが記録した状態を優先する
            # 5分以上新しい記録がなければ「止まっているかも」の印を付ける
            "stale": (datetime.now() - ts) > timedelta(minutes=5)}


@app.route("/api/data")
def api_data():
    # その日のグラフ用データと最新値を返す（ラズパイ内のサイトが1分ごとに読む）
    day = parse_day(request.args.get("day"))
    rows = load(day)
    return jsonify({
        "day": day.strftime("%Y-%m-%d"),
        "points": every_10min(rows),
        "latest": latest_reading(rows, day),
        "high": TEMP_HIGH,
        "low": TEMP_LOW,
    })


def now_reading():
    """ロガーが5秒ごとに書く now.json を読む。古い・無いときはCSVの最新値を使う。"""
    try:
        with open(NOW_PATH, encoding="utf-8") as f:
            n = json.load(f)
        ts = datetime.fromisoformat(n["iso"])
        age = datetime.now(ts.tzinfo) - ts
        # 2分以内に書かれたものなら、それを現在値として返す
        if age < timedelta(minutes=2):
            n["time"] = ts.strftime("%Y-%m-%d %H:%M:%S")
            n["stale"] = False
            return n
    except (OSError, ValueError, KeyError):
        pass    # ファイルがない・壊れているときは、下のCSVの値を使う
    day = datetime.now()
    return latest_reading(load(day), day)


@app.route("/api/now")
def api_now():
    """現在値だけを返す。GitHub Pages のサイトから読むため、別サイトからの読み込みを許可する。"""
    resp = jsonify({"latest": now_reading()})
    # 別のサイト（omoti12.github.io）のページから読めるようにする
    resp.headers["Access-Control-Allow-Origin"] = "*"
    # 古い値が使い回されないよう、ブラウザや途中の経路に保存させない
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/download")
def download():
    # その日のCSVをファイルとしてダウンロードさせる。なければ「見つかりません」（404）
    day = parse_day(request.args.get("day"))
    path = csv_path(day)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


# このファイルを直接実行したときの入口
# 0.0.0.0 = ラズパイ以外（同じネットワークのPCなど）からも開けるようにする
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
