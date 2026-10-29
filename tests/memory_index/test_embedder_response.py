"""Malformed local-provider replies use the adapter's unavailable contract."""
import io
import json
import math

import pytest
from core.memory_index import embedder as E, indexer
from tests.memory_index.helpers import FakeEmbedder, world


def replies(monkeypatch, value):
    calls = []
    def urlopen(request, **kwargs):
        calls.append((request, kwargs))
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(E.urllib.request, 'urlopen', urlopen)
    return calls


@pytest.mark.parametrize('value', [None, [], 'bad', 1, True])
@pytest.mark.parametrize('operation', ['identity', 'embedding'])
def test_non_object_response(monkeypatch, value, operation):
    replies(monkeypatch, value)
    adapter = E.OllamaEmbedder()
    with pytest.raises(E.EmbedderUnavailable):
        adapter.identity() if operation == 'identity' else adapter.embed_query('synthetic')


@pytest.mark.parametrize('models', [1, 'bad', {}, [None], [1], ['bad']])
def test_malformed_models(monkeypatch, models):
    replies(monkeypatch, {'models': models})
    with pytest.raises(E.EmbedderUnavailable):
        E.OllamaEmbedder().identity()


@pytest.mark.parametrize('vector', [None, 1, 'x' * E.DIMS, {}, [1] * (E.DIMS - 1),
                                    [True] * E.DIMS, ['1'] * E.DIMS,
                                    [None] * E.DIMS, [float('nan')] * E.DIMS,
                                    [float('inf')] * E.DIMS, [10 ** 400] * E.DIMS,
                                    [10 ** 200] + [0] * (E.DIMS - 1),
                                    [0] * E.DIMS])
def test_invalid_vector(monkeypatch, vector):
    replies(monkeypatch, {'embeddings': [vector]})
    with pytest.raises(E.EmbedderUnavailable):
        E.OllamaEmbedder().embed_query('synthetic')


def test_valid_vector_and_request_preserved(monkeypatch):
    vector = [3, 4] + [0] * (E.DIMS - 2)
    calls = replies(monkeypatch, {'embeddings': [vector]})
    adapter = E.OllamaEmbedder()
    result = adapter.embed_documents(['synthetic'], 40)
    assert result == [[.6, .8] + [0.0] * (E.DIMS - 2)]
    payload = json.loads(calls[0][0].data)
    assert payload == {'model': E.PIN_NAME + ':latest', 'input': ['search_document: synthetic'],
                       'truncate': True, 'keep_alive': E.RUN_KEEP_ALIVE}
    assert math.isclose(sum(x * x for x in result[0]), 1)


def test_valid_model_and_pin_preserved(monkeypatch):
    replies(monkeypatch, {'models': [{'name': E.PIN_NAME + ':latest', 'digest': E.PIN_DIGEST + 'f' * 52}]})
    adapter = E.OllamaEmbedder()
    assert adapter.check_pin().dims == E.DIMS
    assert adapter.identity() is adapter.identity()


@pytest.mark.parametrize('bad_vector', [None, [10 ** 200] + [0] * (E.DIMS - 1)])
def test_bad_reply_preserves_old_index_on_rebuild(tmp_path, monkeypatch, bad_vector):
    shelf, _ = world(tmp_path)
    assert indexer.update(shelf.root, FakeEmbedder(), min_free_bytes=0)['code'] == 'ok'
    path = indexer.default_index_path(shelf.root)
    before = path.read_bytes()
    adapter = E.OllamaEmbedder()
    adapter._ident = E.Identity(E.PIN_NAME + ':latest', E.PIN_DIGEST + 'f' * 52, E.DIMS)
    def malformed_batch(request, **kwargs):
        count = len(json.loads(request.data)['input'])
        return io.BytesIO(json.dumps({'embeddings': [bad_vector] * count}).encode())
    monkeypatch.setattr(E.urllib.request, 'urlopen', malformed_batch)
    result = indexer.rebuild(shelf.root, adapter, min_free_bytes=0)
    assert result['code'] == 'embedder_unavailable' and result['swapped'] is False
    assert path.read_bytes() == before
    assert not list(path.parent.glob('*.rebuild-*'))
