#!/usr/bin/env python3
"""SHT31 温湿度ロガー: 一定間隔で測定し、日付ごとのCSVに追記する。
温度が設定値を超えたら警報メールを送る。
現在値は NOW_INTERVAL 秒ごとに測って now.json に書く（Webサイトのリアルタイム表示用）。"""
import csv
import json
import os
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage

from smbus2 import SMBus, i2c_msg

ADDR = 0x45            # ADDRピンをVINに接続した場合。GNDまたは未接続なら0x44
INTERVAL = 60          # 記録（CSV）・警報判定の間隔（秒）
NOW_INTERVAL = 5       # 現在値（now.json）の更新間隔（秒）。INTERVAL を割り切れる値にする
LOG_DIR = "/home/iot-user/templog"
# 現在値はメモリ上（/dev/shm）に置く。5秒ごとの上書きでSDカードを消耗させないため。再起動で消えてよい
NOW_PATH = "/dev/shm/temp-monitor/now.json"

TEMP_HIGH = 30.0       # この温度以上で「高温」警報
TEMP_LOW = 21.0        # この温度以下で「低温」警報
HYSTERESIS = 0.5       # 正常に戻ったと判断する幅（30℃なら29.5℃以下で回復）
REMIND_MIN = 60        # 異常が続くとき、この間隔（分）で再通知する。0なら再通知しない

# メール設定（環境変数で渡す。sht31-logger.service に書く）
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
MAIL_USER = os.environ.get("MAIL_USER", "")          # 送信元アドレス
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")  # Gmailならアプリパスワード
MAIL_TO = [a.strip() for a in os.environ.get("MAIL_TO", "").split(",") if a.strip()]


def crc8(data):
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def read_sht31(bus):
    bus.write_i2c_block_data(ADDR, 0x24, [0x00])   # 高精度・シングルショット測定
    time.sleep(0.02)
    msg = i2c_msg.read(ADDR, 6)
    bus.i2c_rdwr(msg)
    d = list(msg)
    if crc8(d[0:2]) != d[2] or crc8(d[3:5]) != d[5]:
        raise IOError("CRCエラー")
    t = -45 + 175 * (d[0] << 8 | d[1]) / 65535
    h = 100 * (d[3] << 8 | d[4]) / 65535
    return t, h


def status(t, prev="正常"):
    if t >= TEMP_HIGH:
        return "高温"
    if t <= TEMP_LOW:
        return "低温"
    # 境目でメールが連発しないよう、少し戻るまでは異常のままにする
    if prev == "高温" and t > TEMP_HIGH - HYSTERESIS:
        return "高温"
    if prev == "低温" and t < TEMP_LOW + HYSTERESIS:
        return "低温"
    return "正常"


def send_mail(subject, body):
    """送信したらTrue、メール設定がなく送らなかったらFalseを返す。"""
    if not (MAIL_USER and MAIL_PASSWORD and MAIL_TO):
        return False
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = MAIL_USER
    msg["To"] = ", ".join(MAIL_TO)
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(MAIL_USER, MAIL_PASSWORD)
        s.send_message(msg)
    return True


class Alarm:
    """状態が変わったとき（と異常が続くとき）にメールを送る。送信に失敗したら次の測定で再送する。"""

    def __init__(self):
        self.notified = "正常"   # 最後にメールで知らせた状態
        self.last_sent = 0.0

    def check(self, now, t, h, st):
        remind = (st != "正常" and REMIND_MIN > 0
                  and time.time() - self.last_sent >= REMIND_MIN * 60)
        if st == self.notified and not remind:
            return
        if st == "高温":
            subject = f"【高温警報】温度 {t:.1f}℃（{TEMP_HIGH:g}℃以上）"
        elif st == "低温":
            subject = f"【低温警報】温度 {t:.1f}℃（{TEMP_LOW:g}℃以下）"
        else:
            subject = f"【回復】温度 {t:.1f}℃ 正常に戻りました"
        if st == self.notified:
            subject = "【継続】" + subject
        body = (f"日時: {now:%Y-%m-%d %H:%M}\n温度: {t:.1f}℃\n湿度: {h:.1f}%\n"
                f"状態: {st}\n\n設定: {TEMP_LOW:g}℃以下で低温、{TEMP_HIGH:g}℃以上で高温")
        try:
            if send_mail(subject, body):
                print(f"メール送信: {subject}", flush=True)
            else:
                print(f"メール設定がないため送信しません: {subject}", flush=True)
            self.notified = st
            self.last_sent = time.time()
        except Exception as e:
            print(f"メール送信失敗（次の測定で再送）: {e}", flush=True)


def append_csv(now, t, h, st):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, now.strftime("%Y-%m-%d") + ".csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["日時", "温度(℃)", "湿度(%)", "状態"])
        w.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), f"{t:.1f}", f"{h:.1f}", st])


def write_now(now, t, h, st):
    """現在値を now.json に書く。読み取り中に中途半端なファイルが見えないよう、置き換えで更新する。"""
    path = NOW_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"iso": now.astimezone().isoformat(timespec="seconds"),
                   "temp": round(t, 1), "hum": round(h, 1), "status": st}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def main():
    alarm = Alarm()
    st = "正常"
    last_logged = None      # 最後にCSVへ記録した区切りの時刻
    with SMBus(1) as bus:
        while True:
            now = datetime.now()
            slot = int(time.time() // INTERVAL)
            try:
                t, h = read_sht31(bus)
                if slot != last_logged:         # 毎分1回：記録と警報判定
                    st = status(t, st)
                    append_csv(now, t, h, st)
                    last_logged = slot
                    print(f"{now:%H:%M:%S}  温度 {t:.1f}℃  湿度 {h:.1f}%  {st}", flush=True)
                    alarm.check(now, t, h, st)
                write_now(now, t, h, status(t, st))
            except Exception as e:
                print(f"{now:%H:%M:%S}  読み取り失敗: {e}", flush=True)
            # 次の区切り（5秒ごと）まで待つ。測定にかかった時間でずれていかないようにする
            time.sleep(NOW_INTERVAL - time.time() % NOW_INTERVAL)


if __name__ == "__main__":
    main()
