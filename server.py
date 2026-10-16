"""Servidor del prototipo. Solo librería estándar: no hay que instalar nada.
Antes (una vez):  python3 tools/preparar_datos.py
Uso:              python server.py   ->   http://localhost:8000  (opcional: python server.py 8080)
"""
import csv, io, json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import logic

ROOT = Path(__file__).parent
ESTACIONES = ROOT / "data" / "estaciones_aire.csv"
TEMPERATURAS = ROOT / "data" / "temperatura_verano.geojson"


def num(q, clave, defecto, minimo, maximo):
    try:
        v = int(float(q.get(clave, [defecto])[0]))
    except ValueError:
        v = defecto
    return max(minimo, min(maximo, v))


def parametros(q):
    return dict(w_calor=num(q, "w_calor", 50, 0, 100), w_contam=num(q, "w_contam", 20, 0, 100),
                w_sombra=num(q, "w_sombra", 50, 0, 100), n=num(q, "n", 100, 1, 1000),
                max_por_calle=num(q, "max_por_calle", 15, 1, 100),
                barrio=q.get("barrio", [""])[0].strip(),
                presupuesto=num(q, "presupuesto", 0, 0, 50_000_000),
                coste_arbol=num(q, "coste", logic.SUPUESTOS["coste_por_arbol_eur"], 50, 10_000))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT / "static"), **k)

    def _enviar(self, cuerpo: bytes, tipo, extra=None):
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, obj):
        self._enviar(json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/api/simular":
            return self._json(logic.simular(**parametros(q)))
        if url.path == "/api/temperaturas":
            try:
                return self._json(json.loads(TEMPERATURAS.read_text(encoding="utf-8"))["features"])
            except FileNotFoundError:
                return self._json([])
        if url.path == "/api/barrios":
            return self._json(logic.barrios_disponibles())
        if url.path == "/api/estaciones":
            with open(ESTACIONES, encoding="utf-8-sig", newline="") as f:
                return self._json(list(csv.DictReader(f)))
        if url.path == "/api/exportar.csv":
            r = logic.simular(**parametros(q))["seleccion"]
            sal = io.StringIO()
            campos = ["rank", "id", "calle", "barrio", "distrito", "lat", "lon", "prioridad", "accion", "motivo"]
            w = csv.DictWriter(sal, fieldnames=campos, extrasaction="ignore")
            w.writeheader()
            w.writerows(r)
            return self._enviar(("﻿" + sal.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                                {"Content-Disposition": 'attachment; filename="plan_plantacion.csv"'})
        return super().do_GET()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import sys
    import threading
    import webbrowser

    if not (ROOT / "data" / "candidatos.json").exists():
        sys.exit("Falta data/candidatos.json. Genera los datos con: python tools/preparar_datos.py "
                 "(necesita data/arboles.geojson). Ver el README.")
    logic.cargar()
    puerto = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    url = f"http://localhost:{puerto}"
    print(f"Prototipo en {url}  (Ctrl+C para parar)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", puerto), Handler).serve_forever()
