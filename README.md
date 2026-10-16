# 温湿度モニター

Raspberry Pi と温湿度センサー SHT31 で、温度・湿度を1分ごとに記録し、Webサイトで確認できるようにする試作システム。

![スマホ表示](docs/screenshot_phone.png)

## できること

- 1分ごとに温度・湿度を測ってSDカードにCSVで保存（日付ごとに1ファイル）
- 25℃以上で【高温警報】、21℃以下で【低温警報】のメールを送信（正常に戻ったら【回復】メール、異常が続けば60分ごとに再通知）
- Webサイトで現在の温度・湿度・状態と、10分ごとのグラフを表示（スマホ対応、1分ごとに自動更新）
- 10分ごとにグラフ画像（PNG）を作成。ラズパイにつないだモニターで表示できる
- 1時間ごとに記録をGoogleドライブへバックアップ
- Tailscale で、同じWi-Fiでなくても外から閲覧できる

## 構成

| ファイル | 内容 |
|---|---|
| `sht31_logger.py` | 測定・CSV記録・警報メール |
| `sht31_graph.py` | グラフ画像（PNG）の作成 |
| `web/` | Webサイト（Flask） |
| `systemd/` | 自動起動・定期実行の設定 |

記録先は `/home/pi/templog/`。

## 必要なもの

- Raspberry Pi（ネット接続できること）
- 温湿度センサー Adafruit SHT31-D（I2Cアドレス 0x45 で使用。ADDRピン未接続なら `sht31_logger.py` の `ADDR` を 0x44 に変更）
- microSDカード（高耐久タイプ推奨）

## セットアップ

ユーザー名が `pi` 以外の場合は、各ファイルの `/home/pi` と `User=pi` を書き換える。

### 1. インストール

```
sudo apt install python3-smbus2 python3-matplotlib python3-flask fonts-noto-cjk rclone
git clone <このリポジトリのURL> /home/pi/temp-monitor
cp /home/pi/temp-monitor/sht31_logger.py /home/pi/temp-monitor/sht31_graph.py /home/pi/
cp -r /home/pi/temp-monitor/web /home/pi/
```

I2Cを有効にしておく（`sudo raspi-config` → Interface Options → I2C）。

### 2. 設定を書き換える

- `systemd/sht31-logger.service`：警報メールの送信元・アプリパスワード・宛先
  （Gmailの場合、2段階認証をオンにして「アプリパスワード」を作成する）
- `systemd/temp-web.service`：Webサイトのパスワード

**パスワードを書き換えたファイルはこのリポジトリにコミットしないこと。**

### 3. 自動起動を設定する

```
sudo cp /home/pi/temp-monitor/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now sht31-logger temp-web sht31-graph.timer
```

### 4. Googleドライブへのバックアップ

```
rclone config        # n → 名前 gdrive → 種類 drive → あとはEnter
sudo systemctl enable --now templog-backup.timer
rclone ls gdrive:温湿度記録   # CSVが並べば成功
```

### 5. 外から見る（Tailscale）

```
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

スマホにTailscaleアプリを入れて同じアカウントでログインし、`http://ラズパイの名前:8000` を開く。

## 確認方法

```
systemctl status sht31-logger      # 記録の動作状況
tail -f /home/pi/templog/*.csv      # 記録の中身
journalctl -u sht31-logger -f       # 警報メールの送信ログ
```

## 設定値

| 項目 | 値 | 場所 |
|---|---|---|
| 測定間隔 | 60秒 | `sht31_logger.py` の `INTERVAL` |
| 高温警報 | 25℃以上 | `TEMP_HIGH`（3ファイルとも同じ値にする） |
| 低温警報 | 21℃以下 | `TEMP_LOW`（同上） |
| 回復と判断する幅 | 0.5℃ | `sht31_logger.py` の `HYSTERESIS` |
| 再通知の間隔 | 60分 | `sht31_logger.py` の `REMIND_MIN` |
| グラフの間隔 | 10分 | `STEP_MIN` |

## ライセンス

`web/static/chart.umd.js` は [Chart.js](https://www.chartjs.org/)（MIT License、`web/static/chart.js-LICENSE.md`）。
