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

## Saved sessions, autosave and resume

Every person who presses **Start** is registered in the server database before they answer:

- A row is written to the `sessions` table with their name, role, store and chosen section(s).
- The server sets a first-party `HttpOnly`, `SameSite=Lax` session cookie (also `Secure` on HTTPS) that lasts 30 days.
- Draft answers autosave about a second after typing stops. The top bar shows **Saved 14:32** (a green dot on small phones).
- **Save & resume** is always in the top bar. On phones, a floating **Save & resume later** pill sits above the question navigation. It flushes the draft immediately and shows a confirmation with the link to reopen on the same device.
- Reopening the form on the same browser shows **Welcome back** with the saved name, sections, progress and last-saved time. **Resume my session** restores every answer, or **Start a new person** clears the device for the next staff member.
- A submitted session shows **Already submitted** on reload, with a receipt. **Start a new response** registers the next person.
- Drafts are also downloadable as JSON for use on another device or as a fallback if cookies are blocked.

The session cookie contains only a random token; answers are never stored in `localStorage` or in readable cookies.

## Files

| File | Purpose |
|---|---|
| `index.html` | The questionnaire. Self-contained; also works as a static download-only form when no server is present. |
| `server.py` | Dependency-free Python server: static files, registration/session API, submission API, SQLite, admin API, exports, backup. |
| `entrypoint.py` | Container startup: fixes `/data` ownership, drops to UID 10001, starts the server. |
| `admin.html` | Coordinator dashboard: stats, side-by-side team view, registered people and live drafts, responses, exports. |
| `admin-login.html` | Branded password page served at `/admin` until a valid admin session exists. |
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

The admin token is required at startup; the server will not run without `ADMIN_TOKEN`. The `/admin` dashboard itself is protected by a server-enforced password page. By default the password is `ADMIN_TOKEN`; set `ADMIN_PASSWORD` to use a separate, easier-to-type password. A successful login sets a 12-hour `HttpOnly` admin cookie.

## Coolify

The short version:

1. Push this folder to a Git repository.
2. Coolify → **New Resource → Application → Git repository**.
3. Build pack: **Dockerfile**; base directory `output/justrite-questionnaire` if using the whole repository.
4. Port: `8000`.
5. Environment: `ADMIN_TOKEN=<long random token>`, optional `ADMIN_PASSWORD=<dashboard password>`, and `DATA_DIR=/data`.
6. Persistent storage: mount a volume at **`/data`**.
7. Health check: `/api/health`.
8. Domain: `justrite.dependifyllc.com`; enable HTTPS. Deploy.

Full instructions, backups and troubleshooting: **[DEPLOY-COOLIFY.md](./DEPLOY-COOLIFY.md)**.

## How responses are saved

- Pressing **Start** calls `POST /api/sessions`, which registers the person in `sessions` and sets the HttpOnly session cookie. Drafts update through `PUT /api/sessions/current`.
- `POST /api/responses` validates the response, checks the sign-off, computes priority gaps and stores it in `$DATA_DIR/questionnaire.db`.
- A successful submission marks the linked session as `submitted`; the session keeps the final answers, receipt and timestamps for the coordinator.
- Resubmitting the same `responseId` updates the existing record and increments its version instead of creating a duplicate.
- Multiple staff are separate `responseId`s, so each person appears separately.
- Answers live on the server, not in browser storage. Only a random session token is kept in the first-party HttpOnly cookie.

## Coordinator dashboard (`/admin`)

- `/admin` is server-gated: without a valid admin cookie it serves only the password page. The dashboard HTML is not exposed before login.
- The password defaults to `ADMIN_TOKEN`; set `ADMIN_PASSWORD` to use a separate password. Login attempts are rate-limited.
- A successful login sets a 12-hour `HttpOnly`, `SameSite=Lax` admin session cookie (`Secure` on HTTPS). The password is never stored in the page, URL or `localStorage`. Admin sessions are held in server memory, so a server restart or redeploy requires signing in again.
- **Sign out** clears the admin cookie. The dashboard APIs accept either that cookie or `Authorization: Bearer <ADMIN_TOKEN>` for scripts.
- Totals: responses, sections covered, priority questions covered by at least one person, last response.
- **People & sessions:** every registered person, including live drafts that have not been submitted. Open a person to see their status, progress and current answers before they send.
- **Team view:** choose a section and compare every respondent's answers side by side, with per-question coverage.
- **Responses:** open a full response, delete it, or download its JSON.
- **Exports:** combined JSON, long-format CSV for Excel, and a SQLite database backup.
- Filter by store and search by name, role, store or section.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | **required** | Bearer token for `/api/admin/*` and fallback dashboard password. Use 24+ random characters. |
| `ADMIN_PASSWORD` | `ADMIN_TOKEN` | Optional separate password for the `/admin` login page. Use 12+ characters. |
| `LOGIN_RATE_LIMIT_PER_MIN` | `10` | Maximum admin login attempts per client IP per minute. Set `0` to disable. |
| `DATA_DIR` | `./data` | Directory for `questionnaire.db`. In Coolify use `/data` with a persistent volume. |
| `PORT` | `8000` | HTTP port. |
| `MAX_BODY_BYTES` | `2000000` | Maximum submission size. |
| `RATE_LIMIT_PER_MIN` | `20` | Submission rate limit per client IP (best effort). Set `0` to disable. |

## Backups

- In the dashboard, **Backup DB** downloads a consistent SQLite snapshot.
- Also copy the persistent volume (`/data/questionnaire.db` plus WAL files) or use Coolify's volume backup.
- Download a backup before every redeploy until the volume is confirmed persistent.

## Security and privacy

- The admin API requires `Authorization: Bearer <ADMIN_TOKEN>` or a valid admin session cookie; values are compared in constant time.
- `/admin` is server-gated; the dashboard HTML is only served after the password is accepted. Admin login attempts are rate-limited.
- Security headers and CSP are set by the server; HTTPS is handled by Coolify.
- The server accepts only JSON, caps body size, validates sections/questions and requires confirmation.
- No external scripts, fonts or analytics. The form keeps only a first-party HttpOnly session cookie (random token); answers are not in cookies or localStorage. The admin password is never stored in the browser.
- Data is stored unencrypted in SQLite on the server volume; protect the host, the volume and every backup.
- Do not collect patient-identifying information; the form tells respondents this.
- Keep the admin token private and rotate it if it leaks.

## Static-only use

`index.html` alone still works: each worker downloads a `.json` response and sends it on WhatsApp/email; the browser-side **Coordinator tools** merges files. Without the server there is no registration, autosave or resume; **Save & resume** downloads a draft file instead. Use this only as a fallback or preview.

## Contact details

The form uses the contact details printed in the proposal: WhatsApp **08060596697**, email **OTolulope@dependifyllc.com**. Confirm the email before distribution.
