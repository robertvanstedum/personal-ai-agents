import re
from flask import Flask
from core.founders_ui import install

def test_shared_fonts_are_delivered_from_the_same_origin():
    app=Flask(__name__)
    install(app)
    client=app.test_client()
    css=client.get('/founders-ui/fonts.css')
    assert css.status_code == 200
    paths=re.findall(r"url\(['\"]?([^)'\"]+)",css.get_data(as_text=True))
    assert len(paths) >= 6
    for path in paths:
        response=client.get('/founders-ui/'+path)
        assert response.status_code == 200, path
        assert len(response.data)>10000
        assert response.data[:4] in (b'\x00\x01\x00\x00',b'OTTO',b'wOF2')
    assert client.get('/founders-ui/../../core/founders_ui/__init__.py').status_code == 404
