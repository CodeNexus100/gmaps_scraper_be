import sys
import os
import json
from scraper import scrape_query

def safe_str(s):
    if s is None:
        return "None"
    return str(s).encode("ascii", "replace").decode("ascii")

def run_single_query(query: str):
    print("=" * 70)
    print(f"=== RUNNING QUERY: '{query}' ===")
    print("=" * 70)
    
    results = scrape_query(query, max_scrolls=3, click_details=True)

    print(f"\nSuccessfully scraped {len(results)} total listings for query '{query}'.")
    print(f"Displaying parsed output for top 3 listings:\n")

    for idx, listing in enumerate(results[:3], 1):
        print(f"--- LISTING #{idx} ---")
        print(f"Name:         {safe_str(listing.get('name'))}")
        print(f"CID:          {safe_str(listing.get('cid'))}")
        print(f"Category:     {safe_str(listing.get('category'))}")
        print(f"Rating:       {listing.get('rating')} ({listing.get('review_count')} reviews)")
        print(f"Address:      {safe_str(listing.get('address'))}")
        print(f"Coordinates:  Lat={listing.get('lat')}, Lng={listing.get('lng')}")
        detail = listing.get('detail', {})
        print(f"Detail Phone: {safe_str(detail.get('phone'))}")
        print(f"Detail Web:   {safe_str(detail.get('website'))}")
        print(f"Detail Addr:  {safe_str(detail.get('full_address'))}")
        print()

    raw_subdirs = [d for d in os.listdir("raw") if os.path.isdir(os.path.join("raw", d))] if os.path.exists("raw") else []
    print(f"Raw Scoped Subdirectories on disk: {raw_subdirs}")
    return results

def main():
    queries = ["travel agencies in Delhi", "travel agencies in Mumbai"]
    if len(sys.argv) > 1:
        queries = [" ".join(sys.argv[1:])]

    print("======================================================================")
    print("=== MULTI-QUERY SEQUENTIAL SCRAPER VERIFICATION RUN ===")
    print("======================================================================")

    results_map = {}
    for q in queries:
        res = run_single_query(q)
        results_map[q] = res

    print("\n" + "=" * 70)
    print("MULTI-QUERY SEQUENTIAL EXECUTION VERIFICATION SUMMARY")
    print("=" * 70)
    for q, res in results_map.items():
        print(f"  - Query: '{q}' -> Extracted {len(res)} total listings.")
    print("=" * 70)

if __name__ == "__main__":
    main()
