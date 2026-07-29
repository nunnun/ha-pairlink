# PairLink Home Assistant Integration 実装案

## 1. 作るもの

PairLink対応スイッチへHome AssistantからBLE Centralとして常時接続し、物理ON/OFFを
Home AssistantのEvent Entityとして通知するcustom integrationを作る。

このintegrationは壁スイッチ専用とし、PairLink電球を直接操作する`light`
エンティティ、BLE広告送信、GATT Peripheral / GATT Server公開は実装しない。
PairLink電球の直接制御にはHome Assistantの公式Bluetooth API外のBlueZ D-Bus操作、
専用App、または外部daemonが必要になるため、将来TODOではなく明示的な非対応範囲とする。

初期リリース`0.1.0`の対象は次のとおり。

- UIのBluetooth discoveryからスイッチを1台ずつ登録
- 登録ボタン操作で`type 0x0d`広告からcredentialを取得
- LOGIN handshakeを成功確認してからConfig Entryを作成
- 1スイッチを1 Config Entry / 1 Device / 1 Event Entityとして表現
- 1つのBluetooth adapterから複数スイッチへ並行接続
- ON/OFFイベントの受信（連打を含む全packetを通知）
- 一時切断・Home Assistant再起動後の自動再接続
- credential不一致時のreauth
- credentialと実機識別情報を除外したdiagnostics
- HACS custom repositoryとして導入可能な構成

初期リリースには、dedup windowを変更するOptions Flow、未知commandのentity化、
3台以上の動作保証は含めない。

実装基準はHome Assistant Core 2026.7以降とする。使用するBluetooth APIとConfig Entry
更新方式をこのversionで固定し、`hacs.json`にもminimum versionを記載する。

## 2. Self-contained方針と参考実装から再利用する範囲

完成する`ha-pairlink`はself-containedとし、`pairlink-proto`へ実行時、テスト時、
ビルド時のいずれも依存しない。

具体的には次を禁止する。

- `pairlink-proto`からのPython import
- 相対pathや絶対pathを使ったmodule参照
- Git submodule / subtreeによる実行時参照
- editable installやlocal path dependency
- test実行時に`pairlink-proto`のfileやtest vectorを読む処理
- Home Assistant環境へ`pairlink-proto`を配置する前提

必要なprotocol実装、dataclass、定数、匿名化test vector、fixtureはすべて
`ha-pairlink`内へ取り込み、このrepositoryだけをcloneして開発、テスト、導入できる
状態にする。`pairlink-proto`は仕様確認と移植元の参考資料としてのみ扱う。

`pairlink-proto`から次を移植する。

| 移植元 | 移植先 | 方針 |
|---|---|---|
| `pairlink_codec.py` | `protocol.py` | AES-128、PKCS#7、鍵導出、LOGIN、event復号・parseを移植 |
| `pairlink_discovery.py` | `discovery.py` | 広告parserだけ移植し、独自scannerとcredential cacheは移植しない |
| `pairlink_button_listener.py` | `session.py` | notify queue、handshake、dedupの考え方をHA非同期lifecycleへ再構成 |
| `test_pairlink_codec.py` | `tests/test_protocol.py` | 匿名化vectorを維持 |
| `test_pairlink_discovery.py` | `tests/test_discovery.py` | 匿名化広告vectorを維持 |
| `MULTI_SWITCH_VALIDATION.md` | hardware acceptance | 2セッション分離の期待値として使用 |

以下は移植しない。

- `BleakScanner`の直接起動
- CLI、引数処理、stdout reporter
- home directoryのcredential cache
- packet plaintext/ciphertextのログ出力
- 接続ごとの同一`BleakClient`再利用

## 3. 設計書に対する実装時の調整

### 3.1 Bluetooth manifest matcher

設計書のmatcherは`manufacturer_id=65535`と`C0 FF`を同じmatcherへ指定している。
一方、実測parserはcompany ID差異を許容するため、これではPairLink payloadでも
company IDが異なる個体をdiscoveryできない。

初期実装はOR条件の2 matcherとする。

```json
"bluetooth": [
  {
    "connectable": true,
    "local_name": "connected-switch*"
  },
  {
    "connectable": true,
    "manufacturer_id": 65535,
    "manufacturer_data_start": [192, 255]
  }
]
```

Config Flow側で全manufacturer valueを再parseし、完全な`type 0x05`または`0x0d`、
`remote_id`ありの広告だけを受理する。これによりmatcherは発見の入口、parserは正当性
判定という役割分担になる。

### 3.2 登録広告待機

一時callback、active scan、timeoutの管理を個別実装せず、
`bluetooth.async_process_advertisements()`を使用する。

