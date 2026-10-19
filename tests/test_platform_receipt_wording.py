from types import SimpleNamespace
from flask import Flask, Blueprint, jsonify
from minimoi_portal.guild_ui import _name_the_partner


def test_platform_saved_text_is_not_rebranded_but_partner_status_is():
    app=Flask(__name__);bp=Blueprint('receipt_test',__name__)
    _name_the_partner(bp,SimpleNamespace(mc=SimpleNamespace(display_name='Chief of Staff')))
    @bp.get('/receipt')
    def receipt():
        return jsonify(backend_kind='platform',message='Saved Board post-it #3: Master Craftsman post-it test',
                       reply_note={'text':'Master Craftsman post-it test'},mc_header='Master Craftsman is ready')
    @bp.get('/status')
    def status():
        return jsonify(backend_kind='cos',message='Master Craftsman answered')
    app.register_blueprint(bp);client=app.test_client()
    data=client.get('/receipt').get_json()
    assert data['message']=='Saved Board post-it #3: Master Craftsman post-it test'
    assert data['reply_note']['text']=='Master Craftsman post-it test'
    assert data['mc_header']=='Chief of Staff is ready'
    assert client.get('/status').get_json()['message']=='Chief of Staff answered'
