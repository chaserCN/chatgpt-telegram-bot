import logging
import os
import string
import random
from typing import Dict
from urllib.parse import quote_plus

import requests

from .plugin import Plugin


PLACES_SEARCH_URL = 'https://places.googleapis.com/v1/places:searchText'
PLACES_NEARBY_URL = 'https://places.googleapis.com/v1/places:searchNearby'
DIRECTIONS_URL = 'https://maps.googleapis.com/maps/api/directions/json'
STATIC_MAP_URL = 'https://maps.googleapis.com/maps/api/staticmap'

MARKER_LABELS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

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
    'places.primaryTypeDisplayName',
])

MAX_PRESENTED_PLACES = 20


class PlacesPlugin(Plugin):
    """
    Google Places (New) + Directions plugin.
    """

    def __init__(self):
        self.api_key = os.environ.get('GOOGLE_MAPS_API_KEY')

    def get_source_name(self) -> str:
        return 'Google Maps'

    def get_icon(self) -> str:
        return '🗺️'

    def get_spec(self) -> [Dict]:
        return [
            {
                'type': 'function',
                'name': 'search_places',
                'description': (
                    'Search for places (restaurants, cafes, museums, shops, landmarks, etc.) '
                    'by a free-text query, optionally biased to a location. '
                    'Returns a list of candidates. '
                    'IMPORTANT: if the user mentions a specific street, landmark, neighborhood, '
                    'or "near X", you MUST pass latitude+longitude (and a sensible radius_meters, '
                    'e.g. 500-1500) to bias results locally. Without a locationBias the API '
                    'returns the most famous places city-wide instead of the closest ones. '
                    'If you do not know the exact coordinates, first call search_places once for '
                    'the landmark itself ("Rue Monsieur le Prince Paris") to get its lat/lng, '
                    'then use those for the real search. '
                    'After getting results, call present_places to show selected ones to the user '
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
                    'Find places of a given category near a coordinate. '
                    'Use when the user gave a precise point (e.g. their hotel) and a category. '
                    'After getting results, call present_places to show selected ones with your commentary.'
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
                    'Present 1-20 selected places to the user as a numbered text list '
                    '(A, B, C, ...) plus a single map image with all of them pinned. '
                    'Call this AFTER search_places or find_nearby_places, picking the relevant '
                    'candidates and writing a short personal commentary for each one '
                    '(why it stands out, what to expect, who it suits). '
                    'Order matters: the first place gets label A on the map and #1 in the list.'
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
                    'Get a route between two points. Returns distance, duration and step-by-step '
                    'instructions, plus a Google Maps deep link the user can tap to open in their '
                    'native maps app with the route preloaded.'
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
                            'description': 'End address, place name, or "lat,lng".',
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
            lat = float(place['latitude'])
            lng = float(place['longitude'])
            maps_url = place.get('maps_url') or f'https://www.google.com/maps/search/?api=1&query={lat},{lng}'

            safe_name = self._md_escape(name)
            link = f'[{safe_name}]({maps_url})'

            line = f'{label}. {link}'
            if address:
                line += f' — {address}'
            if comment:
                line += f'\n   {comment}'
            lines.append(line)

            marker_params.append(f'markers=color:red%7Clabel:{label}%7C{lat},{lng}')

        items.append({
            'type': 'text',
            'text': '\n\n'.join(lines),
            'parse_mode': 'Markdown',
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

    def _get_directions(self, origin, destination, mode='walking', **_) -> Dict:
        params = {
            'origin': origin,
            'destination': destination,
            'mode': mode or 'walking',
            'key': self.api_key,
        }
        r = requests.get(DIRECTIONS_URL, params=params, timeout=15)
        data = r.json()
        if data.get('status') != 'OK':
            return {
                'error': f'Directions API status {data.get("status")}',
                'details': data.get('error_message') or '',
            }
        route = data['routes'][0]
        leg = route['legs'][0]
        steps = [
            {
                'instruction': self._strip_html(s.get('html_instructions', '')),
                'distance': s.get('distance', {}).get('text'),
                'duration': s.get('duration', {}).get('text'),
                'travel_mode': s.get('travel_mode'),
            }
            for s in leg.get('steps', [])
        ]
        deep_link = (
            'https://www.google.com/maps/dir/?api=1'
            f'&origin={quote_plus(origin)}'
            f'&destination={quote_plus(destination)}'
            f'&travelmode={mode or "walking"}'
        )
        return {
            'distance': leg['distance']['text'],
            'duration': leg['duration']['text'],
            'start_address': leg.get('start_address'),
            'end_address': leg.get('end_address'),
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
    def _strip_html(html: str) -> str:
        import re
        text = re.sub(r'<[^>]+>', ' ', html or '')
        return re.sub(r'\s+', ' ', text).strip()
