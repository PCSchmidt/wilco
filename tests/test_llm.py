"""The language-model planner client, tested against a stub HTTP server that plays the part of llama.cpp."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from wilco.llm import MISSION_SCHEMA, SYSTEM, LlamaPlanner, feedback_text, limits_text
from wilco.monitor import Veto
from wilco.scenarios import Scenario, generate_library
from wilco.schema import parse_json

SC = next(s for s in generate_library() if isinstance(s, Scenario) and s.kind == "search")
GOOD = {"id": "m", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 50}, {"type": "goto", "x": 0, "y": 100, "alt": 50}, {"type": "return"}]}
BAD = {"id": "m", "lost_link": "rtl", "steps": [{"type": "takeoff", "alt": 50}, {"type": "goto", "x": 9000, "y": 0, "alt": 50}, {"type": "return"}]}


class Stub:
    """Serves a scripted list of replies (strings are returned as the model's text, ints as HTTP error codes)."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                r = outer.replies.pop(0)
                if isinstance(r, int):
                    self.send_response(r)
                    self.end_headers()
                    self.wfile.write(b'{"error":"scripted"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"content": r}}], "usage": {"completion_tokens": 7}}).encode())

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


@pytest.fixture
def stub():
    made = []

    def make(replies):
        s = Stub(replies)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def test_the_schema_accepts_what_the_parser_accepts_and_every_step_type_is_covered():
    kinds = {s["properties"]["type"]["const"] for s in MISSION_SCHEMA["properties"]["steps"]["items"]["oneOf"]}
    assert kinds == {"takeoff", "goto", "search_lawnmower", "orbit", "report", "return"}
    assert parse_json(json.dumps(GOOD)).steps[0].alt == 50
    assert "strike" not in json.dumps(MISSION_SCHEMA) and "engage" not in SYSTEM  # nothing that engages a target


def test_the_request_carries_the_schema_and_the_limits_only_when_asked(stub):
    s = stub([json.dumps(GOOD), json.dumps(GOOD)])
    LlamaPlanner(s.url, with_limits=False).plan(SC)
    LlamaPlanner(s.url, with_limits=True).plan(SC)
    plain, limited = s.requests
    assert plain["response_format"]["schema"] == MISSION_SCHEMA and plain["temperature"] == 0 and plain["seed"] == 0
    assert "Limits for this flight" not in plain["messages"][1]["content"] and "Limits for this flight" in limited["messages"][1]["content"]
    assert SC.text in plain["messages"][1]["content"]


def test_an_approved_first_plan_is_not_questioned(stub):
    s = stub([json.dumps(GOOD)])
    run = LlamaPlanner(s.url, True, max_retries=2).plan(SC)
    assert len(run.attempts) == 1 and run.attempts[0].approved and len(s.requests) == 1


def test_a_vetoed_plan_is_sent_back_with_the_reasons_and_the_second_plan_is_used(stub):
    s = stub([json.dumps(BAD), json.dumps(GOOD)])
    run = LlamaPlanner(s.url, True, max_retries=2).plan(SC)
    assert [a.approved for a in run.attempts] == [False, True]
    follow = s.requests[1]["messages"]
    assert [m["role"] for m in follow] == ["system", "user", "assistant", "user"]
    assert "geofence" in follow[-1]["content"] and follow[2]["content"] == json.dumps(BAD)


def test_corrections_stop_at_the_limit_and_the_last_attempt_is_kept(stub):
    s = stub([json.dumps(BAD)] * 3)
    run = LlamaPlanner(s.url, True, max_retries=2).plan(SC)
    assert len(run.attempts) == 3 and not run.attempts[-1].approved and len(s.requests) == 3


def test_a_server_error_is_recorded_as_a_failed_attempt_not_raised(stub):
    s = stub([400])
    run = LlamaPlanner(s.url, True, max_retries=2).plan(SC)
    assert len(run.attempts) == 1 and run.attempts[0].text == "" and "HTTPError" in run.attempts[0].error and not run.attempts[0].approved


def test_malformed_text_is_a_schema_veto_and_gets_a_correction(stub):
    s = stub(['{"id": "m", "lost_link": "rtl", "steps": [{"type": "takeoff"', json.dumps(GOOD)])
    run = LlamaPlanner(s.url, True, max_retries=1).plan(SC)
    assert run.attempts[0].rules == ("schema",) and run.attempts[1].approved


def test_feedback_is_compact_numeric_and_deduplicated():
    vetoes = [Veto("battery", "reserve", "after step 1 the vehicle could not fly home and keep the reserve", 1, -3.2, 20.0)] * 500 + [Veto("geofence", "leg", "leg leaves the fence", 2)]
    text = feedback_text(vetoes)
    assert len(text) < 700 and text.count("battery") == 1 and "500 times" in text and "-3.2 Wh" in text and "geofence" in text
    assert len(feedback_text(vetoes, max_lines=1).splitlines()) == 3  # header, one reason, request


def test_limits_text_states_the_scenarios_own_numbers():
    t = limits_text(SC, SC.envelope())
    h = SC.cfg["half"]
    assert f"|x| <= {h:.0f}" in t and f"{SC.envelope().alt_min:.0f} and {SC.envelope().alt_max:.0f}" in t and f"{SC.start_energy_wh:.0f} Wh" in t
