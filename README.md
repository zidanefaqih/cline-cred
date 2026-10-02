# cline-cred — pasang akun Cline ke 9router / VansRouter

Satu script Python untuk memasukkan kredensial akun Cline (OAuth WorkOS) ke database
9router atau VansRouter. Tanpa browser, tanpa klik-klik, tanpa restart.

**Tidak ada dependency.** Semua pakai pustaka bawaan Python.

---

## UNTUK AGENT AI — mulai dari sini

Kalau kamu agent yang diminta memasang akun Cline untuk pengguna, jalankan urutan ini.
Jangan menebak; kalau langkah 1 gagal, ikuti bagian Troubleshooting.

```bash
# 1. Pastikan Python ada (3.8+). Di Windows pakai "python", bukan "python3".
python --version

# 2. Cari database aplikasinya. Path beda di tiap komputer, jadi jangan ditebak.
python cline_cred.py find-db
#    Outputnya memberi satu baris --db "...". Pakai itu di semua perintah berikutnya.
#    Kalau tidak ketemu, pakai --instance 9router (jalan bila container-nya bernama 9router).

# 3. Pasang akunnya. Ganti akun.json dengan berkas kredensial yang diberikan penjual.
python cline_cred.py --instance 9router import --file akun.json --verify

# 4. Buktikan berhasil. Ganti dengan email dari berkas kredensial.
python cline_cred.py --instance 9router verify --email nama@domain.com
```

**Selesai kalau langkah 4 mencetak `PASS`.**

Kalau langkah 2 mencetak error "unable to open database file" atau daftar kosong padahal
akun seharusnya ada → lanjut ke **bagian 2** (Cari lokasi database). Itu penyebab 90% kegagalan.

---

## 1. Kebutuhan

| | |
| --- | --- |
| Python | **3.8 atau lebih baru**. Cek: `python --version` |
| Aplikasi | 9router atau VansRouter, **harus sedang jalan** (untuk langkah verifikasi) |
| Docker | Opsional. Hanya diperlukan untuk mendeteksi lokasi database otomatis |
| OS | Windows, macOS, Linux — semuanya didukung |

Tidak perlu `pip install` apa pun.

**Cara menjalankan perintah:**

| OS | Perintah |
| --- | --- |
| Windows | `python cline_cred.py ...` |
| macOS / Linux | `python3 cline_cred.py ...` |

Sepanjang dokumen ini ditulis `python cline_cred.py`. Di macOS/Linux ganti `python` → `python3`.

---

## 2. Cari lokasi database — langkah paling sering gagal

Script perlu tahu di mana `data.sqlite` berada. **Path-nya berbeda di setiap komputer**, jadi
tidak ada default yang bisa diandalkan. Empat cara, coba berurutan:

### Cara A — biarkan script mencarinya (mulai dari sini)

```bash
python cline_cred.py find-db
```

Script akan memeriksa semua container Docker yang me-mount sesuatu di `/app/data`, lalu
menyisir folder home, dan melaporkan setiap `data.sqlite` yang punya tabel
`providerConnections`:

```
3 kandidat database:

  cline=2    total=42    /home/user/.vansrouter/db/data.sqlite
                        dari: container 'vansrouter' (/app/data)
  cline=0    total=13    /home/user/.9router/db/data.sqlite
                        dari: filesystem

Paling banyak akun Cline (2) → pakai ini:

  --db "/home/user/.vansrouter/db/data.sqlite"
```

Perintah ini **hanya membaca**, tidak menulis apa pun. Kalau database-mu tidak ketemu,
tambahkan lokasinya:

```bash
python cline_cred.py find-db --root "D:\aplikasi\9router"
```

### Cara B — otomatis lewat Docker

Kalau aplikasinya jalan di Docker dan container-nya bernama `9router` atau `vansrouter`,
path-nya sudah terdeteksi sendiri:

```bash
python cline_cred.py --instance 9router list
python cline_cred.py --instance vansrouter list
```

Cek nama container kalau ragu:

```bash
docker ps --format "{{.Names}}"
```

**Kalau nama container-nya bukan `9router`/`vansrouter`** (mis. `9router-app`, `myrouter`),
cara ini akan jatuh ke path default yang salah. Pakai cara A atau C.

### Cara C — tunjuk langsung dengan `--db` (paling pasti)

