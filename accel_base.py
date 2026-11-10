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
#   ※ accel-logger が動いていれば、その今の値（accel.json）を5秒分平均する。止まっていれば直接読む
# ============================================================
import json
import os
import sys
import time
from datetime import datetime

from gpiozero.exc import GPIOPinInUse

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
    print(f"{SECONDS}秒間はかります。加速度センサーにさわらないでください...", flush=True)
    vals = []
    end = time.time() + SECONDS
    if compare_data.now_reading(compare_data.ACCEL):
        # 記録のプログラムが動いている: その今の値（1秒ごとに書かれる）を読む
        while time.time() < end:
            n = compare_data.now_reading(compare_data.ACCEL)
            if n:
                vals.append((n["x"], n["y"], n["z"]))
            time.sleep(1)
    else:
        # 記録のプログラムが止まっている: 直接読む
        try:
            adc = accel_logger.ADC()
        except (GPIOPinInUse, FileNotFoundError) as e:
            sys.exit(f"加速度センサーを読めません: {e}")
        while time.time() < end:
            vals.append(adc.read_g())
            time.sleep(accel_logger.SAMPLE)
        adc.close()
    if not vals:
        sys.exit("加速度センサーの値を読めませんでした")
    x, y, z = (accel_logger.average(vals, i) for i in range(3))
    # 止めて置いてあれば、加速度の大きさは約 1G（地球の重力）。大きく違えば、配線などの問題で読めていないので基準にしない
    if not 0.7 < (x * x + y * y + z * z) ** 0.5 < 1.3:
        sys.exit(f"値がおかしいので基準にしませんでした（X {x:+.2f}G  Y {y:+.2f}G  Z {z:+.2f}G）。配線を確認してください")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"x": round(x, 4), "y": round(y, 4), "z": round(z, 4),
                   "set": datetime.now().astimezone().isoformat(timespec="seconds")}, f)
    os.replace(path + ".tmp", path)
    print(f"基準にしました: X {x:+.2f}G  Y {y:+.2f}G  Z {z:+.2f}G（1分以内に記録とサイトに反映されます）")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
