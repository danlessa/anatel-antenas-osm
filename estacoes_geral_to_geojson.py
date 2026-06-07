#!/usr/bin/env python3
"""
estacoes_geral_to_geojson.py

Convert the ANATEL "Estacoes_Geral" Estacoes Licenciadas CSV (the big general
licensed-stations dump, ~1.3M rows) into a digestible GeoJSON, deduplicated per
site, keeping only tower-plausible land-mobile / broadcast-auxiliary services.

Why this is a separate script from anatel_to_geojson / srd_to_geojson:
  - Coordinates here are PACKED DMS ("22S232520" / "54W303080"), not decimal.
    Format DD[NSEW]MMSSHH -> +-(DD + MM/60 + (SS + HH/100)/3600), neg for S/W.
    (HH = hundredths of a second.) Decoded by parse_coord() below.
  - The file mixes ~30 services; most rows are VSAT / limited-private gear that
    is NOT on freestanding towers. We keep only a tower-plausible allowlist by
    default (--servico-codes to override, --keep-all-services to disable).

CAVEATS (see CLAUDE.md):
  - "Altura" here is the ANTENNA height, not the structure top. Surfaced as a
    hint only (property altura_antena_m). Never auto-copy into OSM height=.
  - Service 099 (radiacao restrita / isentas) = ISM rooftop/pole gear, never a
    tower. Excluded by the default allowlist.

Output properties (matching the MapComplete theme where it overlaps):
    estacao_id, estacoes_no_local, servico, frequencia_mhz, altura_antena_m,
    entidade, municipio, uf

Dropped per request: CNPJ/CPF, Bairro/Logradouro (addresses), and the verbose
emission/power/geometry columns.

Stdlib only (Python 3.8+). Auto-detects tab / ';' / ',' and UTF-8 / Latin-1.

Examples
--------
  # default: tower-plausible services, deduped per site
  python3 estacoes_geral_to_geojson.py data/estacoes_licenciadas/Estacoes_Geral.csv \
      -o data/estacoes_geral.geojson

  # keep a custom set of Aplicacao-do-Servico codes
  python3 estacoes_geral_to_geojson.py Estacoes_Geral.csv -o out.geojson \
      --servico-codes 251,252,604,507

  # everything (no service filter), Sao Paulo bbox
  python3 estacoes_geral_to_geojson.py Estacoes_Geral.csv -o out.geojson \
      --keep-all-services --bbox=-46.83,-24.01,-46.36,-23.36
"""

import argparse, csv, json, re, sys, unicodedata
from collections import defaultdict

# column resolution: target -> normalized header candidates
COLS = {
    "id":        ["numerodaestacao", "numestacao", "id"],
    "servico":   ["aplicacaodoservico", "nomedoservico"],
    "entidade":  ["nomerazaosocial", "nomeentidade", "entidade"],
    "municipio": ["municipio"],
    "uf":        ["uf"],
    "freq":      ["freqtransmissao", "freqtx", "freqtransmissão"],
    "height":    ["altura"],
    "lat":       ["latitude", "lat"],
    "lon":       ["longitude", "lon"],
}

# default tower-plausible "Aplicacao do Servico" 3-digit codes:
# land-mobile dispatch / trunking, radiotaxi, paging, broadcast-auxiliary links,
# maritime & aeronautical base stations. Excludes satellite, limited-private
# point-to-point, supervision/telemetry, and 099 radiacao restrita.
DEFAULT_CODES = {
    "020",  # Servico Movel Especializado
    "023",  # Servico Limitado Movel Privativo (SLMP)
    "033",  # Radioenlaces associados ao radiotaxi
    "034",  # SLMP a grupos de usuarios
    "050",  # Limitado Privado inclusao digital 2,5/3,5 GHz
    "051",  # Especial de Radiochamada
    "060",  # Limitado Privado de Radiochamada (SLPR)
    "076",  # Servico de Rede Privado
    "078",  # Radiotaxi Privado
    "079",  # Radiotaxi Especializado
    "251",  # Auxiliar Radiodif. - transmissao de programas
    "252",  # Auxiliar Radiodif. - reportagem externa
    "253",  # Auxiliar Radiodif. - comunicacao de ordens internas
    "255",  # Auxiliar Radiodif. - telemedicao
    "507",  # Movel Aeronautico
    "604",  # Movel Maritimo
}

