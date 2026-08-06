import os
import json
import time
import re
import random
import logging
import functools
from typing import List, Dict, Any, Optional, Callable
from playwright.sync_api import sync_playwright, Page, Response

from parser import parse_raw_json, parse_search_item, parse_place_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gmaps_scraper")

class BotChallengeDetectedException(Exception):
    """Raised when CAPTCHA, consent redirect, or 429/403 bot block is detected."""
    pass

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
    {"width": 1600, "height": 900}
]

def get_random_user_agent() -> str:
    return random.choice(USER_AGENTS)

def get_random_viewport() -> dict:
    return random.choice(VIEWPORTS)

def jitter_delay(base_seconds: float, variance: float = 0.4):
    """Add human-like randomized delay around a base interval."""
    actual_delay = max(0.2, base_seconds + random.uniform(-variance, variance))
    time.sleep(actual_delay)

def apply_stealth_scripts(page: Page):
    """Inject Playwright stealth scripts to patch automation indicators."""
    stealth_code = """
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Google Inc. (NVIDIA)';
            if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)';
            return getParameter.apply(this, arguments);
        };
    """
    page.add_init_script(stealth_code)

def retry_with_backoff(max_retries: int = 3, initial_delay: float = 2.0, backoff_factor: float = 2.0):
    """Higher-level retry decorator with exponential backoff and jitter for recoverable errors."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            last_exception = None
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except BotChallengeDetectedException as e:
                    logger.error(f"[BOT BLOCKED] Non-retryable bot challenge: {e}")
                    raise e
                except Exception as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"[RUN RETRY EXHAUSTED] Run failed after {max_retries} attempts: {e}")
                        raise e
                    
                    jittered_delay = delay + random.uniform(0.1, 1.0)
                    logger.warning(f"[RUN RETRY #{attempt}/{max_retries}] Recoverable run error: {e}. Backing off {jittered_delay:.2f}s...")
                    time.sleep(jittered_delay)
                    delay *= backoff_factor
            if last_exception:
                raise last_exception
        return wrapper
    return decorator


class GoogleMapsScraper:
    def __init__(self, raw_dir: str = "raw", proxy: Optional[Dict[str, str]] = None, progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.raw_dir = raw_dir
        self.proxy = proxy
        self.progress_callback = progress_callback
        os.makedirs(self.raw_dir, exist_ok=True)
        self.search_pages_raw: List[str] = []
        self.place_details_raw: Dict[str, str] = {} # cid -> raw_json_text
        self.place_reviews_raw: Dict[str, str] = {} # cid -> raw_json_text
        self.bot_challenges_count: int = 0
        self.click_loop_started: bool = False

    def emit_progress(self, data: Dict[str, Any]):
        """Emit live progress event to registered callback if present."""
        if self.progress_callback:
            try:
                self.progress_callback(data)
            except Exception as e:
                logger.warning(f"Error in progress callback: {e}")

    def clear_raw_dir(self):
        """Clear JSON files in raw_dir directory at the start of a query run."""
        if os.path.exists(self.raw_dir):
            for f in os.listdir(self.raw_dir):
                if f.endswith(".json"):
                    try:
                        os.remove(os.path.join(self.raw_dir, f))
                    except Exception as e:
                        logger.warning(f"Could not remove old raw file {f}: {e}")
            logger.info(f"Cleared old raw JSON files in directory: '{self.raw_dir}'")

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

    def check_bot_challenges(self, page: Page, response: Optional[Response] = None):
        """Detect CAPTCHA, unexpected consent redirects, or 429/403 block signals."""
        current_url = page.url
        
        if response and response.status in [429, 403]:
            if any(rpc in response.url for rpc in ["/maps/rpc/search", "/maps/rpc/place", "/maps/preview/place"]):
                shot_path = os.path.join(self.raw_dir, f"bot_challenge_http_{response.status}.png")
                page.screenshot(path=shot_path)
                self.bot_challenges_count += 1
                msg = f"HTTP {response.status} Bot Block on {response.url[:70]}. Saved screenshot to {shot_path}"
                self.emit_progress({"phase": "blocked", "reason": msg, "status": "blocked"})
                raise BotChallengeDetectedException(msg)

        if "google.com/sorry/index" in current_url:
            shot_path = os.path.join(self.raw_dir, "bot_challenge_sorry.png")
            page.screenshot(path=shot_path)
            self.bot_challenges_count += 1
            msg = f"Redirected to Google Sorry/CAPTCHA page. Saved screenshot to {shot_path}"
            self.emit_progress({"phase": "blocked", "reason": msg, "status": "blocked"})
            raise BotChallengeDetectedException(msg)

        if page.query_selector("iframe[src*='recaptcha'], iframe[src*='captcha'], #captcha-form"):
            shot_path = os.path.join(self.raw_dir, "bot_challenge_captcha.png")
            page.screenshot(path=shot_path)
            self.bot_challenges_count += 1
            msg = f"ReCAPTCHA iframe detected in DOM. Saved screenshot to {shot_path}"
            self.emit_progress({"phase": "blocked", "reason": msg, "status": "blocked"})
            raise BotChallengeDetectedException(msg)

    def handle_response(self, response: Response):
        url = response.url
        try:
            if response.status in [429, 403]:
                logger.warning(f"[BOT BLOCKED STATUS] {response.status} on {url[:80]}")

            # Intercept search responses
            if "/maps/rpc/search" in url or ("search?" in url and "tbm=map" in url):
                text = response.text()
                
                if self.click_loop_started:
                    logger.warning(
                        f"[RACE WARNING] Search XHR response arrived AFTER click loop started! URL: {url[:80]}..."
                    )

                logger.info(f"Intercepted search response ({len(text)} bytes): {url[:80]}...")
                self.search_pages_raw.append(text)
                
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

    def is_ad_card(self, card) -> bool:
        """Check if DOM card is a sponsored ad listing."""
        try:
            inner_text = card.inner_text() or ""
            html = card.inner_html() or ""
            if "Sponsored" in inner_text or "Ad ·" in inner_text or "Ad " in inner_text:
                return True
            if "aclk" in html or "googleadservices" in html:
                return True
            if card.query_selector(".ub7k4e, [aria-label*='Sponsored'], [aria-label*='Ad']"):
                return True
        except Exception:
            pass
        return False

    def match_card_to_item(self, card, expected_items_by_cid: Dict[str, dict], expected_items_by_name: Dict[str, dict]):
        """Match a DOM card element to an expected search listing by CID or Name."""
        try:
            link = card.query_selector("a.hfA20e") or card.query_selector("a[href*='/maps/place/']") or card
            href = link.get_attribute("href") if link else ""
            aria_label = link.get_attribute("aria-label") if link else ""
            
            title_elem = card.query_selector("div.fontHeadlineSmall")
            title_text = title_elem.inner_text().strip() if title_elem else ""

            if href:
                match = re.search(r'0x[0-9a-fA-F]+:0x[0-9a-fA-F]+', href) or re.search(r'0x[0-9a-fA-F]+%3A0x[0-9a-fA-F]+', href)
                if match:
                    cid = match.group(0).replace("%3A", ":")
                    if cid in expected_items_by_cid:
                        return expected_items_by_cid[cid]

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
        """Build expected items map from all search XHR responses captured so far."""
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
        self.clear_raw_dir()
        self.search_pages_raw.clear()
        self.place_details_raw.clear()
        self.place_reviews_raw.clear()
        self.click_loop_started = False

        expected_by_cid: Dict[str, dict] = {}
        expected_by_name: Dict[str, dict] = {}

        ua = get_random_user_agent()
        vp = get_random_viewport()
        logger.info(f"Starting browser session. User-Agent: {ua[:50]}..., Viewport: {vp['width']}x{vp['height']}")
        self.emit_progress({"phase": "init", "status": "starting", "query": query})

        processed_cids: set = set()
        failed_cids: set = set()
        skipped_ad_cids: set = set()

        with sync_playwright() as p:
            launch_args = {"headless": True}
            if self.proxy:
                launch_args["proxy"] = self.proxy
                logger.info(f"Using pluggable proxy configuration: {self.proxy}")

            browser = p.chromium.launch(**launch_args)
            context = browser.new_context(viewport=vp, user_agent=ua)
            page = context.new_page()

            apply_stealth_scripts(page)
            page.on("response", self.handle_response)

            logger.info("Navigating to Google Maps...")
            page.goto("https://www.google.com/maps?hl=en", wait_until="load", timeout=60000)
            jitter_delay(3.0, variance=0.5)

            self.check_bot_challenges(page)

            for frame in page.frames:
                for btn_text in ["Accept all", "Reject all", "I agree", "Agree"]:
                    try:
                        btn = frame.query_selector(f"button:has-text('{btn_text}')")
                        if btn and btn.is_visible():
                            logger.info(f"Clicking consent button: '{btn_text}'")
                            btn.click()
                            jitter_delay(2.0, variance=0.4)
                            break
                    except Exception:
                        pass

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
            jitter_delay(0.5, variance=0.2)
            page.keyboard.press("Enter")
            jitter_delay(4.5, variance=0.8)

            self.check_bot_challenges(page)

            results_feed = None
            for feed_sel in ["div[role='feed']", "div.m6QEfe", "div[aria-label*='Results']"]:
                try:
                    elem = page.wait_for_selector(feed_sel, timeout=10000)
                    if elem:
                        results_feed = elem
                        break
                except Exception:
                    pass

            if results_feed:
                logger.info("Starting Search Discovery Phase (scrolling until search XHR responses settle)...")
                self.emit_progress({"phase": "search", "status": "discovering", "search_pages": 0, "unique_cids_discovered": 0})
                
                idle_search_scrolls = 0
                max_idle_search_scrolls = 4
                last_search_pages_count = 0
                last_cids_count = 0

                while idle_search_scrolls < max_idle_search_scrolls:
                    page.evaluate("el => el.scrollBy(0, 1000)", results_feed)
                    jitter_delay(2.5, variance=0.6)
                    
                    self.check_bot_challenges(page)

                    current_search_pages_count = len(self.search_pages_raw)
                    current_cids_map, _ = self.build_expected_items()
                    current_cids_count = len(current_cids_map)

                    if current_search_pages_count > last_search_pages_count or current_cids_count > last_cids_count:
                        logger.info(
                            f"[SEARCH DISCOVERY] Captured new search data: {current_search_pages_count} search pages, "
                            f"{current_cids_count} unique CIDs discovered."
                        )
                        idle_search_scrolls = 0
                        last_search_pages_count = current_search_pages_count
                        last_cids_count = current_cids_count
                        
                        self.emit_progress({
                            "phase": "search",
                            "status": "discovering",
                            "search_pages": current_search_pages_count,
                            "unique_cids_discovered": current_cids_count
                        })
                    else:
                        idle_search_scrolls += 1
                        logger.info(f"[SEARCH DISCOVERY] No new search XHR. Idle search scroll pass {idle_search_scrolls}/{max_idle_search_scrolls}")

                    end_elem = page.query_selector("span.HvvBDc, div.HvvBDc")
                    if end_elem and end_elem.is_visible():
                        logger.info("[SEARCH DISCOVERY] Reached end of list in DOM feed. Completing search phase.")
                        break

            # Build expected items map safely
            expected_by_cid, expected_by_name = self.build_expected_items()
            expected_total = len(expected_by_cid)
            logger.info(f"SEARCH PHASE COMPLETE! Confirmed {expected_total} total unique CIDs across {len(self.search_pages_raw)} search pages.")
            
            self.emit_progress({
                "phase": "search_complete",
                "status": "complete",
                "search_pages": len(self.search_pages_raw),
                "total_expected_cids": expected_total
            })

            if click_details and expected_total > 0 and results_feed:
                logger.info("Starting DOM Virtualization Interleaved Click & Scroll Loop...")
                self.click_loop_started = True

                idle_scroll_count = 0
                max_idle_scrolls = 8

                while True:
                    self.check_bot_challenges(page)

                    never_attempted_count = expected_total - (len(processed_cids) + len(failed_cids) + len(skipped_ad_cids))

                    self.emit_progress({
                        "phase": "click",
                        "status": "in_progress",
                        "expected": expected_total,
                        "captured": len(processed_cids),
                        "skipped_ad": len(skipped_ad_cids),
                        "failed": len(failed_cids),
                        "never_attempted": max(0, never_attempted_count)
                    })

                    if len(processed_cids) + len(failed_cids) + len(skipped_ad_cids) >= expected_total:
                        logger.info(
                            f"All {expected_total} expected CIDs accounted for "
                            f"({len(processed_cids)} captured, {len(skipped_ad_cids)} skipped ads, {len(failed_cids)} failed). Exiting click loop."
                        )
                        break

                    if idle_scroll_count >= max_idle_scrolls:
                        logger.warning(
                            f"[SAFETY EXIT] Safety exit triggered after {max_idle_scrolls} consecutive idle scrolls.\n"
                            f"  Target CIDs Expected: {expected_total}\n"
                            f"  Captured Succeeded:  {len(processed_cids)}\n"
                            f"  Skipped Ads:         {len(skipped_ad_cids)}\n"
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

                        if cid in processed_cids or cid in failed_cids or cid in skipped_ad_cids:
                            continue

                        if self.is_ad_card(card):
                            skipped_ad_cids.add(cid)
                            logger.info(f"[SKIPPED AD] CID {cid} ('{name[:35]}') - Detected sponsored ad listing, skipping click.")
                            continue

                        new_clicks_in_pass += 1
                        idle_scroll_count = 0
                        
                        click_target = card.query_selector("a.hfA20e") or card.query_selector("a[href*='/maps/place/']") or card
                        
                        try:
                            click_target.scroll_into_view_if_needed(timeout=2000)
                            jitter_delay(0.3, variance=0.1)
                        except Exception:
                            pass

                        def is_place_rpc(resp):
                            return ("/maps/rpc/place" in resp.url or "/maps/preview/place" in resp.url) and resp.status == 200

                        click_success = False
                        for attempt in [1, 2]:
                            try:
                                logger.info(f"[CLICK ATTEMPT {attempt}] CID {cid} ('{name[:35]}')...")
                                with page.expect_response(is_place_rpc, timeout=5000):
                                    click_target.click()
                                click_success = True
                                logger.info(f"[CLICK SUCCESS] Intercepted place detail for CID {cid}")
                                break
                            except Exception as te:
                                logger.warning(f"[CLICK TIMEOUT] Attempt {attempt} timed out for CID {cid}. Error: {te}")
                                jitter_delay(1.0, variance=0.3)

                        if click_success:
                            processed_cids.add(cid)
                        else:
                            failed_cids.add(cid)
                            logger.error(f"[CLICK PERMANENT FAIL] CID {cid} ('{name}') failed after 2 attempts.")

                        detail_pane = page.query_selector("div.m6QEfe[tabindex='-1']")
                        if detail_pane:
                            page.evaluate("el => el.scrollBy(0, 500)", detail_pane)
                            jitter_delay(0.5, variance=0.2)

                    logger.info(
                        f"Pass complete ({new_clicks_in_pass} new clicks). Progress: "
                        f"{len(processed_cids)} captured, {len(skipped_ad_cids)} skipped ads, {len(failed_cids)} failed out of {expected_total} expected. Scrolling feed..."
                    )
                    page.evaluate("el => el.scrollBy(0, 1000)", results_feed)
                    jitter_delay(2.5, variance=0.6)

                    if new_clicks_in_pass == 0:
                        idle_scroll_count += 1
                        logger.info(f"No new cards clicked in pass. Idle scroll count: {idle_scroll_count}/{max_idle_scrolls}")

            browser.close()

        # Final 4-State Classification & Extended Resilience Report
        expected_cids_set = set(expected_by_cid.keys())
        never_attempted_cids = expected_cids_set - (processed_cids | failed_cids | skipped_ad_cids)

        if click_details and len(expected_cids_set) > 0:
            if not self.click_loop_started or len(never_attempted_cids) > 0:
                raise RuntimeError(f"Details incomplete: Click-loop bypassed or failed to process all expected CIDs. "
                                   f"Expected: {len(expected_cids_set)}, Never Attempted: {len(never_attempted_cids)}")

        metrics_summary = {
            "query": query,
            "raw_dir": self.raw_dir,
            "bot_challenges": self.bot_challenges_count,
            "expected": len(expected_cids_set),
            "captured": len(processed_cids),
            "failed": len(failed_cids),
            "skipped_ad": len(skipped_ad_cids),
            "never_attempted": len(never_attempted_cids)
        }

        logger.info("=" * 70)
        logger.info("EXTENDED RESILIENCE & STATE METRICS REPORT:")
        logger.info(f"  Query Scraped:                 {query}")
        logger.info(f"  Scoped Raw Directory:          {self.raw_dir}")
        logger.info(f"  Pluggable Proxy Configured:    {'YES' if self.proxy else 'NO'}")
        logger.info(f"  Bot Challenge Detections:       {self.bot_challenges_count}")
        logger.info(f"  Total Unique CIDs Expected:     {len(expected_cids_set)}")
        logger.info(f"  1. Captured (Success):          {len(processed_cids)}")
        logger.info(f"  2. Attempted but Failed:        {len(failed_cids)}")
        logger.info(f"  3. Skipped Ad (Pre-click):      {len(skipped_ad_cids)}")
        logger.info(f"  4. Never Attempted:             {len(never_attempted_cids)}")
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
                    
                    # Requirement 2 Option (a): Exclude skipped ad listings from final results payload
                    if cid in skipped_ad_cids:
                        logger.info(f"Excluding skipped ad CID {cid} ('{parsed_item.get('name')}') from final results payload.")
                        continue

                    safe_cid_key = cid.replace(":", "_")
                    if safe_cid_key in self.place_details_raw:
                        detail_raw = parse_raw_json(self.place_details_raw[safe_cid_key])
                        detail_parsed = parse_place_detail(detail_raw)
                        parsed_item["detail"] = detail_parsed
                    else:
                        parsed_item["detail"] = parse_place_detail(None)

                    all_parsed_results.append(parsed_item)

        logger.info(f"Completed scraping query '{query}'. Extracted {len(all_parsed_results)} organic listings (excluded {len(skipped_ad_cids)} ads).")
        self.emit_progress({
            "phase": "complete",
            "status": "completed",
            "metrics": metrics_summary,
            "total_listings": len(all_parsed_results)
        })
        return all_parsed_results


@retry_with_backoff(max_retries=3, initial_delay=2.0, backoff_factor=2.0)
def scrape_query(
    query: str,
    max_scrolls: int = 5,
    click_details: bool = True,
    proxy: Optional[Dict[str, str]] = None,
    raw_dir: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> List[Dict[str, Any]]:
    """
    Single entry point function: scrape_query(query: str) -> list[dict]
    Includes pre-click Search Discovery Phase, exponential backoff retries, stealth session randomization,
    bot challenge detection, pluggable proxy support, progress streaming callbacks, and per-query scoped raw directory.
    """
    if not raw_dir:
        slug = re.sub(r'[^a-zA-Z0-9]+', '_', query).strip('_').lower()
        raw_dir = os.path.join("raw", f"query_{slug}")

    scraper = GoogleMapsScraper(raw_dir=raw_dir, proxy=proxy, progress_callback=progress_callback)
    results = scraper.scrape(query=query, max_scrolls=max_scrolls, click_details=click_details)
    return results
