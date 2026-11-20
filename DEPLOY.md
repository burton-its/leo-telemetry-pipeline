# Deploying to AWS EC2

One EC2 instance running the same `docker compose` stack.

## 1. Launch the instance
- Ubuntu Server 24.04 LTS, `t3.small` (2 GB RAM), 20 GB gp3.
- Create a key pair and keep the `.pem` file out of the repo.
- Security group: SSH (22) from your IP only, HTTP (80) from anywhere.
  Do not open 8000; nginx proxies `/api`.
- Allocate and associate an Elastic IP so the address survives stop/start.

## 2. Install Docker
```bash
ssh -i your-key.pem ubuntu@<elastic-ip>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu
exit    # reconnect so the group change applies
docker compose version
```

## 3. Get the code and tokens
```bash
git clone https://github.com/burton-its/leo-telemetry-pipeline.git
cd leo-telemetry-pipeline
cp .env.example .env
nano .env          # SATNOGS_DB_TOKEN and VITE_CESIUM_ION_TOKEN
chmod 600 .env
```

## 4. Start
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
docker compose logs -f ingest-iss
```
Open `http://<elastic-ip>`.

## Updating
```bash
git pull origin main
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