# Sanity bounds for Brazil incl. oceanic islands (Fernando de Noronha -32.4,
# Trindade -29.3, Sao Pedro/Sao Paulo). A small fraction of source rows have
# lat/lon swapped or corrupt; these are dropped by default (--no-brazil-bounds).
BRAZIL_BBOX = (-74.5, -34.0, -28.8, 5.5)  # minlon, minlat, maxlon, maxlat


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


_DMS_RE = re.compile(r"^\s*(\d{1,3})\s*([NSEWnsew])\s*(\d{2})(\d{2})(\d{2})\s*$")


def parse_coord(s):
    """Packed DMS 'DD[NSEW]MMSSHH' -> signed decimal degrees. Falls back to
    plain decimal if the value is not packed DMS."""
    if s is None:
        return None
    s = s.strip()
    if not s:
        return None
    m = _DMS_RE.match(s)
    if not m:
        return parse_num(s)
    deg = int(m.group(1))
    hemi = m.group(2).upper()
    mm, ss, hh = int(m.group(3)), int(m.group(4)), int(m.group(5))
    val = deg + mm / 60.0 + (ss + hh / 100.0) / 3600.0
    if hemi in ("S", "W"):
        val = -val
    return val


_FREQ_UNIT = {"ghz": 1000.0, "mhz": 1.0, "khz": 0.001, "hz": 1e-6}


def parse_freq_mhz(s):
    """'2400,00000000 MHz' / '750 kHz' / '11,2 GHz' -> float MHz."""
    if not s:
        return None
    s = s.strip()
    mult = 1.0
    low = s.lower()
    for u, m in _FREQ_UNIT.items():
        if low.endswith(u):
            mult = m
            s = s[: -len(u)]
            break
    n = parse_num(s)
    return n * mult if n is not None else None


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


def servico_code(s):
    """Leading 3-digit code of 'Aplicacao do Servico', e.g. '604 - Movel...' -> '604'."""
    if not s:
        return None
    m = re.match(r"\s*(\d{3})", s)
    return m.group(1) if m else None