```bash
python cline_cred.py --db "C:\path\ke\data.sqlite" list
python cline_cred.py --db /path/ke/data.sqlite list
```

Kalau sudah tahu lokasinya, ini cara tercepat — dan yang paling tidak bisa salah.

### Cara D — cari sendiri

```bash
# Windows (PowerShell)
Get-ChildItem -Path C:\ -Filter data.sqlite -Recurse -ErrorAction SilentlyContinue

# Windows, kalau pakai Docker Desktop
docker inspect <nama-container> --format "{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}"

# macOS / Linux
find / -name data.sqlite -not -path "*/node_modules/*" 2>/dev/null
```

Yang dicari adalah `<data-dir>/db/data.sqlite` — file yang berada di dalam folder bernama `db`.

### Setelah ketemu, pakai terus

Tambahkan `--db` di **setiap** perintah dengan path yang sama:

```bash
python cline_cred.py --db "C:\Users\budi\9router\data\db\data.sqlite" import --file akun.json --verify
```

**Kalau `list` sudah menampilkan akun yang benar, sisanya pasti jalan.**

## 3. Pasang akunnya (import)

```bash
python cline_cred.py --instance 9router import --file akun.json --verify
```

Yang terjadi:

1. Script membaca `akun.json`, memvalidasi isinya.
2. **Backup otomatis** database ke `<data-dir>/db/backups/` sebelum menulis apa pun.
3. Menulis baris kredensial ke tabel `providerConnections`.
4. Kalau ada `--verify`, aplikasi mengetes sendiri ke server Cline dan melaporkan hasilnya.

**Tidak perlu restart.** Aplikasi membaca `providerConnections` dari SQLite tiap request,
jadi akunnya langsung muncul di dashboard dan langsung bisa dipakai routing.

### Aman dijalankan berulang

Kalau kamu menjalankan perintah yang sama dua kali, **tidak akan membuat duplikat**.
Kunci pencocokannya adalah `email`:

- email sudah ada → baris itu **diperbarui**
- email belum ada → baris **baru** ditambahkan

### Opsi lain untuk `import`

| Opsi | Fungsi |
| --- | --- |
| `--file akun.json` | Baca dari berkas (satu objek atau daftar objek) |
| `--verify` | Langsung tes ke upstream setelah menulis |
| `--dry-run` | Baca dan laporkan saja, tidak menulis apa pun |
| `--missing-only` | Lewati email yang sudah ada di DB |
| `--priority 5` | Tentukan prioritas akun secara manual |
| `--no-backup` | Lewati backup otomatis (tidak disarankan) |
| `--cpa` | Baca dari berkas `cline-*.json` milik CPA (butuh Docker) |

Contoh: cek dulu tanpa menulis.

```bash
python cline_cred.py --instance 9router import --file akun.json --dry-run
```

---

## 4. Verifikasi — jangan lewatkan

```bash
python cline_cred.py --instance 9router verify --email nama@domain.com
```

Hasil yang diharapkan:

```
PASS nama@domain.com    valid=true refreshed=False
```

Artinya: aplikasi berhasil memakai kredensial itu untuk memanggil server Cline.

- `valid=true` — kredensial hidup dan diterima upstream
- `refreshed=false` — access token masih segar, tidak perlu diperbarui
- `refreshed=true` — access token sudah basi dan **berhasil diperbarui** memakai refresh token.
  Ini juga hasil bagus; justru membuktikan refresh token-nya bekerja.

`--email` menerima **potongan** email, bukan hanya alamat penuh:

```bash
python cline_cred.py --instance 9router verify --email budi
```

**Verifikasi butuh aplikasinya sedang jalan**, karena script memanggil endpoint tes milik
aplikasi di `127.0.0.1`. Kalau aplikasi mati, hasilnya gagal koneksi.

---

## 5. Perintah lain

### Lihat isi database

```bash
python cline_cred.py --instance 9router list
```

```
DB: /data/db/data.sqlite
2 cline connection(s)

  [ 1] nama@domain.com
       authType=oauth active=1 test=active RT exp=2026-10-02T15:20:12.130Z (ok)
       id=00000000-1111-2222-3333-444444444444
```

- `RT` — punya refresh token (wajib ada; tanpa ini akun mati dalam 1 jam)
- `exp` — kedaluwarsa **access token**, bukan refresh token. Normal kalau sudah lewat.
- `active=1` — akun diaktifkan dan ikut dipilih saat routing
- `test=active` — status tes terakhir

