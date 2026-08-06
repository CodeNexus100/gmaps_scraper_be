import json
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gmaps_parser")

def strip_google_prefix(text: str) -> str:
    """
    Safely strips Google anti-JSON hijacking prefix )]}' or tch/ech wrappers.
    """
    if not text:
        return ""
    
    text = text.strip()
    
    # Handle tch/ech JSON wrapper object if present
    if text.startswith('{"c":'):
        try:
            wrapper = json.loads(text.split('/*""*/')[0].strip())
            if isinstance(wrapper, dict) and "d" in wrapper:
                text = wrapper["d"].strip()
        except Exception:
            pass

    if text.startswith(")]}'"):
        text = text[4:].strip()

    return text

def parse_raw_json(raw_text: str):
    """
    Parses raw response text into python data structure after stripping prefix.
    """
    cleaned = strip_google_prefix(raw_text)
    if not cleaned:
        return None
    return json.loads(cleaned)

def extract_cid(item: list) -> str:
    """Extract Feature ID / CID string (0x...:0x...)."""
    if len(item) > 10 and isinstance(item[10], str) and item[10].startswith("0x"):
        return item[10]
    
    def _find(obj):
        if isinstance(obj, str) and obj.startswith("0x") and ":" in obj:
            return obj
        if isinstance(obj, list):
            for sub in obj:
                res = _find(sub)
                if res:
                    return res
        return None
    
    cid = _find(item)
    if cid:
        return cid
    raise ValueError("CID not found in raw listing item")

def extract_name(item: list) -> str:
    """Extract Business Name."""
    if len(item) > 11 and isinstance(item[11], str) and len(item[11]) > 1:
        return item[11]
    
    def _scan(obj):
        if isinstance(obj, str) and len(obj) > 2 and " " in obj and not obj.startswith("http") and not obj.startswith("0x"):
            if not any(kw in obj.lower() for kw in ["reviews", "market", "delhi 110", "floor", "road"]):
                return obj
        if isinstance(obj, list):
            for sub in obj:
                res = _scan(sub)
                if res:
                    return res
        return None

    res = _scan(item)
    if res:
        return res
    raise ValueError("Business name not found in item")

def extract_rating(item: list) -> float:
    """Extract rating float (e.g. 4.7)."""
    if len(item) > 4 and isinstance(item[4], list):
        def _scan_rating(obj):
            if isinstance(obj, float) and 1.0 <= obj <= 5.0:
                return obj
            if isinstance(obj, list):
                for sub in obj:
                    res = _scan_rating(sub)
                    if res is not None:
                        return res
            return None
        res = _scan_rating(item[4])
        if res is not None:
            return round(res, 2)

    def _scan(obj):
        if isinstance(obj, float) and 1.0 <= obj <= 5.0:
            return obj
        if isinstance(obj, list):
            for elem in obj:
                res = _scan(elem)
                if res is not None:
                    return res
        return None

    res = _scan(item)
    if res is not None:
        return round(res, 2)
    return None

def extract_review_count(item: list) -> int:
    """Extract total review count int (e.g. 879)."""
    if len(item) > 4 and isinstance(item[4], list):
        def _scan_reviews(obj):
            if isinstance(obj, int) and obj > 0:
                return obj
            if isinstance(obj, str) and "reviews" in obj.lower():
                match = re.search(r'([\d,]+)\s+reviews', obj, re.IGNORECASE)
                if match:
                    return int(match.group(1).replace(",", ""))
            if isinstance(obj, list):
                for sub in obj:
                    res = _scan_reviews(sub)
                    if res is not None:
                        return res
            return None
        res = _scan_reviews(item[4])
        if res is not None:
            return res

    def _scan(obj):
        if isinstance(obj, str) and "reviews" in obj.lower():
            match = re.search(r'([\d,]+)\s+reviews', obj, re.IGNORECASE)
            if match:
                return int(match.group(1).replace(",", ""))
        if isinstance(obj, list):
            for elem in obj:
                res = _scan(elem)
                if res is not None:
                    return res
        return None

    res = _scan(item)
    if res is not None:
        return res
    return 0

def extract_address(item: list) -> str:
    """Extract address string."""
    if len(item) > 2 and isinstance(item[2], list) and item[2]:
        addr_parts = [str(x) for x in item[2] if isinstance(x, str)]
        if addr_parts:
            return ", ".join(addr_parts)

    def _scan(obj):
        if isinstance(obj, list) and len(obj) >= 2:
            if all(isinstance(x, str) for x in obj):
                if any(x.isdigit() for x in "".join(obj)) or any(loc in "".join(obj).lower() for loc in ["delhi", "road", "street", "market", "nagar", "place", "floor"]):
                    return ", ".join(obj)
        if isinstance(obj, list):
            for elem in obj:
                res = _scan(elem)
                if res:
                    return res
        return None

    res = _scan(item)
    return res or ""

def extract_category(item: list) -> str:
    """Extract business category string (e.g. Travel agency)."""
    if len(item) > 13 and isinstance(item[13], list) and item[13]:
        cats = [x for x in item[13] if isinstance(x, str)]
        if cats:
            return cats[0]

    categories = ["travel agency", "tour operator", "car rental agency", "tourist information center", "hotel", "agency"]
    def _scan(obj):
        if isinstance(obj, str) and not obj.startswith("http"):
            if any(c in obj.lower() for c in categories):
                return obj
        elif isinstance(obj, list):
            for elem in obj:
                res = _scan(elem)
                if res:
                    return res
        return None

    res = _scan(item)
    return res or ""

