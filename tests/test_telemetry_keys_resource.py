from dataclasses import fields

from iagent.models import IRSDKVars


def test_telemetry_keys_have_descriptions():
    keys = {f.name: (f.metadata.get('desc') if f.metadata else None) for f in fields(IRSDKVars)}
    # spot-check a few well-known keys
    assert 'RPM' in keys
    assert keys['RPM'] == 'Engine rpm, revs/min'
    assert 'Speed' in keys
    assert isinstance(keys['Speed'], str) or keys['Speed'] is None
    # ensure there are many keys
    assert len(keys) > 100
