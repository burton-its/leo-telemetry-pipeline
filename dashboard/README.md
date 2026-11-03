# LEO Satellite Dashboard

React/TypeScript frontend

## Run locally

```bash
npm install
npm run dev
```

open http://localhost:5173

just a framework currently

## ideas to onnect the backend

Set `VITE_API_BASE_URL` to the FastAPI server origin (for example,
`http://localhost:8000`) and restart Vite. The frontend requests
`GET /api/dashboard` every 30 seconds and when Refresh is clicked

Expected response contract-

```json
{
  "updatedAt": "2026-11-03T20:00:00Z",
  "observations": [
    {
      "id": "example-id",
      "noradId": 25544,
      "satellite": "ISS",
      "timestamp": "2026-11-03T19:55:00Z",
      "station": "example-station",
      "stationId": 123,
      "frameHex": "",
      "parseStatus": "unparsed"
    }
  ]
}

