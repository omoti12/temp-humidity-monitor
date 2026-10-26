#!/usr/bin/env python3
"""温湿度モニター Webサイト: sht31_logger.py が書いたCSVを読んで表示する。"""
# ============================================================
# このプログラムの役割
#   ラズパイの中で動く小さなWebサーバー（Flask）。サイトはすべてラズパイの中にあり、
#   インターネットには公開しない。見られるのは次の端末だけ:
#     ・ラズパイと同じネットワーク（社内のWi-Fiなど）の端末 … http://raspberrypi.local:8000
#     ・Tailscale にログインした端末（外からでも）      … https://raspberrypi.tailfa1dcc.ts.net
#   画面は index.html（1つだけ）。グラフの部品などは static/ に置いてある
#
# URLの一覧
#   /                    … サイトの画面（index.html）
#   /data/latest.json    … 最新の記録・記録のある日の一覧・警報の基準・設置場所の名前
#   /data/日付.json       … その日の10分ごとのデータ（グラフ用）
#   /data/日付.csv        … その日のCSVをダウンロード
#   /api/now             … 現在値（5秒ごとに更新）
#   /api/recipients      … 警報メールの宛先の表示・追加・削除（管理用パスワードが必要）
#   /api/settings        … 警報の設定の表示・変更（管理用パスワードが必要）
#
# 動かし方
#   systemd（temp-web.service）が自動で起動する
#   手動で動かすとき : python3 web/app.py
# ============================================================
import csv
import hmac
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

# 1つ上のフォルダにある alert_mail.py（警報メールの設定・宛先・送信）を使えるようにする
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import alarm_settings  # noqa: E402
import alert_mail  # noqa: E402

# ---- 設定値（環境変数で上書きできる。temp-web.service の Environment= で設定）----
LOG_DIR = os.environ.get("LOG_DIR", "/home/iot-user/templog")
NOW_PATH = os.environ.get("NOW_PATH", "/dev/shm/temp-monitor/now.json")   # sht31_logger.py と同じ場所
STEP_MIN = 10          # グラフの間隔（分）

# このファイルがあるフォルダ（画面 index.html と static/ の場所）
WEB_DIR = os.path.dirname(os.path.abspath(__file__))
# 画面から読む部品（static/ にあるもの）。この一覧にないファイルは渡さない
STATIC_FILES = {"chart.umd.js", "chartjs-plugin-zoom.min.js", "hammer.min.js",
                "manifest.webmanifest", "icon-192.png", "icon-512.png", "sw.js",
                "chart.js-LICENSE.md", "chartjs-plugin-zoom-LICENSE.md", "hammerjs-LICENSE.md"}

# ログイン用。環境変数 WEB_USER / WEB_PASSWORD で設定する（未設定ならログインなし）
WEB_USER = os.environ.get("WEB_USER", "")
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

# Webサーバー本体
app = Flask(__name__)


@app.before_request
def require_login():
    # どのページを開くときも、最初にここを通る（サイト全体にパスワードをかけるとき用。ふだんは未設定）
    # 宛先と設定の管理（/api/recipients・/api/settings）は、下の check_admin() で管理用パスワードを確認する
    if not WEB_PASSWORD or request.path in ("/api/recipients", "/api/settings"):
        return None
    # ブラウザが送ってきたユーザー名・パスワードを確認する
    # （hmac.compare_digest は、比べる時間から中身を推測されにくい比べ方）
    auth = request.authorization
    if (auth and hmac.compare_digest(auth.username or "", WEB_USER)
            and hmac.compare_digest(auth.password or "", WEB_PASSWORD)):
        return None
    # 合っていなければ、ブラウザにログイン画面を出させる
    return Response("ログインが必要です", 401,
                    {"WWW-Authenticate": 'Basic realm="temp-monitor", charset="UTF-8"'})


@app.after_request
def security_headers(resp):
    # どの応答にも付ける安全のための指示
    resp.headers["X-Content-Type-Options"] = "nosniff"     # ファイルの種類をブラウザに推測させない
    resp.headers["X-Frame-Options"] = "DENY"               # ほかのサイトの中に埋め込ませない
    resp.headers["Referrer-Policy"] = "no-referrer"        # ほかのサイトへ移るときにURLを伝えない
    if request.path.startswith(("/api/", "/data/")) or request.path == "/":
        resp.headers["Cache-Control"] = "no-store"         # 古い値や古い画面を使い回させない
    return resp


