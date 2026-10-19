#!/usr/bin/env python3
"""SHT31 温湿度ロガー: 一定間隔で測定し、日付ごとのCSVに追記する。
温度が設定値を超えたら警報メールを送る。
現在値は NOW_INTERVAL 秒ごとに測って now.json に書く（Webサイトのリアルタイム表示用）。"""
# ============================================================
# このプログラムの役割（システムの中心）
#   1. 5秒ごとに温湿度センサー（SHT31）から温度・湿度を読む
#   2. 読んだ値を「現在値」として now.json に書く（サイトのリアルタイム表示用）
#   3. 1分に1回、値をCSVファイルに記録し、警報（高温・低温）を判定する
#   4. 状態が変わったら警報メールを送る
#
# 動かし方
#   普段は systemd（sht31-logger.service）が自動で起動・再起動する
#   手動で試すとき     : python3 sht31_logger.py
#   テストメールを送る : python3 sht31_logger.py --test-mail
# ============================================================

# ---- 使う部品（ライブラリ）の読み込み ----
import csv                      # CSVファイルの読み書き
import json                     # JSONファイル（now.json）の書き込み
import os                       # フォルダ作成・ファイル操作
import smtplib                  # メール送信のエラーの種類を見分ける
import sys                      # コマンドの引数を見る・エラーで終了する
import time                     # 待ち時間・現在時刻（秒）
from datetime import datetime   # 日付・時刻

from smbus2 import SMBus, i2c_msg   # I2C通信でセンサーとやり取りする

import alert_mail               # 警報メールの設定・宛先・送信（alert_mail.py）

# ============================================================
# 設定値（ここを書き換えると動きが変わる）
# ============================================================

# センサーのI2Cアドレス。基板のADDRピンの配線で 0x44 か 0x45 になる
# （分からないときは `i2cdetect -y 1` で確認できる）
ADDR = 0x45            # ADDRピンをVINに接続した場合。GNDまたは未接続なら0x44
INTERVAL = 60          # 記録（CSV）・警報判定の間隔（秒）
NOW_INTERVAL = 5       # 現在値（now.json）の更新間隔（秒）。INTERVAL を割り切れる値にする
# CSVを保存するフォルダ（SDカード上）。1日1ファイル（例: 2026-10-19.csv）
LOG_DIR = "/home/iot-user/templog"
# 現在値はメモリ上（/dev/shm）に置く。5秒ごとの上書きでSDカードを消耗させないため。再起動で消えてよい
NOW_PATH = "/dev/shm/temp-monitor/now.json"

# 警報の基準。変えるときは sht31_graph.py・web/app.py・pages/publish.py も同じ値にする
TEMP_HIGH = 30.0       # この温度以上で「高温」警報
TEMP_LOW = 21.0        # この温度以下で「低温」警報
HYSTERESIS = 0.5       # 正常に戻ったと判断する幅（30℃なら29.5℃以下で回復）
REMIND_MIN = 60        # 異常が続くとき、この間隔（分）で再通知する。0なら再通知しない

# メールの設定（送信元・パスワード）と宛先は alert_mail.py が送るたびに読む
#   送信元・パスワード : /home/iot-user/.config/temp-monitor/mail.env
#   宛先              : サイトの「警報メールの宛先」画面で追加・削除できる


# ============================================================
# センサーの読み取り
# ============================================================

