import logging
import os
import string
import random
import html
import json
import math
import concurrent.futures
from typing import Dict
from urllib.parse import quote_plus

import requests
from google import genai
from google.genai import types

from .plugin import Plugin


PLACES_SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'
PLACES_NEARBY_URL = 'https://places.googleapis.com/v1/places:searchNearby'
DIRECTIONS_URL = 'https://maps.googleapis.com/maps/api/directions/json'
STATIC_MAP_URL = 'https://maps.googleapis.com/maps/api/staticmap'
GEOCODE_URL = 'https://maps.googleapis.com/maps/api/geocode/json'
TRIPADVISOR_SEARCH_URL = 'https://api.content.tripadvisor.com/api/v1/location/search'
TRIPADVISOR_DETAILS_URL = 'https://api.content.tripadvisor.com/api/v1/location/{location_id}/details'

MARKER_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
GOOGLE_LANGUAGE_CODE = 'fr'
TRIPADVISOR_LANGUAGE = 'fr'
TRIPADVISOR_ENRICH_LIMIT = 10

RESTAURANT_CUISINE_TYPES = [
    'french_restaurant', 'italian_restaurant', 'spanish_restaurant',
    'portuguese_restaurant', 'greek_restaurant', 'mediterranean_restaurant',
    'tapas_restaurant',
    'asian_restaurant', 'japanese_restaurant', 'ramen_restaurant',
    'sushi_restaurant', 'chinese_restaurant', 'cantonese_restaurant',
    'dim_sum_restaurant', 'vietnamese_restaurant', 'thai_restaurant',
    'korean_restaurant', 'korean_barbecue_restaurant', 'indian_restaurant',
    'lebanese_restaurant', 'turkish_restaurant', 'moroccan_restaurant',
    'persian_restaurant', 'israeli_restaurant', 'middle_eastern_restaurant',
    'falafel_restaurant', 'shawarma_restaurant',
    'african_restaurant', 'ethiopian_restaurant',
    'mexican_restaurant', 'peruvian_restaurant', 'brazilian_restaurant',
    'argentinian_restaurant',
    'seafood_restaurant', 'barbecue_restaurant', 'pizza_restaurant',
    'vegetarian_restaurant', 'vegan_restaurant', 'brunch_restaurant',
    'fine_dining_restaurant', 'buffet_restaurant',
    'fast_food_restaurant', 'hamburger_restaurant',
]

CUISINE_UMBRELLA_EXPANSIONS = {
    'african_restaurant': ['african_restaurant', 'moroccan_restaurant', 'ethiopian_restaurant'],
    'asian_restaurant': [
        'asian_restaurant', 'chinese_restaurant', 'cantonese_restaurant',
        'dim_sum_restaurant', 'japanese_restaurant', 'ramen_restaurant',
        'sushi_restaurant', 'korean_restaurant', 'korean_barbecue_restaurant',
        'thai_restaurant', 'vietnamese_restaurant', 'indian_restaurant',
    ],
}

PLACES_FIELD_MASK = ','.join([
    'places.id',
    'places.displayName',
    'places.formattedAddress',
    'places.location',
    'places.rating',
    'places.userRatingCount',
    'places.priceLevel',
    'places.googleMapsUri',
    'places.regularOpeningHours.openNow',
    'places.primaryType',
    'places.primaryTypeDisplayName',
])

MAX_PRESENTED_PLACES = 20


