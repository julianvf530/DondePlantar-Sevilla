"""
Prepara los datos UNA VEZ antes de la demo.

Entrada : data/arboles.geojson  (tu inventario; si no existe usa arboles_demo.geojson)
          data/estaciones_aire.csv
Salida  : data/candidatos.json  (posiciones viables con sombra, calor y aire ya calculados)

Uso: python3 tools/preparar_datos.py

Qué calcula para cada posición viable de plantación (TIPO_MARRA = VIABLE):
  - sombra     : sombra que ya dan los árboles existentes a <25 m  (dato real)
  - calor      : PROXY = poca cobertura arbórea en el entorno (~300 m) => más calor
  - contam     : contaminación interpolada (IDW) desde las estaciones de calidad del aire
"""
import csv, json, math, sys
from bisect import bisect_left
from collections import Counter, defaultdict
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "data"
ENTRADA = DATOS / "arboles.geojson"
if not ENTRADA.exists():
    ENTRADA = DATOS / "arboles_demo.geojson"
    print("AVISO: no hay data/arboles.geojson, uso los datos DEMO (sintéticos).")
ESTACIONES = DATOS / "estaciones_aire.csv"
TEMPERATURAS = DATOS / "temperatura_verano.geojson"   # 12 puntos (estimación generada con IA, NO medición)
PESO_TEMP = 0.6   # calor = 0.6 * temperatura interpolada + 0.4 * cobertura arbórea del entorno (proxy)
SALIDA = DATOS / "candidatos.json"

R_SOMBRA = 25.0      # m: radio en el que un árbol da sombra a una posición
CELDA_CALOR = 100.0  # m: celda para medir la cobertura del entorno (se suman 3x3 celdas)
LAT0, LON0 = 37.39, -5.98
M_LAT = 110540.0
M_LON = 111320.0 * math.cos(math.radians(LAT0))


def xy(lon, lat):
    return (lon - LON0) * M_LON, (lat - LAT0) * M_LAT


def texto(v):
    return str(v).strip() if v is not None else ""


def peso_arbol(p):
    """Peso de sombra de un árbol existente según su altura (0.15 a 1)."""
    try:
        h = float(p.get("ALTURA") or 0)
    except (TypeError, ValueError):
        h = 0.0
    return 0.3 if h <= 0 else min(1.0, max(0.15, h / 12.0))


# Tipologías que NO se proponen como destino de plantación (parques en las afueras)
TIPOLOGIAS_EXCLUIDAS = {"PARQUE PERIURBANO"}


def es_viable(tipo_marra, tipologia=""):
    """Posición candidata: 'Posición VIABLE plantación' (con o sin tocón) o 'Alcorque vacío'."""
    t = texto(tipo_marra).upper()
    if texto(tipologia).upper() in TIPOLOGIAS_EXCLUIDAS:
        return False
    return ("VIABLE" in t and "NO VIABLE" not in t) or t == "ALCORQUE VACÍO"


