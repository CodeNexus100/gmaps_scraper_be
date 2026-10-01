import time
import subprocess
import requests
import json
import asyncio
import websockets

PORT = 8005
API_BASE = f"http://127.0.0.1:{PORT}"
WS_BASE = f"ws://127.0.0.1:{PORT}"

async def listen_websocket(job_id: str, ws_frames: list):
    ws_url = f"{WS_BASE}/ws/jobs/{job_id}"
    print(f"[WS CLIENT] Connecting to {ws_url}...")
    try:
        async with websockets.connect(ws_url) as websocket:
            print("[WS CLIENT] Connected!")
            while True:
                try:
                    msg = await asyncio.wait_for(websocket.recv(), timeout=600.0)
                    data = json.loads(msg)
                    ws_frames.append(data)
                    phase = data.get("progress", {}).get("phase", data.get("status"))
                    
                    quad_stats = ""
                    if data.get("progress", {}).get("quadtree"):
                        q = data["progress"]["quadtree"]
                        quad_stats = f" [Quad Depth: {q['depth']}, Searched: {q['boxes_searched']}, Queued: {q['boxes_queued']}, CIDs: {q['total_unique_cids']}]"
                        
                    print(f"  [WS FRAME #{len(ws_frames)}] Status: {data.get('status')}, Phase: {phase}{quad_stats}")
                    if data.get("status") in ["completed", "failed", "blocked"]:
                        break
                except asyncio.TimeoutError:
                    print("[WS CLIENT] WebSocket receive timeout, ending listener.")
                    break
    except Exception as e:
        print(f"[WS CLIENT] WebSocket error: {e}")

def run_test_case(name: str, payload: dict):
    print(f"\n{'='*70}\n=== RUNNING TEST CASE: {name} ===\n{'='*70}")
    
    print(f"[POST /api/scrape] Submitting job with payload: {payload}...")
    res = requests.post(f"{API_BASE}/api/scrape", json=payload)
    assert res.status_code == 202, f"Expected 202, got {res.status_code}: {res.text}"
    job_id = res.json()["job_id"]
    print(f"[POST SUCCESS] Received job_id: '{job_id}'")

    ws_frames = []
    asyncio.run(listen_websocket(job_id, ws_frames))

    print(f"\n[GET /api/jobs/{job_id}] Checking job status...")
    status_res = requests.get(f"{API_BASE}/api/jobs/{job_id}")
    assert status_res.status_code == 200

    print(f"\n[GET /api/jobs/{job_id}/results] Retrieving final results...")
    results_res = requests.get(f"{API_BASE}/api/jobs/{job_id}/results")
    assert results_res.status_code == 200
    results_data = results_res.json()
    print(f"[RESULTS SUCCESS] Retrieved {results_data['total_results']} total listings!")
    
    for idx, item in enumerate(results_data["listings"][:3], 1):
        name = str(item.get('name')).encode('ascii', 'ignore').decode('ascii')
        print(f"  #{idx} Name: {name} | Phone: {item.get('detail', {}).get('phone')} | Web: {item.get('detail', {}).get('website')}")
        
    return results_data

def run_test():
    print("Starting fresh uvicorn server on http://127.0.0.1:8005...")
    server_process = subprocess.Popen(
        ["python", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)

    try:
        # Run Current BBox 3 times back to back to measure variance
        payload = {
            "query": "restaurants",
            "bbox": [28.5355, 77.1291, 28.6855, 77.2791] 
        }
        
        run1 = run_test_case("Restaurants - Run 1", payload)
        time.sleep(2) # Small delay between runs
        run2 = run_test_case("Restaurants - Run 2", payload)
        time.sleep(2)
        run3 = run_test_case("Restaurants - Run 3", payload)

        print("\n" + "=" * 70)
        print("FASTAPI VARIANCE VERIFICATION SUMMARY:")
        print("=" * 70)
        print(f"Run 1 Results: {run1['total_results']} listings")
        print(f"Run 2 Results: {run2['total_results']} listings")
        print(f"Run 3 Results: {run3['total_results']} listings")
        print("=" * 70)

    finally:
        print("\nStopping uvicorn server...")
        try:
            server_process.kill()
        except Exception:
            pass

if __name__ == "__main__":
    run_test()
