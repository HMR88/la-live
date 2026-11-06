# LA open traffic data: inventory (Caltrans District 7, CHP, city and county)

Date: 2026-11-05, about 20:15 to 20:25 UTC, fetched by a research agent. "Confirmed" means read from a page or feed fetched that day. Downloaded copies sit in the session scratch folder, not the repo.

## The one-line result

There is no open live source of arterial speeds in Los Angeles. PeMS is login-walled and its real-time feeds need Caltrans approval. The open live sources are: a deprecated but live Caltrans ATMS text feed of freeway detector speeds every minute, 592 Caltrans cameras with 2-minute stills and 486 live HLS video streams, CHP incidents every minute, lane closures every 5 minutes, message signs every minute.

## Sources

| Source | URL | Content | Interval | Access | Terms | Status |
|---|---|---|---|---|---|---|
| Caltrans PeMS | pems.dot.ca.gov | Detector data from "over 39,000 individual detectors", incidents, closures, ADUS archive | not stated on any Caltrans page | Free account, approved in 1 to 2 business days; "You can not extract the real-time data via the web site"; real-time feeds need PeMS Support approval as a Value Added Reseller | Conditions of use say public domain unless indicated | Confirmed login wall; no public live endpoint exists |
| Caltrans ATMS D07 (deprecated, live) | cwwp2.dot.ca.gov/data/d7/atms/webupdate.txt and webinit.txt | webupdate: 2,128 VDS rows `VDS <id> <colour> <speed mph> <volume per 30 s> <% good lanes>` plus 179 CMS rows and a `### Created:` line; 310 of 2,128 had speed > 0 at fetch time. webinit: per VDS id, site name, route, direction, postmile, State Plane X/Y, longitude and latitude to two decimals | about once a minute (observed) | Open, no key | Caltrans conditions of use; page says the files are deprecated and "should not be used for new software development" | Confirmed live |
| Caltrans CCTV D07 | cwwp2.dot.ca.gov/data/d7/cctv/cctvStatusD07.json (also csv, xml, txt) | 592 cameras: location (route, direction, postmile, lon, lat, county), inService (487 true), still image URL with `currentImageUpdateFrequency` (2 minutes for 573, 60 for 19), 12 reference images, `streamingVideoURL` for 486 of the form wzmedia.dot.ca.gov/D7/CCTV-<n>.stream/playlist.m3u8 | stills every 2 minutes (observed); status file "as necessary" | Open, no key; streams send `Access-Control-Allow-Origin: *` | Caltrans conditions of use; photographs "may require additional permissions"; "Usage that risks degrading the availability of the CCTV streaming service is prohibited"; footage is not retained | Confirmed; two of four sampled streams answered (CCTV-196, CCTV-17 at 1280x720 H.264), two returned 404 |
| Caltrans CMS D07 | cwwp2.dot.ca.gov/data/d7/cms/cmsStatusD07.json | Message sign text and location | once a minute | Open | Caltrans conditions of use | Confirmed |
| Caltrans LCS D07 | cwwp2.dot.ca.gov/data/d7/lcs/lcsStatusD07.json (13.7 MB) | 4,499 planned and current lane, ramp and road closures with begin and end lon/lat, lanes closed, type of work, estimated delay | every 5 minutes | Open | Caltrans conditions of use | Confirmed |
| Caltrans chain controls D07 | cwwp2.dot.ca.gov/data/d7/cc/ccStatusD07.json | 37 records | once a minute | Open | same | Confirmed, irrelevant to the corridor |
| Caltrans travel times, RWIS D07 | tt and rwis feeds | not published for District 7 (HTTP 500; the page lists D03, D08, D11, D12 only) | | | | Confirmed absent |
| CHP incidents | media.chp.ca.gov/sa_xml/sa.xml | Statewide XML: Center, Dispatch (LACC = Los Angeles), Log with LogTime, LogType ("1125-Traffic Hazard"), Location, Area, LATLON as "33949745:118170477" (degrees times 1e6, longitude sign dropped), details and units. All values wrapped in literal double quotes | about once a minute (observed) | Open, no key | No terms found | Confirmed; the server serves the file cut in busy hours (2026-11-06: HEAD says 229 KB, every GET stops at 151,552 bytes, ranges past that return 416). The LACC block sat whole inside the cut, so the parser reads that block alone |
| Caltrans QuickMap | quickmap.dot.ca.gov | A map over the feeds above; its speed layer is Google's traffic layer | | no API | | Confirmed: nothing open beyond the feeds above |
| LADOT ATSAC arterials | | no open feed found | | | | Not confirmed |
| LA City, LA County open data | data.lacity.org, geohub.lacity.org, egis-lacounty.hub.arcgis.com | Historical intersection counts (2020, 2023) and one week of 2015 mean speeds; live road-closure points for the county | static | Open | | Confirmed historical only |
| LA Metro RIITS | riits.net | SCAG says an XML feed with "a limited amount of arterial traffic data" exists | | unknown; the site's TLS certificate is wrong | | Not confirmed |

## Facts that shape the design

- Freeway speeds: use the ATMS webupdate feed and say that it is deprecated. Station positions in webinit are coarse (two decimals of a degree, about 1 km); matching to freeway segments has to use route, direction and postmile, or the State Plane coordinates if they prove finer. PeMS can still supply the historical baseline once an account exists, by hand export.
- Cameras: 2-minute stills give counts and density per camera. The HLS streams give video, which allows tracking, line crossing counts and, with a camera-to-ground calibration, speed. Holding hundreds of streams open would breach the usage rule; sample a corridor subset for a few seconds at a time.
- Incidents and closures are point and segment events with coordinates, usable as is after parsing the quoted XML and the LATLON encoding.
- Arterial speeds have to come from transit vehicles (see the transit inventory). Nothing else is open.

## Pages fetched

pems.dot.ca.gov (home, tou, apply, Clearinghouse), dot.ca.gov pems-source and conditions-of-use, cwwp2.dot.ca.gov home and the CCTV, lane closure, CMS, chain control, travel time, ATMS documentation pages, the D07 cctv/cms/lcs/cc/atms feeds, one camera image (HEAD five times), four HLS playlists, media.chp.ca.gov/sa_xml/sa.xml (three times), cad.chp.ca.gov/Traffic.aspx, quickmap.dot.ca.gov and its FAQ, the LA City, GeoHub and LA County catalog APIs, trafficinfo.lacity.org (redirect), ladot.lacity.gov ATSAC page (403), scag.ca.gov data exchange archive, riits.net (TLS mismatch).