def leer_estaciones():
    est = []
    with open(ESTACIONES, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            x, y = xy(float(r["lon"]), float(r["lat"]))
            idx = (float(r["no2"]) / 40.0 + float(r["pm25"]) / 25.0) / 2.0  # frente a límites anuales UE
            est.append((x, y, idx))
    return est


def leer_temperaturas():
    if not TEMPERATURAS.exists():
        return []
    pts = []
    for f in json.load(open(TEMPERATURAS, encoding="utf-8"))["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        x, y = xy(lon, lat)
        pts.append((x, y, float(f["properties"]["temp_max_media_verano"])))
    return pts


def idw(x, y, est, p=2.0):
    num = den = 0.0
    for ex, ey, v in est:
        d = math.hypot(x - ex, y - ey)
        if d < 1.0:
            return v
        w = 1.0 / d ** p
        num += w * v
        den += w
    return num / den


def main():
    print(f"Leyendo {ENTRADA.name} ...")
    gj = json.load(open(ENTRADA, encoding="utf-8"))
    marra = Counter()
    arboles, cands = [], []
    for f in gj["features"]:
        g = f.get("geometry") or {}
        if g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"][:2]
        p = f.get("properties") or {}
        t = texto(p.get("TIPO_MARRA"))
        marra[t or "(vacío = árbol existente)"] += 1
        x, y = xy(lon, lat)
        if es_viable(t, p.get("TIPOLOGIA")):
            cands.append({"id": p.get("ID", p.get("OBJECTID")), "lat": lat, "lon": lon, "x": x, "y": y,
                          "distrito": texto(p.get("DISTRITO")), "barrio": texto(p.get("BARRIO")),
                          "calle": texto(p.get("GESTION")), "tipologia": texto(p.get("TIPOLOGIA"))})
        elif not t and texto(p.get("ESPECIE")):
            # árbol existente = TIPO_MARRA vacío (tocones, árboles muertos y alcorques no dan sombra)
            arboles.append((x, y, peso_arbol(p)))

    print("\nValores de TIPO_MARRA:")
    for k, v in marra.most_common():
        print(f"   {v:>8}  {k}")
    print(f"\nÁrboles existentes: {len(arboles)} | Posiciones viables: {len(cands)}")
    if not cands:
        sys.exit("No se encontró ninguna posición VIABLE. Revisa los valores de TIPO_MARRA de arriba "
                 "y ajusta la función es_viable().")

    # Rejillas para búsqueda rápida
    rej_s, rej_c = defaultdict(list), defaultdict(float)
    for x, y, w in arboles:
        rej_s[(int(x // R_SOMBRA), int(y // R_SOMBRA))].append((x, y, w))
        rej_c[(int(x // CELDA_CALOR), int(y // CELDA_CALOR))] += w

    densidades = []
    for c in cands:
        ix, iy = int(c["x"] // R_SOMBRA), int(c["y"] // R_SOMBRA)
        sombra = 0.0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ax, ay, w in rej_s.get((ix + dx, iy + dy), ()):
                    d = math.hypot(c["x"] - ax, c["y"] - ay)
                    if d < R_SOMBRA:
                        sombra += w * (1 - d / R_SOMBRA)
        jx, jy = int(c["x"] // CELDA_CALOR), int(c["y"] // CELDA_CALOR)
        dens = sum(rej_c.get((jx + dx, jy + dy), 0.0) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
        c["sombra"] = sombra
        c["dens"] = dens
        densidades.append(dens)

    # Calor (proxy): menos cobertura que el resto de posiciones => más calor (0-1, por percentil)
    ordenadas = sorted(densidades)
    n = len(ordenadas)
    for c in cands:
        c["calor_proxy"] = 1.0 - bisect_left(ordenadas, c["dens"]) / n

    # Calor (temperatura): IDW de la temperatura máxima media de verano, normalizada 0-1 entre posiciones
    temps = leer_temperaturas()
    if temps:
        for c in cands:
            c["temp"] = idw(c["x"], c["y"], temps)
        tlo, thi = min(c["temp"] for c in cands), max(c["temp"] for c in cands)
        for c in cands:
            c["calor_temp"] = (c["temp"] - tlo) / (thi - tlo) if thi > tlo else 0.5
            c["calor"] = PESO_TEMP * c["calor_temp"] + (1 - PESO_TEMP) * c["calor_proxy"]
        print(f"Temperatura: {len(temps)} puntos interpolados ({tlo:.1f}-{thi:.1f} °C entre posiciones)")
    else:
        for c in cands:
            c["temp"] = None
            c["calor_temp"] = None
            c["calor"] = c["calor_proxy"]
        print("AVISO: sin temperatura_verano.geojson; el calor usa solo el proxy de cobertura arbórea.")

    # Contaminación: IDW desde estaciones, normalizada 0-1 entre las posiciones (relativa)
    est = leer_estaciones()
    for c in cands:
        c["contam"] = idw(c["x"], c["y"], est)
    lo, hi = min(c["contam"] for c in cands), max(c["contam"] for c in cands)
    for c in cands:
        c["contam"] = (c["contam"] - lo) / (hi - lo) if hi > lo else 0.5

    salida = {
        "meta": {
            "fuente_arboles": ENTRADA.name,
            "n_arboles": len(arboles),
            "n_candidatos": len(cands),
            "tipo_marra": dict(marra),
            "calor_fuente": "temperatura (IA, 12 puntos, IDW) 60% + cobertura arbórea 40%" if temps else "cobertura arbórea (proxy)",
            "centro": [sum(c["lat"] for c in cands) / n, sum(c["lon"] for c in cands) / n],
        },
        "candidatos": [{
            "id": c["id"], "lat": round(c["lat"], 6), "lon": round(c["lon"], 6),
            "x": round(c["x"], 1), "y": round(c["y"], 1),
            "distrito": c["distrito"], "barrio": c["barrio"], "calle": c["calle"], "tipologia": c["tipologia"],
            "sombra": round(c["sombra"], 3), "calor": round(c["calor"], 3), "contam": round(c["contam"], 3),
            "temp": None if c["temp"] is None else round(c["temp"], 1),
            "calor_proxy": round(c["calor_proxy"], 3),
        } for c in cands],
    }
    json.dump(salida, open(SALIDA, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"\nListo -> {SALIDA}  ({SALIDA.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
