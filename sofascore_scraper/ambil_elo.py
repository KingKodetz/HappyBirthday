"""
Ambil rating Elo klub dari clubelo.com dan pasangkan ke setiap laga hasil scraping SofaScore.

Elo = ukuran kekuatan tim yang tersedia untuk SEMUA klub Eropa (termasuk Benfica, Ajax,
Galatasaray, dst.), jadi cocok untuk membandingkan tim dari liga berbeda di UCL/UEL.

Langkah:
  1. Unduh daftar klub clubelo (beberapa tanggal 2018-2026) untuk tahu nama-nama klubnya.
  2. Cocokkan nama tim SofaScore -> nama clubelo (otomatis + bisa dikoreksi manual).
  3. Unduh riwayat Elo setiap klub yang dipakai (disimpan di cache, aman dihentikan).
  4. Ambil Elo H-1 (sehari SEBELUM laga, agar tidak bocor hasil laga itu sendiri).

Hasil (di folder data_sofascore/):
  elo_per_laga.csv                     event_id, home_elo, away_elo, elo_diff
  sofascore_dengan_elo_per_laga.csv    data per laga + kolom Elo
  elo_pemetaan_nama.csv                hasil pencocokan nama (PERIKSA baris 'cek_manual')

Koreksi nama: buat file elo_pemetaan_manual.csv (pemisah ';') berkolom
  sofascore;clubelo
lalu jalankan lagi. Isi clubelo dengan nama persis seperti di elo_pemetaan_nama.csv /
situs clubelo.com (mis. "Man City"). Kosongkan clubelo untuk tim yang memang tidak ada.

Jalankan:  python ambil_elo.py
"""
import argparse
import difflib
import io
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ELO_API = "http://api.clubelo.com"

# Nama SofaScore yang beda jauh dengan nama clubelo (dipakai hanya jika ada di daftar clubelo)
ALIAS = {
    "Manchester City": "Man City", "Manchester United": "Man United",
    "Tottenham Hotspur": "Tottenham", "Wolverhampton": "Wolves",
    "Brighton & Hove Albion": "Brighton", "Nottingham Forest": "Forest",
    "West Ham United": "West Ham", "Newcastle United": "Newcastle",
    "Atlético Madrid": "Atletico", "Athletic Club": "Bilbao", "Real Betis": "Betis",
    "Real Sociedad": "Sociedad", "Deportivo Alavés": "Alaves", "Celta Vigo": "Celta",
    "FC Bayern München": "Bayern", "Borussia Dortmund": "Dortmund",
    "Bayer 04 Leverkusen": "Leverkusen", "Borussia M'gladbach": "Gladbach",
    "Eintracht Frankfurt": "Frankfurt", "1. FC Köln": "Koeln", "FC Schalke 04": "Schalke",
    "SV Werder Bremen": "Werder", "1. FC Union Berlin": "Union Berlin",
    "Paris Saint-Germain": "Paris SG", "Olympique de Marseille": "Marseille",
    "Olympique Lyonnais": "Lyon", "Hellas Verona": "Verona", "Inter": "Inter",
    "Sporting CP": "Sporting", "FC Porto": "Porto", "PSV Eindhoven": "PSV",
    "AZ Alkmaar": "Alkmaar", "Fenerbahçe": "Fenerbahce", "Beşiktaş": "Besiktas",
    "Başakşehir": "Basaksehir", "Red Bull Salzburg": "Salzburg", "Club Brugge KV": "Brugge",
    "Shakhtar Donetsk": "Shakhtar", "Zenit St. Petersburg": "Zenit",
    "Olympiacos": "Olympiakos", "Crvena Zvezda": "Crvena Zvezda",
}

# Kata umum yang dibuang sebelum membandingkan nama
KATA_UMUM = {"fc", "cf", "afc", "ac", "as", "sc", "ssc", "sv", "vfl", "vfb", "tsg", "rc", "cd",
             "ud", "sd", "rcd", "ogc", "club", "calcio", "de", "la", "le", "1", "fk", "sk", "nk",
             "bk", "if", "ff", "kv", "krc", "rsc", "bsc", "hsc", "us", "ss", "acf", "sad", "spor",
             "kulubu", "jk", "cp", "sl"}


