"""警報の設定（2段階の温度の基準など）と、状態の判定をまとめた部品。"""
# ============================================================
# このファイルの役割
#   ・警報の設定を読み書きする（~/.config/temp-monitor/settings.json）
#       サイトの「警報の設定」画面から変更すると、このファイルが書き換わる
#       ファイルがなければ DEFAULTS（初期値）を使う
#   ・温度から状態（正常 / 高温注意 / 高温警報 / 低温注意 / 低温警報）を判定する
#
# 使っているところ
#   sht31_logger.py（警報の判定とメール）、sht31_graph.py・web/app.py・pages/publish.py（グラフの線）
# ============================================================
import json
import os
import threading

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
    "mail_on_warn": True,   # 「注意」でもメールを送るか（False なら「警報」だけ送る）
}

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
        for k in ("high_alarm", "high_warn", "low_warn", "low_alarm", "hysteresis"):
            if k in new:
                s[k] = round(float(new[k]), 1)
        if "remind_min" in new:
            s["remind_min"] = int(new["remind_min"])
        if "mail_on_warn" in new:
            s["mail_on_warn"] = bool(new["mail_on_warn"])
    except (TypeError, ValueError):
        raise ValueError("数字で入力してください")
    # センサー（SHT31）が測れる範囲は -40〜125℃
    for k in ("high_alarm", "high_warn", "low_warn", "low_alarm"):
        if not -40 <= s[k] <= 125:
            raise ValueError("温度は -40〜125℃ の範囲で入力してください")
    if not s["low_alarm"] < s["low_warn"] < s["high_warn"] < s["high_alarm"]:
        raise ValueError("「低温警報 ＜ 低温注意 ＜ 高温注意 ＜ 高温警報」の順になるように入力してください")
    if not 0 <= s["hysteresis"] <= 5:
        raise ValueError("回復の幅は 0〜5℃ で入力してください")
    if not 0 <= s["remind_min"] <= 1440:
        raise ValueError("再通知の間隔は 0〜1440分 で入力してください")
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
    # 高温側から下がってきたとき: 基準より hysteresis 以上下がるまでは前の段階を保つ
    if p > 0 and raw < p:
        for k in range(p, max(raw, 0), -1):
            if t > s["high_alarm" if k == 2 else "high_warn"] - hyst:
                return LEVELS[k]
    # 低温側から上がってきたとき: 基準より hysteresis 以上上がるまでは前の段階を保つ
    if p < 0 and raw > p:
        for k in range(p, min(raw, 0)):
            if t < s["low_alarm" if k == -2 else "low_warn"] + hyst:
                return LEVELS[k]
    return LEVELS[raw]


def thresholds(s=None):
    """グラフに線を引くための4つの基準"""
    s = s or load()
    return {k: s[k] for k in ("high_alarm", "high_warn", "low_warn", "low_alarm")}
