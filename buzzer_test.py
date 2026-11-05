#!/usr/bin/env python3
"""ブザーの鳴らし試し: ラズパイのピンにつないだブザーを「ピッ、ピッ」と鳴らし続ける。Ctrl+C で止まる。"""
# ============================================================
# このプログラムの役割（試しに鳴らすだけ。温湿度の記録や警報とは関係ない）
#   ブザーをつないだピンに 2000Hz の信号を 0.3秒ずつ出したり止めたりして、ピッ、ピッと鳴らす
#   電子ブザー（電源を入れるだけで鳴るもの）でも、圧電ブザー（信号で鳴らすもの）でも音が出る
#
# 動かし方（ラズパイで）
#   python3 ~/temp-monitor/buzzer_test.py ピン番号
#     ピン番号 = ラズパイの40本のピンの番号（1〜40。GPIO の番号ではない）
#     例: 33番ピンにつないだとき → python3 ~/temp-monitor/buzzer_test.py 33
#   止めるとき: Ctrl+C
# ============================================================
import sys
import time

from gpiozero import PWMOutputDevice

FREQ = 2000         # 音の高さ[Hz]
ON_SEC = 0.3        # 鳴らす時間[秒]
OFF_SEC = 0.3       # 止める時間[秒]

# 使ってはいけないピン（電源・GND と、ほかの機器が使っているピン）
NG_PINS = {
    1: "3.3V", 17: "3.3V", 2: "5V", 4: "5V",
    6: "GND", 9: "GND", 14: "GND", 20: "GND", 25: "GND", 30: "GND", 34: "GND", 39: "GND",
    3: "SHT31（I2C SDA）", 5: "SHT31（I2C SCL）",
    19: "RPi-GP40T（SPI）", 21: "RPi-GP40T（SPI）", 23: "RPi-GP40T（SPI）", 24: "RPi-GP40T（SPI CS）",
    13: "RPi-GP40T（電源）",
}


def main():
    try:
        pin = int(sys.argv[1])
    except (IndexError, ValueError):
        sys.exit("ピン番号（1〜40）を付けて動かしてください。例: python3 ~/temp-monitor/buzzer_test.py 33")
    if not 1 <= pin <= 40:
        sys.exit("ピン番号は 1〜40 で指定してください")
    if pin in NG_PINS:
        sys.exit(f"{pin}番ピンは {NG_PINS[pin]} なので使えません。ブザーの線がつながっているピンを確かめてください")

    buzzer = PWMOutputDevice(f"BOARD{pin}", frequency=FREQ)
    print(f"{pin}番ピンのブザーを鳴らしています。止めるときは Ctrl+C", flush=True)
    try:
        while True:
            buzzer.value = 0.5      # 鳴らす（信号のオン・オフを半分ずつ）
            time.sleep(ON_SEC)
            buzzer.value = 0        # 止める
            time.sleep(OFF_SEC)
    except KeyboardInterrupt:
        pass                        # Ctrl+C が押された
    finally:
        buzzer.off()                # 鳴りっぱなしにならないよう、必ず止めてから終わる
        buzzer.close()
    print("\n止めました")


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    main()
