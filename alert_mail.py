"""警報メールの設定・宛先・送信をまとめた部品。sht31_logger.py と web/app.py の両方から使う。"""
# ============================================================
# このファイルの役割
#   ・メール設定ファイル（mail.env）を読む
#   ・警報メールの宛先リスト（recipients.json）を読み書きする
#       サイトの「警報メールの宛先」画面から追加・削除すると、このファイルが書き換わる
#   ・メールを送る
#
# 設定・宛先は送るたびに読み直すので、変更はすぐ反映される（再起動は不要）
# ============================================================
import json
import os
import re
import smtplib
import threading
from email.message import EmailMessage

# 設定ファイルを置くフォルダ（本人だけが読めるようにしておく）。環境変数 CONFIG_DIR で変えられる
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/home/iot-user/.config/temp-monitor")
# メール設定ファイル（mail.env.example を参考に作る）
MAIL_CONFIG = os.path.join(CONFIG_DIR, "mail.env")
# 警報メールの宛先リスト。なければ mail.env の MAIL_TO を使う
RECIPIENTS_PATH = os.path.join(CONFIG_DIR, "recipients.json")

# 宛先リストを同時に書き換えて壊さないための鍵
_lock = threading.Lock()


# 文の中のメールアドレスを見つける形（伏せ字にするため）
_EMAIL_IN_TEXT = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask(text):
    """文の中のメールアドレスを伏せ字にする（例: maruyama@example.com → ma***@example.com）。
    動作ログにアドレスをそのまま残さないために使う"""
    def hide(m):
        name = m.group(1)
        return name[:2 if len(name) > 2 else 1] + "***@" + m.group(2)
    return _EMAIL_IN_TEXT.sub(hide, str(text))


def load_config(path=None):
    """KEY=VALUE 形式の設定ファイルを読む。環境変数があればそちらを優先する。"""
    conf = {}
    try:
        with open(path or MAIL_CONFIG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                # 空行と「#」で始まる説明の行は飛ばし、「名前=値」の行だけ読む
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    conf[k.strip()] = v.strip().strip('"')
    except FileNotFoundError:
        pass    # 設定ファイルがなければメールは送らない（記録などは普通に動く）
    # 環境変数（MAIL_〜 / SMTP_〜 / ADMIN_〜）が設定されていれば、ファイルの値より優先する
    conf.update({k: v for k, v in os.environ.items() if k.startswith(("MAIL_", "SMTP_", "ADMIN_"))})
    return conf


def sender():
    """送信元の設定を返す: (サーバー, ポート, 送信元アドレス, パスワード)"""
    c = load_config()
    return (c.get("SMTP_HOST", "smtp.gmail.com"), int(c.get("SMTP_PORT", 587)),
            c.get("MAIL_USER", ""), c.get("MAIL_PASSWORD", "").replace(" ", ""))


def admin_password():
    """サイトから宛先を変更するときのパスワード（mail.env の ADMIN_PASSWORD）。未設定なら空"""
    return load_config().get("ADMIN_PASSWORD", "")


def get_recipients():
    """警報メールの宛先リスト。recipients.json がなければ mail.env の MAIL_TO を使う"""
    try:
        with open(RECIPIENTS_PATH, encoding="utf-8") as f:
            return [a for a in json.load(f) if isinstance(a, str) and a]
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass    # 壊れていたら mail.env の値を使う
    return [a.strip() for a in load_config().get("MAIL_TO", "").split(",") if a.strip()]


def update_recipients(change):
    """宛先リストを読み、change(リスト) で書き換えて保存する。保存後のリストを返す。
    change が例外を出したら保存しない（同時に2人が変更しても壊れないよう、鍵をかけて行う）"""
    with _lock:
        lst = get_recipients()
        change(lst)
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = RECIPIENTS_PATH + ".tmp"
        # 本人だけが読み書きできる権限（600）で作ってから、本物と入れ替える
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(lst, f, ensure_ascii=False, indent=1)
        os.replace(tmp, RECIPIENTS_PATH)
        return lst


def send(subject, body, to=None):
    """メールを送る。送信したらTrue、設定がなく送らなかったらFalseを返す。
    to を省略すると、警報メールの宛先リスト全員に送る"""
    host, port, user, password = sender()
    to = to if to is not None else get_recipients()
    if not (user and password and to):
        return False
    # メールを組み立てる（件名・送信元・宛先・本文）
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to)
    msg.set_content(body)
    # メールサーバーにつないで、暗号化（STARTTLS）→ ログイン → 送信
    # 20秒で応答がなければあきらめる（ネットが切れていても止まり続けないように）
    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)
    return True
