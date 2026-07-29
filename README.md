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

## インストール

### HACS

1. HACSでこのrepositoryをcustom repositoryとして追加します。
2. PairLinkをインストールします。
3. Home Assistantを再起動します。

### 手動

`custom_components/pairlink`をHome Assistant設定directoryの
`custom_components/pairlink`へコピーし、Home Assistantを再起動します。

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
