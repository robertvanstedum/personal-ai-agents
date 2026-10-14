"""Ask (M4): the one agent-neutral way to ask the memory a question (v0.5 Component D; owner direction 4 Oct 2026).

``ask`` is the single procedure: the index's search (two labelled lists), the adjacent slot, and one line in the query
log. ``moi ask``, Master Craftsman and the Chief of Staff are callers of it and get the same result shape; they differ
only in the caller identity the host binds (never the model's own say-so) and in what the log records.

See ``ask.py`` (the procedure), ``tool.py`` (the contract agents share), ``querylog.py``, ``citation.py``,
``canary_check.py`` and ``cli.py`` (``moi ask``). Nothing is re-exported here: ``ask`` is both a module and a function.
"""
