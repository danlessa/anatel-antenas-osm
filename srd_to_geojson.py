#!/usr/bin/env python3
"""
srd_to_geojson.py

Convert an ANATEL SRD "plano basico" CSV (the TV/FM/OM channel dataset you pasted)
into GeoJSON, deduplicated per site.

Drops, per request: CNPJ/CPF, verbose service-name/application columns, and street
addresses. Keeps tower-relevant fields and Municipio/UF as light context.

Output properties (matching the MapComplete theme where it overlaps):
    estacao_id, servico, canal, frequencia_mhz, classe, erp_kw,
    altura_antena_m (from HCI), entidade, municipio, uf, status

IMPORTANT: rows with status "Canal Vago" are vacant channel allotments, NOT
physical stations. They are dropped by default. Use --keep-vacant to include them.
HCI is the height of the radiation centre (antenna), NOT the structure top.

Optional second dataset (e.g. TV_FM_OM.csv): there is no shared id, so matching is
SPATIAL (nearest neighbour within a metre threshold). Two modes:
  --filter-by-augment  keep only sites with a match in the second file
  (default)            keep all, and copy --aug-fields from the nearest match

Stdlib only (Python 3.8+). Auto-detects tab / ';' / ',' and UTF-8 / Latin-1.

Examples
--------
  # plano-basico only, real stations (vacant dropped):
  python3 srd_to_geojson.py plano_basico.csv -o srd.geojson

  # filter to sites that also exist in TV_FM_OM (you supply its lat/lon col names):
  python3 srd_to_geojson.py plano_basico.csv -o srd.geojson \
      --augment TV_FM_OM.csv --aug-lat "Latitude" --aug-lon "Longitude" \
      --filter-by-augment --match-meters 200

  # augment: copy fields from nearest TV_FM_OM row instead of filtering:
  python3 srd_to_geojson.py plano_basico.csv -o srd.geojson \
      --augment TV_FM_OM.csv --aug-lat "Latitude" --aug-lon "Longitude" \
      --aug-fields "Altura,Potencia,Entidade"

  # only FM and OM, São Paulo bbox:
  python3 srd_to_geojson.py plano_basico.csv -o srd.geojson \
      --servico FM,OM --bbox=-46.83,-24.01,-46.36,-23.36
"""

import argparse, csv, json, math, sys, unicodedata
from collections import defaultdict

# primary-file column resolution: target -> normalized header candidates
PRIMARY = {
    "id":        ["id"],
    "servico":   ["siglaservico", "servico"],
    "canal":     ["canal"],
    "freq":      ["frequencia", "freq", "frequenciamhz"],
    "classe":    ["classe"],
    "erp":       ["erp", "erpkw"],
    "height":    ["hci", "alturaantena", "altura"],
    "entidade":  ["entidade", "nomeentidade"],
    "municipio": ["srdplanobasiconomemunicipio", "nomemunicipio", "municipio"],
    "uf":        ["srdplanobasicosiglauf", "siglauf", "uf"],
    "status":    ["statusdescricao", "servicostatus", "siglastatus"],
    "lat":       ["latitudedecimalsrd", "latitudedecimal", "latitude", "lat"],
    "lon":       ["longitudedecimalsrd", "longitudedecimal", "longitude", "lon"],
}


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum())


