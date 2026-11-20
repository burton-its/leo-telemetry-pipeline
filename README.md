# leo-telemetry-pipeline

Pulls satellite telemetry from SatNOGS, decodes it, stores it in SQLite, and
shows satellite positions on a 3D globe. Currently tracking the ISS (NORAD 25544).

## Quick start (Docker)

Prerequisites: Docker Desktop, a SatNOGS DB API token, and a Cesium Ion token.

```bash
git clone https://github.com/burton-its/leo-telemetry-pipeline.git
cd leo-telemetry-pipeline
cp .env.example .env     # then fill in both tokens (for linxux/mac)
docker compose up --build
```

- Globe: http://localhost:8080
- API: http://localhost:8000/api/get-tle-telemetry?ids=25544

Stop with Ctrl+C or `docker compose down`. Data lives in a Docker volume;
`docker compose down -v` also deletes it.

## Services

| Service | What it does |
|---|---|
| `ingest-iss` | Fetches ISS frames from SatNOGS every 15 minutes |
| `pipeline` | Runs decode, then store, on a timer |
| `api` | FastAPI serving telemetry and CZML |
| `web` | nginx serving the Cesium globe and proxying `/api` |

To track another satellite, copy the `ingest-iss` block in `docker-compose.yml`
(new name and NORAD ID) and add the ID to `NORAD_IDS` on the `pipeline` service.

## Deploying

See [DEPLOY.md](DEPLOY.md) for hosting on a single AWS EC2 instance.

