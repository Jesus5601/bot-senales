#!/usr/bin/env python3
"""
Bot de señales -> avisos al celular con ntfy.

Cada vez que se ejecuta (GitHub Actions lo corre cada ~5 minutos), revisa
cada moneda en cada temporalidad y avisa si aparece una señal en una vela
YA CERRADA. Guarda en estado.json qué velas ya avisó para no repetir.

Solo usa la librería estándar de Python: no hay que instalar nada.
"""

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ======================= CONFIGURACIÓN =======================

MONEDAS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "SHIBUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT",
]
TEMPORALIDADES = ["15m", "1h", "4h"]

# Gráfico de TradingView que se abre al tocar el aviso (perpetuos de Binance).
# Por defecto BINANCE:<MONEDA>.P; aquí van solo las excepciones.
SIMBOLO_TRADINGVIEW = {"SHIBUSDT": "BINANCE:1000SHIBUSDT.P"}

# --- Estrategia PROVISIONAL (se reemplaza por la lógica de tu indicador) ---
EMA_RAPIDA = 9
EMA_LENTA = 21
EMA_TENDENCIA = 200
RSI_PERIODO = 14
RSI_MAX_LONG = 70     # no avisar LONG si el RSI ya está sobrecomprado
RSI_MIN_SHORT = 30    # no avisar SHORT si el RSI ya está sobrevendido
ATR_PERIODO = 14
SL_ATR = 1.5          # stop de referencia = 1.5 x ATR desde el cierre
TP_RATIO = 2.0        # take profit de referencia = 2 x la distancia del stop

# Si GitHub se atrasa, se revisan velas cerradas hace como máximo
# esta cantidad de velas (15m -> 45 min, 1h -> 3 h, 4h -> 12 h).
MAX_VELAS_ATRASADAS = 3

# Aviso de error al celular como máximo cada tantas horas.
HORAS_ENTRE_AVISOS_DE_ERROR = 6

ARCHIVO_ESTADO = "estado.json"
ZONA_LIMA = timezone(timedelta(hours=-5))

# =============================================================

MS_POR_TF = {"15m": 15 * 60_000, "1h": 60 * 60_000, "4h": 4 * 60 * 60_000}


# ---------------------------- Datos ----------------------------

def http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "bot-senales"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def velas_binance(moneda, tf):
    # data-api.binance.vision: datos públicos de Binance que funcionan
    # también desde los servidores de GitHub (que están en EE.UU.).
    url = ("https://data-api.binance.vision/api/v3/klines"
           f"?symbol={moneda}&interval={tf}&limit=1000")
    return [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
         "l": float(k[3]), "c": float(k[4])}
        for k in http_json(url)
    ]


def velas_okx(moneda, tf):
    # Respaldo: perpetuos de OKX (máx. 300 velas, la EMA 200 queda aproximada).
    inst = moneda[:-4] + "-USDT-SWAP"
    barra = {"15m": "15m", "1h": "1H", "4h": "4H"}[tf]
    url = ("https://www.okx.com/api/v5/market/candles"
           f"?instId={inst}&bar={barra}&limit=300")
    datos = http_json(url).get("data") or []
    return [
        {"t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
         "l": float(k[3]), "c": float(k[4])}
        for k in reversed(datos)  # OKX las da de la más nueva a la más vieja
    ]


FUENTES = [("Binance", velas_binance), ("OKX", velas_okx)]


def obtener_velas(moneda, tf, ahora_ms):
    """Velas CERRADAS, de la más antigua a la más nueva."""
    errores = []
    for nombre, fuente in FUENTES:
        try:
            velas = fuente(moneda, tf)
            cerradas = [v for v in velas if v["t"] + MS_POR_TF[tf] <= ahora_ms]
            if len(cerradas) > EMA_TENDENCIA + 2:
                return cerradas, nombre
            errores.append(f"{nombre}: solo {len(cerradas)} velas")
        except Exception as e:  # noqa: BLE001
            errores.append(f"{nombre}: {e}")
    raise RuntimeError(" | ".join(errores))


# ------------------------- Indicadores -------------------------

def ema(valores, n):
    res = [None] * len(valores)
    if len(valores) < n:
        return res
    k = 2 / (n + 1)
    s = sum(valores[:n]) / n
    res[n - 1] = s
    for i in range(n, len(valores)):
        s = valores[i] * k + s * (1 - k)
        res[i] = s
    return res


def rma(valores, n):
    """Media de Wilder (la que usa TradingView para RSI y ATR)."""
    res = [None] * len(valores)
    if len(valores) < n:
        return res
    s = sum(valores[:n]) / n
    res[n - 1] = s
    for i in range(n, len(valores)):
        s = (s * (n - 1) + valores[i]) / n
        res[i] = s
    return res


def rsi(cierres, n):
    subidas = [max(cierres[i] - cierres[i - 1], 0.0) for i in range(1, len(cierres))]
    bajadas = [max(cierres[i - 1] - cierres[i], 0.0) for i in range(1, len(cierres))]
    res = [None]
    for a, b in zip(rma(subidas, n), rma(bajadas, n)):
        if a is None:
            res.append(None)
        elif b == 0:
            res.append(100.0)
        else:
            res.append(100 - 100 / (1 + a / b))
    return res


def atr(velas, n):
    rangos = [velas[0]["h"] - velas[0]["l"]]
    for i in range(1, len(velas)):
        h, l, cp = velas[i]["h"], velas[i]["l"], velas[i - 1]["c"]
        rangos.append(max(h - l, abs(h - cp), abs(l - cp)))
    return rma(rangos, n)


