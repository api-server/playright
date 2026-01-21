# Playwright URL Sniffer

Simple yet powerful Docker-based Playwright URL extractor to get data in realtime. Uses a queue system to handle requests one at a time for optimal memory usage.

## Features

- 🎯 **URL Sniffing**: Intercepts all network requests from target pages
- 🎬 **Auto-play Detection**: Automatically clicks play buttons to trigger streams
- 🔍 **Filtered Extraction**: Extract specific resource types (m3u8, mpd, etc.)
- 🚦 **Queue System**: Processes requests sequentially to minimize memory usage
- 🔒 **SSL Bypass**: Ignores SSL certificate errors
- 🐳 **Docker Ready**: Easy deployment on Render, VPS, or any Docker host

## API Endpoints

### `GET /`
Health check endpoint showing server status and queue information.

**Response:**
```json
{
  "status": "online",
  "service": "Playwright URL Sniffer",
  "queue_size": 0,
  "processing": false
}
```

### `GET /api/{url}`
Sniff ALL URLs loaded by the target page. Automatically clicks play buttons to trigger streams.

**Example:**
```
GET /api/https://example.com/video-page
```

**Response:**
```json
{
  "url": "https://example.com/video-page",
  "total_urls": 45,
  "urls": ["url1", "url2", ...]
}
```

### `GET /api/{url}/{filter}`
Sniff and return only URLs matching the specified filter (e.g., m3u8, mpd, ts).

**Example:**
```
GET /api/https://example.com/video-page/m3u8
```

**Response:**
```json
{
  "url": "https://example.com/video-page",
  "filter": "m3u8",
  "total_urls": 2,
  "urls": ["https://cdn.example.com/stream/playlist.m3u8"]
}
```

## Local Development

### Prerequisites
- Python 3.11+
- Docker (for containerized deployment)

### Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
playwright install chromium
```

2. **Run the server:**
```bash
python app.py
# or
uvicorn app:app --host 0.0.0.0 --port 8000
```

3. **Test the API:**
```bash
curl http://localhost:8000/
curl http://localhost:8000/api/https://example.com
curl http://localhost:8000/api/https://example.com/m3u8
```

## Docker Deployment

### Build and run locally:
```bash
docker build -t playwright-sniffer .
docker run -p 8000:8000 playwright-sniffer
```

### Deploy to Render:
1. Push your code to GitHub
2. Connect your repository to Render
3. Render will automatically detect `render.yaml` and deploy

### Deploy to VPS:
```bash
# SSH into your VPS
ssh user@your-vps-ip

# Clone repository
git clone https://github.com/yourusername/playright.git
cd playright

# Build and run with Docker
docker build -t playwright-sniffer .
docker run -d -p 8000:8000 --name playwright-sniffer --restart unless-stopped playwright-sniffer

# (Optional) Setup Nginx reverse proxy for production
```

## Memory Management

The application uses a queue system to process requests one at a time:
- ✅ Opens browser for each request
- ✅ Closes browser after processing
- ✅ Waits 2 seconds between requests to free memory
- ✅ Prevents memory overload from concurrent browsers

## Configuration

Key settings in [app.py](app.py):
- `timeout=30000` - Page load timeout (30 seconds)
- `await asyncio.sleep(2)` - Wait time after page load
- `await asyncio.sleep(5)` - Wait time after clicking play
- `await asyncio.sleep(2)` - Wait between queue requests

Adjust these based on your target websites and server resources.

## Troubleshooting

**Timeout errors:** Increase the timeout values in `app.py`

**Memory issues:** The queue system should handle this, but you can add resource limits in Docker:
```bash
docker run -p 8000:8000 --memory="1g" --cpus="1.0" playwright-sniffer
```

**Sites not loading:** Some sites have anti-bot protection. Try adjusting the user agent or adding stealth plugins.

## License

MIT
