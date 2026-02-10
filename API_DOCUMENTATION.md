# TimeTree Unofficial API Documentation

Reverse-engineered from the TimeTree Web App (https://timetreeapp.com).

## Authentication

### Mechanism
- **Cookie-based session auth** (no Bearer token for the main API)
- The web app uses `credentials: "include"` on all fetch requests
- A **CSRF token** is sent via `X-CSRF-Token` header (read from `<meta name="csrf-token">`)
- A custom header **`X-TimeTreeA`** is always sent with value `web/{version}/{build}`

### Required Headers
```
Content-Type: application/json
X-TimeTreeA: web/<app_version>/<build_id>
X-CSRF-Token: <csrf_token_from_meta_tag>
```

### Login
Login is handled via the web interface at `/auth/`. The session cookie is set after login.
Email/password auth sends: `{ uid: "<email>", password: "<password>" }` to the auth endpoint.

### Auth Endpoint
- `GET /api/v1/auths` - Validate current session / get auth info

---

## Base URL
```
https://timetreeapp.com/api
```

All endpoints are relative to this base. The app uses both `/v1/` and `/v2/` API versions.

---

## API Endpoints

### User
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/user` | Get current user info |
| GET | `/v1/user/setting` | Get user settings |
| GET | `/v1/user_agreements?country=ZZ` | Get user agreements |
| POST | `/v1/app_launch` | Report app launch (analytics) |

### Calendars
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v2/calendars` | List all calendars |
| GET | `/v1/public_calendars` | List public calendars |
| GET | `/v2/calendars/{calendar_id}/users` | Get calendar members |
| GET | `/v1/calendars/{calendar_id}/virtual_users` | Get virtual users |
| GET | `/v1/calendar/{calendar_id}/labels` | Get calendar labels (color tags) |
| GET | `/v1/calendars/{calendar_id}/read_markers` | Get read markers |
| PUT | `/v1/calendar/{calendar_id}/mark` | Mark calendar as read |

### Events (CRUD)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/calendar/{calendar_id}/events?since={timestamp_ms}` | Get events (delta sync) |
| GET | `/v1/calendar/{calendar_id}/events/sync?since={timestamp_ms}` | Sync events (chunked) |
| POST | `/v1/calendar/{calendar_id}/event` | Create event |
| PUT | `/v1/calendar/{calendar_id}/event/{event_id}` | Update event |
| DELETE | `/v1/calendar/{calendar_id}/event/{event_id}` | Delete event |
| GET | `/v1/calendar/{calendar_id}/event/{event_id}/activities` | Get event activities/comments |
| POST | `/v1/calendar/{calendar_id}/event/{event_id}/files` | Upload files to event |
| POST | `/v1/events/files/presigned_urls` | Get presigned URLs for file upload |

### Memorial Days (Holidays)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v2/memorialdays?country_iso[]={code}&from={iso_date}&to={iso_date}` | Get holidays |

---

## Data Structures

### Calendar IDs
- **External ID** (URL): e.g., `A06aWY8_t8Gp` (used in web URLs)
- **Internal ID** (API): e.g., `20390654` (numeric, used in API calls)

### Event Object (API Parameters)

#### POST (Create Event)
```json
{
  "id": "string (UUID)",
  "fileUuids": ["string"],
  "title": "string",
  "allDay": true/false,
  "startAt": 1234567890000,  // Unix timestamp in milliseconds
  "startTimezone": "Europe/Berlin",
  "endAt": 1234567890000,    // Unix timestamp in milliseconds
  "endTimezone": "Europe/Berlin",
  "labelId": "number",       // Color label ID
  "note": "string",
  "location": "string",
  "locationLat": "number",
  "locationLon": "number",
  "attendees": ["string"],
  "recurrences": ["RRULE:..."],  // iCal RRULE format
  "alerts": [],
  "parentId": "string",      // For recurring event children
  "attachment": {},
  "silent": false,
  "category": "string",      // "schedule" or "memo"
  "copy": false
}
```

#### PUT (Update Event)
Same as POST, but instead of `id` and `fileUuids`:
- `addFileUuids`: File UUIDs to add
- `deleteFileUuids`: File UUIDs to remove

### Event Categories
- `schedule` - Regular calendar events
- `memo` - Memos/notes (keep events)

### Event Properties
- `allDay`: Boolean - whether it's an all-day event
- `startAt` / `endAt`: Unix timestamps in **milliseconds**
- `startTimezone` / `endTimezone`: IANA timezone strings (e.g., "Europe/Berlin"), "UTC" for all-day events
- `recurrences`: Array of iCal recurrence rules (RRULE, EXDATE)
- `labelId`: Numeric label/color identifier
- `category`: "schedule" (default) or "memo"

### Response Format
- Responses use **camelCase** keys
- Request bodies are converted to **snake_case** (decamelize) before sending
- The `camelizeKeys` function is applied to all responses
- Empty responses for status 204 and 502

### Delta Sync
Events are synced using a `since` parameter (Unix timestamp in milliseconds).
The API returns only events modified after that timestamp.

---

## Client-Side Storage

The web app uses **wa-sqlite** (WebAssembly SQLite) via IndexedDB for local caching:
- Database: `timetree-sqlite` (IndexedDB v6)
- Key-value store: `timetree-keyval` (IndexedDB v2)
- Firebase: `firebase-heartbeat-database`, `firebaseLocalStorageDb`

### localStorage Keys
- `timetree` - Contains `uuid` and `currentCalendarIds`
- `timetree.viewMode` - "monthly" / "weekly"
- `timetree.loginDate` - Last login date

---

## Notes for HA Integration

1. **Auth Challenge**: The API uses cookie-based auth with CSRF protection. For a HA integration, we need to:
   - Either use email/password login to obtain session cookies
   - Or provide a way for users to extract their session cookie

2. **API Key Alternative**: TimeTree has an official OAuth API at `https://developers.timetreeapp.com/` but it has limited functionality. The internal API documented here has full access.

3. **Rate Limiting**: The app uses a request queue (`Fq` array) with sequential processing and a 100ms delay between requests.

4. **Decamelize/Camelize**: Request params are sent in snake_case, responses come in camelCase (converted client-side).
