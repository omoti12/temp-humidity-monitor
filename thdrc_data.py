"""THD-R-C（比較用の4〜20mA温湿度センサー）の記録を読む部品。
web/app.py と pages/publish.py の両方から使う。"""
# ============================================================
# このファイルの役割
#   thdrc_logger.py が書いた記録を、サイト用に読み出す
#     ・現在値   : /dev/shm/temp-monitor/thdrc.json（5秒ごとに上書き）
#     ・1分ごと  : /home/iot-user/templog/thdrc/日付.csv
#   SHT31 の記録（templog/日付.csv）とは別のフォルダに置くので、今までの記録には影響しない
# ============================================================
import csv
import json
import os
from datetime import datetime, timedelta

LOG_DIR = os.path.join(os.environ.get("LOG_DIR", "/home/iot-user/templog"), "thdrc")
NOW_PATH = os.environ.get("THDRC_NOW_PATH", "/dev/shm/temp-monitor/thdrc.json")
STEP_MIN = 10          # グラフの間隔（分）。SHT31 のグラフと同じ


def csv_path(day):
    # その日のCSVファイルの場所（例: /home/iot-user/templog/thdrc/2026-10-29.csv）
    return os.path.join(LOG_DIR, day.strftime("%Y-%m-%d") + ".csv")


def load(day):
    """その日のCSVを読んで [(日時, 温度, 湿度), ...] にする。測れなかった値は None"""
    path = csv_path(day)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                ts = datetime.strptime(r["日時"], "%Y-%m-%d %H:%M:%S")
            except (KeyError, ValueError):
                continue    # 書き込み途中の行などは飛ばす
            t, h = r.get("温度(℃)") or "", r.get("湿度(%)") or ""
            try:
                rows.append((ts, float(t) if t else None, float(h) if h else None))
            except ValueError:
                continue
    return rows


def every_10min(day):
    """1分ごとの記録を10分単位にまとめた平均。[{"time": "10:30", "temp": .., "hum": ..}, ...]"""
    buckets = {}
    for ts, t, h in load(day):
        key = ts.replace(minute=ts.minute - ts.minute % STEP_MIN, second=0)
        buckets.setdefault(key, []).append((t, h))
    out = []
    for k in sorted(buckets):
        ts = [v[0] for v in buckets[k] if v[0] is not None]
        hs = [v[1] for v in buckets[k] if v[1] is not None]
        out.append({
            "time": k.strftime("%H:%M"),
            "temp": round(sum(ts) / len(ts), 1) if ts else None,
            "hum": round(sum(hs) / len(hs), 1) if hs else None,
        })
    return out


def now_reading():
    """thdrc_logger.py が5秒ごとに書く現在値。2分以上古い・無いときは None（止まっている）"""
    try:
        with open(NOW_PATH, encoding="utf-8") as f:
            n = json.load(f)
        ts = datetime.fromisoformat(n["iso"])
    except (OSError, ValueError, KeyError):
        return None
    if datetime.now(ts.tzinfo) - ts > timedelta(minutes=2):
        return None
    return n