def calcular_indicadores(velas):
    cierres = [v["c"] for v in velas]
    return {
        "rapida": ema(cierres, EMA_RAPIDA),
        "lenta": ema(cierres, EMA_LENTA),
        "tendencia": ema(cierres, EMA_TENDENCIA),
        "rsi": rsi(cierres, RSI_PERIODO),
        "atr": atr(velas, ATR_PERIODO),
    }


# -------------------------- Estrategia --------------------------

def calcular_senal(velas, i, ind):
    """
    Devuelve ("LONG" | "SHORT", motivo) o None para la vela i.

    >>> ESTA es la única función que hay que cambiar para usar la lógica
    >>> de tu indicador de TradingView.

    Estrategia provisional:
      LONG : la EMA 9 cruza hacia arriba la EMA 21, el cierre está sobre
             la EMA 200 y el RSI está por debajo de 70.
      SHORT: la EMA 9 cruza hacia abajo la EMA 21, el cierre está bajo
             la EMA 200 y el RSI está por encima de 30.
    """
    r, l, t, x = ind["rapida"], ind["lenta"], ind["tendencia"], ind["rsi"]
    if i < 1 or None in (r[i - 1], l[i - 1], r[i], l[i], t[i], x[i]):
        return None
    cierre = velas[i]["c"]
    if r[i - 1] <= l[i - 1] and r[i] > l[i] and cierre > t[i] and x[i] < RSI_MAX_LONG:
        return "LONG", f"Cruce EMA {EMA_RAPIDA}/{EMA_LENTA} al alza, sobre la EMA {EMA_TENDENCIA}"
    if r[i - 1] >= l[i - 1] and r[i] < l[i] and cierre < t[i] and x[i] > RSI_MIN_SHORT:
        return "SHORT", f"Cruce EMA {EMA_RAPIDA}/{EMA_LENTA} a la baja, bajo la EMA {EMA_TENDENCIA}"
    return None


# --------------------------- Avisos ----------------------------

def formato_precio(p):
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


def armar_aviso(moneda, tf, velas, i, ind, direccion, motivo, ahora_ms):
    cierre = velas[i]["c"]
    distancia = ind["atr"][i] * SL_ATR if ind["atr"][i] else None
    lineas = [f"Precio: {formato_precio(cierre)}", motivo, f"RSI: {ind['rsi'][i]:.1f}"]
    if distancia:
        if direccion == "LONG":
            sl, tp = cierre - distancia, cierre + distancia * TP_RATIO
        else:
            sl, tp = cierre + distancia, cierre - distancia * TP_RATIO
        lineas.append(f"SL ref: {formato_precio(sl)} · TP ref: {formato_precio(tp)}")

    cierre_vela_ms = velas[i]["t"] + MS_POR_TF[tf]
    hora = datetime.fromtimestamp(cierre_vela_ms / 1000, ZONA_LIMA).strftime("%d/%m %H:%M")
    lineas.append(f"Vela de {tf} cerrada {hora} (Lima)")
    atraso_min = (ahora_ms - cierre_vela_ms) / 60_000
    if atraso_min > 10:
        lineas.append(f"⚠️ Aviso atrasado {atraso_min:.0f} min")

    titulo = f"{direccion} {moneda} · {tf}"
    etiquetas = ["green_circle"] if direccion == "LONG" else ["red_circle"]
    return titulo, "\n".join(lineas), etiquetas


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

    if not estado.get("iniciado"):
        enviar_ntfy(
            "Bot de señales activo",
            f"Vigilando {len(MONEDAS)} monedas en {', '.join(TEMPORALIDADES)}.\n"
            "Te aviso cuando haya una señal en una vela cerrada.",
            prioridad=3, etiquetas=["white_check_mark"],
        )
        estado["iniciado"] = True

    avisos, fallos = 0, []
    for moneda in MONEDAS:
        for tf in TEMPORALIDADES:
            clave = f"{moneda}_{tf}"
            try:
                velas, fuente = obtener_velas(moneda, tf, ahora_ms)
            except Exception as e:  # noqa: BLE001
                fallos.append(f"{clave}: {e}")
                print(f"ERROR {clave}: {e}")
                continue

            ind = calcular_indicadores(velas)
            limite_ms = ahora_ms - MAX_VELAS_ATRASADAS * MS_POR_TF[tf]
            previo = ultimo_avisado.get(clave, 0)

            for i in range(max(1, len(velas) - MAX_VELAS_ATRASADAS), len(velas)):
                cierre_vela_ms = velas[i]["t"] + MS_POR_TF[tf]
                if velas[i]["t"] <= previo or cierre_vela_ms < limite_ms:
                    continue
                senal = calcular_senal(velas, i, ind)
                if not senal:
                    continue
                direccion, motivo = senal
                titulo, mensaje, etiquetas = armar_aviso(
                    moneda, tf, velas, i, ind, direccion, motivo, ahora_ms)
                enviar_ntfy(titulo, mensaje, prioridad=4, etiquetas=etiquetas,
                            enlace=enlace_tradingview(moneda))
                ultimo_avisado[clave] = velas[i]["t"]
                avisos += 1
                print(f"AVISO {titulo} ({fuente})")

    if fallos:
        ultimo_error = estado.get("ultimo_aviso_error_ms", 0)
        if ahora_ms - ultimo_error > HORAS_ENTRE_AVISOS_DE_ERROR * 3_600_000:
            enviar_ntfy(
                "⚠️ El bot tuvo problemas",
                f"No pude leer {len(fallos)} de {len(MONEDAS) * len(TEMPORALIDADES)} mercados.\n"
                + "\n".join(fallos[:5]),
                prioridad=3, etiquetas=["warning"],
            )
            estado["ultimo_aviso_error_ms"] = ahora_ms

    guardar_estado(estado)
    print(f"Listo: {avisos} aviso(s), {len(fallos)} fallo(s).")


if __name__ == "__main__":
    main()
