"""Product taxonomy + synonym expansion.

Deal channels write in shorthand ("kurti", "trimmer", "PB"), while users search
in natural words ("women kurta", "beard trimmer", "power bank"). This module is
the bridge: every category owns a list of surface forms, and a search term is
expanded to all sibling forms before matching.

Extend CATEGORIES freely — everything downstream is derived from it.
"""
from __future__ import annotations

import re
from typing import Dict, List, Set

# category -> subcategory -> surface forms seen in channel posts / user queries
CATEGORIES: Dict[str, Dict[str, List[str]]] = {
    "Electronics": {
        "Charger": ["charger", "fast charger", "charging adapter", "adapter", "wall charger", "gan charger", "type c charger", "18w", "20w", "33w", "65w"],
        "Cable": ["cable", "data cable", "usb cable", "type c cable", "lightning cable", "braided cable", "otg"],
        "Power Bank": ["power bank", "powerbank", "pb", "10000mah", "20000mah", "portable charger"],
        "Earbuds": ["earbuds", "tws", "airdopes", "buds", "true wireless", "earphone", "earphones", "neckband"],
        "Headphones": ["headphone", "headphones", "headset", "over ear", "on ear"],
        "Smartwatch": ["smartwatch", "smart watch", "fitness band", "smartband", "watch series"],
        "Mobile": ["mobile", "smartphone", "phone", "5g phone", "redmi", "realme", "iphone", "samsung galaxy"],
        "Laptop": ["laptop", "notebook", "macbook", "chromebook", "thin and light"],
        "Speaker": ["speaker", "bluetooth speaker", "soundbar", "party speaker"],
        "Storage": ["pendrive", "pen drive", "memory card", "sd card", "ssd", "hard disk", "hdd", "flash drive"],
        "Accessories": ["mouse", "keyboard", "mousepad", "webcam", "laptop bag", "mobile cover", "back cover", "screen guard", "tempered glass", "stand", "tripod"],
    },
    "Women Fashion": {
        "Kurta": ["kurta", "kurti", "kurtis", "kurtas", "kurta set", "anarkali", "straight kurta"],
        "Dress": ["dress", "dresses", "maxi", "midi", "gown", "bodycon", "one piece", "frock"],
        "Saree": ["saree", "sari", "sarees", "lehenga", "choli", "dupatta"],
        "Top": ["top", "tops", "tshirt women", "blouse", "crop top", "tunic", "shirt women"],
        "Bottomwear": ["legging", "leggings", "palazzo", "jeggings", "women jeans", "skirt", "trousers women", "salwar", "patiala"],
        "Ethnic Set": ["suit set", "salwar suit", "ethnic set", "co ord set", "coord set"],
        "Innerwear": ["bra", "lingerie", "nightwear", "nighty", "camisole", "innerwear women"],
    },
    "Men Fashion": {
        "Shirt": ["shirt", "shirts", "formal shirt", "casual shirt", "check shirt"],
        "T-Shirt": ["tshirt", "t shirt", "t-shirt", "tee", "polo", "oversized tshirt"],
        "Bottomwear": ["jeans", "trouser", "trousers", "chinos", "track pant", "trackpant", "joggers", "shorts", "cargo"],
        "Ethnic": ["kurta men", "sherwani", "nehru jacket", "dhoti"],
        "Innerwear": ["vest", "brief", "boxer", "trunk", "innerwear men"],
        "Outerwear": ["jacket", "hoodie", "sweatshirt", "sweater", "blazer"],
    },
    "Footwear": {
        "Sports Shoes": ["sports shoes", "running shoes", "sneakers", "sneaker", "trainers"],
        "Casual Shoes": ["casual shoes", "loafers", "canvas shoes"],
        "Sandals": ["sandal", "sandals", "floaters", "slippers", "flip flop", "chappal", "crocs"],
        "Formal Shoes": ["formal shoes", "derby", "oxford shoes"],
        "Heels": ["heels", "wedges", "bellies", "flats women"],
    },
    "Home & Kitchen": {
        "Cookware": ["kadai", "cookware", "frying pan", "tawa", "pressure cooker", "non stick", "nonstick", "cook n serve"],
        "Appliances": ["mixer grinder", "juicer", "air fryer", "induction", "toaster", "kettle", "sandwich maker", "chopper", "blender"],
        "Storage": ["container", "containers", "lunch box", "tiffin", "bottle", "water bottle", "casserole", "jar"],
        "Furnishing": ["bedsheet", "bed sheet", "curtain", "curtains", "pillow", "blanket", "mattress", "doormat", "carpet"],
        "Cleaning": ["mop", "broom", "vacuum", "detergent", "cleaner", "dustbin"],
    },
    "Appliances": {
        "Large": ["washing machine", "refrigerator", "fridge", "air conditioner", "ac ", "television", "smart tv", " tv ", "microwave", "dishwasher", "geyser", "water heater"],
        "Cooling": ["fan", "cooler", "air cooler", "ceiling fan", "table fan"],
    },
    "Beauty": {
        "Skincare": ["face wash", "facewash", "moisturizer", "sunscreen", "serum", "cream", "lotion", "face pack"],
        "Haircare": ["shampoo", "conditioner", "hair oil", "hair color", "hair dryer", "straightener"],
        "Makeup": ["lipstick", "kajal", "foundation", "compact", "eyeliner", "mascara", "nail polish"],
        "Grooming": ["trimmer", "shaver", "razor", "beard", "epilator", "grooming kit"],
        "Fragrance": ["perfume", "deodorant", "deo", "body spray", "attar", "cologne"],
    },
    "Grocery": {
        "Staples": ["atta", "rice", "dal", "oil", "sugar", "salt", "masala", "ghee"],
        "Snacks": ["chocolate", "biscuit", "namkeen", "chips", "dry fruits", "almond", "cashew", "coffee", "tea"],
        "Health": ["protein", "whey", "supplement", "multivitamin", "chyawanprash", "honey"],
    },
    "Baby & Kids": {
        "Baby Care": ["diaper", "diapers", "wipes", "baby lotion", "baby soap", "feeding bottle"],
        "Kids Wear": ["kids", "boys", "girls", "infant", "toddler", "kids tshirt", "kids dress"],
        "Toys": ["toy", "toys", "lego", "puzzle", "board game", "soft toy", "remote car"],
    },
    "Sports & Fitness": {
        "Fitness": ["dumbbell", "yoga mat", "resistance band", "skipping rope", "treadmill", "gym"],
        "Outdoor": ["cricket bat", "football", "badminton", "racket", "cycle", "bicycle", "helmet"],
    },
    "Bags & Luggage": {
        "Bags": ["backpack", "bag", "handbag", "sling bag", "wallet", "purse", "clutch"],
        "Luggage": ["trolley", "suitcase", "luggage", "duffle", "travel bag"],
    },
    "Books & Stationery": {
        "Stationery": ["pen", "notebook copy", "diary", "stationery", "marker", "geometry box", "colour pencil"],
        "Books": ["book", "novel", "guide", "textbook"],
    },
}

