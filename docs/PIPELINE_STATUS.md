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
| 0/1 | Descubrir campañas Whop | `scripts/whop_discovery.py --max-active 3 --no-fetch-detail` | Whop API → `campaigns.status='discovered'` (solo si hay < 3 activas) | 08:15 y 20:15 Europe/Madrid |
| 3a | Brief reader (Grok) | `scripts/brief_reader_tick.py --limit 1` | `discovered` → `briefed` / `failed_brief` (rules, asset_links, score_preview) | cada 15 min |
| 3b | Asset resolver (Drive vía `gog`, Dropbox file, URL directa) | `scripts/drive_resolver_tick.py --limit 25` (ver gap abajo) | `briefed` (+ reintento `failed_resolve` si fallo de gog) → `assets_resolved` / `failed_resolve`; crea assets | cada 8 min |
| 3c | Scorer determinista | `scripts/campaign_scorer_tick.py --limit 5` | `assets_resolved` → `scored` / `blocked_no_assets` (score < 50, 0 assets reales o host no soportado) | cada 5 min |
| 7 | Encolar descargas | `scripts/download_enqueue_tick.py --limit 1` | campañas `scored`/`ready` + assets `pending` sin job → job `download` | cada 10 min |
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
| `whop_discovery.py` | `WHOP_TENANT_URL`, `WHOP_API_BASE`, `WHOP_API_TIMEOUT_S`; opcional `DISCOVERY_MAX_ACTIVE` |
| `brief_reader_tick.py`, `grok_clip_decider_tick.py` | `XAI_API_KEY`, `XAI_API_BASE`, `XAI_MODEL`; salida a `docs.google.com` (briefs públicos) |
| `drive_resolver_tick.py` | binario `gog` (v0.40.0, en la imagen `/usr/local/bin/gog`), `GOG_KEYRING_PASSWORD`, config/keyring de gog montados en `/root/.config/gogcli` y `/root/.local/share/gogcli` (host: `./gog/config`, `./gog/share`, fuera de git) |
| `campaign_scorer_tick.py`, `download_enqueue_tick.py` | solo BD |
| `publish_enqueue_tick.py` | `PUBLISH_DRY_RUN` (default `1`); el Worker hace la subida real |
| Todos | `CLIPPING_DB_*` (desde `.env`, montado read-only) |

## Crontab instalada (usuario `jarvis`, host en UTC)

`cron` de Ubuntu no soporta `CRON_TZ`, así que discovery se lanza a las :15 de 06/07/18/19 UTC
con un guard `TZ=Europe/Madrid date +%H ∈ {08,20}` → siempre 08:15 y 20:15 hora de Madrid (a prueba de cambio horario).

```
C=/home/jarvis/clipping-cron
X=docker exec clipping-system-vps-api-1 python
15 6,7,18,19 * * * H=$(TZ=Europe/Madrid date +\%H); { [ "$H" = 08 ] || [ "$H" = 20 ]; } && flock -n $C/locks/discovery.lock timeout 20m $X scripts/whop_discovery.py --max-active 3 --no-fetch-detail >> $C/logs/discovery.log 2>&1
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
| `vps_pipeline_tick.py`, `clip_scanner.py`, `clip_scanner_quiet.sh`, `campaign_prioritizer.py`, `clip_decider_tick.py.disabled` | legacy (pipeline v1 / MiniMax) |
| `whop_playwright_probe.py` | sonda de investigación |

## Operación rápida

- Ejecutar un paso a mano: `docker exec clipping-system-vps-api-1 python scripts/<script>.py --dry-run`.
- Re-leer un brief: `... brief_reader_tick.py --campaign-id N`; re-resolver: `... drive_resolver_tick.py --campaign-id N`.
- Re-autorizar Google Drive (si 3b falla con `invalid_grant`): `docker exec -it clipping-system-vps-api-1 gog auth add <email> --services drive,docs --manual`.
- Migraciones: `docker exec clipping-system-vps-api-1 alembic upgrade head`.

## Gaps conocidos

- No hay gate duro en el backend que valide candidatos contra las reglas de campaña (solo el prompt del decider).
- `drive_resolver_tick.py` aplica `--limit` en SQL **antes** de saltar campañas `failed_resolve` no reintentables: con `--limit 1` una campaña atascada de id bajo bloquea a todas las demás (pasaba en el VPS con la campaña 9). Mitigado en cron con `--limit 25`; arreglo correcto: filtrar las atascadas en la query.
- Carpetas de Dropbox no se listan (`dropbox_folder_needs_list` → `failed_resolve`).
- Publicación real (`--live`) y cron de publish pendientes de cerrar el trial de YouTube.

**Regla de mantenimiento:** cualquier cambio de script o de cron del pipeline se refleja aquí en el mismo commit.
