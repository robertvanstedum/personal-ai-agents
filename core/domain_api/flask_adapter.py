"""The supported Flask ingress for future domain API routes; installs nothing.

Handlers use the returned raw bytes, never request.args or a second body read.
The domain still owns schema validation and commit-time revalidation.
"""
from .contract import MAX_REQUEST, require


def verify_request(authenticator, request, *, capability):
    require(not request.query_string, 'query_refused')
    # When the server supplies the original target, reject noncanonical aliases
    # as well. WSGI PATH_INFO alone has already undergone URL decoding.
    target = request.environ.get('RAW_URI') or request.environ.get('REQUEST_URI')
    if target is not None:
        require(target == request.path, 'route_invalid')
    require(request.content_length is None or request.content_length <= MAX_REQUEST, 'body_bound', 413)
    raw = request.stream.read(MAX_REQUEST + 1)
    require(len(raw) <= MAX_REQUEST, 'body_bound', 413)
    context = authenticator.verify(method=request.method, route=request.path, raw=raw,
                                   headers=request.headers, capability=capability)
    return context, raw
