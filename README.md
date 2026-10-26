# Buenos días, Bogotá

Bot que cada día a las **8:00 a.m. (America/Bogota)** envía por Telegram un resumen personalizado: clima, calidad del aire, TRM, movilidad, noticias y una frase del día, con datos cruzados entre varias fuentes. Guarda un histórico en el repo (commits diarios) y publica un dashboard.

**Dashboard:** `https://<TU_USUARIO>.github.io/buenos-dias-bogota/`

## Arquitectura

```
GitHub Actions (cron 7:40 a.m. Bogotá) → python -m brief.main
  ├─ sources/*  consultan APIs/RSS en paralelo (concurrent.futures), con timeout y reintentos
  ├─ consensus  mediana + dispersión + confianza (alta/media/baja); rango si las fuentes difieren
  ├─ personalization  saludo variable (día, clima, festivos con `holidays` CO)
  ├─ formatter  HTML de Telegram, escapado, división limpia a 4096 caracteres
  ├─ telegram   sendMessage (sin reintentos en POST para no duplicar)
  └─ history    data/history.csv + docs/latest.json + docs/history.json → commit → GitHub Pages
```

- **Clima:** Open-Meteo con 3 modelos (ecmwf_ifs025, gfs_seamless, icon_seamless) + WeatherAPI y OpenWeatherMap (opcionales) + IDEAM (datos.gov.co `sbwg-7ju4`, se descarta si es antiguo).
- **Aire:** Open-Meteo Air Quality + WAQI (opcional, token).
- **TRM:** dataset oficial `32sa-8pi3` (datos.gov.co); respaldo open.er-api.com (tasa de mercado, solo contraste).
- **Noticias:** RSS (feedparser), últimas 24 h, deduplicación con rapidfuzz, puntuación por palabras clave.
- **Espera y duplicados:** si el script llega antes de las 8:00 espera (máx. 25 min); si ya hay un envío del día en `history.csv`, no reenvía.

## Uso local

```
pip install -r requirements.txt
python -m brief.main --dry-run          # imprime sin enviar ni escribir
python -m brief.main --no-wait          # envía sin esperar a las 8:00
python -m brief.main --force            # ignora el control de duplicados
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
- **Feeds deshabilitados** por fallar al verificarlos: RCN, Citytv, Movilidad Bogotá.
- **RMCAB:** no hay dataset en datos.gov.co; la calidad del aire se apoya en Open-Meteo y WAQI.
- **IDEAM:** a menudo desactualizado; se descarta cuando es así.
- **ZenQuotes** devuelve frases en inglés; por eso se mezcla con 60+ frases locales.
- GitHub desactiva crons tras 60 días sin actividad del repo; los commits diarios lo evitan. Los crons pueden retrasarse (el script ya lo contempla).
