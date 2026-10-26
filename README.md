# Buenos días, Bogotá

Bot que cada día a las **8:00 a.m. (America/Bogota)** envía por Telegram un resumen personalizado: clima, calidad del aire, TRM, movilidad, noticias y una frase del día, con datos cruzados entre varias fuentes. Guarda un histórico en el repo (commits diarios) y publica un dashboard.

**Dashboard:** `https://<TU_USUARIO>.github.io/buenos-dias-bogota/`

## Arquitectura

```
GitHub Actions (cron 7:40 a.m. Bogotá) → python -m brief.main
  ├─ sources/*  consultan APIs/RSS en paralelo (concurrent.futures), con timeout y reintentos
  ├─ consensus  mediana + dispersión + porcentaje de confianza; rango si las fuentes difieren
  ├─ personalization  saludo variable (día, clima, festivos con `holidays` CO)
  ├─ formatter  HTML de Telegram, escapado, división limpia a 4096 caracteres
  ├─ telegram   sendMessage (sin reintentos en POST para no duplicar)
  └─ history    data/history.csv + docs/latest.json + docs/history.json → commit → GitHub Pages
```

- **Clima:** Open-Meteo con varios modelos (por variable, en `config.yaml`) + WeatherAPI y OpenWeatherMap (opcionales) + IDEAM (datos.gov.co `sbwg-7ju4`, se descarta si es antiguo sus datos). Incluye sensación térmica por franja (mediana entre fuentes).
- **Aire:** Open-Meteo Air Quality + WAQI (opcional, token).
- **TRM:** dataset oficial `32sa-8pi3` (datos.gov.co); respaldo open.er-api.com (tasa de mercado, solo contraste).
- **Movilidad (solo Bogotá):** RSS (feedparser), últimas 24 h, máx. 4. Una noticia entra solo si menciona Bogotá (ciudad, localidades, avenidas, sistemas) **y** una palabra clave de movilidad (o, en segundo lugar, servicios públicos/orden público). Deduplicación con rapidfuzz. Sin resultados: "Sin alertas de movilidad importantes por ahora".
- **Una buena noticia:** feeds en español (BBC Mundo, DW, Noticias ONU, El País, Europa Press, Mongabay), últimas 48 h, filtro de palabras positivas/negativas. Si nada pasa, se omite.
- **Frases:** solo `data/quotes_es.json` (100+ frases en español, autor o "Anónimo"), selección determinista por fecha sin repetir en 90 días.
- **Espera y duplicados:** si el script llega antes de las 8:00 espera (máx. 25 min); si ya hay un envío del día en `history.csv`, no reenvía.

## Confianza del dato (porcentaje)

Cada sección (clima, lluvia, aire, TRM) muestra "Confianza del dato: N %". Es una **medida de concordancia entre las fuentes disponibles, no una probabilidad estadística de acierto**.

```
puntaje = 100
        - min(50, 30 × dispersión / umbral)        # umbral de config.yaml (°C, puntos de lluvia, AQI, % TRM)
        - 8 × (fuentes esperadas − fuentes que votaron)   # fallidas o descartadas
puntaje = limitado a [30, 98]
si solo votó una fuente: máximo 60
```

Los parámetros están en la sección `confidence` de `config.yaml`. En la lluvia, además se resta hasta 30 puntos si los modelos de cantidad (mm) discrepan. Los nombres de modelos y fuentes no aparecen en el mensaje de Telegram, pero se guardan en `history.csv` y `docs/latest.json` para depurar y para el dashboard.

## Uso local

```
pip install -r requirements.txt
python -m brief.main --dry-run          # imprime sin enviar ni escribir
python -m brief.main --no-wait          # envía sin esperar a las 8:00
python -m brief.main --force            # ignora el control de duplicados
python -m brief.main --dry-run --as-of 08:00  # simula las 08:00 (hora Bogotá) para ver todas las franjas sin "ya pasó"
pytest && ruff check .
```

## Secretos (Settings → Secrets and variables → Actions)

| Secreto | Obligatorio |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí |
| `TELEGRAM_CHAT_ID` | Sí |
| `WEATHERAPI_KEY` | No |
| `OPENWEATHER_API_KEY` | No |
| `WAQI_TOKEN` | No (https://aqicn.org/data-platform/token/) |

## Crear el bot de Telegram

1. En Telegram abre **@BotFather** → `/newbot` → elige nombre y usuario → copia el token (`TELEGRAM_BOT_TOKEN`).
2. Abre tu bot y envíale cualquier mensaje (`/start`).
3. Visita `https://api.telegram.org/bot<TOKEN>/getUpdates` y copia `message.chat.id` (`TELEGRAM_CHAT_ID`).
4. Guarda ambos como secretos del repo.

## Activar GitHub Pages

Settings → Pages → *Deploy from a branch* → rama `main`, carpeta `/docs` → Save.

## Configuración

Todo en [config.yaml](config.yaml): nombre, ciudad, placa, temas, umbrales, feeds y palabras clave.

## Limitaciones conocidas

- **Pico y placa:** la regla vigente no se pudo verificar automáticamente, por lo que es configurable (`plate`, `mode`, `restricted`). Si hay placa pero no modo, el mensaje te remite a movilidadbogota.gov.co. No se inventa.
- **Feeds deshabilitados** por fallar al verificarlos: RCN, Citytv (403), Secretaría de Movilidad (404), Transmilenio (404), Caracol Bogotá (vacío). La Alcaldía funciona pero publica poco.
- **RMCAB:** no hay dataset en datos.gov.co; la calidad del aire se apoya en Open-Meteo y WAQI.
- **IDEAM:** a menudo desactualizado; se descarta cuando es así.
- Los nombres de medios en las noticias son citas de la fuente, no fuentes de datos de clima/aire/TRM.
- GitHub desactiva crons tras 60 días sin actividad del repo; los commits diarios lo evitan. Los crons pueden retrasarse (el script ya lo contempla).
