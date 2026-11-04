"""Deterministic query router (intent + search plan + preferences).

Handles most messages without an LLM call (important on CPU-only machines),
understands common English and Hinglish phrasings, and flags low-confidence
cases so the local LLM router can take a second look.
"""

from __future__ import annotations

import re

from app.llm.schemas import PlacesPlan, PreferenceUpdates, QueryPlan
from app.location.categories import category_by_key, match_category

_GREETING = re.compile(
    r"^\s*(hi+|hello|hey+|namaste|namaskar|good (morning|afternoon|evening|night)|thanks?|thank you|thx|ty|"
    r"ok(ay)?|cool|great|awesome|nice|bye|goodbye|shukriya|dhanyavaad|perfect|sounds good|got it)\b[\s!.,]*"
    r"(so much|a lot|bhai|yaar|ji)?[\s!.]*$",
    re.I,
)
_EMERGENCY = re.compile(
    r"\b(emergency|ambulance|bleeding|injur(ed|y)|accident|heart attack|chest pain|unconscious|"
    r"can'?t breathe|fire|police|robbed|stolen|theft|assault|danger|unsafe|poison(ed|ing)?|"
    r"nearest hospital|hospital (near|nearby|close)|need a doctor|urgent(ly)? doctor|"
    r"severe|fainted|collapsed|help me)\b",
    re.I,
)
_PROPERTY = re.compile(
    r"\b(wi-?fi|internet|password|check[- ]?in|check[- ]?out|checkout|checkin|parking|park my|"
    r"house rules?|rules|smok(e|ing)|pets?|dogs?|visitors?|guests? allowed|noise|quiet hours|part(y|ies)|"
    r"a\.?c\.?|air ?con\w*|heater|heating|geyser|hot water|kitchen|cook|fridge|refrigerator|"
    r"washing machine|laundry at|iron|tv|television|lift|elevator|power ?(cut|backup)|inverter|"
    r"drinking water|ro water|housekeeping|cleaning|towels?|bedsheets?|garbage|trash|dustbin|key|keys|"
    r"lock|door code|lockbox|host|caretaker|manager|contact number|address of (the|my|our)|"
    r"bedrooms?|bathrooms?|amenit(y|ies)|facilit(y|ies)|the (flat|apartment|property|place|room|house|villa|stay)|"
    r"my (stay|room|flat|apartment)|our (stay|room|flat|apartment)|early check|late check|"
    r"emergency exit|fire exit|security|gate|balcony|terrace|max(imum)? guests|"
    r"included|include|includes|complimentary|do you provide|do you offer|provided)\b",
    re.I,
)
# Strong cues: the guest clearly wants places around them.
_LOCAL_STRONG = re.compile(
    r"\b(near(by)?|near me|around( here)?|close by|close to (me|here|the property)|walking distance|"
    r"in the (area|neighbou?rhood)|kahan|kaha|within \d+|open (now|late))\b",
    re.I,
)
# Weak cues: generic phrasings that also appear in property questions
# ("Where is the key?", "best way to get in").
_LOCAL_WEAK = re.compile(
    r"\b(suggest|recommend|where can i|where to|where('?s| is) (a|the|an) |any good|best|places?|options|spots?|"
    r"batao|bata|find (a|me|some))\b",
    re.I,
)
_LOCAL = _LOCAL_STRONG  # backwards-compatible alias
_NEIGHBOURHOOD = re.compile(
    r"\b(area|neighbou?rhood|locality|is (dwarka|janakpuri|saket) (good|safe|nice)|safe (area|at night)|"
    r"how (far|do i get|to get)|airport|railway station|getting around|metro (line|card)|"
    r"uber|ola|auto|rickshaw|transport|weather|best time|what'?s (dwarka|janakpuri|saket) like|known for)\b",
    re.I,
)
_HINGLISH = re.compile(
    r"\b(bhai|yaar|koi|batao|bata|achh?[ai]|accha|kahan|kaha|kya|hai|hain|jagah|khana|khaana|chahiye|"
    r"sasta|sasti|mast|thoda|kuch|mujhe|humein|hum|kitna|kitne|wala|wali|kar|karo|dena|milega|milegi)\b",
    re.I,
)
_CHEAPER = re.compile(r"\b(cheap(er)?|budget|affordable|inexpensive|less expensive|sast[ai]|kam (paise|daam)|pocket[- ]friendly|economical)\b", re.I)
_PREMIUM = re.compile(r"\b(fanc(y|ier)|premium|upscale|luxur(y|ious)|classy|high[- ]end|expensive|special occasion)\b", re.I)
_CLOSER = re.compile(r"\b(closer|nearer|walking distance|walkable)\b", re.I)
_OPEN = re.compile(r"\b(open now|open late|late night|after midnight|midnight|24 ?(hours|hrs|x ?7|/7))\b", re.I)
_FILTER_PREV = re.compile(
    r"\b(any of (these|those|them)|which (of|one)|of (these|those)|these|those|them|that one|the (first|second|third|last) one)\b",
    re.I,
)
_REFINE = re.compile(r"\b(something|anything|any|show|more|other|another|else|different)\b", re.I)
_LATE = re.compile(r"\b(late night|late-night|after midnight|midnight|open late|24 ?(hours|hrs|x ?7|/7))\b", re.I)
_RADIUS = re.compile(r"(?:within|in|under|upto|up to)\s+(\d+(?:\.\d+)?)\s*(km|kms|kilomet(?:er|re)s?|m|meters?|metres?)\b|\b(\d+(?:\.\d+)?)\s*(km|kms)\s*(?:radius|range|away|ke andar)\b", re.I)
_SWITCH = re.compile(
    r"\b(switch|change|move|moved|actually|now) (to |i'?m |staying |in |at )*(?:the )?(dwarka|janakpuri|saket)\b"
    r"|\b(i'?m|we'?re|i am|we are) (actually |now )?(staying )?(in|at) (?:the )?(dwarka|janakpuri|saket)\b",
    re.I,
)
_LOCATION_NAMES = ("dwarka", "janakpuri", "saket")
_AMENITY_Q = re.compile(
    r"^\s*(is there|are there|do you have|do we have|have you got|does (it|the (flat|apartment|property|place|room|house|stay)) have|"
    r"is (a|an|the) .{1,30} (available|provided|included))\b",
    re.I,
)
_IN_PROPERTY = re.compile(
    r"\b(in|at|inside) (the|this|our|my) (building|property|flat|apartment|society|complex|premises|house|villa|stay)\b",
    re.I,
)


