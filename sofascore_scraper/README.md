# Scraper SofaScore – 5 Liga Top Eropa (2018/19 – 2025/26)

Mengambil statistik semua pertandingan Premier League, La Liga, Serie A, Bundesliga,
dan Ligue 1 dari SofaScore untuk keperluan penelitian (dengan izin SofaScore).

## Cara kerjanya

Seperti scraping yang pernah kamu lakukan dulu, laptop akan **membuka browser sendiri**:

1. Chrome terbuka ke `https://www.sofascore.com/` seperti pengunjung biasa.
2. Kalau muncul pop-up cookie atau halaman verifikasi, selesaikan manual di jendela itu,
   lalu tekan **Enter** di terminal.
3. Script mengambil data dari dalam halaman itu (lewat `fetch()` ke `/api/v1/...`),
   sama seperti cara situs SofaScore memuat datanya sendiri.
4. Semua respons disimpan ke `data_sofascore/cache_json/`. Script aman dihentikan
   (Ctrl+C) dan dijalankan ulang; yang sudah terambil tidak diminta lagi.
5. Di akhir, CSV dibangun otomatis dari cache.

> Kenapa versi lama gagal? Versi lama membuka URL `api.sofascore.com/...` langsung di tab
> browser. SofaScore menolak permintaan seperti itu (403 / halaman verifikasi), karena
> situsnya sendiri tidak pernah memuat data dengan cara itu.

## 1. Persiapan (sekali saja)

1. Pasang **Python 3.10+** dari <https://www.python.org/downloads/>.
   Di Windows, centang **"Add python.exe to PATH"** saat instalasi.
2. Pasang **Google Chrome** (atau pakai Microsoft Edge bawaan Windows, lihat `--browser edge`).
3. Buka terminal (Windows: Command Prompt / PowerShell) di folder ini, lalu:

   ```bash
   pip install -r requirements.txt
   ```

   Driver browser (chromedriver) tidak perlu diunduh manual; Selenium mengurusnya
   otomatis saat pertama kali jalan (butuh internet).

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

## 3. Jalankan pengambilan data

Disarankan per liga dulu (lebih mudah dipantau):

```bash
python sofascore_scraper.py --leagues premier-league --contact emailkamu@student.uns.ac.id
python sofascore_scraper.py --leagues laliga --contact emailkamu@student.uns.ac.id
python sofascore_scraper.py --leagues serie-a --contact emailkamu@student.uns.ac.id
python sofascore_scraper.py --leagues bundesliga --contact emailkamu@student.uns.ac.id
python sofascore_scraper.py --leagues ligue-1 --contact emailkamu@student.uns.ac.id
```

Atau sekaligus semua liga dan musim:

```bash
python sofascore_scraper.py --contact emailkamu@student.uns.ac.id
```

Setelah semua liga terkumpul, bangun CSV gabungan 5 liga dari cache (tanpa internet):

```bash
python sofascore_scraper.py --build-only
```

**Perkiraan waktu:** ±14.000 laga × ±2,5 detik ≈ **10 jam** untuk semua liga
(±20 jam jika memakai `--with-odds`). Laptop jangan sampai *sleep*; kalau terputus,
cukup jalankan perintah yang sama lagi dan script akan melanjutkan dari cache.

Selama berjalan, **jangan tutup dan jangan pakai jendela browser yang dibuka script**.
Kalau jendelanya tertutup, script akan membukanya lagi.

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
| `--delay` | jeda antar request, detik (default 2) |
| `--with-odds` | ikut ambil odds 1X2 (request jadi 2×) |
| `--contact` | email kamu, dicantumkan di identitas request mode requests |
| `--out` | folder keluaran (default `data_sofascore`) |
| `--mode requests` | tanpa browser; hanya jalan jika IP kamu di-whitelist SofaScore |
| `--probe`, `--build-only`, `--cek-koneksi` | lihat di atas |

## Jika ada masalah

| Gejala | Solusi |
|---|---|
| `Gagal membuka browser ...` | Pastikan Chrome terpasang; tutup semua jendela Chrome yang dibuka script sebelumnya; atau coba `--browser edge`. |
| `This version of ChromeDriver only supports ...` | Update Chrome ke versi terbaru, lalu `pip install -U selenium`. |
| Muncul `Akses ditolak (status 403)` | Lihat jendela browser, selesaikan verifikasi manual, tekan Enter. |
| `[BERHENTI] 5 permintaan berturut-turut gagal` | Tunggu 15–30 menit lalu jalankan lagi, dan naikkan jeda (`--delay 4`). |
| `'python' is not recognized` | Python belum masuk PATH; instal ulang dan centang "Add python.exe to PATH". |

Karena kamu sudah punya izin resmi, opsi paling stabil adalah meminta SofaScore
**whitelist IP** atau akses API resmi; setelah itu pakai `--mode requests` (lebih cepat,
tanpa browser).
