"""Dev portal entry; deploy beside an exact reviewed copy of dev_portal_bridge.py.

Run through the existing portal dev_launcher.py. No Records application path is
added to sys.path, so its app.py cannot shadow the existing portal application.
"""
import importlib.util
import logging
import os
from pathlib import Path


def enable_records(portal_module, bridge_path=None):
    if os.environ.get('BASE_URL') != 'https://dev.minimoi.ai':
        return False
    path = Path(bridge_path) if bridge_path else Path(__file__).with_name('dev_portal_bridge.py')
    try:
        spec = importlib.util.spec_from_file_location('minimoi_records_dev_bridge', path)
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        bridge.install(portal_module.app, portal_module._require_login,
                       portal_module._require_owner)
    except Exception:
        # If registration failed partway, no partial Records route may forward.
        # Keep the existing portal alive even when this optional module is bad.
        for endpoint in tuple(portal_module.app.view_functions):
            if endpoint.startswith('records_dev_'):
                portal_module.app.view_functions[endpoint] = lambda **kwargs: ('Records unavailable', 503)
        logging.getLogger(__name__).exception('Optional Records dev bridge disabled; portal continues')
        return False
    return True


def main():
    from minimoi_portal import app as portal_module
    enable_records(portal_module)
    portal_module.main()


if __name__ == '__main__':
    main()
