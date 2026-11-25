"""The real embedder client's identity check (#660): the full pinned digest, a known-answer probe, and the idle keep-alive.

No Ollama runs here: the HTTP calls are answered by a stand-in, so the client's own checks are what is tested."""
import io
import json
import math

import pytest

from core.memory_index import embedder as E


def server(monkeypatch, *, digest=E.PIN_DIGEST_FULL, name=E.PIN_NAME + ':latest', vector=None, dims=E.DIMS):
    """A stand-in Ollama: /api/tags names one model; /api/embed answers ``vector`` (default: the probe's reference)."""
    _text, reference, _floor = E.load_probe()
    sent = []

    def urlopen(request, **kwargs):
        body = json.loads(request.data) if request.data else None
        sent.append((request.full_url, body))
        if request.full_url.endswith('/api/tags'):
            return io.BytesIO(json.dumps({'models': [{'name': name, 'digest': digest}]}).encode())
        vec = list(vector if vector is not None else reference)[:dims]
        return io.BytesIO(json.dumps({'embeddings': [vec for _ in body['input']]}).encode())

    monkeypatch.setattr(E.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(E, '_PROBED', set())
    return sent


def test_pinned_model_passes_and_probes_once(monkeypatch):
    sent = server(monkeypatch)
    assert E.OllamaEmbedder().check_pin().digest == E.PIN_DIGEST_FULL
    assert E.OllamaEmbedder().check_pin().label() == 'nomic-embed-text:latest@0a109f422b47'     # a second client: no second probe
    probes = [body for url, body in sent if url.endswith('/api/embed')]
    assert len(probes) == 1 and probes[0]['input'] == [E.load_probe()[0]]


@pytest.mark.parametrize('digest', [E.PIN_DIGEST + 'f' * 52, E.PIN_DIGEST, 'a' * 64])
def test_another_or_shortened_digest_is_refused(monkeypatch, digest):
    server(monkeypatch, digest=digest)
    with pytest.raises(E.PinMismatch):
        E.OllamaEmbedder().check_pin()


def test_another_model_name_is_refused(monkeypatch):
    server(monkeypatch, name='mxbai-embed-large:latest')
    with pytest.raises(E.EmbedderUnavailable):              # the pinned name is not installed on that server at all
        E.OllamaEmbedder().check_pin()


def test_right_digest_other_weights_fail_the_probe(monkeypatch):
    _text, reference, _floor = E.load_probe()
    rotated = reference[1:] + reference[:1]                 # same shape, same norm, another model's answer
    server(monkeypatch, vector=rotated)
    with pytest.raises(E.PinMismatch, match='known-answer probe'):
        E.OllamaEmbedder().check_pin()


def test_slightly_different_cpu_arithmetic_still_passes(monkeypatch):
    _text, reference, _floor = E.load_probe()
    nudged = [x + (1e-4 if i % 2 else -1e-4) for i, x in enumerate(reference)]   # far above float noise between back ends
    assert E.cosine(E.normalize(nudged), reference) > 0.999
    server(monkeypatch, vector=nudged)
    assert E.OllamaEmbedder().check_pin()


def test_wrong_width_is_unavailable_not_trusted(monkeypatch):
    server(monkeypatch, dims=384)
    with pytest.raises(E.EmbedderUnavailable):
        E.OllamaEmbedder().check_pin()


def test_probe_reference_is_the_pinned_model_and_unit_length():
    doc = json.loads(E.PROBE_PATH.read_text())
    assert doc['digest'] == E.PIN_DIGEST_FULL and doc['model'] == E.PIN_NAME + ':latest'
    assert len(doc['vector']) == E.DIMS and doc['min_cosine'] == 0.999
    assert math.isclose(math.sqrt(sum(x * x for x in doc['vector'])), 1.0, abs_tol=1e-5)


def test_malformed_probe_file_refuses(tmp_path):
    bad = tmp_path / 'probe.json'
    bad.write_text(json.dumps({'text': 'x', 'vector': [1.0] * 10, 'min_cosine': 0.999, 'digest': E.PIN_DIGEST_FULL}))
    with pytest.raises(E.PinMismatch):
        E.load_probe(bad)


@pytest.mark.parametrize('value,expected', [(None, 0), ('0', 0), ('15m', '15m'), ('2h', '2h'), ('forever', 0), ('-1', 0)])
def test_release_keep_alive(monkeypatch, value, expected):
    sent = server(monkeypatch)
    if value is None:
        monkeypatch.delenv(E.IDLE_KEEP_ALIVE_ENV, raising=False)
    else:
        monkeypatch.setenv(E.IDLE_KEEP_ALIVE_ENV, value)
    client = E.OllamaEmbedder()
    client.embed_query('synthetic')
    client.release()
    assert sent[-1][1]['keep_alive'] == expected
