import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class RequestFailed(RuntimeError):
    def __init__(self, service: str, status: int | None = None):
        self.status = status
        super().__init__(f"{service}: " + (f"HTTP {status}" if status else "通信または応答形式のエラー"))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.last_yahoo_request = 0.0

    def request(self, service, url, *, method="GET", payload=None, headers=None, retry=False):
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url, data=body, method=method,
            headers={"User-Agent": "yahoo-tire-watcher/1.0", **(headers or {})},
        )
        if body is not None:
            request.add_header("Content-Type", "application/json")

        for attempt in range(2 if retry else 1):
            if service == "Yahoo":
                time.sleep(max(0.0, self.last_yahoo_request + 1.5 - time.monotonic()))
                self.last_yahoo_request = time.monotonic()
            try:
                with self.opener.open(request, timeout=7) as response:
                    data = response.read(4_000_001)
                    if len(data) > 4_000_000:
                        raise RequestFailed(service)
                    return data
            except HTTPError as error:
                failure = RequestFailed(service, error.code)
                error.close()
            except (URLError, OSError):
                failure = RequestFailed(service)
            if not retry or attempt == 1 or failure.status not in (None, 500, 502, 503, 504):
                raise failure
            time.sleep(1)