def _preferences(text: str) -> PreferenceUpdates:
    t = text.lower()
    p = PreferenceUpdates()
    if _CHEAPER.search(t):
        p.budget = "budget"
    elif _PREMIUM.search(t):
        p.budget = "premium"
    if re.search(r"\bjain\b", t):
        p.dietary = "jain"
    elif re.search(r"\bvegan\b", t):
        p.dietary = "vegan"
    elif re.search(r"\b(pure veg|vegetarian|veg only|no meat)\b", t):
        p.dietary = "vegetarian"
    elif re.search(r"\b(non[- ]?veg|chicken|mutton|meat)\b", t):
        p.dietary = "non-vegetarian"
    if re.search(r"\b(parents|famil(y|ies)|kids|children|mom|dad|mummy|papa)\b", t):
        p.companions = "family"
    elif re.search(r"\b(date|girlfriend|boyfriend|wife|husband|partner|couple|romantic)\b", t):
        p.companions = "couple"
    elif re.search(r"\b(friends|dosto|gang|group)\b", t):
        p.companions = "friends"
    elif re.search(r"\b(alone|solo|by myself)\b", t):
        p.companions = "solo"
    if re.search(r"\b(walk|walking|on foot|paidal)\b", t):
        p.travel_mode = "walking"
    elif re.search(r"\bmetro\b", t) and re.search(r"\b(by|take|using|via)\s+metro\b", t):
        p.travel_mode = "metro"
    m = _RADIUS.search(t)
    if m:
        num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        val = float(num)
        p.max_distance_km = val if unit.lower().startswith("k") else val / 1000
    return p


