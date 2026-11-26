# Buenos días, Bogotá · Diario inteligente + Radar semanal

Dos productos automatizados en Python 3.12+ sobre Telegram y GitHub Pages:

1. **☀️ Aviso diario inteligente (`python -m brief.main`)** — cada mañana a las **8:00 a.m. (America/Bogota)** evalúa reglas de relevancia sobre clima, movilidad, aire, TRM y festivos:
   - **Modo silencioso (`disable_notification=true`, 2 líneas):** si ninguna regla se activa, envía un saludo corto + una línea `"Hoy:"` con clima tranquilo, TRM y datos útiles no urgentes (p. ej. pico y placa informativo, cierres programados para el fin de semana o buena noticia verificada).
   - **Modo con sonido (4 a 6 líneas):** si se cumple alguna regla, envía notificación con sonido empezando por la alerta de mayor prioridad:
     - **Lluvia en franja de salida:** probabilidad `>= 70 %` en tus franjas configuradas (`mañana`, `tarde`).
     - **Movilidad con impacto hoy o en <= 24 h:** solo suena si hay efecto real en las vías o el servicio (cierre, desvío, paro, marcha, bloqueo, suspensión, accidente con afectación) que ocurra **hoy o en las próximas 24 horas** (detectando fechas y días en titular/resumen; los cierres de fin de semana solo suenan viernes/sábado/domingo y de lunes a jueves van en el mensaje silencioso; las notas *"en vivo"* solo cuentan si el titular menciona un efecto actual) o si el **pico y placa afecta a tu placa configurada**. Las noticias rutinarias de pico y placa nunca disparan sonido.
     - **Calidad del aire dañina:** AQI US EPA `>= 101`.
     - **Sensación térmica o UV extremos:** sensación `<= 9 °C` o `>= 23 °C`, o índice UV `>= 8`.
     - **Movimiento relevante de TRM:** variación diaria `>= 1,5 %` frente al último día hábil distinto (según vigencia oficial de Datos Abiertos) o nuevo mínimo/máximo de 30 días con **enfriamiento de 5 días** (`trm_extreme_cooldown_days: 5`, persistido en `data/sent.json`) para no repetir alertas durante una misma tendencia.
     - **Festivo en Colombia:** hoy o mañana.
   - **Coherencia y sin duplicados:** la línea `"Hoy:"` solo dice `"Clima tranquilo"` cuando no hay ninguna regla climática activa, nunca repite datos ya mostrados en las alertas (lluvia o TRM), y muestra el porcentaje de confianza una sola vez por variable.
   - **Variedad real:** 44 plantillas de saludo sin repetir la misma estructura dos días seguidos (`greeting_id` en `data/history.csv`), más de 100 citas y aforismos en español (`data/quotes_es.json`, sin letras de canciones) y filtro estricto de buena noticia (100 % en español, exigiendo término positivo y descartando polémicas, sanciones, demandas o riesgos).
   - Persiste `data/history.csv`, `data/sent.json`, `docs/latest.json` y `docs/history.json` todos los días (salvo en `--dry-run`, que no escribe archivos).