# ============================================================
# 記録（CSV）の読み込みと集計
# ============================================================

def csv_path(day):
    # その日のCSVファイルの場所（例: /home/iot-user/templog/2026-10-19.csv）
    return os.path.join(LOG_DIR, day.strftime("%Y-%m-%d") + ".csv")


def load(day):
    # その日のCSVを読んで [(日時, 温度, 湿度, 状態), ...] のリストにする
    path = csv_path(day)
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((datetime.strptime(r["日時"], "%Y-%m-%d %H:%M:%S"),
                             float(r["温度(℃)"]), float(r["湿度(%)"]), r.get("状態") or ""))
            except (KeyError, ValueError):
                continue    # 書き込み途中の行などは飛ばす
    return rows


def every_10min(rows):
    """1分ごとの記録を10分単位にまとめ、平均・最高・最低を出す。"""
    buckets = {}
    for ts, t, h, _ in rows:
        # 時刻を10分単位に切り捨てる（例: 10:37 → 10:30）。同じ箱に入れる
        key = ts.replace(minute=ts.minute - ts.minute % STEP_MIN, second=0)
        buckets.setdefault(key, []).append((t, h))
    out = []
    for k in sorted(buckets):
        ts = [v[0] for v in buckets[k]]
        hs = [v[1] for v in buckets[k]]
        # グラフ1点分のデータ（温度は平均・最高・最低、湿度は平均）
        out.append({
            "time": k.strftime("%H:%M"),
            "temp": round(sum(ts) / len(ts), 1),
            "temp_max": max(ts),
            "temp_min": min(ts),
            "hum": round(sum(hs) / len(hs), 1),
        })
    return out


def status(t):
    # 状態の判定（CSVに状態が書かれていない古い記録のときだけ使う）
    return alarm_settings.status(t)


def parse_day(s):
    # "2026-10-19" の形の日付を読む。形がおかしければ「見つかりません」（404）
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except (TypeError, ValueError):
        abort(404)


def record_days():
    """記録のある日の一覧（"2026-10-19" のような名前のCSVだけを対象にする）"""
    try:
        names = os.listdir(LOG_DIR)
    except OSError:
        return []
    return sorted(f[:-4] for f in names if f.endswith(".csv") and len(f) == len("2026-01-01.csv"))


# ============================================================
# 画面と、画面が読むデータ
# ============================================================

@app.route("/")
def index():
    # サイトの画面（index.html）を返す
    return send_file(os.path.join(WEB_DIR, "index.html"))


@app.route("/<name>")
def static_file(name):
    # グラフの部品・アイコンなど（STATIC_FILES の一覧にあるものだけ）
    if name not in STATIC_FILES:
        abort(404)
    resp = send_from_directory(os.path.join(WEB_DIR, "static"), name)
    # スマホにアプリとして認めてもらうため、設定ファイル（manifest）の種類を正しく伝える
    if name == "manifest.webmanifest":
        resp.headers["Content-Type"] = "application/manifest+json"
    # サービスワーカーは、直したときにすぐ入れ替わるよう、保存させない
    if name == "sw.js":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def latest_reading(rows, day):
    """一番新しい記録。今日の分がまだなければ前日の最後の値を使う。"""
    src = rows or load(day - timedelta(days=1))
    if not src:
        return None
    ts, t, h, st = src[-1]
    return {"time": ts.strftime("%Y-%m-%d %H:%M"), "iso": ts.astimezone().isoformat(),
            "temp": t, "hum": h,
            "status": st or status(t),   # ロガーが記録した状態を優先する
            "hum_status": alarm_settings.hum_status(h),   # 湿度の状態（記録には残していないので、ここで判定）
            # 5分以上新しい記録がなければ「止まっているかも」の印を付ける
            "stale": (datetime.now() - ts) > timedelta(minutes=5)}


