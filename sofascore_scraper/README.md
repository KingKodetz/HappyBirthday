# Scraper SofaScore – 5 Liga Top Eropa (2018/19 – 2025/26)

Mengambil statistik semua pertandingan Premier League, La Liga, Serie A, Bundesliga,
dan Ligue 1 dari SofaScore untuk keperluan penelitian (dengan izin SofaScore).

## Cara kerjanya

Seperti scraping yang pernah kamu lakukan dulu, laptop akan **membuka browser sendiri**:

1. Script membuka **Chrome biasa** ke `https://www.sofascore.com/`, dengan profil
   tersendiri di `data_sofascore/profil_chrome/`.
2. Kalau muncul verifikasi *"I'm not a robot"* atau pop-up cookie, selesaikan manual di
   jendela itu sampai isi halaman (jadwal/skor) tampil normal, lalu tekan **Enter** di terminal.
3. Script terhubung ke tab itu lewat port debugging Chrome (port 9222) dan mengambil data
   dari dalam halaman (lewat `fetch()` ke `/api/v1/...`), sama seperti cara situs
   SofaScore memuat datanya sendiri.
4. Semua respons disimpan ke `data_sofascore/cache_json/`. Script aman dihentikan
   (Ctrl+C) dan dijalankan ulang; yang sudah terambil tidak diminta lagi.
5. Di akhir, CSV dibangun otomatis dari cache.

> Kenapa tidak memakai Selenium? Chrome yang dibuka Selenium ditandai sebagai browser
> otomatis (bar *"Chrome is being controlled by automated test software"*,
> `navigator.webdriver = true`). SofaScore membalasnya dengan `403 "challenge"`, dan
> verifikasinya tidak pernah lolos walaupun sudah dicentang. Chrome yang dibuka biasa
> tidak memiliki tanda itu.

## 1. Persiapan (sekali saja)

1. Pasang **Python 3.10+** dari <https://www.python.org/downloads/>.
   Di Windows, centang **"Add python.exe to PATH"** saat instalasi.
2. Pasang **Google Chrome** (atau pakai Microsoft Edge bawaan Windows, lihat `--browser edge`).
3. Buka terminal (Windows: Command Prompt / PowerShell) di folder ini, lalu:

   ```bash
   pip install -r requirements.txt
   ```

   Tidak perlu Selenium maupun chromedriver.

## 2. Cek koneksi & uji kecil (probe)

```bash
python sofascore_scraper.py --cek-koneksi
python sofascore_scraper.py --probe --leagues premier-league
```

- `--cek-koneksi` memeriksa DNS → koneksi → API. Kalau langkah **[2/3] gagal**, masalahnya
  ada di jaringan (misalnya WiFi kampus/antivirus); coba pakai hotspot HP.
  Kalau langkah [3/3] mendapat **403**, itu normal: API memang menolak klien non-browser,
  jadi pakai mode browser (default).
- `--probe` hanya mengirim beberapa request. Kalau muncul **`PROBE BERHASIL`** beserta
  contoh kolom statistik, lanjut ke langkah 3.

## 3. Jalankan pengambilan data (semua 5 liga, 2018/19 – 2025/26, sekali jalan)

Cukup **klik dua kali `jalankan_semua.bat`**, atau di terminal:

```bash
python sofascore_scraper.py
```

Tanpa `--leagues`/`--seasons`, script otomatis mengambil kelima liga dan semua musim,
lalu membangun CSV gabungan di akhir. **Boleh ditinggal tidur**:

- Kena challenge/403: halaman dimuat ulang, script istirahat bertahap (1, 2, 5, 10, 15,
  lalu 30 menit) dan mencoba lagi sendiri, tanpa menunggu Enter.
- Internet/WARP putus atau browser tertutup: script menunggu, membuka browser lagi, lalu lanjut.
- Error tak terduga: script dimulai ulang otomatis dari cache.
- Data yang sempat gagal dicoba lagi di putaran berikutnya secara otomatis.
- Laptop dicegah masuk *sleep* selama berjalan (layar boleh mati).
- Ritme pelan (±7 detik per request, istirahat 5 menit tiap 100 request) agar sesuai batas SofaScore.
- **Ritme adaptif:** tiap kena challenge, jeda otomatis diperlambat ×1,5 (maks. 30 detik);
  setelah 200 request lancar, pelan-pelan dipercepat lagi (tidak lebih cepat dari `--delay`).
  Ritme yang dipelajari disimpan di `data_sofascore/ritme.json` dan dipakai lagi pada run berikutnya.
- Di awal tiap musim tampil perkiraan waktu, mis. `perlu diambil: 380 laga, perkiraan ±1 jam 10 menit`.
- Setiap laga yang berhasil diambil tampil di layar, mis. `(51/380) Arsenal 2-0 Chelsea | 2018-08-11 14:00 | berhasil`.
- Semua kejadian dicatat di `data_sofascore/log_scraping.txt`.

**Perkiraan waktu:** ±14.000 laga ≈ **1,5–2 hari**. Kalau pagi belum selesai, jalankan
lagi perintah yang sama; script melanjutkan dari cache.

Sebelum ditinggal: **WARP tetap Connected**, laptop dicolok charger, dan jendela
Chrome yang dibuka script jangan ditutup (boleh di-*minimize*).

## 4. Hasil (folder `data_sofascore/`)