2. **📡 Radar semanal de tecnología (`python -m brief.weekly`)** — cada **domingo a las 6:00 p.m. (America/Bogota)** construye y envía un digest curado:
   - **Fuentes verificadas (últimos 7 días, tope `max_per_source: 15`):** arXiv API (`cs.LG`, `cs.AI`, `cs.CL`, `stat.ML`, `math.OC`, `cs.CY`), Hugging Face Daily Papers, Semantic Scholar (con backoff ante HTTP 429, soporte opcional de `S2_API_KEY` y reporte explícito cuando queda bloqueado), Hacker News (API oficial) y RSS técnicos (MIT Technology Review, Ars Technica, Hugging Face Blog, Simon Willison, Google Research Blog, Quanta Magazine).
   - **Curaduría por afinidad y diversidad (hasta 7 piezas):**
     - Puntúa cada candidato contra tus 6 intereses ponderados (`ml_applied`, `small_models_edge` con peso reforzado en términos núcleo como `llama.cpp`, `gguf`, `quantization`, `slm`, `on-device` y `offline`; `edtech`, `prob_optimization`, `software_engineering`, `open_data_latam`), descarta dominios ajenos (`off_topic_keywords`) y exige afinidad mínima (`min_affinity_score: 2.0`). Solo la *Joya de la semana* puede tener afinidad `< 2.0` (marcada como `"fuera de tu radar habitual"`).
     - Selecciona **3 papers** (*accesible*, *profundo*, *aplicado*), **2 noticias/lanzamientos**, **1 herramienta/repo** y **1 joya**, respetando un tope estricto de **máximo 2 piezas por fuente** y **máximo 2 piezas por tema**.
   - **Jerarquía de notificaciones en Telegram:**
     - **Mensaje 1 — Teaser de pantalla de bloqueo (máximo 4 líneas, ÚNICO con sonido):** gancho con el tema dominante calculado sobre las piezas seleccionadas + los 3 titulares principales (`[EN]`/`[ES]` con título original sin LLM, o traducidos al español con LLM).
     - **Mensajes 2 y 3 — Cuerpo del digest (`disable_notification=true`):** conservan siempre el **título original intacto** con enlace, etiqueta de tema en su propia línea (`🏷️`), línea `🎯` solo si hay una coincidencia concreta con un interés o proyecto (sin frases de relleno), extracto limpio de **máximo 25 palabras** en oración completa con marca `[EN]`/`[ES]` (sin título repetido, sin prefijos `"Release:"`, sin `".."`, uniendo palabras cortadas por guion como `state-of-the-art` y omitiendo extractos vacíos), sin tiempo de lectura en papers ni snippets cortos, y conexión `🛠️` con **Aula Ya**, **Table Master** o **PostMark** solo cuando coinciden **al menos 2 términos clave distintos** del proyecto (o cuando el LLM propone una conexión concreta).
   - **Secciones formativas y quiz:**
     - *Concepto en 60 segundos:* explica un solo término técnico presente en las lecturas de la semana con un ejemplo cotidiano.
     - *Reto de la semana (20 min):* propone probar la herramienta/repo solo si es compatible con tu stack (`Python, pandas, scikit-learn, TensorFlow, Django, Postgres, Next.js, TypeScript`); de lo contrario, propone leer abstract y conclusión de uno de los papers y anotar 3 ideas aplicables.
     - *Mini quiz (`sendPoll` con `disable_notification=true`):* posición de la respuesta correcta aleatorizada (`correct_option_id`) y explicación `<= 200` caracteres.
   - **Aprendizaje con botones inline y archivo público:**
     - Cada mensaje incluye botones (`📌 Guardar`, `✅ Ya la leí`, `🔼 Más como esto`, `🔽 Menos como esto`). Tanto `daily.yml` como `weekly.yml` procesan `getUpdates` (`callback_query`) con `last_update_id`, toleran callbacks expirados, actualizan el botón en el mensaje original (`editMessageReplyMarkup` → `"✅ Guardado"`), ajustan los pesos en `data/feedback.json` y guardan tu biblioteca en `docs/library.json` y `biblioteca/AAAA.md` ordenada por tema.
     - En envíos reales (nunca en `--dry-run`), archiva la edición en `digests/AAAA/AAAA-Wnn.md`, actualiza `digests/README.md` y `docs/digests.json` (buscador en `docs/digests.html`), y registra la semana en `data/sent.json`.

---

## Arquitectura y Workflows

```
GitHub Actions (concurrency: group "brief-state", cancel-in-progress: false)
 ├─ daily.yml   (7:40 a.m. + respaldo 8:10 a.m. Bogotá) → python -m brief.main
 │   ├─ git pull --rebase + chequeo en data/sent.json / data/history.csv
 │   ├─ feedback.sync_telegram_feedback()   # procesa clics inline y actualiza biblioteca/feedback
 │   ├─ sources/* + consensus               # mediana, dispersión y % de confianza por variable
 │   ├─ relevance.evaluate_relevance()      # 2 líneas silenciosas vs. 4-6 líneas con sonido
 │   └─ commit + push con 3 reintentos      # data/sent.json data/feedback.json data/history.csv docs/ digests/ biblioteca/
 │
 └─ weekly.yml  (Domingo 6:00 p.m. + respaldo 6:30 p.m. Bogotá, o workflow_dispatch con input dry_run)
     ├─ git pull --rebase + chequeo en data/sent.json (o ejecución limpia sin escritura si dry_run=true)
     ├─ feedback.sync_telegram_feedback()   # actualiza data/feedback.json y biblioteca/AAAA.md
     ├─ sources/tech_sources.py             # arXiv, HF Papers, Semantic Scholar (backoff + S2_API_KEY), HN, RSS
     ├─ weekly.build_weekly_digest()        # afinidad >=2.0, máx 2/fuente y 2/tema, 1 llamada JSON Claude o extractivo <=25 palabras
     ├─ telegram (Teaser con sonido + cuerpo y sendPoll silenciosos + teclados inline)
     └─ commit + push con 3 reintentos      # data/sent.json data/feedback.json data/history.csv docs/ digests/ biblioteca/
```

