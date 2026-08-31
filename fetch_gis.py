#!/usr/bin/env python3
"""
Fetch public-domain GIS data for an area drawn in geojson.io.

Reads the rectangle you drew, works out its bounding box, picks a data source
based on where that box is, and downloads what the importer needs.

UNITED STATES  (source: federal, public domain)
  buildings.geojson  USA Structures (FEMA / Oak Ridge / USGS) — carries
                     OCC_CLS / PRIM_OCC occupancy class
  roads.geojson      Census TIGER/Line

  TIGERweb splits roads across layers 0-8 by class and scale range. Querying a
  single layer silently returns zero features for any road not in that class,
  which looks identical to "there is no road here". This queries all nine and
  merges, deduplicating on geometry.

JAPAN  (source: OpenStreetMap via Overpass, ODbL)
  buildings.geojson  building=* ways and relations. OSM tags are mapped onto
                     the same OCC_CLS / PRIM_OCC fields the US path produces,
                     so the importer does not care which country it came from.
  roads.geojson      highway=*
  water.geojson      waterway=* and natural=water, tagged with NHD-style fcode
                     so GisImport.waterWidth() picks a channel width
  landuse.geojson    landuse=* and leisure=park

  Water is consumed: GisImport auto-discovers water.geojson beside
  buildings.geojson and rasterises it as Cover.WATER. Landuse is not — Cover
  has no landcover type yet, so the file is written for when it does.

Source selection is automatic, from the bounding box. Anything outside the US
falls through to OSM, which has global coverage, so drawing a box in Germany or
Brazil will work too — only the US has a second, better-attributed source.

Everything stays on your machine. Nothing is uploaded anywhere; the only
outbound requests are the public data queries.

    python3 fetch_gis.py ~/pzgis/area.geojson ~/pzgis
    python3 fetch_gis.py ~/pzgis/area.geojson ~/pzgis --source osm
"""

import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# --- US sources ---------------------------------------------------------

BUILDINGS = ("https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/"
             "USA_Structures_View/FeatureServer/0/query")
ROADS_BASE = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
              "Transportation/MapServer")
ROAD_LAYERS = range(0, 9)

# Rough continental-US envelope, plus Alaska and Hawaii. Only used to decide
# which service to ask; a box that straddles the edge just picks OSM, which
# still works.
US_BOXES = [
    (-125.0, 24.4, -66.9, 49.4),    # continental
    (-179.2, 51.2, -129.9, 71.4),   # Alaska
    (-160.3, 18.9, -154.8, 22.3),   # Hawaii
]

# --- OSM source ---------------------------------------------------------

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

JAPAN_BOX = (122.9, 24.0, 153.99, 45.6)

