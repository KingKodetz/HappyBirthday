@echo off
REM Scrape SEMUA 5 liga, musim 2018/19 - 2025/26, lalu bangun CSV. Boleh ditinggal tidur.
cd /d "%~dp0"
python sofascore_scraper.py
echo.
echo Selesai. Hasil ada di folder data_sofascore. Log: data_sofascore\log_scraping.txt
pause