def crc8(data):
    # センサーから届いたデータが壊れていないか確かめるための計算（CRC-8）
    # SHT31のデータシートで決められた方式（初期値0xFF、多項式0x31）
    crc = 0xFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def read_sht31(bus):
    # センサーに「1回測って」と命令する（コマンド 0x24 0x00）
    bus.write_i2c_block_data(ADDR, 0x24, [0x00])   # 高精度・シングルショット測定
    # 測定が終わるまで少し待つ（高精度測定は最大約15ミリ秒）
    time.sleep(0.02)
    # 結果の6バイトを読む: [温度2バイト, 温度のCRC, 湿度2バイト, 湿度のCRC]
    msg = i2c_msg.read(ADDR, 6)
    bus.i2c_rdwr(msg)
    d = list(msg)
    # 通信エラーで値が化けていないか確認。化けていたら記録しない
    if crc8(d[0:2]) != d[2] or crc8(d[3:5]) != d[5]:
        raise IOError("CRCエラー")
    # 生の値（0〜65535）を ℃ と % に変換する（データシートの換算式）
    t = -45 + 175 * (d[0] << 8 | d[1]) / 65535
    h = 100 * (d[3] << 8 | d[4]) / 65535
    return t, h


# ============================================================
# 状態の判定（正常 / 高温 / 低温）
# ============================================================

def status(t, prev="正常"):
    # t: 今の温度、prev: 前回の状態。戻り値は "正常" "高温" "低温" のどれか
    t = round(t, 1)     # 表示・記録と同じ小数第1位の値で判定する（29.97℃が「30.0℃ 正常」と見えないように）
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


# ============================================================
# 警報メール
# ============================================================

def send_mail(subject, body):
    """警報メールの宛先全員に送る。送信したらTrue、メール設定がなく送らなかったらFalseを返す。"""
    return alert_mail.send(subject, body)


class Alarm:
    """状態が変わったとき（と異常が続くとき）にメールを送る。送信に失敗したら次の測定で再送する。"""

    def __init__(self):
        self.notified = "正常"   # 最後にメールで知らせた状態
        self.last_sent = 0.0    # 最後にメールを送った時刻（再通知の判断に使う）

    def check(self, now, t, h, st):
        # 1分ごとに呼ばれる。送る必要があるときだけメールを送る
        # 異常が続いていて、前回の送信から REMIND_MIN 分たったら再通知する
        remind = (st != "正常" and REMIND_MIN > 0
                  and time.time() - self.last_sent >= REMIND_MIN * 60)
        # 状態が前回知らせたときと同じで、再通知の時期でもなければ何もしない
        if st == self.notified and not remind:
            return
        # 状態に合わせて件名を作る
        if st == "高温":
            subject = f"【高温警報】温度 {t:.1f}℃（{TEMP_HIGH:g}℃以上）"
        elif st == "低温":
            subject = f"【低温警報】温度 {t:.1f}℃（{TEMP_LOW:g}℃以下）"
        else:
            subject = f"【回復】温度 {t:.1f}℃ 正常に戻りました"
        if st == self.notified:
            subject = "【継続】" + subject      # 再通知のときは【継続】を付ける
        body = (f"日時: {now:%Y-%m-%d %H:%M}\n温度: {t:.1f}℃\n湿度: {h:.1f}%\n"
                f"状態: {st}\n\n設定: {TEMP_LOW:g}℃以下で低温、{TEMP_HIGH:g}℃以上で高温")
        try:
            if send_mail(subject, body):
                print(f"メール送信: {subject}", flush=True)
            else:
                print(f"メール設定がないため送信しません: {subject}", flush=True)
            # 送れたら「知らせた状態」を更新する
            self.notified = st
            self.last_sent = time.time()
        except Exception as e:
            # 送れなかったら notified を更新しない → 次の測定（1分後）でもう一度送る
            print(f"メール送信失敗（次の測定で再送）: {e}", flush=True)


# ============================================================
# 保存（CSVと現在値）
# ============================================================

def append_csv(now, t, h, st):
    # その日のCSVファイルに1行追記する（例: /home/iot-user/templog/2026-10-19.csv）
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, now.strftime("%Y-%m-%d") + ".csv")
    new = not os.path.exists(path)
    # "a" は追記モード。Excelで開ける形式（UTF-8）で書く
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            # その日の最初の1行目には見出しを書く
            w.writerow(["日時", "温度(℃)", "湿度(%)", "状態"])
        w.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), f"{t:.1f}", f"{h:.1f}", st])


