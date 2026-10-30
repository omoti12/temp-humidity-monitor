# 温湿度モニター

Raspberry Pi と温湿度センサー SHT31 で、温度・湿度を1分ごとに記録し、Webサイトで確認できるようにする試作システム。

**公開サイト：https://omoti12.github.io/temp-humidity-monitor/**

![スマホ表示](docs/screenshot_phone.png)

## できること

- 1分ごとに温度・湿度を測ってSDカードにCSVで保存（日付ごとに1ファイル）
- 温度の警報は2段階（注意・警報）。初期値は 30℃以上で【高温警報】、28℃以上で【高温注意】、22℃以下で【低温注意】、21℃以下で【低温警報】のメールを送信（正常に戻ったら【回復】メール、続けば60分ごとに再通知）
- 湿度にも上限の警報がある（初期値は 65%以上で【高湿注意】、70%以上で【高湿警報】。使わない設定にもできる）
- センサーで測れない状態が5分続いたら【センサー異常】、また測れるようになったら【回復】のメールを送る
- 警報の設定とメールの宛先は、公開サイトの管理欄からブラウザで変更できる
- 設置場所の名前を管理欄で登録すると、サイトの一番上とメールの件名（例:［第1倉庫］【高温警報】…）に出る
- 警報・注意・センサー異常のメールは、管理欄のボタンで止められる（1時間 / 再開するまで）。止めている間も測定・記録は続く
- Webサイトで現在の温度・湿度・状態と、10分ごとのグラフを表示（スマホ対応）
  - GitHub Pages：どこからでも見られる。グラフは10分ごと、現在値は5秒ごと（ラズパイから直接読む）
  - ラズパイ内のサイト（`http://ラズパイのIP:8000`）：同じネットワーク内から。1分ごとに更新
- グラフは拡大できる（表示範囲のボタン、PCはドラッグ、スマホは2本指）。拡大中は「◀ 前へ」「次へ ▶」ボタンで左右に動かせる
- 10分ごとにグラフ画像（PNG）を作成。ラズパイにつないだモニターで表示できる
- 1時間ごとに記録をGoogleドライブへバックアップ

```
センサー ─▶ ラズパイ（1分ごとに記録・警報メール）
              ├─ 10分ごと ─▶ GitHub Pages（どこからでも閲覧）
              ├─ 現在値 ─▶ Tailscale Funnel（/api/now だけ公開）─▶ GitHub Pages の画面
              └─ 1時間ごと ─▶ Googleドライブ（バックアップ）
```

## 構成

| ファイル | 内容 |
|---|---|
| `sht31_logger.py` | 測定・CSV記録・警報メール |
| `netcheck.py` | ネットの見張り役（つながらないときに、つなぎ直し・再起動） |
| `alarm_settings.py` | 警報の設定（2段階の温度の基準など）と状態の判定 |
| `alert_mail.py` | 警報メールの設定・宛先・送信 |
| `sht31_graph.py` | グラフ画像（PNG）の作成 |
| `thdrc_logger.py` | 比較用センサー THD-R-C（4〜20mA）の記録（RPi-GP40T で読む。使うときだけ） |
| `pt100_logger.py` | 比較用センサー Pt100（Y2Kb の USB モジュール）の記録（使うときだけ） |
| `compare_data.py` | 比較用センサー（THD-R-C・Pt100）の記録をサイト用に読む部品 |
| `pages/` | GitHub Pages 用のサイトと公開スクリプト |
| `web/` | ラズパイ内で動くサイト（Flask） |
| `systemd/` | 自動起動・定期実行の設定 |

記録先は `/home/iot-user/templog/`（SDカード）。
現在値 `now.json` とグラフ画像 `graph.png` は `/dev/shm/temp-monitor/`（メモリ上。再起動で消え、すぐ作り直される）。プログラムは `/home/iot-user/temp-monitor/`（このリポジトリ）から直接動かす。

## 必要なもの

- Raspberry Pi（ネット接続できること）
- 温湿度センサー Adafruit SHT31-D（I2Cアドレス 0x45 で使用。ADDRピン未接続なら `sht31_logger.py` の `ADDR` を 0x44 に変更）
- microSDカード（高耐久タイプ推奨）

## セットアップ

ユーザー名は `iot-user` 前提。違う場合は、各ファイルの `/home/iot-user` と `User=iot-user` を書き換える。

