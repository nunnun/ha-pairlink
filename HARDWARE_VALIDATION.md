# Hardware validation

## 2026-07-25–26

Validation host:

- Raspberry Pi at the development host supplied by the project owner
- Linux `6.18.34+rpt-rpi-v8`
- BlueZ `5.82`
- Python `3.14.2`
- Home Assistant test environment `2026.7.4`

No Production Home Assistant instance was accessed or modified.

Results:

| Check | Result |
|---|---|
| `connected-switch` discovery | 2 devices visible |
| Real type `0x05` advertisement parse and cached remote match | 2/2 passed |
| FFD2-only notification subscription | 2/2 passed as part of handshake |
| LOGIN response validation using the integration codec | 2/2 passed |
| WELCOME and DEVICE_STATUS write sequence | 2/2 passed |
| Random local/unicast `light_id` fallback | 2/2 passed |
| Two simultaneous authenticated connections | passed |
| Physical ON/OFF event reception | passed on both switches |
| Per-switch event isolation | passed |
| Rapid repeated button operations | passed; every packet must be emitted |
| 12-second semantic deduplication | unsuitable; disabled by default after this test |

Accepted event sequences during the manual test:

- switch 1: `off`, `on`, `off`, `on`
- switch 2: `on`, `off`, `on`, `off`, `on`, `off`

The switches remained separately identifiable throughout the simultaneous
connection test. The operator confirmed that the additional packets were
created by intentional rapid button presses, not protocol retransmission. The
test listener incorrectly suppressed 29 valid operations with its 12-second
window, so the integration default was changed to no deduplication.

The test commands emitted only switch indexes and pass/fail results. Credentials,
stable identifiers, raw registration advertisements, encrypted packets, and
plaintext packets were not logged.

## 2026-07-29

Validation host:

- Radxa ROCK 4C+ at the development host supplied by the project owner
- Linux `6.1.115-8-rk2501`
- BlueZ `5.66`
- Python `3.14.6`
- Home Assistant test environment `2026.7.4`

No Production Home Assistant instance was accessed or modified.

Results:

| Check | Result |
|---|---|
| Two registered switches after a full power cycle | both reached `READY` |
| Two registered switches after Home Assistant restart | both reached `READY` |
| Home Assistant service shutdown and startup | completed in about 8.5 seconds |
| Recovery after PairLink setup on the service restart | about 28–31 seconds |
| Concurrent established GATT connections | 2/2 connected |
| Serialized GATT connection and LOGIN setup | passed |
| Connection-slot errors after serialization | none observed |
| Kernel errors after returning to standard BlueZ mode | none observed |

The adapter received the switches at approximately `-87` to `-103 dBm`, so the
full power-cycle test needed multiple retries and recovered more slowly than the
service-only restart.

BlueZ experimental mode reported passive-scanning support, but its Advertisement
Monitor removal path triggered a kernel Oops in
`mgmt_remove_adv_monitor_complete`. The test host was returned to standard BlueZ
mode with active scanning. Passive mode must not be enabled on this host. A full
power cycle was required to recover the kernel after the Oops.
