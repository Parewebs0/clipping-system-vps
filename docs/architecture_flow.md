# Flujo de arquitectura — fuente única de verdad

> **Actualizado 2026-09-30.** Pipeline **solo scripts** (sin OpenClaw), orquestado por
> estados en PostgreSQL. Cada paso es un script idempotente que coge objetos en un estado,
> los procesa y los deja en el siguiente. La cadencia la da el crontab de `jarvis` en el
> mini PC (ver `PIPELINE_STATUS.md`). El backend encadena automáticamente los jobs del Worker.

## Componentes

```
MINI PC (Docker)                                   WINDOWS WORKER (PC de Molina)
├── cron (jarvis) ─ docker exec ─┐                 ├── polling GET /worker/jobs/next
├── api  (FastAPI :8080) ◄───────┼──── HTTP ──────►├── download
│   ├── scripts/*.py (ticks)  ◄──┘   Bearer token  ├── transcribe (WhisperX)
│   ├── hooks de jobs (on_*_completed)             ├── render (FFmpeg 9:16, subs, watermark)
│   └── Mission Control (/mission-control)         ├── qa (FFprobe)
└── postgres (16)                                   └── publish (YouTube API; dry-run por defecto)
Servicios externos: Whop API · xAI Grok · Google Docs público · Google Drive (gog CLI)
```

## Flujo completo

```
0/1  whop_discovery.py  (08:15 y 20:15 Madrid)
     Whop API → UPSERT campaigns (name, cpm, prize_pool, source_url, source_metadata.discovered)
     Solo inserta si hay < MAX_ACTIVE (3) campañas activas
     (activas = discovered | briefed | assets_resolved | scored)
     → status='discovered'
      ↓
3a   brief_reader_tick.py  (cada 15 min, 1 campaña)
     Lee source_metadata.discovered + descarga Google Docs públicos del brief
     1 llamada Grok JSON → rules (duración, formato, plataformas, captions, watermark,
     tags, música, FTC, content_source_urls…) + asset_links + score_preview
     → 'briefed'   (error → 'failed_brief')
      ↓
3b   drive_resolver_tick.py  (cada 8 min, 1 campaña)
     URLs candidatas = asset_links + rules.content_source_urls + reference_materials
     Backends: Drive folder (gog drive ls, profundidad ≤ 2, sigue shortcuts, máx 12 vídeos),
               Drive file, Dropbox file, URL directa de vídeo
     Crea 1 asset por vídeo real (status='pending')
     → 'assets_resolved'   (0 vídeos / host no soportado / error gog → 'failed_resolve';
                            los fallos de gog se reintentan en ticks posteriores)
      ↓
3c   campaign_scorer_tick.py  (cada 5 min, hasta 5 campañas) — determinista, sin LLM
     base = 15 + f(nº assets reales) + 4·min(cpm,10) + min(prize/10k,20) + 8 si verificado
     − penalizaciones (watermark 20, captions 10, texto en pantalla 10, tags 5, música 2,
       host no soportado 25, muchos assets 6/12, ficheros pesados 12/15)
     score ≥ 50 y ≥ 1 asset real → 'scored'; si no → 'blocked_no_assets'
      ↓
7    download_enqueue_tick.py  (cada 10 min, 1 job)
     campañas 'scored' → siguiente asset 'pending' sin job → job 'download'
     (salta carpetas, docs, perfiles, skip_download)
      ↓
8-9  WORKER download → POST /worker/jobs/{id}/result
     hook on_download_completed → asset 'downloaded' + job 'transcribe'
      ↓
10-11 WORKER transcribe (WhisperX) → hook on_transcribe_completed → asset 'transcribed'
     Si el audio no tiene voz (silent/gameplay): el backend crea candidatos por ventanas de
     duración (sin LLM) y auto-aprueba el primero si no hay render en curso → job 'render'
      ↓
12-13 grok_clip_decider_tick.py  (4×/hora, 1 asset)
     assets 'transcribed' con voz y sin candidatos → 1 llamada Grok → 1–2 ventanas
     (start/end dentro de duration_min/max de la campaña, title, caption)
     → candidates status='pending'
      ↓
14   APROBACIÓN HUMANA: POST /candidates/{id}/approve  (o Mission Control)
     → candidato 'approved' → job 'render'
     (el script admite --approve para auto-aprobar; hoy NO se usa en cron)
      ↓
15-17 WORKER render → hook on_render_completed → clip 'created' + job 'qa'
      ↓
18-19 WORKER qa → hook on_qa_completed → PASS: clip 'approved' · FAIL: 'rejected' · REVIEW: 'review'
      ↓
20   PUBLISH GATE HUMANO: POST /clips/{id}/approve_publish
     requiere clip 'approved' + qa 'pass' → clip_publications 'pending' (por social_account)
      ↓
21   publish_enqueue_tick.py --limit 1 [--live] [--platform youtube]
     clip_publications 'pending' → job 'publish' (payload dry_run=true salvo --live)
     WORKER publica → hook on_publish_completed → mark_uploaded / published_at
     (sin cron hasta cerrar el trial)
```

## Estados

**Campaña:** `discovered` → `briefed` → `assets_resolved` → `scored` (→ trabajo) ·
terminales/errores: `failed_brief`, `failed_resolve`, `blocked_no_assets`. El estado legacy `ready` ya no existe (migración 0012) ni se acepta en el paso 7.

**Asset:** `pending` → `downloaded` → `transcribed` · `failed`.

**Job (`jobs.job_type`):** `download`, `transcribe`, `render`, `qa`, `publish` —
estados `pending` → `assigned` → `processing` → `completed` / `failed` / `cancelled`.

**Candidato:** `pending` → `approved` → `rendered` · `rejected` / `superseded`.

**Clip:** `created` → `approved` / `rejected` / `review` → `published`; ubicación (`location`) y `publish_approved_at` para el gate.

## API usada por el Worker

`POST /worker/register`, `POST /worker/heartbeat`, `GET /worker/jobs/next`,
`POST /worker/jobs/{id}/start|result|fail|heartbeat`, `POST /clips/{id}/mark_uploaded`,
`POST /clips/{id}/location/{location}`. Autenticación: `Authorization: Bearer <API_TOKEN>`.

## Reglas

1. Cada script es idempotente y procesa pocos objetos por tick (`--limit`), para ir "de campaña en campaña".
2. Todo cambio de flujo o de cron se documenta aquí y en `PIPELINE_STATUS.md` en el mismo commit.
3. Worker = código de Molina (repo aparte). Backend + scripts = este repo.