def parse_num(s):
    if s is None:
        return None
    s = s.strip()
    if not s or s == "*":
        return None
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def detect_encoding(path):
    for enc in ("utf-8-sig", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                for _ in range(2000):
                    fh.readline()
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def detect_delimiter(path, enc):
    with open(path, encoding=enc) as fh:
        line = fh.readline()
    counts = {d: line.count(d) for d in ("\t", ";", ",")}
    return max(counts, key=counts.get)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def resolve(header, candidates, overrides):
    nh = {norm(h): h for h in header}
    out = {}
    for target, cands in candidates.items():
        forced = overrides.get(target)
        if forced:
            if forced not in header:
                sys.exit(f"--{target}-col '{forced}' not in header:\n{header}")
            out[target] = forced
        else:
            out[target] = next((nh[c] for c in cands if c in nh), None)
    return out


def open_reader(path, enc, delim):
    fh = open(path, encoding=enc, newline="")
    return fh, csv.reader(fh, delimiter=delim)


def build_augment_index(path, latcol, loncol, fields, meters, enc, delim):
    fh, reader = open_reader(path, enc, delim)
    header = next(reader)
    nh = {norm(h): h for h in header}
    def col(name):
        if name in header:
            return header.index(name)
        if norm(name) in nh:
            return header.index(nh[norm(name)])
        sys.exit(f"augment column '{name}' not found. Header:\n{header}")
    li, oi = col(latcol), col(loncol)
    fidx = {f: col(f) for f in fields}
    cell = meters / 111000.0
    grid = defaultdict(list)
    pts = []
    for row in reader:
        if li >= len(row) or oi >= len(row):
            continue
        lat, lon = parse_num(row[li]), parse_num(row[oi])
        if lat is None or lon is None:
            continue
        props = {f: (row[i].strip() if i < len(row) else "") for f, i in fidx.items()}
        idx = len(pts)
        pts.append((lat, lon, props))
        grid[(int(lat / cell), int(lon / cell))].append(idx)
    fh.close()
    return pts, grid, cell


def nearest(pts, grid, cell, lat, lon, meters):
    gy, gx = int(lat / cell), int(lon / cell)
    best, bestd = None, meters
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            for i in grid.get((gy + dy, gx + dx), ()):
                plat, plon, pr = pts[i]
                d = haversine(lat, lon, plat, plon)
                if d <= bestd:
                    best, bestd = pr, d
    return best, (bestd if best else None)


def main():
    p = argparse.ArgumentParser(description="ANATEL SRD plano-basico CSV -> GeoJSON")
    p.add_argument("csv_path")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat (use = form for negatives)")
    p.add_argument("--servico", help="keep only these SiglaServico values, e.g. TV,FM,OM")
    p.add_argument("--keep-vacant", action="store_true", help="keep 'Canal Vago' allotments")
    p.add_argument("--dedup-decimals", type=int, default=5)
    p.add_argument("--encoding")
    p.add_argument("--delimiter")
    for t in PRIMARY:
        p.add_argument(f"--{t}-col", dest=f"{t}_col")
    # augmentation
    p.add_argument("--augment", help="second CSV to match spatially (e.g. TV_FM_OM.csv)")
    p.add_argument("--aug-lat")
    p.add_argument("--aug-lon")
    p.add_argument("--aug-fields", default="", help="comma-separated columns to copy from match")
    p.add_argument("--aug-delimiter")
    p.add_argument("--match-meters", type=float, default=150.0)
    p.add_argument("--filter-by-augment", action="store_true",
                   help="keep only sites with a match in --augment")
    args = p.parse_args()

    bbox = None
    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))
        if len(bbox) != 4:
            sys.exit("--bbox must be minlon,minlat,maxlon,maxlat")
    keep_serv = {norm(s) for s in args.servico.split(",")} if args.servico else None

    enc = args.encoding or detect_encoding(args.csv_path)
    delim = args.delimiter or detect_delimiter(args.csv_path, enc)
    sys.stderr.write(f"primary: encoding={enc} delimiter={delim!r}\n")

    overrides = {t: getattr(args, f"{t}_col") for t in PRIMARY}

    aug_pts = aug_grid = aug_cell = None
    aug_fields = [f.strip() for f in args.aug_fields.split(",") if f.strip()]
    if args.augment:
        if not (args.aug_lat and args.aug_lon):
            sys.exit("--augment requires --aug-lat and --aug-lon")
        a_enc = detect_encoding(args.augment)
        a_delim = args.aug_delimiter or detect_delimiter(args.augment, a_enc)
        sys.stderr.write(f"augment: encoding={a_enc} delimiter={a_delim!r}\n")
        aug_pts, aug_grid, aug_cell = build_augment_index(
            args.augment, args.aug_lat, args.aug_lon, aug_fields, args.match_meters, a_enc, a_delim)
        sys.stderr.write(f"augment points indexed: {len(aug_pts):,}\n")

    sites = {}
    rows = skipped = dropped_vacant = 0
    dec = args.dedup_decimals

    fh, reader = open_reader(args.csv_path, enc, delim)
    header = next(reader)
    cols = resolve(header, PRIMARY, overrides)
    sys.stderr.write("column mapping: " + json.dumps(cols, ensure_ascii=False) + "\n")
    if not cols["lat"] or not cols["lon"]:
        sys.exit(f"Could not find lat/lon. Header:\n{header}\nUse --lat-col/--lon-col.")
    idx = {k: (header.index(v) if v else None) for k, v in cols.items()}

    def g(row, k):
        i = idx[k]
        return row[i].strip() if (i is not None and i < len(row)) else ""

    for row in reader:
        rows += 1
        lat, lon = parse_num(g(row, "lat")), parse_num(g(row, "lon"))
        if lat is None or lon is None:
            skipped += 1
            continue
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            continue
        serv = g(row, "servico")
        if keep_serv and norm(serv) not in keep_serv:
            continue
        status = g(row, "status")
        if not args.keep_vacant and "vago" in norm(status):
            dropped_vacant += 1
            continue

        key = (round(lat, dec), round(lon, dec))
        s = sites.get(key)
        if s is None:
            s = sites[key] = {"lat": lat, "lon": lon, "n": 0, "h": None,
                              "ids": set(), "serv": set(), "canais": set(),
                              "freqs": set(), "classe": set(),
                              "ent": set(), "muni": "", "uf": "", "status": set()}
        s["n"] += 1
        for fld, k in (("ids", "id"), ("serv", "servico"), ("canais", "canal"),
                       ("classe", "classe"), ("status", "status")):
            v = g(row, k)
            if v:
                s[fld].add(v)
        f = parse_num(g(row, "freq"))
        if f:
            s["freqs"].add(f)
        h = parse_num(g(row, "height"))
        if h and h > 0 and (s["h"] is None or h > s["h"]):
            s["h"] = h
        ent = g(row, "entidade")
        if ent:
            s["ent"].add(ent)
        s["muni"] = s["muni"] or g(row, "municipio")
        s["uf"] = s["uf"] or g(row, "uf")
    fh.close()

    matched = 0
    with open(args.output, "w", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for s in sites.values():
            aug = None
            if aug_pts is not None:
                aug, _ = nearest(aug_pts, aug_grid, aug_cell, s["lat"], s["lon"], args.match_meters)
                if args.filter_by_augment and aug is None:
                    continue
                if aug:
                    matched += 1
            sid = sorted(s["ids"])[0] if s["ids"] else f'{s["lat"]:.5f}_{s["lon"]:.5f}'
            props = {"estacao_id": sid, "estacoes_no_local": s["n"]}
            if s["serv"]:
                props["servico"] = "/".join(sorted(s["serv"]))
            if s["canais"]:
                props["canal"] = "/".join(sorted(s["canais"]))
            if s["freqs"]:
                lo, hi = min(s["freqs"]), max(s["freqs"])
                props["frequencia_mhz"] = f"{lo:g}" if lo == hi else f"{lo:g}\u2013{hi:g}"
            if s["classe"]:
                props["classe"] = "/".join(sorted(s["classe"]))
            if s["h"] is not None:
                props["altura_antena_m"] = round(s["h"], 1)
            if s["ent"]:
                props["entidade"] = sorted(s["ent"])[0]
            if s["muni"]:
                props["municipio"] = s["muni"]
            if s["uf"]:
                props["uf"] = s["uf"]
            if s["status"]:
                props["status"] = "/".join(sorted(s["status"]))
            if aug:
                for k, v in aug.items():
                    if v:
                        props[f"match_{k}"] = v
            feat = {"type": "Feature", "id": f"srd-{sid}", "properties": props,
                    "geometry": {"type": "Point",
                                 "coordinates": [round(s["lon"], 7), round(s["lat"], 7)]}}
            out.write(("" if first else ",\n") + json.dumps(feat, ensure_ascii=False))
            first = False
        out.write("\n]}\n")

    sys.stderr.write(
        f"\nrows read: {rows:,}\nskipped (no coords): {skipped:,}\n"
        f"dropped vacant (Canal Vago): {dropped_vacant:,}\n"
        f"sites emitted: {sum(1 for _ in sites):,} (before augment filter)\n"
        + (f"sites with augment match: {matched:,}\n" if aug_pts is not None else "")
        + f"output: {args.output}\n")


if __name__ == "__main__":
    main()
