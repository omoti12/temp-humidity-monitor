#!/usr/bin/env python3
"""加速度センサーの読み試し: 3軸の加速度センサーを AD コンバーター MCP3004 で読み、0.5秒ごとに表示する。
Ctrl+C で止まる。"""
# ============================================================
# このプログラムの役割（試しに読むだけ。温湿度の記録や警報とは関係ない）
#   加速度センサーの X・Y・Z の電圧を MCP3004 の Ch0・Ch1・Ch2 で数字（0〜1023）にし、SPI で読む
#   数字 → 電圧 → 加速度（g）に計算して表示する
#
# ラズパイの「SPI1」を使う（RPi-GP40T は「SPI0」を使っているので、線がぶつからない）
#   そのため THD-R-C の記録（thdrc-logger）は止めなくてよい。両方同時に使える
#
# つなぎ方（ブレッドボード側は本のとおり。ラズパイ側のピンだけ本と違う）
#   MCP3004 の DIN  ← 38番ピン（SPI1 MOSI）   本ではオレンジの線・19番
#   MCP3004 の DOUT → 35番ピン（SPI1 MISO）   本では黄の線・21番
#   MCP3004 の CLK  ← 40番ピン（SPI1 SCLK）   本では緑の線・23番
#   MCP3004 の CS   ← 12番ピン（SPI1 CE0）    本では青の線・24番
#   ブレッドボードの ＋ の列 ← 17番ピン（3.3V）、－ の列 ← GND（20番・25番など）
#   ※ ＋ の列に 5V（2番・4番）は絶対につながない（ラズパイが壊れるおそれがある）
#
# 準備（1回だけ）: SPI1 を使えるようにして再起動する
#   echo "dtoverlay=spi1-1cs" | sudo tee -a /boot/firmware/config.txt
#   sudo reboot
#   → ls /dev/spidev* で /dev/spidev1.0 が出れば準備OK
#
# 動かし方（ラズパイで）
#   python3 ~/temp-monitor/accel_test.py
#   止めるとき: Ctrl+C
# ============================================================
import os
import sys
import time

import spidev

SPI_BUS, SPI_DEV = 1, 0     # SPI1 の CE0（12番ピン）
VREF = 3.3                  # MCP3004 の基準の電圧[V]（3.3V につないでいる）
INTERVAL = 0.5              # 表示の間隔[秒]

# 加速度センサーの換算（3.3V で使う KXR94-2050 などのアナログ出力の場合）
#   0g のとき 電源の半分（1.65V）、1g あたり 電源の1/5（0.66V）変わる
ZERO_G = VREF / 2
PER_G = VREF / 5


def read_ch(spi, ch):
    """MCP3004 の ch（0〜3）を読んで 0〜1023 の数字を返す（CS はラズパイが自動で出す）"""
    r = spi.xfer2([0x01, (0x08 | ch) << 4, 0x00])  # 開始の合図・どの ch か・結果を受け取る分
    return ((r[1] & 0x03) << 8) | r[2]          # 10ビットの結果を取り出す


def main():
    if not os.path.exists(f"/dev/spidev{SPI_BUS}.{SPI_DEV}"):
        sys.exit("SPI1 が使えるようになっていません。準備の2行を実行してから再起動してください:\n"
                 '  echo "dtoverlay=spi1-1cs" | sudo tee -a /boot/firmware/config.txt\n'
                 "  sudo reboot")
    spi = spidev.SpiDev()
    spi.open(SPI_BUS, SPI_DEV)
    spi.mode = 0                # MCP3004 は SPI モード0
    spi.max_speed_hz = 1000000  # 1MHz（MCP3004 を 3.3V で使うときの上限より遅くする）
    print("加速度センサーを読んでいます（0.5秒ごと）。止めるときは Ctrl+C", flush=True)
    print("   X[g]    Y[g]    Z[g]   | 数字（0〜1023）X / Y / Z", flush=True)
    try:
        while True:
            raw = [read_ch(spi, ch) for ch in (0, 1, 2)]
            g = [(n / 1023 * VREF - ZERO_G) / PER_G for n in raw]
            print(f"{g[0]:+7.2f} {g[1]:+7.2f} {g[2]:+7.2f}   | {raw[0]:4d} / {raw[1]:4d} / {raw[2]:4d}", flush=True)
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass                    # Ctrl+C が押された
    finally:
        spi.close()
    print("\n止めました")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