# Brands worth recognising explicitly — helps both search and dedup.
KNOWN_BRANDS: List[str] = [
    "boat", "noise", "realme", "redmi", "mi", "xiaomi", "samsung", "oneplus", "oppo", "vivo",
    "apple", "iphone", "jbl", "sony", "philips", "syska", "ambrane", "portronics", "zebronics",
    "hp", "dell", "lenovo", "asus", "acer", "msi", "logitech", "tp-link", "anker", "spigen",
    "nike", "adidas", "puma", "campus", "sparx", "bata", "woodland", "red tape", "skechers",
    "levis", "allen solly", "van heusen", "peter england", "roadster", "hrx", "wrogn", "here&now",
    "biba", "w for woman", "aurelia", "libas", "anouk", "sangria", "vishudh",
    "prestige", "pigeon", "milton", "cello", "borosil", "hawkins", "butterfly", "bajaj", "havells",
    "usha", "crompton", "orient", "lg", "whirlpool", "godrej", "voltas", "haier", "tcl",
    "nivea", "lakme", "maybelline", "loreal", "l'oreal", "mamaearth", "wow", "plum", "minimalist",
    "beardo", "the man company", "park avenue", "gillette", "braun", "vega",
    "amul", "tata", "nestle", "cadbury", "britannia", "himalaya", "dabur", "patanjali",
    "pampers", "huggies", "mamypoko", "johnson", "chicco",
    "american tourister", "skybags", "safari", "wildcraft", "vip",
]

