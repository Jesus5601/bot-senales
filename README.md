# bot-senales

Bot que revisa criptomonedas cada ~5 minutos y manda un aviso al celular (app **ntfy**) cuando aparece una señal en una vela cerrada.

- **Monedas:** BTC, ETH, BNB, SOL, SHIB, XRP, DOGE, LINK (contra USDT)
- **Temporalidades:** 15m, 1h y 4h
- **Datos:** Binance (respaldo: perpetuos de OKX)
- **Corre en:** GitHub Actions, gratis; no necesitas tener la PC encendida

## Archivos

| Archivo | Para qué sirve |
|---|---|
| `bot.py` | El bot. Arriba está la configuración (monedas, temporalidades, parámetros). |
| `.github/workflows/senales.yml` | Le dice a GitHub que corra el bot cada 5 minutos. |
| `estado.json` | Lo crea el bot solo; recuerda qué señales ya avisó para no repetirlas. |

## Configuración

En **Settings → Secrets and variables → Actions** debe existir el secreto `NTFY_TOPIC` con el nombre de tu tema de ntfy.

## Cambiar monedas o temporalidades

Edita las listas `MONEDAS` y `TEMPORALIDADES` al inicio de `bot.py`. Temporalidades disponibles: `15m`, `1h`, `4h`.

## Estrategia

La estrategia actual es **provisional** (cruce de EMA 9/21 con filtro de EMA 200 y RSI). Para usar la lógica de tu indicador de TradingView solo hay que reemplazar la función `calcular_senal` en `bot.py`.

Los niveles "SL ref" y "TP ref" del aviso son solo una referencia (1.5 × ATR y relación 1:2), no una recomendación.
