"""CoS's own Board: shared Board implementation, separate floor and API scope."""
from dataclasses import replace
from flask import Blueprint, render_template
from . import cfg, owner_page, asset, _headers, _api_error, _asset_url_defaults
from . import api, pages
from .security import csrf_token, OFF_RECORD_TEXT

PREFIX = "/guild-next/cos-board"
NAME = "cos_board"

@owner_page
def view():
    c = cfg()
    res, data = pages._board_payload()
    urls = {"api": PREFIX + "/api/v1", "board": PREFIX,
            "home": "/app/cos/ui/records", "media": "/guild-next/guild/media",
            "build_log": "/guild-next/guild/build/log"}
    page = {"urls": urls, "storage_ns": c["storage_ns"],
            "csrf_token": csrf_token(), "off_record_text": OFF_RECORD_TEXT}
    return render_template("guild_floor/cos_board.html", res=res, board_data=data,
                           urls=urls, page_json=page)

def mount(app, source):
    # No second chat backend, jobs, or agent tools are installed here.
    services = replace(source["services"], floor=source["services"].floor.for_floor("cos-board"))
    app.extensions[NAME] = {**source, "services": services, "blueprint_name": NAME,
                            "url_prefix": PREFIX, "storage_ns": "cos.board"}
    bp = Blueprint(NAME, __name__)
    @bp.after_request
    def headers(response):
        response = _headers(response)
        response.headers["Content-Security-Policy"] = response.headers["Content-Security-Policy"].replace("frame-ancestors 'none'", "frame-ancestors 'self'")
        return response
    bp.register_error_handler(Exception, _api_error)
    bp.url_defaults(_asset_url_defaults)
    bp.add_url_rule("", "board", view)
    bp.add_url_rule("/popout", "board_popout", view)
    bp.add_url_rule("/assets/<path:filename>", "asset", asset)
    from .board_api import media_file
    bp.add_url_rule("/media/<asset_id>/<variant>", "media_file", media_file)
    for rule, endpoint, handler, methods in api.RULES:
        if (rule == "/session" or rule.startswith("/board") or rule.startswith("/postits")
                or rule == "/media"):
            bp.add_url_rule("/api/v1" + rule, endpoint, handler, methods=methods)
    app.register_blueprint(bp, url_prefix=PREFIX)
