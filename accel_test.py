#!/usr/bin/env python3
"""加速度センサーの読み試し: 3軸の加速度センサーを AD コンバーター MCP3004 で読み、0.5秒ごとに表示する。
Ctrl+C で止まる。"""
# ============================================================
# このプログラムの役割（試しに読むだけ）
#   加速度センサーの X・Y・Z の電圧を MCP3004 の Ch0・Ch1・Ch2 で数字（0〜1023）にし、SPI で読む
#   数字 → 電圧 → 加速度（G）に計算して表示する。読み方は accel_logger.py と同じ
#
# ラズパイの「SPI1」を使う（RPi-GP40T は「SPI0」を使っているので、線がぶつからない）
#   そのため THD-R-C の記録（thdrc-logger）は止めなくてよい
#
# つなぎ方（ブレッドボード側は本のとおり。ラズパイ側のピンだけ本と違う）
#   MCP3004 の DIN  ← 38番ピン（SPI1 MOSI）   本ではオレンジの線・19番
#   MCP3004 の DOUT → 35番ピン（SPI1 MISO）   本では黄の線・21番
#   MCP3004 の CLK  ← 40番ピン（SPI1 SCLK）   本では緑の線・23番
#   MCP3004 の CS   ← 11番ピン（GPIO17。プログラムで操作）  本では青の線・24番
#   ※ ブレッドボードの ＋ の列に 5V（2番・4番）は絶対につながない
#
# 準備（1回だけ）: SPI1 を使えるようにする
#   echo "dtoverlay=spi1-1cs" | sudo tee -a /boot/firmware/config.txt
#   sudo dtoverlay spi1-1cs（再起動するなら不要）
#
# 動かし方（ラズパイで）
#   accel-logger が動いていると 11番ピンを使えないので、先に止める: sudo systemctl stop accel-logger
#   python3 ~/temp-monitor/accel_test.py
#   止めるとき: Ctrl+C（終わったら sudo systemctl start accel-logger）
# ============================================================
import sys
import time

from gpiozero.exc import GPIOPinInUse

import accel_logger

INTERVAL = 0.5              # 表示の間隔[秒]


def main():
    try:
        adc = accel_logger.ADC()
    except GPIOPinInUse:
        sys.exit("記録のプログラム（accel-logger）が動いています。先に止めてください: sudo systemctl stop accel-logger")
    except FileNotFoundError as e:
        sys.exit(str(e))
    print("加速度センサーを読んでいます（0.5秒ごと）。止めるときは Ctrl+C", flush=True)
    print("   X[G]    Y[G]    Z[G]   | 数字（0〜1023）X / Y / Z", flush=True)
    try:
        while True:
            raw = [adc.read_count(ch) for ch in (0, 1, 2)]
            g = [accel_logger.to_g(n) for n in raw]
            print(f"{g[0]:+7.2f} {g[1]:+7.2f} {g[2]:+7.2f}   | {raw[0]:4d} / {raw[1]:4d} / {raw[2]:4d}", flush=True)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass                    # Ctrl+C が押された
    finally:
        adc.close()
    print("\n止めました")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
