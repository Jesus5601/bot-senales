"""
Réplica en Python del indicador de TradingView
"Backtester Universal de Señales + Barreras [ES]" (obra derivada de
"Universal Signal Backtester [LuxAlgo]" © LuxAlgo, licencia CC BY-NC-SA 4.0).

Reproduce, vela por vela y en el mismo orden que el script Pine:
  - cruce de medias (EMA 9/21, EMA 12/26 o SMA 50/200) y filtros opcionales
  - barreras de soporte/resistencia por pivotes y toques (con ruptura e inversión)
  - simulación de operaciones con 3 TP y 3 SL -> % de acierto y TP sugerido
  - calificación por estrellas (tendencia, ADX, RSI, volumen)
  - "posibles entradas" (medias acercándose)

La función principal es simular(velas, ajustes): devuelve, para cada vela,
la lista de eventos que el indicador marcaría en esa vela (los mismos de
sus alertas: entrada, posible entrada, cambio de tendencia, cierre).
"""

import math

# Ajustes por defecto = valores por defecto del indicador en TradingView.
AJUSTES_POR_DEFECTO = {
    # Fuente de señales
    "cruceDef": "Cruce EMA 9/21",        # "Cruce EMA 9/21" | "Cruce EMA 12/26" | "Cruce Dorado/Muerte (SMA 50/200)"
    "direccion": "Ambas",                # "Ambas" | "Solo compras" | "Solo ventas"
    "usarRupturaEnt": False,
    # Filtros de calidad
    "usarFiltroATR": False, "lenFiltroATR": 50,
    "usarFiltroTend": False, "lenTend": 200,
    "usarFiltroADX": False, "umbralADX": 20,
    "usarFiltroRSI": False, "rsiSobrecompra": 70, "rsiSobreventa": 30,
    # Barreras
    "lenPivote": 10, "anchoZonaATR": 0.3, "toquesFuerte": 3,
    "maxBarreras": 8, "invertirRotas": True,
    # Objetivos
    "tipoDist": "ATR",                   # "ATR" | "Puntos/Precio"
    "lenATR": 14,
    "useTp1": True, "tp1Val": 1.0,
    "useTp2": True, "tp2Val": 2.0,
    "useTp3": True, "tp3Val": 3.0,
    "useSl1": True, "sl1Val": 1.5,
    "useSl2": True, "sl2Val": 2.5,
    "useSl3": True, "sl3Val": 3.5,
    # Posibles entradas
    "umbralPosible": 0.25, "velasPosible": 5,
    # Métrica del TP sugerido
    "metricaSug": "Tasa de acierto",     # "Tasa de acierto" | "Ganancia esperada" | "Ganancia total" | "Riesgo/Beneficio"
    # Costos (el spread en ticks no se simula; solo la comisión)
    "usarCostos": False, "comision": 0.0,
}


# ------------------------------------------------------------------
# Series técnicas (None = "na" de Pine)
# ------------------------------------------------------------------

def _primer_valido(src):
    for i, v in enumerate(src):
        if v is not None:
            return i
    return None


def sma(src, n):
    res = [None] * len(src)
    f = _primer_valido(src)
    if f is None:
        return res
    suma = 0.0
    for i in range(f, len(src)):
        suma += src[i]
        if i - f >= n:
            suma -= src[i - n]
        if i - f >= n - 1:
            res[i] = suma / n
    return res


def ema(src, n):
    """ta.ema: arranca con la SMA de las primeras n velas."""
    res = [None] * len(src)
    f = _primer_valido(src)
    if f is None or len(src) - f < n:
        return res
    k = 2 / (n + 1)
    s = sum(src[f:f + n]) / n
    res[f + n - 1] = s
    for i in range(f + n, len(src)):
        s = k * src[i] + (1 - k) * s
        res[i] = s
    return res


