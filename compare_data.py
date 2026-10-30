"""比較用センサー（THD-R-C・Pt100）の記録を読む部品。
web/app.py と pages/publish.py の両方から使う。"""
# ============================================================
# このファイルの役割
#   比較用センサーのロガーが書いた記録を、サイト用に読み出す
#     ・現在値   : /dev/shm/temp-monitor/名前.json（数秒ごとに上書き）
#     ・1分ごと  : /home/iot-user/templog/名前/日付.csv
#   名前は "thdrc"（thdrc_logger.py）または "pt100"（pt100_logger.py）
#   SHT31 の記録（templog/日付.csv）とは別のフォルダに置くので、今までの記録には影響しない
# ============================================================
import csv
import json
import os
from datetime import datetime, timedelta

LOG_BASE = os.environ.get("LOG_DIR", "/home/iot-user/templog")
NOW_DIR = os.environ.get("COMPARE_NOW_DIR", "/dev/shm/temp-monitor")
STEP_MIN = 10          # グラフの間隔（分）。SHT31 のグラフと同じ
SENSORS = ("thdrc", "pt100")    # サイトに出す比較用センサーの名前


def log_dir(name):
    # そのセンサーの記録フォルダ（例: /home/iot-user/templog/pt100）
    return os.path.join(LOG_BASE, name)


def now_path(name):
    # そのセンサーの現在値のファイル（例: /dev/shm/temp-monitor/pt100.json）
    return os.path.join(NOW_DIR, name + ".json")


def csv_path(name, day):
    # その日のCSVファイルの場所（例: /home/iot-user/templog/pt100/2026-10-30.csv）
    return os.path.join(log_dir(name), day.strftime("%Y-%m-%d") + ".csv")


def load(name, day):
    """その日のCSVを読んで [(日時, 温度, 湿度), ...] にする。測れなかった値・湿度のない記録は None"""
    path = csv_path(name, day)
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


def every_10min(name, day):
    """1分ごとの記録を10分単位にまとめた平均。[{"time": "10:30", "temp": .., "hum": ..}, ...]"""
    buckets = {}
    for ts, t, h in load(name, day):
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


def now_reading(name):
    """ロガーが書く現在値。2分以上古い・無いときは None（止まっている）"""
    try:
        with open(now_path(name), encoding="utf-8") as f:
            n = json.load(f)
        ts = datetime.fromisoformat(n["iso"])
    except (OSError, ValueError, KeyError):
        return None
    if datetime.now(ts.tzinfo) - ts > timedelta(minutes=2):
        return None
    return n


def all_points(day):
    """サイト用: 全センサーの10分ごとの平均 {"thdrc": [...], "pt100": [...]}"""
    return {name: every_10min(name, day) for name in SENSORS}


def all_now():
    """サイト用: 全センサーの現在値 {"thdrc": {...} または None, "pt100": ...}"""
    return {name: now_reading(name) for name in SENSORS}