@app.route("/data/latest.json")
def data_latest():
    # 画面が最初に（と1分ごとに）読むデータ
    day = datetime.now()
    latest = latest_reading(load(day), day)
    if latest:
        latest["time"] = latest["iso"]      # 画面では日付つきの形（iso）で使う
    return jsonify({
        "latest": latest,
        "days": record_days(),
        # グラフに引く警報・注意の線（高温警報・高温注意・低温注意・低温警報・高湿警報・高湿注意）
        "thresholds": alarm_settings.thresholds(),
        "location": alarm_settings.load()["location_name"],    # 設置場所の名前
    })


@app.route("/data/<name>.json")
def data_day(name):
    # その日の10分ごとのデータ（グラフ用）。記録がなければ「見つかりません」（404）
    day = parse_day(name)
    if not os.path.exists(csv_path(day)):
        abort(404)
    return jsonify({"day": name, "points": every_10min(load(day))})


@app.route("/data/<name>.csv")
def data_csv(name):
    # その日のCSVをファイルとしてダウンロードさせる。なければ「見つかりません」（404）
    path = csv_path(parse_day(name))
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


def now_reading():
    """ロガーが5秒ごとに書く now.json を読む。古い・無いときはCSVの最新値を使う。"""
    try:
        with open(NOW_PATH, encoding="utf-8") as f:
            n = json.load(f)
        ts = datetime.fromisoformat(n["iso"])
        age = datetime.now(ts.tzinfo) - ts
        # 2分以内に書かれたものなら、それを現在値として返す
        if age < timedelta(minutes=2):
            n["time"] = ts.strftime("%Y-%m-%d %H:%M:%S")
            n["stale"] = False
            return n
    except (OSError, ValueError, KeyError):
        pass    # ファイルがない・壊れているときは、下のCSVの値を使う
    day = datetime.now()
    return latest_reading(load(day), day)


@app.route("/api/now")
def api_now():
    """現在値だけを返す（画面が5秒ごとに読む）"""
    return jsonify({"latest": now_reading(), "location": alarm_settings.load()["location_name"]})


# ============================================================
# 警報メールの宛先・警報の設定の管理（画面の管理欄から使う）
# ============================================================

MAX_RECIPIENTS = 20         # 宛先の上限（いたずらで大量に登録されないように）
# メールアドレスの形（○○@○○.○○）になっているかの確認
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

# パスワードの総当たりを防ぐ
#   同じ相手（IPアドレス）が10分間に10回まちがえたら、その相手からはしばらく受け付けない
#   いろいろな相手から合わせて10分間に50回まちがえたら、全員しばらく受け付けない
_fail_lock = threading.Lock()
_fail_times = {}            # 相手 → まちがえた時刻のリスト
FAIL_LIMIT, FAIL_LIMIT_ALL, FAIL_WINDOW = 10, 50, 600


def client_id():
    """問い合わせてきた相手のIPアドレス。Tailscale 経由の問い合わせは tailscale serve が
    ラズパイの中（127.0.0.1）から中継してくるので、そのときは中継元が付ける X-Forwarded-For を使う。
    相手が自分で偽の X-Forwarded-For を付けてきても、中継元は本当の相手を最後に書き足すので、最後を使う"""
    addr = request.remote_addr or ""
    forwarded = request.headers.get("X-Forwarded-For", "")
    if addr in ("127.0.0.1", "::1") and forwarded:
        return forwarded.split(",")[-1].strip()
    return addr


def admin_error(message, code):
    return jsonify({"error": message}), code


def check_admin():
    """管理用パスワードを確認する。正しければ None、まちがっていればエラーの応答を返す"""
    password = alert_mail.admin_password()
    if not password:
        return admin_error("ラズパイに管理用パスワード（ADMIN_PASSWORD）が設定されていません", 503)
    now = time.time()
    who = client_id()
    with _fail_lock:
        # 10分より前のまちがいは忘れる
        for k in list(_fail_times):
            _fail_times[k] = [t for t in _fail_times[k] if now - t < FAIL_WINDOW]
            if not _fail_times[k]:
                del _fail_times[k]
        total = sum(len(v) for v in _fail_times.values())
        if len(_fail_times.get(who, [])) >= FAIL_LIMIT or total >= FAIL_LIMIT_ALL:
            return admin_error("パスワードを何度もまちがえたため、10分ほど受け付けません", 429)
    # 画面からは「Authorization: Bearer パスワード」の形で送られてくる
    given = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if hmac.compare_digest(given.encode(), password.encode()):
        return None
    with _fail_lock:
        _fail_times.setdefault(who, []).append(now)
    time.sleep(1)       # まちがえたときは少し待たせて、総当たりを遅くする
    return admin_error("パスワードが違います", 401)


