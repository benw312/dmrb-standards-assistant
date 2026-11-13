"""Review queue over REST: flagged answers are queued by /ask, then listed and resolved."""

import pytest
from fastapi.testclient import TestClient

from agent.rag import DISCLAIMER
from app.api import AskResponse, create_app, flag_for_review, get_answerer, to_response
from app.review_api import get_queue
from review.queue import ReviewQueue
from tests.test_api import FakeAnswerer, _agent_result
from tests.test_rag import GOOD

ESCALATED = ("Answer 2: citation check failed: CD109-5.9 was not retrieved.",)
CORRECTION = {"outcome": "corrected", "reviewer": "Ben Wallbridge", "note": "4% is the limit."}


@pytest.fixture
def client(review_queue):
    app = create_app()

    def make(result=None) -> TestClient:
        app.dependency_overrides[get_answerer] = lambda: FakeAnswerer(result or _agent_result())
        return TestClient(app)

    yield make
    app.dependency_overrides.clear()


def _ask(client: TestClient) -> dict:
    return client.post("/ask", json={"question": "Max motorway gradient?"}).json()


def test_a_clean_answer_is_not_queued(client, review_queue):
    assert _ask(client())["review_id"] is None
    assert review_queue.list() == []


def test_an_escalated_answer_is_queued_with_why_and_without_the_disclaimer(client, review_queue):
    data = _ask(client(_agent_result(escalation=ESCALATED)))

    (item,) = review_queue.list("pending")
    assert data["review_id"] == item.id
    assert item.flagged.reasons == ESCALATED
    assert item.flagged.request_id == data["request_id"]
    assert item.flagged.citations == ("CD109-5.1", "CD109-Table-5.1")
    assert DISCLAIMER not in item.flagged.answer and item.flagged.answer == GOOD.answer
    assert item.flagged.trace_id == "20261025-000000-abc123"


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"is_departure": True, "needs_human_review": True}, "possible Departure from Standard"),
        ({"confidence": "low", "needs_human_review": True}, "low confidence"),
        ({"needs_human_review": True}, "the model asked for review"),
    ],
)
def test_a_flagged_but_checked_answer_is_queued_with_its_flag(
    client, review_queue, changes, reason
):
    answer = GOOD.model_copy(update=changes)
    _ask(client(_agent_result(answer=answer)))
    assert reason in review_queue.list("pending")[0].flagged.reasons


def test_the_answer_is_still_returned_when_the_queue_cant_be_written(tmp_path, caplog):
    broken = tmp_path / "broken.sqlite"
    broken.write_text("not a database " * 30)
    response = to_response(_agent_result(escalation=ESCALATED), "agent", "req1")

    out = flag_for_review(response, ReviewQueue(broken))

    assert out == response and out.review_id is None and out.needs_human_review
    assert "couldn't add to the review queue" in caplog.text


def test_list_shows_pending_first_in_first_out_with_counts(client):
    c = client(_agent_result(escalation=ESCALATED))
    first, second = _ask(c)["review_id"], _ask(c)["review_id"]

    body = c.get("/reviews", params={"status": "pending"}).json()

    assert [r["id"] for r in body["reviews"]] == [first, second]
    assert body["stats"] == {"pending": 2, "approved": 0, "corrected": 0}
    assert body["reviews"][0]["status"] == "pending" and body["reviews"][0]["verdict"] is None


def test_a_verdict_resolves_the_review_and_shows_in_get(client):
    c = client(_agent_result(escalation=ESCALATED))
    review_id = _ask(c)["review_id"]

    done = c.post(
        f"/reviews/{review_id}/verdict", json=CORRECTION | {"expected": ["CD 109 cl. 5.1"]}
    )

    assert done.status_code == 200
    body = c.get(f"/reviews/{review_id}").json()
    assert body == done.json()
    assert body["status"] == "resolved" and body["resolved_at"]
    assert body["verdict"]["expected"] == ["CD109-5.1"]
    assert c.get("/reviews", params={"status": "pending"}).json()["reviews"] == []
    assert c.get("/reviews").json()["stats"]["corrected"] == 1


def test_a_second_verdict_is_a_409_and_the_first_stands(client):
    c = client(_agent_result(escalation=ESCALATED))
    review_id = _ask(c)["review_id"]
    c.post(f"/reviews/{review_id}/verdict", json=CORRECTION)

    again = c.post(
        f"/reviews/{review_id}/verdict", json={"outcome": "approved", "reviewer": "Someone"}
    )

    assert again.status_code == 409
    assert c.get(f"/reviews/{review_id}").json()["verdict"]["outcome"] == "corrected"


def test_unknown_reviews_are_404(client):
    c = client()
    assert c.get("/reviews/99").status_code == 404
    assert c.post("/reviews/99/verdict", json=CORRECTION).status_code == 404
    assert c.get("/reviews/abc").status_code == 422


@pytest.mark.parametrize(
    "verdict",
    [
        {"outcome": "corrected", "reviewer": "Ben"},
        {"outcome": "approved", "reviewer": ""},
        {"outcome": "approved", "reviewer": "Ben", "expected": ["somewhere in section 5"]},
        {"outcome": "approved"},
    ],
)
def test_a_bad_verdict_is_a_422_and_leaves_the_review_pending(client, verdict):
    c = client(_agent_result(escalation=ESCALATED))
    review_id = _ask(c)["review_id"]
    assert c.post(f"/reviews/{review_id}/verdict", json=verdict).status_code == 422
    assert c.get(f"/reviews/{review_id}").json()["status"] == "pending"


def test_list_limits_are_validated(client):
    c = client()
    assert c.get("/reviews", params={"limit": 0}).status_code == 422
    assert c.get("/reviews", params={"limit": 1000}).status_code == 422
    assert c.get("/reviews", params={"status": "lost"}).status_code == 422


def test_an_unusable_queue_is_a_503_without_the_path(client, tmp_path):
    broken = tmp_path / "secret-folder" / "reviews.sqlite"
    broken.parent.mkdir()
    broken.write_text("not a database " * 30)
    c = client()
    c.app.dependency_overrides[get_queue] = lambda: ReviewQueue(broken)

    for response in (
        c.get("/reviews"),
        c.get("/reviews/1"),
        c.post("/reviews/1/verdict", json=CORRECTION),
    ):
        assert response.status_code == 503
        assert "secret-folder" not in response.text


def test_review_id_is_in_the_published_schema():
    assert "review_id" in AskResponse.model_json_schema()["properties"]
    paths = create_app().openapi()["paths"]
    assert {"/reviews", "/reviews/{review_id}", "/reviews/{review_id}/verdict"} <= set(paths)


def test_the_queue_file_can_be_moved_with_an_environment_variable(tmp_path):
    import os
    import subprocess
    import sys

    target = tmp_path / "elsewhere" / "queue.sqlite"
    code = "from app.review_api import get_queue; print(get_queue().stats().pending)"
    done = subprocess.run(
        [sys.executable, "-c", code],
        env=os.environ | {"REVIEW_DB": str(target)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "0" and target.exists()
