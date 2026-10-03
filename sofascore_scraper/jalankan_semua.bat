@echo off
REM Ambil semua data untuk model UCL/UEL, berurutan dari yang terpenting. Boleh ditinggal.
REM Data yang sudah ada di cache tidak diambil ulang; aman dihentikan dan dijalankan lagi.
cd /d "%~dp0"
echo [1/5] UCL + UEL (fase utama) + odds
python sofascore_scraper.py --leagues eropa --with-odds --tanpa-kualifikasi
echo [2/5] Super Lig, Eredivisie, Liga Portugal + odds
python sofascore_scraper.py --leagues liga-tambahan --with-odds
echo [3/5] Odds 5 liga top (statistik sudah di cache, hanya odds yang diambil)
python sofascore_scraper.py --leagues top5 --with-odds
echo [4/5] Buang laga play-off domestik
python bersihkan_playoff.py
echo [5/5] Elo dari clubelo.com
python ambil_elo.py
echo.
echo Selesai. Hasil ada di folder data_sofascore. Log: data_sofascore\log_scraping.txt
pause
