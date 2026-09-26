"""
Scraper statistik pertandingan SofaScore - 5 Liga Top Eropa (2018/19 - 2025/26)
Untuk keperluan penelitian, dengan izin dari SofaScore.

Cara kerja mode browser (default):
  1. Selenium membuka https://www.sofascore.com/ di Chrome/Edge seperti pengunjung
     biasa. Pop-up cookie atau verifikasi bisa diselesaikan manual di jendela itu.
  2. Data diminta dengan fetch() dari DALAM halaman tersebut ke /api/v1/..., sama
     seperti cara situs SofaScore memuat datanya sendiri.
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
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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
}

SEASONS = ["18/19", "19/20", "20/21", "21/22", "22/23", "23/24", "24/25", "25/26"]

# Kolom per-sisi yang BUKAN statistik (dipakai untuk memisahkan meta vs statistik)
META_SISI = {"team_id", "team", "goals", "goals_ht"}

# Dijalankan di dalam tab sofascore.com: minta data API dengan cookie browser itu sendiri.
SKRIP_FETCH = """
const url = arguments[0], selesai = arguments[arguments.length - 1];
const ctrl = new AbortController();
const batas = setTimeout(() => ctrl.abort(), 45000);
fetch(url, {credentials: "include", headers: {"Accept": "application/json"}, signal: ctrl.signal})
  .then(r => r.text().then(t => { clearTimeout(batas); selesai([r.status, t]); }))
  .catch(e => { clearTimeout(batas); selesai(["koneksi: " + e, ""]); });
