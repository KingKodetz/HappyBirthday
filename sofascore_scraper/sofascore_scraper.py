"""
Scraper statistik pertandingan SofaScore - 5 Liga Top Eropa (2018/19 - 2025/26)
Untuk keperluan penelitian, dengan izin dari SofaScore.

Cara kerja mode browser (default):
  1. Script membuka Chrome/Edge BIASA (bukan Chrome "automated test software" milik
     Selenium) ke https://www.sofascore.com/ dengan profil tersendiri. Verifikasi
     "I'm not a robot" / pop-up cookie diselesaikan manual oleh Anda di jendela itu.
  2. Script terhubung ke tab tersebut lewat port debugging Chrome (DevTools Protocol)
     dan meminta data dengan fetch() dari DALAM halaman ke /api/v1/..., sama seperti
     cara situs SofaScore memuat datanya sendiri.
  3. Setiap respons disimpan ke cache, jadi script aman dihentikan (Ctrl+C) dan
     dilanjutkan kapan saja tanpa mengulang request yang sudah berhasil.

Contoh:
  python sofascore_scraper.py --probe --leagues premier-league
  python sofascore_scraper.py --leagues premier-league --seasons 24/25
  python sofascore_scraper.py                     # semua liga & semua musim
  python sofascore_scraper.py --build-only        # bangun CSV dari cache saja
"""

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

BERANDA = "https://www.sofascore.com/"
API = "https://www.sofascore.com/api/v1"

# slug -> (unique tournament id SofaScore, nama liga)
LEAGUES = {
    "premier-league": (17, "Premier League"),
    "laliga": (8, "La Liga"),
    "serie-a": (23, "Serie A"),
    "bundesliga": (35, "Bundesliga"),
    "ligue-1": (34, "Ligue 1"),
    "super-lig": (52, "Super Lig"),
    "eredivisie": (37, "Eredivisie"),
    "liga-portugal": (238, "Liga Portugal"),
    "ucl": (7, "UEFA Champions League"),
    "uel": (679, "UEFA Europa League"),
}

# Singkatan untuk --leagues
GRUP_LIGA = {
    "top5": ["premier-league", "laliga", "serie-a", "bundesliga", "ligue-1"],
    "liga-tambahan": ["super-lig", "eredivisie", "liga-portugal"],
    "eropa": ["ucl", "uel"],
}
KOMPETISI_EROPA = {"UEFA Champions League", "UEFA Europa League"}

SEASONS = ["18/19", "19/20", "20/21", "21/22", "22/23", "23/24", "24/25", "25/26"]

# Kolom per-sisi yang BUKAN statistik (dipakai untuk memisahkan meta vs statistik)
META_SISI = {"team_id", "team", "goals", "goals_ht", "goals_90", "pens"}

# Dijalankan di dalam tab sofascore.com: minta data API dengan cookie browser itu sendiri.
# %s diganti URL tujuan (dalam format JSON).
SKRIP_FETCH = """(async (url) => {
  const ctrl = new AbortController();
  const batas = setTimeout(() => ctrl.abort(), 45000);
  try {
    const r = await fetch(url, {credentials: "include", headers: {"Accept": "application/json"},
                                signal: ctrl.signal});
    return [r.status, await r.text()];
  } catch (e) {
    return ["koneksi: " + e, ""];
  } finally {
    clearTimeout(batas);
  }
})(%s)"""


class Terblokir(Exception):
    pass


