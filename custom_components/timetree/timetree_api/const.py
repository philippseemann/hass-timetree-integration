"""Constants for the TimeTree API client."""

__version__ = "0.1.0"

BASE_URL = "https://timetreeapp.com"
API_BASE = f"{BASE_URL}/api"

SIGNIN_URL = f"{BASE_URL}/signin"
AUTH_SIGNIN_ENDPOINT = f"{API_BASE}/v1/auth/email/signin"
AUTH_VALIDATE_ENDPOINT = f"{API_BASE}/v1/auths"

CALENDARS_ENDPOINT = f"{API_BASE}/v2/calendars"
CALENDAR_EVENTS_ENDPOINT = f"{API_BASE}/v1/calendar/{{calendar_id}}/events"
CALENDAR_EVENT_ENDPOINT = f"{API_BASE}/v1/calendar/{{calendar_id}}/event"
CALENDAR_EVENT_DETAIL_ENDPOINT = (
    f"{API_BASE}/v1/calendar/{{calendar_id}}/event/{{event_id}}"
)
CALENDAR_LABELS_ENDPOINT = f"{API_BASE}/v1/calendar/{{calendar_id}}/labels"
USER_ENDPOINT = f"{API_BASE}/v1/user"

HEADER_CSRF = "X-CSRF-Token"
HEADER_TIMETREE_APP = "X-TimeTreeA"
TIMETREE_APP_ID = "web/2.1.0/de"

DEFAULT_THROTTLE_SECONDS = 0.1