| File | Isi |
|---|---|
| `sofascore_top5_2018_2026_per_laga.csv` | 1 baris = 1 laga (`home_*` dan `away_*`) |
| `sofascore_top5_2018_2026_per_tim.csv` | 1 baris = 1 tim per laga (`*_for` / `*_against`, `result` W/D/L) |
| `kamus_statistik.csv` | arti setiap kolom statistik |
| `laporan_cakupan_statistik.csv` | % laga yang punya statistik tsb, per liga-musim |
| `laporan_jumlah_laga.csv` | jumlah laga per liga-musim |
| `kolom_lengkap_semua_liga.txt` | statistik yang lengkap 100% di semua liga-musim |
| `laga_dilewati.csv` | laga ditunda/dibatalkan/tanpa statistik |
| `debug_respons.txt` | cuplikan respons yang gagal (untuk diagnosis) |

CSV memakai pemisah `;` agar langsung rapi di Excel berbahasa Indonesia (ubah dengan `--sep ,`).

**Catatan penting:** kolom seperti `wonTacklePercent` berisi **jumlah** berhasil, bukan
persen. Pakai kolom `..._pct` untuk persentasenya.

## Semua opsi

| Opsi | Keterangan |
|---|---|
| `--leagues` | `premier-league laliga serie-a bundesliga ligue-1` (default semua) |
| `--seasons` | `18/19 … 25/26` (default semua) |
| `--browser chrome\|edge` | browser yang dipakai (default chrome) |
| `--browser-path` | lokasi `chrome.exe`/`msedge.exe` jika tidak di tempat standar |
| `--port` | port debugging browser (default 9222) |
| `--delay` | jeda rata-rata antar request, detik (default 7) |
| `--istirahat-tiap`, `--istirahat-menit` | istirahat berkala (default tiap 100 request, 5 menit) |
| `--manual` | perilaku lama: minta Enter saat challenge (tidak bisa ditinggal) |
| `--with-odds` | ikut ambil odds 1X2 (request jadi 2×) |
| `--contact` | email kamu, dicantumkan di identitas request mode requests |
| `--out` | folder keluaran (default `data_sofascore`) |
| `--mode requests` | tanpa browser; hanya jalan jika IP kamu di-whitelist SofaScore |
| `--probe`, `--build-only`, `--cek-koneksi` | lihat di atas |

## Jika ada masalah

### `--cek-koneksi` gagal di langkah [2/3] / IP dibelokkan

Contoh gejala: DNS laptop memberi `158.140.186.3`, padahal IP asli SofaScore ada di
jaringan Fastly (`151.101.x.52`), lalu koneksi port 443 *timeout*. Artinya DNS
jaringanmu **membelokkan** sofascore.com (pola blokir ISP), jadi permintaan tidak
pernah sampai ke SofaScore. Perbaikan di dalam script tidak akan membantu; yang perlu
diubah adalah jalur internetnya.

**Opsi A – Cloudflare WARP (paling mudah, gratis):**
1. Unduh aplikasi **1.1.1.1 / Cloudflare WARP** dari <https://one.one.one.one/>.
2. Pasang, buka, lalu aktifkan (**Connected**).
3. Jalankan `python sofascore_scraper.py --cek-koneksi` lagi. Langkah [2/3] harus
   `berhasil`, lalu lanjutkan dengan `--probe`.

**Opsi B – Ganti DNS Windows 11 ke 1.1.1.1 + DNS-over-HTTPS:**
1. *Settings → Network & internet → Wi-Fi* (atau *Ethernet*) → *Hardware properties*.
2. *DNS server assignment → Edit → Manual*, aktifkan **IPv4**.
3. *Preferred DNS*: `1.1.1.1`, *DNS over HTTPS*: **On (automatic template)**.
   *Alternate DNS*: `1.0.0.1`, *DNS over HTTPS*: **On**. Simpan.
4. Buka PowerShell: `ipconfig /flushdns`, lalu jalankan `--cek-koneksi` lagi.

Di Windows 10 (tanpa pilihan DNS over HTTPS), mengganti DNS saja sering tidak cukup
karena ISP bisa ikut menyadap DNS biasa, jadi pakai **Opsi A**. Kalau DNS sudah benar
(langkah [1/3] tidak lagi menampilkan PERHATIAN) tetapi langkah [2/3] masih *timeout*,
artinya pemblokiran dilakukan lebih dari sekadar DNS, jadi pakai **Opsi A**.

Cara cepat memastikan: buka `https://www.sofascore.com` di browser biasa. Kalau
tidak bisa dibuka atau muncul halaman blokir, masalahnya ada di jaringan, bukan di script.

### Masalah lain

| Gejala | Solusi |
|---|---|
| `Browser chrome tidak ditemukan` | Pasang Chrome, coba `--browser edge`, atau isi `--browser-path "C:\...\chrome.exe"`. |
| `port 9222 tidak bisa dihubungi` | Tutup semua jendela browser yang dibuka script sebelumnya, lalu jalankan lagi; atau pakai `--port 9333`. |
| Muncul `Akses ditolak (status 403)` / `"reason": "challenge"` | Di jendela browser, selesaikan verifikasi sampai isi halaman tampil normal, lalu tekan Enter. |
| Verifikasi sudah dicentang tapi isi sofascore.com tetap kosong | Kemungkinan IP WARP dicurigai. Di aplikasi Cloudflare, ubah **Mode** ke **DNS only** (1.1.1.1), jalankan `--cek-koneksi`; kalau [2/3] tetap `berhasil`, coba lagi. |
| `[BERHENTI] 5 permintaan berturut-turut gagal` | Tunggu 15–30 menit lalu jalankan lagi, dan naikkan jeda (`--delay 4`). |
| `'python' is not recognized` | Python belum masuk PATH; instal ulang dan centang "Add python.exe to PATH". |

Karena kamu sudah punya izin resmi, opsi paling stabil adalah meminta SofaScore
**whitelist IP** atau akses API resmi; setelah itu pakai `--mode requests` (lebih cepat,
tanpa browser).
