"""Candidate Curator research layout: application and private state are distinct.
Pure path resolution; never copies, creates, rewrites or deletes private material.
No application dependency falls back to _NewDomains or _working. Old saved path
SPELLINGS are interpreted in memory so existing dive/history records need no edit.
"""
from dataclasses import dataclass
import os
from pathlib import Path,PurePosixPath

@dataclass(frozen=True)
class ResearchPaths:
    repo:Path
    data:Path
    state:Path

    def __post_init__(self):
        for value in (self.repo,self.data,self.state):
            if not isinstance(value,Path) or not value.is_absolute() or '..' in value.parts:
                raise ValueError('research_path_requires_absolute_root')
        if self.code==self.data or self.code in self.data.parents or self.code==self.state or self.code in self.state.parents:
            raise ValueError('research_private_state_cannot_be_application_code')

    @property
    def code(self):return self.repo/'domains/curator/research'
    @property
    def config(self):return self.state/'agent/config.json'
    @property
    def library(self):return self.state/'library'
    @property
    def topics(self):return self.state/'topics'
    @property
    def tips(self):return self.repo/'config/curator/tips.json'

    def saved(self,value):
        """Resolve only known legacy history namespaces, never arbitrary paths.
        Old absolute container paths are labels, not filesystem access to oldroot.
        """
        if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:
            raise ValueError('research_history_path_invalid')
        path=PurePosixPath(value)
        if '..' in path.parts:raise ValueError('research_history_path_invalid')
        if path.is_absolute():
            prefixes=(PurePosixPath('/app/_NewDomains/research-intelligence'),PurePosixPath(self.repo/'_NewDomains/research-intelligence'))
            for prefix in prefixes:
                if prefix in path.parents:
                    path=path.relative_to(prefix);break
            else:raise ValueError('research_history_path_outside_legacy_scope')
        if len(path.parts)<2:raise ValueError('research_history_path_invalid')
        first,*rest=path.parts
        if first=='data':return self.data.joinpath(*rest)
        if first in {'library','topics'}:return self.state.joinpath(first,*rest)
        if tuple(path.parts)==('agent','config.json'):return self.config
        raise ValueError('research_history_path_namespace_invalid')

    def persisted(self,value):
        """Current absolute private paths or recognized legacy saved labels."""
        if not isinstance(value,str) or not value or '\\' in value or '\x00' in value:
            raise ValueError('research_history_path_invalid')
        path=Path(value)
        if '..' in path.parts:raise ValueError('research_history_path_invalid')
        if path.is_absolute() and (self.data in path.parents or self.state in path.parents):return path
        return self.saved(value)

    def saved_label(self,path):
        """Emit original data/... and topics/... spellings for API compatibility."""
        path=Path(path)
        if '..' in path.parts:raise ValueError('research_history_path_invalid')
        for name in ('topics','library'):
            root=self.state/name
            if root in path.parents:return str(PurePosixPath(name)/path.relative_to(root).as_posix())
        if self.data in path.parents:return str(PurePosixPath('data')/path.relative_to(self.data).as_posix())
        raise ValueError('research_history_path_outside_private_roots')


def configured(environ=None,*,repo=None):
    environ=os.environ if environ is None else environ
    repo=Path(repo) if repo is not None else Path(__file__).resolve().parents[3]
    data=Path(environ.get('CURATOR_RESEARCH_DATA_DIR',str(repo/'data/research-intelligence')))
    state=Path(environ.get('CURATOR_RESEARCH_STATE_DIR',str(data/'state')))
    return ResearchPaths(repo,data,state)
