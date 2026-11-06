#!/usr/bin/env python3
"""加速度センサーのロガー: 3軸の加速度センサーを AD コンバーター MCP3004（SPI1）で読み、記録する。"""
# ============================================================
# このプログラムの役割（記録と表示だけ。警報メールは送らない）
#   1. 0.1秒ごとに X・Y・Z の加速度を読む
#   2. 1秒ごとに、今の値を accel.json に書く（サイトの「加速度センサー（今の値）」用）
#   3. 1分に1回、templog/accel/日付.csv に記録する（グラフ用）
#        X・Y・Z … その1分間の平均[g]
#        揺れ    … その1分間で、加速度の大きさが 1g（止まっているとき）から一番離れた量[g]
#                  机をたたく・持ち上げるなどで大きくなる。止まっていれば 0 に近い
#        傾き    … 基準（accel_base.py で決めた、置いた位置での向き）から何度傾いたか[度]
#                  基準を決めていなければ空。基準は1分ごとに読み直すので、決め直しても再起動はいらない
#
# つなぎ方: accel_test.py と同じ（SPI1。38番 DIN・35番 DOUT・40番 CLK・12番 CS）
#   準備として /boot/firmware/config.txt に dtoverlay=spi1-1cs が必要
#
# 動かし方
#   普段は systemd（accel-logger.service）が自動で起動・再起動する
#   手動で試すとき : python3 accel_logger.py
#   ※ accel_test.py と同時に動かしてもよい（同じ SPI1 を交互に使う）
# ============================================================
import csv
import json
import math
import os
import sys
import time
from datetime import datetime

import spidev

import compare_data

NAME = "accel"          # 記録の名前（templog/accel/・accel.json）
SPI_BUS, SPI_DEV = 1, 0 # SPI1 の CE0（12番ピン）
VREF = 3.3              # MCP3004 の基準の電圧[V]
SAMPLE = 0.1            # 読む間隔[秒]
INTERVAL = 60           # 記録（CSV）の間隔[秒]

# 加速度センサーの換算（3.3V で使う KXR94-2050 などのアナログ出力の場合）
#   0g のとき 電源の半分（1.65V）、1g あたり 電源の1/5（0.66V）変わる
ZERO_G = VREF / 2
PER_G = VREF / 5


def read_g(spi):
    """MCP3004 の Ch0・Ch1・Ch2（X・Y・Z）を読んで、加速度[g]の (x, y, z) を返す"""
    out = []
    for ch in (0, 1, 2):
        r = spi.xfer2([0x01, (0x08 | ch) << 4, 0x00])   # 開始の合図・どの ch か・結果を受け取る分
        n = ((r[1] & 0x03) << 8) | r[2]                 # 0〜1023 の数字
        out.append((n / 1023 * VREF - ZERO_G) / PER_G)  # 数字 → 電圧 → 加速度
    return tuple(out)


def average(vals, i):
    """(x, y, z) の並び vals の、i 番目（0=X・1=Y・2=Z）の平均"""
    return sum(v[i] for v in vals) / len(vals)


def shake_of(vals):
    """揺れ = 加速度の大きさ（√(x²+y²+z²)）が 1g（止まっているとき）から一番離れた量"""
    return max(abs(math.sqrt(x * x + y * y + z * z) - 1) for x, y, z in vals)


def write_now(now, x, y, z, shake, base):
    """今の値を accel.json に書く。読み取り中に中途半端なファイルが見えないよう、置き換えで更新する。
    基準があれば、基準からの変化（dx・dy・dz）と傾き（tilt）も書く"""
    path = compare_data.now_path(NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tilt = compare_data.tilt_deg(x, y, z, base)
    d = {"iso": now.astimezone().isoformat(timespec="seconds"),
         "x": round(x, 2), "y": round(y, 2), "z": round(z, 2), "shake": round(shake, 2),
         "base": bool(base), "tilt": round(tilt, 1) if tilt is not None else None}
    if base:
        d.update(dx=round(x - base["x"], 2), dy=round(y - base["y"], 2), dz=round(z - base["z"], 2))
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(path + ".tmp", path)


def append_csv(now, x, y, z, shake, base):
    # その日のCSVファイルに1行追記する（例: /home/iot-user/templog/accel/2026-11-06.csv）
    os.makedirs(compare_data.log_dir(NAME), exist_ok=True)
    path = compare_data.csv_path(NAME, now)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(compare_data.ACCEL_HEADER)
        tilt = compare_data.tilt_deg(x, y, z, base)
        w.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), f"{x:.3f}", f"{y:.3f}", f"{z:.3f}", f"{shake:.3f}",
                    f"{tilt:.1f}" if tilt is not None else ""])


def main():
    path = f"/dev/spidev{SPI_BUS}.{SPI_DEV}"
    if not os.path.exists(path):
        # SPI1 の準備がないときはエラーで止まる（systemd が10秒後に起動し直す）
        sys.exit(f"{path} がありません。/boot/firmware/config.txt に dtoverlay=spi1-1cs を書いて再起動してください")
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEV)
    spi.mode = 0                # MCP3004 は SPI モード0
    spi.max_speed_hz = 1000000  # 1MHz
    print("加速度センサーの記録を始めました", flush=True)
    minute = []     # この1分間の (x, y, z)
    sec = []        # この1秒間の (x, y, z)
    last_sec = int(time.time())
    last_min = int(time.time() // INTERVAL)
    base = compare_data.accel_base()    # 基準（1分ごとに読み直す）
    while True:
        xyz = read_g(spi)
        minute.append(xyz)
        sec.append(xyz)
        now_t = time.time()
        if int(now_t) != last_sec:          # 1秒ごと：今の値
            write_now(datetime.now(), average(sec, 0), average(sec, 1), average(sec, 2), shake_of(sec), base)
            sec = []
            last_sec = int(now_t)
        if int(now_t // INTERVAL) != last_min:  # 1分ごと：記録
            now = datetime.now()
            x, y, z, shake = average(minute, 0), average(minute, 1), average(minute, 2), shake_of(minute)
            append_csv(now, x, y, z, shake, base)
            print(f"{now:%H:%M:%S}  X {x:+.2f}g  Y {y:+.2f}g  Z {z:+.2f}g  揺れ {shake:.2f}g", flush=True)
            minute = []
            last_min = int(now_t // INTERVAL)
            base = compare_data.accel_base()
        time.sleep(SAMPLE)


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
