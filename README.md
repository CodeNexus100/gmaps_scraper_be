# Google Maps Scraper Backend Service

A high-performance FastAPI service that acts as a wrapper around a Playwright-based Google Maps network interception scraper. It extracts rich, deep listing details directly from Google Maps backend XHR/RPC endpoints rather than brittle DOM scraping, and features live WebSocket progress streaming.

## Setup Instructions

This project requires Python 3.8+ and uses Playwright for browser automation.

1. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   
   # Windows
   .\venv\Scripts\activate
   
   # macOS/Linux
   source venv/bin/activate
   ```

2. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Playwright browsers:**
   This is a crucial step! Even after pip installing playwright, you must tell it to download the actual browser binaries (Chromium).
   ```bash
   playwright install chromium
   ```

## Running the Service

Start the FastAPI server via Uvicorn:
```bash
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```
*(Add `--reload` if you are developing locally.)*

You can then run the end-to-end integration test in another terminal window:
```bash
python test_fastapi_service.py
```