- matcher: 対象address、`connectable=True`
- scanning mode: `ACTIVE`
- predicate: 全manufacturer valueをparseし、対象`remote_id`と一致する完全な
  `type 0x0d`だけtrue
- timeout: 120秒

このAPIがcallback解除、active scan要求、timeout時のcleanupを担当する。

### 3.3 `light_id`

保存値は「wireへ送る6 bytes」をlowercase hexで保持し、全PairLink entryで再利用する。
これは照明側receiverを模擬するためのプロトコル上のidentityであり、PairLink電球の
検出・操作やHome Assistantの`light`エンティティを表すものではない。

候補の優先順位:

1. 既存PairLink entryの`light_id`
2. local/unicast bitを設定したランダム6 bytes

ランダムidentityは実機2台で受理済みである。scanner sourceやlocal adapterのMACから
導出せず、保存後はadapterやproxyが変わっても同じ値を使用する。

### 3.4 現在addressの追従

Config Entryのunique IDは`remote_id.hex()`とし、addressは接続先情報として扱う。
同じ`remote_id`を新しいaddressで再発見した場合は、既存entryのaddressを更新して
flowをabortする。Device RegistryのBluetooth connectionもentry reload時に追従させる。

これにより、現在は固定addressの実機を扱いつつ、将来のaddress randomizationにも
破綻しない構造にする。

### 3.5 Reauthとentry reload

Config Entry update listenerを1つだけ登録し、data/options更新時のreloadを担当させる。
reauth flowは`async_update_and_abort()`でcredentialを更新し、自身ではreloadしない。
Home Assistant 2026.6以降の二重reloadを避ける。

## 4. ファイル構成

```text
custom_components/pairlink/
├── __init__.py
├── manifest.json
├── const.py
├── config_flow.py
├── models.py
├── protocol.py
├── discovery.py
├── session.py
├── event.py
├── diagnostics.py
└── translations/
    ├── en.json
    └── ja.json
tests/
├── conftest.py
├── test_protocol.py
├── test_discovery.py
├── test_config_flow.py
├── test_init.py
├── test_session.py
├── test_event.py
└── test_diagnostics.py
.github/workflows/
├── test.yml
└── hassfest.yml
hacs.json
pyproject.toml
README.md
```

custom integrationなので`strings.json`は作らず、英語・日本語とも完成文面を
`translations/`へ直接置く。

`manifest.json`には少なくとも次を含める。

```text
domain:            pairlink
version:           0.1.0
integration_type:  device
config_flow:       true
dependencies:      [bluetooth_adapters]
iot_class:         local_push
requirements:      []
codeowners:        [@nunnun]
```

Bleakと`bleak-retry-connector`はHome Assistant Core側に含まれるため、custom
requirementとして重複指定しない。documentation / issue tracker URLはrepositoryの
公開先が決まった時点でmanifestと`hacs.json`へ設定する。

## 5. モジュール設計

### 5.1 `const.py`

次を一元管理する。

- `DOMAIN = "pairlink"`
- Config Entry data key
- FFD0/FFD1/FFD2 UUID
- timeout、将来設定用のdedup window、backoff
- event typeとcommand mapping
- supported platforms

credentialやpacket値は定数・logger messageへ含めない。

### 5.2 `models.py`

主な型:

```text
PairLinkConfigEntry = ConfigEntry[PairLinkSession]
PairLinkAdvertisement
PairLinkCredentials
RemoteEvent
SessionState
SessionDiagnostics
```

`PairLinkAdvertisement`と`RemoteEvent`はimmutable dataclassとする。
`SessionDiagnostics`だけmutableにしてcounterとtimestampを保持する。

### 5.3 `protocol.py`

Home AssistantやBleakをimportしないpure Python moduleとする。

公開API:

```text
PairLinkCodec(home_id, password)
PairLinkCodec.make_login()
PairLinkCodec.parse_login_response(packet)
PairLinkCodec.decrypt_data_event(packet, peer_crypto_vaddr)
PairLinkCodec.parse_remote_event(plaintext)
```

境界で長さ、category、channel、PKCS#7、credential一致を検証する。
例外messageにはkey、credential、packet bodyを埋め込まない。

### 5.4 `discovery.py`

公開API:

```text
parse_manufacturer_value(value) -> PairLinkAdvertisement | None
parse_service_info(service_info) -> list[PairLinkAdvertisement]
find_pairlink_advertisement(service_info, expected_type=None)
```

要件:

- `C0 FF`がvalue先頭でない場合も探索
- `type 0x05`は13 bytes以上かつHome ID / remote ID必須
- `type 0x0d`は`F0 FB`、remote ID、remote channelまで揃ったものだけsetup用途に採用
- passwordは4 ASCII bytesか検証
- malformed payloadは例外でflow/sessionを落とさず無視
- raw advertisementをログへ渡さない

