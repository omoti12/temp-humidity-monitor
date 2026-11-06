#!/usr/bin/env python3
"""加速度センサーの基準を決める: 今置いてある位置での X・Y・Z を5秒間はかって平均し、「基準」として保存する。"""
# ============================================================
# このプログラムの役割
#   今の置き方を「正しい位置」として覚える。サイトと記録では、ここからどれだけ変わったか・何度傾いたかを出す
#   基準は ~/.config/temp-monitor/accel_base.json に保存する（accel-logger は1分以内に読み直す）
#
# 動かし方（ラズパイで。測っている5秒間はさわらない）
#   基準を決める : python3 ~/temp-monitor/accel_base.py
#   基準をやめる : python3 ~/temp-monitor/accel_base.py --clear
#   ※ accel-logger が動いたままでよい（同じ SPI1 を交互に使う）
# ============================================================
import json
import os
import sys
import time
from datetime import datetime

import spidev

import accel_logger
import compare_data

SECONDS = 5         # はかる時間[秒]


def main():
    path = compare_data.ACCEL_BASE_PATH
    if len(sys.argv) > 1 and sys.argv[1] == "--clear":
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        print("基準をやめました（1分以内に反映されます）")
        return
    dev = f"/dev/spidev{accel_logger.SPI_BUS}.{accel_logger.SPI_DEV}"
    if not os.path.exists(dev):
        sys.exit(f"{dev} がありません。SPI1 の準備（dtoverlay=spi1-1cs）をしてください")
    spi = spidev.SpiDev()
    spi.open(accel_logger.SPI_BUS, accel_logger.SPI_DEV)
    spi.mode = 0
    spi.max_speed_hz = 1000000
    print(f"{SECONDS}秒間はかります。加速度センサーにさわらないでください...", flush=True)
    vals = []
    end = time.time() + SECONDS
    while time.time() < end:
        vals.append(accel_logger.read_g(spi))
        time.sleep(accel_logger.SAMPLE)
    spi.close()
    x, y, z = (accel_logger.average(vals, i) for i in range(3))
    # 止めて置いてあれば、加速度の大きさは約 1g（地球の重力）。大きく違えば、配線などの問題で読めていないので基準にしない
    if not 0.7 < (x * x + y * y + z * z) ** 0.5 < 1.3:
        sys.exit(f"値がおかしいので基準にしませんでした（X {x:+.2f}g  Y {y:+.2f}g  Z {z:+.2f}g）。配線を確認してください")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"x": round(x, 4), "y": round(y, 4), "z": round(z, 4),
                   "set": datetime.now().astimezone().isoformat(timespec="seconds")}, f)
    os.replace(path + ".tmp", path)
    print(f"基準にしました: X {x:+.2f}g  Y {y:+.2f}g  Z {z:+.2f}g（1分以内に記録とサイトに反映されます）")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