### Cari database (lihat juga bagian 2)

```bash
python cline_cred.py find-db
python cline_cred.py find-db --root "D:\\aplikasi\\9router"
```

Hanya membaca. Melaporkan setiap `data.sqlite` yang punya tabel `providerConnections`,
beserta jumlah akun Cline di masing-masing, lalu menyarankan satu baris `--db` untuk dipakai.

### Ekspor kredensial

```bash
python cline_cred.py --instance 9router export
python cline_cred.py --instance 9router export --format cpa --out kirim.json
```

Format: `json` (dump penuh), `cpa` (ringkas, untuk dikirim ke orang lain), `env` (variabel shell).
Berkas ditulis dengan izin terbatas (0600) — **di Windows izin ini tidak berlaku**, lihat bagian 6.

### Hapus akun

```bash
python cline_cred.py --instance 9router delete --email nama@domain.com
```

Backup otomatis tetap dibuat sebelum menghapus.

---

## 6. Catatan khusus Windows

Scriptnya jalan di Windows, tapi ada empat hal yang beda:

**1. Perintahnya `python`, bukan `python3`.**

**2. Izin berkas tidak dilindungi.** Script menulis berkas ekspor dengan mode `0600`
(hanya pemilik bisa baca). Windows tidak memakai mode bit — permission diatur lewat ACL.
Tidak akan error, tapi **berkasnya tidak otomatis terlindungi**. Kalau menyimpan berkas
kredensial di Windows, pindahkan ke folder pribadi, dan jangan pernah taruh di folder yang
disinkronkan (OneDrive, Dropbox, Google Drive).

**3. Lokasi database biasanya di dalam Docker.** Kebanyakan pengguna Windows menjalankan
9router/VansRouter lewat Docker Desktop. Kalau begitu, cara A di bagian 2 akan bekerja —
selama container-nya bernama `9router` atau `vansrouter`.

**4. Path pakai tanda kutip.** Path Windows mengandung spasi dan backslash:

```bash
python cline_cred.py --db "C:\Users\budi\9router\data\db\data.sqlite" list
```

---

## 7. Troubleshooting

| Gejala | Sebab | Solusi |
| --- | --- | --- |
| `DB not found: <path>` | Lokasi DB salah, atau aplikasinya belum pernah jalan | Bagian 2. Pakai `--db` dengan path yang benar |
| `0 cline connection(s)` padahal harusnya ada | **Membaca DB aplikasi lain.** Path default menunjuk ke `<home>/.9router` — kalau di mesinmu ada lebih dari satu instalasi, ini akan membuka yang salah | Cek baris `DB:` di output. Pastikan menunjuk instalasi yang kamu pakai |
| `no such object: 9router` | Nama container beda, atau Docker mati | `docker ps --format "{{.Names}}"`, lalu pakai `--db` |
| `credential ... has no email and no JWT claim` | Berkas kredensial tanpa email, dan tokennya bukan JWT | Minta berkas kredensial yang benar dari pihak yang memberikannya |
| `FAIL ... connection refused` saat `verify` | Aplikasinya tidak jalan | Nyalakan 9router/VansRouter dulu |
| `FAIL ... 401` saat `verify` | Kredensial sudah mati / refresh token dicabut | Hubungi pihak yang memberi kredensial |
| `FAIL ... 404` saat `verify` | `--port` salah | Tambahkan `--port <port>` yang benar |
| Import sukses tapi akun tidak muncul di dashboard | Aplikasi membaca DB lain | Cek baris `DB:` — pastikan sama dengan instalasi yang kamu pakai |
| `no data.sqlite found` saat `find-db` | Database di luar folder home | Tambahkan `--root <dir>`, atau pakai `--db` dengan path yang sudah diketahui |
| `found data.sqlite file(s), but none has a providerConnections table` | Ketemu file `data.sqlite` lain yang bukan database 9router | Pakai `--db` untuk menunjuk yang benar |
| `SyntaxError` | Python terlalu tua | Butuh 3.8+. Cek `python --version` |

**Jalan keluar universal kalau bingung — dua perintah ini menyelesaikan hampir semua kasus:**

```bash
python cline_cred.py find-db                          # temukan database yang benar
python cline_cred.py --db "<hasil di atas>" list      # buktikan sudah menunjuk yang benar
```