_GENERIC_CATEGORIES = {"restaurant", "attraction", "entertainment", "budget_food", "family_restaurant"}
_NON_KEYWORDS = {
    "near", "nearby", "good", "best", "nice", "some", "place", "places", "suggest", "recommend", "where", "find",
    "food", "dinner", "lunch", "breakfast", "eat", "restaurant", "restaurants", "around", "here", "there", "want",
    "looking", "please", "can", "get", "any", "great", "options", "spot", "spots", "tonight", "today", "tomorrow",
    "kahan", "batao", "koi", "achhi", "jagah", "bhai", "yaar", "with", "for", "what", "about", "also", "really",
    "something", "anything", "cheap", "cheaper", "budget", "family", "friendly", "parents", "kids", "within",
    "radius", "walking", "distance", "open", "late", "night", "now", "the", "and", "our", "stay", "property",
    "khana", "khaana", "liye", "ke", "interesting", "things", "do", "visit", "go", "local", "authentic",
}


def _extract_keyword(lower: str, category) -> str | None:
    """Specific words worth searching OSM names/cuisines for, when the matched
    category is generic or missing (e.g. "tibetan food nearby" -> "tibetan")."""
    if category is not None and category.key not in _GENERIC_CATEGORIES:
        return None
    kws = {k for c in [category] if c for k in c.keywords}
    words = [w for w in re.findall(r"[a-z]{4,}", lower) if w not in _NON_KEYWORDS and w not in kws]
    return " ".join(words[:2]) or None


