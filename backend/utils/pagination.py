from math import ceil

from flask import request


def parse_pagination(default_limit=25, max_limit=100):
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1

    try:
        limit = int(request.args.get("limit", default_limit))
    except (TypeError, ValueError):
        limit = default_limit

    page = max(page, 1)
    limit = min(max(limit, 1), max_limit)

    return page, limit


def clamp_page(page, total, limit):
    total_pages = ceil(total / limit) if total > 0 else 0
    if total_pages and page > total_pages:
        page = total_pages
    skip = (page - 1) * limit if total_pages else 0
    return page, skip, total_pages


def pagination_payload(total, page, limit, total_pages):
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "totalPages": total_pages,
    }