def cari_browser(nama: str, path_manual: str = ""):
    """Cari lokasi chrome.exe / msedge.exe di tempat instalasi yang umum."""
    if path_manual:
        return path_manual if Path(path_manual).exists() else None
    relatif = {"chrome": ["Google/Chrome/Application/chrome.exe"],
               "edge": ["Microsoft/Edge/Application/msedge.exe"]}[nama]
    kandidat = [Path(os.environ[v]) / r
                for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA") if os.environ.get(v)
                for r in relatif]
    kandidat += {"chrome": [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")],
                 "edge": [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]}[nama]
    perintah = {"chrome": ["chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
                "edge": ["msedge", "microsoft-edge", "microsoft-edge-stable"]}[nama]
    kandidat += [Path(p) for p in map(shutil.which, perintah) if p]
    return next((str(k) for k in kandidat if k.exists()), None)


def identitas_ua(contact: str = "") -> str:
    ua = "research-scraper/1.0 (skripsi"
    return ua + (f"; kontak: {contact})" if contact else ")")


def interaktif() -> bool:
    return bool(sys.stdin) and sys.stdin.isatty()


def tunggu_pengguna(pesan: str, detik_cadangan: int = 15):
    """Tunggu Enter dari pengguna; jika tidak dijalankan di terminal, tunggu sebentar saja."""
    if interaktif():
        input(f"  >> {pesan} Tekan Enter untuk lanjut... ")
    else:
        print(f"  >> {pesan} (menunggu {detik_cadangan} detik)")
        time.sleep(detik_cadangan)


# Jeda (detik) saat terkena challenge/403: naik bertahap, angka terakhir diulang terus.
JEDA_BLOKIR = [60, 120, 300, 600, 900, 1800]
# Jeda (detik) saat koneksi/browser bermasalah untuk satu URL; setelah habis, URL dilewati
# (akan dicoba lagi otomatis di putaran berikutnya).
JEDA_GAGAL = [30, 60, 120, 300, 600, 900]

LOG_PATH = None  # diisi di main(): data_sofascore/log_scraping.txt


def catat(pesan: str):
    """Cetak pesan dengan jam, dan simpan juga ke file log."""
    baris = f"[{datetime.now():%H:%M:%S}] {pesan}"
    print(baris, flush=True)
    if LOG_PATH is not None:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d} {baris}\n")
        except OSError:
            pass


def tidur(detik: float, alasan: str = ""):
    """Tidur panjang dengan keterangan kapan lanjut."""
    if detik >= 60:
        lanjut = datetime.fromtimestamp(time.time() + detik)
        catat(f"   ...{alasan} istirahat {detik / 60:.0f} menit, lanjut sekitar pukul {lanjut:%H:%M}")
    time.sleep(detik)


def bunyi():
    """Bunyi singkat (Windows) supaya terdengar jika Anda ada di dekat laptop."""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def cegah_sleep(aktif: bool):
    """Cegah Windows masuk sleep selama scraping (layar boleh mati)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if aktif else 0))
    except Exception:
        pass


def ekstrak_json(teks: str):
    """Ambil objek JSON dari teks halaman, walau ada teks tambahan di sekitarnya."""
    if not teks:
        return None
    try:
        return json.loads(teks)
    except ValueError:
        pass
    awal, akhir = teks.find("{"), teks.rfind("}")
    if 0 <= awal < akhir:
        try:
            return json.loads(teks[awal:akhir + 1])
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# Pengambil data (dengan cache, retry, dan penghentian saat terblokir)
# --------------------------------------------------------------------------
class Fetcher:
    def __init__(self, cache_dir: Path, mode="browser", delay=2.0, backoff=30.0,
                 max_retries=3, max_gagal_beruntun=5, contact="", headless=False,
                 allow_network=True, browser="chrome", browser_path="", port=9222,
                 otomatis=False, istirahat_tiap=300, istirahat_menit=3.0):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.delay = delay            # jeda saat ini (menyesuaikan otomatis, lihat _sesuaikan_ritme)
        self.delay_min = delay        # tidak akan lebih cepat dari ini
        self.sukses_beruntun = 0
        self.backoff = backoff
        self.max_retries = max_retries
        self.max_gagal_beruntun = max_gagal_beruntun
        self.contact = contact
        self.headless = headless
        self.allow_network = allow_network
        self.browser = browser
        self.browser_path = browser_path
        self.port = port
        self.otomatis = otomatis              # True: tidak pernah menunggu Enter / berhenti sendiri
        self.istirahat_tiap = istirahat_tiap  # istirahat tiap N request ke server
        self.istirahat_menit = istirahat_menit
        self.gagal_beruntun = 0
        self.jumlah_request = 0
        self._session = None
        self._proses = None   # proses browser yang dibuka script ini
        self._ws = None       # koneksi DevTools ke tab sofascore.com
        self._id_pesan = 0

    def _path_cache(self, path: str) -> Path:
        return self.cache_dir / (path.strip("/").replace("/", "__") + ".json")

    def get(self, path: str):
        cp = self._path_cache(path)
        if cp.exists():
            data = json.loads(cp.read_text(encoding="utf-8"))
            return None if isinstance(data, dict) and data.get("_tidak_ada") else data
        if not self.allow_network:
            return None

        url = API + path
        percobaan = ronde_blokir = ronde_gagal = 0
        while True:
            status, data = self._ambil(url)
            self._jeda_setelah_request()

            if status == 200 and data is not None:
                cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.gagal_beruntun = 0
                self._sesuaikan_ritme(kena_blokir=False)
                if ronde_blokir or ronde_gagal:
                    catat("   -> berhasil lagi, lanjut.")
                return data

            if status == 404:  # memang tidak ada (mis. laga tanpa statistik)
                cp.write_text(json.dumps({"_tidak_ada": True}), encoding="utf-8")
                self.gagal_beruntun = 0
                return None

            if status in (403, 429, "verifikasi"):
                ronde_blokir += 1
                if self.otomatis:
                    self._tunggu_blokir(path, status, ronde_blokir)
                    continue
                if self.mode == "browser" and interaktif() and ronde_blokir <= self.max_retries:
                    print(f"\n  >> Akses ditolak (status {status}) untuk {path}.")
                    self._buka_beranda()
                    tunggu_pengguna("Halaman sofascore.com dimuat ulang. Jika ada verifikasi "
                                    "\"I'm not a robot\", selesaikan MANUAL sampai isi halaman "
                                    "(jadwal/skor) tampil normal.")
                    continue

            percobaan += 1
            print(f"  [!] {path} -> status {status} (percobaan {percobaan}/{self.max_retries})")
            if percobaan < self.max_retries:
                time.sleep(self.backoff * percobaan)
                continue

            if self.otomatis and ronde_gagal < len(JEDA_GAGAL):
                # Koneksi/browser bermasalah (mis. WARP putus): tunggu lalu ulangi URL yang sama.
                catat(f"[!] {path} gagal terus (status {status}). Cek WARP/internet; "
                      "script menunggu lalu mencoba lagi.")
                self.tutup()  # browser dibuka ulang dengan bersih pada percobaan berikutnya
                tidur(JEDA_GAGAL[ronde_gagal], "koneksi bermasalah,")
                ronde_gagal += 1
                percobaan = 0
                continue
            break

        self.gagal_beruntun += 1
        if self.otomatis:
            catat(f"[!] {path} dilewati dulu; akan dicoba lagi di putaran berikutnya.")
            return None
        if self.gagal_beruntun >= self.max_gagal_beruntun:
            raise Terblokir(
                f"{self.gagal_beruntun} permintaan berturut-turut gagal. Kemungkinan akses "
                "diblokir. Script dihentikan agar tidak membebani server. Tunggu beberapa "
                "waktu, atau hubungi SofaScore dengan bukti izin Anda untuk akses resmi."
            )
        return None

    def _jeda_setelah_request(self):
        """Jeda acak antar request + istirahat berkala, agar tidak memicu challenge."""
        self.jumlah_request += 1
        time.sleep(self.delay * random.uniform(0.7, 1.5))
        if self.istirahat_tiap and self.jumlah_request % self.istirahat_tiap == 0:
            tidur(self.istirahat_menit * 60, f"sudah {self.jumlah_request} request,")

    def _sesuaikan_ritme(self, kena_blokir: bool):
        """Ritme adaptif: melambat saat kena challenge, pelan-pelan cepat lagi saat lancar."""
        if kena_blokir:
            self.sukses_beruntun = 0
            lama, self.delay = self.delay, min(self.delay * 1.5, 30.0)
            if self.delay != lama:
                catat(f"   ritme diperlambat: {lama:.1f} -> {self.delay:.1f} detik per request")
            return
        self.sukses_beruntun += 1
        if self.sukses_beruntun >= 200 and self.delay > self.delay_min:
            self.sukses_beruntun = 0
            lama, self.delay = self.delay, max(self.delay_min, self.delay * 0.9)
            catat(f"   lancar 200 request, ritme dipercepat: {lama:.1f} -> {self.delay:.1f} detik")

    def _tunggu_blokir(self, path, status, ronde):
        """Saat kena challenge: muat ulang halaman, istirahat bertahap, lalu coba lagi."""
        detik = JEDA_BLOKIR[min(ronde, len(JEDA_BLOKIR)) - 1]
        catat(f"[BLOKIR] {path} -> status {status} (ke-{ronde}). Tidak perlu apa-apa; script akan "
              "mencoba lagi sendiri. Jika Anda di dekat laptop dan ada \"I'm not a robot\" di "
              "browser, boleh diselesaikan.")
        bunyi()
        if self.mode == "browser":
            if ronde % 3 == 0:  # sesekali mulai dengan browser yang benar-benar baru
                self.tutup()
            else:
                self._buka_beranda()
        self._sesuaikan_ritme(kena_blokir=True)
        tidur(detik, "menunggu challenge reda,")

    def _ambil(self, url):
        return self._ambil_browser(url) if self.mode == "browser" else self._ambil_requests(url)

    # ---- mode requests (butuh akses resmi / IP di-whitelist SofaScore) ----
    def _ambil_requests(self, url):
        if self._session is None:
            import requests
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": identitas_ua(self.contact),
                                          "Accept": "application/json",
                                          "Referer": BERANDA})
        try:
            r = self._session.get(url, timeout=30)
        except Exception as e:  # koneksi putus, timeout, dll.
            return (f"koneksi: {type(e).__name__}", None)
        if r.status_code == 200:
            data = ekstrak_json(r.text)
            if data is not None:
                return (200, data)
            self._catat_debug(url, "bukan-json", r.text)
            return ("bukan-json", None)
        if r.status_code != 404:
            self._catat_debug(url, r.status_code, r.text)
        return (r.status_code, None)

    # ---- mode browser (default): browser BIASA + DevTools Protocol ----
    def _http_lokal(self, jalur, metode="GET"):
        """Akses endpoint DevTools browser di 127.0.0.1 (tanpa proxy sistem)."""
        import requests
        s = requests.Session()
        s.trust_env = False
        r = s.request(metode, f"http://127.0.0.1:{self.port}{jalur}", timeout=5)
        r.raise_for_status()
        return r.json()

    def _devtools_hidup(self) -> bool:
        try:
            self._http_lokal("/json/version")
            return True
        except Exception:
            return False

    def _sambung_ws(self, alamat):
        import websocket
        return websocket.create_connection(alamat, timeout=60, suppress_origin=True,
                                           http_no_proxy=["127.0.0.1", "localhost"])

    def _mulai_browser(self):
        if self._devtools_hidup():
            print(f"Memakai jendela browser yang sudah terbuka (port {self.port}).")
        else:
            exe = cari_browser(self.browser, self.browser_path)
            if not exe:
                raise SystemExit(
                    f"\nBrowser {self.browser} tidak ditemukan. Pasang browsernya, coba --browser edge,\n"
                    "atau isi --browser-path dengan lokasi chrome.exe / msedge.exe.")
            # Profil tersendiri: cookie & hasil verifikasi tersimpan untuk run berikutnya.
            profil = (self.cache_dir.parent / f"profil_{self.browser}").resolve()
            argumen = [exe, f"--remote-debugging-port={self.port}", f"--user-data-dir={profil}",
                       "--no-first-run", "--no-default-browser-check", "--window-size=1200,900"]
            if self.headless:
                argumen.append("--headless=new")
            argumen.append(BERANDA)
            print(f"Membuka {self.browser} biasa (profil: {profil})")
            # Grup proses terpisah agar Ctrl+C di terminal tidak ikut mematikan browser.
            bendera = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            self._proses = subprocess.Popen(argumen, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=bendera)
            for _ in range(60):
                if self._devtools_hidup() or self._proses.poll() is not None:
                    break
                time.sleep(0.5)
            if not self._devtools_hidup():
                self.tutup()
                # Bukan SystemExit: di mode otomatis ini dianggap gangguan sementara dan dicoba lagi.
                raise RuntimeError(
                    f"port {self.port} tidak bisa dihubungi. Jika terus terjadi, tutup semua jendela "
                    "browser yang dibuka script sebelumnya (atau pakai --port 9333).")
            time.sleep(3)
        self._sambung_tab()
        if self.otomatis:
            catat("Browser siap. Jangan tutup jendelanya (boleh di-minimize).")
            time.sleep(8)
        else:
            tunggu_pengguna("Lihat jendela browser. Jika ada verifikasi \"I'm not a robot\" / "
                            "pop-up cookie, selesaikan dulu sampai isi sofascore.com (jadwal/skor) "
                            "tampil normal.")

    def _sambung_tab(self):
        host = urlparse(BERANDA).netloc
        tab = next((t for t in self._http_lokal("/json/list")
                    if t.get("type") == "page" and host in t.get("url", "")), None)
        if tab is None:  # belum ada tab sofascore.com -> buka tab baru
            tab = self._http_lokal(f"/json/new?{BERANDA}", "PUT")
            time.sleep(5)
        self._ws = self._sambung_ws(tab["webSocketDebuggerUrl"])

    def _perintah(self, metode, **params):
        """Kirim satu perintah DevTools ke tab dan tunggu balasannya."""
        self._id_pesan += 1
        self._ws.send(json.dumps({"id": self._id_pesan, "method": metode, "params": params}))
        while True:
            pesan = json.loads(self._ws.recv())
            if pesan.get("id") == self._id_pesan:
                break
        if "error" in pesan:
            raise RuntimeError(pesan["error"].get("message"))
        return pesan.get("result", {})

    def _jalankan_js(self, ekspresi):
        hasil = self._perintah("Runtime.evaluate", expression=ekspresi,
                               awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in hasil:
            raise RuntimeError(hasil["exceptionDetails"].get("text", "error JavaScript"))
        return hasil.get("result", {}).get("value")

    def _buka_beranda(self):
        if self._ws is None:
            return
        try:
            self._perintah("Page.navigate", url=BERANDA)
        except Exception as e:
            print(f"  [!] gagal membuka {BERANDA}: {type(e).__name__}")
        time.sleep(5)  # beri waktu halaman selesai dimuat

    def _ambil_browser(self, url):
        try:
            if self._ws is None:
                self._mulai_browser()
            # fetch() harus dijalankan dari halaman sofascore.com (satu origin dengan API)
            asal = urlparse(BERANDA)
            if self._jalankan_js("location.origin") != f"{asal.scheme}://{asal.netloc}":
                self._buka_beranda()
            status, teks = self._jalankan_js(SKRIP_FETCH % json.dumps(url))
        except Exception as e:
            print(f"  [!] koneksi ke browser terputus ({type(e).__name__}); akan disambung ulang.")
            self._putus_ws()
            return (f"browser: {type(e).__name__}", None)

        if status == 200:
            data = ekstrak_json(teks)
            if data is not None:
                return (200, data)
            self._catat_debug(url, "bukan-json", teks)
            return ("bukan-json", None)
        if status != 404:
            self._catat_debug(url, status, teks)
        return (status, None)

    def _catat_debug(self, url, status, teks):
        """Simpan cuplikan respons gagal ke debug_respons.txt agar penyebabnya terlihat."""
        potongan = " ".join(str(teks).split())[:400]
        with (self.cache_dir.parent / "debug_respons.txt").open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] status={status} url={url}\n  {potongan}\n")
        print(f"      cuplikan respons: {potongan[:150]!r}")

    def _putus_ws(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def tutup(self):
        """Tutup koneksi; browser ditutup rapi hanya jika dibuka oleh script ini."""
        self._putus_ws()
        if self._proses is None:
            return
        if self._proses.poll() is None:
            try:  # tutup rapi agar cookie hasil verifikasi tersimpan di profil
                ws = self._sambung_ws(self._http_lokal("/json/version")["webSocketDebuggerUrl"])
                ws.send(json.dumps({"id": 1, "method": "Browser.close"}))
                ws.close()
                self._proses.wait(timeout=10)
            except Exception:
                self._proses.terminate()
        self._proses = None


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def slug_kunci(nama: str) -> str:
    """'Ball possession' -> 'ballPossession' (cadangan jika field 'key' tidak ada)."""
    s = re.sub(r"[^0-9a-zA-Z]+", " ", nama).title().replace(" ", "")
    return s[:1].lower() + s[1:]


def angka(v):
    """Ambil angka pertama dari teks tampilan, mis. '55%' -> 55, '400/500 (80%)' -> 400."""
    if v is None or isinstance(v, (int, float)):
        return v
    m = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def parse_statistik(js, kamus=None) -> dict:
    """Statistik periode 'ALL' (satu laga penuh) -> {'home_<key>': v, 'away_<key>': v, ...}

    PENTING (terverifikasi dari respons asli): untuk item berpasangan seperti
    'Tackles won' (key: wonTacklePercent) atau 'Ground duels' (groundDuelsPercentage),
    homeValue berisi JUMLAH berhasil, bukan persen - walau namanya 'Percent'.
    Karena itu, untuk item yang punya total, dibuat juga kolom _total dan _pct.
    """
    hasil = {}
    if not js:
        return hasil
    for blok in js.get("statistics", []):
        if blok.get("period") != "ALL":
            continue
        for grup in blok.get("groups", []):
            for item in grup.get("statisticsItems", []):
                kunci = item.get("key") or slug_kunci(item.get("name", ""))
                if kunci in META_SISI:
                    kunci += "_stat"
                punya_total = item.get("homeTotal") is not None or item.get("awayTotal") is not None
                for sisi in ("home", "away"):
                    nilai = item.get(f"{sisi}Value")
                    if nilai is None:
                        nilai = angka(item.get(sisi))
                    hasil[f"{sisi}_{kunci}"] = nilai
                    total = item.get(f"{sisi}Total")
                    if total is not None:
                        hasil[f"{sisi}_{kunci}_total"] = total
                        hasil[f"{sisi}_{kunci}_pct"] = (round(nilai / total * 100, 1)
                                                        if nilai is not None and total else None)
                if kamus is not None and kunci not in kamus:
                    if punya_total:
                        arti = "JUMLAH berhasil (bukan persen); persentase ada di kolom _pct"
                    elif item.get("renderType") == 2:
                        arti = "persen (0-100)"
                    else:
                        arti = "jumlah"
                    kamus[kunci] = {"kolom": kunci, "nama_di_sofascore": item.get("name"),
                                    "grup": grup.get("groupName"), "arti_nilai": arti}
    return hasil


def pecahan_ke_desimal(s):
    if not s:
        return None
    try:
        if "/" in str(s):
            a, b = str(s).split("/")
            return round(float(a) / float(b) + 1, 3)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return None


def parse_odds(js) -> dict:
    if not js:
        return {}
    for pasar in js.get("markets", []):
        if pasar.get("marketId") == 1 or pasar.get("marketName") in ("Full time", "1X2"):
            pilihan = {c.get("name"): c for c in pasar.get("choices", [])}
            return {
                "odds_home": pecahan_ke_desimal(pilihan.get("1", {}).get("fractionalValue")),
                "odds_draw": pecahan_ke_desimal(pilihan.get("X", {}).get("fractionalValue")),
                "odds_away": pecahan_ke_desimal(pilihan.get("2", {}).get("fractionalValue")),
            }
    return {}


def skor_90(skor: dict):
    if skor.get("normaltime") is not None:
        return skor["normaltime"]
    if skor.get("period1") is not None and skor.get("period2") is not None:
        return skor["period1"] + skor["period2"]
    return skor.get("current")


def kualifikasi(ev) -> bool:
    """Babak kualifikasi/pendahuluan kompetisi Eropa (sebelum fase grup/liga)."""
    teks = " ".join(str(x or "") for x in ((ev.get("tournament") or {}).get("name"),
                                           (ev.get("roundInfo") or {}).get("name"))).lower()
    return any(k in teks for k in ("qualif", "preliminary"))


def meta_laga(ev, liga, musim) -> dict:
    skor_h, skor_a = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    ts = ev.get("startTimestamp")
    return {
        "event_id": ev.get("id"),
        "league": liga,
        "season": musim,
        "round": (ev.get("roundInfo") or {}).get("round"),
        "date_utc": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else None,
        "timestamp": ts,
        "home_team_id": (ev.get("homeTeam") or {}).get("id"),
        "home_team": (ev.get("homeTeam") or {}).get("name"),
        "away_team_id": (ev.get("awayTeam") or {}).get("id"),
        "away_team": (ev.get("awayTeam") or {}).get("name"),
        "home_goals": skor_h.get("current", skor_h.get("normaltime")),
        "away_goals": skor_a.get("current", skor_a.get("normaltime")),
        "home_goals_ht": skor_h.get("period1"),
        "away_goals_ht": skor_a.get("period1"),
        # Skor 90 menit (tanpa perpanjangan waktu & adu penalti) -> target prediksi yang tepat
        "home_goals_90": skor_90(skor_h),
        "away_goals_90": skor_90(skor_a),
        "home_pens": skor_h.get("penalties"),
        "away_pens": skor_a.get("penalties"),
        "extra_time": any(k in skor_h for k in ("overtime", "extra1", "extra2")),
        "stage": (ev.get("tournament") or {}).get("name"),        # mis. "...Group A", "...Knockout"
        "round_name": (ev.get("roundInfo") or {}).get("name"),    # mis. "Round of 16", "Final"
        "previous_leg_event_id": ev.get("previousLegEventId"),     # terisi untuk leg ke-2
        "winner_code": ev.get("winnerCode"),                       # 1 kandang, 2 tandang, 3 seri
        "aggregated_winner_code": ev.get("aggregatedWinnerCode"),  # pemenang agregat 2 leg
        # Kartu merah TIDAK ada di endpoint statistik (terverifikasi). Di objek laga,
        # field ini hanya muncul bila ada kartu merah -> tidak ada berarti 0.
        "home_red_cards": ev.get("homeRedCards", 0),
        "away_red_cards": ev.get("awayRedCards", 0),
    }


# --------------------------------------------------------------------------
# Penjelajahan: musim -> daftar laga -> statistik (-> odds)
# --------------------------------------------------------------------------
def id_musim(f: Fetcher, utid: int) -> dict:
    js = f.get(f"/unique-tournament/{utid}/seasons")
    return {s.get("year"): s.get("id") for s in (js or {}).get("seasons", [])}


def daftar_laga(f: Fetcher, utid: int, sid: int) -> list:
    laga, halaman = {}, 0
    while halaman <= 60:  # pengaman agar tidak berputar tanpa akhir
        js = f.get(f"/unique-tournament/{utid}/season/{sid}/events/last/{halaman}")
        if not js:
            break
        for ev in js.get("events", []):
            laga[ev["id"]] = ev
        if not js.get("hasNextPage"):
            break
        halaman += 1
    return list(laga.values())


def label_musim(m: str) -> str:
    return f"20{m[:2]}/{m[3:]}"  # '18/19' -> '2018/19'


def sudah_selesai(ev) -> bool:
    return (ev.get("status") or {}).get("type") == "finished"


def jelajah(f: Fetcher, liga_pilih, musim_pilih, dengan_odds, cerewet=True, tanpa_kualifikasi=False):
    baris, dilewati, kamus = [], [], {}
    for slug in liga_pilih:
        utid, nama = LEAGUES[slug]
        peta = id_musim(f, utid)
        for m in musim_pilih:
            sid = peta.get(m)
            if not sid:
                dilewati.append({"league": nama, "season": label_musim(m), "alasan": "id musim tidak ditemukan"})
                continue
            semua = daftar_laga(f, utid, sid)
            if not semua:
                dilewati.append({"league": nama, "season": label_musim(m),
                                 "alasan": "belum ada laga terkumpul (belum dijalankan / gagal diambil)"})
                continue
            selesai = [e for e in semua if sudah_selesai(e)]
            for e in semua:
                if not sudah_selesai(e):
                    dilewati.append({"league": nama, "season": label_musim(m), "event_id": e.get("id"),
                                     "alasan": f"status: {(e.get('status') or {}).get('type')}"})
            ada = lambda e, jenis: f._path_cache(f"/event/{e['id']}/{jenis}").exists()
            if nama in KOMPETISI_EROPA:
                # Kualifikasi dilewati jika diminta; saat build, kualifikasi yang memang
                # belum pernah diambil juga dilewati (bukan dianggap "statistik hilang").
                lewati = [e for e in selesai if kualifikasi(e) and
                          (tanpa_kualifikasi if f.allow_network else not ada(e, "statistics"))]
                if lewati:
                    selesai = [e for e in selesai if e not in lewati]
                    if f.allow_network and cerewet:
                        catat(f"   {len(lewati)} laga kualifikasi dilewati (--tanpa-kualifikasi)")
            if cerewet:
                catat(f"== {nama} {label_musim(m)}: {len(selesai)} laga selesai ==")
                req = sum((not ada(e, "statistics")) + (dengan_odds and not ada(e, "odds/1/all"))
                          for e in selesai)
                if req and f.allow_network:
                    menit = req * (f.delay + 1.0) / 60
                    if f.istirahat_tiap:
                        menit += req / f.istirahat_tiap * f.istirahat_menit
                    catat(f"   perlu diambil: {req} request, perkiraan ±{menit // 60:.0f} jam "
                          f"{menit % 60:.0f} menit (ritme {f.delay:.1f} detik/request)")
            for i, ev in enumerate(sorted(selesai, key=lambda e: e.get("startTimestamp") or 0), 1):
                baris_laga = meta_laga(ev, nama, label_musim(m))
                sebelum = f.jumlah_request
                stat = parse_statistik(f.get(f"/event/{ev['id']}/statistics"), kamus)
                if not stat:
                    dilewati.append({"league": nama, "season": label_musim(m), "event_id": ev["id"],
                                     "alasan": "statistik tidak tersedia"})
                baris_laga.update(stat)
                if dengan_odds:
                    baris_laga.update(parse_odds(f.get(f"/event/{ev['id']}/odds/1/all")))
                baris.append(baris_laga)
                if cerewet and f.jumlah_request > sebelum:  # laga yang baru diambil (bukan dari cache)
                    hasil = "berhasil" if stat else "statistik tidak tersedia"
                    catat(f"   ({i}/{len(selesai)}) {baris_laga['home_team']} "
                          f"{baris_laga['home_goals']}-{baris_laga['away_goals']} "
                          f"{baris_laga['away_team']} | {baris_laga['date_utc']} | {hasil}")
    return pd.DataFrame(baris), pd.DataFrame(dilewati), kamus


# --------------------------------------------------------------------------
# Transformasi & laporan
# --------------------------------------------------------------------------
def kunci_statistik(per_laga: pd.DataFrame) -> list:
    return sorted({c[5:] for c in per_laga.columns
                   if c.startswith("home_") and c[5:] not in META_SISI})


def ke_per_tim(per_laga: pd.DataFrame, kunci: list) -> pd.DataFrame:
    meta = [c for c in ("event_id", "league", "season", "stage", "round", "round_name", "leg",
                        "extra_time", "date_utc", "timestamp") if c in per_laga]
    potongan = []
    for sisi, lawan in (("home", "away"), ("away", "home")):
        d = {c: per_laga[c] for c in meta}
        d["is_home"] = sisi == "home"
        d["team_id"], d["team"] = per_laga[f"{sisi}_team_id"], per_laga[f"{sisi}_team"]
        d["opponent_id"], d["opponent"] = per_laga[f"{lawan}_team_id"], per_laga[f"{lawan}_team"]
        d["goals_for"], d["goals_against"] = per_laga[f"{sisi}_goals"], per_laga[f"{lawan}_goals"]
        if f"{sisi}_goals_90" in per_laga:
            d["goals_for_90"], d["goals_against_90"] = per_laga[f"{sisi}_goals_90"], per_laga[f"{lawan}_goals_90"]
            d["pens_for"], d["pens_against"] = per_laga[f"{sisi}_pens"], per_laga[f"{lawan}_pens"]
        if {"odds_home", "odds_draw", "odds_away"} <= set(per_laga.columns):
            d["odds_win"] = per_laga[f"odds_{sisi}"]
            d["odds_draw"] = per_laga["odds_draw"]
            d["odds_loss"] = per_laga[f"odds_{lawan}"]
        for k in kunci:
            d[f"{k}_for"] = per_laga.get(f"{sisi}_{k}")
            d[f"{k}_against"] = per_laga.get(f"{lawan}_{k}")
        potongan.append(pd.DataFrame(d))
    per_tim = pd.concat(potongan, ignore_index=True)
    gf, ga = per_tim["goals_for"], per_tim["goals_against"]
    per_tim.insert(per_tim.columns.get_loc("goals_against") + 1, "result",
                   np.select([gf > ga, gf == ga], ["W", "D"], "L"))
    if "goals_for_90" in per_tim:  # hasil 90 menit: target prediksi yang tepat untuk laga sistem gugur
        g9, a9 = per_tim["goals_for_90"], per_tim["goals_against_90"]
        per_tim.insert(per_tim.columns.get_loc("goals_against_90") + 1, "result_90",
                       np.select([g9 > a9, g9 == a9], ["W", "D"], "L"))
    return per_tim.sort_values(["timestamp", "league", "team"]).reset_index(drop=True)


def simpan_keluaran(per_laga, dilewati, kamus, out: Path, sep: str):
    if per_laga.empty:
        print("Belum ada data di cache untuk dibangun.")
        return
    per_laga = per_laga.sort_values(["timestamp", "league"]).reset_index(drop=True)
    if "previous_leg_event_id" in per_laga:
        leg_1 = set(per_laga["previous_leg_event_id"].dropna().astype(int))
        per_laga.insert(per_laga.columns.get_loc("round_name") + 1, "leg",
                        np.select([per_laga["previous_leg_event_id"].notna(),
                                   per_laga["event_id"].isin(leg_1)], [2, 1], np.nan))
    kunci = kunci_statistik(per_laga)
    per_tim = ke_per_tim(per_laga, kunci)

    kolom_home = [f"home_{k}" for k in kunci]
    cakupan = (per_laga.groupby(["league", "season"])[kolom_home]
               .agg(lambda s: round(s.notna().mean() * 100, 1)))
    cakupan.columns = [c[5:] for c in cakupan.columns]
    lengkap = [k for k in cakupan.columns if (cakupan[k] == 100).all()]

    jumlah = (per_laga.groupby(["league", "season"]).size()
              .unstack("season").fillna(0).astype(int))

    kw = dict(sep=sep, index=False, encoding="utf-8-sig")
    per_laga.to_csv(out / "sofascore_top5_2018_2026_per_laga.csv", **kw)
    per_tim.to_csv(out / "sofascore_top5_2018_2026_per_tim.csv", **kw)
    cakupan.reset_index().to_csv(out / "laporan_cakupan_statistik.csv", **kw)
    jumlah.to_csv(out / "laporan_jumlah_laga.csv", sep=sep, encoding="utf-8-sig")
    (out / "kolom_lengkap_semua_liga.txt").write_text("\n".join(lengkap), encoding="utf-8")
    if kamus:
        (pd.DataFrame(kamus.values()).sort_values(["grup", "kolom"])
           .to_csv(out / "kamus_statistik.csv", **kw))
    if not dilewati.empty:
        dilewati.to_csv(out / "laga_dilewati.csv", **kw)

    print("\n== Jumlah laga per liga-musim ==")
    print(jumlah.to_string())
    print(f"\nStatistik unik ditemukan      : {len(kunci)}")
    print(f"Lengkap 100% di SEMUA liga-musim: {len(lengkap)} (lihat kolom_lengkap_semua_liga.txt)")
    print(f"Laga dilewati/tanpa statistik : {len(dilewati)} (lihat laga_dilewati.csv)")
    menyesatkan = [k for k, v in kamus.items()
                   if "percent" in k.lower() and v["arti_nilai"].startswith("JUMLAH")]
    if menyesatkan:
        print("PERHATIAN - kolom bernama 'Percent' tapi isinya JUMLAH, pakai versi _pct:")
        print("   " + ", ".join(sorted(menyesatkan)))
    print(f"Per laga: {len(per_laga)} baris | Per tim: {len(per_tim)} baris -> {out.resolve()}")


# --------------------------------------------------------------------------
def ip_dns_publik(host):
    """IP menurut DNS publik lewat HTTPS (DoH), yang tidak bisa dibelokkan seperti DNS biasa.
    Mengembalikan set IPv4, atau None jika layanan DoH tidak bisa dihubungi."""
    import requests
    layanan = (("https://cloudflare-dns.com/dns-query", {"accept": "application/dns-json"}),
               ("https://dns.google/resolve", {}))
    for url, header in layanan:
        try:
            r = requests.get(url, params={"name": host, "type": "A"}, headers=header, timeout=10)
            return {j["data"] for j in r.json().get("Answer", []) if j.get("type") == 1}
        except Exception:
            continue
    return None


def cek_koneksi(contact=""):
    """Diagnosis bertahap: DNS -> koneksi TCP -> request HTTPS."""
    import socket
    host = "www.sofascore.com"

    print(f"[1/3] Menerjemahkan nama domain {host}")
    try:
        ip = sorted({a[4][0] for a in socket.getaddrinfo(host, 443)})
        print(f"      -> {', '.join(ip)}")
    except OSError as e:
        print(f"      GAGAL ({e}). Masalah DNS/jaringan, belum sampai ke SofaScore.")
        return

    publik = ip_dns_publik(host)
    dibelokkan = False
    if publik:
        print(f"      IP menurut DNS publik (Cloudflare/Google): {', '.join(sorted(publik))}")
        ip4 = {x for x in ip if ":" not in x}
        dibelokkan = bool(ip4) and not (ip4 & publik)
        if dibelokkan:
            print("      PERHATIAN: IP dari DNS laptop BERBEDA dengan DNS publik.")
            print("      Kemungkinan besar DNS jaringanmu membelokkan domain ini (blokir ISP).")
    else:
        print("      (DNS publik lewat HTTPS tidak bisa dihubungi untuk pembanding)")

    print("[2/3] Membuka koneksi ke port 443")
    try:
        with socket.create_connection((host, 443), timeout=10):
            print("      -> berhasil")
    except OSError as e:
        print(f"      GAGAL ({type(e).__name__}). Koneksi tidak terbentuk sama sekali.")
        if dibelokkan:
            print("      KESIMPULAN: domain sofascore.com DIBELOKKAN oleh DNS jaringanmu ke")
            print("      server lain (pola blokir ISP), jadi permintaan tidak pernah sampai")
            print("      ke SofaScore. Solusi: pakai Cloudflare WARP (aplikasi 1.1.1.1), atau")
            print("      ubah DNS ke 1.1.1.1 dengan DNS-over-HTTPS. Lihat README bagian")
            print("      'Jika ada masalah', lalu jalankan --cek-koneksi lagi.")
        else:
            print("      KESIMPULAN: hambatan di level JARINGAN (firewall/antivirus/jaringan")
            print("      kampus/penyedia internet), bukan penolakan dari server SofaScore.")
            print("      Coba dari jaringan lain (mis. hotspot HP) untuk memastikan.")
        return

    print("[3/3] Request HTTPS ke API (tanpa browser)")
    import requests
    try:
        r = requests.get(API + "/unique-tournament/17/seasons", timeout=20,
                         headers={"User-Agent": identitas_ua(contact), "Accept": "application/json",
                                  "Referer": BERANDA})
    except requests.RequestException as e:
        print(f"      GAGAL ({type(e).__name__}).")
        return
    awal = " ".join(r.text.split())[:120]
    print(f"      -> status {r.status_code} | awal respons: {awal!r}")
    if r.status_code == 200 and ekstrak_json(r.text) is not None:
        print("      KESIMPULAN: jaringan & API aman. Mode requests bisa dipakai (--mode requests).")
    elif r.status_code in (403, 429):
        penanda_jaringan = ("allowlist", "proxy", "firewall", "egress", "internet positif",
                            "blocked by", "administrator", "network")
        if any(t in awal.lower() for t in penanda_jaringan):
            print("      KESIMPULAN: penolakan tampaknya berasal dari PERANTARA JARINGAN")
            print("      (proxy/firewall/pengelola jaringan), bukan dari SofaScore. Lihat cuplikan.")
        else:
            print("      KESIMPULAN: server menolak klien non-browser. Ini NORMAL; pakai mode")
            print("      browser (default) atau minta whitelist IP ke SofaScore memakai izinmu.")
    else:
        print("      KESIMPULAN: respons tidak terduga. Kirim cuplikan di atas untuk dianalisis.")


# --------------------------------------------------------------------------
def kumpulkan(a, cache: Path, opsi_browser: dict):
    """Isi cache sampai tuntas. Mode otomatis (default) tidak berhenti karena challenge,
    koneksi putus, atau error tak terduga: script beristirahat lalu melanjutkan sendiri."""
    otomatis = not a.manual
    jumlah_file = lambda: sum(1 for _ in cache.glob("*.json"))
    total_request, putaran, crash = 0, 0, 0
    ritme_path = cache.parent / "ritme.json"

    def simpan_ritme(nilai):
        try:
            ritme_path.write_text(json.dumps({"delay": nilai}), encoding="utf-8")
        except OSError:
            pass

    try:  # lanjutkan ritme yang dipelajari dari run sebelumnya
        ritme = float(json.loads(ritme_path.read_text(encoding="utf-8"))["delay"])
    except (OSError, ValueError, KeyError, TypeError):
        ritme = a.delay
    if ritme > a.delay:
        catat(f"Memakai ritme dari run sebelumnya: {ritme:.1f} detik per request.")
    catat("Mengumpulkan data. Semua respons disimpan ke cache; aman dihentikan (Ctrl+C) kapan saja.")
    if otomatis:
        catat("Mode otomatis: boleh ditinggal. Riwayat kejadian ada di log_scraping.txt.")
    cegah_sleep(True)
    try:
        while True:
            putaran += 1
            sebelum = jumlah_file()
            f = Fetcher(cache, a.mode, max(a.delay, ritme), a.backoff, otomatis=otomatis,
                        istirahat_tiap=a.istirahat_tiap, istirahat_menit=a.istirahat_menit,
                        **opsi_browser)
            f.delay_min = a.delay  # boleh kembali secepat --delay setelah lama lancar
            tuntas = False
            try:
                jelajah(f, a.leagues, a.seasons, a.with_odds, cerewet=True,  # hanya mengisi cache
                        tanpa_kualifikasi=a.tanpa_kualifikasi)
                tuntas = True
            except Terblokir as e:  # hanya terjadi di mode --manual
                catat(f"[BERHENTI] {e}")
                return
            except Exception as e:
                crash += 1
                import traceback
                if LOG_PATH is not None:
                    with open(LOG_PATH, "a", encoding="utf-8") as fh:
                        fh.write(traceback.format_exc())
                catat(f"[ERROR] {type(e).__name__}: {e}")
                if not otomatis or crash > 20:
                    catat("Terlalu banyak error; berhenti. Jalankan perintah yang sama untuk melanjutkan.")
                    return
                catat(f"Script dimulai ulang otomatis (ke-{crash}); data yang sudah ada tetap di cache.")
                time.sleep(60)
            finally:
                f.tutup()
                total_request += f.jumlah_request
                ritme = f.delay
                simpan_ritme(ritme)

            baru = jumlah_file() - sebelum
            if tuntas and (baru == 0 or not otomatis):
                break
            if tuntas:
                # Putaran ulang: hanya URL yang tadi gagal/dilewati yang diminta lagi.
                catat(f"Putaran {putaran} selesai ({baru} data baru). Mengecek ulang data yang "
                      "sempat gagal...")
        catat("SELESAI: semua data yang tersedia sudah terkumpul.")
    except KeyboardInterrupt:
        catat("[DIHENTIKAN] Data yang sudah terkumpul aman di cache.")
    finally:
        cegah_sleep(False)
        catat(f"Total request sesi ini: {total_request}")


def main():
    try:  # cegah error cetak huruf non-ASCII di konsol Windows
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Scraper SofaScore 5 liga top Eropa (penelitian berizin)")
    p.add_argument("--leagues", nargs="+", default=list(LEAGUES), choices=list(LEAGUES) + list(GRUP_LIGA),
                   help="liga/kompetisi, atau singkatan: top5, liga-tambahan, eropa (default: semua)")
    p.add_argument("--tanpa-kualifikasi", action="store_true",
                   help="lewati babak kualifikasi UCL/UEL (lebih cepat; fokus fase utama)")
    p.add_argument("--seasons", nargs="+", default=SEASONS, choices=SEASONS)
    p.add_argument("--mode", choices=["browser", "requests"], default="browser")
    p.add_argument("--browser", choices=["chrome", "edge"], default="chrome",
                   help="browser yang dipakai mode browser (default chrome)")
    p.add_argument("--browser-path", default="",
                   help="lokasi file chrome.exe/msedge.exe jika tidak di lokasi standar")
    p.add_argument("--port", type=int, default=9222,
                   help="port debugging browser (ganti jika 9222 sudah dipakai program lain)")
    p.add_argument("--headless", action="store_true", help="browser tanpa jendela (tidak disarankan)")
    p.add_argument("--delay", type=float, default=7.0, help="jeda rata-rata antar request (detik)")
    p.add_argument("--istirahat-tiap", type=int, default=100,
                   help="istirahat setiap N request agar tidak memicu challenge (0 = tanpa istirahat)")
    p.add_argument("--istirahat-menit", type=float, default=5.0, help="lama istirahat berkala (menit)")
    p.add_argument("--manual", action="store_true",
                   help="perilaku lama: berhenti & minta Enter saat kena challenge (tidak bisa ditinggal)")
    p.add_argument("--backoff", type=float, default=30.0, help="jeda dasar saat gagal (detik)")
    p.add_argument("--with-odds", action="store_true", help="ikut ambil odds 1X2 (request 2x lipat)")
    p.add_argument("--contact", default="", help="email Anda, dicantumkan di identitas request")
    p.add_argument("--sep", default=";", help="pemisah CSV (default ';' agar rapi di Excel Indonesia)")
    p.add_argument("--out", default="data_sofascore")
    p.add_argument("--probe", action="store_true", help="uji struktur respons dengan sedikit request")
    p.add_argument("--build-only", action="store_true", help="bangun CSV dari cache saja")
    p.add_argument("--cek-koneksi", action="store_true", help="diagnosis jaringan ke API SofaScore")
    a = p.parse_args()
    a.leagues = list(dict.fromkeys(s for x in a.leagues for s in GRUP_LIGA.get(x, [x])))

    if a.cek_koneksi:
        cek_koneksi(a.contact)
        return

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache_json"
    global LOG_PATH
    LOG_PATH = out / "log_scraping.txt"
    opsi_browser = dict(contact=a.contact, headless=a.headless,
                        browser=a.browser, browser_path=a.browser_path, port=a.port)

    if a.probe:
        # retry pendek agar probe cepat memberi kabar
        f = Fetcher(cache, a.mode, a.delay, backoff=min(a.backoff, 5), max_retries=2, **opsi_browser)
        try:
            for slug in a.leagues:  # pastikan ID turnamen menunjuk kompetisi yang benar
                info = (f.get(f"/unique-tournament/{LEAGUES[slug][0]}") or {}).get("uniqueTournament", {})
                print(f"ID {LEAGUES[slug][0]:>4} ({slug}) -> di SofaScore: {info.get('name')!r} "
                      f"[{(info.get('category') or {}).get('name')}]")
            utid, nama = LEAGUES[a.leagues[0]]
            peta = id_musim(f, utid)
            tersedia = [m for m in SEASONS if m in peta]
            print(f"{nama}: musim target yang ditemukan -> {tersedia}")
            if tersedia:
                laga = daftar_laga(f, utid, peta[tersedia[-1]])
                selesai = [e for e in laga if sudah_selesai(e)]
                print(f"Laga selesai di {label_musim(tersedia[-1])}: {len(selesai)}")
                babak = sorted({f"{(e.get('tournament') or {}).get('name')} | "
                                f"{(e.get('roundInfo') or {}).get('name')}" for e in selesai})
                print(f"Babak/tahap yang ditemukan ({len(babak)}):")
                for b in babak[:25]:
                    print(f"   {b}{'   <- kualifikasi' if kualifikasi({'tournament': {'name': b}}) else ''}")
                if selesai:
                    stat = parse_statistik(f.get(f"/event/{selesai[0]['id']}/statistics"))
                    print(f"Contoh laga {selesai[0]['id']}: {len(stat)} kolom statistik")
                    for k, v in list(stat.items())[:12]:
                        print(f"   {k:<45} {v}")
            if tersedia:
                print("\nPROBE BERHASIL. Lanjutkan dengan menjalankan tanpa --probe.")
            else:
                print("\nPROBE GAGAL: daftar musim tidak bisa diambil. Baca debug_respons.txt, "
                      "lalu jalankan --cek-koneksi.")
        except Terblokir as e:
            print(f"\n[BERHENTI] {e}")
        except KeyboardInterrupt:
            print("\n[DIHENTIKAN] Probe dihentikan.")
        finally:
            f.tutup()
        contoh = sorted(cache.glob("*.json"))
        if contoh:
            print(f"\n{len(contoh)} contoh JSON ASLI tersimpan di: {cache.resolve()}")
        if (out / "debug_respons.txt").exists():
            print(f"Cuplikan respons yang gagal ada di: {(out / 'debug_respons.txt').resolve()}")
        return

    if not a.build_only:
        kumpulkan(a, cache, opsi_browser)

    print("\nMembangun CSV dari cache...")
    offline = Fetcher(cache, allow_network=False)
    # Selalu gabungkan SEMUA kompetisi yang sudah ada di cache (bukan hanya --leagues yang
    # baru dijalankan), supaya CSV lama tidak tertimpa versi yang lebih sedikit isinya.
    di_cache = [s for s, (utid, _) in LEAGUES.items()
                if offline._path_cache(f"/unique-tournament/{utid}/seasons").exists()]
    print("Kompetisi di cache: " + ", ".join(LEAGUES[s][1] for s in di_cache))
    per_laga, dilewati, kamus = jelajah(offline, di_cache, SEASONS, True, cerewet=False)
    simpan_keluaran(per_laga, dilewati, kamus, out, a.sep)


if __name__ == "__main__":
    main()
