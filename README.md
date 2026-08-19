# PairLink for Home Assistant

PairLink対応スイッチへHome AssistantからBLE Centralとして常時接続し、物理的な
ON/OFF操作をEvent Entityとして利用できるcustom integrationです。

暗号処理、
広告解析、接続管理はこのrepository内に含まれます。

## 対応範囲

このintegrationはPairLink壁スイッチ専用です。

- 壁スイッチの物理ON/OFF操作をEvent Entityとして通知します。
- PairLink電球を直接操作する`light`エンティティは提供しません。
- BLE広告の送信やGATT Peripheral / GATT Serverの公開は行いません。

PairLink電球はBLE Central、壁スイッチはBLE Peripheralとして動作するため、電球の
直接制御にはHome Assistantの通常のBluetooth APIでは提供されていないPeripheral機能が
必要です。この機能は、integrationを小さく安定したself-contained構成に保つため、
対応範囲外とします。

壁スイッチのイベントをトリガーにして、Home Assistantへ既に登録されている通常の
照明やその他のentityをオートメーションで操作することはできます。

## 必要環境

- Home Assistant 2026.7以降
- 接続可能なlocal Bluetooth adapterまたはBluetooth proxy
- PairLink対応スイッチ

接続経路の選択と切り替えはHome AssistantのBluetooth Managerへ委譲しています。
ESPHomeを含む接続可能なBluetooth proxyを利用できる設計で、local BlueZ adapter固有の
APIには依存しません。listen-only Bluetooth proxyでは利用できません。

スイッチごとに常時GATT接続を1つ使用するため、同時利用するスイッチ数以上のconnection
slotがproxy側に必要です。現時点の実機検証はlocal adapterで完了しており、ESP32 proxy
経由のhardware acceptanceは今後の検証項目です。

### Aruba APをBluetooth proxyとして使う

AP-635などのAruba IoT radioを使う場合は、先に
[Aruba BLE Proxy](https://github.com/robertoamd90/aruba-ble-proxy)をHome Assistantへ
インストールしてActive BLEを有効にします。複数APは同じWebSocket listenerへ接続でき、
各APが独立したconnectable scannerとしてHome Assistant Bluetooth Managerへ登録されます。

PairLinkはAP MACをDeviceやConfig Entryのidentityに使いません。スイッチのPairLink
remote IDから復元した正規MACをConfig Entryのunique IDとし、APはその時点の接続経路
としてだけ扱います。同じスイッチをAP-AとAP-Bが同時に観測してもDeviceは1つです。
現在の接続が切れた後はBluetooth Managerから経路を再解決するため、AP-Bが選ばれても
同じEvent Entityのまま復旧します。接続中に無停止でAPを切り替えるmake-before-breakでは
ありません。

Aruba APが通知なしにidle GATT接続を失うことがあるため、READY中は60秒ごとに標準GAP
Device Nameをreadします。このreadはリンク確認とkeepaliveを兼ね、失敗時は通常の再接続
処理へ移ります。

Aruba BLE Proxy 1.1.1がBluetooth SIGの16-bit UUIDを128-bitへ展開して送る問題に対し、
PairLink 0.2.0は使用するUUIDだけをnative 2-byte形式へ補正します。Aruba側がnative
16-bit送信へ対応済みなら補正は自動的に無効になります。

## インストール

### HACS

1. HACSでこのrepositoryをcustom repositoryとして追加します。
2. PairLinkをインストールします。
3. Home Assistantを再起動します。

### 手動

`custom_components/pairlink`をHome Assistant設定directoryの
`custom_components/pairlink`へコピーし、Home Assistantを再起動します。

### Gitで更新する場合

このrepositoryは開発用ファイルを含むため、repository全体を直接
`custom_components/pairlink`へcloneする構成ではありません。設定directoryの外へcloneし、
integration directoryをsymlinkすると、`git pull`で更新できます。

```bash
cd /config
git clone https://github.com/nunnun/ha-pairlink.git ha-pairlink
mkdir -p custom_components
ln -s ../ha-pairlink/custom_components/pairlink custom_components/pairlink
```

更新時は次のコマンドを実行し、Home Assistantを再起動します。

```bash
git -C /config/ha-pairlink pull --ff-only
```

既存の`custom_components/pairlink` directoryがある場合は、symlinkを作成する前に
backupまたは削除を行ってください。

## スイッチの追加

1. Home Assistantの「設定」→「デバイスとサービス」を開きます。
2. 発見されたPairLinkスイッチを選択するか、「統合を追加」からPairLinkを選びます。
3. 対象スイッチの登録ボタン2を、LEDが1回点灯するまで押し続けてから離します。
4. 登録広告の取得とLOGIN検証が成功すると、Event Entityが作成されます。

各スイッチは個別のConfig Entryになります。複数スイッチは同じBluetooth adapterから
並行接続され、ON/OFFの送信元も別々のDeviceとして扱われます。

## 受信信号強度

各スイッチには診断用の「受信信号強度」Sensor Entityも作成されます。値はBluetooth広告を
最後に受信したときのRSSIで、単位はdBmです。最初の広告を受信するまではunavailableです。

## Event Entity

Event type:

- `on`
- `off`

追加属性:

- `channel`
- `command`
- `command_hex`
- `extra`
- `repeat_count`

実機確認では、短時間に続くpacketは再送ではなく物理ボタンの連打として発生しました。
そのため重複抑制は既定で無効にし、受信したON/OFF操作をすべて通知します。

## セキュリティ

スイッチの4文字passwordは接続に必要なためConfig Entryへ保存され、Home Assistantの
backupにも含まれます。通常ログとdiagnosticsにはpassword、Home ID、full BLE address、
remote ID、light ID、packet plaintext/ciphertextを出力しません。

## Production環境での更新

新しいversionは、まず別環境または検証用Home Assistantで起動・再接続・unloadを確認して
ください。Productionへ配置する前にHome Assistant backupを取得することを推奨します。

## 開発

```bash
python3.14 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest
```

BLE実機を使わない暗号、広告、Config Flow、session lifecycleテストを含みます。
Raspberry Piでの実機確認結果は
[`HARDWARE_VALIDATION.md`](HARDWARE_VALIDATION.md)に記録しています。
