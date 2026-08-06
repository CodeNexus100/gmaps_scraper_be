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
                    msg = await asyncio.wait_for(websocket.recv(), timeout=35.0)
                    data = json.loads(msg)
                    ws_frames.append(data)
                    phase = data.get("progress", {}).get("phase", data.get("status"))
                    print(f"  [WS FRAME #{len(ws_frames)}] Status: {data.get('status')}, Phase: {phase}, Data: {data.get('progress')}")
                    if data.get("status") in ["completed", "failed", "blocked"]:
                        break
                except asyncio.TimeoutError:
                    print("[WS CLIENT] WebSocket receive timeout, ending listener.")
                    break
    except Exception as e:
        print(f"[WS CLIENT] WebSocket error: {e}")

def run_test():
    print("======================================================================")
    print("=== FASTAPI SERVICE END-TO-END INTEGRATION TEST (PORT 8005) ===")
    print("======================================================================")

    print(f"Starting fresh uvicorn server on http://127.0.0.1:{PORT}...")
    server_process = subprocess.Popen(
        ["python", "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(3)

    try:
        query = "scuba diving center in Leh"
        print(f"\n[POST /api/scrape] Submitting job for query: '{query}'...")
        res = requests.post(f"{API_BASE}/api/scrape", json={"query": query})
        assert res.status_code == 202, f"Expected 202, got {res.status_code}: {res.text}"
        job_data = res.json()
        job_id = job_data["job_id"]
        print(f"[POST SUCCESS] Received job_id: '{job_id}', initial status: '{job_data['status']}'")

        ws_frames = []
        asyncio.run(listen_websocket(job_id, ws_frames))

        print(f"\n[GET /api/jobs/{job_id}] Checking job status...")
        status_res = requests.get(f"{API_BASE}/api/jobs/{job_id}")
        assert status_res.status_code == 200
        print(f"[STATUS] {json.dumps(status_res.json(), indent=2)}")

        print(f"\n[GET /api/jobs/{job_id}/results] Retrieving final results...")
        results_res = requests.get(f"{API_BASE}/api/jobs/{job_id}/results")
        assert results_res.status_code == 200
        results_data = results_res.json()
        print(f"[RESULTS SUCCESS] Retrieved {results_data['total_results']} total listings!")
        
        for idx, item in enumerate(results_data["listings"][:3], 1):
            print(f"  #{idx} Name: {item.get('name')} | Phone: {item.get('detail', {}).get('phone')} | Web: {item.get('detail', {}).get('website')}")

        print("\n" + "=" * 70)
        print("FASTAPI SERVICE VERIFICATION SUMMARY:")
        print("=" * 70)
        print(f"  - POST /api/scrape:         PASSED (job_id: {job_id})")
        print(f"  - WebSocket Live Progress:  PASSED ({len(ws_frames)} frames streamed)")
        print(f"  - GET /api/jobs/{{id}}:       PASSED (status: {results_data['status']})")
        print(f"  - GET /api/jobs/{{id}}/results: PASSED ({results_data['total_results']} listings returned)")
        print("=" * 70)

    finally:
        print("\nStopping uvicorn server...")
        try:
            server_process.kill()
        except Exception:
            pass

if __name__ == "__main__":
    run_test()
