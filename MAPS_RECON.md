# Google Maps Web Frontend Reconnaissance (`MAPS_RECON.md`)

## Executive Summary
This document details how the Google Maps web application (`https://www.google.com/maps`) fetches search listings and detailed business metadata internally over HTTP XHR/Fetch API calls. The goal of this reconnaissance is to enable intercepting structured internal responses rather than parsing rendered DOM elements.

---

## 1. Search & Pagination Endpoint

### Endpoint Overview
* **URL Pattern**: `https://www.google.com/maps/rpc/search` (or `/maps/rpc/locations/search` / `/maps/preview/place`)
* **HTTP Method**: `GET` (or `POST` for batched RPC)
* **Example URL** (Redacted):
  `https://www.google.com/maps/rpc/search?authuser=0&hl=en&gl=in&pb=!1m...!2m...!3m...&q=travel+agencies+in+Delhi&ech=1`

### Key Query Parameters & Token Analysis
| Parameter | Description / Pattern |
|---|---|
| `q` | URL-encoded search query (e.g., `travel+agencies+in+Delhi`). |
| `hl` | Interface language code (e.g., `en`). |
| `gl` | Country code for location bias (e.g., `in` for India). |
| `authuser` | Active Google account index (`0` for guest/default session). |
| `pb` | Protobuf-encoded parameter string (starts with `!1m...!2m...`). Encodes viewport coordinates, map zoom level, search query metadata, and pagination offsets. |
| `ech` | Internal RPC sequence counter (e.g., `1`, `2`). |

#### Pagination Mechanics
* Pagination is governed by the `pb` parameter.
* **Offset-based pagination**: `!2i20`, `!2i40`, `!2i60` inside the `pb` string represents result offsets (20 items per page).
* **Token-based pagination**: Google Maps returns a base64 session/continuation token in the response payload of page *N*, which must be injected into the `pb` string of page *N+1*.

### Response Format & Shape
* **Security Prefix**: Prefix string `)]}'\n` (5 characters: `)`, `]`, `}`, `'`, `\n`) protecting against JSON Hijacking.
* **Data Schema**: Multi-nested JSON arrays (JSON-serialized Protobuf messages) without key names.
* **Response Snippet Example**:
  ```json
  )]}'
  [
    "search_response",
    [
      null,
      [
        [
          "Swan Tours - Travel Agents in India | Best Tour Operator in Delhi",
          "0x390cfd...:0x...",
          ["Flat No. 6, Shankar Market, 2nd Floor, Above Shop No.1, Delhi"],
          [4.7, 879],
          ["Travel agency"],
          [28.6315, 77.2201]
        ]
      ]
    ]
  ]
  ```

### Field Mapping (Search List Items)
> [!WARNING]
> **Field Stability Notice**: Google uses an **unstable, indexed array format**. Array positions correspond to Protobuf tag field numbers and can shift between Google web client builds or A/B test variations.

| Field Name | Typical Array Index Path | Type | Example Value |
|---|---|---|---|
| Business Name | `item[14][18]` or `item[0]` | String | `"Swan Tours - Travel Agents in India"` |
| Feature ID / CID | `item[14][11]` | String (Hex Pair / ChIJ) | `"0x390cfd...:0x..."` |
| Address Preview | `item[14][2]` | Array of Strings | `["Flat No. 6, Shankar Market..."]` |
| Rating | `item[14][4][7]` | Float | `4.7` |
| Review Count | `item[14][4][8]` | Integer | `879` |
| Category | `item[14][13]` | String | `"Travel agency"` |
| Coordinates | `item[14][9]` | Pair `[Lat, Lng]` | `[28.6315, 77.2201]` |

---

## 2. Business Detail Pane Endpoint

### Endpoint Overview
* **URL Pattern**: `https://www.google.com/maps/rpc/place` (or `/maps/preview/place`)
* **HTTP Method**: `GET`
* **Example URL** (Redacted):
  `https://www.google.com/maps/rpc/place?authuser=0&hl=en&gl=in&pb=!1s0x390cfd...:0x...`

### Key Query Parameters
* `pb`: Protobuf reference string containing the unique place identifier (e.g. `!1s0x390cfd...:0x...` matching the hex CID or Place ID string extracted from the search result list).

### Response Format & Shape
* **Security Prefix**: `)]}'\n`
* **Data Schema**: Multi-nested JSON arrays containing detailed business metadata.
* **Fields Returned**:
  - **Phone Number**: Formatted string (e.g. `"+91 11 2341 5678"`).
  - **Official Website**: Full outbound URL (e.g. `"https://www.swantours.com/"`).
  - **Full Street Address**: Array of structured street/locality address strings.
  - **Opening Hours**: Weekly schedule matrix (days, open/close status, hours).
  - **User Reviews**: List of recent review objects (or fetched via dedicated endpoint `/maps/rpc/listreviews`).

---

## 3. Required Headers (Browser Interception Context)

To issue or replay requests outside of an active browser instance, the following HTTP headers are mandatory:

```http
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
Referer: https://www.google.com/maps/
x-goog-maps-client-id: google-maps-web
sec-ch-ua: "Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Windows"
Cookie: CONSENT=YES+; NID=511=...
```

> [!IMPORTANT]
> The `Referer: https://www.google.com/maps/` header is strictly enforced by Google's backend servers. Replaying XHR requests without a valid `Referer` header results in `403 Forbidden` or `400 Bad Request`.

---

## 4. Bot Detection & Defense Signals

During the session, the following automated protection signals were identified:

1. **Cookie / Consent Wall Redirect**:
   * Accessing Google Maps from headless or clean cookie environments triggers an initial consent flow or redirect to `consent.google.com`.
2. **`pb` Parameter Cryptographic / Session Markers**:
   * The `pb` query string contains session-bound tokens. Tampering with or constructing arbitrary `pb` strings without valid base64 tokens yields empty or invalid responses (`400 Bad Request`).
3. **Automated Scraping Defenses (ReCAPTCHA v3 / Enterprise)**:
   * Rapid or uniform request cadence without human-like delays triggers invisible ReCAPTCHA challenges or forces HTTP `429 Too Many Requests` status codes.
4. **TLS / HTTP Fingerprinting**:
   * Standard Python HTTP clients (`requests`, `urllib`) without HTTP/2 and modern TLS browser fingerprint emulation are flagged and throttled.
