#!/usr/bin/env python3
"""ネットの見張り役: インターネットにつながらない状態が続いたら、自動で直す。"""
# ============================================================
# このプログラムの役割
#   1分ごとにインターネットにつながっているか確かめる。つながらない状態が続いたら:
#     5分  → ネットワークをつなぎ直す（NetworkManager の再起動 = Wi-Fi の再接続）
#     15分 → ラズパイを再起動する
#   ただし、ルーター（Wi-Fiの親機）までは届いていて、その先のインターネットだけが切れているときは、
#   ラズパイ側の問題ではないので再起動しない。再起動は1時間に1回まで。
#
# 動かし方
#   systemd（netcheck.service）が root として常に動かす（つなぎ直し・再起動に root の権限が要るため）
#   root で動くので、iot-user が書き換えられない /usr/local/sbin/temp-monitor-netcheck.py にコピーして動かす
#   （このファイルを直したら: sudo install -o root -g root -m 755 netcheck.py /usr/local/sbin/temp-monitor-netcheck.py）
#   ログを見る : journalctl -u netcheck -n 20
#   （ログは状態が変わったときだけ書く。SDカードへの書き込みを増やさないため）
# ============================================================
import os
import socket
import subprocess
import time

CHECK_SEC = 60              # 確かめる間隔（秒）
WIFI_RESTART_MIN = 5        # この分数つながらなければ、ネットワークをつなぎ直す
REBOOT_MIN = 15             # この分数つながらなければ、ラズパイを再起動する
REBOOT_INTERVAL_MIN = 60    # 再起動は、前回の再起動からこの分数たってから（くり返さないため）
# 前回の再起動の時刻を覚えておくファイル（再起動しても消えないよう、SDカードに置く。書くのは再起動のときだけ）
LAST_REBOOT_PATH = "/var/lib/temp-monitor/last-net-reboot"

# つながっているかを確かめる相手（どれか1つにつながればOK）
TARGETS = [("1.1.1.1", 53), ("8.8.8.8", 53), ("github.com", 443)]


def internet_ok():
    """インターネットにつながっているか（相手のどれか1つに、5秒以内につながればOK）"""
    for host, port in TARGETS:
        try:
            with socket.create_connection((host, port), timeout=5):
                return True
        except OSError:
            continue
    return False


def gateway_ok():
    """ルーター（Wi-Fiの親機）まで届いているか"""
    try:
        route = subprocess.run(["ip", "route", "show", "default"],
                               capture_output=True, text=True, timeout=5).stdout.split()
        gw = route[route.index("via") + 1]      # 例: "default via 192.168.1.1 dev wlan0" → 192.168.1.1
    except (ValueError, IndexError, OSError, subprocess.SubprocessError):
        return False                            # ルーターの情報もない = Wi-Fi自体がつながっていない
    return subprocess.run(["ping", "-c", "1", "-W", "3", gw],
                          capture_output=True).returncode == 0


def last_reboot():
    """前回、このプログラムが再起動した時刻（なければ 0）"""
    try:
        with open(LAST_REBOOT_PATH) as f:
            return float(f.read().strip())
    except (OSError, ValueError):
        return 0.0


def log(msg):
    # print の内容は動作ログに残る（journalctl -u netcheck で見られる）
    print(msg, flush=True)


def run(sleep=time.sleep, now=time.time, act=subprocess.run):
    """見張りを続ける。sleep・now・act はテストで差し替えられるようにしてある"""
    down_since = None       # つながらなくなった時刻（つながっているときは None）
    restarted = False       # この「つながらない間」に、つなぎ直しをしたか
    while True:
        if internet_ok():
            if down_since is not None:
                log(f"インターネットに戻りました（{int((now() - down_since) // 60)}分間つながっていませんでした）")
            down_since, restarted = None, False
        else:
            if down_since is None:
                down_since = now()
                log("インターネットにつながりません。様子を見ます")
            down_min = (now() - down_since) / 60
            # 5分: ネットワークをつなぎ直す（1回だけ）
            if down_min >= WIFI_RESTART_MIN and not restarted:
                log(f"{int(down_min)}分つながらないため、ネットワークをつなぎ直します")
                act(["systemctl", "restart", "NetworkManager"])
                restarted = True
            # 15分: ラズパイを再起動する（ルーターにも届かないときだけ。1時間に1回まで）
            elif down_min >= REBOOT_MIN:
                if gateway_ok():
                    pass    # ルーターには届いている = インターネット側の問題。再起動しても直らない
                elif now() - last_reboot() < REBOOT_INTERVAL_MIN * 60:
                    pass    # 少し前に再起動したばかり。くり返さない
                else:
                    log(f"{int(down_min)}分つながらず、ルーターにも届かないため、ラズパイを再起動します")
                    os.makedirs(os.path.dirname(LAST_REBOOT_PATH), exist_ok=True)
                    with open(LAST_REBOOT_PATH, "w") as f:
                        f.write(str(now()))
                    act(["systemctl", "reboot"])
        sleep(CHECK_SEC)


# このファイルを直接実行したときの入口
if __name__ == "__main__":
    run()
