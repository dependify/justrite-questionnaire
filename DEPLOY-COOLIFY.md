# Deploy the Justrite questionnaire on Coolify

The app is a single Python service with SQLite storage. No external database or third-party Python packages are needed.

## 1. Push the code

Push this folder (`output/justrite-questionnaire`) to a Git repository that Coolify can access.

If you are pushing the whole research folder, the application base directory is:

```
output/justrite-questionnaire
```

## 2. Create the Coolify resource

1. **New Resource → Application → Git repository** (or a Dockerfile resource if you already have the files).
2. Select the repository and branch.
3. Build pack: **Dockerfile**.
4. Base directory: `output/justrite-questionnaire` (only if you pushed the whole repository).
5. Dockerfile location: `Dockerfile`.
6. Exposed port: **8000**.
7. Start command: **leave empty** (the image entrypoint must run; it fixes `/data` permissions and starts the server).
8. Health check:
   - Type: HTTP
   - Path: `/api/health`
   - Port: `8000`
   - Expected status: `200`
9. Domain: `https://justrite.dependifyllc.com` (Coolify → Domains). Enable HTTPS.

### DNS for justrite.dependifyllc.com

In the DNS zone for `dependifyllc.com`:

- Add an **A record**: host `justrite` → the public IPv4 of the Coolify server.
- Or add a **CNAME**: host `justrite` → the hostname Coolify gives you, if it provides one.

Wait for DNS to propagate, then let Coolify issue the certificate.

