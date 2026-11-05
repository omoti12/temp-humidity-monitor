#!/usr/bin/env python3
"""加速度センサーの読み試し: 3軸の加速度センサーを AD コンバーター MCP3004 で読み、0.5秒ごとに表示する。
Ctrl+C で止まる。"""
# ============================================================
# このプログラムの役割（試しに読むだけ。温湿度の記録や警報とは関係ない）
#   加速度センサーの X・Y・Z の電圧を MCP3004 の Ch0・Ch1・Ch2 で数字（0〜1023）にし、SPI で読む
#   数字 → 電圧 → 加速度（g）に計算して表示する
#
# つなぎ方（本の配線と同じ。ただし青の線だけ 24番 → 26番ピンに差しかえる）
#   オレンジ: 19番ピン（SPI MOSI）  黄: 21番ピン（SPI MISO）  緑: 23番ピン（SPI SCLK）
#   青: 26番ピン（CS。MCP3004 を選ぶ線）
#   ※ 24番ピン（GPIO8）は RPi-GP40T（THD-R-C を読む基板）の CS と同じ線なので、
#     そのままだと2つが同時に返事をしてしまい、正しく読めない
#
# 動かし方（ラズパイで）
#   1. THD-R-C の記録を止める（同じ SPI の線を使うため）: sudo systemctl stop thdrc-logger
#   2. python3 ~/temp-monitor/accel_test.py
#   3. 止めるとき: Ctrl+C
#   4. THD-R-C の記録を戻す: sudo systemctl start thdrc-logger
# ============================================================
import sys
import time

import spidev
from gpiozero import DigitalOutputDevice
from gpiozero.exc import GPIOPinInUse

CS_PIN = "BOARD26"      # MCP3004 の CS（青の線）
GP40_CS_PIN = "BOARD24" # RPi-GP40T の CS。読んでいるあいだ「選ばない（High）」にしておく
VREF = 3.3              # MCP3004 の基準の電圧[V]（3.3V につないでいる）
INTERVAL = 0.5          # 表示の間隔[秒]

# 加速度センサーの換算（3.3V で使う KXR94-2050 などのアナログ出力の場合）
#   0g のとき 電源の半分（1.65V）、1g あたり 電源の1/5（0.66V）変わる
ZERO_G = VREF / 2
PER_G = VREF / 5


def read_ch(spi, cs, ch):
    """MCP3004 の ch（0〜3）を読んで 0〜1023 の数字を返す"""
    cs.off()                                    # CS を Low にして MCP3004 を選ぶ
    r = spi.xfer2([0x01, (0x08 | ch) << 4, 0x00])  # 開始の合図・どの ch か・結果を受け取る分
    cs.on()                                     # CS を High に戻す
    return ((r[1] & 0x03) << 8) | r[2]          # 10ビットの結果を取り出す


def main():
    try:
        # RPi-GP40T を選ばないようにしておく（THD-R-C の記録が動いていると、この線を使えない）
        gp40_cs = DigitalOutputDevice(GP40_CS_PIN, initial_value=True)
    except GPIOPinInUse:
        sys.exit("THD-R-C の記録が動いています。先に止めてください: sudo systemctl stop thdrc-logger")
    cs = DigitalOutputDevice(CS_PIN, initial_value=True)
    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.mode = 0                # MCP3004 は SPI モード0
    spi.max_speed_hz = 1000000  # 1MHz（MCP3004 を 3.3V で使うときの上限より遅くする）
    print("加速度センサーを読んでいます（0.5秒ごと）。止めるときは Ctrl+C", flush=True)
    print("   X[g]    Y[g]    Z[g]   | 数字（0〜1023）X / Y / Z", flush=True)
    try:
        while True:
            raw = [read_ch(spi, cs, ch) for ch in (0, 1, 2)]
            g = [(n / 1023 * VREF - ZERO_G) / PER_G for n in raw]
            print(f"{g[0]:+7.2f} {g[1]:+7.2f} {g[2]:+7.2f}   | {raw[0]:4d} / {raw[1]:4d} / {raw[2]:4d}", flush=True)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass                    # Ctrl+C が押された
    finally:
        spi.close()
        cs.close()
        gp40_cs.close()
    print("\n止めました。THD-R-C の記録を戻すときは: sudo systemctl start thdrc-logger")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
