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
#   5. センサーで測れない状態が続いたら【センサー異常】のメールを送る
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

import alarm_settings           # 警報の設定（2段階の温度の基準など）と状態の判定（alarm_settings.py）
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

# 警報の設定（高温警報・高温注意・低温注意・低温警報の温度、回復の幅、再通知の間隔など）は
# alarm_settings.py が /home/iot-user/.config/temp-monitor/settings.json から読む。
# サイトの「警報の設定」画面で変えられ、1分ごとに読み直すので再起動は不要

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
# 状態の判定
#   温度: 正常 / 高温注意 / 高温警報 / 低温注意 / 低温警報
#   湿度: 正常 / 高湿注意 / 高湿警報（alarm_settings.hum_status()）
# ============================================================

def status(t, prev="正常", s=None):
    # t: 今の温度、prev: 前回の状態。判定のしかたは alarm_settings.py の status() を参照
    return alarm_settings.status(t, prev, s)


def settings_text(s):
    # メールの本文に入れる、今の設定の説明
    text = (f"設定: 高温警報 {s['high_alarm']:g}℃以上 / 高温注意 {s['high_warn']:g}℃以上 / "
            f"低温注意 {s['low_warn']:g}℃以下 / 低温警報 {s['low_alarm']:g}℃以下")
    if s["hum_enabled"]:
        text += f"\n      高湿警報 {s['hum_alarm']:g}%以上 / 高湿注意 {s['hum_warn']:g}%以上"
    return text


# ============================================================
# 警報メール
# ============================================================

def send_mail(subject, body):
    """警報メールの宛先全員に送る。送信したらTrue、メール設定がなく送らなかったらFalseを返す。"""
    return alert_mail.send(subject, body)


class Alarm:
    """状態が変わったとき（と異常が続くとき）にメールを送る。送信に失敗したら次の測定で再送する。
    温度用（kind="temp"）と湿度用（kind="hum"）を別々に作り、それぞれ別に知らせる"""

    def __init__(self, kind="temp"):
        self.kind = kind
        self.notified = "正常"   # 最後にメールで知らせた状態
        self.last_sent = 0.0    # 最後にメールを送った時刻（再通知の判断に使う）

    def check(self, now, t, h, st, s=None):
        # 1分ごとに呼ばれる。送る必要があるときだけメールを送る
        s = s or alarm_settings.load()
        # 「注意でもメールを送る」がオフのときは、注意の段階はメールの上では「正常」と同じに扱う
        level = st if (s["mail_on_warn"] or "注意" not in st) else "正常"
        # 警報・注意が続いていて、前回の送信から remind_min 分たったら再通知する
        remind = (level != "正常" and s["remind_min"] > 0
                  and time.time() - self.last_sent >= s["remind_min"] * 60)
        # 状態が前回知らせたときと同じで、再通知の時期でもなければ何もしない
        if level == self.notified and not remind:
            return
        # 状態に合わせて件名を作る
        if self.kind == "hum":
            subject = self.hum_subject(level, st, h, s)
        elif level == "高温警報":
            subject = f"【高温警報】温度 {t:.1f}℃（{s['high_alarm']:g}℃以上）"
        elif level == "高温注意":
            subject = f"【高温注意】温度 {t:.1f}℃（{s['high_warn']:g}℃以上）"
        elif level == "低温注意":
            subject = f"【低温注意】温度 {t:.1f}℃（{s['low_warn']:g}℃以下）"
        elif level == "低温警報":
            subject = f"【低温警報】温度 {t:.1f}℃（{s['low_alarm']:g}℃以下）"
        elif st == "正常":
            subject = f"【回復】温度 {t:.1f}℃ 正常に戻りました"
        else:
            # 注意のメールを送らない設定で、警報から注意に下がったとき
            subject = f"【回復】温度 {t:.1f}℃ 警報が解除されました（現在: {st}）"
        if level == self.notified:
            subject = "【継続】" + subject      # 再通知のときは【継続】を付ける
        if self.kind == "hum":
            st = f"湿度 {st}"                  # 本文の「状態」に、湿度の状態だと分かるように書く
        body = (f"日時: {now:%Y-%m-%d %H:%M}\n温度: {t:.1f}℃\n湿度: {h:.1f}%\n"
                f"状態: {st}\n\n{settings_text(s)}")
        try:
            if send_mail(subject, body):
                print(f"メール送信: {subject}", flush=True)
            else:
                print(f"メール設定がないため送信しません: {subject}", flush=True)
            # 送れたら「知らせた状態」を更新する
            self.notified = level
            self.last_sent = time.time()
        except Exception as e:
            # 送れなかったら notified を更新しない → 次の測定（1分後）でもう一度送る
            # エラー文に宛先が含まれることがあるので、アドレスは伏せ字にしてログに残す
            print(f"メール送信失敗（次の測定で再送）: {alert_mail.mask(e)}", flush=True)

    @staticmethod
    def hum_subject(level, st, h, s):
        # 湿度のメールの件名
        if level == "高湿警報":
            return f"【高湿警報】湿度 {h:.1f}%（{s['hum_alarm']:g}%以上）"
        if level == "高湿注意":
            return f"【高湿注意】湿度 {h:.1f}%（{s['hum_warn']:g}%以上）"
        if st == "正常":
            return f"【回復】湿度 {h:.1f}% 正常に戻りました"
        return f"【回復】湿度 {h:.1f}% 警報が解除されました（現在: {st}）"


