import dataclasses
import functools
import json
from json import load, loads, dumps as _dumps


class CustomJSONEncoder(json.JSONEncoder):

    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return None

@functools.wraps(_dumps)
def dumps(obj, **kwargs):
    return json.dumps(obj, cls=CustomJSONEncoder, **kwargs)
