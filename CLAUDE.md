# CLAUDE.md — ANATEL → OSM comms-tower heights

Context for continuing this project in Claude Code. Read before editing.

## Goal
Update communication-tower `height=` in OpenStreetMap using a **MapComplete
read-only-reference** workflow: editable OSM towers on one layer, ANATEL
licensed-station data shown as a non-editable reference layer. The mapper reads
the nearest ANATEL value and transcribes `height` **with judgement** — no bulk
import / auto-conflation.

## Non-negotiable caveats (do not "optimize away")
- **Antenna ≠ structure height.** ANATEL `AlturaAntena` / SRD `HCI` are the
  antenna phase-centre / radiation-centre height, NOT the tower top. Surfaced as
  a hint only. Never auto-copy into OSM `height=`.
- **Vacant ≠ physical.** SRD plano-básico rows with status `Canal Vago` are
  channel allotments with no transmitter. Dropped by default in `srd_to_geojson.py`.
- **Service 099 (radiação restrita / isentas)** = ISM-band WISP gear on existing
  rooftops/poles, NOT freestanding towers. Don't map as `man_made=tower`.
- **License:** ANATEL data is public domain — OSM-compatible. Still required:
  run a small verified pilot and clear a bulk addition with **talk-br** + the OSM
  import guidelines before scaling.
- **CORS:** MapComplete fetches theme + GeoJSON cross-origin; host with
  `Access-Control-Allow-Origin: *` (GitHub Pages / raw.githubusercontent both do).

## Files in this repo
- `anatel_tower_heights.json` — MapComplete theme. Two layers:
  - `osm_tower` (editable): `source.osmTags = man_made=tower`; `height` question;
    `communication:*` multi-answer; marker coloured black/red(TV)/blue(radio)/
    green(mobile), **first-match-wins** (reorder mappings to change precedence).
    Uses `calculatedTags` + `feat.closest('anatel_reference')` to show the nearest
    reference height inside the tower popup.
  - `anatel_reference` (read-only): `source.geoJson = <YOUR HOSTED URL>`.
    Renders `altura_antena_m`, `servico`, `entidade`, `frequencia_mhz`.
  - GeoJSON property names the theme expects: `estacao_id`, `altura_antena_m`,
    `servico`, `entidade`, `frequencia_mhz`.
- `anatel-towers-sample.geojson` — 3-feature SP test fixture.
- `anatel_to_geojson.py` — converts **Estações Licenciadas** CSVs (SMP, etc.).
  Columns: `Latitude/Longitude` (decimal, may use comma), `AlturaAntena`,
  `NumEstacao`, `NomeEntidade`, `NumServico`, `FreqTxMHz`, `Tecnologia`.
  Dedups per site (rounded coord, keeps MAX height). `;`-separated, ~4.3M rows (SMP).
- `srd_to_geojson.py` — converts the **SRD plano-básico** TV/FM/OM CSV. Decimal
  coords present (`Latitude/Longitude Decimal SRD`). `HCI` → `altura_antena_m`.
  Drops vacant channels + CNPJ + addresses. Has `--servico TV,FM,OM` filter and a
  grid-indexed **spatial nearest-neighbour** augment/filter against a second CSV.

Both scripts: stdlib only; auto-detect tab/`;`/`,` and UTF-8/Latin-1; parse
comma-decimals; stream large files; print column mapping + summary to stderr;
`--bbox` needs the `=` form for negatives (`--bbox=-46.83,-24.01,-46.36,-23.36`).

## Coordinate formats seen
- SRD plano-básico: decimal (`-9.7525`) — no decode needed.
- Estações Licenciadas: decimal (comma or dot).
- Estações Isentas / radiação restrita: **packed DMS** `22S232520` =
  `DD[NSEW]MMSSHH` → `±(DD + MM/60 + (SS+HH/100)/3600)`, neg for S/W.
  (Not yet implemented — needed only if that dataset is used.)

## Run
```
# Estações Licenciadas (mobile etc.)
python3 anatel_to_geojson.py Estacoes_Licenciadas_SMP.csv -o anatel.geojson \
  --bbox=-46.83,-24.01,-46.36,-23.36

# SRD plano-básico TV/FM/OM
python3 srd_to_geojson.py plano_basico.csv -o srd.geojson --servico TV,FM,OM

# Load theme (after hosting both files, CORS-enabled):
#   https://mapcomplete.org/theme.html?userlayout=<theme-json-url>
#   approve the "custom JavaScript" prompt (calculatedTags), log in with OSM
# Or validate/host via https://mapcomplete.org/studio
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
1. **TV_FM_OM spatial join** — need its header (lat/lon col names; decimal or DMS?).
   Then wire `--augment TV_FM_OM.csv --aug-lat <c> --aug-lon <c>`
   (`--filter-by-augment` to keep only matches, or `--aug-fields` to copy fields).
   Tune `--match-meters` (plano-básico coords are often pre-fixed → tens–hundreds m off).
2. Confirm the **live SMP/Estações resource URL** from the catalog page.
3. Verify theme validates on current MapComplete schema (`pointRendering` vs older
   `mapRendering`); fix in Studio if flagged.
4. Fill `SERVICO_LABELS` in `anatel_to_geojson.py` once NumServico codes confirmed.
5. Implement packed-DMS decoder if the isentas/radiação-restrita base is used.
6. Pilot one município, eyeball vs map, then take to talk-br before scaling.