def normal(nama: str) -> str:
    teks = unicodedata.normalize("NFKD", str(nama)).encode("ascii", "ignore").decode().lower()
    teks = re.sub(r"[^a-z0-9 ]+", " ", teks)
    kata = [k for k in teks.split() if k not in KATA_UMUM and not k.isdigit()]
    return " ".join(kata) or teks.strip()


class Elo:
    def __init__(self, cache: Path, delay: float):
        self.cache = cache
        self.cache.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "research-elo/1.0 (skripsi)"

    def csv(self, jalur: str, nama_file: str) -> pd.DataFrame:
        """Ambil CSV dari clubelo dengan cache + retry."""
        f = self.cache / nama_file
        if not f.exists():
            for coba in range(1, 6):
                try:
                    r = self.s.get(f"{ELO_API}/{jalur}", timeout=30)
                    if r.status_code == 200:
                        f.write_text(r.text, encoding="utf-8")
                        break
                    print(f"  [!] {jalur} -> status {r.status_code} (coba {coba}/5)")
                except requests.RequestException as e:
                    print(f"  [!] {jalur} -> {type(e).__name__} (coba {coba}/5)")
                time.sleep(self.delay * 5 * coba)
            else:
                return pd.DataFrame()
            time.sleep(self.delay)
        teks = f.read_text(encoding="utf-8")
        return pd.read_csv(io.StringIO(teks)) if teks.strip() else pd.DataFrame()


def cocokkan(tim: list, daftar: pd.DataFrame, manual: dict) -> pd.DataFrame:
    nama_elo = sorted(set(daftar["Club"]))
    per_normal = {}
    for n in nama_elo:
        per_normal.setdefault(normal(n), n)
    kunci = list(per_normal)
    baris = []
    for t in tim:
        if t in manual:
            baris.append((t, manual[t] or None, "manual", 1.0))
            continue
        if ALIAS.get(t) in nama_elo:
            baris.append((t, ALIAS[t], "alias", 1.0))
            continue
        nt = normal(t)
        if nt in per_normal:
            baris.append((t, per_normal[nt], "persis", 1.0))
            continue
        calon = difflib.get_close_matches(nt, kunci, n=1, cutoff=0.0)
        skor = difflib.SequenceMatcher(None, nt, calon[0]).ratio() if calon else 0.0
        # nama satu kata yang terkandung (mis. "napoli" di "ssc napoli") dianggap cocok
        isi = [k for k in kunci if nt and (nt in k.split() or k in nt.split())]
        if isi and skor < 0.85:
            calon, skor = [isi[0]], max(skor, 0.85)
        metode = "mirip" if skor >= 0.85 else "cek_manual"
        baris.append((t, per_normal[calon[0]] if calon else None, metode, round(skor, 2)))
    return pd.DataFrame(baris, columns=["sofascore", "clubelo", "metode", "skor"])


def elo_pada(riwayat: pd.DataFrame, tanggal: pd.Series) -> pd.Series:
    """Elo yang berlaku pada tanggal tertentu (baris dengan From <= tanggal <= To)."""
    if riwayat.empty:
        return pd.Series([None] * len(tanggal), index=tanggal.index)
    r = riwayat.sort_values("From")
    q = pd.DataFrame({"t": tanggal.values, "i": tanggal.index}).sort_values("t")
    hasil = pd.merge_asof(q, r[["From", "To", "Elo"]], left_on="t", right_on="From")
    hasil.loc[hasil["t"] > hasil["To"], "Elo"] = None
    return hasil.set_index("i")["Elo"].reindex(tanggal.index)


