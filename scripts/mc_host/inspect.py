"""Fixed, isolated host reader. JSON stdin/stdout; no imports from the caller's cwd.

Run only after portal admission, with python -I. Never execute document content.
Refusals contain fixed codes only. The source hash describes original bytes, not a rendering.
"""
import hashlib
import io
import json
import os
import re
import stat
import sys
import time
import unicodedata
import zipfile
import xml.etree.ElementTree as ET

MAX_SOURCE = 4 * 1024 * 1024
MAX_ENTRIES = 4000
DENIED = {'.ssh', '.aws', '.gnupg', '.azure', '.kube', '.docker', '.config', '.codex', '.claude',
          '.openclaw', '.clawdbot', '.git', 'keychains', 'cookies', 'login data', 'web data',
          'local storage', 'session storage', 'indexeddb', 'sessions', 'credentials', 'secrets',
          'chrome', 'chromium', 'firefox', '.mozilla', 'bravesoftware', 'microsoft edge', 'safari',
          'containers', 'group containers', 'auth-profiles.json', 'auth.json', '.netrc', '.npmrc', '.pypirc', '.git-credentials', '.gitconfig', '.bash_history', '.zsh_history',
          '.bashrc', '.zshrc', '.bash_profile', '.zprofile', '.profile'}
SECRET = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})|(?i:(?<![\w])\s*["\']?(?:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key))["\']?\s*[:=]\s*\S)|(?i:authorization\s*:\s*(?:bearer|basic)\s+\S+)|\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+')

class Refused(Exception):
    pass


def safe_path(path, denied=()):
    if not isinstance(path, str) or not path.startswith('/') or len(path) > 2048:
        raise Refused('host_path_refused')
    parts = path.split('/')[1:] if path != '/' else []
    if any(not p or p in ('.', '..') or any(ord(c) < 32 or ord(c) == 127 for c in p) for p in parts):
        raise Refused('host_path_refused')
    for i, part in enumerate(parts):
        p = part.lower()
        if p in ('.codex', '.claude') and i + 1 < len(parts) and parts[i + 1].lower() == 'worktrees':
            continue
        if (p in DENIED or p.startswith('.env') or p.endswith(('.pem', '.key', '.p12', '.pfx', '.keychain-db'))
                or any(x in p for x in ('credential', 'secret', 'private_key', 'private-key'))):
            raise Refused('host_path_refused')
    folded = path.lower()
    if any(x in folded for x in ('/library/application support/google/chrome', '/library/application support/chromium',
                               '/library/application support/firefox', '/library/safari', '/library/keychains',
                               '/minimoi-runtime/config', '/minimoi-runtime/auth')):
        raise Refused('host_path_refused')
    normalized = lambda p: unicodedata.normalize('NFC', p).casefold()
    folded_path = normalized(path)
    if any(folded_path == normalized(d) or folded_path.startswith(normalized(d).rstrip('/') + '/') for d in denied):
        raise Refused('host_path_refused')
    return parts


def open_path(path, directory=False, denied=()):
    parts = safe_path(path, denied)
    # Compare inode identities as well as spelling: case-insensitive filesystems,
    # Unicode normalization and alternate volume mounts can alias a denied file/root.
    denied_ids = set()
    for excluded in denied:
        try:
            st = os.stat(excluded)
            denied_ids.add((st.st_dev, st.st_ino))
        except FileNotFoundError:
            pass
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) in denied_ids:
            raise Refused('host_path_refused')
        for i, part in enumerate(parts):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(parts) - 1 or directory:
                flags |= os.O_DIRECTORY
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            st = os.fstat(fd)
            if (st.st_dev, st.st_ino) in denied_ids:
                raise Refused('host_path_refused')
        st = os.fstat(fd)
        if directory:
            if not stat.S_ISDIR(st.st_mode):
                raise Refused('host_path_refused')
        elif not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise Refused('host_file_refused')
        return fd
    except BaseException:
        os.close(fd)
        raise


