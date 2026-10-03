"""
Buang laga play-off promosi/degradasi dari hasil scraping, sisakan laga liga reguler.

SofaScore memasukkan play-off (mis. Bundesliga vs 2. Bundesliga, Ligue 1 vs Ligue 2,
tiebreaker Serie A 2022/23, play-off Eredivisie) ke dalam musim liga. Skor play-off juga
bisa memuat adu penalti. UCL/UEL TIDAK disentuh (semua laganya dipertahankan).
Jalankan setelah --build-only:  python bersihkan_playoff.py
"""
from pathlib import Path

import pandas as pd

folder = Path("data_sofascore")
d = pd.read_csv(folder / "sofascore_top5_2018_2026_per_laga.csv", sep=";")
t = pd.read_csv(folder / "sofascore_top5_2018_2026_per_tim.csv", sep=";")

EROPA = {"UEFA Champions League", "UEFA Europa League"}
domestik = ~d.league.isin(EROPA)

# Tim yang hanya main sedikit laga di suatu liga-musim = tim divisi 2 di laga play-off
main = pd.concat([d[["league", "season", "home_team"]].rename(columns={"home_team": "team"}),
                  d[["league", "season", "away_team"]].rename(columns={"away_team": "team"})])
jumlah = main.value_counts().rename("n").reset_index()
# relatif terhadap tim terbanyak main di liga-musim itu (aman untuk musim yang belum lengkap)
jumlah["maks"] = jumlah.groupby(["league", "season"]).n.transform("max")
tamu = set(map(tuple, jumlah.loc[jumlah.n < 0.3 * jumlah.maks, ["league", "season", "team"]].values))

playoff = d.apply(lambda r: (r.league, r.season, r.home_team) in tamu
                  or (r.league, r.season, r.away_team) in tamu, axis=1)
# Laga liga reguler hanya punya nomor pekan; play-off punya nama babak (mis. "Playoffs")
if "round_name" in d:
    playoff |= d.round_name.fillna("").str.contains("play|relegation|promotion|final", case=False)
# Serie A 2022/23: laga tiebreaker degradasi Spezia vs Hellas Verona
playoff |= (d.league == "Serie A") & (d.season == "2022/23") & (d.date_utc >= "2023-06-10")
playoff &= domestik
id_playoff = set(d.loc[playoff, "event_id"])

kw = dict(sep=";", index=False, encoding="utf-8-sig")
d[playoff].to_csv(folder / "laga_playoff_dibuang.csv", **kw)
d[~playoff].to_csv(folder / "sofascore_top5_reguler_per_laga.csv", **kw)
t[~t.event_id.isin(id_playoff)].to_csv(folder / "sofascore_top5_reguler_per_tim.csv", **kw)

print(f"Laga play-off dibuang: {playoff.sum()} (lihat laga_playoff_dibuang.csv)")
print(d[~playoff].groupby(["league", "season"]).size().unstack("season").to_string())
