"""Single-request worker. Its parent owns the deadline and reaps this process."""

from __future__ import annotations

import json
import sys
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, OpenerDirector, ProxyHandler, Request, build_opener


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def post_json(
    url: str, payload: dict[str, object], *, timeout_seconds: float,
    opener: OpenerDirector | None = None,
) -> tuple[int, str | None]:
    """Send once without proxies/redirects and never read response content."""
    transport = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())
    request = Request(
        url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with transport.open(request, timeout=timeout_seconds) as response:
            return response.status, response.headers.get("Retry-After")
    except HTTPError as error:
        try:
            return error.code, error.headers.get("Retry-After")
        finally:
            error.close()


def main() -> None:
    try:
        request = json.load(sys.stdin)
        status, retry_after = post_json(**request)
        # Never forward arbitrary response bodies, exception text, or long headers.
        if retry_after is not None and len(retry_after) > 64:
            retry_after = None
        print(json.dumps([status, retry_after]))
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