def rma(src, n):
    """ta.rma (media de Wilder): arranca con la SMA de las primeras n velas."""
    res = [None] * len(src)
    f = _primer_valido(src)
    if f is None or len(src) - f < n:
        return res
    s = sum(src[f:f + n]) / n
    res[f + n - 1] = s
    for i in range(f + n, len(src)):
        s = (s * (n - 1) + src[i]) / n
        res[i] = s
    return res


def true_range(h, l, c, primera_con_rango=True):
    tr = [None] * len(c)
    for i in range(len(c)):
        if i == 0:
            tr[i] = h[0] - l[0] if primera_con_rango else None
        else:
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    return tr


def atr(h, l, c, n):
    return rma(true_range(h, l, c, True), n)


def rsi(c, n):
    up = [None] + [max(c[i] - c[i - 1], 0.0) for i in range(1, len(c))]
    dn = [None] + [max(c[i - 1] - c[i], 0.0) for i in range(1, len(c))]
    ru, rd = rma(up, n), rma(dn, n)
    res = [None] * len(c)
    for i in range(len(c)):
        if ru[i] is None or rd[i] is None:
            continue
        res[i] = 100.0 if rd[i] == 0 else (0.0 if ru[i] == 0 else 100 - 100 / (1 + ru[i] / rd[i]))
    return res


def dmi(h, l, c, di_len=14, adx_len=14):
    n = len(c)
    pdm, mdm = [None] * n, [None] * n
    for i in range(1, n):
        up, down = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > down and up > 0) else 0.0
        mdm[i] = down if (down > up and down > 0) else 0.0
    trur = rma(true_range(h, l, c, False), di_len)
    rp, rm = rma(pdm, di_len), rma(mdm, di_len)
    plus, minus = [None] * n, [None] * n
    ult_p = ult_m = None
    for i in range(n):  # fixnan(100 * rma / trur)
        if trur[i] and rp[i] is not None:
            ult_p = 100 * rp[i] / trur[i]
        if trur[i] and rm[i] is not None:
            ult_m = 100 * rm[i] / trur[i]
        plus[i], minus[i] = ult_p, ult_m
    dx = [None] * n
    for i in range(n):
        if plus[i] is not None and minus[i] is not None:
            s = plus[i] + minus[i]
            dx[i] = abs(plus[i] - minus[i]) / (s if s != 0 else 1)
    adx = [None if v is None else 100 * v for v in rma(dx, adx_len)]
    return plus, minus, adx


def pivote_alto(h, i, izq, der):
    """ta.pivothigh: devuelve el máximo de la vela i-der si es pivote (empate con velas
    anteriores sí vale, empate con velas posteriores no, igual que en Pine)."""
    c = i - der
    if c - izq < 0:
        return None
    v = h[c]
    for k in range(1, izq + 1):
        if h[c - k] > v:
            return None
    for k in range(1, der + 1):
        if h[c + k] >= v:
            return None
    return v


def pivote_bajo(l, i, izq, der):
    c = i - der
    if c - izq < 0:
        return None
    v = l[c]
    for k in range(1, izq + 1):
        if l[c - k] < v:
            return None
    for k in range(1, der + 1):
        if l[c + k] <= v:
            return None
    return v


def _gt(a, b):
    return a is not None and b is not None and a > b


def _lt(a, b):
    return a is not None and b is not None and a < b


# ------------------------------------------------------------------
# Simulación vela por vela
# ------------------------------------------------------------------

class _Nivel:
    __slots__ = ("es_tp", "activo", "precio", "tocado")

    def __init__(self, es_tp):
        self.es_tp, self.activo, self.precio, self.tocado = es_tp, False, None, False

    def toca(self, d, h, l):
        if not self.activo or self.tocado:
            return False
        if self.es_tp:
            return h >= self.precio if d == 1 else l <= self.precio
        return l <= self.precio if d == 1 else h >= self.precio


class _Barrera:
    __slots__ = ("precio", "tipo", "toques", "ancho")

    def __init__(self, precio, tipo, ancho):
        self.precio, self.tipo, self.toques, self.ancho = precio, tipo, 1, ancho


