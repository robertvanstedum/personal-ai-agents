"""CoS inference exclusion shared by normal Confer turns and service reviews.

Opt-in only until the return adapters are deployed. The lock protects inference
ordering, not action authority or canonical history writes. Review consumers use
nonblocking hold and must also give pending owner turns priority.
"""
from contextlib import contextmanager
from functools import wraps
import fcntl
import hashlib
import os
from pathlib import Path


class Lane:
    def __init__(self, root, *, environment, lead):
        if lead != 'cos' or not environment:
            raise ValueError('configured CoS lane required')
        self.root=Path(root)
        if not self.root.is_absolute():
            raise ValueError('absolute persistent lane directory required')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.path=self.root/(hashlib.sha256((environment+':'+lead).encode()).hexdigest()+'.lock')

    @contextmanager
    def hold(self, *, blocking):
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
        acquired=False
        try:
            try:
                fcntl.flock(fd,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
                acquired=True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            if acquired:fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)


def configured_cos_lane(environ=None):
    env=os.environ if environ is None else environ
    if env.get('COS_LEAD_WAKE')!='1':
        return None
    root=env.get('COS_EXECUTION_LANE_DIR')
    environment=env.get('COS_STORE_ENVIRONMENT')
    if not root or not environment:
        raise RuntimeError('CoS continuation lane is not configured')
    return Lane(root,environment=environment,lead='cos')


def serialize_cos(function):
    @wraps(function)
    def wrapped(*args,**kwargs):
        lane=configured_cos_lane()
        if lane is None:return function(*args,**kwargs)
        with lane.hold(blocking=True):
            return function(*args,**kwargs)
    return wrapped
