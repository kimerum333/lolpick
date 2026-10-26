"""Bound JSON bytes while consuming an HTTPX streaming response."""
import json
import math


class InvalidUpstreamJSON(ValueError):
    pass


def read_json(response, limit, *, check_deadline=None):
    # Do not let automatic decompression allocate an unbounded decoded body.
    # Both clients request identity; unexpected compression fails before reading.
    if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
        raise InvalidUpstreamJSON("Compressed upstream JSON is not accepted.")
    length = response.headers.get("Content-Length")
    if length is not None and (
        not length.isascii() or not length.isdigit()
        or len(length) > len(str(limit)) or int(length) > limit
    ):
        raise InvalidUpstreamJSON("Upstream response exceeds the accepted size.")
    body = bytearray()
    for chunk in response.iter_bytes(chunk_size=65536):
        if check_deadline is not None:
            check_deadline()
        if len(body) + len(chunk) > limit:
            raise InvalidUpstreamJSON("Upstream response exceeds the accepted size.")
        body.extend(chunk)
    try:
        return json.loads(body, object_pairs_hook=unique_pairs, parse_constant=invalid_constant,
                          parse_float=finite_float)
    except (ValueError, UnicodeError, RecursionError):
        raise InvalidUpstreamJSON("Invalid upstream JSON.") from None


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidUpstreamJSON("Duplicate upstream field.")
        result[key] = value
    return result


def invalid_constant(value):
    raise InvalidUpstreamJSON("Non-finite upstream JSON value.")


def finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InvalidUpstreamJSON("Non-finite upstream JSON value.")
    return parsed
