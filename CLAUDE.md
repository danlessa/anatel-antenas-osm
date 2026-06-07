# CLAUDE.md — ANATEL → OSM comms-tower heights

Context for continuing this project in Claude Code. Read before editing.

## Goal
Update communication-tower `height=` in OpenStreetMap using a **MapComplete
read-only-reference** workflow: editable OSM towers on one layer, ANATEL
licensed-station data shown as non-editable reference layers. The mapper reads
the nearest ANATEL value and transcribes `height` **with judgement** — no bulk
import / auto-conflation.

## Non-negotiable caveats (do not "optimize away")
- **Antenna ≠ structure height.** ANATEL `AlturaAntena` / SRD `HCI` are the
  antenna phase-centre / radiation-centre height, NOT the tower top. Surfaced as
  a hint only. Never auto-copy into OSM `height=`.
- **Vacant ≠ physical.** SRD plano-básico rows with status `Canal Vago` are
  channel allotments with no transmitter. Dropped by default in `srd_to_geojson.py`.
- **Service 099 (radiação restrita / isentas)** = ISM-band WISP gear on existing
  rooftops/poles, NOT freestanding towers. Excluded by the default allowlist in
  `estacoes_geral_to_geojson.py`. Don't map as `man_made=tower`.
- **License:** ANATEL data is public domain — OSM-compatible. Still required:
  run a small verified pilot and clear a bulk addition with **talk-br** + the OSM
  import guidelines before scaling.
- **CORS:** MapComplete fetches theme + GeoJSON cross-origin; host with
  `Access-Control-Allow-Origin: *` (GitHub raw + jsDelivr both do).

