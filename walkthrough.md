# Google Maps Scraper Frontend Walkthrough

We built and verified the React/TypeScript frontend for the Google Maps scraper service, adapted from the Zomato scraper frontend shell.

## Summary of Accomplishments

### 1. Job Submission Form (`InputPanel.tsx`)
- Search Query input with placeholder `e.g. "travel agencies in Delhi" or "cafes"`.
- Optional Location Bias input (e.g., `Delhi, India`).
- Submit button triggers `POST /api/scrape`, storing returned `job_id` and connecting to the event stream.
- Submissions disable inputs while scraping and present an active loading spinner.

### 2. Live Progress View (`LiveProgressPanel.tsx`)
- Connects immediately to WebSocket `ws://localhost:8000/ws/jobs/{job_id}`.
- Real-time phase indicators:
  - **Phase 1 (Search Discovery)**: Search pages captured & unique CIDs discovered.
  - **Phase 2 (Interleaved Details Extraction Loop)**: Expected CIDs & progress percentage.
  - **4-State Click Progress Counters**: `Captured` (emerald), `Skipped Ad` (amber), `Failed` (rose), `Never Attempted` (slate).
- **Distinct Visual Alert States**:
  - **Blocked (Bot Challenge Detected)**: Amber shield alert with security warning badge.
  - **Failed (Generic Error)**: Crimson error card detailing exception trace.
- **Event Stream Terminal**: Live log window streaming all raw WebSocket frames with color-coded tags and auto-scrolling.

### 3. Results View & CSV Export (`DataTable.tsx` & `DownloadButton.tsx`)
- Calls `GET /api/jobs/{job_id}/results` upon job completion.
- Interactive results table displaying: `#`, `Name`, `Phone`, `Address`, `Website` (clickable links), `Rating` (star badges), and `Reviews`.
- **Export CSV Button**: Client-side UTF-8 BOM CSV exporter formatting scraped rows into `.csv` files.

### 4. Zero Persistence Assumptions
- State maintained in memory per session without invalid assumptions about backend restarts.

---

## Visual Demonstration

![Google Maps Scraper Frontend Dashboard](file:///C:/Users/smart/.gemini/antigravity-ide/brain/89376832-9a06-484a-bed8-4a7845fc38a5/dashboard_top_1785681582819.png)

---

## Backend Agent Instruction Prompt

Copy and paste the prompt below to your backend agent to ensure `server.py` runs smoothly on Windows with Python 3.8+:

```markdown
Please update `server.py` to configure the Windows Selector event loop policy and handle thread-safe WebSocket broadcasts:

1. Add the Windows Selector Event Loop Policy configuration near top of `server.py`:
```python
import sys
import asyncio

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```

2. Capture the main event loop on startup and broadcast thread-safely:
```python
MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

@app.on_event("startup")
async def startup_event():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

def safe_broadcast(job_id: str, data: Dict[str, Any]):
    if MAIN_LOOP and MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(broadcast_ws_event(job_id, data), MAIN_LOOP)
```

3. Inside `run_scrape_background_job`, instantiate a new event loop for Playwright on the background thread:
```python
def run_scrape_background_job(job_id: str, query: str):
    thread_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(thread_loop)
    # Use safe_broadcast(job_id, ...) inside on_progress and for completion/blocked/failed events
```
```
