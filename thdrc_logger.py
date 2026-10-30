#!/usr/bin/env python3
"""THD-R-C 比較用ロガー: 4〜20mA の温湿度センサー THD-R-C を RPi-GP40T で読み、
SHT31 と比べられるように記録する。"""
# ============================================================
# このプログラムの役割（比較の実験用。警報メールは送らない）
#   1. 5秒ごとに RPi-GP40T で THD-R-C の温度（ch0）・湿度（ch1）を読む
#   2. 読んだ値を thdrc.json に書く（サイトの「SHT31 と THD-R-C の比較」用）
#   3. 1分に1回、templog/thdrc/日付.csv に記録する（グラフ用）
#
# つなぎ方: 4〜20mAを抵抗（150Ω）に流して電圧（0.6〜3V）に変え、RPi-GP40Tの0-5Vレンジで読む
#   温度: THD-R-C の A → GP40T の A0（A0とG0のあいだに150Ω）
#   湿度: THD-R-C の B → GP40T の A1（A1とG1のあいだに150Ω）
#   G0・G1 は THD-R-C の電源の −（GND）につなぐ
#
# 動かし方
#   普段は systemd（thdrc-logger.service）が自動で起動・再起動する
#   手動で試すとき : python3 thdrc_logger.py
#   ※ 動いている間は RPi-GP40T を使っているので、ほかのプログラム（thdrc_read.py など）で
#     GP40T を読むときは先に止める : sudo systemctl stop thdrc-logger
# ============================================================
import csv
import json
import os
import time
from datetime import datetime

import spidev
from gpiozero import LED, DigitalOutputDevice

import alarm_settings
import compare_data

NAME = "thdrc"      # 記録の名前（templog/thdrc/・thdrc.json）

# ============================================================
# 設定値
# ============================================================

# THD-R-C の測定範囲（4mA〜20mAに対応する値。取扱説明書で確認済み）
TEMP_LO, TEMP_HI = -19.9, 60.0    # 温度[℃]
HUMI_LO, HUMI_HI = 0.0, 99.9      # 湿度[%RH]
SHUNT = 150.0       # 4〜20mAを電圧に変える抵抗[Ω]
TEMP_CH = 0         # 温度をつないだ ch（A0）
HUMI_CH = 1         # 湿度をつないだ ch（A1）
# 4〜20mAとして正しいとみなす範囲[mA]。外れたら断線・故障の疑いとして記録しない
MA_MIN, MA_MAX = 3.6, 20.5

INTERVAL = 60       # 記録（CSV）の間隔（秒）
NOW_INTERVAL = 5    # 現在値（thdrc.json）の更新間隔（秒）

# RPi-GP40T の設定
RANGE_0_5V = 6      # レンジ選択レジスタの値（0-5V。実際は0〜5.12V）
LSB_MV = 1.25       # 0-5Vレンジの1目盛り[mV]
PIN_CS = 8          # SPI CS（GPIO8）。spi0-0cs の設定で GPIO として自分で操作する
PIN_PWR = 27        # RPi-GP40T の絶縁電源（GPIO27）


# ============================================================
# RPi-GP40T の読み取り
# ============================================================

class GP40:
    def __init__(self):
        self.pwr = LED(PIN_PWR)
        self.pwr.on()
        time.sleep(0.5)                 # 絶縁電源の安定待ち
        self.cs = DigitalOutputDevice(PIN_CS, initial_value=True)
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.mode = 1
        self.spi.max_speed_hz = 10000000
        for ch in (TEMP_CH, HUMI_CH):
            self._xfer([((5 + ch) << 1) | 1, RANGE_0_5V, 0x00, 0x00])   # 0-5Vレンジにする

    def _xfer(self, wd):
        self.cs.off()
        rd = self.spi.xfer(wd)
        self.cs.on()
        return rd

    def read_volt(self, ch):
        wd = [0xC0 + (ch << 2), 0x00, 0x00, 0x00]
        self._xfer(wd)                  # 1回目でchを指定
        rd = self._xfer(wd)             # 2回目で結果を受け取る
        raw = (rd[2] << 4) + (rd[3] >> 4)
        return raw * LSB_MV / 1000


def to_value(ma, lo, hi):
    """4〜20mAを測定値に換算する。範囲外（断線など）なら None"""
    if not (MA_MIN <= ma <= MA_MAX):
        return None
    return lo + (ma - 4.0) / 16.0 * (hi - lo)


def read_thdrc(gp):
    """温度・湿度と、それぞれの電流[mA]を返す。測れなかった値は None"""
    mt = gp.read_volt(TEMP_CH) / SHUNT * 1000
    mh = gp.read_volt(HUMI_CH) / SHUNT * 1000
    return to_value(mt, TEMP_LO, TEMP_HI), to_value(mh, HUMI_LO, HUMI_HI), mt, mh


# ============================================================
# 保存（CSVと現在値）
# ============================================================

def fmt1(v):
    return "" if v is None else f"{v:.1f}"


def append_csv(now, t, h, mt, mh):
    # その日のCSVファイルに1行追記する（例: /home/iot-user/templog/thdrc/2026-10-29.csv）
    os.makedirs(compare_data.log_dir(NAME), exist_ok=True)
    path = compare_data.csv_path(NAME, now)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日時", "温度(℃)", "湿度(%)", "温度電流(mA)", "湿度電流(mA)"])
        w.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), fmt1(t), fmt1(h), f"{mt:.2f}", f"{mh:.2f}"])


def write_now(now, t, h, mt, mh):
    """現在値を thdrc.json に書く。読み取り中に中途半端なファイルが見えないよう、置き換えで更新する"""
    path = compare_data.now_path(NAME)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"iso": now.astimezone().isoformat(timespec="seconds"),
                   "temp": None if t is None else round(t, 1),
                   "hum": None if h is None else round(h, 1),
                   "temp_ma": round(mt, 2), "hum_ma": round(mh, 2)}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# ============================================================
# メインの処理（ずっと繰り返す）
# ============================================================

def main():
    gp = GP40()             # 開けないときはエラーで止まり、systemd が10秒後に起動し直す
    last_logged = None      # 最後にCSVへ記録した区切りの時刻
    last_fail_log = 0.0     # 最後に「読み取り失敗」をログに書いた時刻（ログは1分に1回まで）
    s = alarm_settings.load()   # 補正の設定（1分ごとに読み直す）
    while True:
        now = datetime.now()
        slot = int(time.time() // INTERVAL)
        if slot != last_logged:
            s = alarm_settings.load()   # 設定画面で変えた補正がここで反映される
        try:
            t, h, mt, mh = read_thdrc(gp)
            t, h = alarm_settings.calibrate(NAME, t, h, s)     # 補正（電流[mA]は測ったままの値を記録する）
            write_now(now, t, h, mt, mh)
            if slot != last_logged:         # 毎分1回：記録
                append_csv(now, t, h, mt, mh)
                last_logged = slot
                print(f"{now:%H:%M:%S}  温度 {fmt1(t) or '--'}℃ ({mt:.2f}mA)"
                      f"  湿度 {fmt1(h) or '--'}% ({mh:.2f}mA)", flush=True)
            last_fail_log = 0.0
        except Exception as e:
            # 読めなくても止まらず、次の測定（5秒後）で続ける。ログは1分に1回だけ書く
            if time.time() - last_fail_log >= 60:
                print(f"{now:%H:%M:%S}  読み取り・記録でエラー: {e}", flush=True)
                last_fail_log = time.time()
        # 次の区切り（5秒ごと）まで待つ
        time.sleep(NOW_INTERVAL - time.time() % NOW_INTERVAL)


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
