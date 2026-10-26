# 温湿度モニター

Raspberry Pi と温湿度センサー SHT31 で、温度・湿度を1分ごとに記録し、Webサイトで確認できるようにする試作システム。
**サイトはラズパイの中だけで動かし、インターネットには公開しない。**

- 社内のネットワーク（ラズパイと同じWi-Fiなど）から：http://raspberrypi.local:8000
- 外から（Tailscale にログインした端末だけ）：https://raspberrypi.tailfa1dcc.ts.net

![スマホ表示](docs/screenshot_phone.png)

## できること

- 1分ごとに温度・湿度を測ってSDカードにCSVで保存（日付ごとに1ファイル）
- 温度の警報は2段階（注意・警報）。初期値は 30℃以上で【高温警報】、28℃以上で【高温注意】、22℃以下で【低温注意】、21℃以下で【低温警報】のメールを送信（正常に戻ったら【回復】メール、続けば60分ごとに再通知）
- 湿度にも上限の警報がある（初期値は 65%以上で【高湿注意】、70%以上で【高湿警報】。使わない設定にもできる）
- センサーで測れない状態が5分続いたら【センサー異常】、また測れるようになったら【回復】のメールを送る
- サイトで現在の温度・湿度・状態（5秒ごとに更新）と、10分ごとのグラフを表示（スマホ対応）
- グラフは拡大できる（表示範囲のボタン、PCはドラッグ、スマホは2本指）。拡大中は「◀ 前へ」「次へ ▶」ボタンで左右に動かせる
- 警報の設定とメールの宛先は、サイトの管理欄からブラウザで変更できる
- 設置場所の名前を管理欄で登録すると、サイトの一番上とメールの件名（例:［第1倉庫］【高温警報】…）に出る
- 警報・注意・センサー異常のメールは、管理欄のボタンで止められる（1時間 / 再開するまで）。止めている間も測定・記録は続く
- スマホのホーム画面にアイコンを置いて、アプリのように開ける（PWA）
- 10分ごとにグラフ画像（PNG）を作成。ラズパイにつないだモニターで表示できる

```
センサー ─▶ ラズパイ（記録・警報メール・サイト）
              ├─ 社内のネットワーク ─────────────▶ スマホ・PC（http://raspberrypi.local:8000）
              ├─ Tailscale（暗号化した専用の通り道）─▶ Tailscale を入れたスマホ・PC（外からでも）
              └─ 警報メール ─▶ Gmail ─▶ 宛先
```

インターネットの他の人からは見えない。クラウドにもデータを置かない（外のサービスを通るのは警報メールだけ）。

## 構成

| ファイル | 内容 |
|---|---|
| `sht31_logger.py` | 測定・CSV記録・警報メール |
| `netcheck.py` | ネットの見張り役（つながらないときに、つなぎ直し・再起動） |
| `alarm_settings.py` | 警報の設定（2段階の温度・湿度の基準など）と状態の判定 |
| `alert_mail.py` | 警報メールの設定・宛先・送信 |
| `sht31_graph.py` | グラフ画像（PNG）の作成 |
| `web/app.py` | サイト（Flask）。画面は `web/index.html`、部品とアイコンは `web/static/` |
| `systemd/` | 自動起動・定期実行の設定 |

記録先は `/home/iot-user/templog/`（SDカード）。
現在値 `now.json` とグラフ画像 `graph.png` は `/dev/shm/temp-monitor/`（メモリ上。再起動で消え、すぐ作り直される）。
設定（メール・宛先・警報の設定）は `/home/iot-user/.config/temp-monitor/`。
プログラムは `/home/iot-user/temp-monitor/`（このリポジトリ）から直接動かす。

## 必要なもの

- Raspberry Pi（Pi 5 で試作。現場にモニターを置かず VS Code でもつながないなら Zero 2 WH でも動く）
- 温湿度センサー Adafruit SHT31-D（I2Cアドレス 0x45 で使用。ADDRピン未接続なら `sht31_logger.py` の `ADDR` を 0x44 に変更）
- microSDカード（高耐久タイプ推奨）

## セットアップ

ユーザー名は `iot-user` 前提。違う場合は、各ファイルの `/home/iot-user` と `User=iot-user` を書き換える。

### 1. インストール（ラズパイで）

```
sudo apt install git python3-smbus2 python3-matplotlib python3-flask python3-waitress fonts-noto-cjk rclone
git clone https://github.com/omoti12/temp-humidity-monitor.git /home/iot-user/temp-monitor
```

I2Cを有効にしておく（`sudo raspi-config` → Interface Options → I2C）。

### 2. 自動起動の設定ファイルを入れる

```
sudo cp /home/iot-user/temp-monitor/systemd/*.service /home/iot-user/temp-monitor/systemd/*.timer /etc/systemd/system/
```

### 3. 警報メールの設定

