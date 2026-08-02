import sys
import os
import json
from scraper import scrape_query

def safe_str(s):
    if s is None:
        return "None"
    return str(s).encode("ascii", "replace").decode("ascii")

def main():
    query = "travel agencies in Delhi"
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])

    print(f"=== Running Google Maps Scraper for: '{query}' ===")
    results = scrape_query(query, max_scrolls=4, click_details=True)

    print(f"\nSuccessfully scraped {len(results)} total listings.")
    print("=" * 60)
    print(f"Displaying parsed output for top 5 listings:\n")

    for idx, listing in enumerate(results[:5], 1):
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
        hours = detail.get('hours')
        if isinstance(hours, dict):
            print("Detail Hours:")
            for day, sched in hours.items():
                print(f"  - {day}: {safe_str(sched)}")
        else:
            print(f"Detail Hours: {safe_str(hours)}")
        print()

    # Verify raw JSON files saved
    raw_files = [f for f in os.listdir("raw") if f.endswith(".json")] if os.path.exists("raw") else []
    place_files = [f for f in raw_files if f.startswith("place_")]
    search_files = [f for f in raw_files if f.startswith("search_page_")]

    print("=" * 60)
    print(f"Raw Response Archival Verification:")
    print(f"Total raw search page JSON files: {len(search_files)}")
    print(f"Total raw place detail JSON files: {len(place_files)}")
    print(f"Total raw JSON files saved in raw/: {len(raw_files)}")
    for f in raw_files[:10]:
        size = os.path.getsize(os.path.join("raw", f))
        print(f"  - raw/{f} ({size} bytes)")

    if len(raw_files) > 10:
        print(f"  ... and {len(raw_files) - 10} more raw JSON files.")

if __name__ == "__main__":
    main()
