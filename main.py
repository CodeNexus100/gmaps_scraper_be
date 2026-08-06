import sys
import os
import json
from scraper import scrape_query

def safe_str(s):
    if s is None:
        return "None"
    return str(s).encode("ascii", "replace").decode("ascii")

def run_test_query(query: str):
    print("\n" + "=" * 70)
    print(f"=== TESTING SPARSE-RESULT QUERY: '{query}' ===")
    print("=" * 70)

    results = scrape_query(query, max_scrolls=2, click_details=True)

    print(f"\nSuccessfully scraped {len(results)} total listings for query '{query}'.")
    if results:
        print(f"Displaying parsed output for top 5 listings:\n")
        for idx, listing in enumerate(results[:5], 1):
            print(f"--- LISTING #{idx} ---")
            print(f"Name:         {safe_str(listing.get('name'))}")
            print(f"CID:          {safe_str(listing.get('cid'))}")
            print(f"Category:     {safe_str(listing.get('category'))}")
            print(f"Rating:       {listing.get('rating')} ({listing.get('review_count')} reviews)")
            print(f"Address:      {safe_str(listing.get('address'))}")
            detail = listing.get('detail', {})
            print(f"Detail Phone: {safe_str(detail.get('phone'))}")
            print(f"Detail Web:   {safe_str(detail.get('website'))}")
            print(f"Detail Addr:  {safe_str(detail.get('full_address'))}")
            print()

    slug = query.replace(" ", "_").lower()
    raw_dir = os.path.join("raw", f"query_{slug}")
    if os.path.exists(raw_dir):
        files = os.listdir(raw_dir)
        search_files = [f for f in files if f.startswith("search_page_")]
        place_files = [f for f in files if f.startswith("place_")]
        print("=" * 60)
        print(f"Raw Archival Summary for '{raw_dir}':")
        print(f"  - Search Page JSON Files:  {len(search_files)}")
        print(f"  - Place Detail JSON Files: {len(place_files)}")
        print(f"  - Total Files in Directory: {len(files)}")
        print("=" * 60)
    return results

def main():
    print("======================================================================")
    print("=== GENUINE SPARSE-RESULT EDGE CASE SCRAPER TEST ===")
    print("======================================================================")

    sparse_query = "scuba diving center in Leh"
    sparse_results = run_test_query(sparse_query)

    print("\n" + "=" * 70)
    print("SPARSE-RESULT TEST SUMMARY")
    print("=" * 70)
    print(f"  - Query: '{sparse_query}' -> Extracted {len(sparse_results)} listings.")
    print("=" * 70)

if __name__ == "__main__":
    main()
