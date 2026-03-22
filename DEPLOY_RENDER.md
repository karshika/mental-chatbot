# Deploy MindCare on Render (Flask)

This project is now configured for Render using a root Render blueprint file:
- `render.yaml` at repo root
- App root directory: `.`

## 1. Push latest code

Push all current changes to your GitHub repository.

## 2. Create Web Service on Render

1. Open Render dashboard.
2. Click **New +** -> **Blueprint** (recommended) and connect your GitHub repo.
3. Render will detect `render.yaml` from repo root.
4. Confirm the service name and create deployment.

If you choose **Web Service** manually instead of Blueprint, use:
- Root Directory: `.`
- Build Command: `pip install -r requirements.txt`
- Start Command: `gunicorn app:app`

## 3. Set required environment variables

In Render service settings, set:
- `MISTRAL_API_KEY`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SUPABASE_SERVICE_KEY`
- `FLASK_SECRET` (random long string)

Optional:
- `FLASK_DEBUG=0`

## 4. Confirm first deploy

Check Render logs for:
- successful package install
- `gunicorn` startup with `app:app`
- no missing env var errors

## 5. Verify app behavior

1. Open the Render URL.
2. Check homepage loads.
3. Test sign up/sign in.
4. Open chat and send a test message.
5. Verify resources, games, and accessibility tools in chat UI.

## 6. Common issues and fixes

1. Error: `ModuleNotFoundError`
   - Ensure dependency is listed in `requirements.txt`.
2. Error: App failed to bind port
   - Keep start command as `gunicorn app:app`.
3. Error: Auth/DB failures
   - Recheck Supabase env var values in Render.
4. 502/503 at startup
   - Check build/runtime logs and confirm all required env vars are set.