class SensorWatch:
    """センサーで測れない状態が sensor_fail_min 分続いたら【センサー異常】、
    また測れるようになったら【回復】のメールを送る"""

    def __init__(self):
        self.last_ok = time.time()  # 最後に測れた時刻（起動したときから数える）
        self.last_ok_dt = None      # 最後に測れた日時（メールの本文用）
        self.notified = False       # 【センサー異常】を知らせたか
        self.last_sent = 0.0        # 最後にメールを送った時刻（再通知の判断に使う）
        self.last_try = 0.0         # 最後にメールの送信を試した時刻

    def ok(self, now):
        # 測れたとき（5秒ごと）に呼ばれる
        self.last_ok = time.time()
        self.last_ok_dt = now
        if self.notified and self._send("【回復】センサーの読み取りが戻りました",
                                        f"日時: {now:%Y-%m-%d %H:%M}\n温度・湿度の測定が再開しました。"):
            self.notified = False

    def failed(self, now, err, s):
        # 測れなかったとき（5秒ごと）に呼ばれる
        down_min = int((time.time() - self.last_ok) // 60)     # 測れていない時間（分）
        if down_min < s["sensor_fail_min"]:
            return
        # すでに知らせていたら、再通知の間隔がたつまでは送らない
        if self.notified and not (s["remind_min"] > 0
                                  and time.time() - self.last_sent >= s["remind_min"] * 60):
            return
        last = f"{self.last_ok_dt:%Y-%m-%d %H:%M}" if self.last_ok_dt else "（起動してから一度も測れていません）"
        subject = ("【継続】" if self.notified else "") + f"【センサー異常】温度・湿度が{down_min}分間測れていません"
        body = (f"日時: {now:%Y-%m-%d %H:%M}\n最後に測れた日時: {last}\nエラーの内容: {alert_mail.mask(err)}\n\n"
                "センサーの配線が外れていないか、ラズパイの状態を確認してください。")
        if self._send(subject, body):
            self.notified = True

    def _send(self, subject, body):
        # メールを送る。送れたら（またはメールの設定がなければ）True。失敗したら1分後に送り直す
        if time.time() - self.last_try < 60:
            return False
        self.last_try = time.time()
        try:
            if send_mail(subject, body):
                print(f"メール送信: {subject}", flush=True)
            else:
                print(f"メール設定がないため送信しません: {subject}", flush=True)
            self.last_sent = time.time()
            return True
        except Exception as e:
            print(f"メール送信失敗（1分後に再送）: {alert_mail.mask(e)}", flush=True)
            return False


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


def write_now(now, t, h, st, hst="正常"):
    """現在値を now.json に書く。読み取り中に中途半端なファイルが見えないよう、置き換えで更新する。
    st は温度の状態、hst は湿度の状態"""
    path = NOW_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # いったん .tmp に書いてから、本物と入れ替える
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"iso": now.astimezone().isoformat(timespec="seconds"),
                   "temp": round(t, 1), "hum": round(h, 1), "status": st, "hum_status": hst},
                  f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# ============================================================