"""


class Terblokir(Exception):
    pass


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
                 allow_network=True, browser="chrome", browser_path=""):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.delay = delay
        self.backoff = backoff
        self.max_retries = max_retries
        self.max_gagal_beruntun = max_gagal_beruntun
        self.contact = contact
        self.headless = headless
        self.allow_network = allow_network
        self.browser = browser
        self.browser_path = browser_path
        self.gagal_beruntun = 0
        self.jumlah_request = 0
        self._session = None
        self._driver = None

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
        for percobaan in range(1, self.max_retries + 1):
            status, data = self._ambil(url)
            self.jumlah_request += 1
            time.sleep(self.delay)

            if status == 200 and data is not None:
                cp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self.gagal_beruntun = 0
                return data

            if status == 404:  # memang tidak ada (mis. laga tanpa statistik)
                cp.write_text(json.dumps({"_tidak_ada": True}), encoding="utf-8")
                self.gagal_beruntun = 0
                return None

            terblokir = status in (403, 429, "verifikasi")
            if terblokir and self.mode == "browser" and interaktif():
                print(f"\n  >> Akses ditolak (status {status}) untuk {path}.")
                self._buka_beranda()
                tunggu_pengguna("Lihat jendela browser. Jika ada halaman verifikasi, "
                                "selesaikan secara MANUAL.")
                continue

            print(f"  [!] {path} -> status {status} (percobaan {percobaan}/{self.max_retries})")
            if percobaan < self.max_retries:
                time.sleep(self.backoff * percobaan)

        self.gagal_beruntun += 1
        if self.gagal_beruntun >= self.max_gagal_beruntun:
            raise Terblokir(
                f"{self.gagal_beruntun} permintaan berturut-turut gagal. Kemungkinan akses "
                "diblokir. Script dihentikan agar tidak membebani server. Tunggu beberapa "
                "waktu, atau hubungi SofaScore dengan bukti izin Anda untuk akses resmi."
            )
        return None

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

    # ---- mode browser (default) ----
    def _mulai_browser(self):
        from selenium import webdriver
        pakai_edge = self.browser == "edge"
        opsi = webdriver.EdgeOptions() if pakai_edge else webdriver.ChromeOptions()
        # Profil browser tersendiri: cookie & hasil verifikasi tersimpan antar sesi.
        profil = (self.cache_dir.parent / f"profil_{self.browser}").resolve()
        opsi.add_argument(f"--user-data-dir={profil}")
        opsi.add_argument("--window-size=1200,900")
        if self.browser_path:
            opsi.binary_location = self.browser_path
        if self.headless:
            opsi.add_argument("--headless=new")
        print(f"Membuka browser {self.browser} (profil: {profil})")
        try:
            self._driver = (webdriver.Edge if pakai_edge else webdriver.Chrome)(options=opsi)
        except Exception as e:
            raise SystemExit(
                f"\nGagal membuka browser {self.browser}: {str(e).strip().splitlines()[0]}\n"
                "Periksa: (1) browser sudah terpasang, (2) jendela browser dari run sebelumnya\n"
                "sudah ditutup semua (profil tidak boleh dipakai dua kali), (3) coba\n"
                "--browser edge, atau --browser-path jika browser tidak di lokasi standar.")
        self._driver.set_script_timeout(60)
        self._buka_beranda()
        tunggu_pengguna("Browser sudah membuka sofascore.com. Tutup pop-up cookie / "
                        "selesaikan verifikasi jika ada.")

    def _buka_beranda(self):
        if self._driver is None:
            return
        try:
            self._driver.get(BERANDA)
        except Exception as e:
            print(f"  [!] gagal membuka {BERANDA}: {type(e).__name__}")
        time.sleep(4)  # beri waktu halaman selesai dimuat

    def _ambil_browser(self, url):
        if self._driver is None:
            self._mulai_browser()
        try:
            # fetch() harus dijalankan dari halaman sofascore.com (satu origin dengan API)
            if not self._driver.current_url.startswith(BERANDA.rstrip("/")):
                self._buka_beranda()
            status, teks = self._driver.execute_async_script(SKRIP_FETCH, url)
        except Exception as e:
            nama = type(e).__name__
            if nama in ("NoSuchWindowException", "InvalidSessionIdException"):
                print("  [!] Jendela browser tertutup, akan dibuka ulang.")
                self.tutup()
            return (f"browser: {nama}", None)

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

    def tutup(self):
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None


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


def jelajah(f: Fetcher, liga_pilih, musim_pilih, dengan_odds, cerewet=True):
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
            if cerewet:
                print(f"== {nama} {label_musim(m)}: {len(selesai)} laga selesai ==")
            for i, ev in enumerate(sorted(selesai, key=lambda e: e.get("startTimestamp") or 0), 1):
                baris_laga = meta_laga(ev, nama, label_musim(m))
                stat = parse_statistik(f.get(f"/event/{ev['id']}/statistics"), kamus)
                if not stat:
                    dilewati.append({"league": nama, "season": label_musim(m), "event_id": ev["id"],
                                     "alasan": "statistik tidak tersedia"})
                baris_laga.update(stat)
                if dengan_odds:
                    baris_laga.update(parse_odds(f.get(f"/event/{ev['id']}/odds/1/all")))
                baris.append(baris_laga)
                if cerewet and i % 50 == 0:
                    print(f"   {i}/{len(selesai)} laga | total request: {f.jumlah_request}")
    return pd.DataFrame(baris), pd.DataFrame(dilewati), kamus


# --------------------------------------------------------------------------
# Transformasi & laporan
# --------------------------------------------------------------------------
def kunci_statistik(per_laga: pd.DataFrame) -> list:
    return sorted({c[5:] for c in per_laga.columns
                   if c.startswith("home_") and c[5:] not in META_SISI})


def ke_per_tim(per_laga: pd.DataFrame, kunci: list) -> pd.DataFrame:
    meta = [c for c in ("event_id", "league", "season", "round", "date_utc", "timestamp")
            if c in per_laga]
    potongan = []
    for sisi, lawan in (("home", "away"), ("away", "home")):
        d = {c: per_laga[c] for c in meta}
        d["is_home"] = sisi == "home"
        d["team_id"], d["team"] = per_laga[f"{sisi}_team_id"], per_laga[f"{sisi}_team"]
        d["opponent_id"], d["opponent"] = per_laga[f"{lawan}_team_id"], per_laga[f"{lawan}_team"]
        d["goals_for"], d["goals_against"] = per_laga[f"{sisi}_goals"], per_laga[f"{lawan}_goals"]
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
    return per_tim.sort_values(["timestamp", "league", "team"]).reset_index(drop=True)


def simpan_keluaran(per_laga, dilewati, kamus, out: Path, sep: str):
    if per_laga.empty:
        print("Belum ada data di cache untuk dibangun.")
        return
    per_laga = per_laga.sort_values(["timestamp", "league"]).reset_index(drop=True)
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

    print("[2/3] Membuka koneksi ke port 443")
    try:
        with socket.create_connection((host, 443), timeout=10):
            print("      -> berhasil")
    except OSError as e:
        print(f"      GAGAL ({type(e).__name__}). Koneksi tidak terbentuk sama sekali.")
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
def main():
    try:  # cegah error cetak huruf non-ASCII di konsol Windows
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    p = argparse.ArgumentParser(description="Scraper SofaScore 5 liga top Eropa (penelitian berizin)")
    p.add_argument("--leagues", nargs="+", default=list(LEAGUES), choices=list(LEAGUES))
    p.add_argument("--seasons", nargs="+", default=SEASONS, choices=SEASONS)
    p.add_argument("--mode", choices=["browser", "requests"], default="browser")
    p.add_argument("--browser", choices=["chrome", "edge"], default="chrome",
                   help="browser yang dipakai mode browser (default chrome)")
    p.add_argument("--browser-path", default="",
                   help="lokasi file chrome.exe/msedge.exe jika tidak di lokasi standar")
    p.add_argument("--headless", action="store_true", help="browser tanpa jendela (tidak disarankan)")
    p.add_argument("--delay", type=float, default=2.0, help="jeda antar request (detik)")
    p.add_argument("--backoff", type=float, default=30.0, help="jeda dasar saat gagal (detik)")
    p.add_argument("--with-odds", action="store_true", help="ikut ambil odds 1X2 (request 2x lipat)")
    p.add_argument("--contact", default="", help="email Anda, dicantumkan di identitas request")
    p.add_argument("--sep", default=";", help="pemisah CSV (default ';' agar rapi di Excel Indonesia)")
    p.add_argument("--out", default="data_sofascore")
    p.add_argument("--probe", action="store_true", help="uji struktur respons dengan sedikit request")
    p.add_argument("--build-only", action="store_true", help="bangun CSV dari cache saja")
    p.add_argument("--cek-koneksi", action="store_true", help="diagnosis jaringan ke API SofaScore")
    a = p.parse_args()

    if a.cek_koneksi:
        cek_koneksi(a.contact)
        return

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = out / "cache_json"
    opsi_browser = dict(contact=a.contact, headless=a.headless,
                        browser=a.browser, browser_path=a.browser_path)

    if a.probe:
        # retry pendek agar probe cepat memberi kabar
        f = Fetcher(cache, a.mode, a.delay, backoff=min(a.backoff, 5), max_retries=2, **opsi_browser)
        try:
            utid, nama = LEAGUES[a.leagues[0]]
            peta = id_musim(f, utid)
            tersedia = [m for m in SEASONS if m in peta]
            print(f"{nama}: musim target yang ditemukan -> {tersedia}")
            if tersedia:
                laga = daftar_laga(f, utid, peta[tersedia[-1]])
                selesai = [e for e in laga if sudah_selesai(e)]
                print(f"Laga selesai di {label_musim(tersedia[-1])}: {len(selesai)}")
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
        finally:
            f.tutup()
        contoh = sorted(cache.glob("*.json"))
        if contoh:
            print(f"\n{len(contoh)} contoh JSON ASLI tersimpan di: {cache.resolve()}")
        if (out / "debug_respons.txt").exists():
            print(f"Cuplikan respons yang gagal ada di: {(out / 'debug_respons.txt').resolve()}")
        return

    if not a.build_only:
        f = Fetcher(cache, a.mode, a.delay, a.backoff, **opsi_browser)
        print("Mengumpulkan data. Semua respons disimpan ke cache; aman dihentikan (Ctrl+C) kapan saja.")
        try:
            jelajah(f, a.leagues, a.seasons, a.with_odds, cerewet=True)  # hanya mengisi cache
        except Terblokir as e:
            print(f"\n[BERHENTI] {e}")
        except KeyboardInterrupt:
            print("\n[DIHENTIKAN] Data yang sudah terkumpul aman di cache.")
        finally:
            f.tutup()
            print(f"Total request sesi ini: {f.jumlah_request}")

    print("\nMembangun CSV dari cache...")
    offline = Fetcher(cache, allow_network=False)
    per_laga, dilewati, kamus = jelajah(offline, a.leagues, a.seasons, a.with_odds, cerewet=False)
    simpan_keluaran(per_laga, dilewati, kamus, out, a.sep)


if __name__ == "__main__":
    main()
