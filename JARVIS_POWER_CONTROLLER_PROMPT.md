# JARVIS Prompt - ESP32 Power Controller

## Rekomendasi arsitektur

Untuk device ini, jalur paling mudah adalah **direct local action**, bukan `ask codex`.

Alasannya:
- kontrol target voltage ini deterministik
- API device sudah sederhana
- JARVIS sudah punya pola serupa untuk `FAN` dan `WIZ`
- respons yang dibutuhkan pendek dan real-time

Jadi pola yang direkomendasikan:
- user bicara ke JARVIS
- JARVIS mengubah intent menjadi action tag
- router JARVIS mengeksekusi HTTP ke ESP32
- JARVIS membacakan hasil singkat

## Action tag yang disarankan

Gunakan action baru:

```text
[ACTION:POWER_CTRL] perintah user
```

Contoh:

```text
[ACTION:POWER_CTRL] set 12 volt
[ACTION:POWER_CTRL] status power controller
[ACTION:POWER_CTRL] naikkan ke 9.5 volt
[ACTION:POWER_CTRL] turunkan ke 3 volt
[ACTION:POWER_CTRL] cek target vs output
```

## Prompt sistem untuk JARVIS

Tempel ini ke prompt/behavior JARVIS:

```text
You CAN control the local ESP32 Power Controller directly over the LAN.
Use [ACTION:POWER_CTRL] for any request about target voltage, output voltage, power controller status, set voltage, raise voltage, lower voltage, or comparing target vs output.

Power Controller rules:
- Device name: ESP32 Power Controller
- Base URL default: http://192.168.1.35
- Always prefer direct LAN HTTP control, not Codex, for normal target/output operations.
- For a status request, check the device first before answering.
- For a set request, send the new target voltage to the device and confirm the result briefly.
- Use the live output reading from the device as the source of truth, not assumptions.
- Keep spoken confirmations short.
- If the request is ambiguous, ask one short clarification.
- If the device is unreachable, say so honestly.

Examples:
- "set power controller ke 12 volt" -> [ACTION:POWER_CTRL] set 12 volt
- "berapa target dan output sekarang" -> [ACTION:POWER_CTRL] status target vs output
- "naikkan ke 9 volt" -> [ACTION:POWER_CTRL] set 9 volt
- "turunkan 1 volt dari sekarang" -> [ACTION:POWER_CTRL] lower 1 volt
- "cek power controller" -> [ACTION:POWER_CTRL] status
```

## Kontrak komunikasi yang disarankan

### 1. Baca status

Request:

```http
GET http://192.168.1.35/status
```

Field penting dari respons:
- `targetVoltage`
- `adcActualVoltage`
- `controlLocked`
- `controlState`
- `controlMessage`
- `actuatorBusy`
- `targetMinVoltage`
- `targetMaxVoltage`

### 2. Set target voltage

Request:

```http
POST http://192.168.1.35/set
Content-Type: application/x-www-form-urlencoded

voltage=12.0
```

Field hasil penting:
- `ok`
- `message`
- `targetVoltage`

## Perilaku action yang disarankan

### `status`

Langkah:
1. `GET /status`
2. baca `targetVoltage` dan `adcActualVoltage`
3. balas singkat

Contoh jawaban:
- `Target sekarang 12.0 volt, output terbaca 11.92 volt, status locked.`

### `set X volt`

Langkah:
1. clamp dulu ke range device
2. `POST /set`
3. opsional: `GET /status` sekali lagi untuk baca output live
4. balas singkat

Contoh jawaban:
- `Target saya set ke 12 volt, output sekarang 11.8 volt dan masih tracking.`

### `naikkan` / `turunkan`

Logika:
1. `GET /status`
2. ambil `targetVoltage`
3. hitung target baru
4. `POST /set`
5. balas singkat

## Parsing intent yang disarankan

JARVIS sebaiknya mengenali frasa seperti:
- `power controller`
- `target voltage`
- `output voltage`
- `target vs output`
- `set voltage`
- `set target`
- `naikkan volt`
- `turunkan volt`
- `set ke 12 volt`
- `berapa output sekarang`

## Format command internal yang rapi

Kalau mau dibuat lebih stabil di backend JARVIS, action `POWER_CTRL` bisa diterjemahkan menjadi objek internal seperti ini:

```json
{
  "action": "power_ctrl",
  "operation": "set",
  "value": 12.0,
  "unit": "volt"
}
```

Contoh lain:

```json
{
  "action": "power_ctrl",
  "operation": "status"
}
```

```json
{
  "action": "power_ctrl",
  "operation": "delta",
  "delta": -1.0,
  "unit": "volt"
}
```

## Rekomendasi implementasi di JARVIS

Pola paling cocok adalah meniru integrasi `FAN`:
- parsing intent di `server.py`
- extractor dan executor di `actions.py`
- direct HTTP pakai `httpx`
- confirmation pendek untuk TTS

Nama env yang saya sarankan:

```text
POWER_CTRL_BASE_URL=http://192.168.1.35
POWER_CTRL_TIMEOUT=5
```

## Contoh respons pendek untuk TTS

- `Power controller online, sir.`
- `Target set ke 10 volt.`
- `Output sekarang 9.86 volt.`
- `Masih tracking ke target.`
- `Device power controller tidak merespons.`

## Catatan penting

- Untuk operasi normal target/output, **jangan lempar ke Codex**.
- Codex baru dipakai kalau user minta ubah firmware, debug kalibrasi, atau modifikasi project.
- Untuk otomasi cepat JARVIS, direct HTTP action akan jauh lebih ringan dan stabil.