### 5.5 `config_flow.py`

Discovery flow:

```text
async_step_bluetooth
  -> parse / connectable確認
  -> unique_id = remote_id.hex()
  -> 既存entryならaddress更新してabort
  -> async_step_bluetooth_confirm
  -> async_step_registration(progress)
  -> async_step_registration_done
  -> temporary LOGIN validation
  -> create entry
```

手動の`async_step_user`では、現在見えている未登録PairLinkスイッチを一覧表示する。
選択後はBluetooth discoveryと同じconfirmへ合流する。これにより自動発見通知を
見逃してもUIだけで追加できる。

progress taskは次を行う。

1. 対象`remote_id`の完全な`type 0x0d`を最大120秒待つ
2. `home_id`、password、remote channelをmemory上へ保持
3. `light_id`を既存entryから再利用するかtransport非依存に生成
4. 一時GATT接続でLOGIN responseまで検証
5. WELCOME / DEVICE_STATUSを送って切断
6. 成功時だけentry dataを組み立てる

失敗は`cannot_connect`、`invalid_auth`、`registration_timeout`へ分類し、credentialや
addressをerror textへ含めない。

Reauth flowは同じregistration taskとhandshake validatorを再利用し、
`remote_id`の一致を確認して既存entryだけを更新する。

### 5.6 `session.py`

`PairLinkSession`をentryごとに1つ作る。

状態:

```text
STOPPED -> RESOLVING -> CONNECTING -> AUTHENTICATING -> READY
                                      |                 |
                                      +---- BACKOFF <---+
```

実装上の要点:

- `async_ble_device_from_address(..., connectable=True)`で毎回最新`BLEDevice`を取得
- `BLEDevice`のbackend、`details`、scanner sourceを解釈しない
- `bleak-retry-connector`で接続し、接続ごとに新しいclientを作る
- FFD2だけをnotify購読し、FFD1へwrite-with-response
- notify callbackはpacketをbounded `asyncio.Queue`へ積むだけ
- queueと`peer_crypto_vaddr`は接続ごとに作り直す
- `LOGIN -> response -> WELCOME -> 0.5秒待機 -> DEVICE_STATUS`を順守
- READY後はcategory 6だけを復号し、remote ID一致を再確認
- command `0x01`/`0x02`だけsubscriberへ通知
- dedupは既定で無効（0秒）とし、連打を含む全eventをsubscriberへ通知
- 将来明示設定する場合に備え、semantic key単位のdeduplicatorはsession内に保持
- disconnect callbackは`asyncio.Event`をsetするだけ
- 外側loopが新しいclientで再接続
- backoffは`1, 2, 5, 10, 30, 60`秒、成功時reset
- unloadではloop cancel、notify解除、disconnectをawait

接続先が見つからなくてもentry setupは成功させ、backgroundで待機する。
connectable scannerが1つもない場合だけ`ConfigEntryNotReady`にする。

認証失敗の扱い:

- LOGIN responseを受け取ったが復号・credential一致に失敗: reauth開始
- GATT接続成功後のLOGIN response timeoutが3回連続: reauth開始
- 通常の接続失敗・一時切断: reauthせずbackoff

reauth開始後は接続loopを停止し、複数flowを起動しない。

### 5.7 `event.py`

`PairLinkButtonEvent(EventEntity)`を1 entryにつき1つ作る。

```text
unique_id:       <remote_id_hex>_button
device_class:    button
event_types:     ["on", "off"]
translation_key: button
available:       session.state == READY
```

session callbackはHome Assistant event loop上で次を行う。

1. commandを`on`/`off`へ変換
2. `_trigger_event()`を呼ぶ
3. `async_write_ha_state()`を呼ぶ

属性:

```json
{
  "channel": 1,
  "command": 1,
  "command_hex": "0x01",
  "extra": "00",
  "repeat_count": 1
}
```

entity追加時にevent/state callbackを登録し、削除時に必ず解除する。

### 5.8 `__init__.py`

setup:

1. Config Entry dataをbytesへ安全に変換
2. connectable scannerの存在確認
3. `PairLinkSession`を作成して`entry.runtime_data`へ格納
4. event platformをforward setup
5. session background taskを開始
6. entry update listenerを登録

unload:

1. sessionを停止
2. platformをunload
3. runtime参照を解放

entry削除時は保存addressをBluetooth managerへrediscover要求する。

### 5.9 `diagnostics.py`

出力はentry dataのredacted copyと、secretを含まないsession状態だけに限定する。

