"""Catalogue mapping guest intents to OpenStreetMap tag filters.

`osm` holds Overpass-style selectors; a place matches a category if it matches
ANY selector, and a selector matches if ALL of its [..] conditions hold:
    ["amenity"="cafe"]            exact value
    ["cuisine"~"indian|punjabi",i] case-insensitive regex
    ["opening_hours"]              key exists
The same selectors drive the live Overpass query and the offline datasets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    group: str  # food / nightlife / entertainment / shopping / tourism / essentials / other
    label: str
    osm: tuple[str, ...]
    keywords: tuple[str, ...]
    rank_by_distance: bool = False


_R = '["amenity"="restaurant"]'
_FF = '["amenity"="fast_food"]'
_CAFE = '["amenity"="cafe"]'

CATEGORIES: list[Category] = [
    # ---- food
    Category("momos", "food", "momos", (f'{_R}["cuisine"~"momo|tibetan|nepal",i]', f'{_FF}["cuisine"~"momo|tibetan|nepal",i]', '["amenity"~"restaurant|fast_food"]["name"~"momo",i]'), ("momo", "momos", "dumpling")),
    Category("street_food", "food", "street food", (_FF, '["amenity"="food_court"]', f'{_R}["cuisine"~"chaat|street|indian",i]'), ("street food", "chaat", "golgappa", "pani puri", "tikki", "chole bhature", "paratha")),
    Category("budget_food", "food", "budget food", (_FF, '["amenity"="food_court"]', f'{_R}["name"~"dhaba|bhojnalaya|canteen|chaat",i]'), ("cheap eats", "budget food", "sasta khana")),
    Category("cafe", "food", "cafe", (_CAFE, '["shop"="coffee"]'), ("cafe", "café", "coffee", "chai", "tea", "brunch")),
    Category("work_cafe", "food", "work-friendly cafe", (_CAFE, '["amenity"="coworking_space"]'), ("work on my laptop", "laptop", "work from", "quiet place to work", "wifi cafe")),
    Category("bakery", "food", "bakery", ('["shop"="bakery"]', '["shop"="pastry"]', '["shop"="confectionery"]'), ("bakery", "cake", "pastry", "pastries", "bread", "dessert")),
    Category("cake_delivery", "food", "cake shop", ('["shop"="bakery"]', '["shop"="pastry"]', '["shop"="confectionery"]'), ("cake delivered", "cake delivery", "birthday cake")),
    Category("fine_dining", "food", "fine dining", (_R,), ("fine dining", "fancy", "upscale", "romantic dinner", "date", "date night")),
    Category("north_indian", "food", "North Indian restaurant", (f'{_R}["cuisine"~"indian|punjabi|mughlai|north_indian|biryani|kebab|tandoor",i]',), ("north indian", "butter chicken", "dal makhani", "punjabi", "mughlai", "biryani", "kebab", "tandoori")),
    Category("south_indian", "food", "South Indian restaurant", (f'{_R}["cuisine"~"south_indian|dosa|udupi|kerala|chettinad",i]', f'{_R}["name"~"dosa|udupi|sagar|madras",i]'), ("south indian", "dosa", "idli", "vada", "uttapam")),
    Category("chinese", "food", "Chinese restaurant", (f'{_R}["cuisine"~"chinese|asian|momo",i]', f'{_FF}["cuisine"~"chinese",i]'), ("chinese", "noodles", "manchurian", "chowmein", "hakka")),
    Category("fast_food", "food", "fast food", (_FF,), ("fast food", "burger", "pizza", "fries", "sandwich")),
    Category("vegetarian", "food", "vegetarian food", ('["amenity"~"restaurant|fast_food|cafe"]["diet:vegetarian"~"yes|only",i]', f'{_R}["cuisine"~"vegetarian|vegan",i]', f'{_R}["name"~"vaishno|veg|bhojnalaya|haldiram|bikanerwala",i]'), ("vegetarian", "pure veg", "veg only", "vegan", "jain")),
    Category("family_restaurant", "food", "family restaurant", (_R,), ("family restaurant", "family dinner", "parents", "kids", "children")),
    Category("late_night_food", "food", "late-night food", (_R, _FF), ("late night", "after midnight", "midnight", "open late", "24 hours", "24x7", "24/7")),
    Category("restaurant", "food", "restaurant", (_R,), ("restaurant", "dinner", "lunch", "breakfast", "eat", "food", "khana", "khaana", "dine", "meal", "hungry")),
    # ---- nightlife & entertainment
    Category("pub", "nightlife", "pub", ('["amenity"="pub"]', '["craft"="brewery"]', '["microbrewery"="yes"]'), ("pub", "beer", "brewery", "microbrewery")),
    Category("bar", "nightlife", "bar", ('["amenity"="bar"]', '["amenity"="pub"]'), ("bar", "cocktail", "drinks", "daru", "sharab")),
    Category("club", "nightlife", "nightclub", ('["amenity"="nightclub"]', '["amenity"="bar"]'), ("club", "clubbing", "dancing", "dance", "nightclub", "party")),
    Category("lounge", "nightlife", "lounge", ('["amenity"="bar"]', '["amenity"="nightclub"]'), ("lounge", "rooftop", "resto-bar", "resto bar")),
    Category("live_music", "nightlife", "live music", ('["live_music"="yes"]', '["amenity"="music_venue"]', '["amenity"~"bar|pub"]'), ("live music", "gig", "concert", "band")),
    Category("sports_bar", "nightlife", "sports bar", ('["amenity"~"bar|pub"]["sport"]', '["amenity"~"bar|pub"]'), ("cricket match", "watch the match", "sports bar", "screening", "ipl", "football match", "watch cricket")),
    Category("cinema", "entertainment", "cinema", ('["amenity"="cinema"]',), ("movie", "cinema", "film", "pvr", "multiplex")),
    Category("gaming", "entertainment", "gaming zone", ('["leisure"="amusement_arcade"]', '["leisure"="trampoline_park"]'), ("gaming", "arcade", "game zone", "vr")),
    Category("bowling", "entertainment", "bowling", ('["leisure"="bowling_alley"]',), ("bowling",)),
    Category("entertainment", "entertainment", "entertainment", ('["amenity"="cinema"]', '["leisure"~"amusement_arcade|bowling_alley|trampoline_park|escape_game"]', '["amenity"="theatre"]'), ("entertainment", "fun", "things to do", "activities")),
    # ---- shopping
    Category("mall", "shopping", "shopping mall", ('["shop"="mall"]', '["shop"="department_store"]'), ("mall", "shopping mall", "shopping")),
    Category("market", "shopping", "market", ('["amenity"="marketplace"]', '["place"="square"]["name"~"market",i]'), ("market", "bazaar", "bazar", "street shopping", "shopping street")),
    Category("grocery", "essentials", "grocery store", ('["shop"="supermarket"]', '["shop"="convenience"]', '["shop"="greengrocer"]'), ("grocery", "supermarket", "kirana", "vegetables", "fruits", "milk")),
    Category("convenience", "essentials", "convenience store", ('["shop"="convenience"]',), ("convenience store", "general store")),
    Category("clothing", "shopping", "clothing store", ('["shop"="clothes"]', '["shop"="boutique"]', '["shop"="fabric"]'), ("clothes", "clothing", "kurta", "saree", "apparel")),
    # ---- tourism
    Category("monument", "tourism", "historic site", ('["historic"~"monument|memorial|tomb|fort|ruins|archaeological_site|castle"]', '["heritage"]'), ("monument", "historical", "heritage", "fort", "tomb", "history")),
    Category("park", "tourism", "park", ('["leisure"="park"]', '["leisure"="garden"]'), ("park", "garden", "walk", "jog", "morning walk")),
    Category("museum", "tourism", "museum", ('["tourism"="museum"]',), ("museum",)),
    Category("art_gallery", "tourism", "art gallery", ('["tourism"="gallery"]', '["shop"="art"]'), ("art gallery", "art", "gallery", "exhibition")),
    Category("religious", "tourism", "place of worship", ('["amenity"="place_of_worship"]',), ("temple", "mandir", "gurudwara", "mosque", "church", "religious", "worship")),
    Category("attraction", "tourism", "attraction", ('["tourism"~"attraction|museum|viewpoint|gallery|theme_park|zoo"]', '["historic"~"monument|memorial|tomb|fort"]', '["leisure"="park"]["name"]'), ("tourist", "attraction", "sightseeing", "visit", "weekend", "explore", "interesting", "things to see")),
    # ---- essentials
    Category("pharmacy", "essentials", "pharmacy", ('["amenity"="pharmacy"]', '["shop"="chemist"]', '["healthcare"="pharmacy"]'), ("pharmacy", "chemist", "medical store", "medicine", "dawai"), True),
    Category("hospital", "essentials", "hospital", ('["amenity"="hospital"]', '["healthcare"="hospital"]'), ("hospital", "emergency room", "a&e"), True),
    Category("clinic", "essentials", "clinic", ('["amenity"="clinic"]', '["amenity"="doctors"]', '["healthcare"~"clinic|doctor"]'), ("clinic", "doctor", "physician", "dentist"), True),
    Category("atm", "essentials", "ATM", ('["amenity"="atm"]', '["amenity"="bank"]["atm"="yes"]'), ("atm", "cash", "withdraw"), True),
    Category("bank", "essentials", "bank", ('["amenity"="bank"]',), ("bank",), True),
    Category("petrol", "essentials", "petrol pump", ('["amenity"="fuel"]',), ("petrol", "fuel", "gas station", "cng", "diesel"), True),
    Category("laundry", "essentials", "laundry", ('["shop"="laundry"]', '["shop"="dry_cleaning"]'), ("laundry", "dry clean", "dhobi", "ironing"), True),
    Category("salon", "essentials", "salon", ('["shop"="hairdresser"]', '["shop"="beauty"]'), ("salon", "haircut", "barber", "spa", "parlour", "parlor"), True),
    Category("gym", "essentials", "gym", ('["leisure"="fitness_centre"]', '["leisure"="sports_centre"]["sport"~"fitness",i]'), ("gym", "workout", "fitness", "yoga"), True),
    Category("car_rental", "essentials", "car rental", ('["amenity"="car_rental"]', '["amenity"="bicycle_rental"]'), ("car rental", "rent a car", "self drive", "bike rental"), True),
    Category("metro", "essentials", "metro station", ('["railway"="station"]["station"="subway"]', '["public_transport"="station"]["subway"="yes"]', '["railway"="station"]["network"~"metro",i]'), ("metro", "metro station", "subway"), True),
    Category("bus", "essentials", "bus stop", ('["highway"="bus_stop"]', '["amenity"="bus_station"]'), ("bus stop", "bus station", "bus"), True),
    Category("coworking", "other", "coworking space", ('["amenity"="coworking_space"]', '["office"="coworking"]'), ("coworking", "co-working", "work space", "workspace")),
]

QUICK_ACTION_QUERIES = {
    "property": "Tell me the key details about my stay - check-in, Wi-Fi and house rules",
    "food": "Suggest some good places to eat nearby",
    "cafes": "Any nice cafes nearby?",
    "things_to_do": "What are some interesting things to do near my stay?",
    "shopping": "Where can I go shopping nearby?",
    "nightlife": "Any good places for drinks or nightlife nearby?",
    "essentials": "Where is the nearest pharmacy, ATM and grocery store?",
}

_KW_CACHE: dict[str, re.Pattern] = {}


def _kw_pattern(kw: str) -> re.Pattern:
    pat = _KW_CACHE.get(kw)
    if pat is None:
        pat = re.compile(rf"(?<![a-z]){re.escape(kw.strip())}(e?s)?(?![a-z])")
        _KW_CACHE[kw] = pat
    return pat


def match_category(text: str) -> Category | None:
    """The most specific catalogue category mentioned in `text` (longest keyword wins)."""
    t = f" {text.lower()} "
    best: dict[str, tuple[int, Category]] = {}
    for cat in CATEGORIES:
        for kw in cat.keywords:
            if _kw_pattern(kw).search(t) and (cat.key not in best or len(kw) > best[cat.key][0]):
                best[cat.key] = (len(kw), cat)
    if not best:
        return None
    # Time qualifiers ("after midnight") refine a venue type rather than replace it:
    # "bars open after midnight" is a bar search with a late-night filter.
    if len(best) > 1:
        best.pop("late_night_food", None)
    return max(best.values(), key=lambda x: x[0])[1]


def category_by_key(key: str | None) -> Category | None:
    if not key:
        return None
    return next((c for c in CATEGORIES if c.key == key), None)


# ------------------------------------------------------------------ selector matching
_COND = re.compile(r'\["([^"]+)"(?:(=|~)"([^"]*)"(,i)?)?\]')


def parse_selector(selector: str) -> list[tuple[str, str | None, str | None, bool]]:
    return [(k, op or None, v, bool(ci)) for k, op, v, ci in _COND.findall(selector)]


def tags_match(tags: dict, selector: str) -> bool:
    for key, op, value, ci in parse_selector(selector):
        actual = tags.get(key)
        if actual is None:
            return False
        if op == "=" and actual != value:
            return False
        if op == "~" and not re.search(value or "", str(actual), re.I if ci else 0):
            return False
    return True


def matches_category(tags: dict, category: Category) -> bool:
    return any(tags_match(tags, sel) for sel in category.osm)
