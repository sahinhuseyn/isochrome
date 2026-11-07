#!/usr/bin/env python3
"""Download central Baku streets + named places from OpenStreetMap (Overpass)
and write a compact baku.json for the Reach page.

Usage:
    python prepare_baku.py                       # default central-Baku bbox -> baku.json
    python prepare_baku.py --bbox 40.35 49.79 40.43 49.90 --out baku.json

Standard library only. Overpass is slow, so the area is split into tiles and each
tile is retried; expect several minutes. Streets are walkable ways only (same
filter the page used), so this file is for walking mode.
"""
import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# S, W, N, E  (central Baku: seafront to Nərimanov, Yasamal to the coast)
DEFAULT_BBOX = (40.350, 49.790, 40.430, 49.900)
DEFAULT_TILE = (0.02, 0.03)  # degrees lat, lon (~2.2 km x 2.5 km)

KEEP_TAGS = [
    "name", "name:az", "name:en", "amenity", "shop", "leisure", "railway", "station",
    "subway", "public_transport", "tourism", "historic", "wikidata", "wikipedia",
    "website", "contact:website", "opening_hours", "brand", "phone", "contact:phone",
    "instagram", "contact:instagram",
]

WAY_Q = """[out:json][timeout:90];
way["highway"]["highway"!~"motorway|motorway_link|trunk|trunk_link|construction|proposed|raceway|bus_guideway"]
["foot"!="no"]["access"!~"private|no"]({s},{w},{n},{e});
out geom;"""

POI_Q = """[out:json][timeout:90];
(
  nwr["amenity"~"^(cafe|restaurant|fast_food|food_court)$"]({s},{w},{n},{e});
  nwr["shop"~"^(beauty|hairdresser|cosmetics)$"]({s},{w},{n},{e});
  nwr["leisure"~"^(park|garden)$"]({s},{w},{n},{e});
  nwr["railway"="station"]["station"="subway"]({s},{w},{n},{e});
  nwr["railway"="station"]["subway"="yes"]({s},{w},{n},{e});
  nwr["public_transport"="station"]["subway"="yes"]({s},{w},{n},{e});
  nwr["tourism"~"^(attraction|museum|gallery|viewpoint)$"]({s},{w},{n},{e});
  nwr["historic"]({s},{w},{n},{e});
);
out center tags;"""


def overpass(query, retries=4):
    last = None
    for attempt in range(retries):
        for url in OVERPASS:
            try:
                req = urllib.request.Request(
                    url,
                    data=("data=" + urllib.parse.quote(query)).encode(),
                    headers={"User-Agent": "reach-prototype/0.1"},
                )
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.load(r)["elements"]
            except Exception as e:  # noqa: BLE001 - any failure -> try next mirror
                last = e
                print(f"   retry ({url.split('/')[2]}): {e}", file=sys.stderr)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Overpass failed after {retries} rounds: {last}")


def tiles(s, w, n, e, dlat, dlon):
    lat = s
    while lat < n - 1e-9:
        lon = w
        while lon < e - 1e-9:
            yield (round(lat, 6), round(lon, 6), round(min(lat + dlat, n), 6), round(min(lon + dlon, e), 6))
            lon += dlon
        lat += dlat


def build_graph(ways):
    """ways: Overpass way elements with geometry. Returns (lat, lon, edges) with
    shared coordinates merged into one node and ways seen in several tiles counted once."""
    index, lat, lon, edges, seen = {}, [], [], [], set()

    def node_id(p):
        key = (round(p["lat"], 7), round(p["lon"], 7))
        i = index.get(key)
        if i is None:
            i = len(lat)
            index[key] = i
            lat.append(round(p["lat"], 6))
            lon.append(round(p["lon"], 6))
        return i

    for w in ways:
        if w.get("type") != "way" or "geometry" not in w:
            continue
        if w["id"] in seen:
            continue
        seen.add(w["id"])
        prev = None
        for p in w["geometry"]:
            cur = node_id(p)
            if prev is not None and prev != cur:
                edges += [prev, cur]
            prev = cur
    return lat, lon, edges


def compact_pois(elements):
    """Named places only, as node-like records the page's ranking code reads directly."""
    out, seen = [], set()
    for el in elements:
        key = (el["type"], el["id"])
        if key in seen:
            continue
        seen.add(key)
        tags = el.get("tags", {})
        if not (tags.get("name") or tags.get("name:az") or tags.get("name:en")):
            continue
        if el["type"] == "node":
            la, lo = el["lat"], el["lon"]
        else:
            c = el.get("center")
            if not c:
                continue
            la, lo = c["lat"], c["lon"]
        out.append({
            "type": "node", "lat": round(la, 6), "lon": round(lo, 6),
            "tags": {k: tags[k] for k in KEEP_TAGS if k in tags},
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("S", "W", "N", "E"), default=DEFAULT_BBOX)
    ap.add_argument("--tile", nargs=2, type=float, metavar=("DLAT", "DLON"), default=DEFAULT_TILE)
    ap.add_argument("--out", default="baku.json")
    a = ap.parse_args()
    s, w, n, e = a.bbox
    grid = list(tiles(s, w, n, e, *a.tile))
    ways, pois = [], []
    t0 = time.time()
    for i, (ts, tw, tn, te) in enumerate(grid, 1):
        box = dict(s=ts, w=tw, n=tn, e=te)
        wy = overpass(WAY_Q.format(**box))
        po = overpass(POI_Q.format(**box))
        ways += wy
        pois += po
        print(f"[{i}/{len(grid)}] tile {ts},{tw}: {len(wy)} ways, {len(po)} places  ({time.time() - t0:.0f}s)")
        time.sleep(1)
    lat, lon, edges = build_graph(ways)
    places = compact_pois(pois)
    data = {"v": 1, "bbox": [s, w, n, e], "lat": lat, "lon": lon, "edges": edges, "pois": places}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    mb = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()) / 1e6
    print(f"wrote {a.out}: {len(lat)} nodes, {len(edges) // 2} edges, {len(places)} places, {mb:.1f} MB")


if __name__ == "__main__":
    main()
