"""Materialize an explicitly approved source delivery; never execute bundle contents."""
import base64
import hashlib
import json
import lzma
from pathlib import Path, PurePosixPath

ROOT = Path.cwd().resolve()
STAGING = ROOT / '.delivery'
ready = json.loads((STAGING / 'READY.json').read_text(encoding='utf-8'))
if ready.get('schema_version') != 1 or not 1 <= ready.get('part_count', 0) <= 100:
    raise SystemExit('Invalid delivery manifest')
parts = []
for index in range(ready['part_count']):
    part = (STAGING / f'{index:03}.b64').read_text(encoding='ascii')
    parts.append(''.join(part.split()))
encoded = ''.join(parts)
if len(encoded) > 20_000_000:
    raise SystemExit('Delivery exceeds import size limit')
archive = base64.b64decode(encoded, validate=True)
if hashlib.sha256(archive).hexdigest() != ready['archive_sha256']:
    raise SystemExit('Compressed delivery checksum mismatch')
decoder = lzma.LZMADecompressor(memlimit=256 * 1024 * 1024)
payload = decoder.decompress(archive, max_length=10_000_001)
if not decoder.eof or decoder.unused_data or len(payload) > 10_000_000:
    raise SystemExit('Invalid or oversized source delivery')
if hashlib.sha256(payload).hexdigest() != ready['payload_sha256']:
    raise SystemExit('Source delivery checksum mismatch')
def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate key in delivery: ' + key)
        value[key] = item
    return value
files = json.loads(payload, object_pairs_hook=unique_object)['files']
if len(files) != ready['file_count']:
    raise SystemExit('Source file count mismatch')
manifest = json.loads(files['UPLOAD_MANIFEST.json'])
expected = {item['path']: item for item in manifest['files']}
if len(expected) != ready['source_file_count']:
    raise SystemExit('Manifest source count mismatch')
if set(files) != set(expected) | {'UPLOAD_MANIFEST.json', 'UPLOAD_REPORT.md'}:
    raise SystemExit('Unexpected or missing file in delivery')
validated = []
for name, text in files.items():
    rel = PurePosixPath(name)
    if (not name or rel.is_absolute() or '..' in rel.parts or '\\' in name
            or '\x00' in name or rel.parts[0] in {'.git', '.github', '.delivery'}
            or rel.as_posix() != name):
        raise SystemExit('Unsafe path in delivery')
    destination = ROOT.joinpath(*rel.parts)
    if not destination.resolve().is_relative_to(ROOT):
        raise SystemExit('Path escapes repository')
    if any(parent.is_symlink() for parent in [destination, *destination.parents]):
        raise SystemExit('Symlink destination refused')
    if not isinstance(text, str):
        raise SystemExit('Only UTF-8 source files are supported')
    data = text.encode('utf-8')
    if name in expected:
        item = expected[name]
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise SystemExit('Per-file checksum mismatch: ' + name)
    validated.append((name, destination, data))
# All checks complete before writing any approved source files.
for name, destination, data in validated:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    if destination.read_bytes() != data:
        raise SystemExit('Read-back mismatch: ' + name)
result = {
    'import_status': 'SOURCE_IMPORT_COMPLETE',
    'source_archive_sha256': manifest['source_archive_sha256'],
    'payload_sha256': ready['payload_sha256'],
    'verified_source_files': len(expected),
    'imported_files': len(files),
    'excluded_files': len(manifest['excluded_files']),
    'business_tests_run': False,
    'deployment_status': 'NOT_PRODUCTION_READY'
}
(STAGING / 'import-result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
paths = [name for name, _, _ in validated] + ['.delivery/import-result.json']
(STAGING / 'paths.nul').write_bytes(b'\0'.join(name.encode('utf-8') for name in paths) + b'\0')
print(json.dumps(result, indent=2))
