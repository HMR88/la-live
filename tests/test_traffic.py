"""Parsers for the Caltrans and CHP sources on small samples shaped like the
live files of 2026-11-06, plus the detector-to-segment join."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from shapely.geometry import LineString

from poller.__main__ import detector_rows
from poller.base import BaseData, Detector, Segment
from poller.traffic import Closure, parse_chp, parse_cms, parse_latlon, parse_lcs, parse_webupdate

PACIFIC = ZoneInfo("America/Los_Angeles")

WEBUPDATE = """VDS  767494  0  0   0   0
VDS  767541  2 70   4 100
VDS  716933  2 59  13  60
VDS  759906  0 78   3 100
VDS  700001  1  9   2  50
CMS  767472 "MOVE OVER OR" "SLOW DOWN FOR" "CRASH RESPONDERS" "" "" ""
### Created: Thu Nov  5 21:34:37 2026
"""

SA_XML = """<?xml version="1.0" ?>
<State><Center ID = "SAHB">
<Dispatch ID = "STCC">
\t\t<Log ID = "261105ST0401">
\t\t\t<LogTime>"Nov  5 2026  9:32PM"</LogTime>
\t\t\t<LogType>"1125-Traffic Hazard"</LogType>
\t\t\t<Location>"Sr99 N / Sr12 W Ofr"</Location>
\t\t\t<LocationDesc>"NB 99 JSO"</LocationDesc>
\t\t\t<Area>"Stockton"</Area>
\t\t\t<ThomasBrothers>""</ThomasBrothers>
\t\t\t<LATLON>"38113088:121258492"</LATLON>
\t\t\t<LogDetails><details/></LogDetails></Log>
</Dispatch>
<Dispatch ID = "LACC">
\t\t<Log ID = "261105LA2719">
\t\t\t<LogTime>"Nov  5 2026  9:31PM"</LogTime>
\t\t\t<LogType>"1125-Traffic Hazard"</LogType>
\t\t\t<Location>"I10 E / S Hoover St"</Location>
\t\t\t<LocationDesc>"I10 E / S HOOVER ST"</LocationDesc>
\t\t\t<Area>"Central LA"</Area>
\t\t\t<ThomasBrothers>""</ThomasBrothers>
\t\t\t<LATLON>"34036997:118283991"</LATLON>
\t\t\t<LogDetails>
\t\t<details>
\t\t\t<DetailTime>"Nov  5 2026  9:33PM"</DetailTime>
\t\t\t<IncidentDetail>"[1] LADDER IN #2 LN"</IncidentDetail>
\t\t</details></LogDetails></Log>
</Dispatch>
</Center>
</State>
"""


def test_webupdate_parser_reads_vds_rows_and_created_time() -> None:
    snap = parse_webupdate(WEBUPDATE)
    assert snap.created_ts == int(datetime(2026, 11, 5, 21, 34, 37, tzinfo=PACIFIC).timestamp())
    assert len(snap.readings) == 5  # the CMS row is not a reading
    assert not snap.readings[767494].valid  # colour 0, speed 0: no data
    assert snap.readings[767541].valid and abs(snap.readings[767541].speed_kmh - 112.65) < 0.01
    assert snap.readings[716933].good_lanes_pct == 60 and snap.readings[716933].volume_30s == 13
    assert snap.readings[759906].valid  # colour 0 but a speed and good lanes: kept
    assert snap.readings[700001].colour == 1 and snap.readings[700001].speed_mph == 9
    with pytest.raises(ValueError):
        parse_webupdate(WEBUPDATE.split("###")[0])  # no Created line: truncated


def test_chp_parser_unquotes_values_decodes_latlon_and_filters_dispatch() -> None:
    assert parse_latlon("33949745:118170477") == (-118.170477, 33.949745)
    logs = parse_chp(SA_XML)
    assert len(logs) == 1
    log = logs[0]
    assert (log.log_id, log.log_type, log.location, log.area) == ("261105LA2719", "1125-Traffic Hazard", "I10 E / S Hoover St", "Central LA")
    assert (log.lon, log.lat) == (-118.283991, 34.036997)
    assert log.ts == int(datetime(2026, 11, 5, 21, 31, tzinfo=PACIFIC).timestamp())
    assert log.details == ("[1] LADDER IN #2 LN",)
    assert len(parse_chp(SA_XML, dispatch="STCC")) == 1


def test_chp_cut_file_still_yields_the_complete_dispatch_block() -> None:
    """The CHP server serves sa.xml cut at about 150 KB in busy hours; the block of one
    dispatch centre is parsed on its own, so a cut after its closing tag loses nothing."""
    cut_after_lacc = SA_XML[: SA_XML.index("</Center>")]
    assert [i.log_id for i in parse_chp(cut_after_lacc)] == ["261105LA2719"]
    assert [i.log_id for i in parse_chp(SA_XML.replace("</Center>\n", ""))] == ["261105LA2719"]


def test_chp_cut_inside_the_dispatch_block_keeps_the_logs_before_the_cut() -> None:
    second = SA_XML[SA_XML.index('\t\t<Log ID = "261105LA2719">'): SA_XML.index("</Log>\n</Dispatch>\n</Center>") + len("</Log>\n")].replace("261105LA2719", "261105LA2720")
    two_logs = SA_XML.replace("</Log>\n</Dispatch>\n</Center>", "</Log>\n" + second + "</Dispatch>\n</Center>")
    assert [i.log_id for i in parse_chp(two_logs)] == ["261105LA2719", "261105LA2720"]
    cut_mid_second = two_logs[: two_logs.index("261105LA2720") + 40]
    assert [i.log_id for i in parse_chp(cut_mid_second)] == ["261105LA2719"]


def test_chp_cut_before_the_dispatch_block_is_rejected() -> None:
    with pytest.raises(ValueError, match="LACC"):
        parse_chp(SA_XML[: SA_XML.index('<Dispatch ID = "LACC">')])


def closure_item(cid: str, start: int, end: int | None, code1098: bool = False) -> dict:
    ts = {"closureStartEpoch": str(start), "closureEndEpoch": "" if end is None else str(end),
          "isClosureEndIndefinite": "true" if end is None else "false"}
    return {"lcs": {"location": {"travelFlowDirection": "North",
                                 "begin": {"beginLongitude": "-118.40", "beginLatitude": "34.00", "beginRoute": "I-405", "beginLocationName": "A"},
                                 "end": {"endLongitude": "-118.41", "endLatitude": "34.01", "endLocationName": "B"}},
                    "closure": {"closureID": cid, "closureTimestamp": ts, "facility": "Mainline", "typeOfClosure": "Lane",
                                "typeOfWork": "Paving", "lanesClosed": "1",
                                "code1097": {"isCode1097": "true"}, "code1098": {"isCode1098": "true" if code1098 else "false"}}}}


def test_closure_filter_current_versus_future_past_indefinite_and_ended() -> None:
    now = 1_794_000_000
    closures = parse_lcs({"data": [
        closure_item("current", now - 3600, now + 3600),
        closure_item("future", now + 60, now + 7200),
        closure_item("past", now - 7200, now - 60),
        closure_item("indefinite", now - 60, None),
        closure_item("ended_early", now - 3600, now + 3600, code1098=True),
    ]})
    assert [c.closure_id for c in closures if c.is_current(now)] == ["current", "indefinite"]
    cur = next(c for c in closures if c.closure_id == "current")
    assert (cur.lon0, cur.lat0, cur.lon1, cur.lat1, cur.route, cur.direction, cur.in_effect) == (-118.40, 34.00, -118.41, 34.01, "I-405", "North", True)
    assert isinstance(cur, Closure)


def test_cms_parser_joins_message_lines() -> None:
    signs = parse_cms({"data": [{"cms": {"index": "7", "recordTimestamp": {"recordDate": "2026-11-05", "recordTime": "21:33:34"},
                                         "location": {"longitude": "-118.37", "latitude": "33.91", "route": "I-405", "direction": "North", "locationName": "X"},
                                         "inService": "true",
                                         "message": {"display": "1 Page (Normal)",
                                                     "phase1": {"phase1Line1": "MOVE OVER OR", "phase1Line2": "SLOW DOWN FOR", "phase1Line3": "CRASH RESPONDERS"},
                                                     "phase2": {"phase2Line1": "", "phase2Line2": "", "phase2Line3": ""}}}}]})
    assert len(signs) == 1 and signs[0].in_service and signs[0].text == "MOVE OVER OR / SLOW DOWN FOR / CRASH RESPONDERS"
    assert signs[0].record_ts == int(datetime(2026, 11, 5, 21, 33, 34, tzinfo=PACIFIC).timestamp())


def test_detector_readings_become_segment_rows_only_with_a_segment() -> None:
    seg = Segment(2614, 1, "motorway", "Golden State Freeway", 300, LineString([(0, 0), (300, 0)]))
    base = BaseData(segments={2614: seg}, shapes={}, stops=[], trips={}, detectors={
        718371: Detector(718371, 2614, -118.23, 34.09, "5", "N", 21.47, "DORRIS"),
        767541: Detector(767541, None, -118.19, 34.08, "2", "E", 16.4, "34TH AVE"),
        767494: Detector(767494, 2614, -118.19, 34.08, "2", "E", 15.2, "E/O ROUTE 5"),
    })
    snap = parse_webupdate("VDS  718371  2 65  10  80\nVDS  767541  2 70   4 100\nVDS  767494  0  0   0   0\n### Created: Thu Nov  5 21:34:37 2026\n")
    rows = detector_rows(base, snap)
    assert len(rows) == 1
    r = rows[0]
    assert (r.segment_id, r.source, r.weight, r.vehicle_id, r.agency, r.ts) == (2614, "vds", 1.0, "718371", "caltrans", snap.created_ts)
    assert abs(r.speed_kmh - 104.6) < 0.1
