# Buenos días, Bogotá · Diario inteligente + Radar semanal

Dos productos automatizados en Python 3.12+ sobre Telegram y GitHub Pages:

1. **☀️ Aviso diario inteligente (`brief.main`)** — cada mañana a las **8:00 a.m. (America/Bogota)** evalúa reglas de relevancia.
   - **Si ninguna regla se activa:** envía un mensaje mínimo de **2 líneas** con notificación silenciosa (`disable_notification=true`): saludo corto + una cosa útil del día.
   - **Si alguna regla se activa:** envía un mensaje **con sonido**, de **4 a 6 líneas**, empezando por lo más importante (lluvia >= 70 % en tu franja de salida, cierres/paros/marchas o pico y placa que afecte tu placa, AQI dañino, sensación térmica extrema, UV >= 8, movimiento de TRM >= 1,5 % o mínimo/máximo de 30 días, festivo hoy o mañana).
   - **Variedad real:** más de 40 plantillas de saludo sin repetir la misma estructura dos días seguidos (se persiste `greeting_id` en `data/history.csv`) y más de 100 citas y aforismos en español.
   - Guarda `data/history.csv`, `docs/latest.json` y `docs/history.json` todos los días.

2. **📡 Radar semanal de tecnología (`brief.weekly`)** — cada **domingo a las 6:00 p.m. (America/Bogota)** envía un digest curado con sonido:
   - **Fuentes verificadas:** arXiv API (`cs.LG`, `cs.AI`, `cs.CL`, `stat.ML`, `math.OC`, `cs.CY`), Hugging Face Daily Papers, Semantic Scholar, Hacker News (API oficial) y RSS técnicos (MIT Technology Review, Ars Technica, Hugging Face Blog, Simon Willison, Google Research Blog, Quanta Magazine).
   - **7 piezas curadas:** 3 papers (*accesible*, *profundo*, *aplicado*), 2 noticias o lanzamientos de alto impacto, 1 herramienta o repo para probar y 1 *joya* inesperada.
   - **Conectado con tus proyectos:** cada paper incluye *"En una línea para mi proyecto"* conectando la idea con **Aula Ya**, **Table Master** o **PostMark**.
   - **Formato vivo:** historia de la semana, rotación entre 3 estructuras (`ranking_top5`, `tres_cambios`, `pregunta_semana`), *Concepto en 60 segundos* con ejemplo cotidiano, *Reto de la semana* (15–30 min) y *Mini quiz* interactivo (`sendPoll` tipo quiz).
   - **Aprendizaje con botones inline:** cada mensaje incluye botones (`📌 Guardar`, `✅ Ya la leí`, `🔼 Más como esto`, `🔽 Menos como esto`). Los workflows diario y semanal procesan `getUpdates` (`callback_query`) con control de `offset` sin duplicados, ajustan los pesos de tus intereses en `data/feedback.json` y archivan tus lecturas guardadas en `docs/library.json` y `biblioteca/AAAA.md`.
   - **Archivo público:** cada edición se guarda en `digests/AAAA/AAAA-Wnn.md`, actualiza `digests/README.md` y publica un buscador por tema en `docs/digests.html`.

---

## Arquitectura

```
GitHub Actions
 ├─ daily.yml   (7:40 a.m. + respaldo 8:10 a.m. Bogotá) → python -m brief.main
 │   ├─ feedback.sync_telegram_feedback()   # procesa clics inline pendientes sin duplicar
 │   ├─ sources/* + consensus               # mediana, dispersión y % de confianza
 │   ├─ relevance.evaluate_relevance()      # 2 líneas silenciosas vs. 4-6 líneas con sonido
 │   └─ history                             # data/history.csv + docs/latest.json + docs/history.json
 │
 └─ weekly.yml  (Domingo 6:00 p.m. + respaldo 6:30 p.m. Bogotá) → python -m brief.weekly
     ├─ feedback.sync_telegram_feedback()   # actualiza data/feedback.json y biblioteca/AAAA.md
     ├─ sources/tech_sources.py             # arXiv, HF Papers, Semantic Scholar, HN, RSS
     ├─ weekly.build_weekly_digest()        # puntuación, diversidad, LLM opcional o extractivo <=40 palabras
     ├─ telegram (2-3 mensajes + sendPoll)  # teclados inline + mini quiz explicado
     └─ weekly.write_digest_archive()       # digests/AAAA/AAAA-Wnn.md + digests/README.md + docs/digests.json
```

---

## Uso local y flags CLI

```bash
pip install -r requirements.txt

# Aviso diario inteligente
python -m brief.main --dry-run                       # imprime el mensaje diario sin enviar ni guardar
python -m brief.main --dry-run --as-of 08:00         # simula las 08:00 (hora Bogotá) para las franjas del clima
python -m brief.main --no-wait --force               # envía de inmediato ignorando el control de duplicados

# Radar semanal de tecnología
python -m brief.weekly --dry-run --write-archive     # genera el digest semanal actual y escribe digests/
python -m brief.weekly --dry-run --week 2026-W48     # genera o regenera una semana ISO específica
python -m brief.main --dry-run --week 2026-W48       # atajo equivalente desde brief.main

# Calidad y tests (100 % offline)
python -m ruff check .
python -m pytest -v
```

---

## Cómo personalizar intereses, proyectos y fuentes

Todo se configura en [config.yaml](config.yaml):

- **Reglas del diario inteligente (`relevance:`):** ajusta `outing_periods` (`["mañana", "tarde"]`), `rain_outing_pct` (`70.0`), `feels_cold_extreme_c` (`9.0`), `feels_hot_extreme_c` (`23.0`), `uv_extreme` (`8.0`), `aqi_harmful` (`101.0`) y `trm_change_pct` (`1.5`).
- **Intereses ponderados (`weekly_digest.interests:`):** cada tema (`ml_applied`, `small_models_edge`, `edtech`, `prob_optimization`, `software_engineering`, `open_data_latam`) tiene su etiqueta (`label`), peso inicial (`weight`) y lista de palabras clave (`keywords`). Los clics en `🔼 Más como esto` / `🔽 Menos como esto` en Telegram ajustan automáticamente los pesos en `data/feedback.json`.
- **Proyectos propios (`weekly_digest.projects:`):** define los temas y el ángulo de **Aula Ya**, **Table Master** y **PostMark** para la línea de aplicación de cada paper.
- **Fuentes del Radar semanal (`weekly_digest.sources:`):** activa o desactiva `arxiv`, `huggingface_papers`, `semantic_scholar`, `hacker_news` o agrega feeds en `rss_feeds` con `{name, url, topic, language, enabled}`.

---

## Secretos (Settings → Secrets and variables → Actions)

| Secreto | Obligatorio | Uso |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí | Envío de mensajes, encuestas (`sendPoll`) y lectura de botones (`getUpdates`) |
| `TELEGRAM_CHAT_ID` | Sí | Chat destino en Telegram |
| `ANTHROPIC_API_KEY` | No | Opcional: resúmenes y ganchos con Claude (prompt estricto sin inventar datos; sin clave usa resumen extractivo propio <= 40 palabras) |
| `WEATHERAPI_KEY` | No | Fuente adicional de clima |
| `OPENWEATHER_API_KEY` | No | Fuente adicional de clima |
| `WAQI_TOKEN` | No | Fuente adicional de calidad del aire |
