#!/usr/bin/env python3
"""温湿度モニター Webサイト: sht31_logger.py が書いたCSVを読んで表示する。"""
import csv
import hmac
import os
from datetime import datetime, timedelta

from flask import Flask, Response, abort, jsonify, render_template, request, send_file

LOG_DIR = os.environ.get("LOG_DIR", "/home/pi/templog")
STEP_MIN = 10          # グラフの間隔（分）
TEMP_HIGH = 25.0       # sht31_logger.py と同じ値にする
TEMP_LOW = 21.0

# ログイン用。環境変数 WEB_USER / WEB_PASSWORD で設定する（未設定ならログインなし）
WEB_USER = os.environ.get("WEB_USER", "")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

app = Flask(__name__)


@app.before_request
def require_login():
    if not WEB_PASSWORD:
        return None
    auth = request.authorization
    if (auth and hmac.compare_digest(auth.username or "", WEB_USER)
            and hmac.compare_digest(auth.password or "", WEB_PASSWORD)):
        return None
    return Response("ログインが必要です", 401,
                    {"WWW-Authenticate": 'Basic realm="temp-monitor", charset="UTF-8"'})


def csv_path(day):
    return os.path.join(LOG_DIR, day.strftime("%Y-%m-%d") + ".csv")


def load(day):
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


def status(t):
    if t >= TEMP_HIGH:
        return "高温"
    if t <= TEMP_LOW:
        return "低温"
    return "正常"


def parse_day(s):
    if not s:
        return datetime.now()
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        abort(400)


@app.route("/")
def index():
    return render_template("index.html", step=STEP_MIN)


@app.route("/api/data")
def api_data():
    day = parse_day(request.args.get("day"))
    rows = load(day)
    latest = None
    # 今日の分がまだなければ前日の最後の値を「現在値」にする
    src = rows or load(day - timedelta(days=1))
    if src:
        ts, t, h, st = src[-1]
        latest = {"time": ts.strftime("%Y-%m-%d %H:%M"), "temp": t, "hum": h,
                  "status": st or status(t),   # ロガーが記録した状態を優先する
                  "stale": (datetime.now() - ts) > timedelta(minutes=5)}
    return jsonify({
        "day": day.strftime("%Y-%m-%d"),
        "points": every_10min(rows),
        "latest": latest,
        "high": TEMP_HIGH,
        "low": TEMP_LOW,
    })


@app.route("/download")
def download():
    day = parse_day(request.args.get("day"))
    path = csv_path(day)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