# OSM tag -> OCC_CLS. First match wins, so the specific keys (amenity, shop)
# are checked before the generic building value. OCC_CLS vocabulary is the one
# USA Structures uses, because the importer already reads it:
# Residential, Commercial, Industrial, Agriculture, Education, Government,
# Utility, Religion, Assembly, Unclassified.
BUILDING_CLASS = {
    "building": {
        "residential": "Residential", "apartments": "Residential",
        "house": "Residential", "detached": "Residential",
        "semidetached_house": "Residential", "terrace": "Residential",
        "bungalow": "Residential", "dormitory": "Residential",
        "hotel": "Commercial", "commercial": "Commercial",
        "retail": "Commercial", "supermarket": "Commercial",
        "office": "Commercial", "kiosk": "Commercial",
        "industrial": "Industrial", "warehouse": "Industrial",
        "factory": "Industrial", "manufacture": "Industrial",
        "farm": "Agriculture", "farm_auxiliary": "Agriculture",
        "barn": "Agriculture", "greenhouse": "Agriculture",
        "stable": "Agriculture", "cowshed": "Agriculture",
        "school": "Education", "university": "Education",
        "college": "Education", "kindergarten": "Education",
        "government": "Government", "civic": "Government",
        "public": "Government", "fire_station": "Government",
        "church": "Religion", "chapel": "Religion", "cathedral": "Religion",
        "mosque": "Religion", "temple": "Religion", "shrine": "Religion",
        "synagogue": "Religion", "monastery": "Religion",
        "hospital": "Assembly", "stadium": "Assembly",
        "sports_hall": "Assembly", "train_station": "Assembly",
        "transportation": "Assembly", "museum": "Assembly",
        "service": "Utility", "transformer_tower": "Utility",
        "water_tower": "Utility", "storage_tank": "Utility",
        "garage": "Utility", "garages": "Utility", "carport": "Utility",
        "shed": "Utility", "hut": "Utility", "roof": "Utility",
    },
    # Function tags override the physical building tag: a building=yes with
    # amenity=restaurant is Commercial, not Unclassified.
    "amenity": {
        "restaurant": "Commercial", "cafe": "Commercial",
        "fast_food": "Commercial", "bar": "Commercial", "pub": "Commercial",
        "bank": "Commercial", "pharmacy": "Commercial",
        "fuel": "Commercial", "marketplace": "Commercial",
        "school": "Education", "university": "Education",
        "college": "Education", "kindergarten": "Education",
        "library": "Education",
        "hospital": "Assembly", "clinic": "Assembly", "doctors": "Assembly",
        "theatre": "Assembly", "cinema": "Assembly",
        "community_centre": "Assembly", "townhall": "Government",
        "courthouse": "Government", "police": "Government",
        "fire_station": "Government", "post_office": "Government",
        "prison": "Government",
        "place_of_worship": "Religion",
    },
    "office": {"*": "Commercial"},
    "shop": {"*": "Commercial"},
    "tourism": {"hotel": "Commercial", "motel": "Commercial",
                "guest_house": "Commercial", "museum": "Assembly"},
    "industrial": {"*": "Industrial"},
}


def in_box(bbox, box):
    """True when the query bbox overlaps the given envelope."""
    return not (bbox[2] < box[0] or bbox[0] > box[2] or
                bbox[3] < box[1] or bbox[1] > box[3])


def pick_source(bbox, override=None):
    if override:
        return override
    if any(in_box(bbox, b) for b in US_BOXES):
        return "us"
    return "osm"


def region_label(bbox):
    """Human-readable hint about where the box is, for the banner only."""
    if any(in_box(bbox, b) for b in US_BOXES):
        return "United States"
    if in_box(bbox, JAPAN_BOX):
        return "Japan"
    return "outside the US"


def coords_of(node):
    """Yield every [lon, lat] pair anywhere in a nested coordinate structure."""
    if isinstance(node, (int, float)):
        return
    if len(node) == 2 and all(isinstance(v, (int, float)) for v in node):
        yield node
        return
    for child in node:
        yield from coords_of(child)


def bbox_of(geojson):
    pts = []
    feats = geojson.get("features", [geojson])
    for f in feats:
        geom = f.get("geometry") or f
        if geom and geom.get("coordinates") is not None:
            pts.extend(coords_of(geom["coordinates"]))
    if not pts:
        raise SystemExit("no coordinates found — is this the file you drew?")
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)


def describe(feats, indent="    "):
    if not feats:
        return
    props = feats[0].get("properties") or {}
    keys = sorted(props.keys())
    tail = f" ... (+{len(keys) - 12} more)" if len(keys) > 12 else ""
    print(f"{indent}attributes: {', '.join(keys[:12])}{tail}")


def write_layer(feats, out_path, label):
    out_path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats}))
    print(f"  {label}: {len(feats)} features -> {out_path}")
    describe(feats)
    return len(feats)


# ======================================================================
# United States — ArcGIS FeatureServer / TIGERweb
# ======================================================================

