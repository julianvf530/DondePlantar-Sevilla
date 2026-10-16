"""
LÓGICA SIMULADA DEL PROTOTIPO: dónde plantar árboles primero.

Para cada posición viable de plantación:
    prioridad = (w_calor*calor + w_contam*contaminacion + w_sombra*deficit_sombra) / (suma de pesos)

  deficit_sombra : 1 - sombra_actual/OBJETIVO_SOMBRA   (dato real: árboles existentes a <25 m)
  calor          : PROXY (poca cobertura arbórea en el entorno)   -> sustituir por temperatura real
  contaminacion  : interpolada (IDW) desde 4 estaciones de calidad del aire (relativa, 0-1)

Selección de N árboles con IMPACTO MARGINAL: cada árbol que se elige añade sombra a las
posiciones cercanas, baja su prioridad y evita concentrarlo todo en un punto.
"""
import heapq, json, math
from collections import defaultdict
from pathlib import Path

DATOS = Path(__file__).parent / "data" / "candidatos.json"

SUPUESTOS = {
    "objetivo_sombra": 2.5,          # sombra equivalente deseada en un radio de 25 m
    "radio_sombra_m": 25.0,
    "peso_arbol_nuevo": 0.5,         # sombra que aporta un árbol recién plantado a su entorno
    "umbral_plantar_ya": 0.70,
    "umbral_siguiente_fase": 0.50,
    "coste_por_arbol_eur": 400,      # EJEMPLO: árbol + plantación + riego de arraigo; justificar con fuente
    "m2_sombra_por_arbol": 20,       # EJEMPLO: copa adulta; justificar con fuente
    "kg_co2_por_arbol_y_ano": 20,    # EJEMPLO: árbol adulto; justificar con fuente
}

_M = None


def cargar():
    global _M
    if _M is None:
        d = json.load(open(DATOS, encoding="utf-8"))
        cands = d["candidatos"]
        celda = SUPUESTOS["radio_sombra_m"]
        rej = defaultdict(list)
        for i, c in enumerate(cands):
            rej[(int(c["x"] // celda), int(c["y"] // celda))].append(i)
        _M = {"meta": d["meta"], "c": cands, "rej": rej}
    return _M


def _vecinos(M, i):
    c = M["c"][i]
    R = SUPUESTOS["radio_sombra_m"]
    ix, iy = int(c["x"] // R), int(c["y"] // R)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in M["rej"].get((ix + dx, iy + dy), ()):
                if j != i:
                    d = math.hypot(c["x"] - M["c"][j]["x"], c["y"] - M["c"][j]["y"])
                    if d < R:
                        yield j, 1 - d / R


def _deficit(sombra):
    return max(0.0, 1.0 - sombra / SUPUESTOS["objetivo_sombra"])


def _motivo(c, deficit, w):
    parte = {"poca sombra": w[2] * deficit, "calor del entorno": w[0] * c["calor"], "contaminación": w[1] * c["contam"]}
    ordenado = sorted(parte, key=parte.get, reverse=True)
    return f"{ordenado[0].capitalize()} y {ordenado[1]}"


def barrios_disponibles():
    """Barrios con posiciones candidatas, de más a menos."""
    M = cargar()
    cuenta = defaultdict(int)
    for c in M["c"]:
        cuenta[c["barrio"]] += 1
    return [{"barrio": b, "posiciones": k} for b, k in sorted(cuenta.items(), key=lambda x: -x[1])]


def simular(w_calor=50, w_contam=20, w_sombra=50, n=100, max_por_calle=15,
            barrio="", presupuesto=0, coste_arbol=None):
    M = cargar()
    cands = M["c"]
    coste = coste_arbol or SUPUESTOS["coste_por_arbol_eur"]
    if presupuesto and presupuesto > 0:          # modo presupuesto: nº de árboles = presupuesto / coste
        n = max(1, min(2000, int(presupuesto // coste)))
    permitido = [(not barrio) or c["barrio"] == barrio for c in cands]
    suma = (w_calor + w_contam + w_sombra) or 1
    w = (w_calor / suma, w_contam / suma, w_sombra / suma)

    sombra = [c["sombra"] for c in cands]
    version = [0] * len(cands)

    def prio(i):
        c = cands[i]
        return w[0] * c["calor"] + w[1] * c["contam"] + w[2] * _deficit(sombra[i])

    prio0 = [prio(i) for i in range(len(cands)) if permitido[i]]
    media_todas = sum(prio0) / len(prio0) if prio0 else 0
    heap = [(-prio(i), 0, i) for i in range(len(cands)) if permitido[i]]
    heapq.heapify(heap)
    elegido = [False] * len(cands)
    por_calle = defaultdict(int)
    sel = []

    while heap and len(sel) < n:
        negp, v, i = heapq.heappop(heap)
        if elegido[i] or v != version[i]:
            continue
        c = cands[i]
        clave = (c["barrio"], c["calle"])
        if por_calle[clave] >= max_por_calle:
            continue
        elegido[i] = True
        por_calle[clave] += 1
        sel.append((i, -negp))
        for j, f in _vecinos(M, i):
            if not elegido[j]:
                sombra[j] += SUPUESTOS["peso_arbol_nuevo"] * f
                if permitido[j]:
                    version[j] += 1
                    heapq.heappush(heap, (-prio(j), version[j], j))

    resultados = []
    for k, (i, p) in enumerate(sel, 1):
        c = cands[i]
        accion = ("Plantar ya" if p >= SUPUESTOS["umbral_plantar_ya"]
                  else "Siguiente fase" if p >= SUPUESTOS["umbral_siguiente_fase"] else "No prioritaria")
        resultados.append({
            "rank": k, "id": c["id"], "calle": c["calle"], "barrio": c["barrio"], "distrito": c["distrito"],
            "lat": c["lat"], "lon": c["lon"], "prioridad": round(p, 3), "accion": accion,
            "calor": c["calor"], "contam": c["contam"], "temp": c.get("temp"), "deficit_sombra": round(_deficit(c["sombra"]), 3),
            "motivo": _motivo(c, _deficit(c["sombra"]), w),
        })

    por_barrio = defaultdict(lambda: [0, 0.0])
    for r in resultados:
        por_barrio[r["barrio"]][0] += 1
        por_barrio[r["barrio"]][1] += r["prioridad"]
    barrios = sorted(({"barrio": b, "arboles": v[0], "prioridad_media": round(v[1] / v[0], 3)}
                      for b, v in por_barrio.items()), key=lambda x: x["arboles"], reverse=True)

    media_sel = sum(r["prioridad"] for r in resultados) / len(resultados) if resultados else 0
    na = len(resultados)
    return {
        "seleccion": resultados,
        "barrios": barrios,
        "kpis": {
            "arboles": na,
            "arboles_pedidos": n,
            "coste_total_eur": na * coste,
            "coste_arbol_eur": coste,
            "calles": len({(r["barrio"], r["calle"]) for r in resultados}),
            "barrios": len(barrios),
            "sombra_m2": na * SUPUESTOS["m2_sombra_por_arbol"],
            "co2_kg_ano": na * SUPUESTOS["kg_co2_por_arbol_y_ano"],
            "mejora_vs_azar_pct": round((media_sel / media_todas - 1) * 100) if media_todas else 0,
        },
        "meta": M["meta"],
        "supuestos": SUPUESTOS,
    }
