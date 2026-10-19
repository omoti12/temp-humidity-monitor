#!/usr/bin/env python3
"""今日のCSVを読み、10分ごとの温度・湿度グラフをPNGで保存する。"""
import csv
import os
import sys
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

LOG_DIR = "/home/iot-user/templog"
# グラフ画像はメモリ上（/dev/shm）に置く。10分ごとに作り直すのでSDカードに書く必要はない
OUT_PATH = "/dev/shm/temp-monitor/graph.png"
STEP_MIN = 10          # グラフの間隔（分）

TEMP_HIGH = 30.0       # sht31_logger.py と同じ値にする
TEMP_LOW = 21.0

from matplotlib import font_manager

# インストール済みの日本語フォントを使う（sudo apt install fonts-noto-cjk）
_installed = {f.name for f in font_manager.fontManager.ttflist}
for _name in ["Noto Sans CJK JP", "IPAexGothic", "IPAGothic", "TakaoGothic"]:
    if _name in _installed:
        plt.rcParams["font.family"] = _name
        break


def load(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((datetime.strptime(r["日時"], "%Y-%m-%d %H:%M:%S"),
                         float(r["温度(℃)"]), float(r["湿度(%)"])))
    return rows


def every_10min(rows):
    """1分ごとの記録を10分単位にまとめ、平均を取る。"""
    buckets = {}
    for ts, t, h in rows:
        key = ts.replace(minute=ts.minute - ts.minute % STEP_MIN, second=0)
        buckets.setdefault(key, []).append((t, h))
    times = sorted(buckets)
    temps = [sum(v[0] for v in buckets[k]) / len(buckets[k]) for k in times]
    hums = [sum(v[1] for v in buckets[k]) / len(buckets[k]) for k in times]
    return times, temps, hums


def draw(times, temps, hums, day):
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.suptitle(f"温湿度 {day:%Y-%m-%d}（{STEP_MIN}分ごと）　更新 {datetime.now():%H:%M}",
                 fontsize=16)

    ax1.plot(times, temps, marker="o", markersize=3, color="tab:red")
    ax1.axhline(TEMP_HIGH, color="orange", linestyle="--", label=f"高温 {TEMP_HIGH:g}℃")
    ax1.axhline(TEMP_LOW, color="purple", linestyle="--", label=f"低温 {TEMP_LOW:g}℃")
    ax1.set_ylabel("温度 (℃)")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)
    ax1.annotate(f"{temps[-1]:.1f}℃", (times[-1], temps[-1]),
                 textcoords="offset points", xytext=(8, 0), fontsize=14, color="tab:red")

    ax2.plot(times, hums, marker="o", markersize=3, color="tab:blue")
    ax2.set_ylabel("湿度 (%)")
    ax2.grid(alpha=0.3)
    ax2.annotate(f"{hums[-1]:.1f}%", (times[-1], hums[-1]),
                 textcoords="offset points", xytext=(8, 0), fontsize=14, color="tab:blue")

    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.set_xlim(day.replace(hour=0, minute=0), day.replace(hour=23, minute=59))
    ax2.xaxis.set_major_locator(mdates.HourLocator(interval=2))

    fig.tight_layout()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp.png"
    fig.savefig(tmp, dpi=100)
    os.replace(tmp, OUT_PATH)   # 表示中の画像が書きかけにならないよう、置き換えで更新する
    plt.close(fig)


def main():
    day = datetime.now()
    path = os.path.join(LOG_DIR, day.strftime("%Y-%m-%d") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"記録がまだありません: {path}")
    rows = load(path)
    if not rows:
        sys.exit("記録が空です")
    draw(*every_10min(rows), day)
    print(f"保存しました: {OUT_PATH}")


if __name__ == "__main__":
    main()
