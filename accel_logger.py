#!/usr/bin/env python3
"""加速度センサーのロガー: 3軸の加速度センサーを AD コンバーター MCP3004（SPI1）で読み、記録する。"""
# ============================================================
# このプログラムの役割（記録と表示だけ。警報メールは送らない）
#   1. 0.1秒ごとに X・Y・Z の加速度を読む
#   2. 1秒ごとに、今の値を accel.json に書く（サイトの「加速度センサー（今の値）」用）
#        揺れは「直近5秒間の最大」。サイトは5秒ごとに読みに来るので、そのあいだの短い揺れも見逃さない
#   3. 1分に1回、templog/accel/日付.csv に記録する（グラフ用）
#        X・Y・Z … その1分間の平均[g]
#        揺れ    … その1分間で、加速度の大きさが 1g（止まっているとき）から一番離れた量[g]
#                  机をたたく・持ち上げるなどで大きくなる。止まっていれば 0 に近い
#        傾き    … 基準（accel_base.py で決めた、置いた位置での向き）から何度傾いたか[度]
#                  基準を決めていなければ空。基準は1分ごとに読み直すので、決め直しても再起動はいらない
#
# つなぎ方: accel_test.py と同じ（SPI1。38番 DIN・35番 DOUT・40番 CLK、CS は 11番を自分で操作する）
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
from collections import deque
from datetime import datetime

import spidev
from gpiozero import DigitalOutputDevice

import compare_data

NAME = "accel"          # 記録の名前（templog/accel/・accel.json）
SPI_BUS, SPI_DEV = 1, 0 # SPI1（38番・35番・40番ピン）
# MCP3004 の CS（呼び出しの合図）は 11番ピン（GPIO17）を、このプログラムが自分で Low・High にする
# （SPI1 の CE0 の 12番ピンでは MCP3004 に合図が届かなかったため）
CS_PIN = "BOARD11"
VREF = 3.3              # MCP3004 の基準の電圧[V]
SAMPLE = 0.1            # 読む間隔[秒]
INTERVAL = 60           # 記録（CSV）の間隔[秒]
NOW_SHAKE_SEC = 5       # 今の値の揺れを、直近何秒間の最大にするか（サイトが読みに来る間隔と同じ）

# 加速度センサーの換算（3.3V で使う KXR94-2050 などのアナログ出力の場合）
#   0g のとき 電源の半分（1.65V）、1g あたり 電源の1/5（0.66V）変わる
ZERO_G = VREF / 2
PER_G = VREF / 5


class ADC:
    """MCP3004（AD コンバーター）。SPI1 でやり取りし、CS は 11番ピンを自分で操作する"""

    def __init__(self):
        path = f"/dev/spidev{SPI_BUS}.{SPI_DEV}"
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} がありません。/boot/firmware/config.txt に dtoverlay=spi1-1cs を書いて再起動してください")
        # ほかのプログラム（accel-logger など）が 11番ピンを使っていると、ここで GPIOPinInUse になる
        self.cs = DigitalOutputDevice(CS_PIN, initial_value=True)   # High = 呼び出していない
        self.spi = spidev.SpiDev()
        self.spi.open(SPI_BUS, SPI_DEV)
        self.spi.mode = 0               # MCP3004 は SPI モード0
        # 読む速さ。加速度センサー（KXR94）の出力は電気が弱い（出力の抵抗が大きい）ので、
        # 速く読むと MCP3004 の中のコンデンサーが正しい電圧になる前に読んでしまう。ゆっくり（100kHz）読む
        self.spi.max_speed_hz = 100000

    def _read_once(self, ch):
        self.cs.off()                   # CS を Low にして MCP3004 を呼び出す
        r = self.spi.xfer2([0x01, (0x08 | ch) << 4, 0x00])  # 開始の合図・どの ch か・結果を受け取る分
        self.cs.on()                    # CS を High に戻す
        return ((r[1] & 0x03) << 8) | r[2]

    def read_count(self, ch):
        """Ch（0〜3）を読んで 0〜1023 の数字を返す。
        ch を切りかえた直後は前の ch の電圧が残りやすいので、2回読んで2回目を使う"""
        self._read_once(ch)
        return self._read_once(ch)

    def read_g(self):
        """Ch0・Ch1・Ch2（X・Y・Z）を読んで、加速度[G]の (x, y, z) を返す"""
        return tuple(to_g(self.read_count(ch)) for ch in (0, 1, 2))

    def close(self):
        self.spi.close()
        self.cs.close()


def to_g(n):
    """MCP3004 の数字（0〜1023）→ 電圧 → 加速度[G]"""
    return (n / 1023 * VREF - ZERO_G) / PER_G


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
    try:
        adc = ADC()
    except Exception as e:
        # SPI1 の準備がない・11番ピンが使われているときはエラーで止まる（systemd が10秒後に起動し直す）
        sys.exit(f"加速度センサーを読めません: {e}")
    print("加速度センサーの記録を始めました", flush=True)
    minute = []     # この1分間の (x, y, z)
    sec = []        # この1秒間の (x, y, z)
    last_sec = int(time.time())
    last_min = int(time.time() // INTERVAL)
    base = compare_data.accel_base()    # 基準（1分ごとに読み直す）
    recent_shakes = deque(maxlen=NOW_SHAKE_SEC)     # 直近5秒の、1秒ごとの揺れの最大
    while True:
        xyz = adc.read_g()
        minute.append(xyz)
        sec.append(xyz)
        now_t = time.time()
        if int(now_t) != last_sec:          # 1秒ごと：今の値
            recent_shakes.append(shake_of(sec))
            write_now(datetime.now(), average(sec, 0), average(sec, 1), average(sec, 2), max(recent_shakes), base)
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
