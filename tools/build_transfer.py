"""Build a checked local migration bundle without credentials or machine caches."""
import argparse
import base64
import hashlib
import importlib.metadata
import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SKIP_DIRS={'.venv','node_modules','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.cache'}
SECRET_NAMES={'apikey.txt','.npmrc','.pypirc','.netrc','.git-credentials','auth.json','credentials.json','id_rsa','id_ed25519','id_ecdsa','id_dsa'}
SECRET_PATTERNS=[
    re.compile(rb'sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}'),
    re.compile(rb'gh[pousr]_[A-Za-z0-9]{20,}'),
    re.compile(rb'github_pat_[A-Za-z0-9_]{20,}'),
    re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----'),
    re.compile(rb'https?://[^\s/@<>]+:[^\s/@<>]+@'),
]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def excluded(relative):
    parts=relative.parts;name=relative.name.lower();suffix=relative.suffix.lower()
    return (any(p in SKIP_DIRS for p in parts)
        or tuple(parts[:2]) in {('frontend','dist'),('runtime','calculation-cache'),('.git','hooks')}
        or name in SECRET_NAMES or name.startswith('.env') or name.startswith('~$')
        or suffix in {'.pyc','.pyo','.tsbuildinfo','.log','.lock','.pem','.key','.pfx','.p12'}
        or name.endswith(('-wal','-shm','-journal')) or name in {'transfer_manifest.json','.ds_store'}
        or (parts[0]=='.git' and name in {'config','config.worktree'}))


def source_files():
    found=[]
    for directory,dirs,names in os.walk(ROOT,followlinks=False):
        here=Path(directory)
        kept=[]
        for name in dirs:
            path=here/name
            if path.is_symlink():raise ValueError('Refusing a directory symlink: '+str(path.relative_to(ROOT)))
            if not excluded(path.relative_to(ROOT)):kept.append(name)
        dirs[:]=kept
        for name in names:
            path=here/name;rel=path.relative_to(ROOT)
            if excluded(rel):continue
            if path.is_symlink():raise ValueError('Refusing a file symlink: '+str(rel))
            if rel.as_posix()=='runtime/audit.sqlite':continue
            found.append(path)
    return sorted(found)


def git_executable():
    found=shutil.which('git')
    fallback=Path(os.environ.get('LOCALAPPDATA',''))/'Temp/opencode/git-tools/mingit/cmd/git.exe'
    if found:return found
    if fallback.is_file():return str(fallback)
    raise ValueError('Git is required to verify copied Git objects before packaging')


def git(git_path,*args):
    return subprocess.check_output([git_path,'--no-optional-locks','-C',str(ROOT),*args],text=True,encoding='utf-8').strip()


def known_credentials():
    values=set()
    file=ROOT/'apikey.txt'
    if file.is_file():
        value=file.read_text(encoding='utf-8').strip()
        if value:values.add(value.encode())
    for name in ('OPENAI_API_KEY','ANTHROPIC_API_KEY','GITHUB_TOKEN','GH_TOKEN'):
        value=os.environ.get(name,'').strip()
        if value:values.add(value.encode())
    return values|{base64.b64encode(v) for v in values}


def scan_bytes(content,label,known,depth=0):
    if any(value in content for value in known) or any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise ValueError('Credential-like content detected; bundle not published. Location: '+label)
    # Workbook ZIP members and other nested ZIPs may hide compressed secrets.
    stream=io.BytesIO(content)
    if zipfile.is_zipfile(stream):
        if depth>=4:raise ValueError('Nested archive depth prevents verification: '+label)
        with zipfile.ZipFile(stream) as archive:
            total=0
            for item in archive.infolist():
                total+=item.file_size
                if total>256*1024*1024:raise ValueError('Nested archive too large to verify: '+label)
                if not item.is_dir():scan_bytes(archive.read(item),label+' / '+item.filename,known,depth+1)


