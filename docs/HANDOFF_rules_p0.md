# Handoff: P0 de reglas (clipping) — 2026-10-03

Para el agente que continúe (Grok Build). Está escrito para leerse sin contexto previo.

## Reglas operativas (no negociables)

- **No activar publish.** La línea `publish_enqueue_tick` del crontab de jarvis está comentada y debe seguir así.
- **No pasar `--approve`** al decider (`grok_clip_decider_tick.py`).
- **No tocar manualmente #6 ni #18.** Las confirmaciones de reglas las hace Jesús desde el dashboard.
- **Worker Windows** (Parewebs0/clipping-windows-worker): no se despliega desde aquí. Se mergea en main y Jesús hace `git pull` y reinicia el worker en su PC. No entrar en su PC.
- **Tests:**
  - Siempre contra un Postgres desechable cuyo nombre termine en `_test`; conftest rechaza cualquier otra BD.
  - Usar `set -o pipefail`. Si haces `pytest | tail`, sin pipefail se tragan los fallos; ya pasó una vez (PR #46, corregido en #47).
- **Revisiones de alembic:** como mucho 32 caracteres y siempre aditivas.
- **Nunca imprimir tokens ni secretos.**

## Estado de producción (mini PC)

- **API:** `100.70.150.107:8080`; `/health` devuelve 200.
- **Repo en el mini PC:** `/srv/datos/apps/clipping-system-vps`.
- **Usuarios SSH:**
  - `ssh minipc-molina`: git pull, build, pg_dump y backups en `/srv/datos/backups`.
  - `ssh minipc`: usuario jarvis (grupo docker, crontab, logs en `/home/jarvis/clipping-cron/logs`).
- **Despliegue:** `ssh minipc-molina 'bash /tmp/deploy.sh <label>'`. El script hace, en orden:
  1. pg_dump
  2. tag de rollback de la imagen
  3. git pull
  4. `docker compose build api` + `up -d`
  5. `alembic upgrade head` + `alembic current`
  6. comprobación de health

  Si `/tmp` se ha limpiado, recrea el script con esos pasos.
- **Rollback:**
  1. `docker tag clipping-system-vps-api:rollback-pre-rules5-20261003T1146Z <imagen-actual>` y `docker compose up -d api`.
  2. Si hace falta, restaurar `/srv/datos/backups/clipping_pre_rules5_20261003T1146Z.sql`.

  Hay backups anteriores: `pre_containment`, `pre_rules1` … `pre_rules4`.
- **Último despliegue:** label `rules5`, commit main `a2e19e8`, alembic `0021_clip_compliance (head)`.
- **Campañas:**

  | Estado | Campañas | Motivo |
  |---|---|---|
  | `needs_review` | #6 | 17 requisitos de cuenta + logo sin fichero |
  | `needs_review` | #18 | pre-aprobación + logo sin fichero |
  | `parked` | #13 | contención: asset único = logo PNG; carpeta en frame.io, que no se soporta |
  | `parked` | 10 más | — |
  | `blocked_low_score` | #9, #15 | — |
  | `failed_resolve` | #8 | — |

  Ninguna campaña está trabajable ahora mismo. Jesús debe confirmar los requisitos en «Mission Control → campaña → Reglas y score».
- **Jobs:** 20 descargas canceladas (12 de #15 por la contención y 8 de #6 por el gate). No hay workers, candidatos ni clips.

## Hecho: Parewebs0/clipping-system-vps

| Issue | PR | Merge | Contenido |
|---|---|---|---|
| #31 | #32 | 37ca956 | El cron de descargas solo encola para campañas trabajables y cancela las pendientes de las no trabajables (`app/services/download_gate.py`) |
| #33 | #34 | 9f954e3 | RuleSet v2 tipado con citas literales y enforcement auto/human/unsupported (`app/services/rules/{schema,sources,structured,extract,enforcement,service}.py`, `scripts/rules_reader.py`) |
| #35 | #36 | 32aca28 | Fuente única: `app/services/rules/runtime.py`. Sin parser regex en `approve_candidate`; QA, decider y silent cutter leen el esquema |
| #37 | #38 | f3c1ac3 | Gate `needs_review` (migración 0020). `app/services/rules/gate.py`, endpoints `GET /campaigns/{id}/ruleset` y `POST /campaigns/{id}/rules/confirm` (write token), `frontend/src/components/campaign/RulesetPanel.tsx` |
| #39 | #40 | 84b255f | Las prohibiciones nunca son «no soportadas» (`extract.reclassify`) |
| #41 | #42 | bfbfcb4 | `render_spec` v2 en el payload del render (`app/services/rules/render_spec.py`) |
| #43 | #44 | 67dd516 | Verificador post-render (migración 0021: `clips.compliance_status` y `clips.compliance_report`); publish exige `pass`. Ficheros: `app/services/rules/verifier.py`, `POST /clips/{id}/verify`, UI en `ComplianceReport.tsx` |
| #45 | #46 + #47 | 29c530f / 6234060 | Copy de publicación desde el esquema (`publish_copy.platform_copy`) y `paid_promotion` en el payload |
| #48 | #49 | 3af8078 | «No soportada» solo para categorías imposibles; dispensa con nota obligatoria; `rules_reader --reapply` (sin LLM) |
| #50 | #51 | a2e19e8 | Tests golden con #6/#13/#18 (`tests/golden/`, fixtures en `tests/fixtures/rules/ruleset_*.json`) |

Suite: 527 passed, 21 skipped.

## Hecho: Parewebs0/clipping-windows-worker (sin desplegar)

| Issue | PR | Merge | Contenido |
|---|---|---|---|
| #4 | #5 | fb8da74 | Render v2: subtítulos ASS generados desde la transcripción con diccionario de marca (`app/tools/captions.py`), logo por URL con ventana temporal, texto en pantalla, `-map 0:a?`, `applied` y `probe` en el resultado |
| #6 | #7 | bfe2773 | YouTube `paidProductPlacementDetails.hasPaidProductPlacement` |

- Tests: 83 passed. Hay 3 fallos que ya existían en main y son ajenos a esto (issue #9).
- **Pendiente de Jesús:** `git pull` en main y reiniciar el worker. Mientras no lo haga, el verificador marcará `render.metadata = fail` y nada podrá publicarse; es el comportamiento seguro.

## Re-lectura de las 16 campañas

- **Coste LLM:** unos 0,40 $ en total.
  - dry-runs: 0,055 $
  - run1: 0,169 $
  - run2: 0,163 $ (en #18 dio timeout)
  - re-lectura de #18: 0,012 $
  - `--reapply`: 0 $
- **Calidad:** el 100 % de las citas están verificadas y no queda ninguna línea normativa sin cubrir.
- **Bloqueantes «no soportada» reales:** sonido obligatorio (#1, #4, #7, #11, #12), enlace de audio nuevo (#11) y host frame.io (#13).

## Cómo probar en local (box)

- **Backend:**
  1. `source /workspace/clipping/testenv.sh` (BD `clipping_test` en el contenedor `pgtest`)
  2. `alembic upgrade head`
  3. `set -o pipefail; python -m pytest -q`
- **OpenAPI:**
  1. `python scripts/export_openapi.py`
  2. En `frontend/`: `npm run gen:api && npm run check:api && npm run lint && npm test && npm run build`
- **Worker:** `uv venv` + dependencias de `requirements.txt` (salvo whisperx), luego `python -m pytest -q`. Necesita ffmpeg.
- **Flujo por issue:** issue → rama → tests en verde → PR «Closes #n» → `gh pr merge --merge --delete-branch` → desplegar con backup.

## Pendientes (issues abiertos con etiqueta `grok-build`)

Cada issue lleva ficheros, diseño, criterios de aceptación y riesgos.

1. **https://github.com/Parewebs0/clipping-system-vps/issues/52**: claves de confirmación estables entre re-lecturas y refresco o liberación automática del gate.
   - **Riesgo:** perder confirmaciones de Jesús.
   - **Test:** re-leer dos veces la fixture de #6.
2. **https://github.com/Parewebs0/clipping-system-vps/issues/53**: checklist humano por clip al aprobar publish.
   - **Riesgo:** bloquear el flujo de publish (que está apagado).
3. **https://github.com/Parewebs0/clipping-system-vps/issues/54**: copy y verificación para TikTok/Instagram.
4. **https://github.com/Parewebs0/clipping-system-vps/issues/55**: logo como fichero subido desde el dashboard y conversión SVG→PNG.
   - Hoy la URL del logo se pone en la nota al confirmar `human:logo_file`.
   - Los enlaces de Drive de tipo «view» se convierten a descarga directa.
5. **https://github.com/Parewebs0/clipping-system-vps/issues/56**: hook, CTA y end card automáticos.
6. **https://github.com/Parewebs0/clipping-system-vps/issues/57**: captura del dashboard (no llegó a generarse `evidence/50_rules_after_p0.png`) y test e2e del flujo confirmar/dispensar.
7. **https://github.com/Parewebs0/clipping-windows-worker/issues/8**: muestreo de frames real en el worker (hoy el VPS se fía de `applied`, que el propio worker declara).
8. **https://github.com/Parewebs0/clipping-windows-worker/issues/9**: arreglar los 3 tests rotos que ya existían.

## Riesgos conocidos

- **Clasificación LLM:** varía entre ejecuciones; lo mitigan `reclassify`, la lista de categorías imposibles y la dispensa.
- **Re-lecturas:** si cambia el texto de un requisito, hay que volver a confirmarlo (issue #52).
- **Pinned comment:** la API de YouTube no permite fijar comentarios, así que queda como check humano.
- **Términos prohibidos ambiguos:** algunos (por ejemplo «background music» en #13) se buscan también en los subtítulos.
