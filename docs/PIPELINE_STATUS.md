# Pipeline Status — fuente de verdad del estado del pipeline

> **Última actualización:** 2026-09-30 (migración VPS → mini PC).
> **Realidad actual:** el pipeline es **100 % scripts Python** del repo, lanzados por
> **cron del host** (usuario `jarvis`) con `docker exec` dentro del contenedor `api`.
> **OpenClaw ya no interviene** (ni skills, ni agentTurns, ni `openclaw cron`).
> El flujo detallado está en `architecture_flow.md`.

## Dónde corre

| Pieza | Dónde | Notas |
|---|---|---|
| API FastAPI + Mission Control | mini PC `molinaserver` (Tailscale `100.70.150.107`), contenedor `clipping-system-vps-api-1`, puerto `8080` | `docker compose` en `/srv/datos/apps/clipping-system-vps` (rama `feat/publish-youtube`) |
| PostgreSQL 16 | contenedor `clipping-system-vps-postgres-1`, datos en `./pgdata` | Esquema por Alembic (`alembic upgrade head`, hoy `0014_publish_gate`). Arrancó **vacía** el 2026-09-30 (no se migraron datos del VPS) |
| Ticks del pipeline | crontab de `jarvis` en el mini PC | `flock -n` + `timeout`, logs en `/home/jarvis/clipping-cron/logs/` |
| Worker Windows | PC de Molina | download / WhisperX / FFmpeg render / FFprobe QA / publish. Hace polling a `GET /worker/jobs/next` |
| VPS antiguo (`100.109.27.21`) | retirado | crontabs de `ubuntu` y `root` comentados (backup en `~/crontab-backup-*`), datos intactos |

## TL;DR — pasos y quién los ejecuta

