"""Stable source fingerprint for an API boot and its immutable run records."""
import hashlib
from pathlib import Path

from .seed import ROOT


WORKFLOW_VERSION='candidate-investigation-1'


def code_identity():
    files=sorted((ROOT/'backend').rglob('*.py'))
    digest=hashlib.sha256()
    manifest={}
    for path in files:
        name=path.relative_to(ROOT).as_posix()
        fingerprint=hashlib.sha256(path.read_bytes()).hexdigest()
        manifest[name]=fingerprint
        digest.update(name.encode('utf-8')+b'\0'+bytes.fromhex(fingerprint))
    return dict(workflow_version=WORKFLOW_VERSION,code_hash_sha256=digest.hexdigest(),source_hashes=manifest)