Kalau `list` menampilkan akun Cline yang kamu harapkan, semua perintah lain pasti jalan.

---

## 8. Kenapa harus lewat script ini

Cline di 9router/VansRouter itu **OAuth-only**. Provider `cline` tidak mendeklarasikan
`authModes`, jadi:

- `POST /api/providers` dan `/api/providers/bulk` menolaknya dengan **400 `Invalid provider`**
- Tidak ada endpoint import token untuk Cline (yang ada hanya milik codex/cursor/kiro/gitlab/grok-cli/iflow)
- Satu-satunya jalur resmi adalah flow browser yang menempelkan *authorization code*

Ada celah di `POST /api/oauth/cline/exchange` yang menerima JWT mentah, **tapi** jalur itu
menyimpan `authType:"access_token"` **tanpa refresh token** — koneksinya mati saat access
token WorkOS habis, sekitar **1 jam** kemudian.

Script ini menulis baris yang sama persis dengan yang akan ditulis flow OAuth dashboard,
**termasuk refresh token**-nya, langsung ke `providerConnections`. Itu sebabnya akunnya
bertahan lama, bukan cuma sejam.

---

## 9. Format berkas kredensial

Berkas kredensial berbentuk JSON. Dua bentuk diterima:

**Bentuk ringkas (disarankan):**

```json
[
  {
    "type": "cline",
    "email": "nama@domain.com",
    "accessToken": "eyJhbGciOi...",
    "refreshToken": "HR0bHGRP...",
    "expiresAt": "2026-10-02T15:20:12.130Z",
    "providerSpecificData": { "firstName": "", "lastName": "" }
  }
]
```

**Bentuk dump database** (juga diterima, ada field tambahan seperti `id`, `priority`,
`isActive`, `testStatus` — field itu diabaikan saat import).

Bisa juga satu objek saja, tanpa tanda `[ ]`.

**Yang wajib ada:** `email` (atau token yang memuat klaim email di dalamnya) dan
`refreshToken`. Tanpa `refreshToken`, akunnya akan mati dalam 1 jam.

**Catatan:** field `name` di berkas **diabaikan**. Saat membuat baris baru, nama tampilan
selalu diisi dengan email. Kalau ingin nama tampilan berbeda, ubah setelah import:

```sql
UPDATE providerConnections SET name='Akun-01' WHERE email='nama@domain.com';
```

`name` hanya label di tampilan — tidak memengaruhi routing. Yang menentukan routing adalah
`priority`, `isActive`, dan status tes.

---

## 10. Skema database (referensi)

Terverifikasi pada VansRouter 0.91.33, schemaVersion 8.

```
providerConnections(
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, authType TEXT NOT NULL,
  name TEXT, email TEXT, priority INTEGER, isActive INTEGER DEFAULT 1,
  data TEXT NOT NULL,          -- JSON, semua yang bukan kolom
  createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL
)
```

Isi `data` untuk baris Cline OAuth:

```
accessToken   JWT (dengan atau tanpa prefix "workos:" — keduanya sah)
refreshToken  opaque, 25 karakter
expiresAt     kedaluwarsa ACCESS token, bukan refresh token
expiresIn     sisa detik
lastRefreshAt
testStatus    "active"
backoffLevel  0
providerSpecificData {firstName, lastName}
```

Soal prefix `workos:`: flow OAuth dashboard menyimpan JWT **mentah**, sedangkan import
lewat script menyimpan **berprefix**. Keduanya benar — aplikasi menormalkan saat memanggil,
di `open-sse/shared/clineAuth.js` (`getClineAccessToken()` menambahkan prefix kalau belum
ada). Jangan mengubah salah satunya.

---

## 11. Keamanan

- **Backup otomatis** ke `<data-dir>/db/backups/` sebelum setiap penulisan (kecuali `--no-backup`).
- **Tidak pernah mencetak nilai token**, kecuali kamu minta eksplisit dengan `--show-tokens`.
- **Dedup per email**, sama seperti aplikasinya — menjalankan ulang tidak menggandakan baris.

**Berkas kredensial itu setara kata sandi.** Siapa pun yang memegangnya bisa memakai akun
tersebut. Jangan taruh di repositori git, jangan kirim lewat kanal publik, dan jangan
biarkan nyangkut di folder sementara.