class PlacesPlugin(Plugin):
    """
    Google Places (New) + Directions plugin.
    """

    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_MAPS_API_KEY')
        self.tripadvisor_api_key = os.environ.get('TRIPADVISOR_API_KEY')
        self.gemini_api_key = os.environ.get('GEMINI_API_KEY')
        self.gemini_client = genai.Client(api_key=self.gemini_api_key) if self.gemini_api_key else None

    def get_source_name(self) -> str:
        return 'Google Maps'

    def get_icon(self) -> str:
        return '🗺️'

    def get_spec(self) -> [Dict]:
        return [
            {
                'type': 'function',
                'name': 'search_restaurants',
                'description': (
                    'Restaurant discovery near an address or coordinates. Uses Google for retrieval '
                    'and Tripadvisor as a second rating signal on the top candidates, then returns '
                    'an already-ranked shortlist with evidence from both sources.\n'
                    '\n'
                    'Use this WHENEVER the user asks for restaurants (any cuisine, or no cuisine) '
                    'near a specific place. Call this tool ONCE per request — do NOT first call '
                    'search_places to geocode the address; this tool geocodes the raw address '
                    'internally. Pass the user-provided address verbatim in `address` (e.g. '
                    '"rue Monsieur le Prince 26, paris"). Only pass latitude+longitude when the '
                    'user already gave coordinates or a shared location.\n'
                    '\n'
                    'For non-restaurant categories (cafes, museums, shops) use search_places or '
                    'find_nearby_places — they are cheaper and give the same quality.\n'
                    '\n'
                    'After getting results, call present_places to show the selected shortlist with '
                    'your commentary, propagating google_rating, google_rating_count, '
                    'tripadvisor_rating, tripadvisor_review_count and tripadvisor_url from each '
                    'picked place.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'Free-text restaurant query, e.g. "ramen", "bistrot", "italian".',
                        },
                        'address': {
                            'type': 'string',
                            'description': 'Preferred when the user gave an address, street, hotel, or landmark.',
                        },
                        'latitude': {'type': 'number'},
                        'longitude': {'type': 'number'},
                        'radius_meters': {
                            'type': 'number',
                            'description': 'Search radius in meters. Default 1200.',
                        },
                        'max_results': {
                            'type': 'integer',
                            'description': 'Max shortlist size to return. Default 12, max 20.',
                        },
                    },
                    'required': ['query'],
                },
            },
            {
                'type': 'function',
                'name': 'search_places',
                'description': (
                    'Search for places (cafes, museums, shops, landmarks, etc.) by a free-text '
                    'query, optionally biased to a location. Returns a list of candidates with '
                    'name, address, lat/lng, rating, opening status, category and a Google Maps URL.\n'
                    '\n'
                    'DO NOT use this tool for restaurants — call search_restaurants instead, it '
                    'gives a better-ranked shortlist with Tripadvisor evidence. Also DO NOT use '
                    'this tool to geocode an address before search_restaurants — search_restaurants '
                    'accepts a raw address and geocodes it internally.\n'
                    '\n'
                    'IMPORTANT: if the user mentions a specific street, landmark, neighbourhood, '
                    'or "near X", you MUST pass latitude+longitude (and a sensible radius_meters, '
                    'e.g. 500-1500) to bias results locally. Without a locationBias the API returns '
                    'the most famous places city-wide instead of the closest ones — that is almost '
                    'never what the user wants.\n'
                    '\n'
                    'If you do not know the exact coordinates, first call search_places once for '
                    'the landmark itself ("Rue Monsieur le Prince Paris") to get its lat/lng, then '
                    'use those for the real search.\n'
                    '\n'
                    'For dense queries (cafes, bakeries, shops) prefer max_results=20 to give the '
                    'user choice. After getting results, call present_places to show selected ones '
                    'with your personal commentary.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'What to search for, e.g. "boulangerie near Louvre Paris".',
                        },
                        'latitude': {
                            'type': 'number',
                            'description': 'Optional center latitude to bias results.',
                        },
                        'longitude': {
                            'type': 'number',
                            'description': 'Optional center longitude to bias results.',
                        },
                        'radius_meters': {
                            'type': 'number',
                            'description': 'Optional search radius around the center, in meters. Default 1500.',
                        },
                        'max_results': {
                            'type': 'integer',
                            'description': (
                                'Max number of candidates to return. Default 15, max 20. '
                                'For dense queries (cafes, bakeries, shops) prefer 20 to give the user choice.'
                            ),
                        },
                    },
                    'required': ['query'],
                },
            },
            {
                'type': 'function',
                'name': 'find_nearby_places',
                'description': (
                    'Find places of given categories near a coordinate. Use when the user '
                    'gave a precise point (their hotel, a shared location, "from here") plus '
                    'a category, and wants the closest options of that type rather than a '
                    'free-text search.\n'
                    '\n'
                    '`included_types` are Google Places type strings: ["restaurant"], '
                    '["cafe","bakery"], ["museum"], ["pharmacy"], etc. Multiple types act as '
                    'OR — pass several to widen the net (e.g. ["cafe","bakery"] for '
                    '"somewhere for breakfast").\n'
                    '\n'
                    'Returns the same fields as search_places (name, address, lat/lng, '
                    'rating, open_now, category, maps_url). After getting results, call '
                    'present_places to show selected ones with your commentary.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'latitude': {'type': 'number'},
                        'longitude': {'type': 'number'},
                        'included_types': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': (
                                'Google Places type strings, e.g. ["restaurant"], ["cafe","bakery"], '
                                '["museum"], ["pharmacy"]. See Google Places API types.'
                            ),
                        },
                        'radius_meters': {
                            'type': 'number',
                            'description': 'Search radius in meters. Default 1000, max 50000.',
                        },
                        'max_results': {
                            'type': 'integer',
                            'description': 'Max candidates. Default 10, max 20.',
                        },
                    },
                    'required': ['latitude', 'longitude', 'included_types'],
                },
            },
            {
                'type': 'function',
                'name': 'present_places',
                'description': (
                    'Present 1-20 selected places to the user as a numbered list '
                    '(A, B, ...) plus a single Google Static Map image with all of them '
                    'pinned. This is a UI tool — it renders the actual message the user '
                    'sees: tappable name, address, your comment, and the map.\n'
                    '\n'
                    'Call AFTER search_places or find_nearby_places, picking the relevant '
                    'candidates and writing a 1-3 sentence personal recommendation for each '
                    '(why it stands out, what to expect, who it suits) — not a description '
                    'copied from the place\'s category. Order matters: the first place gets '
                    'label A on the map and #1 in the list.\n'
                    '\n'
                    'Use this when the user is BROWSING — choosing where to go, comparing '
                    'options, "what is around / посоветуй кафе / find me a few X". The '
                    'output is independent pins, not a path.\n'
                    '\n'
                    'DO NOT use this for routes, walks, strolls, itineraries or "from X to '
                    'Y" — call get_directions with waypoints instead. Pins on a map are not '
                    'a route.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'intro': {
                            'type': 'string',
                            'description': (
                                'Optional intro shown once before all cards. '
                                'Skip it by default — only include if the user explicitly asked '
                                'for an overview or summary, otherwise leave empty.'
                            ),
                        },
                        'places': {
                            'type': 'array',
                            'description': 'Places to present, in display order.',
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'name': {'type': 'string'},
                                    'address': {'type': 'string'},
                                    'latitude': {'type': 'number'},
                                    'longitude': {'type': 'number'},
                                    'maps_url': {
                                        'type': 'string',
                                        'description': (
                                            'Pass through the maps_url returned by search_places. '
                                            'Used to make the place name a tappable link.'
                                        ),
                                    },
                                    'comment': {
                                        'type': 'string',
                                        'description': (
                                            'Your 1-3 sentence personal recommendation for this place. '
                                            'Shown right above the map card.'
                                        ),
                                    },
                                    'google_rating': {
                                        'type': 'number',
                                        'description': 'Optional Google rating to render as a separate evidence line.',
                                    },
                                    'google_rating_count': {
                                        'type': 'integer',
                                        'description': 'Optional Google review count paired with google_rating.',
                                    },
                                    'tripadvisor_rating': {
                                        'type': 'number',
                                        'description': 'Optional Tripadvisor rating to render as a separate evidence line.',
                                    },
                                    'tripadvisor_review_count': {
                                        'type': 'integer',
                                        'description': 'Optional Tripadvisor review count paired with tripadvisor_rating.',
                                    },
                                    'tripadvisor_url': {
                                        'type': 'string',
                                        'description': 'Optional Tripadvisor page URL for the place.',
                                    },
                                },
                                'required': ['name', 'address', 'latitude', 'longitude', 'comment'],
                            },
                        },
                    },
                    'required': ['places'],
                },
            },
            {
                'type': 'function',
                'name': 'get_directions',
                'description': (
                    'Build a route between two points, optionally via ordered waypoints. '
                    'Returns distance, duration, steps, and a Google Maps deep link the '
                    'user taps to open the route in their maps app.\n'
                    '\n'
                    'Use for ANY walk, stroll, route, itinerary or "from X to Y" — '
                    'including loops with no fixed destination ("a 30-min walk from X"): '
                    'set destination = origin and pass stops as waypoints. Walking pace '
                    '≈80 m/min.\n'
                    '\n'
                    'Compose the walk yourself first, as if you were a local taking a '
                    'friend along — pick waypoints by character of the street, not by '
                    'fame: pedestrian/cobbled lanes, covered passages, hidden courtyards, '
                    'food markets, riverbanks/bridges, viewpoints, small squares, '
                    'specialty shops, neighbourhood landmarks. Use search_places only '
                    'to look up coordinates of spots you already chose.\n'
                    '\n'
                    'For each waypoint, give a one-line "why" in your reply — what makes '
                    'THIS spot worth pausing at (a view, atmosphere, a detail), not a '
                    'Wikipedia summary, not a navigation cue. If you cannot write a '
                    'non-generic "why", drop it.'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'origin': {
                            'type': 'string',
                            'description': 'Start address, place name, or "lat,lng".',
                        },
                        'destination': {
                            'type': 'string',
                            'description': (
                                'End address, place name, or "lat,lng". '
                                'For a loop walk back to the start, pass the same value as origin.'
                            ),
                        },
                        'waypoints': {
                            'type': 'array',
                            'description': (
                                'Optional ordered intermediate stops. Each item is an address, '
                                'place name, or "lat,lng". Order is preserved unless '
                                'optimize_waypoints is true. Max 10 to keep the deep link short.'
                            ),
                            'items': {'type': 'string'},
                        },
                        'optimize_waypoints': {
                            'type': 'boolean',
                            'description': (
                                'If true, Google reorders waypoints for the shortest route. '
                                'Leave false (default) for curated scenic walks where order matters.'
                            ),
                        },
                        'mode': {
                            'type': 'string',
                            'enum': ['walking', 'transit', 'driving', 'bicycling'],
                            'description': 'Travel mode. Default walking.',
                        },
                    },
                    'required': ['origin', 'destination'],
                },
            },
        ]

    async def execute(self, function_name, helper, **kwargs) -> Dict:
        if not self.api_key:
            return {'error': 'GOOGLE_MAPS_API_KEY env var is not set'}

        if function_name == 'search_places':
            return self._search_places(**kwargs)
        if function_name == 'search_restaurants':
            return self._search_restaurants(**kwargs)
        if function_name == 'find_nearby_places':
            return self._find_nearby_places(**kwargs)
        if function_name == 'present_places':
            return self._present_places(**kwargs)
        if function_name == 'get_directions':
            return self._get_directions(**kwargs)
        return {'error': f'Unknown function {function_name}'}

    def _search_places(self, query, latitude=None, longitude=None,
                       radius_meters=1500, max_results=10, **_) -> Dict:
        body = {
            'textQuery': query,
            'maxResultCount': min(int(max_results or 15), 20),
            'languageCode': GOOGLE_LANGUAGE_CODE,
        }
        if latitude is not None and longitude is not None:
            body['locationBias'] = {
                'circle': {
                    'center': {'latitude': float(latitude), 'longitude': float(longitude)},
                    'radius': float(radius_meters or 1500),
                }
            }
        headers = {
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': self.api_key,
            'X-Goog-FieldMask': PLACES_FIELD_MASK,
        }
        r = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=15)
        if r.status_code != 200:
            return {'error': f'Places API error {r.status_code}', 'details': r.text[:500]}
        return {'places': [self._normalize_place(p) for p in r.json().get('places', [])]}

    def _find_nearby_places(self, latitude, longitude, included_types,
                            radius_meters=1000, max_results=10, **_) -> Dict:
        body = {
            'includedTypes': included_types,
            'maxResultCount': min(int(max_results or 15), 20),
            'languageCode': GOOGLE_LANGUAGE_CODE,
            'locationRestriction': {
                'circle': {
                    'center': {'latitude': float(latitude), 'longitude': float(longitude)},
                    'radius': min(float(radius_meters or 1000), 50000.0),
                }
            },
        }
        headers = {
            'Content-Type': 'application/json',
            'X-Goog-Api-Key': self.api_key,
            'X-Goog-FieldMask': PLACES_FIELD_MASK,
        }
        r = requests.post(PLACES_NEARBY_URL, json=body, headers=headers, timeout=15)
        if r.status_code != 200:
            return {'error': f'Places API error {r.status_code}', 'details': r.text[:500]}
        return {'places': [self._normalize_place(p) for p in r.json().get('places', [])]}

    def _search_restaurants(
        self,
        query,
        address=None,
        latitude=None,
        longitude=None,
        radius_meters=None,
        max_results=12,
        **_,
    ) -> Dict:
        logging.info(
            'search_restaurants: query=%r address=%r lat=%r lng=%r radius=%r max_results=%r',
            query, address, latitude, longitude, radius_meters, max_results,
        )
        if address:
            geocoded = self._geocode(address)
            if 'error' in geocoded:
                logging.warning('search_restaurants: geocode failed: %s', geocoded)
                return geocoded
            latitude = geocoded['latitude']
            longitude = geocoded['longitude']
            origin_address = geocoded['formatted_address']
            logging.info(
                'search_restaurants: geocoded address=%r -> lat=%s lng=%s formatted=%r',
                address, latitude, longitude, origin_address,
            )
        elif latitude is not None and longitude is not None:
            latitude = float(latitude)
            longitude = float(longitude)
            origin_address = f'{latitude},{longitude}'
        else:
            return {'error': 'Either address or latitude+longitude is required'}

        radius_meters = int(radius_meters or 1200)
        shortlist_size = min(int(max_results or 12), 20)

        raw_cuisine_types = self._resolve_cuisine_types(query)
        cuisine_types = self._expand_umbrella_cuisines(raw_cuisine_types)
        logging.info(
            'search_restaurants: cuisine resolved raw=%s expanded=%s',
            raw_cuisine_types, cuisine_types,
        )

        google_candidates = self._discover_google_candidates(
            query=query,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            cuisine_types=cuisine_types,
        )
        if 'error' in google_candidates:
            logging.warning('search_restaurants: candidates fetch failed: %s', google_candidates)
            return google_candidates

        places = google_candidates['places']
        logging.info('search_restaurants: %d unique candidates after dedup', len(places))

        for place in places:
            self._score_google_only(place, radius_meters=radius_meters)
        places.sort(key=lambda item: item.get('google_score_total', 0), reverse=True)

        prescore_top = places[:TRIPADVISOR_ENRICH_LIMIT]
        logging.info(
            'search_restaurants: top-%d by Google prescore: %s',
            len(prescore_top),
            [(p.get('name'), round(p.get('google_score_total', 0), 3)) for p in prescore_top],
        )

        self._enrich_restaurants_with_tripadvisor(prescore_top)

        ta_matched = sum(1 for p in prescore_top if p.get('tripadvisor'))
        ta_with_rating = sum(
            1 for p in prescore_top
            if p.get('tripadvisor') and p['tripadvisor'].get('rating') is not None
        )
        logging.info(
            'search_restaurants: tripadvisor matched=%d/%d, with_rating=%d/%d',
            ta_matched, len(prescore_top), ta_with_rating, len(prescore_top),
        )

        for place in places:
            self._finalize_score(place)
        places.sort(key=lambda item: item.get('final_score', 0), reverse=True)

        final_places = places[:shortlist_size]
        logging.info(
            'search_restaurants: returning %d places (shortlist=%d). Top: %s',
            len(final_places), shortlist_size,
            [
                (
                    p.get('name'),
                    round(p.get('final_score', 0), 3),
                    p.get('primary_type'),
                    int(p.get('distance_from_origin_m') or -1),
                    bool(p.get('tripadvisor') and p['tripadvisor'].get('rating') is not None),
                )
                for p in final_places[:5]
            ],
        )

        return {
            'origin': {
                'address': origin_address,
                'latitude': latitude,
                'longitude': longitude,
            },
            'places': final_places,
        }

    def _discover_google_candidates(self, query, latitude, longitude, radius_meters, cuisine_types) -> Dict:
        raw_by_id = {}

        text_result = self._search_places(
            query=query,
            latitude=latitude,
            longitude=longitude,
            radius_meters=radius_meters,
            max_results=20,
        )
        if 'error' in text_result:
            return text_result
        text_places = text_result.get('places', [])
        text_total = len(text_places)
        logging.info(
            'discover_google: text query=%r returned %d. Names: %s',
            query, text_total,
            [(p.get('name'), p.get('primary_type')) for p in text_places],
        )
        if cuisine_types:
            text_places_filtered = [p for p in text_places if self._matches_cuisine(p, cuisine_types)]
            dropped = [p.get('name') for p in text_places if p not in text_places_filtered]
            logging.info(
                'discover_google: text cuisine filter %s passed %d/%d. Dropped: %s',
                cuisine_types, len(text_places_filtered), text_total, dropped,
            )
            text_places = text_places_filtered
        for place in text_places:
            place_id = place.get('id')
            if place_id:
                raw_by_id.setdefault(place_id, place)

        nearby_types = cuisine_types if cuisine_types else ['restaurant']
        nearby = self._find_nearby_places(
            latitude=latitude,
            longitude=longitude,
            included_types=nearby_types,
            radius_meters=radius_meters,
            max_results=20,
        )
        if 'error' in nearby:
            return nearby
        nearby_places = nearby.get('places', [])
        logging.info(
            'discover_google: nearby types=%s returned %d. Names: %s',
            nearby_types, len(nearby_places),
            [(p.get('name'), p.get('primary_type')) for p in nearby_places],
        )
        for place in nearby_places:
            place_id = place.get('id')
            if place_id:
                raw_by_id.setdefault(place_id, place)

        places = list(raw_by_id.values())
        for place in places:
            place['distance_from_origin_m'] = self._haversine_meters(
                latitude, longitude, place.get('latitude'), place.get('longitude')
            )
            place['tripadvisor'] = None

        return {'places': places}

    @staticmethod
    def _matches_cuisine(place, cuisine_types):
        primary = (place.get('primary_type') or '').lower()
        if primary and primary in cuisine_types:
            return True
        category = (place.get('category') or '').lower()
        if not category:
            return False
        cuisine_keywords = {t.replace('_restaurant', '').replace('_', ' ') for t in cuisine_types}
        return any(keyword and keyword in category for keyword in cuisine_keywords)

    def _enrich_restaurants_with_tripadvisor(self, candidates, concurrency=4):
        if not self.tripadvisor_api_key:
            logging.info('enrich_ta: no tripadvisor_api_key, skipping')
            return
        if not self.gemini_client:
            logging.info('enrich_ta: no gemini_client, skipping')
            return
        if not candidates:
            return

        def work(candidate):
            name = candidate.get('name')
            try:
                results = self._tripadvisor_search(
                    query=name,
                    latitude=candidate.get('latitude'),
                    longitude=candidate.get('longitude'),
                )
                if not results:
                    logging.info('enrich_ta[%r]: tripadvisor search returned 0', name)
                    return
                matched = self._gemini_match_tripadvisor_candidate(candidate, results[:10])
                if not matched:
                    logging.info(
                        'enrich_ta[%r]: gemini found no match among %d candidates',
                        name, len(results),
                    )
                    return
                details = self._tripadvisor_details(matched.get('location_id'))
                if details:
                    candidate['tripadvisor'] = details
                    logging.info(
                        'enrich_ta[%r]: matched location_id=%s rating=%s reviews=%s ranking=%s',
                        name, details.get('location_id'), details.get('rating'),
                        details.get('review_count'), details.get('ranking'),
                    )
                else:
                    candidate['tripadvisor'] = matched
                    logging.info(
                        'enrich_ta[%r]: matched location_id=%s but details fetch failed (no rating)',
                        name, matched.get('location_id'),
                    )
            except Exception as exc:
                logging.warning('enrich_ta[%r]: failed: %s', name, exc)

        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [executor.submit(work, candidate) for candidate in candidates]
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logging.warning('enrich_ta: worker failed: %s', exc)

    def _tripadvisor_search(self, query, latitude, longitude):
        params = {
            'key': self.tripadvisor_api_key,
            'searchQuery': query,
            'category': 'restaurants',
            'latLong': f'{latitude},{longitude}',
            'language': TRIPADVISOR_LANGUAGE,
        }
        r = requests.get(TRIPADVISOR_SEARCH_URL, params=params, timeout=20)
        if r.status_code != 200:
            logging.warning('Tripadvisor search failed: %s %s', r.status_code, r.text[:300])
            return []
        items = []
        for item in r.json().get('data', [])[:10]:
            address_obj = item.get('address_obj') or {}
            items.append({
                'location_id': str(item.get('location_id') or ''),
                'name': item.get('name') or '',
                'address': address_obj.get('address_string') or '',
                'latitude': item.get('latitude'),
                'longitude': item.get('longitude'),
            })
        return items

    def _tripadvisor_details(self, location_id):
        r = requests.get(
            TRIPADVISOR_DETAILS_URL.format(location_id=location_id),
            params={'key': self.tripadvisor_api_key, 'language': TRIPADVISOR_LANGUAGE},
            timeout=20,
        )
        if r.status_code != 200:
            logging.warning('Tripadvisor details failed: %s %s', r.status_code, r.text[:300])
            return None
        payload = r.json()
        address_obj = payload.get('address_obj') or {}
        ranking_data = payload.get('ranking_data') or {}
        ranking = ranking_data.get('ranking')
        try:
            ranking = int(ranking) if ranking else None
        except ValueError:
            ranking = None
        return {
            'location_id': str(payload.get('location_id') or location_id),
            'name': payload.get('name') or '',
            'address': address_obj.get('address_string') or '',
            'rating': self._to_float(payload.get('rating')),
            'review_count': self._to_int(payload.get('num_reviews')),
            'ranking': ranking,
            'web_url': payload.get('web_url'),
        }

    @staticmethod
    def _expand_umbrella_cuisines(cuisine_types):
        if not cuisine_types:
            return []
        expanded = []
        seen = set()
        for t in cuisine_types:
            for sub in CUISINE_UMBRELLA_EXPANSIONS.get(t, [t]):
                if sub not in seen:
                    seen.add(sub)
                    expanded.append(sub)
        return expanded

    def _resolve_cuisine_types(self, query):
        if not self.gemini_client:
            logging.info('resolve_cuisine: no gemini_client, skipping')
            return []
        if not query:
            return []
        payload = {
            'user_query': query,
            'allowed_types': RESTAURANT_CUISINE_TYPES,
            'task': (
                'Pick the Google Places restaurant types that best match the cuisine or format '
                'the user asked for. Choose only types from allowed_types. '
                'If the user did not specify a cuisine or format (e.g. "best restaurants near me", '
                '"где поесть рядом"), return an empty array. '
                'If the user named a cuisine that is not in allowed_types (e.g. Georgian, Uzbek), '
                'return an empty array — do not substitute a different cuisine. '
                'Return strict JSON only.'
            ),
            'output_schema': {
                'types': 'array of strings from allowed_types, or empty array',
            },
        }
        try:
            response = self.gemini_client.models.generate_content(
                model='gemini-3.1-flash-lite-preview',
                contents=json.dumps(payload, ensure_ascii=False),
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0,
                ),
            )
        except Exception as exc:
            logging.warning('Cuisine resolution failed: %s', exc)
            return []
        text = getattr(response, 'text', '') or ''
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return []
        types_list = parsed.get('types') or []
        allowed = set(RESTAURANT_CUISINE_TYPES)
        return [t for t in types_list if isinstance(t, str) and t in allowed]

    def _gemini_match_tripadvisor_candidate(self, google_place, tripadvisor_candidates):
        payload = {
            'google_place': {
                'name': google_place.get('name'),
                'address': google_place.get('address'),
                'latitude': google_place.get('latitude'),
                'longitude': google_place.get('longitude'),
                'google_type': google_place.get('category'),
            },
            'tripadvisor_candidates': tripadvisor_candidates[:10],
            'task': (
                'Decide whether any Tripadvisor candidate is the same physical place as the Google place. '
                'Choose exactly one Tripadvisor candidate if there is a strong match, otherwise choose null. '
                'Prefer exact address and location over exact name wording. Return strict JSON only.'
            ),
            'output_schema': {
                'matched_location_id': 'string or null',
                'confidence': 'number 0..1',
            },
        }
        response = self.gemini_client.models.generate_content(
            model='gemini-3.1-flash-lite-preview',
            contents=json.dumps(payload, ensure_ascii=False),
            config=types.GenerateContentConfig(
                response_mime_type='application/json',
                temperature=0,
            ),
        )
        text = getattr(response, 'text', '') or ''
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        location_id = str(parsed.get('matched_location_id') or '')
        confidence = float(parsed.get('confidence') or 0)
        if not location_id or confidence < 0.7:
            return None
        return next((item for item in tripadvisor_candidates if item.get('location_id') == location_id), None)

    def _score_google_only(self, place, radius_meters):
        distance = place.get('distance_from_origin_m') or radius_meters
        geo_score = max(0.0, 1.0 - min(distance, radius_meters) / radius_meters) * 0.20
        google_rating = self._to_float(place.get('rating')) or 0.0
        google_count = self._to_int(place.get('rating_count')) or 0
        google_score = (google_rating / 5.0) * 0.25
        google_confidence = min(math.log10(google_count + 1) / 4.0, 1.0) * 0.15
        place['google_score_total'] = round(geo_score + google_score + google_confidence, 4)

    def _finalize_score(self, place):
        base = place.get('google_score_total', 0.0)
        ta = place.get('tripadvisor')
        if not ta or ta.get('rating') is None:
            place['final_score'] = base
            return
        google_rating = self._to_float(place.get('rating')) or 0.0
        ta_rating = self._to_float(ta.get('rating')) or 0.0
        ta_reviews = self._to_int(ta.get('review_count')) or 0
        ta_score = (ta_rating / 5.0) * 0.24
        ta_score += min(math.log10(ta_reviews + 1) / 4.0, 1.0) * 0.12
        if ta.get('ranking') and ta['ranking'] <= 500:
            ta_score += 0.08
        if google_rating and ta_rating and google_rating - ta_rating >= 0.7:
            ta_score -= 0.10
        place['final_score'] = round(base + ta_score, 4)

    def _geocode(self, address):
        r = requests.get(
            GEOCODE_URL,
            params={'address': address, 'language': GOOGLE_LANGUAGE_CODE, 'key': self.api_key},
            timeout=15,
        )
        if r.status_code != 200:
            return {'error': f'Geocode error {r.status_code}', 'details': r.text[:500]}
        payload = r.json()
        if payload.get('status') != 'OK' or not payload.get('results'):
            return {'error': f'Geocode status {payload.get("status")}'}
        result = payload['results'][0]
        location = result['geometry']['location']
        return {
            'formatted_address': result.get('formatted_address'),
            'latitude': location.get('lat'),
            'longitude': location.get('lng'),
        }

    @staticmethod
    def _haversine_meters(lat1, lng1, lat2, lng2):
        if None in (lat1, lng1, lat2, lng2):
            return None
        radius = 6371000.0
        phi1 = math.radians(float(lat1))
        phi2 = math.radians(float(lat2))
        d_phi = math.radians(float(lat2) - float(lat1))
        d_lambda = math.radians(float(lng2) - float(lng1))
        a = (
            math.sin(d_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        )
        return round(2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)

    @staticmethod
    def _to_float(value):
        if value in (None, ''):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_int(value):
        if value in (None, ''):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _normalize_place(self, p: Dict) -> Dict:
        loc = p.get('location') or {}
        name = (p.get('displayName') or {}).get('text')
        primary = (p.get('primaryTypeDisplayName') or {}).get('text')
        opening = p.get('regularOpeningHours') or {}
        return {
            'id': p.get('id'),
            'name': name,
            'address': p.get('formattedAddress'),
            'latitude': loc.get('latitude'),
            'longitude': loc.get('longitude'),
            'rating': p.get('rating'),
            'rating_count': p.get('userRatingCount'),
            'price_level': p.get('priceLevel'),
            'category': primary,
            'primary_type': p.get('primaryType'),
            'open_now': opening.get('openNow'),
            'maps_url': p.get('googleMapsUri'),
        }

    def _present_places(self, places, intro=None, **_) -> Dict:
        picked = (places or [])[:MAX_PRESENTED_PLACES]
        items = []
        if intro and intro.strip():
            items.append({'type': 'text', 'text': intro.strip()})

        if not picked:
            return {
                'direct_result': {
                    'kind': 'message_sequence',
                    'format': 'inline',
                    'value': items,
                }
            }

        lines = []
        marker_params = []
        for i, place in enumerate(picked):
            label = MARKER_LABELS[i] if i < len(MARKER_LABELS) else str(i + 1)
            name = place.get('name') or ''
            comment = (place.get('comment') or '').strip()
            address = (place.get('address') or '').strip()
            google_rating = self._to_float(place.get('google_rating'))
            google_rating_count = self._to_int(place.get('google_rating_count'))
            tripadvisor_rating = self._to_float(place.get('tripadvisor_rating'))
            tripadvisor_review_count = self._to_int(place.get('tripadvisor_review_count'))
            tripadvisor_url = (place.get('tripadvisor_url') or '').strip()
            lat = float(place['latitude'])
            lng = float(place['longitude'])
            maps_url = place.get('maps_url') or f'https://www.google.com/maps/search/?api=1&query={lat},{lng}'

            safe_name = self._html_escape(name)
            safe_address = self._html_escape(address)
            safe_comment = self._html_escape(comment)
            safe_maps_url = html.escape(maps_url, quote=True)
            link = f'<a href="{safe_maps_url}">{safe_name}</a>'

            line = f'{label}. {link}'
            if safe_address:
                line += f' - {safe_address}'
            evidence_lines = []
            if google_rating is not None:
                google_text = f'Google: {google_rating:.1f}'
                if google_rating_count is not None:
                    google_text += f' ({google_rating_count})'
                evidence_lines.append(self._html_escape(google_text))
            if tripadvisor_rating is not None:
                ta_text = f'Tripadvisor: {tripadvisor_rating:.1f}'
                if tripadvisor_review_count is not None:
                    ta_text += f' ({tripadvisor_review_count})'
                if tripadvisor_url:
                    safe_ta_url = html.escape(tripadvisor_url, quote=True)
                    ta_text = f'<a href="{safe_ta_url}">{self._html_escape(ta_text)}</a>'
                else:
                    ta_text = self._html_escape(ta_text)
                evidence_lines.append(ta_text)
            if evidence_lines:
                line += '\n   ' + '\n   '.join(evidence_lines)
            if comment:
                line += f'\n   {safe_comment}'
            lines.append(line)

            marker_params.append(f'markers=color:red%7Clabel:{label}%7C{lat},{lng}')

        items.append({
            'type': 'text',
            'text': '\n\n'.join(lines),
            'parse_mode': 'HTML',
        })

        size = '640x640' if len(picked) > 4 else '640x480'
        static_map_url = (
            f'{STATIC_MAP_URL}?size={size}&'
            + '&'.join(marker_params)
            + f'&key={self.api_key}'
        )
        try:
            r = requests.get(static_map_url, timeout=15)
            content_type = (r.headers.get('content-type') or '').lower()
            if (r.status_code == 200
                    and r.content
                    and content_type.startswith('image/')
                    and len(r.content) <= 9_500_000):
                map_path = self._save_temp_png(r.content)
                logging.info(
                    'Static Maps fetched: bytes=%s markers=%s path=%s',
                    len(r.content), len(picked), map_path,
                )
                items.append({
                    'type': 'photo',
                    'path': map_path,
                })
            else:
                logging.warning(
                    'Static Maps fetch skipped: status=%s ctype=%s size=%s body=%s',
                    r.status_code, content_type, len(r.content),
                    r.text[:300] if not content_type.startswith('image/') else '<binary>',
                )
        except Exception as e:
            logging.warning('Static Maps fetch raised: %s', e)

        return {
            'direct_result': {
                'kind': 'message_sequence',
                'format': 'inline',
                'value': items,
            }
        }

    def _get_directions(self, origin, destination, mode='walking',
                        waypoints=None, optimize_waypoints=False, **_) -> Dict:
        mode = mode or 'walking'
        clean_waypoints = [w for w in (waypoints or []) if w and str(w).strip()][:10]

        params = {
            'origin': origin,
            'destination': destination,
            'mode': mode,
            'key': self.api_key,
        }
        if clean_waypoints:
            prefix = 'optimize:true|' if optimize_waypoints else ''
            params['waypoints'] = prefix + '|'.join(clean_waypoints)

        r = requests.get(DIRECTIONS_URL, params=params, timeout=15)
        data = r.json()
        if data.get('status') != 'OK':
            return {
                'error': f'Directions API status {data.get("status")}',
                'details': data.get('error_message') or '',
            }
        route = data['routes'][0]
        legs = route.get('legs', [])
        steps = []
        total_distance_m = 0
        total_duration_s = 0
        for leg in legs:
            total_distance_m += (leg.get('distance') or {}).get('value', 0)
            total_duration_s += (leg.get('duration') or {}).get('value', 0)
            for s in leg.get('steps', []):
                steps.append({
                    'instruction': self._strip_html(s.get('html_instructions', '')),
                    'distance': (s.get('distance') or {}).get('text'),
                    'duration': (s.get('duration') or {}).get('text'),
                    'travel_mode': s.get('travel_mode'),
                })

        deep_link = (
            'https://www.google.com/maps/dir/?api=1'
            f'&origin={quote_plus(origin)}'
            f'&destination={quote_plus(destination)}'
            f'&travelmode={mode}'
        )
        deep_link_waypoints = clean_waypoints
        waypoint_order = route.get('waypoint_order') or []
        if optimize_waypoints and waypoint_order:
            deep_link_waypoints = [
                clean_waypoints[index]
                for index in waypoint_order
                if isinstance(index, int) and 0 <= index < len(clean_waypoints)
            ]
        if clean_waypoints:
            deep_link += '&waypoints=' + quote_plus('|'.join(deep_link_waypoints))

        first_leg = legs[0] if legs else {}
        last_leg = legs[-1] if legs else {}
        return {
            'distance': self._format_distance(total_distance_m),
            'duration': self._format_duration(total_duration_s),
            'distance_meters': total_distance_m,
            'duration_seconds': total_duration_s,
            'start_address': first_leg.get('start_address'),
            'end_address': last_leg.get('end_address'),
            'waypoint_order': route.get('waypoint_order'),
            'steps': steps,
            'deep_link': deep_link,
        }

    @staticmethod
    def _save_temp_png(content: bytes) -> str:
        directory = 'uploads'
        if not os.path.exists(directory):
            os.makedirs(directory)
        name = ''.join(random.choices(string.ascii_letters + string.digits, k=15))
        path = os.path.join(directory, f'staticmap_{name}.png')
        with open(path, 'wb') as f:
            f.write(content)
        return path

    @staticmethod
    def _md_escape(text: str) -> str:
        # legacy Markdown link text: must escape [, ], (, ), \, * _ ` to avoid
        # breaking the link parser. Names like "Sephora (Châtelet)" are common.
        if not text:
            return ''
        out = []
        for ch in text:
            if ch in '[]()\\*_`':
                out.append('\\' + ch)
            else:
                out.append(ch)
        return ''.join(out)

    @staticmethod
    def _html_escape(text: str) -> str:
        if not text:
            return ''
        return html.escape(text, quote=False)

    @staticmethod
    def _strip_html(html: str) -> str:
        import re
        text = re.sub(r'<[^>]+>', ' ', html or '')
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _format_distance(meters: int) -> str:
        if meters >= 1000:
            return f'{meters / 1000:.1f} km'
        return f'{int(meters)} m'

    @staticmethod
    def _format_duration(seconds: int) -> str:
        minutes = round(seconds / 60)
        if minutes < 60:
            return f'{minutes} min'
        hours, mins = divmod(minutes, 60)
        return f'{hours} h {mins} min' if mins else f'{hours} h'