Si cualquier paso falla en `daily.yml` o `weekly.yml`, el workflow envía una alerta por Telegram indicando el nombre exacto del paso fallido.

---

## Uso local y flags CLI

```bash
pip install -r requirements.txt

# Aviso diario inteligente
python -m brief.main --dry-run                             # imprime el mensaje diario sin enviar ni modificar archivos
python -m brief.main --dry-run --as-of 08:00               # simula las 08:00 (hora Bogotá) para evaluar franjas
python -m brief.main --dry-run --calm-fixture              # prueba determinista de un día tranquilo (2 líneas silenciosas)
python -m brief.main --no-wait --force                     # envía de inmediato ignorando la espera a las 08:00 y el control de duplicados

# Radar semanal de tecnología
python -m brief.weekly --dry-run                           # genera e imprime el digest semanal sin enviar ni escribir archivos
python -m brief.weekly --dry-run --week 2026-W48           # previsualiza una semana ISO específica en modo dry-run
python -m brief.main --dry-run --week 2026-W48             # atajo equivalente desde brief.main
python -m brief.weekly --force                             # envía y archiva el digest semanal actual aunque ya figure en data/sent.json

# Calidad y tests (100 % offline)
python -m ruff check .
python -m pytest -q
```

---

## Cómo personalizar intereses, proyectos y fuentes

Todo se configura en [config.yaml](config.yaml):

- **Reglas del diario inteligente (`relevance:`):** ajusta `outing_periods` (`["mañana", "tarde"]`), `rain_outing_pct` (`70.0`), `feels_cold_extreme_c` (`9.0`), `feels_hot_extreme_c` (`23.0`), `uv_extreme` (`8.0`), `aqi_harmful` (`101.0`), `trm_change_pct` (`1.5`) y `trm_extreme_cooldown_days` (`5`).
- **Intereses ponderados y filtros (`weekly_digest:`):** configura `min_affinity_score` (`2.0`), `off_topic_keywords` y los 6 temas en `weekly_digest.interests` (`ml_applied`, `small_models_edge`, `edtech`, `prob_optimization`, `software_engineering`, `open_data_latam`) con su etiqueta (`label`), peso inicial (`weight`), palabras clave (`keywords`) y términos núcleo (`core_keywords`). Los botones `🔼 Más como esto` / `🔽 Menos como esto` en Telegram ajustan automáticamente los multiplicadores en `data/feedback.json`.
- **Stack y proyectos propios (`weekly_digest.user_stack` y `weekly_digest.projects`):** define tus tecnologías habituales y los términos clave de **Aula Ya**, **Table Master** y **PostMark**.
- **Fuentes del Radar semanal (`weekly_digest.sources:`):** activa o desactiva `arxiv`, `huggingface_papers`, `semantic_scholar`, `hacker_news` o agrega feeds en `rss_feeds` con `{name, url, topic, language, enabled}`.

---

## Secretos (Settings → Secrets and variables → Actions)

| Secreto | Obligatorio | Uso |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Sí | Envío de mensajes, encuestas (`sendPoll`), edición de botones (`editMessageReplyMarkup`) y lectura de callbacks (`getUpdates`) |
| `TELEGRAM_CHAT_ID` | Sí | Chat destino en Telegram |
| `ANTHROPIC_API_KEY` | No | Opcional: una sola llamada JSON validada por digest con Claude (`claude-haiku-5-5` / `claude-sonnet-5-5`) para titulares en español, síntesis de 25 palabras y conexiones con tus proyectos; sin clave usa el modo extractivo propio |
| `S2_API_KEY` | No | Opcional: API key de Semantic Scholar (`x-api-key`) para evitar límites de tasa HTTP 429 |
| `WEATHERAPI_KEY` | No | Fuente adicional de clima |
| `OPENWEATHER_API_KEY` | No | Fuente adicional de clima |
| `WAQI_TOKEN` | No | Fuente adicional de calidad del aire |
