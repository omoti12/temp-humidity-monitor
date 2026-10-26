"""警報の設定（2段階の温度・湿度の基準など）と、状態の判定をまとめた部品。"""
# ============================================================
# このファイルの役割
#   ・警報の設定を読み書きする（~/.config/temp-monitor/settings.json）
#       サイトの「警報の設定」画面から変更すると、このファイルが書き換わる
#       ファイルがなければ DEFAULTS（初期値）を使う
#   ・温度から状態（正常 / 高温注意 / 高温警報 / 低温注意 / 低温警報）を判定する
#   ・湿度から状態（正常 / 高湿注意 / 高湿警報）を判定する（湿度は上限だけ）
#
# 使っているところ
#   sht31_logger.py（警報の判定とメール）、sht31_graph.py・web/app.py・pages/publish.py（グラフの線）
# ============================================================
import json
import os
import threading
import time

# 設定ファイルの場所（alert_mail.py と同じフォルダ）。環境変数 CONFIG_DIR で変えられる
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/home/iot-user/.config/temp-monitor")
SETTINGS_PATH = os.path.join(CONFIG_DIR, "settings.json")

# 初期値（設定ファイルがないとき、または一部の項目が書かれていないときに使う）
DEFAULTS = {
    "high_alarm": 30.0,     # この温度以上で「高温警報」
    "high_warn": 28.0,      # この温度以上で「高温注意」
    "low_warn": 22.0,       # この温度以下で「低温注意」
    "low_alarm": 21.0,      # この温度以下で「低温警報」
    "hysteresis": 0.5,      # 回復と判断する幅（30℃の警報なら29.5℃以下に下がったら解除）
    "remind_min": 60,       # 警報・注意が続くとき、この間隔（分）で再通知する。0なら再通知しない
    "mail_on_warn": True,   # 「注意」でもメールを送るか（False なら「警報」だけ送る。温度・湿度共通）
    "hum_enabled": True,    # 湿度の警報を使うか
    "hum_alarm": 70.0,      # この湿度以上で「高湿警報」
    "hum_warn": 65.0,       # この湿度以上で「高湿注意」
    "hum_hysteresis": 2.0,  # 湿度の回復と判断する幅（70%の警報なら68%以下に下がったら解除）
    "sensor_fail_min": 5,   # センサーで測れない状態がこの分数続いたら【センサー異常】のメールを送る
    # 警報・注意・センサー異常のメールを止めている期限（1970年からの秒数）。0 = 止めていない、-1 = 再開するまで止める
    "mail_pause_until": 0,
}

# 湿度の状態の名前（数字が大きいほど高い）
HUM_LEVELS = {0: "正常", 1: "高湿注意", 2: "高湿警報"}
HUM_LEVEL_OF = {name: k for k, name in HUM_LEVELS.items()}

# 状態の名前。数字は「どれくらい外れているか」（マイナスが低温、プラスが高温）
LEVELS = {-2: "低温警報", -1: "低温注意", 0: "正常", 1: "高温注意", 2: "高温警報"}
# 前の版で記録した状態の名前も読めるようにする
LEVEL_OF = {name: k for k, name in LEVELS.items()} | {"高温": 2, "低温": -2}

_lock = threading.Lock()


def load():
    """設定を読む。ファイルにない項目は初期値で埋める"""
    s = dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            s.update({k: saved[k] for k in DEFAULTS if k in saved})
    except (OSError, ValueError):
        pass    # ファイルがない・壊れているときは初期値
    return s


def validate(new):
    """画面から送られた設定を確かめて、正しい形に直して返す。おかしければ ValueError を出す"""
    s = load()
    try:
        for k in ("high_alarm", "high_warn", "low_warn", "low_alarm", "hysteresis",
                  "hum_alarm", "hum_warn", "hum_hysteresis"):
            if k in new:
                s[k] = round(float(new[k]), 1)
        for k in ("remind_min", "sensor_fail_min"):
            if k in new:
                s[k] = int(new[k])
        for k in ("mail_on_warn", "hum_enabled"):
            if k in new:
                s[k] = bool(new[k])
        hours = float(new["pause_hours"]) if "pause_hours" in new else None
    except (TypeError, ValueError):
        raise ValueError("数字で入力してください")
    # メールの一時停止: pause_hours = 何時間止めるか（0 = 再開する、-1 = 再開するまで止める）
    if hours is not None:
        if hours < 0:
            s["mail_pause_until"] = -1
        elif hours == 0:
            s["mail_pause_until"] = 0
        elif hours <= 24 * 7:
            s["mail_pause_until"] = time.time() + hours * 3600
        else:
            raise ValueError("メールを止める時間は1週間までにしてください")
    # センサー（SHT31）が測れる範囲は -40〜125℃
    for k in ("high_alarm", "high_warn", "low_warn", "low_alarm"):
        if not -40 <= s[k] <= 125:
            raise ValueError("温度は -40〜125℃ の範囲で入力してください")
    # 注意と警報を同じ温度にすると「注意の段階なし」（その温度でいきなり警報）として扱う
    if s["high_warn"] > s["high_alarm"]:
        raise ValueError("高温注意は、高温警報と同じか低い温度にしてください（同じにすると高温注意なし）")
    if s["low_warn"] < s["low_alarm"]:
        raise ValueError("低温注意は、低温警報と同じか高い温度にしてください（同じにすると低温注意なし）")
    if not s["low_warn"] < s["high_warn"]:
        raise ValueError("低温注意（低温警報）は、高温注意（高温警報）より低い温度にしてください")
    if not 0 <= s["hysteresis"] <= 5:
        raise ValueError("回復の幅は 0〜5℃ で入力してください")
    if not 0 <= s["remind_min"] <= 1440:
        raise ValueError("再通知の間隔は 0〜1440分 で入力してください")
    # 湿度（0〜100%）。注意と警報を同じにすると「注意の段階なし」
    for k in ("hum_alarm", "hum_warn"):
        if not 0 <= s[k] <= 100:
            raise ValueError("湿度は 0〜100% の範囲で入力してください")
    if s["hum_warn"] > s["hum_alarm"]:
        raise ValueError("湿度注意は、湿度警報と同じか低い値にしてください（同じにすると湿度注意なし）")
    if not 0 <= s["hum_hysteresis"] <= 10:
        raise ValueError("湿度の回復の幅は 0〜10% で入力してください")
    if not 1 <= s["sensor_fail_min"] <= 1440:
        raise ValueError("センサー異常を知らせるまでの時間は 1〜1440分 で入力してください")
    return s


