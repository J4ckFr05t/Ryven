# Ryven

Ryven is a FastAPI + WebSocket app with a static frontend, now fully containerized with Docker.

## Prerequisites

- Docker Desktop (or Docker Engine + Docker Compose v2)
- API keys set as environment variables (shell export, `.env`, or Portainer UI)
- Required `AUTH_SIGNING_KEY` for auth hashing/session signing

## Development (your laptop)

Start the app:

```bash
APP_MODE=dev docker compose up --build --watch
```

What this gives you:

- App available at `http://localhost:8000`
- Backend auto-reloads when Python files change (`uvicorn --reload`)
- Compose `watch` syncs changed project files into the container and restarts when needed
- Conversation data persists via mounted `./data` folder

Stop:

```bash
docker compose down
```

## Production (Raspberry Pi via Portainer)

Use the same `docker-compose.yml` in Portainer stack.

Portainer stack file:

```yaml
services:
  ryven:
    container_name: ryven
    build:
      context: .
      dockerfile: Dockerfile
    ports:
      - "8000:8000"
    environment:
      APP_MODE: ${APP_MODE:-prod}
      OPENAI_API_KEY: ${OPENAI_API_KEY:-}
      GEMINI_API_KEY: ${GEMINI_API_KEY:-}
      TAVILY_API_KEY: ${TAVILY_API_KEY:-}
      GITHUB_PERSONAL_ACCESS_TOKEN: ${GITHUB_PERSONAL_ACCESS_TOKEN:-}
      ALLOWED_DIRECTORIES: ${ALLOWED_DIRECTORIES:-}
      AUTH_SIGNING_KEY: ${AUTH_SIGNING_KEY:-}
    volumes:
      - ./data:/app/data
      - ./Files:/app/Files
    restart: unless-stopped
    command: >
      sh -c 'if [ "${APP_MODE:-prod}" = "dev" ]; then
        exec uvicorn server:app --host 0.0.0.0 --port 8000 --reload;
      else
        exec uvicorn server:app --host 0.0.0.0 --port 8000;
      fi'
```

If you deploy from terminal on your Pi, run:

```bash
docker compose up -d --build
```

`APP_MODE` defaults to `prod`, so you do not need to set it in Portainer unless you want to be explicit.
Set all API keys/secrets in the Portainer Stack **Environment variables** UI.
Set `AUTH_SIGNING_KEY` in environment variables before startup. Ryven will not start without it.

Logs:

```bash
docker compose logs -f ryven
```

## How updates flow

- You develop locally with `APP_MODE=dev` (watch + reload).
- When ready, push code to GitHub.
- In Portainer, pull latest source and redeploy the stack (`Recreate`/`Deploy the stack`).
- Production restarts with new code in detached mode.