def main():
    p = argparse.ArgumentParser(description="ANATEL Estacoes_Geral CSV -> GeoJSON")
    p.add_argument("csv_path")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--bbox", help="minlon,minlat,maxlon,maxlat (use = form for negatives)")
    p.add_argument("--servico-codes", help="comma-separated Aplicacao-do-Servico codes to keep "
                                           "(default: tower-plausible allowlist)")
    p.add_argument("--keep-all-services", action="store_true", help="disable the service filter")
    p.add_argument("--no-brazil-bounds", action="store_true",
                   help="keep points outside the Brazil sanity bbox (swapped/corrupt coords)")
    p.add_argument("--dedup-decimals", type=int, default=5)
    p.add_argument("--encoding")
    p.add_argument("--delimiter")
    for t in COLS:
        p.add_argument(f"--{t}-col", dest=f"{t}_col")
    args = p.parse_args()

    bbox = None
    if args.bbox:
        bbox = tuple(float(x) for x in args.bbox.split(","))
        if len(bbox) != 4:
            sys.exit("--bbox must be minlon,minlat,maxlon,maxlat")

    if args.keep_all_services:
        keep_codes = None
    elif args.servico_codes:
        keep_codes = {c.strip() for c in args.servico_codes.split(",") if c.strip()}
    else:
        keep_codes = set(DEFAULT_CODES)

    enc = args.encoding or detect_encoding(args.csv_path)
    delim = args.delimiter or detect_delimiter(args.csv_path, enc)
    sys.stderr.write(f"encoding={enc} delimiter={delim!r}\n")

    overrides = {t: getattr(args, f"{t}_col") for t in COLS}

    fh = open(args.csv_path, encoding=enc, newline="")
    reader = csv.reader(fh, delimiter=delim)
    header = next(reader)
    cols = resolve(header, COLS, overrides)
    sys.stderr.write("column mapping: " + json.dumps(cols, ensure_ascii=False) + "\n")
    if not cols["lat"] or not cols["lon"]:
        sys.exit(f"Could not find lat/lon. Header:\n{header}\nUse --lat-col/--lon-col.")
    idx = {k: (header.index(v) if v else None) for k, v in cols.items()}

    def g(row, k):
        i = idx[k]
        return row[i].strip() if (i is not None and i < len(row)) else ""

    br = None if args.no_brazil_bounds else BRAZIL_BBOX

    sites = {}
    rows = skipped = dropped_serv = filtered_bbox = dropped_oob = 0
    kept_by_code = defaultdict(int)
    dropped_by_code = defaultdict(int)
    dec = args.dedup_decimals

    for row in reader:
        rows += 1
        serv = g(row, "servico")
        code = servico_code(serv)
        if keep_codes is not None and code not in keep_codes:
            dropped_serv += 1
            dropped_by_code[code or "?"] += 1
            continue
        lat, lon = parse_coord(g(row, "lat")), parse_coord(g(row, "lon"))
        if lat is None or lon is None:
            skipped += 1
            continue
        if br and not (br[0] <= lon <= br[2] and br[1] <= lat <= br[3]):
            dropped_oob += 1
            continue
        if bbox and not (bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]):
            filtered_bbox += 1
            continue
        kept_by_code[code or "?"] += 1

        key = (round(lat, dec), round(lon, dec))
        s = sites.get(key)
        if s is None:
            s = sites[key] = {"lat": lat, "lon": lon, "n": 0, "h": None,
                              "ids": set(), "serv": set(), "freqs": set(),
                              "ent": set(), "muni": "", "uf": ""}
        s["n"] += 1
        sid = g(row, "id")
        if sid:
            s["ids"].add(sid)
        if serv:
            s["serv"].add(serv)
        f = parse_freq_mhz(g(row, "freq"))
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

    with open(args.output, "w", encoding="utf-8") as out:
        out.write('{"type":"FeatureCollection","features":[\n')
        first = True
        for s in sites.values():
            sid = sorted(s["ids"])[0] if s["ids"] else f'{s["lat"]:.5f}_{s["lon"]:.5f}'
            props = {"estacao_id": sid, "estacoes_no_local": s["n"]}
            if s["serv"]:
                props["servico"] = "/".join(sorted(s["serv"]))
            if s["freqs"]:
                lo, hi = min(s["freqs"]), max(s["freqs"])
                props["frequencia_mhz"] = f"{lo:g}" if lo == hi else f"{lo:g}–{hi:g}"
            if s["h"] is not None:
                props["altura_antena_m"] = round(s["h"], 1)
            if s["ent"]:
                props["entidade"] = sorted(s["ent"])[0]
            if s["muni"]:
                props["municipio"] = s["muni"]
            if s["uf"]:
                props["uf"] = s["uf"]
            feat = {"type": "Feature", "id": f"geral-{sid}", "properties": props,
                    "geometry": {"type": "Point",
                                 "coordinates": [round(s["lon"], 7), round(s["lat"], 7)]}}
            out.write(("" if first else ",\n") + json.dumps(feat, ensure_ascii=False))
            first = False
        out.write("\n]}\n")

    sys.stderr.write(
        f"\nrows read: {rows:,}\n"
        f"dropped (service not in allowlist): {dropped_serv:,}\n"
        f"skipped (no/invalid coords): {skipped:,}\n"
        f"dropped (outside Brazil bounds): {dropped_oob:,}\n"
        f"filtered out by bbox: {filtered_bbox:,}\n"
        f"sites emitted: {len(sites):,}\n"
        f"output: {args.output}\n")
    if keep_codes is not None:
        sys.stderr.write("\nkept rows by service code:\n")
        for c, n in sorted(kept_by_code.items(), key=lambda kv: -kv[1]):
            sys.stderr.write(f"  {c}: {n:,}\n")
        top_dropped = sorted(dropped_by_code.items(), key=lambda kv: -kv[1])[:10]
        sys.stderr.write("top dropped service codes (not in allowlist):\n")
        for c, n in top_dropped:
            sys.stderr.write(f"  {c}: {n:,}\n")


if __name__ == "__main__":
    main()
