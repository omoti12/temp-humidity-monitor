#!/usr/bin/env python3
"""Pt100・K型熱電対 比較用ロガー: Y2Kb の温度測定モジュール（Pt100 / K型熱電対）から
USB で温度を受け取り、SHT31 と比べられるように記録する。"""
# ============================================================
# このプログラムの役割（比較の実験用。警報メールは送らない）
#   1. モジュールが10秒ごとに送ってくる温度を受け取る
#        送ってくる形: ＜現在温度＞,＜平均温度＞,＜最大温度＞,＜最小温度＞（1行ずつ）
#   2. 受け取った現在温度を 名前.json に書く（サイトの「比較」用）
#   3. 1分に1回、templog/名前/日付.csv に記録する（グラフ用）
#
# Pt100 と K型熱電対のモジュールは同じ形のデータを送ってくるので、このプログラムを2つ動かして両方記録する
#   Pt100       : pt100-logger.service（SENSOR_NAME=pt100）
#   K型熱電対   : ktc-logger.service（SENSOR_NAME=ktc）
#   どちらのモジュールかは、つないだUSBポートの場所で見分ける（環境変数 SENSOR_PORT で指定）
#
# 動かし方
#   普段は systemd（pt100-logger.service）が自動で起動・再起動する
#   手動で試すとき : python3 pt100_logger.py
#   ※ 動いている間はポートを使っているので、y2kb_read.py で読むときは先に止める
#     : sudo systemctl stop pt100-logger
# ============================================================
import csv
import json
import os
import sys
import time
from datetime import datetime

import serial

import alarm_settings
import compare_data

# ============================================================
# 設定値
# ============================================================

# 記録の名前（templog/名前/・名前.json）。"pt100" か "ktc"（K型熱電対）
NAME = os.environ.get("SENSOR_NAME", "pt100")
# モジュールのUSBポート（PT100_PORT は前の版の名前。残っていても使えるようにしておく）
PORT = os.environ.get("SENSOR_PORT") or os.environ.get("PT100_PORT", "/dev/ttyUSB0")
BAUD = 921600       # モジュールの通信速度（出荷時の設定）
INTERVAL = 60       # 記録（CSV）の間隔（秒）
# 正しい値とみなす範囲[℃]。外れたら断線などの疑いとして記録しない（K型熱電対は測れる範囲が広い）
TEMP_MIN, TEMP_MAX = (-200.0, 1300.0) if NAME == "ktc" else (-50.0, 200.0)


# ============================================================
# 保存（CSVと現在値）
# ============================================================

def append_csv(now, t):
    # その日のCSVファイルに1行追記する（例: /home/iot-user/templog/pt100/2026-10-30.csv）
    os.makedirs(compare_data.log_dir(NAME), exist_ok=True)
    path = compare_data.csv_path(NAME, now)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日時", "温度(℃)"])
        w.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), f"{t:.2f}"])


def write_now(now, t):
    """現在値を 名前.json（pt100.json など）に書く。読み取り中に中途半端なファイルが見えないよう、置き換えで更新する"""
    path = compare_data.now_path(NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"iso": now.astimezone().isoformat(timespec="seconds"),
                   "temp": round(t, 1), "hum": None}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def parse(line):
    """「現在,平均,最大,最小」の行から現在温度を取り出す。読めない・範囲外なら None"""
    try:
        t = float(line.decode(errors="replace").split(",")[0])
    except ValueError:
        return None
    return t if TEMP_MIN <= t <= TEMP_MAX else None


# ============================================================
# メインの処理（ずっと繰り返す）
# ============================================================

def main():
    try:
        port = serial.Serial(PORT, BAUD, timeout=30)
    except serial.SerialException as e:
        # 開けないときはエラーで止まり、systemd が10秒後に起動し直す
        sys.exit(f"{PORT} を開けません（USBケーブル・モジュールの電源を確認）: {e}")
    print(f"{PORT} を開きました", flush=True)
    last_logged = None      # 最後にCSVへ記録した区切りの時刻
    s = alarm_settings.load()   # 補正の設定
    while True:
        line = port.readline()      # 1行届くまで待つ（30秒届かなければ空）
        if not line:
            print("30秒間データが届きません", flush=True)
            continue
        t = parse(line)
        if t is None:
            continue    # 電源を入れた直後の途中の行などは飛ばす
        now = datetime.now()
        slot = int(time.time() // INTERVAL)
        if slot != last_logged:
            s = alarm_settings.load()   # 補正の設定（1分ごとに読み直す。設定画面で変えるとここで反映）
        t, _ = alarm_settings.calibrate(NAME, t, None, s)
        write_now(now, t)
        if slot != last_logged:     # 毎分1回：記録
            append_csv(now, t)
            last_logged = slot
            print(f"{now:%H:%M:%S}  温度 {t:.2f}℃", flush=True)


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