### 1. GitHub の準備（PCで）

- リポジトリを **Public** にする（Settings → General → 一番下の Danger Zone → Change visibility）
  ※ 無料プランで GitHub Pages を使うため。サイトはURLを知っている人なら誰でも見られる

### 2. インストール（ラズパイで）

```
sudo apt install git python3-smbus2 python3-matplotlib python3-flask python3-waitress fonts-noto-cjk rclone
git clone https://github.com/omoti12/temp-humidity-monitor.git /home/iot-user/temp-monitor
```

I2Cを有効にしておく（`sudo raspi-config` → Interface Options → I2C）。

### 3. 自動起動の設定ファイルを入れる

```
sudo cp /home/iot-user/temp-monitor/systemd/* /etc/systemd/system/
```

パスワードは **コピーした先（/etc/systemd/system）** で書き換える。リポジトリ内のファイルには書かないこと。

```
sudo nano /etc/systemd/system/temp-web.service       # ラズパイ内サイトにパスワードをかけるとき
```

警報メールを使うときは、設定ファイルを作って送信元・アプリパスワード・宛先を書く：

```
mkdir -p ~/.config/temp-monitor
cp ~/temp-monitor/mail.env.example ~/.config/temp-monitor/mail.env
chmod 600 ~/.config/temp-monitor/mail.env     # 自分だけが読めるようにする
nano ~/.config/temp-monitor/mail.env
python3 ~/temp-monitor/sht31_logger.py --test-mail   # テストメールを送る
sudo systemctl restart sht31-logger
```

Gmailの場合は、Googleアカウントで2段階認証をオンにして「アプリパスワード」を作り、それを使う。

#### サイトから警報の設定・メールの宛先を変更する

公開サイトの一番下「管理（警報の設定・メールの宛先）」から、警報の温度の基準などの変更と、メールの宛先の追加・削除ができる。

1. `~/.config/temp-monitor/mail.env` に `ADMIN_PASSWORD=（管理用パスワード）` を書く（半角英数字で長めに）
2. 管理の窓口もインターネットから使えるようにする（現在値の窓口と同じ Tailscale Funnel）
   ```
   sudo tailscale funnel --bg --set-path /api/recipients http://127.0.0.1:8000/api/recipients
   sudo tailscale funnel --bg --set-path /api/settings http://127.0.0.1:8000/api/settings
   ```
3. `sudo systemctl restart temp-web sht31-logger`

- 追加したアドレスには「宛先に追加されました」というお知らせメールが届く
- 宛先は `~/.config/temp-monitor/recipients.json` に保存される（一度でも変更すると、`MAIL_TO` の代わりにこちらが使われる）
- 注意の段階がいらないときは、注意と警報を同じ温度にする（その温度でいきなり警報になる）
- 警報の設定は `~/.config/temp-monitor/settings.json` に保存される。警報の判定には次の1分で反映され、公開サイトのグラフの線も1〜2分で変わる
- パスワードを10分間に10回まちがえると、しばらく受け付けない。宛先は20件まで。最後の1件は削除できない

### 4. 記録・警報・グラフを開始

```
sudo systemctl daemon-reload
sudo systemctl enable --now sht31-logger temp-web sht31-graph.timer
```

### 5. GitHub Pages で公開する

ラズパイがGitHubに書き込むための鍵を作る：

```
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
cat ~/.ssh/id_ed25519.pub
```

表示された1行（`ssh-ed25519 ...`）をコピーし、GitHubのリポジトリの
Settings → Deploy keys → Add deploy key に貼り付ける。**「Allow write access」にチェックを入れる。**

1回だけ手動で公開してみる（初回は `yes` と聞かれたら `yes`）：

```
ssh -T git@github.com
python3 /home/iot-user/temp-monitor/pages/publish.py
```

「公開しました」と出たら、GitHubの Settings → Pages で
Source を「Deploy from a branch」、Branch を `gh-pages` / `/(root)` にして Save。
数分後に https://omoti12.github.io/temp-humidity-monitor/ で見られる。

10分ごとの自動公開を開始：

```
sudo systemctl enable --now pages-publish.timer
```

### 6. 現在値をリアルタイムで見られるようにする（Tailscale Funnel）

GitHub Pages は更新回数に制限があるので、現在値だけはラズパイから直接読む。
ラズパイ内サイトを起動し、その中の `/api/now`（現在値）だけをインターネットに公開する。

