# Migración OpenClaw → scripts + Grok (CERRADA)

**Estado:** completada. Este documento sustituye al borrador del 2026-09-19.

## Qué se decidió

El borrador proponía mover los "crons inteligentes" de OpenClaw (agentTurns con MiniMax)
a tareas de un agente Grokbot. Finalmente se hizo algo más simple:

- **Cada paso es un script Python del repo** (`scripts/*_tick.py`), sin agente ni skills.
- Donde hace falta LLM, el script hace **una única llamada JSON a la API de xAI (Grok)**
  vía `app/services/grok_client.py` (`XAI_API_KEY`, `XAI_API_BASE`, `XAI_MODEL`):
  - 3a `brief_reader_tick.py` — extrae reglas del brief.
  - 12–13 `grok_clip_decider_tick.py` — elige ventanas de clip en transcripciones con voz.
- El resto es determinista: discovery (Whop API), 3b resolver (`gog` + HTTP),
  3c scorer (`app/services/campaign_score.py`), enqueue de descargas y de publicación.
- La cadencia la da **cron del host** (no OpenClaw, no Grokbot).

| Antes (OpenClaw) | Ahora |
|---|---|
| `whop-discovery-cron` | `whop_discovery.py` — 08:15 y 20:15 Madrid |
| `brief-reader-tick` (agentTurn MiniMax) | `brief_reader_tick.py` (Grok) — cada 15 min |
| `drive-resolver-tick` / `dropbox-resolver-tick` (agentTurn) | `drive_resolver_tick.py` (gog / HTTP) — cada 8 min |
| `campaign-scorer-tick` (agentTurn) | `campaign_scorer_tick.py` — cada 5 min |
| `download-enqueue-tick` | `download_enqueue_tick.py` — cada 10 min |
| `clip-decider-tick` (agentTurn MiniMax) | `grok_clip_decider_tick.py` (Grok) — 4×/hora, aprobación manual; silenciosos en el hook de transcripción |
| `campaign-publish-tick` (agentTurn) | publish gate (`POST /clips/{id}/approve_publish`) + `publish_enqueue_tick.py` (manual por ahora) |
| `campaign-prioritizer-tick`, `campaign-analyze-tick`, `vps-pipeline-tick` | retirados |

## Historial de hosting

1. **VPS** `100.109.27.21` (hasta 2026-09-30): código en `/opt/clipping-system` (venv),
   Postgres nativo, ticks en crontab de `ubuntu` (y `root` para el resolver con gog).
   Crontabs comentadas el 2026-09-30 (backup en `/home/ubuntu/crontab-backup-20260930/`). Datos intactos.
2. **Mini PC** `molinaserver` (desde 2026-09-30): Docker Compose en
   `/srv/datos/apps/clipping-system-vps`, BD nueva vacía, crontab de `jarvis`
   con `docker exec`. Ver `PIPELINE_STATUS.md`.

## Pendiente tras la migración

- Worker Windows: apuntar su URL base de la API a `http://100.70.150.107:8080`.
- Re-autorizar `gog` (el refresh token de Google había caducado): ver `PIPELINE_STATUS.md`.
- Cerrar el trial de publicación en YouTube antes de activar el cron de publish.
