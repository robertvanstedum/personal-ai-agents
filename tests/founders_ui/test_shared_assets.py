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


def test_shared_templates_preserve_application_loader_and_sidebar_hooks():
    from flask import render_template_string
    from jinja2 import DictLoader
    app = Flask(__name__)
    app.jinja_loader = DictLoader({'local.html': 'Application template'})
    install(app)
    with app.app_context():
        assert app.jinja_env.get_template('local.html').render() == 'Application template'
        html = render_template_string("""
          {% from 'founders_ui/components.html' import sidebar_tools %}
          {{ sidebar_tools({'id':'conversation-new','data-new-conversation':''},
             {'data-list-fold':''}, {'data-list-filter':'','aria-label':'Search chats'},
             new_label=label, disabled=true) }}
        """, label='<script>unsafe</script>')
    assert 'id="conversation-new"' in html
    assert 'data-new-conversation=""' in html
    assert 'data-list-fold=""' in html
    assert 'data-list-filter=""' in html
    assert ' disabled' in html
    assert '<script>unsafe</script>' not in html
    assert '&lt;script&gt;' in html


def test_shared_header_preserves_direct_child_mobile_navigation_hooks():
    from flask import render_template_string
    from html.parser import HTMLParser
    class Elements(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.children = []
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if self.stack and self.stack[-1] == ('div', 'mm-domain-row header-nav'):
                self.children.append((tag, attrs))
            self.stack.append((tag, attrs.get('class', '')))
        def handle_endtag(self, tag):
            if self.stack:
                self.stack.pop()
    app = Flask(__name__)
    install(app)
    with app.app_context():
        html = render_template_string("""
        {% from 'founders_ui/components.html' import domain_header %}
        {% call(slot) domain_header('header-nav',
          [{'label':'Confer','classes':'tab-btn','attrs':{'data-tab':'confer'}}],
          'tab-nav','Chief of Staff') %}
          {% if slot == 'brand' %}<a class="brand" href="/ui/home">CoS</a>
          {% else %}<a href="/guild-next/guild/rooms" data-portal-link>Guildhall</a>{% endif %}
        {% endcall %}
        """)
    parser = Elements()
    parser.feed(html)
    assert any(tag == 'nav' and 'tab-nav' in attrs.get('class','').split()
               for tag, attrs in parser.children)
    assert any(tag == 'a' and 'data-portal-link' in attrs for tag, attrs in parser.children)
    assert 'data-tab="confer"' in html