| # | Paso | Script / componente | Estado que consume → produce | Cron (mini PC) |
|---|---|---|---|---|
| 0/1 | Descubrir campañas Whop | `scripts/whop_discovery.py --max-active 3` | catálogo completo paginado → filtros duros `DISCOVERY_*` → ranking por valor esperado → detalle de la lista corta (clipping + active) → `campaigns.status='discovered'` (solo si hay < 3 activas). Fallo de red ⇒ exit 2 (#21) | 08:15 y 20:15 Europe/Madrid |
| 0b | Aparcar cerradas / agotadas / no aptas | `scripts/campaign_closed_tick.py` | toda campaña Whop no archivada ni aparcada → `parked` si en origen está pausada, oculta, 404, ≥ 95 % gastado, < 250 $ restantes, no es clipping, no acepta YouTube o pide solicitud (#23). Refresca `economics`/`detail` | cada 2 h |
| 3a | Brief reader (RuleSet v2, #33) | `scripts/brief_reader_tick.py --limit 1` (re-lectura sin cambiar estado: `scripts/rules_reader.py --all`) | `discovered` → `briefed` / `failed_brief`. Guarda `source_metadata.ruleset` (tipado, con citas y enforcement auto/human/unsupported, cobertura), `rules` legacy derivado, `rules_blockers` | cada 15 min |
| 3b | Asset resolver (Drive vía `gog` por prioridad de carpeta, Dropbox file, URL directa, vídeos subidos a Whop vía bucket público + HEAD; ignora `._*`/diminutos, solo vídeos cuentan hacia 12) | `scripts/drive_resolver_tick.py --limit 25` (ver gap abajo) | `briefed` (+ reintento `failed_resolve` si fallo de gog o si lo hizo un resolver anterior a `RESOLVER_VERSION`) → `assets_resolved` / `failed_resolve`; crea assets | cada 8 min |
| 3c | Scorer determinista | `scripts/campaign_scorer_tick.py --limit 5` | `assets_resolved` → `scored` / `blocked_no_assets` (0 assets reales) / `blocked_low_score` (tarifa YouTube < `SCORE_MIN_RATE_USD`=0,5, score < 50 o host no soportado; motivo en `source_metadata.score.block_reason`). Score = tarifa YouTube + presupuesto restante + assets + verificado, sin penalizar nº de assets ni texto de publicación (#25) | cada 5 min |
| 3d | Gate de reglas (#37) | scorer + `download_enqueue_tick` (`app/services/rules/gate.py`) | `scored` con regla no soportada o requisito humano sin confirmar (cuenta, pre-aprobación, logo sin fichero) → `needs_review` (motivo en historial, descargas pendientes canceladas, approve bloqueado). Jesús confirma en Mission Control → «Reglas y score» (`POST /campaigns/{id}/rules/confirm`, write token) y vuelve a `scored`. Las no soportadas no se confirman: aparcar/archivar. Si la re-lectura cambia el texto de un requisito, su clave cambia y hay que reconfirmar | con cada tick |
| 7 | Encolar descargas | `scripts/download_enqueue_tick.py --limit 1` | solo campañas `scored` sin bloqueantes + assets `pending` sin job → job `download`; cancela `download` pendientes de campañas no trabajables (#31) | cada 10 min |
| 8–9 | Descarga | Worker Windows + hook `on_download_completed` | job `download` → asset `downloaded` + job `transcribe` | — (automático) |
| 10–11 | Transcripción | Worker (WhisperX) + hook `on_transcribe_completed` | asset `transcribed`; si es **silencioso** el backend crea candidatos por duración y auto-aprueba el primero si no hay render en curso | — (automático) |
| 12–13 | Clip decider (voz, Grok) | `scripts/grok_clip_decider_tick.py --limit 1` | assets `transcribed` con voz y sin candidatos → 1–2 candidatos `pending` (con title/caption) | 4×/hora (min 7,22,37,52), **sin `--approve`** |
| 14 | Aprobación de candidato | humano: `POST /candidates/{id}/approve` (o Mission Control) | candidato `approved` → job `render` | manual |
| 15–19 | Render + QA | Worker (FFmpeg / FFprobe) + hooks | job `render` → clip `created` + job `qa` → clip `approved` / `rejected` / `review` | — (automático) |
| 20 | Publish gate | humano: `POST /clips/{id}/approve_publish` | clip `approved` + QA `pass` → `clip_publications.status='pending'` | manual |
| 21 | Encolar publicación | `scripts/publish_enqueue_tick.py --limit 1 [--live]` | `clip_publications` pending → job `publish` (dry-run por defecto) → Worker | **deshabilitado** (línea comentada) hasta cerrar el trial |

## Dependencias externas (solo nombres)

| Script | Necesita |
|---|---|
| `whop_discovery.py` | `WHOP_TENANT_URL`, `WHOP_API_TIMEOUT_S`; opcionales `DISCOVERY_*` (ver `.env.example`) |
| `campaign_closed_tick.py` | `WHOP_TENANT_URL`; opcionales `CLOSED_MAX_SPENT_PCT` (95), `CLOSED_MIN_REMAINING_USD` (250), `DISCOVERY_PLATFORM`, `DISCOVERY_CONTENT_TYPE` |
| `brief_reader_tick.py`, `grok_clip_decider_tick.py` | `XAI_API_KEY`, `XAI_API_BASE`, `XAI_MODEL`; salida a `docs.google.com` (briefs públicos) |
| `drive_resolver_tick.py` | binario `gog` (v0.40.0, descargado en el `docker build` desde la release oficial `openclaw/gogcli` con checksum sha256; `/usr/local/bin/gog`; versión vía build ARG `GOG_VERSION`), `GOG_KEYRING_PASSWORD`, config/keyring de gog montados en `/root/.config/gogcli` y `/root/.local/share/gogcli` (host: `./gog/config`, `./gog/share`, fuera de git) |
| `campaign_scorer_tick.py`, `download_enqueue_tick.py` | solo BD |
| `publish_enqueue_tick.py` | `PUBLISH_DRY_RUN` (default `1`); el Worker hace la subida real |
| Todos | `CLIPPING_DB_*` (desde `.env`, montado read-only) |

## Crontab instalada (usuario `jarvis`, host en UTC)

`cron` de Ubuntu no soporta `CRON_TZ`, así que discovery se lanza a las :15 de 06/07/18/19 UTC
con un guard `TZ=Europe/Madrid date +%H ∈ {08,20}` → siempre 08:15 y 20:15 hora de Madrid (a prueba de cambio horario).

```
C=/home/jarvis/clipping-cron
X=docker exec clipping-system-vps-api-1 python
15 6,7,18,19 * * * H=$(TZ=Europe/Madrid date +\%H); { [ "$H" = 08 ] || [ "$H" = 20 ]; } && flock -n $C/locks/discovery.lock timeout 20m $X scripts/whop_discovery.py --max-active 3 >> $C/logs/discovery.log 2>&1
0 */2 * * * flock -n $C/locks/closed.lock  timeout 15m $X scripts/campaign_closed_tick.py             >> $C/logs/closed.log 2>&1
*/15 * * * * flock -n $C/locks/brief.lock   timeout 12m $X scripts/brief_reader_tick.py --limit 1        >> $C/logs/brief_reader.log 2>&1
*/8  * * * * flock -n $C/locks/resolve.lock timeout 20m $X scripts/drive_resolver_tick.py --limit 25     >> $C/logs/drive_resolver.log 2>&1
*/5  * * * * flock -n $C/locks/score.lock   timeout 4m  $X scripts/campaign_scorer_tick.py --limit 5    >> $C/logs/scorer.log 2>&1
*/10 * * * * flock -n $C/locks/enqueue.lock timeout 9m  $X scripts/download_enqueue_tick.py --limit 1   >> $C/logs/download_enqueue.log 2>&1
7,22,37,52 * * * * flock -n $C/locks/decider.lock timeout 12m $X scripts/grok_clip_decider_tick.py --limit 1 >> $C/logs/clip_decider.log 2>&1
#0 */4 * * * flock -n $C/locks/publish.lock timeout 10m $X scripts/publish_enqueue_tick.py --limit 1  >> $C/logs/publish_enqueue.log 2>&1
0 4 * * 0 find $C/logs -name '*.log' -size +50M -exec truncate -s 0 {} \;
```

Los scripts además escriben sus propios logs en `/opt/clipping-system/logs/` (montado en `./logs` del proyecto).

## Scripts que NO van en cron

| Script | Motivo |
|---|---|
| `requeue_download_missing.py`, `requeue_transcribe_stalled.py` | reparaciones puntuales (el primero está fijado a `campaign_id=6`) |
| `classify_and_cut_silent.py` | puntual; el corte silencioso ya lo hace el hook de transcripción |
| `clip_scanner.py`, `clip_scanner_quiet.sh`, `clip_decider_tick.py.disabled` | legacy (pipeline v1 / MiniMax) |
| `whop_playwright_probe.py` | sonda de investigación |

## Operación rápida

- Ejecutar un paso a mano: `docker exec clipping-system-vps-api-1 python scripts/<script>.py --dry-run`.
- Re-leer un brief: `... brief_reader_tick.py --campaign-id N`; re-resolver: `... drive_resolver_tick.py --campaign-id N`.
- Re-autorizar Google Drive (si 3b falla con `invalid_grant`): `docker exec -it clipping-system-vps-api-1 gog auth add <email> --services drive,docs --manual`.
- Migraciones: `docker exec clipping-system-vps-api-1 alembic upgrade head`.
- Exposición de la API: compose publica `${API_BIND_ADDR:-0.0.0.0}:8080`. En el mini PC poner `API_BIND_ADDR=100.70.150.107` (IP Tailscale) en `.env` para no exponerla en la LAN; nunca `127.0.0.1` (el worker Windows entra por Tailscale). Aplicar con `docker compose up -d api`. Ojo: si Docker arranca antes que Tailscale tras un reboot, el bind a la IP Tailscale falla hasta que `tailscaled` esté arriba (revisar `docker compose ps` tras reiniciar).

Retirados el 2026-10-01 (pipeline v1, sin uso en cron ni en código vivo): `scripts/vps_pipeline_tick.py`, `scripts/campaign_prioritizer.py`, `app/services/campaign_analyzer.py` y los endpoints `POST /campaigns/analyze_due`, `POST /campaigns/{id}/analyze`, `POST /discovery/score_due`; `POST /discovery/run` ya no acepta `analyze_after`.

## Gaps conocidos

- No hay gate duro en el backend que valide candidatos contra las reglas de campaña (solo el prompt del decider).
- `drive_resolver_tick.py` aplica `--limit` en SQL **antes** de saltar campañas `failed_resolve` no reintentables: con `--limit 1` una campaña atascada de id bajo bloquea a todas las demás (pasaba en el VPS con la campaña 9). Mitigado en cron con `--limit 25`; arreglo correcto: filtrar las atascadas en la query.
- Carpetas de Dropbox no se listan (`dropbox_folder_needs_list` → `failed_resolve`).
- Publicación real (`--live`) y cron de publish pendientes de cerrar el trial de YouTube.

**Regla de mantenimiento:** cualquier cambio de script o de cron del pipeline se refleja aquí en el mismo commit.