STORE_DOMAINS = {
    "amazon": ["amazon.in", "amazon.com", "amzn.to", "amzn.in", "amzn.eu"],
    "flipkart": ["flipkart.com", "fkrt.it", "fkrt.cc", "dl.flipkart.com", "fkrt.co"],
    "myntra": ["myntra.com", "myntr.it"],
    "ajio": ["ajio.com", "ajiio.in"],
    "meesho": ["meesho.com", "meesho.io"],
    "jiomart": ["jiomart.com"],
    "tatacliq": ["tatacliq.com", "tcl.sn"],
    "nykaa": ["nykaa.com", "nykd.in"],
    "croma": ["croma.com"],
    "reliancedigital": ["reliancedigital.in"],
    "snapdeal": ["snapdeal.com"],
    "shopsy": ["shopsy.in"],
    "firstcry": ["firstcry.com"],
    "bigbasket": ["bigbasket.com"],
    "zepto": ["zepto.co", "zeptonow.com"],
    "blinkit": ["blinkit.com"],
    "swiggy": ["swiggy.com"],
    "pharmeasy": ["pharmeasy.in"],
    "boat": ["boat-lifestyle.com"],
    "puma": ["puma.com"],
    "adidas": ["adidas.co.in"],
}

# Junk / non-deal chatter that should never become a deal row.
SPAM_PATTERNS = [
    r"\bjoin\s+(?:our|the)?\s*(?:channel|group)\b",
    r"\bshare\s+(?:and|&)\s+support\b",
    r"\bgiveaway\b",
    r"\brefer\s*(?:and|&)\s*earn\b",
    r"\bpaid\s+promotion\b",
    r"\badmin\b.*\bdm\b",
    r"\bsubscribe\b.*\byoutube\b",
    r"\btelegram\.me/joinchat\b",
    r"\bhow\s+to\s+order\b",
]

_SPAM_RE = re.compile("|".join(SPAM_PATTERNS), re.IGNORECASE)


def _build_indexes():
    term_to_cat: Dict[str, tuple] = {}
    group_of_term: Dict[str, Set[str]] = {}
    for category, subs in CATEGORIES.items():
        for sub, terms in subs.items():
            sibling = {t.strip().lower() for t in terms}
            for term in sibling:
                term_to_cat[term] = (category, sub)
                group_of_term.setdefault(term, set()).update(sibling)
    return term_to_cat, group_of_term


TERM_TO_CATEGORY, SYNONYM_GROUPS = _build_indexes()

# Longest terms first so "power bank" wins over "bank"-like partial hits.
_SORTED_TERMS = sorted(TERM_TO_CATEGORY.keys(), key=len, reverse=True)


def category_list() -> List[Dict[str, object]]:
    """Shape the taxonomy for the UI's category picker."""
    return [
        {"name": cat, "subcategories": sorted(subs.keys())}
        for cat, subs in sorted(CATEGORIES.items())
    ]


def classify(text: str) -> tuple:
    """Return (category, subcategory) for a blob of deal text."""
    blob = f" {text.lower()} "
    for term in _SORTED_TERMS:
        needle = term if len(term) > 3 else f" {term} "
        if needle in blob:
            return TERM_TO_CATEGORY[term]
    return ("Other", "General")


def expand_query(query: str) -> Set[str]:
    """Expand a user query into every synonym worth matching against."""
    q = query.lower().strip()
    if not q:
        return set()
    terms: Set[str] = {q}
    terms.update(w for w in re.split(r"[^a-z0-9&']+", q) if len(w) > 2)

    for term in _SORTED_TERMS:
        if term in q:
            terms.update(SYNONYM_GROUPS.get(term, set()))

    # A bare category / subcategory name pulls in everything beneath it.
    for category, subs in CATEGORIES.items():
        if category.lower() in q:
            for sub_terms in subs.values():
                terms.update(t.lower() for t in sub_terms)
        for sub, sub_terms in subs.items():
            if sub.lower() in q:
                terms.update(t.lower() for t in sub_terms)
    return {t for t in terms if t}


def detect_brand(text: str) -> str:
    blob = f" {text.lower()} "
    best = ""
    for brand in KNOWN_BRANDS:
        if f" {brand} " in blob or blob.startswith(f" {brand}"):
            if len(brand) > len(best):
                best = brand
    return best.title() if best else ""


def is_spam(text: str) -> bool:
    return bool(_SPAM_RE.search(text or ""))