## Files in this repo
- `anatel_tower_heights.json` — MapComplete theme (**exists, hosted, loads**).
  Three layers:
  - `anatel_reference` (read-only, orange): `source.geoJson` → hosted `srd.geojson`
    (TV/FM/OM). Renders `altura_antena_m`, `servico`, `canal`, `frequencia_mhz`,
    `municipio`/`uf`, `estacao_id`.
  - `anatel_licenciadas` (read-only, purple): `source.geoJson` → hosted
    `estacoes_geral.geojson` (mobile/aux). Renders `altura_antena_m`, `servico`,
    `frequencia_mhz`, `municipio`/`uf`, `estacoes_no_local`.
  - `osm_tower` (editable): `source.osmTags = man_made=tower`; `height` question;
    `communication:*` multi-answer (mobile_phone / television / radio=yes — single
    `radio` key so FM/AM don't collide); marker coloured black/red(TV)/blue(radio)/
    green(mobile), **first-match-wins** (reorder mappings to change precedence).
    Uses `calculatedTags` (lazy) `closest(feat)('anatel_reference')?.properties?.*`
    to show the nearest reference height/service/distance inside the popup.
  - Theme `icon` is served via jsDelivr (GitHub raw serves `.svg` as `text/plain`,
    which `<img>` won't render): `cdn.jsdelivr.net/gh/<repo>@main/assets/tower.svg`.
  - GeoJSON property names the theme expects: `estacao_id`, `altura_antena_m`,
    `servico`, `canal`, `frequencia_mhz`, `entidade`, `municipio`, `uf`,
    `estacoes_no_local`.
- `assets/tower.svg` — theme icon (red tower glyph).
- `srd_to_geojson.py` — converts the **SRD plano-básico** TV/FM/OM CSV.
  **The actual file is `data/TV_FM_OM.csv`** (despite the name it IS the
  plano-básico: has `SiglaServico`, `Canal`, `HCI`, `Latitude/Longitude Decimal
  SRD`, `Status Descrição`). Decimal coords. `HCI` → `altura_antena_m`. Drops
  vacant channels + CNPJ + addresses. `--servico TV,FM,OM` filter. Also has a
  grid-indexed **spatial nearest-neighbour** augment/filter against a second CSV
  (`--augment`), currently unused — only works if the second CSV has decimal coords.
- `estacoes_geral_to_geojson.py` — converts the **Estações Licenciadas** general
  dump (`data/estacoes_licenciadas/Estacoes_Geral.csv`, ~1.3M rows, 304 MB).
  Columns: `Número da Estação`, `Aplicação do Serviço`, `Nome/Razão Social`,
  `Freq. Transmissão`, `Altura`, `Latitude/Longitude`. **Packed-DMS coords**
  (decoded by `parse_coord`). Dedups per site (rounded coord, keeps MAX height).
  Drops CNPJ/addresses. `DEFAULT_CODES` = tower-plausible `Aplicação do Serviço`
  3-digit allowlist (`--servico-codes` overrides, `--keep-all-services` disables);
  `--no-brazil-bounds` disables the Brazil bbox sanity filter. Logs kept/dropped
  counts by service code.

Both scripts: stdlib only; auto-detect tab/`;`/`,` and UTF-8/Latin-1; parse
comma-decimals; stream large files; print column mapping + summary to stderr;
`--bbox` needs the `=` form for negatives (`--bbox=-46.83,-24.01,-46.36,-23.36`).

### Generated outputs (tracked in git, this is what we host)
- `data/srd.geojson` — 15,093 sites (TV/FM/OM, vacant dropped).
- `data/estacoes_geral.geojson` — 8,819 tower-plausible sites.

`.gitignore` excludes raw ANATEL CSVs (`data/**/*.csv` — multi-GB, incl. a 4.6 GB
`Estacoes_Mosaico_STEL.csv`) and `.DS_Store`. The generated `*.geojson` ARE tracked.

## Hosted URLs (CORS-enabled)
- Theme: `https://raw.githubusercontent.com/danlessa/anatel-antenas-osm/main/anatel_tower_heights.json`
- SRD ref: `https://raw.githubusercontent.com/danlessa/anatel-antenas-osm/main/data/srd.geojson`
- Licensed ref: `https://raw.githubusercontent.com/danlessa/anatel-antenas-osm/main/data/estacoes_geral.geojson`
- Load: `https://mapcomplete.org/theme.html?userlayout=<theme-url>` — approve the
  "custom JavaScript" prompt (calculatedTags), log in with OSM.

## Coordinate formats seen
- SRD plano-básico (`data/TV_FM_OM.csv`): decimal (`-9.7525`) — no decode needed.
- Estações Licenciadas decimal variants (SMP etc.): decimal (comma or dot).
- Estações Geral / Isentas / radiação restrita: **packed DMS** `22S232520` =
  `DD[NSEW]MMSSHH` → `±(DD + MM/60 + (SS+HH/100)/3600)`, neg for S/W (HH =
  hundredths of a second). **Implemented** in `estacoes_geral_to_geojson.py`
  (`parse_coord`). ~25 source sites have swapped/corrupt coords; the Brazil bbox
  sanity filter drops out-of-country points (a few near-border corrupt ones slip
  through — a rectangular filter can't catch all without dropping real islands).

## Run
```
# SRD plano-básico TV/FM/OM (data/TV_FM_OM.csv)
python3 srd_to_geojson.py data/TV_FM_OM.csv -o data/srd.geojson

# Estações Licenciadas general dump (packed DMS, tower-plausible services)
python3 estacoes_geral_to_geojson.py \
  data/estacoes_licenciadas/Estacoes_Geral.csv -o data/estacoes_geral.geojson

# after editing geojson, commit + push so the hosted raw URLs update, then
# load the theme via the Load URL above (or validate via mapcomplete.org/studio)
```

## Data sources
- Catalog (live download links, JS-rendered, hashed filenames):
  `dados.gov.br/dados/conjuntos-dados/outorga-e-licenciamento---estaes-licenciadas`
  and `...---estacoes-do-smp`.
- Machine-readable index of all bases:
  `anatel.gov.br/dadosabertos/PDA/Bases_Publicadas/Inventario_de_Bases_de_Dados.csv`
- The old flat `anatel.gov.br/dadosabertos/PDA/Estacoes_Licenciadas/*.csv` paths
  are stale (404).

## Open tasks / TODO
1. **Verify theme on current MapComplete schema** — built against `pointRendering`/
   `marker` (not old `mapRendering`). On first load, eyeball: `multiAnswer` block,
   `condition` syntax, and the marker `color.mappings`. Fix in Studio if flagged.
2. **Check calculated `_anatel_dist` units** — assumed `distanceTo` returns metres;
   if it looks ×1000 off, adjust that one calculatedTag (height/service hint is fine
   regardless).
3. **Pilot one município**, eyeball reference vs map, then take to **talk-br** before
   any scaling.
4. (Optional) Widen `osm_tower` source to include `man_made=mast`; finer
   `communication:radio=fm`/`=am`; per-state bbox to catch the last corrupt coords.
5. (Optional) If a decimal-coord Estações Licenciadas (e.g. SMP) base is added,
   `srd_to_geojson.py`'s `--augment` spatial join can cross-reference it.
