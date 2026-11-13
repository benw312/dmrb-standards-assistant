"""REST endpoints for the engineer review queue (review/queue.py).

    GET  /reviews?status=pending     what's waiting, oldest first
    GET  /reviews/{id}               one item, with its verdict if it has one
    POST /reviews/{id}/verdict       record a verdict, once

Errors: 404 no such review; 409 it already has a verdict (the first one stands); 422 a bad
verdict (a correction without a note, an unreadable citation); 503 the queue can't be
read or written.

As with POST /ask, authentication belongs in front of the service: the reviewer's name is
taken on trust here, which is right for a single-user tool and wrong for a shared one.
"""

import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from review.queue import (
    MAX_LISTED,
    REVIEW_DB,
    AlreadyResolvedError,
    Flagged,
    QueueStats,
    ReviewError,
    ReviewItem,
    ReviewNotFoundError,
    ReviewQueue,
    Status,
    Verdict,
)

logger = logging.getLogger(__name__)

# Where the queue file lives: data/reviews.sqlite unless REVIEW_DB says otherwise (a mounted
# volume in a container; a temp file in tests, including ones that start a subprocess).
_QUEUE = ReviewQueue(Path(os.environ.get("REVIEW_DB") or REVIEW_DB))


def get_queue() -> ReviewQueue:
    """The one queue shared by REST, the demo and the MCP server. A FastAPI dependency."""
    return _QUEUE


class ReviewOut(BaseModel):
    id: int
    status: Status
    created_at: str
    resolved_at: str | None
    flagged: Flagged
    verdict: Verdict | None


class ReviewList(BaseModel):
    stats: QueueStats
    reviews: list[ReviewOut]


def _out(item: ReviewItem) -> ReviewOut:
    return ReviewOut(
        id=item.id,
        status=item.status,
        created_at=item.created_at.isoformat(),
        resolved_at=item.resolved_at.isoformat() if item.resolved_at else None,
        flagged=item.flagged,
        verdict=item.verdict,
    )


def _unavailable(err: ReviewError) -> HTTPException:
    logger.error("Review queue unavailable: %s", err)  # the message names a local path
    return HTTPException(status_code=503, detail="The review queue is unavailable.")


router = APIRouter(prefix="/reviews", tags=["review"])
QueueDep = Annotated[ReviewQueue, Depends(get_queue)]


# Plain `def` endpoints: SQLite calls block, so FastAPI runs them in worker threads.
@router.get("", response_model=ReviewList, summary="Answers waiting for, or given, a verdict")
def list_reviews(
    queue: QueueDep,
    status: Status | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LISTED)] = 50,
) -> ReviewList:
    try:
        return ReviewList(stats=queue.stats(), reviews=[_out(i) for i in queue.list(status, limit)])
    except ReviewError as err:
        raise _unavailable(err) from err


@router.get("/{review_id}", response_model=ReviewOut, summary="One review")
def get_review(review_id: int, queue: QueueDep) -> ReviewOut:
    try:
        return _out(queue.get(review_id))
    except ReviewNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except ReviewError as err:
        raise _unavailable(err) from err


@router.post(
    "/{review_id}/verdict",
    response_model=ReviewOut,
    summary="Record the engineer's verdict (once)",
    responses={404: {"description": "No such review"}, 409: {"description": "Already resolved"}},
)
def give_verdict(review_id: int, verdict: Verdict, queue: QueueDep) -> ReviewOut:
    try:
        return _out(queue.resolve(review_id, verdict))
    except ReviewNotFoundError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except AlreadyResolvedError as err:
        raise HTTPException(status_code=409, detail=str(err)) from err
    except ReviewError as err:
        raise _unavailable(err) from err
