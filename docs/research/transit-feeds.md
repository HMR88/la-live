# LA Westside to downtown: transit feed inventory

Date: 2026-11-05, about 20:20 UTC. "Confirmed" means the URL was fetched (status, content type, and for GTFS-Realtime the FeedMessage header parsed) or the statement was read on the cited page. No forms were submitted, nothing was registered. Downloaded samples sit in the session scratch folder, not in the repo.

## Summary table

| Agency | Static GTFS | Vehicle positions | Trip updates | Alerts | Key | Licence |
|---|---|---|---|---|---|---|
| LA Metro bus | gitlab.com/LACMTA/gtfs_bus/raw/master/gtfs_bus.zip (22.2 MB) | api.goswift.ly/real-time/lametro/gtfs-rt-vehicle-positions (401 without key) | .../lametro/gtfs-rt-trip-updates | .../lametro/gtfs-rt-alerts (listed by Transitland, not on Metro's page) | Swiftly key via Google form, header `Authorization: <key>`; cost and approval policy not stated | developer.metro.net/terms-conditions plus Swiftly API licence |
| LA Metro bus and rail, no key | same | **wss://api.metro.net/ws/LACMTA/vehicle_positions/{route_codes}** JSON every 3 s, "free to use and does not require an API key", `access-control-allow-origin: *` (a pass-through of Swiftly) | wss://api.metro.net/ws/LACMTA/trip_updates/... | none | none | Metro terms |
| LA Metro rail | gitlab.com/LACMTA/gtfs_rail/raw/master/gtfs_rail.zip (1.3 MB) | Swiftly agency `lametro-rail`, or `wss://api.metro.net/ws/LACMTA_Rail/...` | same | same | as above | as above |
| Big Blue Bus | gtfs.bigbluebus.com/current.zip (1.0 MB, Aug 2026) | cleverapi.bigbluebus.com/gtfsrt/vehicles (live, 10 KB) | cleverapi.bigbluebus.com/gtfsrt/trips (also gtfs.bigbluebus.com/tripupdates.bin, CORS *) | gtfs.bigbluebus.com/alerts.bin | none | ODC-By 1.0 |
| Culver CityBus | web.culvercity.org/gtfs/gtfsexport.zip (1.1 MB) | nextccbus.org/gtfsrt/vehicles (live) | nextccbus.org/gtfsrt/trips | nextccbus.org/gtfsrt/alerts | none | none stated |
| LADOT (DASH, Commuter Express) | ladotbus.com/gtfs (3.6 MB zip served as text/plain, regenerated daily) | ladotbus.com/gtfs-rt/vehiclepositions (live, header version 1.0) | ladotbus.com/gtfs-rt/tripupdates | ladotbus.com/gtfs-rt/alerts | none | LADOT developer licence agreement: free, revocable, required notice text, GTFS-RT not mentioned |
| Metrolink | metrolinktrains.com/globalassets/about/gtfs/gtfs.zip (0.4 MB) | metrolink-gtfsrt.gbsdigital.us/feed/gtfsrt-vehicles (403 without key) | .../gtfsrt-trips | cdn.simplifytransit.com/metrolink/alerts/service-alerts.pb (open, CORS *) | `X-Api-Key`, emailed after a form, no fee stated, 30 s refresh | terms on the access page |

Big Blue Bus's own `gtfs.bigbluebus.com/vehiclepositions.bin` is stale (last timestamp 2025-04-09, no entities); use the cleverapi URL.

## Facts that shape the design

- Three of the four bus agencies in the corridor need no key at all. LA Metro needs either a Swiftly key or the free WebSocket at api.metro.net, which also allows browsers (CORS `*`). The WebSocket is documented at lacmta.github.io/metro-api-v2/docs/websockets.
- Swiftly's GTFS-RT endpoints refuse browser requests (no CORS header) and want polling every 10 to 30 s; Metro says positions update every 5 s, trip updates every 10 s. The other agencies' feeds send no CORS headers either. A server-side poller is needed for everything except the Metro WebSocket and Big Blue Bus trip updates.
- All feeds are protobuf except the Metro WebSocket, which is JSON.
- Metro's bus static feed has three branches: master (base), weekly-updated-service, future-service (merged before June and December shakeups). Rail updates daily.
- No other operator with GTFS-RT was found for the corridor. Pasadena Transit, Santa Clarita Transit and Rosemead Explorer have feeds but lie outside it. Foothill, Torrance, Beach Cities and Metro Micro were not checked.
- Licences: Metro requires accurate, unmodified reproduction and attribution and forbids advertising use; Big Blue Bus is ODC-By; LADOT requires a bold notice that the information is subject to change; Culver City states nothing.

## Source pages

developer.metro.net (`/api/`, `/gtfs-schedule-data/`, `/terms-conditions/`), api.metro.net (`/`, `/openapi.json`), lacmta.github.io/metro-api-v2/docs (`websockets`, `api`), docs.goswift.ly realtime-standalone pages, goswift.ly/api-license-agreement, transit.land feed pages and the Transitland Atlas registry, mobilitydatabase.org feed pages and feeds_v2.csv, gtfs.bigbluebus.com and its terms page, culvercity.gov GIS download page, ladottransit.com/about-us/dla, metrolinktrains.com/about/gtfs and gtfs-rt-access.
