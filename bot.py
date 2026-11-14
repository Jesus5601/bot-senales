#!/usr/bin/env python3
"""
Bot de señales -> avisos al celular con ntfy.

Usa la misma lógica que tu indicador de TradingView
"Backtester Universal de Señales + Barreras [ES]" (ver indicador.py).
Cada vez que GitHub lo ejecuta (cada ~5 minutos) recalcula el indicador
para cada moneda y temporalidad, y te avisa si en una vela YA CERRADA
apareció una entrada (COMPRA/VENTA) o una posible entrada.
Guarda en estado.json qué velas ya avisó para no repetir.

Solo usa la librería estándar de Python: no hay que instalar nada.
"""

import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import indicador

# ======================= CONFIGURACIÓN =======================

MONEDAS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "SHIBUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT",
]
TEMPORALIDADES = ["15m", "1h", "4h"]

# Qué avisos quieres recibir
AVISAR_ENTRADAS = True              # COMPRA / VENTA (con % de acierto y TP sugerido)
AVISAR_POSIBLES = True              # ◆ Posible compra / ◆ Posible venta
AVISAR_CAMBIOS_TENDENCIA = False    # ⚡ ruptura de barrera FUERTE
AVISAR_CIERRES = False              # cierre de la operación simulada

# Ajustes del indicador. Deben coincidir con los que tienes en TradingView.
# Solo escribe aquí los que hayas cambiado; el resto usa los valores por
# defecto del indicador (ver AJUSTES_POR_DEFECTO en indicador.py).
AJUSTES_INDICADOR = {
    # "usarFiltroTend": True,
    # "usarFiltroRSI": True,
}

# Velas de historial para calcular el % de acierto (parecido a lo que
# carga el gráfico de TradingView). Máximo recomendado: 5000.
HISTORIAL_VELAS = 5000

# Gráfico de TradingView que se abre al tocar el aviso (perpetuos de Binance).
# Por defecto BINANCE:<MONEDA>.P; aquí van solo las excepciones.
SIMBOLO_TRADINGVIEW = {"SHIBUSDT": "BINANCE:1000SHIBUSDT.P"}

# Si GitHub se atrasa, se revisan velas cerradas hace como máximo esta
# cantidad de velas (15m -> 45 min, 1h -> 3 h, 4h -> 12 h).
MAX_VELAS_ATRASADAS = 3
HORAS_ENTRE_AVISOS_DE_ERROR = 6
ARCHIVO_ESTADO = "estado.json"
VERSION = 2
ZONA_LIMA = timezone(timedelta(hours=-5))

# =============================================================

MS_POR_TF = {"15m": 15 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000}
LETRAS_TP = ["TP1", "TP2", "TP3"]


# ---------------------------- Datos ----------------------------