@app.route("/api/recipients", methods=["GET", "POST", "DELETE"])
def api_recipients():
    err = check_admin()
    if err:
        return err

    # 表示: 今の宛先の一覧を返す
    if request.method == "GET":
        return jsonify({"recipients": alert_mail.get_recipients()})

    email = str((request.get_json(silent=True) or {}).get("email", "")).strip()

    # 追加
    if request.method == "POST":
        if not EMAIL_RE.match(email):
            return admin_error("メールアドレスの形が正しくありません（半角で入力してください）", 400)

        def add(lst):
            if email.lower() in (a.lower() for a in lst):
                raise ValueError("このアドレスはすでに登録されています")
            if len(lst) >= MAX_RECIPIENTS:
                raise ValueError(f"宛先は{MAX_RECIPIENTS}件までです")
            lst.append(email)
        try:
            lst = alert_mail.update_recipients(add)
        except ValueError as e:
            return admin_error(str(e), 400)
        # 追加したアドレスに「宛先に追加されました」と知らせる（知らないうちに登録されないように）
        try:
            notified = alert_mail.send(
                "【温湿度モニター】警報メールの宛先に追加されました",
                "このアドレスが、温湿度モニターの警報メールの宛先に追加されました。\n"
                "温度が基準を外れたときに、このアドレスへ警報メールが届きます。\n\n"
                "心当たりがない場合は、管理者に連絡してください。", to=[email])
        except Exception:
            notified = False
        # 動作ログにはアドレスを伏せ字で残す
        print(f"警報メールの宛先を追加: {alert_mail.mask(email)}", flush=True)
        return jsonify({"recipients": lst, "notified": notified})

    # 削除（最後の1件は消せない。消すと警報がどこにも届かなくなるため）
    def remove(lst):
        if email not in lst:
            raise ValueError("このアドレスは登録されていません")
        if len(lst) <= 1:
            raise ValueError("最後の1件は削除できません（警報がどこにも届かなくなるため）")
        lst.remove(email)
    try:
        lst = alert_mail.update_recipients(remove)
    except ValueError as e:
        return admin_error(str(e), 400)
    print(f"警報メールの宛先を削除: {alert_mail.mask(email)}", flush=True)
    return jsonify({"recipients": lst})


@app.route("/api/settings", methods=["GET", "PUT"])
def api_settings():
    # 警報の設定（2段階の温度・湿度の基準・回復の幅・再通知の間隔・メールの一時停止・設置場所の名前など）
    err = check_admin()
    if err:
        return err
    # 表示: 今の設定を返す
    if request.method == "GET":
        return jsonify({"settings": alarm_settings.load()})
    # 変更: 確かめてから保存する（おかしな値なら理由を返す）
    try:
        s = alarm_settings.save(request.get_json(silent=True) or {})
    except ValueError as e:
        return admin_error(str(e), 400)
    print(f"警報の設定を変更: {s}", flush=True)
    return jsonify({"settings": s})


# このファイルを直接実行したときの入口
# 0.0.0.0 = ラズパイ以外（同じネットワークのPCなど）からも開けるようにする
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    try:
        # 本番向けのサーバー waitress で動かす（無料）
        # 入れ方: sudo apt install python3-waitress
        from waitress import serve
        print(f"waitress で起動します（ポート {port}）", flush=True)
        serve(app, host="0.0.0.0", port=port, threads=8)
    except ImportError:
        # waitress が入っていないときは、Flask 付属の開発用サーバーで動かす（動作は同じ）
        print("waitress が入っていないため、開発用サーバーで起動します"
              "（sudo apt install python3-waitress を推奨）", flush=True)
        app.run(host="0.0.0.0", port=port)