def arcgis_query(url, bbox):
    """Returns (features, error_string). Empty list is a valid result."""
    params = {
        "where": "1=1",
        "geometry": ",".join(f"{v:.6f}" for v in bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "f": "geojson",
    }
    full = url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(full, timeout=60) as resp:
            raw = resp.read()
    except Exception as e:
        return [], f"request failed — {e}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [], f"response was not JSON: {raw[:120]!r}"

    if "error" in data:
        return [], f"service error — {data['error'].get('message')}"

    return data.get("features", []), None


def us_buildings(bbox, out_path):
    feats, err = arcgis_query(BUILDINGS, bbox)
    if err:
        print(f"  buildings: {err}")
        return 0
    return write_layer(feats, out_path, "buildings")


def us_roads(bbox, out_path):
    merged = []
    seen = set()
    names = set()

    print("  roads: probing layers 0-8")
    for layer in ROAD_LAYERS:
        feats, err = arcgis_query(f"{ROADS_BASE}/{layer}/query", bbox)
        if err:
            print(f"    layer {layer}: {err}")
            continue
        added = 0
        for f in feats:
            props = f.get("properties") or {}
            # Deduplicate on geometry, not LINEARID: a road crossing the box
            # more than once shares an ID across distinct segments.
            key = json.dumps(f.get("geometry"), sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(f)
            added += 1
            n = props.get("NAME") or props.get("BASENAME")
            if n:
                names.add(n)
        if feats:
            print(f"    layer {layer}: {len(feats)} features, {added} new")

    n = write_layer(merged, out_path, "roads")
    if names:
        shown = sorted(names)[:10]
        tail = f" ... (+{len(names) - 10} more)" if len(names) > 10 else ""
        print(f"    named: {', '.join(shown)}{tail}")
    return n


def fetch_us(bbox, outdir):
    counts = {}
    counts["buildings"] = us_buildings(bbox, outdir / "buildings.geojson")
    counts["roads"] = us_roads(bbox, outdir / "roads.geojson")
    return counts


# ======================================================================
# OpenStreetMap — Overpass
# ======================================================================

def overpass(query, label, retries=3):
    """Run one Overpass QL query. Returns (elements, error_string)."""
    body = urllib.parse.urlencode({"data": query}).encode()
    last = "no endpoint tried"
    for attempt in range(retries):
        endpoint = OVERPASS_ENDPOINTS[attempt % len(OVERPASS_ENDPOINTS)]
        try:
            req = urllib.request.Request(
                endpoint, data=body,
                headers={"User-Agent": "PZMapMaker fetch_gis"})
            with urllib.request.urlopen(req, timeout=180) as resp:
                raw = resp.read()
        except Exception as e:
            last = f"{endpoint}: {e}"
            # Overpass rate-limits aggressively; back off before retrying.
            if attempt < retries - 1:
                wait = 5 * (attempt + 1)
                print(f"    {label}: {e} — retrying in {wait}s")
                time.sleep(wait)
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            last = f"response was not JSON: {raw[:120]!r}"
            continue

        if "remark" in data and "error" in str(data.get("remark", "")).lower():
            last = f"overpass remark — {data['remark']}"
            continue

        return data.get("elements", []), None

    return [], f"all attempts failed — {last}"


def ql(selectors, bbox, timeout=180):
    """Build an Overpass QL query for ways and relations matching selectors.

    bbox arrives as (minlon, minlat, maxlon, maxlat); Overpass wants
    south,west,north,east.
    """
    south, west, north, east = bbox[1], bbox[0], bbox[3], bbox[2]
    box = f"{south:.6f},{west:.6f},{north:.6f},{east:.6f}"
    parts = "\n  ".join(f'way{s}({box});\n  relation{s}({box});'
                        for s in selectors)
    return f"[out:json][timeout:{timeout}];\n(\n  {parts}\n);\nout tags geom;"


def ring_of(element):
    """[lon, lat] ring from an Overpass 'geometry' array."""
    return [[p["lon"], p["lat"]] for p in element.get("geometry", [])]


def closed(ring):
    return len(ring) >= 4 and ring[0] == ring[-1]


def osm_polygon(element):
    """GeoJSON geometry for an area element, or None if it has no usable ring.

    Overpass returns relation members under 'members'; for multipolygons we
    take the outer ways only. Inner rings (holes) are dropped — the importer
    rasterises footprints and a courtyard is not worth the complexity here.
    """
    if element.get("type") == "way":
        ring = ring_of(element)
        if not ring:
            return None
        if not closed(ring):
            ring = ring + [ring[0]]
        if len(ring) < 4:
            return None
        return {"type": "Polygon", "coordinates": [ring]}

    if element.get("type") == "relation":
        outers = []
        for m in element.get("members", []):
            if m.get("role") not in ("outer", ""):
                continue
            ring = ring_of(m)
            if not ring:
                continue
            if not closed(ring):
                ring = ring + [ring[0]]
            if len(ring) >= 4:
                outers.append([ring])
        if not outers:
            return None
        if len(outers) == 1:
            return {"type": "Polygon", "coordinates": outers[0]}
        return {"type": "MultiPolygon", "coordinates": outers}

    return None


def osm_linestring(element):
    ring = ring_of(element)
    if len(ring) < 2:
        return None
    return {"type": "LineString", "coordinates": ring}


def classify(tags):
    """Map OSM tags onto (OCC_CLS, PRIM_OCC).

    OCC_CLS is the coarse class the importer reads. PRIM_OCC keeps the OSM
    value that produced it, so the finer distinction survives for later use —
    that mirrors USA Structures, where PRIM_OCC splits Commercial into retail,
    office, restaurant and so on.
    """
    for key in ("amenity", "shop", "office", "tourism", "industrial",
                "building"):
        if key not in tags:
            continue
        value = tags[key]
        table = BUILDING_CLASS.get(key, {})
        if "*" in table:
            return table["*"], f"{key}={value}"
        if value in table:
            return table[value], f"{key}={value}"
    # building=yes with nothing else is the common case in dense Japanese
    # cities; it is a real building, just untyped.
    if "building" in tags:
        return "Unclassified", f"building={tags['building']}"
    return "Unclassified", ""


def osm_buildings(bbox, out_path):
    elements, err = overpass(ql(['["building"]'], bbox), "buildings")
    if err:
        print(f"  buildings: {err}")
        return 0

    feats = []
    for el in elements:
        geom = osm_polygon(el)
        if geom is None:
            continue
        tags = el.get("tags", {})
        occ, prim = classify(tags)
        props = {
            "OCC_CLS": occ,
            "PRIM_OCC": prim,
            "OSM_ID": f"{el.get('type')}/{el.get('id')}",
            "SOURCE": "OpenStreetMap",
        }
        # Keep the handful of OSM tags worth carrying. Height and level count
        # are here because PZ builds upward and a 30-storey tower is not a
        # house; nothing reads them yet.
        for k in ("name", "name:en", "building", "building:levels",
                  "height", "addr:postcode"):
            if k in tags:
                props[k] = tags[k]
        feats.append({"type": "Feature", "properties": props,
                      "geometry": geom})

    n = write_layer(feats, out_path, "buildings")
    if feats:
        classes = {}
        for f in feats:
            c = f["properties"]["OCC_CLS"]
            classes[c] = classes.get(c, 0) + 1
        summary = ", ".join(f"{k} {v}" for k, v in
                            sorted(classes.items(), key=lambda kv: -kv[1]))
        print(f"    classes: {summary}")
    return n


def osm_roads(bbox, out_path):
    elements, err = overpass(ql(['["highway"]'], bbox), "roads")
    if err:
        print(f"  roads: {err}")
        return 0

    feats = []
    names = set()
    for el in elements:
        geom = osm_linestring(el)
        if geom is None:
            continue
        tags = el.get("tags", {})
        props = {
            "MTFCC": tags.get("highway", ""),   # role matches the TIGER field
            "NAME": tags.get("name", "") or tags.get("name:en", ""),
            "OSM_ID": f"{el.get('type')}/{el.get('id')}",
            "SOURCE": "OpenStreetMap",
        }
        for k in ("highway", "surface", "lanes", "bridge", "tunnel", "layer"):
            if k in tags:
                props[k] = tags[k]
        feats.append({"type": "Feature", "properties": props,
                      "geometry": geom})
        if props["NAME"]:
            names.add(props["NAME"])

    n = write_layer(feats, out_path, "roads")
    if names:
        shown = sorted(names)[:10]
        tail = f" ... (+{len(names) - 10} more)" if len(names) > 10 else ""
        print(f"    named: {', '.join(shown)}{tail}")
    return n


# OSM waterway type -> NHD fcode. GisImport.waterWidth() switches on fcode to
# pick a channel width, so OSM features must carry one or they all fall to the
# default. Values chosen to land on the width the NHD code already assigns:
#   46000 -> 3 tiles, 46006/46003 -> 2, 33600/33601/33603 -> 2, 55800 -> 4
WATERWAY_FCODE = {
    "river": "46000",       # 3 tiles — the wide case
    "stream": "46006",      # 2
    "canal": "33600",       # 2
    "drain": "33601",       # 2
    "ditch": "33603",       # 2
    "riverbank": "46000",   # 3
}


def osm_water(bbox, out_path):
    """Waterways as lines, water bodies as polygons.

    Written as water.geojson because GisImport auto-discovers that name next to
    buildings.geojson. Each feature carries an NHD-style fcode so the existing
    waterWidth() switch picks a sensible channel width without knowing the data
    came from OSM.

    CAVEAT: GisImport rasterises water by walking each ring and calling
    waterLine between consecutive points, which traces a polygon's perimeter
    rather than filling it. Linear waterways are correct; a lake or pond from
    natural=water will come out as a ring of water with dry ground inside.
    Filling areal water needs fillPolygon, the way buildings already work.
    """
    elements, err = overpass(
        ql(['["waterway"~"^(river|stream|canal|drain|ditch)$"]',
            '["natural"="water"]',
            '["landuse"="reservoir"]'], bbox), "rivers")
    if err:
        print(f"  water: {err}")
        return 0

    feats = []
    for el in elements:
        tags = el.get("tags", {})
        # natural=water and reservoirs are areas; waterway=* is a centreline.
        if "waterway" in tags and "natural" not in tags:
            geom = osm_linestring(el)
        else:
            geom = osm_polygon(el) or osm_linestring(el)
        if geom is None:
            continue
        waterway = tags.get("waterway", "")
        kind = waterway or tags.get("natural") or tags.get("landuse", "")
        props = {
            # fcode is what GisImport.waterWidth() reads. Areal water has no
            # linear equivalent, so it takes the river code and the widest
            # channel; see the perimeter caveat above.
            "fcode": WATERWAY_FCODE.get(waterway, "46000"),
            "WATER_TYPE": kind,
            "AREAL": "yes" if geom["type"] in ("Polygon", "MultiPolygon") else "no",
            "NAME": tags.get("name", "") or tags.get("name:en", ""),
            "OSM_ID": f"{el.get('type')}/{el.get('id')}",
            "SOURCE": "OpenStreetMap",
        }
        for k in ("waterway", "natural", "width", "intermittent"):
            if k in tags:
                props[k] = tags[k]
        feats.append({"type": "Feature", "properties": props,
                      "geometry": geom})

    n = write_layer(feats, out_path, "water")
    if feats:
        areal = sum(1 for f in feats if f["properties"]["AREAL"] == "yes")
        if areal:
            print(f"    {areal} areal (lake/pond) — these rasterise as a"
                  f" perimeter, not filled")
    return n


def osm_landuse(bbox, out_path):
    """Landcover polygons. Also unread today; see CHUNKS track E."""
    elements, err = overpass(
        ql(['["landuse"]',
            '["leisure"~"^(park|garden|pitch|golf_course|nature_reserve)$"]',
            '["natural"~"^(wood|scrub|grassland|heath|sand|beach)$"]'],
           bbox), "landuse")
    if err:
        print(f"  landuse: {err}")
        return 0

    feats = []
    for el in elements:
        geom = osm_polygon(el)
        if geom is None:
            continue
        tags = el.get("tags", {})
        props = {
            "LAND_USE": tags.get("landuse") or tags.get("leisure")
                        or tags.get("natural", ""),
            "NAME": tags.get("name", "") or tags.get("name:en", ""),
            "OSM_ID": f"{el.get('type')}/{el.get('id')}",
            "SOURCE": "OpenStreetMap",
        }
        feats.append({"type": "Feature", "properties": props,
                      "geometry": geom})

    n = write_layer(feats, out_path, "landuse")
    if feats:
        kinds = {}
        for f in feats:
            k = f["properties"]["LAND_USE"]
            kinds[k] = kinds.get(k, 0) + 1
        top = sorted(kinds.items(), key=lambda kv: -kv[1])[:8]
        print(f"    kinds: {', '.join(f'{k} {v}' for k, v in top)}")
    return n


def fetch_osm(bbox, outdir):
    counts = {}
    counts["buildings"] = osm_buildings(bbox, outdir / "buildings.geojson")
    counts["roads"] = osm_roads(bbox, outdir / "roads.geojson")
    counts["water"] = osm_water(bbox, outdir / "water.geojson")
    counts["landuse"] = osm_landuse(bbox, outdir / "landuse.geojson")
    return counts


# ======================================================================

def main():
    args = [a for a in sys.argv[1:]]
    source_override = None
    if "--source" in args:
        i = args.index("--source")
        try:
            source_override = args[i + 1].lower()
        except IndexError:
            raise SystemExit("--source needs a value: us or osm")
        if source_override not in ("us", "osm"):
            raise SystemExit("--source must be 'us' or 'osm'")
        del args[i:i + 2]

    if not args:
        raise SystemExit(__doc__)

    area = Path(args[0]).expanduser()
    outdir = Path(args[1]).expanduser() if len(args) > 1 else area.parent
    outdir.mkdir(parents=True, exist_ok=True)

    geo = json.loads(area.read_text())
    bbox = bbox_of(geo)

    mid_lat = (bbox[1] + bbox[3]) / 2
    w = (bbox[2] - bbox[0]) * 111_320 * math.cos(math.radians(mid_lat))
    h = (bbox[3] - bbox[1]) * 110_540
    print(f"area: {w:.0f} m x {h:.0f} m  (~{w * h / 10_000:.1f} hectares)")
    print(f"in Project Zomboid tiles that is roughly {w:.0f} x {h:.0f} tiles"
          f" — a cell is 256x256")
    print(f"bbox: {bbox[0]:.6f},{bbox[1]:.6f},{bbox[2]:.6f},{bbox[3]:.6f}")

    source = pick_source(bbox, source_override)
    where = region_label(bbox)
    how = " (forced)" if source_override else ""
    if source == "us":
        print(f"region: {where} — using USA Structures + TIGER/Line{how}\n")
    else:
        print(f"region: {where} — using OpenStreetMap via Overpass{how}\n")

    # Overpass will refuse or time out on a very large box, and a PZ map that
    # size is not practical anyway: 4 km is already 15x15 cells.
    if source == "osm" and max(w, h) > 6000:
        print(f"WARNING: {max(w, h) / 1000:.1f} km across. Overpass may time out,")
        print("         and this is a lot of map. Consider a smaller box.\n")

    print("fetching:")
    counts = fetch_us(bbox, outdir) if source == "us" else fetch_osm(bbox, outdir)

    print()
    if counts.get("buildings", 0) == 0:
        if source == "us":
            print("No buildings returned. USA Structures only covers structures")
            print("over 450 sq ft, so sheds and detached garages are often absent.")
        else:
            print("No buildings returned from OSM. If buildings are visible on")
            print("the geojson.io basemap they should be here — check the box is")
            print("where you think it is, and try again if Overpass was busy.")
    if counts.get("roads", 0) == 0:
        if source == "us":
            print("No roads in ANY layer. If a road is visible on the geojson.io")
            print("basemap, that basemap is OpenStreetMap and TIGER may not carry")
            print("the road — private drives and some rural roads are missing.")
            print("Re-run with --source osm to take the road from OSM instead.")
        else:
            print("No roads returned from OSM.")

    if any(counts.values()):
        print("Ready. Keep these files local — the importer reads them from disk.")
        if source == "osm":
            print()
            print("water.geojson is read by GisImport (auto-discovered beside")
            print("buildings.geojson). landuse.geojson is not — Cover has no")
            print("landcover type yet; it is on disk for when it does.")
            print()
            print("OSM data is ODbL — attribution required if you publish the map.")


if __name__ == "__main__":
    main()
