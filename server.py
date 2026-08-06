import sys
import os
import time
import uuid
import asyncio
import logging
from typing import Dict, Any, List, Optional
from collections import defaultdict

from fastapi import FastAPI, BackgroundTasks, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from scraper import scrape_query, BotChallengeDetectedException

# Apply global Windows Proactor policy for Playwright subprocess compatibility in worker threads
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gmaps_fastapi")

app = FastAPI(
    title="Google Maps Scraper Service API",
    description="FastAPI service wrapper around Playwright Google Maps network interception scraper with live WebSocket progress streaming.",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JOBS: Dict[str, Dict[str, Any]] = {}
WS_CONNECTIONS: Dict[str, List[WebSocket]] = defaultdict(list)

class ScrapeRequest(BaseModel):
    query: str
    location_bias: Optional[str] = None

class ScrapeResponse(BaseModel):
    job_id: str
    query: str
    status: str
    created_at: float

async def broadcast_ws_event(job_id: str, data: Dict[str, Any]):
    """Helper to broadcast JSON event frames to all connected WebSockets for a job_id."""
    connections = WS_CONNECTIONS.get(job_id, [])
    dead_connections = []
    for ws in connections:
        try:
            await ws.send_json(data)
        except Exception:
            dead_connections.append(ws)
    for ws in dead_connections:
        if ws in connections:
            connections.remove(ws)

MAIN_LOOP: Optional[asyncio.AbstractEventLoop] = None

@app.on_event("startup")
async def startup_event():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    logger.info(f"Main FastAPI Event Loop initialized: {type(MAIN_LOOP).__name__}")

def safe_broadcast(job_id: str, data: Dict[str, Any]):
    if MAIN_LOOP and MAIN_LOOP.is_running():
        asyncio.run_coroutine_threadsafe(broadcast_ws_event(job_id, data), MAIN_LOOP)

def run_scrape_background_job(job_id: str, query: str):
    """Background task runner for executing scrape_query with progress callbacks."""
    logger.info(f"[BACKGROUND TASK START] Processing job_id={job_id} for query='{query}' on background thread.")
    
    # Create thread-local loop using the global policy
    thread_loop = asyncio.new_event_loop()
    
    asyncio.set_event_loop(thread_loop)
    logger.info(f"[BACKGROUND TASK LOOP] Active event loop policy: {type(thread_loop).__name__}")

    def on_progress(event_data: Dict[str, Any]):
        JOBS[job_id]["progress"] = event_data
        JOBS[job_id]["updated_at"] = time.time()
        
        try:
            safe_broadcast(job_id, {
                "job_id": job_id,
                "status": JOBS[job_id]["status"],
                "progress": event_data,
                "timestamp": time.time()
            })
        except Exception as e:
            logger.warning(f"Failed to broadcast WS progress for job_id={job_id}: {e}")

    try:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["updated_at"] = time.time()

        full_query = query
        if JOBS[job_id].get("location_bias"):
            full_query = f"{query} in {JOBS[job_id]['location_bias']}"

        results = scrape_query(
            query=full_query,
            max_scrolls=5,
            click_details=True,
            progress_callback=on_progress
        )

        JOBS[job_id]["status"] = "completed"
        JOBS[job_id]["results"] = results
        JOBS[job_id]["updated_at"] = time.time()
        
        safe_broadcast(job_id, {
            "job_id": job_id,
            "status": "completed",
            "progress": {"phase": "complete", "total_listings": len(results)},
            "timestamp": time.time()
        })
        logger.info(f"[JOB COMPLETED] job_id={job_id} extracted {len(results)} organic listings.")

    except BotChallengeDetectedException as bce:
        # Requirement 3: Distinct "blocked" status for bot challenge
        logger.error(f"[JOB BLOCKED] Bot challenge detected for job_id={job_id}: {bce}")
        JOBS[job_id]["status"] = "blocked"
        JOBS[job_id]["error"] = str(bce)
        JOBS[job_id]["updated_at"] = time.time()
        
        safe_broadcast(job_id, {
            "job_id": job_id,
            "status": "blocked",
            "error": str(bce),
            "progress": {
                "phase": "blocked",
                "status": "blocked",
                "reason": str(bce)
            },
            "timestamp": time.time()
        })

    except Exception as e:
        logger.error(f"[JOB FAILED] Error executing job_id={job_id}: {e}", exc_info=True)
        JOBS[job_id]["status"] = "failed"
        JOBS[job_id]["error"] = str(e) or repr(e)
        JOBS[job_id]["updated_at"] = time.time()
        
        safe_broadcast(job_id, {
            "job_id": job_id,
            "status": "failed",
            "error": str(e) or repr(e),
            "timestamp": time.time()
        })

    finally:
        try:
            thread_loop.close()
        except Exception:
            pass


@app.post("/api/scrape", response_model=ScrapeResponse, status_code=202)
def start_scrape_job(request: ScrapeRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    now = time.time()

    JOBS[job_id] = {
        "job_id": job_id,
        "query": request.query,
        "location_bias": request.location_bias,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "progress": {"phase": "pending", "status": "pending"},
        "results": [],
        "error": None
    }

    background_tasks.add_task(run_scrape_background_job, job_id, request.query)
    logger.info(f"Queued background scrape job: job_id={job_id}, query='{request.query}'")
    return ScrapeResponse(job_id=job_id, query=request.query, status="pending", created_at=now)


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail=f"Job ID '{job_id}' not found.")
    
    job = JOBS[job_id]
    return {
        "job_id": job["job_id"],
        "query": job["query"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "progress": job["progress"],
        "result_count": len(job["results"]),
        "error": job["error"]
    }


@app.get("/api/jobs/{job_id}/results")
def get_job_results(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail=f"Job ID '{job_id}' not found.")
    
    job = JOBS[job_id]
    if job["status"] in ["pending", "running"]:
        raise HTTPException(status_code=400, detail=f"Job '{job_id}' is still in progress (status: {job['status']}).")
    
    if job["status"] in ["failed", "blocked"]:
        raise HTTPException(status_code=400, detail=f"Job '{job_id}' ended with status '{job['status']}': {job['error']}")

    return {
        "job_id": job["job_id"],
        "query": job["query"],
        "status": job["status"],
        "total_results": len(job["results"]),
        "listings": job["results"]
    }


@app.websocket("/ws/jobs/{job_id}")
async def websocket_job_progress(websocket: WebSocket, job_id: str):
    await websocket.accept()
    if job_id not in JOBS:
        await websocket.send_json({"error": f"Job ID '{job_id}' not found.", "status": "not_found"})
        await websocket.close()
        return

    WS_CONNECTIONS[job_id].append(websocket)
    logger.info(f"WebSocket client connected for job_id={job_id}")

    job = JOBS[job_id]
    await websocket.send_json({
        "job_id": job["job_id"],
        "status": job["status"],
        "progress": job["progress"],
        "timestamp": time.time()
    })

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"WebSocket client disconnected for job_id={job_id}")
        if websocket in WS_CONNECTIONS[job_id]:
            WS_CONNECTIONS[job_id].remove(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
