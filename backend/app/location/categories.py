"""Catalogue mapping guest intents to Google Places searches.

Used by the heuristic planner (offline / fallback) and as guidance for the LLM
planner. `included_type` values are Google Places (New) "Table A" types.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    group: str  # food / nightlife / shopping / tourism / essentials / other
    query: str  # default text query
    included_type: str | None
    keywords: tuple[str, ...]


CATEGORIES: list[Category] = [
    # Food and dining
    Category("momos", "food", "momos", None, ("momo", "momos", "dumpling")),
    Category("street_food", "food", "street food chaat", None, ("street food", "chaat", "golgappa", "pani puri", "tikki", "chole bhature", "paratha")),
    Category("cafe", "food", "cafe", "cafe", ("cafe", "café", "coffee", "chai", "tea", "work on my laptop", "laptop", "wifi cafe", "brunch")),
    Category("bakery", "food", "bakery", "bakery", ("bakery", "cake", "pastry", "pastries", "bread", "dessert")),
    Category("fine_dining", "food", "fine dining restaurant", "fine_dining_restaurant", ("fine dining", "fancy", "upscale", "romantic dinner", "date")),
    Category("north_indian", "food", "north indian restaurant", "indian_restaurant", ("north indian", "butter chicken", "dal makhani", "punjabi", "mughlai", "biryani", "kebab", "tandoori")),
    Category("south_indian", "food", "south indian restaurant", "indian_restaurant", ("south indian", "dosa", "idli", "vada", "uttapam")),
    Category("chinese", "food", "chinese restaurant", "chinese_restaurant", ("chinese", "noodles", "manchurian", "chowmein", "hakka")),
    Category("fast_food", "food", "fast food", "fast_food_restaurant", ("fast food", "burger", "pizza", "fries", "sandwich")),
    Category("vegetarian", "food", "vegetarian restaurant", "vegetarian_restaurant", ("vegetarian", "veg ", "pure veg", "vegan", "jain")),
    Category("family_restaurant", "food", "family restaurant", "restaurant", ("family restaurant", "parents", "family dinner", "kids", "children")),
    Category("late_night_food", "food", "late night food open now", "restaurant", ("late night", "after midnight", "midnight", "open late", "24 hours", "24x7", "24/7")),
    Category("restaurant", "food", "restaurant", "restaurant", ("restaurant", "dinner", "lunch", "breakfast", "eat", "food", "khana", "khaana", "dine", "meal", "hungry")),
    Category("cake_delivery", "food", "cake shop delivery", "bakery", ("cake delivered", "cake delivery", "birthday cake")),
    # Nightlife and entertainment
    Category("pub", "nightlife", "pub", "pub", ("pub", "beer", "brewery", "microbrewery")),
    Category("bar", "nightlife", "bar", "bar", ("bar", "cocktail", "drinks", "daru", "sharab")),
    Category("club", "nightlife", "night club dance", "night_club", ("club", "clubbing", "dancing", "dance", "nightclub", "party")),
    Category("lounge", "nightlife", "lounge bar", "bar", ("lounge", "rooftop", "resto-bar", "resto bar")),
    Category("live_music", "nightlife", "live music venue", None, ("live music", "gig", "concert", "band")),
    Category("sports_bar", "nightlife", "sports bar screening cricket", "sports_bar", ("cricket match", "watch the match", "sports bar", "screening", "ipl", "football match")),
    Category("cinema", "entertainment", "movie theater", "movie_theater", ("movie", "cinema", "film", "pvr", "multiplex")),
    Category("gaming", "entertainment", "gaming zone arcade", "amusement_center", ("gaming", "arcade", "game zone", "vr")),
    Category("bowling", "entertainment", "bowling alley", "bowling_alley", ("bowling",)),
    Category("entertainment", "entertainment", "entertainment centre", "amusement_center", ("entertainment", "fun", "activities", "things to do")),
    # Shopping
    Category("mall", "shopping", "shopping mall", "shopping_mall", ("mall", "shopping mall", "shopping")),
    Category("market", "shopping", "local market", "market", ("market", "bazaar", "bazar", "street shopping", "shopping street")),
    Category("grocery", "essentials", "grocery store supermarket", "grocery_store", ("grocery", "supermarket", "kirana", "vegetables", "fruits", "milk")),
    Category("convenience", "essentials", "convenience store", "convenience_store", ("convenience store", "general store")),
    Category("clothing", "shopping", "clothing store", "clothing_store", ("clothes", "clothing", "kurta", "saree", "apparel")),
    # Tourism and recreation
    Category("monument", "tourism", "historical monument", "historical_landmark", ("monument", "historical", "heritage", "fort", "tomb", "history")),
    Category("park", "tourism", "park garden", "park", ("park", "garden", "walk", "jog", "morning walk")),
    Category("museum", "tourism", "museum", "museum", ("museum",)),
    Category("art_gallery", "tourism", "art gallery", "art_gallery", ("art gallery", "art", "gallery", "exhibition")),
    Category("religious", "tourism", "temple gurudwara mosque church", "place_of_worship", ("temple", "mandir", "gurudwara", "mosque", "church", "religious", "worship")),
    Category("attraction", "tourism", "tourist attraction", "tourist_attraction", ("tourist", "attraction", "sightseeing", "visit", "weekend", "explore", "interesting")),
    # Essential services
    Category("pharmacy", "essentials", "pharmacy chemist", "pharmacy", ("pharmacy", "chemist", "medical store", "medicine", "dawai")),
    Category("hospital", "essentials", "hospital emergency", "hospital", ("hospital", "emergency room", "a&e")),
    Category("clinic", "essentials", "clinic doctor", "doctor", ("clinic", "doctor", "physician", "dentist")),
    Category("atm", "essentials", "ATM", "atm", ("atm", "cash", "withdraw")),
    Category("bank", "essentials", "bank", "bank", ("bank",)),
    Category("petrol", "essentials", "petrol pump", "gas_station", ("petrol", "fuel", "gas station", "cng", "diesel")),
    Category("laundry", "essentials", "laundry dry cleaning", "laundry", ("laundry", "dry clean", "dhobi", "ironing")),
    Category("salon", "essentials", "salon", "hair_salon", ("salon", "haircut", "barber", "spa", "parlour", "parlor")),
    Category("gym", "essentials", "gym fitness", "gym", ("gym", "workout", "fitness", "yoga")),
    Category("car_rental", "essentials", "car rental", "car_rental", ("car rental", "rent a car", "self drive", "bike rental")),
    Category("metro", "essentials", "metro station", "subway_station", ("metro", "metro station", "subway")),
    Category("bus", "essentials", "bus stop", "bus_station", ("bus stop", "bus station", "bus")),
    Category("coworking", "other", "coworking space", "coworking_space", ("coworking", "co-working", "work space", "workspace")),
]

QUICK_ACTION_QUERIES = {
    "food": "Suggest some good places to eat nearby",
    "things_to_do": "What are some interesting things to do near my stay?",
    "shopping": "Where can I go shopping nearby?",
    "nightlife": "Any good places for drinks or nightlife nearby?",
    "essentials": "Where is the nearest pharmacy, ATM and grocery store?",
    "property": "Tell me the key details about my stay - check-in, Wi-Fi and house rules",
}


_KW_CACHE: dict[str, re.Pattern] = {}


def _kw_pattern(kw: str) -> re.Pattern:
    pat = _KW_CACHE.get(kw)
    if pat is None:
        # Whole-word match, allowing simple plurals ("cafes", "pubs").
        pat = re.compile(rf"(?<![a-z]){re.escape(kw.strip())}(e?s)?(?![a-z])")
        _KW_CACHE[kw] = pat
    return pat


def match_category(text: str) -> Category | None:
    """Return the most specific catalogue category mentioned in `text`."""
    t = f" {text.lower()} "
    best: tuple[int, Category] | None = None
    for cat in CATEGORIES:
        for kw in cat.keywords:
            if _kw_pattern(kw).search(t):
                # Prefer longer (more specific) keyword matches; earlier categories break ties.
                if best is None or len(kw) > best[0]:
                    best = (len(kw), cat)
    return best[1] if best else None


def category_by_key(key: str | None) -> Category | None:
    if not key:
        return None
    return next((c for c in CATEGORIES if c.key == key), None)