def scan_git_objects(git_path,stage,known):
    # Scan decompressed objects, including unreachable ones in the copied repo.
    process=subprocess.Popen([git_path,'-C',str(stage),'cat-file','--batch-all-objects','--batch'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    count=0
    try:
        while True:
            header=process.stdout.readline()
            if not header:break
            oid,kind,size=header.split();size=int(size)
            content=process.stdout.read(size)
            if len(content)!=size or process.stdout.read(1)!=b'\n':raise ValueError('Could not read copied Git object')
            scan_bytes(content,'Git object '+oid.decode(),known)
            count+=1
        if process.wait()!=0:raise ValueError('Could not verify copied Git objects')
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stdout.close();process.stderr.close()
    return count


def check_inactive():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/state',timeout=3) as response:state=json.load(response)
    except (urllib.error.URLError,TimeoutError,ConnectionError):
        return 'Local API not reachable; no server was started by packaging.'
    if state.get('active_run_id'):raise ValueError('Wait for the active analysis to finish before packaging')
    return 'Local API reachable with no active analysis; no paid run requested.'


def backup_audit(destination):
    source=ROOT/'runtime/audit.sqlite'
    if not source.is_file():raise ValueError('Expected saved audit.sqlite is missing; refusing an incomplete migration')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True)) as original,closing(sqlite3.connect(destination)) as snapshot:
        original.backup(snapshot)
        if snapshot.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Copied audit database failed integrity check')
        runs=snapshot.execute('SELECT count(*) FROM runs').fetchone()[0]
        chats=snapshot.execute('SELECT count(*) FROM chats').fetchone()[0]
        latest=snapshot.execute('SELECT id,created FROM runs ORDER BY created DESC LIMIT 1').fetchone()
    return dict(runs=runs,application_chat_messages=chats,latest_analysis=dict(id=latest[0],created=latest[1]) if latest else None)


def environment_versions():
    python_packages={dist.metadata['Name']:dist.version for dist in importlib.metadata.distributions() if dist.metadata.get('Name')}
    node=shutil.which('node')
    return dict(python=platform.python_version(),platform=platform.platform(),
        node=subprocess.check_output([node,'--version'],text=True).strip() if node else 'not found',
        installed_python_packages=dict(sorted(python_packages.items(),key=lambda pair:pair[0].lower())))