def http_json(url, timeout=20, intentos=2):
    ultimo = None
    for intento in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bot-senales"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            ultimo = e
            if e.code not in (429, 500, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            ultimo = e
        time.sleep(2 * (intento + 1))
    raise ultimo


def velas_binance(moneda, tf, total):
    # data-api.binance.vision: datos públicos de Binance que funcionan
    # también desde los servidores de GitHub (que están en EE.UU.).
    velas, fin = [], None
    while len(velas) < total:
        limite = min(1000, total - len(velas))
        url = ("https://data-api.binance.vision/api/v3/klines"
               f"?symbol={moneda}&interval={tf}&limit={limite}")
        if fin is not None:
            url += f"&endTime={fin}"
        lote = http_json(url)
        if not lote:
            break
        velas = [
            {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
             "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
            for k in lote
        ] + velas
        fin = int(lote[0][0]) - 1
        if len(lote) < limite:
            break
    return velas


def velas_okx(moneda, tf, total):
    # Respaldo: perpetuos de OKX (solo 300 velas: el % de acierto sale con menos historial).
    inst = moneda[:-4] + "-USDT-SWAP"
    barra = {"15m": "15m", "1h": "1H", "4h": "4H"}[tf]
    url = ("https://www.okx.com/api/v5/market/candles"
           f"?instId={inst}&bar={barra}&limit=300")
    datos = http_json(url).get("data") or []
    return [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
         "l": float(k[3]), "c": float(k[4]), "v": float(k[5])}
        for k in reversed(datos)  # OKX las da de la más nueva a la más vieja
    ]


FUENTES = [("Binance", velas_binance), ("OKX", velas_okx)]


def obtener_velas(moneda, tf, ahora_ms):
    """Velas CERRADAS, de la más antigua a la más nueva."""
    errores = []
    for nombre, fuente in FUENTES:
        try:
            velas = fuente(moneda, tf, HISTORIAL_VELAS)
            cerradas = [v for v in velas if v["t"] + MS_POR_TF[tf] <= ahora_ms]
            if len(cerradas) >= 250:
                return cerradas, nombre
            errores.append(f"{nombre}: solo {len(cerradas)} velas")
        except Exception as e:  # noqa: BLE001
            errores.append(f"{nombre}: {e}")
    raise RuntimeError(" | ".join(errores))


# --------------------------- Avisos ----------------------------

def formato_precio(p):
    if p is None:
        return "-"
    if p <= 0:
        return str(p)
    decimales = max(2, 4 - math.floor(math.log10(p)))
    return f"{p:,.{decimales}f}"


def enviar_ntfy(titulo, mensaje, prioridad=4, etiquetas=None, enlace=None):
    tema = os.environ.get("NTFY_TOPIC", "").strip()
    if not tema:
        print(f"[NTFY_TOPIC no configurado] {titulo}\n{mensaje}\n")
        return
    cuerpo = {"topic": tema, "title": titulo, "message": mensaje, "priority": prioridad}
    if etiquetas:
        cuerpo["tags"] = etiquetas
    if enlace:
        cuerpo["click"] = enlace
    req = urllib.request.Request(
        "https://ntfy.sh/",
        data=json.dumps(cuerpo).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "bot-senales"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def enlace_tradingview(moneda):
    simbolo = SIMBOLO_TRADINGVIEW.get(moneda, f"BINANCE:{moneda}.P")
    return "https://www.tradingview.com/chart/?symbol=" + urllib.parse.quote(simbolo)


def texto_acierto(ev):
    if ev["total_ops"] == 0:
        return "Acierto: sin historial aún"
    pct = ev["ganadas"] * 100 / ev["total_ops"]
    return f"Acierto: {pct:.1f}% de {ev['total_ops']} operaciones"


def armar_aviso(moneda, tf, vela, ev, ahora_ms):
    """Devuelve (titulo, mensaje, prioridad, etiquetas) o None si ese tipo está desactivado."""
    compra = ev["dir"] == 1
    lineas = []

    if ev["tipo"] == "entrada":
        if not AVISAR_ENTRADAS:
            return None
        estrellas = "★" * ev["estrellas"] + "☆" * (4 - ev["estrellas"])
        titulo = f"{'COMPRA' if compra else 'VENTA'} {estrellas} · {moneda} {tf}"
        lineas.append(f"Precio: {formato_precio(ev['precio'])}")
        lineas.append(texto_acierto(ev))
        if ev["total_ops"] > 0 and ev["tp_sugerido"]:
            lineas.append(f"TP sugerido: TP{ev['tp_sugerido']} ({ev['tp_sugerido_pct']:.1f}%)")
        tps = [f"{n} {formato_precio(p)}" for n, p in zip(LETRAS_TP, ev["tps"]) if p is not None]
        sls = [f"SL{k + 1} {formato_precio(p)}" for k, p in enumerate(ev["sls"]) if p is not None]
        if tps:
            lineas.append(" · ".join(tps))
        if sls:
            lineas.append(" · ".join(sls))
        if ev["barrera_cerca"]:
            lineas.append("⚠ Barrera fuerte cerca")
        prioridad, etiquetas = 4, ["green_circle" if compra else "red_circle"]

    elif ev["tipo"] == "posible":
        if not AVISAR_POSIBLES:
            return None
        ajustes = dict(indicador.AJUSTES_POR_DEFECTO, **AJUSTES_INDICADOR)
        titulo = f"◆ Posible {'compra' if compra else 'venta'} · {moneda} {tf}"
        lineas.append(f"Las medias {ev['media']} se están acercando: "
                      f"un cruce {'al alza' if compra else 'a la baja'} es probable.")
        lineas.append(f"Si no se da en {ajustes['velasPosible']} velas, se descarta.")
        lineas.append(f"Precio: {formato_precio(ev['precio'])}")
        lineas.append(texto_acierto(ev))
        prioridad, etiquetas = 3, ["large_blue_diamond"]

    elif ev["tipo"] == "cambio":
        if not AVISAR_CAMBIOS_TENDENCIA:
            return None
        titulo = f"⚡ Cambio de tendencia {'↑' if compra else '↓'} · {moneda} {tf}"
        lineas.append(f"Rompió {'una resistencia' if compra else 'un soporte'} FUERTE.")
        lineas.append(f"Precio: {formato_precio(ev['precio'])}")
        prioridad, etiquetas = 3, ["zap"]

    elif ev["tipo"] == "cierre":
        if not AVISAR_CIERRES:
            return None
        gano = ev["resultado_pct"] > 0
        titulo = f"{'✓' if gano else '✗'} Operación cerrada · {moneda} {tf}"
        lineas.append(f"{'COMPRA' if compra else 'VENTA'} desde {formato_precio(ev['entrada'])}: "
                      f"{ev['resultado_pct']:+.2f}%")
        lineas.append(texto_acierto(ev))
        prioridad, etiquetas = 3, []
    else:
        return None

    cierre_vela_ms = vela["t"] + MS_POR_TF[tf]
    hora = datetime.fromtimestamp(cierre_vela_ms / 1000, ZONA_LIMA).strftime("%d/%m %H:%M")
    lineas.append(f"Vela de {tf} cerrada {hora} (Lima)")
    atraso_min = (ahora_ms - cierre_vela_ms) / 60_000
    if atraso_min > 10:
        lineas.append(f"⚠️ Aviso atrasado {atraso_min:.0f} min")
    return titulo, "\n".join(lineas), prioridad, etiquetas


# ---------------------------- Estado ----------------------------

def cargar_estado():
    try:
        with open(ARCHIVO_ESTADO, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def guardar_estado(estado):
    with open(ARCHIVO_ESTADO, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=2, sort_keys=True)
        f.write("\n")


# ----------------------------- Main -----------------------------

def main(ahora_ms=None):
    if os.environ.get("GITHUB_ACTIONS") and not os.environ.get("NTFY_TOPIC", "").strip():
        sys.exit("Falta el secreto NTFY_TOPIC: créalo en Settings → Secrets and variables → Actions.")

    ahora_ms = ahora_ms or int(time.time() * 1000)
    estado = cargar_estado()
    ultimo_avisado = estado.setdefault("ultimo_avisado", {})

    if estado.get("version") != VERSION:
        tipos = [t for t, on in (("entradas", AVISAR_ENTRADAS), ("posibles entradas", AVISAR_POSIBLES),
                                 ("cambios de tendencia", AVISAR_CAMBIOS_TENDENCIA),
                                 ("cierres", AVISAR_CIERRES)) if on]
        enviar_ntfy(
            "Bot activo con tu indicador",
            "Señales + Barreras ES\n"
            f"Vigilando {len(MONEDAS)} monedas en {', '.join(TEMPORALIDADES)}.\n"
            f"Te aviso de: {', '.join(tipos)}.",
            prioridad=3, etiquetas=["white_check_mark"],
        )
        estado["version"] = VERSION
        estado["iniciado"] = True

    mercados = [(m, tf) for m in MONEDAS for tf in TEMPORALIDADES]

    def descargar(par):
        try:
            return par, obtener_velas(par[0], par[1], ahora_ms), None
        except Exception as e:  # noqa: BLE001
            return par, None, e

    with ThreadPoolExecutor(max_workers=6) as pool:
        resultados = list(pool.map(descargar, mercados))

    avisos, fallos = 0, []
    for (moneda, tf), datos, error in resultados:
        clave = f"{moneda}_{tf}"
        if error:
            fallos.append(f"{clave}: {error}")
            print(f"ERROR {clave}: {error}")
            continue
        velas, fuente = datos
        eventos = indicador.simular(velas, AJUSTES_INDICADOR)

        limite_ms = ahora_ms - MAX_VELAS_ATRASADAS * MS_POR_TF[tf]
        previo = ultimo_avisado.get(clave, 0)
        for i in range(max(0, len(velas) - MAX_VELAS_ATRASADAS), len(velas)):
            vela = velas[i]
            if vela["t"] <= previo or vela["t"] + MS_POR_TF[tf] < limite_ms or not eventos[i]:
                continue
            for ev in eventos[i]:
                aviso = armar_aviso(moneda, tf, vela, ev, ahora_ms)
                if not aviso:
                    continue
                titulo, mensaje, prioridad, etiquetas = aviso
                enviar_ntfy(titulo, mensaje, prioridad=prioridad, etiquetas=etiquetas,
                            enlace=enlace_tradingview(moneda))
                ultimo_avisado[clave] = vela["t"]
                avisos += 1
                print(f"AVISO {titulo} ({fuente}, {len(velas)} velas)")

    if fallos:
        ultimo_error = estado.get("ultimo_aviso_error_ms", 0)
        if ahora_ms - ultimo_error > HORAS_ENTRE_AVISOS_DE_ERROR * 3_600_000:
            enviar_ntfy(
                "⚠️ El bot tuvo problemas",
                f"No pude leer {len(fallos)} de {len(mercados)} mercados.\n"
                + "\n".join(fallos[:5]),
                prioridad=3, etiquetas=["warning"],
            )
            estado["ultimo_aviso_error_ms"] = ahora_ms

    guardar_estado(estado)
    print(f"Listo: {avisos} aviso(s), {len(fallos)} fallo(s).")


if __name__ == "__main__":
    main()