def main():
    p = argparse.ArgumentParser(description="Pasangkan Elo clubelo.com ke data SofaScore")
    p.add_argument("--out", default="data_sofascore")
    p.add_argument("--delay", type=float, default=1.5, help="jeda antar request ke clubelo (detik)")
    p.add_argument("--sep", default=";")
    a = p.parse_args()

    out = Path(a.out)
    laga = pd.read_csv(out / "sofascore_top5_2018_2026_per_laga.csv", sep=a.sep)
    laga["tanggal"] = pd.to_datetime(laga["date_utc"]).dt.normalize() - pd.Timedelta(days=1)  # H-1
    elo = Elo(out / "cache_elo", a.delay)

    # 1) daftar nama klub clubelo dari beberapa tanggal
    print("[1/4] Mengunduh daftar klub clubelo...")
    potong = []
    for th in range(2018, date.today().year + 1):
        for bln in ("01-15", "08-15"):
            tgl = f"{th}-{bln}"
            if tgl <= date.today().isoformat():
                potong.append(elo.csv(tgl, f"snapshot_{tgl}.csv"))
    daftar = pd.concat([x for x in potong if not x.empty], ignore_index=True)
    if daftar.empty:
        raise SystemExit("Tidak bisa mengunduh data dari clubelo.com. Periksa internet / WARP.")
    print(f"      {daftar['Club'].nunique()} klub di clubelo")

    # 2) pencocokan nama
    print("[2/4] Mencocokkan nama tim...")
    f_manual = out / "elo_pemetaan_manual.csv"
    manual = {}
    if f_manual.exists():
        m = pd.read_csv(f_manual, sep=";", dtype=str).fillna("")
        manual = dict(zip(m["sofascore"], m["clubelo"]))
        print(f"      memakai {len(manual)} koreksi dari {f_manual.name}")
    tim = sorted(set(laga["home_team"]) | set(laga["away_team"]))
    peta = cocokkan(tim, daftar, manual)
    peta.to_csv(out / "elo_pemetaan_nama.csv", sep=";", index=False, encoding="utf-8-sig")
    cek = peta[peta.metode == "cek_manual"]
    print(f"      {len(tim)} tim: {(peta.metode != 'cek_manual').sum()} cocok, "
          f"{len(cek)} perlu dicek (lihat elo_pemetaan_nama.csv)")

    # 3) riwayat Elo per klub
    # Tebakan yang tidak yakin ('cek_manual') TIDAK dipakai sampai dikoreksi manual
    pakai = peta[peta.metode != "cek_manual"].dropna(subset=["clubelo"])
    klub = sorted(set(pakai["clubelo"]))
    print(f"[3/4] Mengunduh riwayat Elo {len(klub)} klub (cache: {elo.cache})...")
    riwayat = {}
    for i, k in enumerate(klub, 1):
        df = elo.csv(k.replace(" ", ""), f"klub_{re.sub(r'[^A-Za-z0-9]+', '_', k)}.csv")
        if not df.empty:
            df["From"], df["To"] = pd.to_datetime(df["From"]), pd.to_datetime(df["To"])
        riwayat[k] = df
        if i % 25 == 0 or i == len(klub):
            print(f"      {i}/{len(klub)} klub")

    # 4) Elo H-1 untuk setiap laga
    print("[4/4] Memasangkan Elo ke setiap laga...")
    ke_elo = dict(zip(pakai["sofascore"], pakai["clubelo"]))
    for sisi in ("home", "away"):
        nilai = pd.Series(index=laga.index, dtype=float)
        for nama_ss, g in laga.groupby(f"{sisi}_team"):
            k = ke_elo.get(nama_ss)
            if isinstance(k, str) and k in riwayat:
                nilai.loc[g.index] = elo_pada(riwayat[k], g["tanggal"]).astype(float)
        laga[f"{sisi}_elo"] = nilai.round(1)
    laga["elo_diff"] = (laga["home_elo"] - laga["away_elo"]).round(1)
    laga = laga.drop(columns="tanggal")

    kw = dict(sep=a.sep, index=False, encoding="utf-8-sig")
    laga[["event_id", "league", "season", "date_utc", "home_team", "away_team",
          "home_elo", "away_elo", "elo_diff"]].to_csv(out / "elo_per_laga.csv", **kw)
    laga.to_csv(out / "sofascore_dengan_elo_per_laga.csv", **kw)

    ada = laga["home_elo"].notna() & laga["away_elo"].notna()
    print(f"\nElo lengkap di {ada.sum()}/{len(laga)} laga ({ada.mean() * 100:.1f}%)")
    print(laga.assign(ada=ada).groupby("league")["ada"].mean().mul(100).round(1).to_string())
    if len(cek):
        print(f"\nPERIKSA {len(cek)} tim berlabel 'cek_manual' di elo_pemetaan_nama.csv (Elo-nya "
              "dikosongkan dulu). Koreksi lewat elo_pemetaan_manual.csv, lalu jalankan lagi "
              "(unduhan sebelumnya tidak diulang).")


if __name__ == "__main__":
    main()