```json
{
  "entry": {
    "address": "**REDACTED**",
    "remote_id": "**REDACTED**",
    "home_id": "**REDACTED**",
    "password": "**REDACTED**",
    "light_id": "**REDACTED**",
    "remote_channel": 1
  },
  "session": {
    "state": "ready",
    "connected": true,
    "last_ready_at": "...",
    "last_event_at": "...",
    "connection_attempts": 2,
    "disconnect_count": 0,
    "decoded_event_count": 10,
    "duplicate_count": 24,
    "unknown_packet_count": 0
  }
}
```

testではdiagnostics全体をJSON化し、既知のpassword、Home ID、full address、
remote ID、light IDがどこにも現れないことを確認する。

## 6. 実装フェーズ

### Phase 0: 実機前提の確定

- random local/unicast `light_id`でLOGIN後のevent受信を確認済み
- FFD2だけのnotify購読で動作することを確認済み
- 2台同時接続とevent分離を確認済み
- scanner sourceやadapter MACに依存しない方針をtestと文書へ反映済み

### Phase 1: Scaffold / pure Python

- custom component scaffold、manifest、translation
- `protocol.py`と`discovery.py`
- pure Python unit test
- lint/type/test環境

完了条件: BluetoothやHome Assistantを起動せず全vector testが成功。

### Phase 2: Config Flow

- Bluetooth discoveryと手動一覧
- unique ID / address更新
- confirm / registration progress
- 一時handshake validation
- Config Entry作成
- reauth flowの骨格

完了条件: mock Bluetooth広告とmock clientだけで、成功、timeout、対象外広告、
重複、認証失敗を再現できる。

### Phase 3: Runtime / Event Entity

- `PairLinkSession`
- reconnect / backoff / lifecycle
- Event Entity / availability
- 2 entryの状態分離

完了条件: client mockを切断・再接続させ、新しいclientが作られ、A/Bのeventが混ざらない。

### Phase 4: Security / recovery / diagnostics

- reauth自動起動とcredential更新
- diagnostics redaction
- address更新とDevice Registry追従
- repeated error log抑制
- setup/unload failure pathのcleanup test

完了条件: secret漏えいtest、task/client残留test、reauth既存entry更新testが成功。

### Phase 5: 実機acceptance / 配布

- 2台同時接続
- 一方だけ電源断・復帰
- Home Assistant再起動
- 2時間連続接続
- HACS導入・削除手順
- release `0.1.0`

## 7. テスト方針

### Pure Python

- FIPS-197 AES-128 known answer
- key derivation
- LOGIN command/response
- ON/OFF decrypt + re-encrypt
- PKCS#7全異常系
- type `0x05` / `0x0d`
- company prefix差異
- truncated / malformed payload
- non-ASCII password

### Home Assistant

- manifest discoveryからconfirm
- non-connectable拒否
- same remote IDのflow/entry重複防止
- user flowの候補一覧
- 対象外remote IDの登録広告無視
- 120秒timeoutとretry
- handshake成功時だけentry作成
- transport非依存な`light_id`生成と既存entryからの再利用
- setup時offlineでもbackground待機
- adapterなしは`ConfigEntryNotReady`
- READY / unavailable遷移
- ON/OFF event attributes
- unknown command / source mismatchの抑制
- dedup既定無効と、明示window指定時のdedup
- disconnect後の新client
- unload時のtask、notify、client回収
- 2 sessionのcodec、queue、dedup分離
- reauthの単一起動と既存entry更新
- diagnostics / logのsecret非露出

### 実機

`HOME_ASSISTANT_INTEGRATION_DESIGN.md`のhardware acceptance 10項目を実施し、
各項目の時刻、HA version、BlueZ/Bluetooth proxy、結果を記録する。

## 8. 実装中に守る境界

- `protocol.py`と`discovery.py`はHome Assistant非依存に保つ
- repository外のlocal module、file、test dataを参照しない
- scanner、adapter選択、reachabilityはHA Bluetooth managerへ委譲する
- local adapterとconnectable Bluetooth proxyを同じ接続経路として扱う
- `hci0`、BlueZ D-Bus、backend固有の`BLEDevice.details`を参照しない
- Config Flowとruntimeでhandshake実装を複製しない
- entry間でclient、codec、queue、dedup、counterを共有しない
- credential、鍵、raw registration広告、復号plaintextをloggerへ渡さない
- entity propertyからI/Oしない
- background taskは必ずentry unloadから到達可能なcleanup ownerを持つ

## 9. 実装開始時の順序

最初の実装作業はPhase 1とする。具体的にはscaffold、`protocol.py`、
`discovery.py`、そのunit testまでを最初のレビュー単位にする。

この単位ではBLE接続やConfig Flowをまだ入れない。既存prototypeの確定ロジックを
Home Assistant非依存の形で固定し、その後の非同期処理の土台を先に安定させる。
