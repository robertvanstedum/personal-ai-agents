"""The Workshop journal: the durable, file-first record of who asked whom for what, and what came back.

Model free by construction (a test scans this package for model, network and HTTP imports). Public surface is in
``journal`` (the engine), ``schema`` (the v2 envelope), ``reducer`` (derived state) and ``legacy`` (the v1 API that
``minimoi_portal/workshop/record.py`` re-exports).
"""