def save(new):
    """設定を確かめてから保存する。保存した設定を返す"""
    with _lock:
        s = validate(new)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, SETTINGS_PATH)
        return s


def status(t, prev="正常", s=None):
    """温度 t と前回の状態 prev から、今の状態を判定する。
    境目でメールが連発しないよう、回復の幅（hysteresis）だけ戻るまでは前の状態のままにする"""
    s = s or load()
    t = round(t, 1)     # 表示・記録と同じ小数第1位の値で判定する
    hyst = s["hysteresis"]
    # 基準だけで決めた状態
    if t >= s["high_alarm"]:
        raw = 2
    elif t >= s["high_warn"]:
        raw = 1
    elif t <= s["low_alarm"]:
        raw = -2
    elif t <= s["low_warn"]:
        raw = -1
    else:
        raw = 0
    p = LEVEL_OF.get(prev, 0)
    # 注意と警報が同じ温度なら「注意の段階なし」。回復するときも注意を通らない
    no_high_warn = s["high_warn"] == s["high_alarm"]
    no_low_warn = s["low_warn"] == s["low_alarm"]
    # 高温側から下がってきたとき: 基準より hysteresis 以上下がるまでは前の段階を保つ
    if p > 0 and raw < p:
        for k in range(p, max(raw, 0), -1):
            if k == 1 and no_high_warn:
                continue
            if t > s["high_alarm" if k == 2 else "high_warn"] - hyst:
                return LEVELS[k]
    # 低温側から上がってきたとき: 基準より hysteresis 以上上がるまでは前の段階を保つ
    if p < 0 and raw > p:
        for k in range(p, min(raw, 0)):
            if k == -1 and no_low_warn:
                continue
            if t < s["low_alarm" if k == -2 else "low_warn"] + hyst:
                return LEVELS[k]
    return LEVELS[raw]


def mail_paused(s=None):
    """警報・注意・センサー異常のメールを、今止めているか"""
    s = s or load()
    until = s.get("mail_pause_until", 0)
    return until == -1 or (until > 0 and time.time() < until)


def hum_status(h, prev="正常", s=None):
    """湿度 h と前回の状態 prev から、湿度の状態（正常 / 高湿注意 / 高湿警報）を判定する。
    湿度の警報を使わない設定なら、いつも「正常」"""
    s = s or load()
    if not s["hum_enabled"]:
        return "正常"
    h = round(h, 1)     # 表示・記録と同じ小数第1位の値で判定する
    raw = 2 if h >= s["hum_alarm"] else 1 if h >= s["hum_warn"] else 0
    p = HUM_LEVEL_OF.get(prev, 0)
    # 下がってきたとき: 基準より hum_hysteresis 以上下がるまでは前の段階を保つ
    if raw < p:
        for k in range(p, raw, -1):
            if k == 1 and s["hum_warn"] == s["hum_alarm"]:
                continue    # 注意と警報が同じ値なら「注意の段階なし」
            if h > s["hum_alarm" if k == 2 else "hum_warn"] - s["hum_hysteresis"]:
                return HUM_LEVELS[k]
    return HUM_LEVELS[raw]


def thresholds(s=None):
    """グラフに線を引くための基準（温度4つ、湿度は使う設定のときだけ2つ）"""
    s = s or load()
    th = {k: s[k] for k in ("high_alarm", "high_warn", "low_warn", "low_alarm")}
    if s["hum_enabled"]:
        th.update(hum_alarm=s["hum_alarm"], hum_warn=s["hum_warn"])
    return th
