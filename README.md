# bot-senales

Bot que reproduce el indicador de TradingView **"Backtester Universal de Señales + Barreras [ES]"** y manda un aviso al celular (app **ntfy**) cuando, en una vela cerrada, aparece:

- **COMPRA / VENTA** con sus estrellas, el % de acierto del indicador, el TP sugerido, los TP/SL y el aviso de barrera fuerte cerca.
- **◆ Posible compra / ◆ Posible venta** (las medias se están acercando), con el % de acierto.

Opcionales (apagados): cambios de tendencia por ruptura de barrera fuerte y cierres de operación.

- **Monedas:** BTC, ETH, BNB, SOL, SHIB, XRP, DOGE, LINK (contra USDT)
- **Temporalidades:** 1h, 4h y 1d (diaria; también se puede usar 15m)
- **Datos:** Binance, últimas ~5000 velas (respaldo: perpetuos de OKX)
- **Corre en:** GitHub Actions cada ~5 minutos, gratis; no necesitas tener la PC encendida

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `bot.py` | El bot. Arriba está la configuración: monedas, temporalidades, qué avisos quieres y los ajustes del indicador. |
| `indicador.py` | La lógica del indicador traducida de Pine a Python. |
| `.github/workflows/senales.yml` | Le dice a GitHub que corra el bot cada 5 minutos. |
| `estado.json` | Lo crea el bot solo; recuerda qué velas ya avisó para no repetir. |

## Configuración

En **Settings → Secrets and variables → Actions** debe existir el secreto `NTFY_TOPIC` con el nombre de tu tema de ntfy.

## Si cambiaste ajustes del indicador en TradingView

El bot usa los valores por defecto del indicador. Si en TradingView activaste algún filtro o cambiaste algún valor, escríbelo en `AJUSTES_INDICADOR` dentro de `bot.py` con el mismo nombre que tiene en el código Pine. Por ejemplo:

```python
AJUSTES_INDICADOR = {
    "usarFiltroTend": True,
    "usarFiltroRSI": True,
}
```

## Diferencias con el gráfico

- El % de acierto se calcula con las últimas ~5000 velas de Binance; TradingView lo calcula con las velas que tenga cargadas tu gráfico, así que el número puede variar un poco.
- Los precios salen del mercado spot de Binance (la API de futuros no responde desde los servidores de GitHub). La diferencia con el perpetuo es mínima.
- Los avisos llegan al cierre de la vela más el retraso de GitHub (a veces 5–15 min).

## Licencia

`indicador.py` es una obra derivada de "Universal Signal Backtester [LuxAlgo]" © LuxAlgo y se distribuye bajo [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/).
