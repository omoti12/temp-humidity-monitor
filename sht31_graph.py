#!/usr/bin/env python3
"""今日のCSVを読み、10分ごとの温度・湿度グラフをPNGで保存する。"""
# ============================================================
# このプログラムの役割
#   今日の記録（CSV）から、10分ごとの温度・湿度のグラフ画像（PNG）を作る。
#   ラズパイにつないだモニターに表示するためのもの。
#   （公開サイトのグラフは pages/index.html がブラウザ上で描いているので、これとは別）
#
# 動かし方
#   systemd のタイマー（sht31-graph.timer）が10分ごとに自動で実行する
#   手動で作るとき : python3 sht31_graph.py
#   モニターに表示 : feh --fullscreen --reload 60 /dev/shm/temp-monitor/graph.png
# ============================================================
import csv
import os
import sys
from datetime import datetime

import matplotlib
# 画面のないところ（自動実行）でも画像を作れるように、描画方式を "Agg" にする
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

# ---- 設定値 ----
LOG_DIR = "/home/iot-user/templog"     # 記録（CSV）があるフォルダ
# グラフ画像はメモリ上（/dev/shm）に置く。10分ごとに作り直すのでSDカードに書く必要はない
OUT_PATH = "/dev/shm/temp-monitor/graph.png"
STEP_MIN = 10          # グラフの間隔（分）

# グラフに引く警報・注意の線は、警報の設定（alarm_settings.py）から読む
import alarm_settings

from matplotlib import font_manager

# インストール済みの日本語フォントを使う（sudo apt install fonts-noto-cjk）
# 日本語フォントがないと、グラフの日本語が「□」になる
_installed = {f.name for f in font_manager.fontManager.ttflist}
for _name in ["Noto Sans CJK JP", "IPAexGothic", "IPAGothic", "TakaoGothic"]:
    if _name in _installed:
        plt.rcParams["font.family"] = _name
        break


def load(path):
    # CSVを読んで [(日時, 温度, 湿度), ...] のリストにする
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
        # 時刻を10分単位に切り捨てる（例: 10:37 → 10:30）。同じ箱に入れる
        key = ts.replace(minute=ts.minute - ts.minute % STEP_MIN, second=0)
        buckets.setdefault(key, []).append((t, h))
    # 箱ごとに平均を計算する
    times = sorted(buckets)
    temps = [sum(v[0] for v in buckets[k]) / len(buckets[k]) for k in times]
    hums = [sum(v[1] for v in buckets[k]) / len(buckets[k]) for k in times]
    return times, temps, hums


def draw(times, temps, hums, day):
    # 上下2段のグラフを作る（上: 温度、下: 湿度。横軸の時刻は共通）
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    fig.suptitle(f"温湿度 {day:%Y-%m-%d}（{STEP_MIN}分ごと）　更新 {datetime.now():%H:%M}",
                 fontsize=16)

    # ---- 上段: 温度 ----
    ax1.plot(times, temps, marker="o", markersize=3, color="tab:red")
    # 警報（破線）と注意（点線）の基準を引く
    th = alarm_settings.thresholds()
    ax1.axhline(th["high_alarm"], color="red", linestyle="--", label=f"高温警報 {th['high_alarm']:g}℃")
    ax1.axhline(th["high_warn"], color="orange", linestyle=":", label=f"高温注意 {th['high_warn']:g}℃")
    ax1.axhline(th["low_warn"], color="mediumpurple", linestyle=":", label=f"低温注意 {th['low_warn']:g}℃")
    ax1.axhline(th["low_alarm"], color="purple", linestyle="--", label=f"低温警報 {th['low_alarm']:g}℃")
    ax1.set_ylabel("温度 (℃)")
    ax1.legend(loc="upper left")
    ax1.grid(alpha=0.3)
    # 一番新しい値を、グラフの右端に大きく書く
    ax1.annotate(f"{temps[-1]:.1f}℃", (times[-1], temps[-1]),
                 textcoords="offset points", xytext=(8, 0), fontsize=14, color="tab:red")

    # ---- 下段: 湿度 ----
    ax2.plot(times, hums, marker="o", markersize=3, color="tab:blue")
    ax2.set_ylabel("湿度 (%)")
    ax2.grid(alpha=0.3)
    ax2.annotate(f"{hums[-1]:.1f}%", (times[-1], hums[-1]),
                 textcoords="offset points", xytext=(8, 0), fontsize=14, color="tab:blue")

    # 横軸は 0:00〜23:59 の1日分、目盛りは2時間ごと
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.set_xlim(day.replace(hour=0, minute=0), day.replace(hour=23, minute=59))
    ax2.xaxis.set_major_locator(mdates.HourLocator(interval=2))

    # 画像として保存する
    fig.tight_layout()
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    tmp = OUT_PATH + ".tmp.png"
    fig.savefig(tmp, dpi=100)
    os.replace(tmp, OUT_PATH)   # 表示中の画像が書きかけにならないよう、置き換えで更新する
    plt.close(fig)


def main():
    # 今日のCSVを読み、グラフを作る
    day = datetime.now()
    path = os.path.join(LOG_DIR, day.strftime("%Y-%m-%d") + ".csv")
    if not os.path.exists(path):
        sys.exit(f"記録がまだありません: {path}")
    rows = load(path)
    if not rows:
        sys.exit("記録が空です")
    draw(*every_10min(rows), day)
    print(f"保存しました: {OUT_PATH}")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