設定ファイルを作って、送信元・アプリパスワード・宛先・管理用パスワードを書く：

```
mkdir -p ~/.config/temp-monitor
cp ~/temp-monitor/mail.env.example ~/.config/temp-monitor/mail.env
chmod 600 ~/.config/temp-monitor/mail.env     # 自分だけが読めるようにする
nano ~/.config/temp-monitor/mail.env
python3 ~/temp-monitor/sht31_logger.py --test-mail   # テストメールを送る
```

Gmailの場合は、Googleアカウントで2段階認証をオンにして「アプリパスワード」を作り、それを使う。
`ADMIN_PASSWORD` はサイトの管理欄（警報の設定・宛先・メールの一時停止）で使うパスワード（半角英数字で長めに）。

### 4. 記録・警報・サイト・グラフを開始

```
sudo systemctl daemon-reload
sudo systemctl enable --now sht31-logger temp-web sht31-graph.timer
```

社内のネットワークのブラウザで http://raspberrypi.local:8000 を開いて、温度が出れば成功。

### 5. 外から見られるようにする（Tailscale。インターネットには公開しない）

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                 # 表示されたURLを開いてログイン
sudo tailscale set --ssh          # 外からコンソールでつなげるようにする（任意）
sudo tailscale serve --bg 8000    # Tailscale の端末だけに https でサイトを見せる
```

見る端末（スマホ・PC）にも Tailscale のアプリを入れて、同じネットワークにログインする。
https://raspberrypi.tailfa1dcc.ts.net を開いて、温度が出れば成功。

`tailscale funnel`（インターネットへの公開）は使わない。`tailscale serve status` で
「(tailnet only)」と出ていれば、Tailscale の端末だけに見えている。

### 6. スマホのホーム画面に置く（任意）

**必ず https://raspberrypi.tailfa1dcc.ts.net （Tailscale の https のアドレス）で開いてから追加する。**
http://raspberrypi.local:8000 から追加すると、ただのブックマークになってブラウザの画面のまま開く。

- iPhone：**Safari** で開く → 共有ボタン →「ホーム画面に追加」（「Webアプリとして開く」の切り替えがあればオン）
- Android：**Chrome** で開く → 右上の「︙」→「**アプリをインストール**」（「ホーム画面に追加」しか出ないときは、1〜2回再読み込みしてから）

アイコンから開くと、アドレスバーのない全画面で表示される。前に追加したアイコンがブラウザの画面で開く場合は、そのアイコンを消して追加し直す。

### 7. Googleドライブへのバックアップ（任意）

```
rclone config        # n → 名前 gdrive → 種類 drive → あとはEnter
sudo systemctl enable --now templog-backup.timer
rclone ls gdrive:温湿度記録   # CSVが並べば成功
```

#### 管理欄について

- 追加したアドレスには「宛先に追加されました」というお知らせメールが届く
- 宛先は `~/.config/temp-monitor/recipients.json` に保存される（一度でも変更すると、`MAIL_TO` の代わりにこちらが使われる）
- 注意の段階がいらないときは、注意と警報を同じ温度にする（その温度でいきなり警報になる）
- 警報の設定は `~/.config/temp-monitor/settings.json` に保存される。警報の判定には次の1分で反映される
- パスワードを10分間に10回まちがえると、しばらく受け付けない。宛先は20件まで。最後の1件は削除できない

## 更新するとき

```
cd /home/iot-user/temp-monitor && git pull
sudo systemctl restart sht31-logger temp-web
```

## 確認方法

```
systemctl status sht31-logger           # 記録の動作状況
tail -f /home/iot-user/templog/*.csv           # 記録の中身
journalctl -u sht31-logger -f            # 警報メールの送信ログ
journalctl -u temp-web -n 20             # サイトのログ（宛先・設定の変更）
systemctl list-timers                    # 定期実行の予定
```

## 長く動かすために

SDカードは書き込み回数で消耗するので、書き込みを減らしている。

| データ | 場所 | 書き込み |
|---|---|---|
| 記録（CSV） | SDカード | 1分に1行追記 |
| 現在値（5秒ごと）・グラフ画像（10分ごと） | メモリ（`/dev/shm`） | SDカードには書かない |

ほかに効果が大きいもの：

- **Googleドライブへのバックアップ**（手順7）：SDカードが壊れても記録が残る
- **UPS（停電対策）**：電源が急に切れるとSDカードが壊れることがある
- **高耐久のSDカード**を使う

止まりにくくする・止まったときに原因を追えるようにする設定（ラズパイで1回だけ）：

```
# Wi-Fiの省電力モードを切る（Wi-Fiが切れて外から見えなくなるのを防ぐ）
printf '[connection]\nwifi.powersave = 2\n' | sudo tee /etc/NetworkManager/conf.d/wifi-powersave-off.conf
sudo systemctl restart NetworkManager

# 動作ログを再起動後も残す（最大50MB）。止まる直前のログは journalctl -b -1 で見られる
sudo mkdir -p /etc/systemd/journald.conf.d /var/log/journal
printf '[Journal]\nStorage=persistent\nSystemMaxUse=50M\n' | sudo tee /etc/systemd/journald.conf.d/persistent.conf
sudo systemctl restart systemd-journald
```

ラズパイ全体が固まったときに自動で再起動する（ウォッチドッグ）：

Raspberry Pi OS では最初から有効になっている（固まってから1分で再起動）。確認だけでよい。

```
systemctl show -p RuntimeWatchdogUSec    # 0 以外（例: 1min）なら有効
```

`0` と出たとき（無効のとき）だけ、次で有効にする（15秒で再起動する設定）：

```
sudo mkdir -p /etc/systemd/system.conf.d
sudo cp ~/temp-monitor/systemd/watchdog.conf /etc/systemd/system.conf.d/
sudo systemctl daemon-reexec
```

再起動すると記録などは自動で再開する。ただし「重いけれど動いている」「ネットだけ切れている」ときは再起動しない。

ネットが切れたときに自動で直す（ネットの見張り役 `netcheck.py`）：

```
sudo install -o root -g root -m 755 ~/temp-monitor/netcheck.py /usr/local/sbin/temp-monitor-netcheck.py
sudo cp ~/temp-monitor/systemd/netcheck.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netcheck
```

見張り役は root で動くので、iot-user が書き換えられない `/usr/local/sbin` にコピーしたものを動かす。
`netcheck.py` を更新したときは、1行目の `install` をもう一度実行してから `sudo systemctl restart netcheck`。

インターネットにつながらない状態が5分続いたらネットワークをつなぎ直し、15分続いたら再起動する。
ルーターまで届いていてインターネット側だけが切れているときは再起動しない。再起動は1時間に1回まで。
ログは `journalctl -u netcheck -n 20`（状態が変わったときだけ書く）。

電源アダプタはラズパイ5用の 5V 5A（27W）を使う。力不足だと負荷がかかったときに止まる
（`vcgencmd get_throttled` が `throttled=0x0` 以外なら電圧不足が起きている）。

ラズパイにつないだモニターにグラフ画像を出し続けるとき：
`feh --fullscreen --reload 60 /dev/shm/temp-monitor/graph.png`

## セキュリティの設定

- サイトはインターネットに公開しない。見られるのは社内のネットワークと Tailscale の端末だけ（`tailscale serve`。Funnel は使わない）
- 設定と宛先は管理用パスワードが必要で、まちがいが続くと相手ごとに10分止める
- GitHub の main ブランチはルールセット（main-protect）で守っている。書き込めるのはリポジトリの持ち主と Claude だけ
- ラズパイでは sudo にパスワードが必要（`/etc/sudoers.d/010_iot-user-nopasswd` は削除済み）
- Node-RED は使っていないので止めている（使うときは `sudo systemctl enable --now nodered`。その場合はパスワードを設定すること）
- ソフトの更新はときどき `sudo apt update && sudo apt full-upgrade -y`

## 設定値

| 項目 | 値 | 場所 |
|---|---|---|
| 記録・警報判定の間隔 | 60秒 | `sht31_logger.py` の `INTERVAL` |
| 現在値の更新間隔 | 5秒 | `sht31_logger.py` の `NOW_INTERVAL` |
| 高温警報 | 30℃以上 | サイトの管理欄（初期値は `alarm_settings.py` の `DEFAULTS`） |
| 高温注意 | 28℃以上 | 同上 |
| 低温注意 | 22℃以下 | 同上 |
| 低温警報 | 21℃以下 | 同上 |
| 回復と判断する幅 | 0.5℃ | 同上 |
| 再通知の間隔 | 60分 | 同上 |
| 「注意」でもメールを送る | する | 同上（温度・湿度共通） |
| 湿度の警報を使う | 使う | 同上 |
| 高湿警報 | 70%以上 | 同上 |
| 高湿注意 | 65%以上 | 同上 |
| 湿度の回復の幅 | 2% | 同上 |
| センサー異常を知らせるまで | 5分 | 同上 |
| 設置場所の名前 | （なし） | 同上 |
| グラフの間隔 | 10分 | `STEP_MIN` |

## ライセンス

`web/static/` の次のファイルは外部のもの（いずれも MIT License。ライセンス文は同じフォルダの `*-LICENSE.md`）。

- `chart.umd.js`：[Chart.js](https://www.chartjs.org/) 4.4.1（グラフ）
- `chartjs-plugin-zoom.min.js`：[chartjs-plugin-zoom](https://github.com/chartjs/chartjs-plugin-zoom) 2.0.1（グラフの拡大）
- `hammer.min.js`：[Hammer.js](https://hammerjs.github.io/) 2.0.8（スマホの指の操作）