# メインの処理（ずっと繰り返す）
# ============================================================

def main():
    alarm = Alarm("temp")       # 温度の警報メール
    hum_alarm = Alarm("hum")    # 湿度の警報メール
    st = "正常"             # 今の温度の状態（1分ごとに更新）
    hst = "正常"            # 今の湿度の状態（1分ごとに更新）
    s = alarm_settings.load()   # 警報の設定（1分ごとに読み直す）
    settings_slot = None    # 最後に設定を読み直した区切りの時刻
    last_logged = None      # 最後にCSVへ記録した区切りの時刻
    sensor = SensorWatch()  # センサーで測れない状態が続いたら知らせる
    last_fail_log = 0.0     # 最後に「読み取り失敗」をログに書いた時刻（ログは1分に1回まで）
    bus = None              # I2Cの接続（開けなかったときは次の測定でもう一度開く）
    while True:
        now = datetime.now()
        # 今が何番目の「1分の区切り」か。この番号が変わったら新しい1分に入ったということ
        slot = int(time.time() // INTERVAL)
        if slot != settings_slot:
            s = alarm_settings.load()   # 設定を読み直す（画面で変えた設定がここで反映される）
            settings_slot = slot
        try:
            # I2Cのバス1番（ラズパイのGPIO2=SDA、GPIO3=SCL）を開いて、センサーを読む
            if bus is None:
                bus = SMBus(1)
            t, h = read_sht31(bus)
        except Exception as e:
            # 読めなくても止まらず、次の測定（5秒後）で続ける。ログは1分に1回だけ書く
            if time.time() - last_fail_log >= 60:
                print(f"{now:%H:%M:%S}  読み取り失敗: {e}", flush=True)
                last_fail_log = time.time()
            sensor.failed(now, e, s)
        else:
            last_fail_log = 0.0
            sensor.ok(now)
            try:
                if slot != last_logged:         # 毎分1回：記録と警報判定
                    st = status(t, st, s)
                    hst = alarm_settings.hum_status(h, hst, s)
                    append_csv(now, t, h, st)
                    last_logged = slot
                    # print の内容は動作ログに残る（journalctl -u sht31-logger で見られる）
                    hum_note = "" if hst == "正常" else f"  {hst}"
                    print(f"{now:%H:%M:%S}  温度 {t:.1f}℃  湿度 {h:.1f}%  {st}{hum_note}", flush=True)
                    alarm.check(now, t, h, st, s)
                    hum_alarm.check(now, t, h, hst, s)
                # 現在値は毎回（5秒ごと）書く
                write_now(now, t, h, status(t, st, s), alarm_settings.hum_status(h, hst, s))
            except Exception as e:
                # 記録や判定で問題が起きても止まらず、次の測定で続ける
                print(f"{now:%H:%M:%S}  記録・判定でエラー: {e}", flush=True)
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
                  f"{settings_text(alarm_settings.load())}")
    except smtplib.SMTPAuthenticationError:
        sys.exit("送信に失敗しました: ログインできません。MAIL_USER とアプリパスワードを確認してください")
    except Exception as e:
        sys.exit(f"送信に失敗しました: {e}")
    print("送信しました。受信箱（届かなければ迷惑メールフォルダ）を確認してください")


# このファイルを直接実行したときの入口
# 「--test-mail」が付いていればテストメール、なければ測定を開始する
if __name__ == "__main__":
    test_mail() if "--test-mail" in sys.argv else main()