def simular(velas, ajustes=None):
    """
    velas: lista de dicts {"t","o","h","l","c","v"} de la más antigua a la más nueva
           (solo velas cerradas).
    Devuelve una lista del mismo largo: en cada posición, la lista de eventos de esa vela.
    """
    a = dict(AJUSTES_POR_DEFECTO)
    a.update(ajustes or {})

    o = [v["o"] for v in velas]
    h = [v["h"] for v in velas]
    l = [v["l"] for v in velas]
    c = [v["c"] for v in velas]
    vol = [v.get("v", 0.0) for v in velas]
    n = len(velas)

    # --- Medias ---
    if a["cruceDef"] == "Cruce EMA 12/26":
        rapida, lenta, desc = ema(c, 12), ema(c, 26), "EMA 12/26"
    elif a["cruceDef"] == "Cruce Dorado/Muerte (SMA 50/200)":
        rapida, lenta, desc = sma(c, 50), sma(c, 200), "SMA 50/200"
    else:
        rapida, lenta, desc = ema(c, 9), ema(c, 21), "EMA 9/21"

    # --- Indicadores auxiliares ---
    atr_val = [x or 0.0 for x in atr(h, l, c, a["lenATR"])]
    atr14 = [x or 0.0 for x in atr(h, l, c, 14)]
    atr_media = sma(atr14, a["lenFiltroATR"])
    ema_tend = ema(c, a["lenTend"])
    _, _, adx = dmi(h, l, c, 14, 14)
    rsi_val = rsi(c, 14)
    vol_media = sma(vol, 20)

    dist_rl = [None if (rapida[i] is None or lenta[i] is None) else rapida[i] - lenta[i] for i in range(n)]

    puede_comprar = a["direccion"] in ("Ambas", "Solo compras")
    puede_vender = a["direccion"] in ("Ambas", "Solo ventas")

    usar_tps = [a["useTp1"], a["useTp2"], a["useTp3"]]
    val_tps = [a["tp1Val"], a["tp2Val"], a["tp3Val"]]
    usar_sls = [a["useSl1"], a["useSl2"], a["useSl3"]]
    val_sls = [a["sl1Val"], a["sl2Val"], a["sl3Val"]]
    comision = a["comision"] if a["usarCostos"] else 0.0

    barreras = []
    tps = [_Nivel(True) for _ in range(3)]
    sls = [_Nivel(False) for _ in range(3)]
    en_pos, dir_op, entrada, cant, pnl = False, 0, 0.0, 0.0, 0.0
    total_ops, ganadas = 0, 0
    tp_hits = [0, 0, 0]
    pos_c_prev = pos_v_prev = False

    eventos = [[] for _ in range(n)]

    def pnl_parcial(px, qty):
        return (px - entrada) * dir_op * qty - entrada * (comision / 100) * qty

    def estadistica():
        return {"total_ops": total_ops, "ganadas": ganadas}

    for i in range(n):
        ci = c[i]
        cruce_arriba = i > 0 and _gt(rapida[i], lenta[i]) and rapida[i - 1] is not None \
            and lenta[i - 1] is not None and rapida[i - 1] <= lenta[i - 1]
        cruce_abajo = i > 0 and _lt(rapida[i], lenta[i]) and rapida[i - 1] is not None \
            and lenta[i - 1] is not None and rapida[i - 1] >= lenta[i - 1]

        ok_atr = not a["usarFiltroATR"] or _gt(atr14[i], atr_media[i])
        tend_c = not a["usarFiltroTend"] or _gt(ci, ema_tend[i])
        tend_v = not a["usarFiltroTend"] or _lt(ci, ema_tend[i])
        adx_ok = not a["usarFiltroADX"] or _gt(adx[i], a["umbralADX"])
        rsi_c = not a["usarFiltroRSI"] or _lt(rsi_val[i], a["rsiSobrecompra"])
        rsi_v = not a["usarFiltroRSI"] or _gt(rsi_val[i], a["rsiSobreventa"])

        vol_alto = _gt(vol[i], vol_media[i])
        pts_c = (_gt(ci, ema_tend[i]) + _gt(adx[i], a["umbralADX"])
                 + (_gt(rsi_val[i], 50) and _lt(rsi_val[i], a["rsiSobrecompra"])) + vol_alto)
        pts_v = (_lt(ci, ema_tend[i]) + _gt(adx[i], a["umbralADX"])
                 + (_lt(rsi_val[i], 50) and _gt(rsi_val[i], a["rsiSobreventa"])) + vol_alto)

        # --- Barreras: 1) rupturas ---
        rup_alc = rup_baj = False
        for j in range(len(barreras) - 1, -1, -1):
            b = barreras[j]
            fuerte = b.toques >= a["toquesFuerte"]
            rompe_arr = b.tipo == 1 and ci > b.precio + b.ancho / 2
            rompe_ab = b.tipo == -1 and ci < b.precio - b.ancho / 2
            if rompe_arr or rompe_ab:
                rup_alc = rup_alc or (rompe_arr and fuerte)
                rup_baj = rup_baj or (rompe_ab and fuerte)
                if a["invertirRotas"]:
                    b.tipo = -b.tipo
                else:
                    del barreras[j]

        # --- Barreras: 2) nuevos pivotes ---
        ancho = max(atr_val[i] * a["anchoZonaATR"], 0.0)
        for precio in (pivote_alto(h, i, a["lenPivote"], a["lenPivote"]),
                       pivote_bajo(l, i, a["lenPivote"], a["lenPivote"])):
            if precio is None:
                continue
            for b in barreras:
                if abs(b.precio - precio) <= ancho:
                    b.toques += 1
                    break
            else:
                barreras.append(_Barrera(precio, -1 if ci > precio else 1, ancho))
                if len(barreras) > a["maxBarreras"]:
                    barreras.pop(0)

        cambio_alc, cambio_baj = rup_alc, rup_baj

        # --- Señales finales ---
        senal_c = cruce_arriba and ok_atr and tend_c and adx_ok and rsi_c
        senal_v = cruce_abajo and ok_atr and tend_v and adx_ok and rsi_v
        if a["usarRupturaEnt"]:
            senal_c = senal_c or (cambio_alc and ok_atr)
            senal_v = senal_v or (cambio_baj and ok_atr)

        multi = atr_val[i] if a["tipoDist"] == "ATR" else 1.0
        d_tps = [v * multi for v in val_tps]
        d_sls = [v * multi for v in val_sls]

        # --- Gestionar la operación abierta ---
        if en_pos:
            cerrada = False
            px_sl = None
            for nv in sls:
                if nv.toca(dir_op, h[i], l[i]):
                    nv.tocado = True
                    if px_sl is None:
                        px_sl = nv.precio
            if px_sl is not None:
                pnl += pnl_parcial(px_sl, cant)
                cant, cerrada = 0.0, True
            if not cerrada:
                qty = 1.0 / max(1, sum(1 for nv in tps if nv.activo))
                for k, nv in enumerate(tps):
                    if nv.toca(dir_op, h[i], l[i]):
                        nv.tocado = True
                        tp_hits[k] += 1
                        pnl += pnl_parcial(nv.precio, qty)
                        cant -= qty
                if cant <= 0.001:
                    cerrada = True
            if not cerrada and ((dir_op == 1 and senal_v) or (dir_op == -1 and senal_c)):
                pnl += pnl_parcial(ci, cant)
                cant, cerrada = 0.0, True
            if cerrada:
                en_pos = False
                total_ops += 1
                if pnl > 0:
                    ganadas += 1
                if pnl != 0:  # en Pine la alerta de cierre es "ganadora o perdedora"
                    eventos[i].append({
                        "tipo": "cierre", "dir": dir_op, "entrada": entrada,
                        "resultado_pct": pnl / entrada * 100 if entrada else 0.0,
                        **estadistica(),
                    })

        # --- Nuevas entradas ---
        if not en_pos:
            nueva = 1 if (senal_c and puede_comprar) else (-1 if (senal_v and puede_vender) else 0)
            if nueva:
                en_pos, dir_op, entrada, cant, pnl = True, nueva, ci, 1.0, 0.0

                sl_dist = next((d for u, d in zip(usar_sls, d_sls) if u), 0.0)
                mejor_v, mejor_tp, mejor_tasa = -1.0e12, 0, 0.0
                for k in range(3):
                    if usar_tps[k]:
                        tasa = tp_hits[k] / max(1.0, total_ops)
                        if a["metricaSug"] == "Ganancia esperada":
                            v = tasa * d_tps[k]
                        elif a["metricaSug"] == "Ganancia total":
                            v = tp_hits[k] * d_tps[k]
                        elif a["metricaSug"] == "Riesgo/Beneficio":
                            v = d_tps[k] / sl_dist if sl_dist else 0.0
                        else:
                            v = tasa * 100
                        if v > mejor_v:
                            mejor_v, mejor_tp, mejor_tasa = v, k + 1, tasa * 100

                dist_aviso = d_tps[0] if a["useTp1"] else atr_val[i]
                barrera_cerca = any(
                    b.toques >= a["toquesFuerte"] and (
                        (nueva == 1 and b.tipo == 1 and b.precio > ci and b.precio - ci <= dist_aviso)
                        or (nueva == -1 and b.tipo == -1 and b.precio < ci and ci - b.precio <= dist_aviso))
                    for b in barreras)

                for k, nv in enumerate(tps):
                    nv.activo, nv.precio, nv.tocado = usar_tps[k], ci + nueva * d_tps[k], False
                for k, nv in enumerate(sls):
                    nv.activo, nv.precio, nv.tocado = usar_sls[k], ci - nueva * d_sls[k], False

                eventos[i].append({
                    "tipo": "entrada", "dir": nueva, "precio": ci, "media": desc,
                    "estrellas": pts_c if nueva == 1 else pts_v,
                    "tp_sugerido": mejor_tp, "tp_sugerido_pct": mejor_tasa,
                    "barrera_cerca": barrera_cerca,
                    "tps": [nv.precio if nv.activo else None for nv in tps],
                    "sls": [nv.precio if nv.activo else None for nv in sls],
                    **estadistica(),
                })

        # --- Posibles entradas (medias acercándose) ---
        d0 = dist_rl[i]
        d1 = dist_rl[i - 1] if i >= 1 else None
        d2 = dist_rl[i - 2] if i >= 2 else None
        conv_alza = conv_baja = False
        if None not in (d0, d1, d2):
            cerca = abs(d0) < atr_val[i] * a["umbralPosible"]
            conv_alza = d0 < 0 and d0 > d1 and d1 > d2 and cerca
            conv_baja = d0 > 0 and d0 < d1 and d1 < d2 and cerca
        pos_c = conv_alza and puede_comprar
        pos_v = conv_baja and puede_vender
        if pos_c and not pos_c_prev and not (en_pos and dir_op == 1):
            eventos[i].append({"tipo": "posible", "dir": 1, "precio": ci, "media": desc, **estadistica()})
        if pos_v and not pos_v_prev and not (en_pos and dir_op == -1):
            eventos[i].append({"tipo": "posible", "dir": -1, "precio": ci, "media": desc, **estadistica()})
        pos_c_prev, pos_v_prev = pos_c, pos_v

        # --- Cambios de tendencia (ruptura de barrera fuerte) ---
        if cambio_alc:
            eventos[i].append({"tipo": "cambio", "dir": 1, "precio": ci})
        if cambio_baj:
            eventos[i].append({"tipo": "cambio", "dir": -1, "precio": ci})

    return eventos