If you use Cloudflare in front of the domain, set SSL/TLS to **Full (strict)** and make sure Coolify has a valid certificate (Let's Encrypt or a Cloudflare Origin Certificate). If certificate issuance is blocked by the proxy, switch the record to DNS-only for the first issuance.

Only the custom domain is the production link. The artifact preview and the Coolify auto-generated hostname are not the production form.

## 3. Environment variables

Set these in Coolify → Environment Variables:

| Name | Value |
|---|---|
| `ADMIN_TOKEN` | A long random token. Generate with `openssl rand -hex 24`. |
| `ADMIN_PASSWORD` | Optional separate password for the `/admin` login page. If unset, `ADMIN_TOKEN` is the password. |
| `LOGIN_RATE_LIMIT_PER_MIN` | `10` (optional) — admin login attempts per IP per minute. |
| `DATA_DIR` | `/data` |
| `PORT` | `8000` |
| `MAX_BODY_BYTES` | `2000000` (optional) |
| `RATE_LIMIT_PER_MIN` | `20` (optional) |

The container will refuse to start without `ADMIN_TOKEN`. This is deliberate: there is no default admin credential.

Keep the token in a password manager. If you do not set `ADMIN_PASSWORD`, that token is also the password for the `/admin` login page. Set a separate `ADMIN_PASSWORD` if you want a shorter, easier-to-type dashboard password; the Bearer token remains available for API scripts.

## 4. Persistent storage (required)

Add a **Persistent Storage** volume:

- Mount path: `/data`
- Name: anything, e.g. `justrite-questionnaire-data`

The SQLite database is `/data/questionnaire.db`. It holds both final submissions and the registered people/autosaved drafts (`sessions` table). Without this volume, both are lost when the container is redeployed or recreated.

The container entrypoint fixes ownership of `/data` and then runs the server as UID/GID `10001`. If Coolify uses a **read-only bind mount**, change it to a writable volume; otherwise the entrypoint logs a warning and the database cannot be written.

Keep the production hostname stable after sharing the link. Resume works by a first-party cookie scoped to the current host; changing domains starts everyone on a fresh session.

A Docker/named volume is simplest and preserves the permissions created in the image.

## 5. Deploy

Deploy and check the logs. A healthy startup looks like:

```
database ready at /data/questionnaire.db
Justrite questionnaire listening on 0.0.0.0:8000 (data: /data/questionnaire.db)
```

Open:

- `https://justrite.dependifyllc.com/` — the questionnaire.
- `https://justrite.dependifyllc.com/?section=STO&store=Bariga` — a team link for one section.
- `https://justrite.dependifyllc.com/admin` — the coordinator dashboard.
- `https://justrite.dependifyllc.com/api/health` — `{"ok":true,"service":"justrite-questionnaire"}`.

## 6. Test the deployment

1. Open the team link.
2. Enter a test name, answer two questions, sign off and **Submit response**.
3. Open `/admin`, enter the admin password (`ADMIN_PASSWORD`, or `ADMIN_TOKEN` if unset).
4. Confirm the response appears, the **Team view** shows the section, and **JSON / CSV / Backup DB** download.
5. Submit a second response as another staff member and confirm both appear side by side.
6. Delete the test responses in the dashboard when done.

## 7. How 2+ staff answer one section

Send the team link to everyone who should contribute:

```
https://justrite.dependifyllc.com/?section=STO&store=Bariga
```

- The section and store are preselected.
- Each staff member uses their own name and submits their own response.
- The dashboard's **Team view** shows the section's questions and every respondent in a column, with a coverage count per question.
- In the department working session, use the side-by-side view to resolve differences and agree the final answers.

The **Copy team link** button in the dashboard creates the same link for the currently selected section (and the selected store filter, if any).

## 8. Backups

Download a consistent SQLite snapshot from the dashboard: **Backup DB**.

Automate it from a safe machine:

```bash
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://justrite.dependifyllc.com/api/admin/backup.db \
  -o questionnaire-$(date +%F).db
```

Store backups off the server. Also consider Coolify's volume backup feature. Test a restore by opening the downloaded file in SQLite or starting a staging copy with it as `/data/questionnaire.db`.

## 9. Updating

1. Push changes to the repository.
2. Redeploy in Coolify.
3. Confirm the `/data` volume is still attached.
4. Download a backup after the deploy.

The database schema is created automatically (`CREATE TABLE IF NOT EXISTS`); existing responses are preserved.

## 10. Troubleshooting

### “This site is not available at the moment” (Coolify 503)

That page comes from Coolify's proxy, not from this app. Work through these in order:

1. **Is the domain attached to this resource?**  
   Coolify → your application → **Domains**. `justrite.dependifyllc.com` must be on the questionnaire resource, with HTTPS enabled. A DNS record alone is not enough.

2. **Is the application running?**  
   Coolify → **Deployments** / **Logs**. If it is stopped, crashed, or restarting, open the latest deployment log.

3. **Search the log for the actual failure:**
   - `ADMIN_TOKEN is required` → add the environment variable and redeploy.
   - `meta.json could not be loaded` → the build context is wrong; set the base directory to `output/justrite-questionnaire`.
   - `Permission denied`, `unable to open database file`, `readonly database` → `/data` is not writable. Use a named volume at `/data`; the entrypoint fixes ownership but cannot fix a read-only mount.
   - `Address already in use` → the port is wrong; set it to `8000`.
   - `database ready` plus `listening on 0.0.0.0:8000` → the app is healthy; the problem is the domain/port/health-check mapping.

4. **Check the port and health check.**  
   - Exposed port: `8000`  
   - Environment: `PORT=8000` (do not leave it to chance; a mismatched injected port is a common 503)  
   - Start command: empty (do not override the entrypoint)  
   - Health check path: `/api/health` (aliases `/health` and `/healthz` also return 200)  
   - Health check port: `8000`  
   - Expected status: `200`

5. **Check DNS.**  
   `justrite` points to Coolify's server (`158.158.1.40`). If the DNS is wrong, Coolify never receives the request.

6. **Test inside the container** (Coolify terminal):
   ```bash
   python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health').read())"
   ```
   If that works but the domain does not, the issue is Coolify's domain/port/health-check mapping, not the app.

7. **Redeploy after fixing the setting.**  
   Environment/domain changes often require a redeploy before the proxy routes correctly.

### Other issues

**401 on the dashboard**
- Enter the dashboard password (`ADMIN_PASSWORD`, or `ADMIN_TOKEN` if unset). It is case-sensitive.
- API clients can still use `Authorization: Bearer <ADMIN_TOKEN>`.
- Change the variable in Coolify and redeploy.

**`unable to open database file` or data disappears**
- `/data` is not mounted as persistent storage, or the container user cannot write to it.
- Use a Docker volume at `/data`; for a bind mount, `chown -R 10001:10001` the host folder.

**`ADMIN_TOKEN is required` in the logs**
- Add the environment variable and redeploy.

**Downloads blocked**
- Use the hosted server, not the sandboxed artifact preview. The form detects the server and submits directly; downloads are only needed for copies.

**Accidental duplicate response**
- Resubmitting the same response ID updates the existing record and increments its version.
- Two staff are separate response IDs by design.
- Delete unwanted records in the dashboard.

## 11. Local Docker test (optional)

```bash
docker build -t justrite-questionnaire .
docker run --rm -p 8000:8000 \
  -e ADMIN_TOKEN=dev-token-change-me \
  -e ADMIN_PASSWORD=dev-password-change-me \
  -v justrite-data:/data \
  justrite-questionnaire
```

Or with Compose:

```bash
cp .env.example .env
# edit .env and set a strong ADMIN_TOKEN
docker compose up -d --build
```

## 12. Privacy and security checklist

- Use HTTPS (Coolify handles certificates).
- Use a long random `ADMIN_TOKEN`; set a separate `ADMIN_PASSWORD` for the dashboard and rotate either if it leaks.
- `/admin` is server-gated; the password is never stored in the page, URL or `localStorage`. Admin sessions expire after 12 hours.
- Keep the admin URL and password with the coordinator only.
- Do not add patient-identifying information to the questionnaire.
- Download backups regularly and store them securely.
- The server sets CSP, `X-Frame-Options`, `X-Content-Type-Options`, a body-size limit, a submission rate limit and constant-time token comparison.