def heuristic_plan(message: str, last_places_plan: dict | None = None, has_previous_places: bool = False) -> QueryPlan:
    text = message.strip()
    lower = text.lower()
    language = "hinglish" if len(_HINGLISH.findall(lower)) >= 2 or re.search(r"\b(bhai|yaar|batao)\b", lower) else "english"
    prefs = _preferences(text)

    switch = _SWITCH.search(lower)
    if switch:
        loc = next((n for n in _LOCATION_NAMES if n in switch.group(0)), None)
        return QueryPlan(intent="GENERAL_CONVERSATION", switch_location=loc, language=language, preference_updates=prefs)

    if _GREETING.match(text):
        return QueryPlan(intent="GENERAL_CONVERSATION", language=language, preference_updates=prefs)

    radius_m = int(prefs.max_distance_km * 1000) if prefs.max_distance_km else None

    if _EMERGENCY.search(lower):
        return QueryPlan(
            intent="EMERGENCY_QUERY",
            property_question="emergency contacts host contact nearby hospital emergency numbers",
            places=PlacesPlan(action="search", query="hospital", category_key="hospital", rank_by_distance=True, radius_m=radius_m),
            language=language,
            preference_updates=prefs,
        )

    category = match_category(lower)
    is_property = bool(_PROPERTY.search(lower))
    strong_local = bool(_LOCAL_STRONG.search(lower))
    weak_local = bool(_LOCAL_WEAK.search(lower))
    # Property cues outrank weak/category-only local cues ("Is breakfast included?",
    # "Where is the key?"); explicit "nearby/around" keeps it local.
    is_local = strong_local or ((bool(category) or weak_local) and not is_property)
    if is_property and category and not strong_local:
        category = None
    # "Is there a pool?", "Do you have a hair dryer?", "Does the flat have a balcony?":
    # amenity questions about the stay unless they say "nearby"/"around".
    if _AMENITY_Q.search(lower) and not strong_local and not weak_local:
        is_property, is_local, category = True, False, None
    # "Is there a gym in the building?" is about the property, not the neighbourhood.
    if _IN_PROPERTY.search(lower) and not strong_local:
        is_property, is_local, category = True, False, None
    # Transit questions: the host's guide and live search both help.
    if category and category.key in {"metro", "bus"}:
        is_property = True
    # "parking" near a restaurant is a place filter, not a property question.
    if is_property and category and re.search(r"\b(restaurant|cafe|bar|mall|place)s?\b.*\bparking\b", lower) and not re.search(r"\b(property|flat|apartment|our|my)\b.*\bparking\b|\bparking\b.*\b(property|flat|apartment)\b", lower):
        is_property = False

    # Follow-ups about previously shown places.
    if has_previous_places and _FILTER_PREV.search(lower) and not is_property:
        return QueryPlan(
            intent="LOCAL_DISCOVERY_QUERY",
            places=PlacesPlan(action="filter_previous", query=(last_places_plan or {}).get("query", "previous results")),
            language=language,
            preference_updates=prefs,
        )

    # Refinements of the previous search: "something cheaper", "anything closer?".
    refine_signal = _CHEAPER.search(lower) or _PREMIUM.search(lower) or _CLOSER.search(lower) or _OPEN.search(lower)
    if last_places_plan and refine_signal and not is_property and (not category or _REFINE.search(lower)):
        prev = PlacesPlan.model_validate(last_places_plan)
        plan = prev.model_copy(update={"action": "refine_previous"})
        if _CHEAPER.search(lower):
            plan.budget = "budget"
            plan.query = re.sub(r"\b(fine dining|fancy|upscale|premium)\b\s*", "", prev.query).strip() or prev.query
            if "budget" not in plan.query:
                plan.query = f"budget {plan.query}"
            if plan.category_key == "fine_dining":
                plan.category_key = "restaurant"
        if _PREMIUM.search(lower):
            plan.budget = "premium"
        if _CLOSER.search(lower):
            plan.rank_by_distance = True
            plan.radius_m = min(prev.radius_m or 3000, 1000)
        if _OPEN.search(lower):
            plan.open_now = True
            plan.late_night = bool(_LATE.search(lower))
        return QueryPlan(intent="LOCAL_DISCOVERY_QUERY", places=plan, language=language, preference_updates=prefs)

    places = None
    confident = True
    if is_local:
        cat = category or category_by_key("restaurant")
        query = cat.label
        if prefs.budget == "budget":
            query = f"budget {query}"
        if prefs.dietary in {"vegetarian", "vegan", "jain"} and cat.group == "food":
            query = f"{prefs.dietary} {query}"
        if prefs.companions == "family" and cat.group == "food":
            query = f"family friendly {query}"
        late = bool(_LATE.search(lower)) or cat.key == "late_night_food"
        places = PlacesPlan(
            action="search",
            query=query,
            category_key=cat.key,
            keyword=_extract_keyword(lower, category),
            radius_m=radius_m,
            open_now=True if re.search(r"\bopen now\b|\bright now\b", lower) else None,
            late_night=late,
            budget=prefs.budget,
            rank_by_distance=cat.rank_by_distance or "nearest" in lower or "closest" in lower,
        )

    if is_property and places:
        intent = "MIXED_QUERY"
    elif is_property:
        intent = "PROPERTY_QUERY"
    elif places:
        intent = "LOCAL_DISCOVERY_QUERY"
    elif _NEIGHBOURHOOD.search(lower):
        intent = "GENERAL_NEIGHBOURHOOD_QUERY"
    else:
        # Unknown: treat as a property question first (knowledge base is the safest source),
        # and flag it so the local LLM router may take a second look.
        intent = "PROPERTY_QUERY"
        confident = False

    property_question = None
    if intent in {"PROPERTY_QUERY", "GENERAL_NEIGHBOURHOOD_QUERY"}:
        property_question = text
    elif intent == "MIXED_QUERY":
        # Keep only the clause(s) about the property for knowledge-base lookup.
        parts = [p.strip() for p in re.split(r"[,;?]|\band\b|\balso\b", text) if p and p.strip()]
        property_question = " ".join(p for p in parts if _PROPERTY.search(p)) or text

    return QueryPlan(
        intent=intent,
        confident=confident,
        property_question=property_question,
        places=places,
        language=language,
        preference_updates=prefs,
    )
