import os
import json
import time
import re
import logging
from typing import List, Dict, Any
from playwright.sync_api import sync_playwright

from parser import parse_raw_json, parse_search_item, parse_place_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gmaps_scraper")

class GoogleMapsScraper:
    def __init__(self, raw_dir: str = "raw"):
        self.raw_dir = raw_dir
        os.makedirs(self.raw_dir, exist_ok=True)
        self.search_pages_raw: List[str] = []
        self.place_details_raw: Dict[str, str] = {} # cid -> raw_json_text
        self.place_reviews_raw: Dict[str, str] = {} # cid -> raw_json_text

    def extract_cid_from_text_or_url(self, url: str, text: str) -> str:
        """Extract CID hex pair (0x...:0x...) from response URL or body."""
        match = re.search(r'0x[0-9a-fA-F]+%3A0x[0-9a-fA-F]+', url)
        if match:
            return match.group(0).replace("%3A", "_")
        
        match = re.search(r'0x[0-9a-fA-F]+:0x[0-9a-fA-F]+', url)
        if match:
            return match.group(0).replace(":", "_")

        match = re.search(r'0x[0-9a-fA-F]+:0x[0-9a-fA-F]+', text)
        if match:
            return match.group(0).replace(":", "_")

        return f"place_{hash(url) & 0xffffffff}"

    def find_place_items(self, data, found_items=None) -> List[list]:
        """Recursively scan JSON array structure to extract listing items."""
        if found_items is None:
            found_items = []

        if isinstance(data, list):
            has_cid = any(isinstance(x, str) and x.startswith("0x") and ":" in x for x in data)
            if has_cid and len(data) > 5:
                found_items.append(data)
            else:
                for item in data:
                    self.find_place_items(item, found_items)

        elif isinstance(data, dict):
            for val in data.values():
                self.find_place_items(val, found_items)

        return found_items

    def handle_response(self, response):
        url = response.url
        try:
            # Intercept search responses
            if "/maps/rpc/search" in url or ("search?" in url and "tbm=map" in url):
                text = response.text()
                logger.info(f"Intercepted search response ({len(text)} bytes): {url[:80]}...")
                self.search_pages_raw.append(text)
                
                # Save raw search page
                page_idx = len(self.search_pages_raw)
                cleaned_text = parse_raw_json(text)
                filename = os.path.join(self.raw_dir, f"search_page_{page_idx}.json")
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(cleaned_text, f, indent=2)
                logger.info(f"Archived raw search page to {filename}")

            # Intercept place detail responses
            elif "/maps/rpc/place" in url or "/maps/preview/place" in url:
                text = response.text()
                logger.info(f"Intercepted place detail response ({len(text)} bytes): {url[:80]}...")
                cleaned_text = parse_raw_json(text)
                
                cid_key = self.extract_cid_from_text_or_url(url, text)
                filename = os.path.join(self.raw_dir, f"place_{cid_key}.json")
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(cleaned_text, f, indent=2)
                self.place_details_raw[cid_key] = text
                logger.info(f"Archived raw place detail to {filename} (CID Key: {cid_key})")

            # Intercept reviews responses
            elif "/maps/rpc/listreviews" in url:
                text = response.text()
                logger.info(f"Intercepted reviews response ({len(text)} bytes)")
                cleaned_text = parse_raw_json(text)
                filename = os.path.join(self.raw_dir, f"reviews_{int(time.time())}.json")
                with open(filename, "w", encoding="utf-8") as f:
                    json.dump(cleaned_text, f, indent=2)
                logger.info(f"Archived raw reviews to {filename}")

        except Exception as e:
            logger.warning(f"Error intercepting response from {url[:80]}: {e}")

    def match_card_to_item(self, card, expected_items_by_cid: Dict[str, dict], expected_items_by_name: Dict[str, dict]):
        """Match a DOM card element to an expected search listing by CID or Name."""
        try:
            link = card.query_selector("a.hfA20e") or card.query_selector("a[href*='/maps/place/']") or card
            href = link.get_attribute("href") if link else ""
            aria_label = link.get_attribute("aria-label") if link else ""
            
            title_elem = card.query_selector("div.fontHeadlineSmall")
            title_text = title_elem.inner_text().strip() if title_elem else ""

            # 1. Check CID match in href
            if href:
                match = re.search(r'0x[0-9a-fA-F]+:0x[0-9a-fA-F]+', href) or re.search(r'0x[0-9a-fA-F]+%3A0x[0-9a-fA-F]+', href)
                if match:
                    cid = match.group(0).replace("%3A", ":")
                    if cid in expected_items_by_cid:
                        return expected_items_by_cid[cid]

            # 2. Check Name match
            for candidate_name in [aria_label, title_text]:
                if candidate_name:
                    cand_clean = candidate_name.strip().lower()
                    if cand_clean in expected_items_by_name:
                        return expected_items_by_name[cand_clean]
                    
                    for name_key, item in expected_items_by_name.items():
                        if name_key and (name_key in cand_clean or cand_clean in name_key):
                            return item
        except Exception as e:
            logger.warning(f"Error matching card to expected item: {e}")

        return None

    def build_expected_items(self):
        """Dynamically build expected items map from all search XHR responses captured so far."""
        by_cid: Dict[str, dict] = {}
        by_name: Dict[str, dict] = {}
        for page_text in self.search_pages_raw:
            cleaned_json = parse_raw_json(page_text)
            if not cleaned_json:
                continue
            raw_items = self.find_place_items(cleaned_json)
            for raw_item in raw_items:
                parsed_item = parse_search_item(raw_item)
                cid = parsed_item.get("cid")
                name = parsed_item.get("name")
                if cid:
                    by_cid[cid] = parsed_item
                if name:
                    by_name[name.strip().lower()] = parsed_item
        return by_cid, by_name

    def scrape(self, query: str, max_scrolls: int = 5, click_details: bool = True) -> List[Dict[str, Any]]:
        logger.info(f"Starting browser automation for query: '{query}'")

        processed_cids: set = set()
        failed_cids: set = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            page.on("response", self.handle_response)

            logger.info("Navigating to Google Maps...")
            page.goto("https://www.google.com/maps?hl=en", wait_until="load", timeout=60000)
            time.sleep(3)

            # Handle consent wall if presented
            for frame in page.frames:
                for btn_text in ["Accept all", "Reject all", "I agree", "Agree"]:
                    try:
                        btn = frame.query_selector(f"button:has-text('{btn_text}')")
                        if btn and btn.is_visible():
                            logger.info(f"Clicking consent button: '{btn_text}'")
                            btn.click()
                            time.sleep(2)
                            break
                    except Exception:
                        pass

            # Search box interaction
            search_input = None
            for sel in ["input[name='q']", "input#searchboxinput", "#searchboxinput", "input.searchboxinput"]:
                try:
                    elem = page.query_selector(sel)
                    if elem and elem.is_visible():
                        search_input = elem
                        break
                except Exception:
                    pass

            if not search_input:
                for sel in ["input[name='q']", "input#searchboxinput"]:
                    try:
                        search_input = page.wait_for_selector(sel, timeout=10000)
                        if search_input:
                            break
                    except Exception:
                        pass

            if not search_input:
                raise RuntimeError("Could not locate Google Maps search input element.")

            logger.info(f"Entering search query: '{query}'")
            search_input.fill(query)
            page.keyboard.press("Enter")
            time.sleep(5)

            results_feed = None
            for feed_sel in ["div[role='feed']", "div.m6QEfe", "div[aria-label*='Results']"]:
                try:
                    elem = page.query_selector(feed_sel)
                    if elem:
                        results_feed = elem
                        break
                except Exception:
                    pass

            if results_feed:
                logger.info(f"Scrolling search results panel ({max_scrolls} initial scroll passes)...")
                for scroll_idx in range(max_scrolls):
                    page.evaluate("el => el.scrollBy(0, 1000)", results_feed)
                    time.sleep(2.5)

                if click_details:
                    logger.info("Starting DOM Virtualization Interleaved Click & Scroll Loop...")
                    
                    idle_scroll_count = 0
                    max_idle_scrolls = 8

                    while True:
                        # Dynamically rebuild expected items from all search XHR responses captured so far
                        expected_by_cid, expected_by_name = self.build_expected_items()
                        expected_total = len(expected_by_cid)

                        if len(processed_cids) + len(failed_cids) >= expected_total and expected_total > 0:
                            logger.info(f"All {expected_total} expected CIDs processed ({len(processed_cids)} succeeded, {len(failed_cids)} failed). Exiting click loop.")
                            break

                        if idle_scroll_count >= max_idle_scrolls:
                            never_attempted_count = expected_total - (len(processed_cids) + len(failed_cids))
                            logger.warning(
                                f"[SAFETY EXIT] Safety exit triggered after {max_idle_scrolls} consecutive idle scrolls.\n"
                                f"  Target CIDs Expected: {expected_total}\n"
                                f"  Captured Succeeded:  {len(processed_cids)}\n"
                                f"  Attempted & Failed:  {len(failed_cids)}\n"
                                f"  Never Attempted:     {never_attempted_count}"
                            )
                            break

                        dom_cards = page.query_selector_all("div[role='article'], a.hfA20e, div.Nv2pk")
                        new_clicks_in_pass = 0

                        for idx, card in enumerate(dom_cards):
                            matched_item = self.match_card_to_item(card, expected_by_cid, expected_by_name)
                            if not matched_item:
                                continue

                            cid = matched_item["cid"]
                            name = matched_item.get("name", "Unknown")

                            if cid in processed_cids or cid in failed_cids:
                                continue

                            new_clicks_in_pass += 1
                            idle_scroll_count = 0
                            
                            try:
                                card.scroll_into_view_if_needed(timeout=2000)
                                time.sleep(0.3)
                            except Exception:
                                pass

                            def is_place_rpc(resp):
                                return ("/maps/rpc/place" in resp.url or "/maps/preview/place" in resp.url) and resp.status == 200

                            click_success = False
                            for attempt in [1, 2]:
                                try:
                                    logger.info(f"[CLICK ATTEMPT {attempt}] CID {cid} ('{name[:35]}')...")
                                    with page.expect_response(is_place_rpc, timeout=5000):
                                        card.click()
                                    click_success = True
                                    logger.info(f"[CLICK SUCCESS] Intercepted place detail for CID {cid}")
                                    break
                                except Exception as te:
                                    logger.warning(f"[CLICK TIMEOUT] Attempt {attempt} timed out for CID {cid}. Error: {te}")
                                    time.sleep(1)

                            if click_success:
                                processed_cids.add(cid)
                            else:
                                failed_cids.add(cid)
                                logger.error(f"[CLICK PERMANENT FAIL] CID {cid} ('{name}') failed after 2 attempts.")

                            detail_pane = page.query_selector("div.m6QEfe[tabindex='-1']")
                            if detail_pane:
                                page.evaluate("el => el.scrollBy(0, 500)", detail_pane)
                                time.sleep(0.5)

                        # Scroll feed down to reveal next virtualized DOM cards and trigger more search XHRs
                        logger.info(f"Pass complete ({new_clicks_in_pass} new clicks). Progress: {len(processed_cids)} captured, {len(failed_cids)} failed out of {expected_total} expected. Scrolling feed...")
                        page.evaluate("el => el.scrollBy(0, 1000)", results_feed)
                        time.sleep(2.5)

                        if new_clicks_in_pass == 0:
                            idle_scroll_count += 1
                            logger.info(f"No new cards clicked in pass. Idle scroll count: {idle_scroll_count}/{max_idle_scrolls}")

            browser.close()

        # Final 3-State Classification & Reporting
        final_expected_by_cid, _ = self.build_expected_items()
        expected_cids_set = set(final_expected_by_cid.keys())
        never_attempted_cids = expected_cids_set - (processed_cids | failed_cids)

        logger.info("=" * 70)
        logger.info("THREE-STATE METRICS BREAKDOWN REPORT:")
        logger.info(f"  1. Total Unique CIDs Expected: {len(expected_cids_set)}")
        logger.info(f"  2. Captured (Success):          {len(processed_cids)}")
        logger.info(f"  3. Attempted but Failed:        {len(failed_cids)}")
        logger.info(f"  4. Never Attempted:             {len(never_attempted_cids)}")
        
        if failed_cids:
            logger.info("  Attempted but Failed CIDs:")
            for fc in failed_cids:
                logger.info(f"    - CID: {fc} | Name: {final_expected_by_cid.get(fc, {}).get('name')}")

        if never_attempted_cids:
            logger.info("  Never Attempted CIDs:")
            for nac in never_attempted_cids:
                logger.info(f"    - CID: {nac} | Name: {final_expected_by_cid.get(nac, {}).get('name')}")

        logger.info("=" * 70)

        # Parse intercepted search responses
        logger.info("Parsing intercepted responses...")
        all_parsed_results: List[Dict[str, Any]] = []
        seen_cids = set()

        for page_text in self.search_pages_raw:
            cleaned_json = parse_raw_json(page_text)
            if not cleaned_json:
                continue

            raw_items = self.find_place_items(cleaned_json)
            for item in raw_items:
                parsed_item = parse_search_item(item)
                cid = parsed_item.get("cid")
                
                if cid and cid not in seen_cids:
                    seen_cids.add(cid)
                    
                    # Merge place detail if captured
                    safe_cid_key = cid.replace(":", "_")
                    if safe_cid_key in self.place_details_raw:
                        detail_raw = parse_raw_json(self.place_details_raw[safe_cid_key])
                        detail_parsed = parse_place_detail(detail_raw)
                        parsed_item["detail"] = detail_parsed
                    else:
                        parsed_item["detail"] = parse_place_detail(None)

                    all_parsed_results.append(parsed_item)

        logger.info(f"Completed scraping query '{query}'. Extracted {len(all_parsed_results)} listings.")
        return all_parsed_results


def scrape_query(query: str, max_scrolls: int = 5, click_details: bool = True) -> List[Dict[str, Any]]:
    """
    Single entry point function: scrape_query(query: str) -> list[dict]
    Runs full Playwright network interception and parser flow.
    """
    scraper = GoogleMapsScraper(raw_dir="raw")
    results = scraper.scrape(query=query, max_scrolls=max_scrolls, click_details=click_details)
    return results