def extract_coordinates(item: list) -> tuple:
    """Extract (lat, lng) tuple."""
    if len(item) > 9 and isinstance(item[9], list) and len(item[9]) >= 4:
        if isinstance(item[9][2], (int, float)) and isinstance(item[9][3], (int, float)):
            return float(item[9][2]), float(item[9][3])

    def _scan(obj):
        if isinstance(obj, list) and len(obj) == 2:
            if isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                lat, lng = float(obj[0]), float(obj[1])
                if -90 <= lat <= 90 and -180 <= lng <= 180 and lat != 0 and lng != 0:
                    return lat, lng
        if isinstance(obj, list):
            for elem in obj:
                res = _scan(elem)
                if res:
                    return res
        return None

    res = _scan(item)
    return res or (None, None)

def parse_search_item(raw_item: list) -> dict:
    """
    Parses a single raw search result listing item into a structured dictionary.
    Each field extraction is individually wrapped to handle missing/shifted indices defensively.
    """
    result = {
        "name": None,
        "cid": None,
        "address": None,
        "rating": None,
        "review_count": None,
        "category": None,
        "lat": None,
        "lng": None
    }

    try:
        result["cid"] = extract_cid(raw_item)
    except Exception as e:
        logger.warning(f"Failed to extract 'cid': {e}. Snippet: {str(raw_item)[:150]}")

    try:
        result["name"] = extract_name(raw_item)
    except Exception as e:
        logger.warning(f"Failed to extract 'name': {e}. Snippet: {str(raw_item)[:150]}")

    try:
        result["address"] = extract_address(raw_item)
    except Exception as e:
        logger.warning(f"Failed to extract 'address': {e}. Snippet: {str(raw_item)[:150]}")

    try:
        result["rating"] = extract_rating(raw_item)
    except Exception as e:
        logger.warning(f"Failed to extract 'rating': {e}")

    try:
        result["review_count"] = extract_review_count(raw_item)
    except Exception as e:
        logger.warning(f"Failed to extract 'review_count': {e}")

    try:
        result["category"] = extract_category(raw_item)
    except Exception as e:
        logger.warning(f"Failed to extract 'category': {e}")

    try:
        lat, lng = extract_coordinates(raw_item)
        result["lat"] = lat
        result["lng"] = lng
    except Exception as e:
        logger.warning(f"Failed to extract 'coordinates': {e}")

    return result

def parse_place_detail(raw_json) -> dict:
    """
    Parses raw place detail RPC response into structured dictionary.
    Fields: phone, website, full_address, hours.
    Wrapped defensively per field.
    """
    result = {
        "phone": None,
        "website": None,
        "full_address": None,
        "hours": None
    }

    if not raw_json:
        return result

    # Extract Phone Number
    try:
        def _find_phone(obj):
            if isinstance(obj, str):
                if obj.startswith("tel:"):
                    return obj.replace("tel:", "").strip()
                if not obj.isdigit() or len(obj) <= 12:
                    if "+91" in obj or re.search(r'\b0\d{2,4}[\s\-]?\d{6,8}\b', obj) or re.search(r'\b\+?91[\s\-]?\d{10}\b', obj):
                        if not obj.startswith("0x") and "http" not in obj:
                            return obj.strip()
            if isinstance(obj, list):
                for item in obj:
                    res = _find_phone(item)
                    if res:
                        return res
            return None
        
        result["phone"] = _find_phone(raw_json)
    except Exception as e:
        logger.warning(f"Failed to extract 'phone' from place detail: {e}")

    # Extract Website
    try:
        def _find_website(obj):
            if isinstance(obj, str) and obj.startswith("http") and not any(x in obj for x in ["google.com", "ggpht", "gstatic", "googleusercontent"]):
                return obj
            if isinstance(obj, list):
                for item in obj:
                    res = _find_website(item)
                    if res:
                        return res
            return None

        result["website"] = _find_website(raw_json)
    except Exception as e:
        logger.warning(f"Failed to extract 'website' from place detail: {e}")

    # Extract Full Address
    try:
        def _find_address(obj):
            if isinstance(obj, list) and len(obj) >= 2 and all(isinstance(x, str) for x in obj):
                if any(x.isdigit() for x in "".join(obj)) or any(l in "".join(obj).lower() for l in ["delhi", "road", "street", "market", "nagar", "place", "floor", "110"]):
                    return ", ".join(obj)
            if isinstance(obj, list):
                for item in obj:
                    res = _find_address(item)
                    if res:
                        return res
            return None

        result["full_address"] = _find_address(raw_json)
    except Exception as e:
        logger.warning(f"Failed to extract 'full_address' from place detail: {e}")

    # Extract Weekly Hours Schedule
    try:
        days_of_week = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        
        def _find_hours_schedule(obj):
            # Locate 7-day array structure
            if isinstance(obj, list) and len(obj) == 7:
                if all(isinstance(x, list) and len(x) > 0 and isinstance(x[0], str) and x[0] in days_of_week for x in obj):
                    weekly_schedule = {}
                    for day_item in obj:
                        day_name = day_item[0]
                        hours_str = "Closed"
                        if len(day_item) > 3 and isinstance(day_item[3], list) and day_item[3]:
                            first_slot = day_item[3][0]
                            if isinstance(first_slot, list) and len(first_slot) > 0:
                                hours_str = str(first_slot[0]).replace("\u202f", " ").replace("\u2013", "-")
                        weekly_schedule[day_name] = hours_str
                    return weekly_schedule

            if isinstance(obj, list):
                for item in obj:
                    res = _find_hours_schedule(item)
                    if res:
                        return res
            elif isinstance(obj, dict):
                for k, v in obj.items():
                    res = _find_hours_schedule(v)
                    if res:
                        return res
            return None

        result["hours"] = _find_hours_schedule(raw_json)
    except Exception as e:
        logger.warning(f"Failed to extract 'hours' from place detail: {e}")

    return result