```
sudo systemctl enable --now temp-web
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up                 # 表示されたURLを開いてログイン
sudo tailscale funnel --bg --set-path /api/now http://127.0.0.1:8000/api/now
```

最後のコマンドで有効化のURLが表示されたら、開いて許可する。
表示された `https://〜.ts.net/api/now` をブラウザで開き、現在値が出れば成功。
そのURLを `pages/index.html` の `NOW_URL` に書いて push し、ラズパイで `git pull` すると、次の公開（10分以内）からサイトの現在値がリアルタイムになる。

### 7. Googleドライブへのバックアップ

```
rclone config        # n → 名前 gdrive → 種類 drive → あとはEnter
sudo systemctl enable --now templog-backup.timer
rclone ls gdrive:温湿度記録   # CSVが並べば成功
```

### 8. 比較用センサー THD-R-C（使うときだけ）

4〜20mA 出力の温湿度センサー Autonics THD-R-C を RPi-GP40T（Ratoc）で読み、SHT31 と比べられるようにする。
動かすと、サイトに「SHT31 と THD-R-C の比較（今の値）」の表と、グラフに THD-R-C の点線が出る（動かさなければ何も出ない）。

- つなぎ方：THD-R-C の A（温度）→ GP40T の A0、B（湿度）→ A1。A0–G0 と A1–G1 のあいだに 150Ω。G0・G1 は THD-R-C の電源の −（GND）へ
- GP40T を使うには SPI を有効にし、`/boot/firmware/config.txt` に `dtoverlay=spi0-0cs` を書いておく
- 記録は `/home/iot-user/templog/thdrc/日付.csv`、現在値は `/dev/shm/temp-monitor/thdrc.json`

```
sudo cp ~/temp-monitor/systemd/thdrc-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now thdrc-logger
sudo systemctl restart temp-web
```

動いている間は GP40T を使っているので、ほかのプログラムで GP40T を読むときは `sudo systemctl stop thdrc-logger` で止める。

### 9. 比較用センサー Pt100（使うときだけ）

動かすと、比較の表に Pt100 の行と、温度のグラフに Pt100 の点線が出る（Pt100 に湿度はない）。

- つなぎ方：Y2Kb の Pt100 温度測定モジュールを USB でラズパイにつなぐ（`/dev/ttyUSB0`）。モジュールは出荷時の設定（USB Tx mode = Auto、10秒ごと）のまま
- K型熱電対のモジュールも同じ形のデータを送ってくるので、USB につなぐのは Pt100 のほうにする
- 記録は `/home/iot-user/templog/pt100/日付.csv`、現在値は `/dev/shm/temp-monitor/pt100.json`

```
sudo cp ~/temp-monitor/systemd/pt100-logger.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pt100-logger
sudo systemctl restart temp-web
```

動いている間はモジュールの USB ポートを使っているので、`y2kb_read.py` で読むときは `sudo systemctl stop pt100-logger` で止める。

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
journalctl -u pages-publish -n 20        # GitHub Pages への公開ログ
systemctl list-timers                    # 定期実行の予定
```

## 長く動かすために

SDカードは書き込み回数で消耗するので、書き込みを減らしている。

| データ | 場所 | 書き込み |
|---|---|---|
| 記録（CSV） | SDカード | 1分に1行追記 |
| 現在値（5秒ごと）・グラフ画像（10分ごと） | メモリ（`/dev/shm`） | SDカードには書かない |
| 公開用の作業データ | SDカード | 10分ごと。古い版は毎日3時台に自動で掃除 |

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

- GitHub の main ブランチはルールセット（main-protect）で守っている。書き込めるのはリポジトリの持ち主と Claude だけで、
  ラズパイの鍵（Deploy key）は gh-pages（公開サイト）にしか書き込めない
- ラズパイでは sudo にパスワードが必要（`/etc/sudoers.d/010_iot-user-nopasswd` は削除済み）
- Node-RED は使っていないので止めている（使うときは `sudo systemctl enable --now nodered`。その場合はパスワードを設定すること）
- インターネットに出しているのは `/api/now`・`/api/settings`・`/api/recipients` の3つだけ（Tailscale Funnel）。
  設定と宛先は管理用パスワードが必要で、まちがいが続くと相手ごとに10分止める
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