def stamp(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink


def docx_text(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            entries = z.infolist()
            if len(entries) > 1000 or sum(i.file_size for i in entries) > 10 * 1024 * 1024:
                raise Refused('host_docx_bounds')
            if len({i.filename for i in entries}) != len(entries):
                raise Refused('host_docx_bounds')
            for item in entries:
                if (item.flag_bits & 1 or item.file_size > 2 * 1024 * 1024 or
                        item.file_size > max(1, item.compress_size) * 100 or
                        stat.S_ISLNK(item.external_attr >> 16)):
                    raise Refused('host_docx_bounds')
            data = z.read('word/document.xml')
            data.decode('utf-8')
            if b'\x00' in data:
                raise Refused('host_docx_refused')
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise Refused('host_docx_refused')
            root = ET.fromstring(data)
            ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
            paragraphs = [''.join(p.itertext()) for p in root.findall('.//w:p', ns)]
            sections = [dict(s.attrib) for s in root.findall('.//w:pgSz', ns)]
            tables = []
            for index, table in enumerate(root.findall('.//w:tbl', ns)):
                rows = table.findall('w:tr', ns)
                tables.append({'index': index, 'grid_widths_twips': [c.get('{'+ns['w']+'}w')
                    for c in table.findall('w:tblGrid/w:gridCol', ns)], 'row_count': len(rows),
                    'rows': [{'cell_count': len(row.findall('w:tc', ns)), 'cells': [
                        {'width': dict(width.attrib) if (width := cell.find('w:tcPr/w:tcW', ns)) is not None else None,
                         'grid_span': span.get('{'+ns['w']+'}val') if (span := cell.find('w:tcPr/w:gridSpan', ns)) is not None else None}
                        for cell in row.findall('w:tc', ns)]} for row in rows]})
            projection = '\n'.join(paragraphs) + '\nSection page sizes (OOXML): ' + json.dumps(sections) + \
                '\nTable structure (OOXML; widths are declared, not rendered): ' + json.dumps(tables)
            if len(projection.encode('utf-8')) > MAX_SOURCE:
                raise Refused('host_docx_bounds')
            return projection
    except Refused:
        raise
    except Exception:
        raise Refused('host_docx_refused') from None


def inspect(request):
    if not isinstance(request, dict) or set(request) != {'operation', 'arguments', 'expires_at', 'denied_paths', 'forbidden_values'}:
        raise Refused('host_request_invalid')
    if not isinstance(request['expires_at'], (int, float)) or time.time() >= request['expires_at']:
        raise Refused('host_read_expired')
    args, op = request['arguments'], request['operation']
    denied = request['denied_paths']
    if not isinstance(request['forbidden_values'], list) or not all(isinstance(v, str) for v in request['forbidden_values']):
        raise Refused('host_request_invalid')
    if not isinstance(denied, list) or not all(isinstance(x, str) and x.startswith('/') for x in denied):
        raise Refused('host_request_invalid')
    allowed = {'path', 'offset', 'limit', 'sha256', 'format'} if op == 'read' else {'path', 'offset', 'limit', 'sha256'}
    if op not in ('read', 'list') or not isinstance(args, dict) or set(args) - allowed:
        raise Refused('host_request_invalid')
    offset, limit = args.get('offset', 0), args.get('limit', 2000 if op == 'read' else 50)
    if type(offset) is not int or not 0 <= offset <= MAX_SOURCE or type(limit) is not int or not 1 <= limit <= (2000 if op == 'read' else 50):
        raise Refused('host_request_invalid')
    if offset and not re.fullmatch('[0-9a-f]{64}', str(args.get('sha256', ''))):
        raise Refused('host_page_hash_required')
    fd = open_path(args.get('path'), op == 'list', denied)
    try:
        before = stamp(os.fstat(fd))
        if op == 'list':
            names = []
            omitted = False
            with os.scandir(fd) as entries:
                for n, entry in enumerate(entries):
                    if n >= MAX_ENTRIES:
                        raise Refused('host_directory_too_large')
                    try:
                        safe_path(args['path'].rstrip('/') + '/' + entry.name, denied)
                        st = entry.stat(follow_symlinks=False)
                        if not (stat.S_ISDIR(st.st_mode) or stat.S_ISREG(st.st_mode) and st.st_nlink == 1):
                            raise Refused('host_file_refused')
                        entry.name.encode('utf-8')
                        if SECRET.search(entry.name) or any(v in entry.name for v in request['forbidden_values'] if v):
                            raise Refused('host_secret_detected')
                        names.append({'name': entry.name, 'type': 'directory' if stat.S_ISDIR(st.st_mode) else 'file'})
                    except (Refused, OSError, UnicodeError):
                        omitted = True
            names.sort(key=lambda x: x['name'])
            raw = json.dumps(names, ensure_ascii=False, separators=(',', ':')).encode()
            digest = hashlib.sha256(raw).hexdigest()
            page, total = names[offset:offset+limit], len(names)
            while len(json.dumps(page, ensure_ascii=False).encode()) > 8000:
                page.pop()
            limit = len(page)
            data = {'entries': page, 'omitted_protected_entries': omitted, 'view': 'directory_names'}
        else:
            if before[2] > MAX_SOURCE:
                raise Refused('host_file_too_large')
            chunks, size = [], 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_SOURCE:
                    raise Refused('host_file_too_large')
            raw = b''.join(chunks)
            digest = hashlib.sha256(raw).hexdigest()
            fmt = args.get('format', 'text')
            if fmt == 'docx':
                content = docx_text(raw)
            elif fmt == 'text':
                try:
                    content = raw.decode('utf-8')
                except UnicodeError:
                    raise Refused('host_binary_refused') from None
                if any(ord(c) < 32 and c not in '\n\r\t' for c in content):
                    raise Refused('host_binary_refused')
            else:
                raise Refused('host_request_invalid')
            # Refuse the entire file, before paging; secrets split over page boundaries cannot leak.
            if SECRET.search(content) or any(v in content for v in request['forbidden_values'] if v):
                raise Refused('host_secret_detected')
            total = len(content)
            data = {'text': content[offset:offset+limit], 'view': 'docx_structure_not_rendered' if fmt == 'docx' else 'utf8_text',
                    'source_bytes': len(raw)}
        if stamp(os.fstat(fd)) != before:
            raise Refused('host_source_changed')
        if args.get('sha256', digest) != digest:
            raise Refused('host_source_changed')
        if offset > total:
            raise Refused('host_page_invalid')
        if time.time() >= request['expires_at']:
            raise Refused('host_read_expired')
        next_offset = offset + limit if offset + limit < total else None
        data.update(sha256=digest, offset=offset, total=total, next_offset=next_offset,
                    hash_kind='source_bytes' if op == 'read' else 'filtered_directory_names')
        return {'status': 'done', 'code': 'host_read', 'data': data}
    finally:
        os.close(fd)


def run(request):
    try:
        return inspect(request)
    except Refused as exc:
        return {'status': 'refused', 'code': str(exc), 'data': {}}
    except Exception:
        return {'status': 'refused', 'code': 'host_read_unavailable', 'data': {}}


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(16385)
        output = run(json.loads(raw)) if len(raw) <= 16384 else run(None)
    except Exception:
        output = {'status': 'refused', 'code': 'host_request_invalid', 'data': {}}
    sys.stdout.write(json.dumps(output, ensure_ascii=False))
