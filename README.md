# Justrite Stakeholder Questionnaire — full-stack online form

A multi-staff questionnaire for Justrite Pharmacy, with a self-contained form, a small dependency-free Python server, SQLite storage and a coordinator dashboard.

**Production URL:** `https://justrite.dependifyllc.com`  
**Coordinator dashboard:** `https://justrite.dependifyllc.com/admin`

## 2+ staff can answer the same section

Each staff member submits **their own response** to the same section. Nothing is overwritten, and the coordinator sees every response side by side.

- The coordinator shares a **team link** such as `https://justrite.dependifyllc.com/?section=STO&store=Bariga` (or uses **Copy team link** in the dashboard).
- Each person opens the link, enters their own name and role, answers, signs off and submits.
- The **Team view** in `/admin` shows the section's questions in rows and each respondent in a column, with a coverage count per question.
- Responses are saved immediately to SQLite on the server. Multiple responses per section are expected, not a conflict.

This is intentionally **separate responses**, not live co-editing of one form. The department working session resolves differences and merges the answers into the agreed SOPs.

## Files

| File | Purpose |
|---|---|
| `index.html` | The questionnaire. Self-contained; also works as a static download-only form when no server is present. |
| `server.py` | Dependency-free Python server: static files, submission API, SQLite, admin API, exports, backup. |
| `entrypoint.py` | Container startup: fixes `/data` ownership, drops to UID 10001, starts the server. |
| `admin.html` | Coordinator dashboard: token sign-in, stats, side-by-side team view, responses, exports. |
| `meta.json` | Generated question/section metadata used by the server and dashboard. |
| `Dockerfile` | Image for Coolify / Docker. |
| `docker-compose.yml` | Local or single-host Compose setup. |
| `.env.example` | Environment variables. |
| `DEPLOY-COOLIFY.md` | Step-by-step deployment guide. |

No third-party Python packages are required. Python 3.11+.

## Run locally

Linux/macOS:
```bash
cd output/justrite-questionnaire
ADMIN_TOKEN=dev-token-please-change DATA_DIR=./data python server.py
```

Windows PowerShell:
```powershell
cd output\justrite-questionnaire
$env:ADMIN_TOKEN="dev-token-please-change"
$env:DATA_DIR=".\data"
python server.py
```

Open:
- Form: `http://localhost:8000/`
- Team link example: `http://localhost:8000/?section=STO&store=Bariga`
- Coordinator dashboard: `http://localhost:8000/admin`

The admin token is required at startup; the server will not run without `ADMIN_TOKEN`.

## Coolify

The short version:

1. Push this folder to a Git repository.
2. Coolify → **New Resource → Application → Git repository**.
3. Build pack: **Dockerfile**; base directory `output/justrite-questionnaire` if using the whole repository.
4. Port: `8000`.
5. Environment: `ADMIN_TOKEN=<long random token>` and `DATA_DIR=/data`.
6. Persistent storage: mount a volume at **`/data`**.
7. Health check: `/api/health`.
8. Domain: `justrite.dependifyllc.com`; enable HTTPS. Deploy.

Full instructions, backups and troubleshooting: **[DEPLOY-COOLIFY.md](./DEPLOY-COOLIFY.md)**.

## How responses are saved

- `POST /api/responses` validates the response, checks the sign-off, computes priority gaps and stores it in `$DATA_DIR/questionnaire.db`.
- Resubmitting the same `responseId` updates the existing record and increments its version instead of creating a duplicate.
- Multiple staff are separate `responseId`s, so each person appears separately.
- Nothing is stored in the browser; the form submits to your server.

## Coordinator dashboard (`/admin`)

- Token sign-in (the token is kept only in that browser tab).
- Totals: responses, sections covered, priority questions covered by at least one person, last response.
- **Team view:** choose a section and compare every respondent's answers side by side, with per-question coverage.
- **Responses:** open a full response, delete it, or download its JSON.
- **Exports:** combined JSON, long-format CSV for Excel, and a SQLite database backup.
- Filter by store and search by name, role, store or section.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | **required** | Bearer token for `/api/admin/*` and the dashboard. Use 24+ random characters. |
| `DATA_DIR` | `./data` | Directory for `questionnaire.db`. In Coolify use `/data` with a persistent volume. |
| `PORT` | `8000` | HTTP port. |
| `MAX_BODY_BYTES` | `2000000` | Maximum submission size. |
| `RATE_LIMIT_PER_MIN` | `20` | Submission rate limit per client IP (best effort). Set `0` to disable. |

## Backups

- In the dashboard, **Backup DB** downloads a consistent SQLite snapshot.
- Also copy the persistent volume (`/data/questionnaire.db` plus WAL files) or use Coolify's volume backup.
- Download a backup before every redeploy until the volume is confirmed persistent.

## Security and privacy

- The admin API requires `Authorization: Bearer <ADMIN_TOKEN>`; tokens are compared in constant time.
- Security headers and CSP are set by the server; HTTPS is handled by Coolify.
- The server accepts only JSON, caps body size, validates sections/questions and requires confirmation.
- No external scripts, fonts or analytics. No cookies or localStorage in the form.
- Data is stored unencrypted in SQLite on the server volume; protect the host, the volume and every backup.
- Do not collect patient-identifying information; the form tells respondents this.
- Keep the admin token private and rotate it if it leaks.

## Static-only use

`index.html` alone still works: each worker downloads a `.json` response and sends it on WhatsApp/email; the browser-side **Coordinator tools** merges files. Without the server, responses are **not** saved centrally. Use this only as a fallback or preview.

## Contact details

The form uses the contact details printed in the proposal: WhatsApp **08060596697**, email **OTolulope@dependifyllc.com**. Confirm the email before distribution.