def build(output):
    output=Path(output).resolve()
    if output==ROOT or ROOT in output.parents:raise ValueError('Keep transfer archives outside the source project')
    status=check_inactive();git_path=git_executable();known=known_credentials()
    git_state=dict(branch=git(git_path,'branch','--show-current'),head=git(git_path,'rev-parse','HEAD'),
        status=git(git_path,'status','--short','--branch'),remote='https://github.com/JomoGeorgeSmith/fxtrader.git')
    tmp_parent=Path(os.environ.get('LOCALAPPDATA',''))/'Temp/opencode'
    with tempfile.TemporaryDirectory(prefix='fx-transfer-',dir=str(tmp_parent) if tmp_parent.is_dir() else None) as temp:
        stage=Path(temp)/'FX Analyst';stage.mkdir()
        files=source_files();before={str(p.relative_to(ROOT)):sha(p) for p in files}
        for source in files:
            dest=stage/source.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
        # Keep history/index/refs but never carry embedded credentials, local
        # credential hooks, proxy headers or machine-specific Git configuration.
        branch=git_state['branch']
        if branch!='orchestrator':raise ValueError('Unexpected branch; review migration instructions before packaging')
        (stage/'.git/config').write_text('[core]\n\trepositoryformatversion = 0\n\tfilemode = false\n\tbare = false\n\tlogallrefupdates = true\n\tsymlinks = false\n\tignorecase = true\n[remote "origin"]\n\turl = https://github.com/JomoGeorgeSmith/fxtrader.git\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n[branch "orchestrator"]\n\tremote = origin\n\tmerge = refs/heads/orchestrator\n',encoding='utf-8')
        audit=backup_audit(stage/'runtime/audit.sqlite')
        after_files=source_files()
        if set(after_files)!=set(files) or any(sha(p)!=before[str(p.relative_to(ROOT))] for p in files):
            raise ValueError('Source changed while copying; retry from a stable project')
        if git(git_path,'rev-parse','HEAD')!=git_state['head']:raise ValueError('Git HEAD changed while copying')
        # Validate restored workbooks without any model calls or workbook writes.
        import sys
        sys.path.insert(0,str(ROOT))
        from backend.app import read_context
        data=read_context(stage/'shared')
        restored_status=git(git_path,'-c','core.autocrlf=true','-C',str(stage),'status','--short','--branch')
        for path in sorted(stage.rglob('*')):
            if path.is_file():scan_bytes(path.read_bytes(),path.relative_to(stage).as_posix(),known)
        git_objects=scan_git_objects(git_path,stage,known)
        payload=[dict(path=p.relative_to(stage).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(stage.rglob('*')) if p.is_file()]
        manifest=dict(format_version=1,created_local=datetime.now().astimezone().isoformat(),project='JN Treasury FX Analyst POC',
            source_git=git_state,copied_git_status=restored_status,environment=environment_versions(),audit=audit,
            workbook_rows={key:len(rows) for key,rows in data.items()},files=payload,
            checks=dict(source_files_unchanged=True,audit_integrity='ok',workbooks_valid=True,git_objects_scanned=git_objects,
                secret_scan='Known local provider tokens, base64 forms and common OpenAI/GitHub/private-key/credential-URL patterns checked; Git objects and nested workbooks scanned.',
                api=status,paid_model_requests=0),
            exclusions=['API keys and .env files','provider logins and OpenCode/OpenChamber profile','machine venv/node_modules/build outputs','bytecode/log/lock files','calculation cache','Git hooks and original machine Git config','database sidecars and old Kafka events'],
            security_note='Not encrypted. Contains banking reference documents and application conversation/audit data. Use employer-approved secure transport. Handover is not a full development-session export.')
        (stage/'TRANSFER_MANIFEST.json').write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding='utf-8')
        scan_bytes((stage/'TRANSFER_MANIFEST.json').read_bytes(),'TRANSFER_MANIFEST.json',known)
        name='FX-Analyst-transfer-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip'
        candidate=Path(temp)/name
        with zipfile.ZipFile(candidate,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in sorted(stage.rglob('*')):
                if path.is_file():archive.write(path,Path('FX Analyst')/path.relative_to(stage))
        with zipfile.ZipFile(candidate) as archive:
            if archive.testzip() is not None:raise ValueError('ZIP integrity verification failed')
            expected={f'FX Analyst/{item["path"]}':item['sha256'] for item in payload}
            for path,digest in expected.items():
                if hashlib.sha256(archive.read(path)).hexdigest()!=digest:raise ValueError('Archive payload verification failed: '+path)
        # Detect any unexpected run/chat additions while copying, but never start
        # an analysis or stop unrelated processes to make a backup.
        with closing(sqlite3.connect((ROOT/'runtime/audit.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as original:
            if original.execute('SELECT count(*) FROM runs').fetchone()[0]!=audit['runs'] or original.execute('SELECT count(*) FROM chats').fetchone()[0]!=audit['application_chat_messages']:
                raise ValueError('Audit history changed during packaging; retry after the active work finishes')
        check_inactive()
        output.mkdir(parents=True,exist_ok=True)
        final=output/name
        if final.exists():raise FileExistsError('Archive already exists; will not overwrite it')
        shutil.copy2(candidate,final)
        checksum=sha(final)
        final.with_suffix('.zip.sha256').write_text(checksum+'  '+name+'\n',encoding='utf-8')
        return dict(archive=str(final),checksum_file=str(final.with_suffix('.zip.sha256')),sha256=checksum,
            bytes=final.stat().st_size,payload_files=len(payload),audit=audit,workbooks_valid=True,source_files_unchanged=True,paid_model_requests=0)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT.parent/'FX Analyst Transfer')
    args=parser.parse_args()
    print(json.dumps(build(args.output),indent=2))