def write_now(now, t, h, st):
    """現在値を now.json に書く。読み取り中に中途半端なファイルが見えないよう、置き換えで更新する。"""
    path = NOW_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # いったん .tmp に書いてから、本物と入れ替える
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"iso": now.astimezone().isoformat(timespec="seconds"),
                   "temp": round(t, 1), "hum": round(h, 1), "status": st}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# ============================================================
# メインの処理（ずっと繰り返す）
# ============================================================

def main():
    alarm = Alarm()
    st = "正常"             # 今の状態（1分ごとに更新）
    last_logged = None      # 最後にCSVへ記録した区切りの時刻
    # I2Cのバス1番（ラズパイのGPIO2=SDA、GPIO3=SCL）を開く
    with SMBus(1) as bus:
        while True:
            now = datetime.now()
            # 今が何番目の「1分の区切り」か。この番号が変わったら新しい1分に入ったということ
            slot = int(time.time() // INTERVAL)
            try:
                t, h = read_sht31(bus)
                if slot != last_logged:         # 毎分1回：記録と警報判定
                    st = status(t, st)
                    append_csv(now, t, h, st)
                    last_logged = slot
                    # print の内容は動作ログに残る（journalctl -u sht31-logger で見られる）
                    print(f"{now:%H:%M:%S}  温度 {t:.1f}℃  湿度 {h:.1f}%  {st}", flush=True)
                    alarm.check(now, t, h, st)
                # 現在値は毎回（5秒ごと）書く
                write_now(now, t, h, status(t, st))
            except Exception as e:
                # センサーの読み取りなどに失敗しても止まらず、次の測定で続ける
                print(f"{now:%H:%M:%S}  読み取り失敗: {e}", flush=True)
            # 次の区切り（5秒ごと）まで待つ。測定にかかった時間でずれていかないようにする
            time.sleep(NOW_INTERVAL - time.time() % NOW_INTERVAL)


# ============================================================
# テストメール（python3 sht31_logger.py --test-mail）
# ============================================================

def test_mail():
    """設定ファイルの内容でテストメールを1通送る: python3 sht31_logger.py --test-mail"""
    _, _, user, password = alert_mail.sender()
    to = alert_mail.get_recipients()
    # 設定が足りないとき・日本語や全角が混ざっているときは、分かりやすいメッセージで止める
    if not (user and password and to):
        sys.exit(f"メール設定が足りません。{alert_mail.MAIL_CONFIG} に MAIL_USER・MAIL_PASSWORD・MAIL_TO を書いてください")
    for name, value in [("MAIL_USER", user), ("MAIL_PASSWORD", password), ("宛先", ",".join(to))]:
        if not value.isascii():
            sys.exit(f"{name} に日本語や全角文字が入っています（見本の文字が残っていないか、半角で入力したか確認してください）\n"
                     f"  ファイル: {alert_mail.MAIL_CONFIG}")
    print(f"送信元: {user}\n宛先: {', '.join(to)}\n送信中...")
    try:
        send_mail("【テスト】温湿度モニター", f"警報メールのテストです。\n日時: {datetime.now():%Y-%m-%d %H:%M}\n\n"
                  f"設定: {TEMP_LOW:g}℃以下で低温、{TEMP_HIGH:g}℃以上で高温")
    except smtplib.SMTPAuthenticationError:
        sys.exit("送信に失敗しました: ログインできません。MAIL_USER とアプリパスワードを確認してください")
    except Exception as e:
        sys.exit(f"送信に失敗しました: {e}")
    print("送信しました。受信箱（届かなければ迷惑メールフォルダ）を確認してください")


# このファイルを直接実行したときの入口
# 「--test-mail」が付いていればテストメール、なければ測定を開始する
if __name__ == "__main__":
    test_mail() if "--test-mail" in sys.argv else main()
