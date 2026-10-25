"""Fixed synthetic-v1 only. Unchanged pinned Career Kit; no owner inputs or model work."""
import hashlib
import contextlib
import copy
import importlib.util
import importlib.metadata
import io
import json
from pathlib import Path
import sys
import os
import stat
import zipfile


def digest(raw):return hashlib.sha256(raw).hexdigest()


def canonical_docx(raw):
    """Normalize archive packaging timestamps only; preserve every XML/media byte."""
    output=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as dst:
        names=src.namelist()
        if len(names)>128 or len(set(names))!=len(names):raise ValueError('archive_bound')
        if sum(x.file_size for x in src.infolist())>8*1024*1024:raise ValueError('archive_bound')
        for name in sorted(names):
            if name.startswith('/') or '..' in Path(name).parts:raise ValueError('archive_path')
            info=zipfile.ZipInfo(name,(2000,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.create_system=3;info.external_attr=0o600<<16
            dst.writestr(info,src.read(name))
    return output.getvalue()


def dependency_digest(name):
    dist=importlib.metadata.distribution(name)
    entries=[]
    for f in sorted(dist.files or [],key=str):
        if str(f).endswith(('.pyc','.pyo')) or '__pycache__' in str(f):continue
        path=Path(dist.locate_file(f))
        if path.is_symlink() or not path.is_file():raise ValueError('dependency_file')
        entries.append([str(f),digest(path.read_bytes())])
    if not entries:raise ValueError('dependency_empty')
    return digest(json.dumps(entries,separators=(',',':')).encode())


def checked_kit(root,manifest):
    root=Path(root)
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('kit_symlink')
    if root.stat().st_uid!=os.getuid() or root.stat().st_mode&0o022:raise ValueError('kit_permissions')
    for name,want in manifest['kit_files'].items():
        if '/' in name or name in ('.','..'):raise ValueError('kit_name')
        path=root/name
        st=path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.getuid() or st.st_mode&0o022 or digest(path.read_bytes())!=want:raise ValueError('kit_changed')
    for name,want in manifest['dependencies'].items():
        if importlib.metadata.version(name)!=want or dependency_digest(name)!=manifest['dependency_sha256'][name]:raise ValueError('dependency_changed')
    if digest(Path(__file__).read_bytes())!=manifest['recipe_sha256']:raise ValueError('recipe_changed')
    spec=importlib.util.spec_from_file_location('mc_fixed_career_kit',root/'__init__.py',submodule_search_locations=[str(root)])
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    from mc_fixed_career_kit import workbook,export,letter,resume_import
    return workbook,export,letter,resume_import


def generate(root,manifest,work):
    """All data below is public synthetic fixture text. No input path parameters."""
    workbook,export,letter,resume_import=checked_kit(root,manifest)
    from docx import Document
    work=Path(work);checks={}
    resume=Document()
    lines=[('SYNTHETIC Example Person',None),('Springfield · synthetic@example.test',None),
        ('PROFESSIONAL SUMMARY',None),('SYNTHETIC operations leader.',None),
        ('CURRENT WORK',None),('Side project   |   example.test',None),('2024–Present',None),('SYNTHETIC personal project.',None),
        ('PROFESSIONAL EXPERIENCE',None),('Director  |   Acme Networks   Springfield',None),('2020–Present',None),
        ('SYNTHETIC led operations through a merger.',None),('SYNTHETIC cut incidents by half.','List Paragraph'),
        ('EDUCATION',None),('SYNTHETIC B.A., Example University',None),('CAPABILITIES',None),('SYNTHETIC delivery and operations',None)]
    for text,style in lines:resume.add_paragraph(text,style=style)
    seed=work/'source-resume.docx';resume.save(seed)
    imported_path=work/'imported.json'
    def cli(fn,args):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):return fn([str(x) for x in args])
    checks['import_cli']=cli(resume_import.main,[seed,imported_path])==0
    imported_bytes=imported_path.read_bytes()
    checks['import_overwrite_refused']=cli(resume_import.main,[seed,imported_path])==1 and imported_path.read_bytes()==imported_bytes
    doc=json.loads(imported_bytes)
    checks['import_roles']=[r['kind'] for r in doc['rows']]==['job','project','about'] and doc['rows'][0]['resume_bullets']==['SYNTHETIC cut incidents by half.'] and doc['rows'][0]['resume_entry']=='SYNTHETIC led operations through a merger.'
    checks['import_header_tail']=doc['header']==[x[0] for x in lines[:2]] and [a['text'] for a in doc['appendix']]==[x[0] for x in lines[-4:]]
    checks['unique_ids_about_last']=len({r['id'] for r in doc['rows']})==len(doc['rows']) and doc['rows'][-1]['id']=='about'
    doc['agreement']=[{'row':'SYNTHETIC Acme','linkedin':'old','resume':'new'}]
    doc['title']='SYNTHETIC TEST — Career E2E — NOT OWNER MATERIAL'
    for row in doc['rows']:
        row['rewrite']=['Summary: SYNTHETIC draft summary.'] if row['id']=='about' else ['SYNTHETIC draft '+row['id'], 'LinkedIn: SYNTHETIC draft entry.','• SYNTHETIC draft bullet.']
    source=work/'input.json';source.write_text(json.dumps(doc,sort_keys=True)+'\n')
    wb=work/'workbook.docx';workbook.build(workbook.load(source),wb)
    parsed=Document(wb)
    checks['landscape']=parsed.sections[0].page_width>parsed.sections[0].page_height
    checks['five_columns']=all(len(r.cells)==5 for r in parsed.tables[0].rows)
    checks['owner_columns_empty']=all(not r.cells[i].text for r in parsed.tables[0].rows[1:] for i in (2,4))
    widths=[c.width for c in parsed.tables[0].columns];total=sum(widths)
    checks['column_widths']=all(abs(widths[i]/total-ratio)<.002 for i,ratio in enumerate((.14,.28,.20,.20,.18)))
    before=wb.read_bytes()
    try:workbook.build(doc,wb)
    except FileExistsError:checks['overwrite_refused']=wb.read_bytes()==before
    for name in ('schema','duplicate_id','about_not_last'):
        invalid=copy.deepcopy(doc)
        if name=='schema':invalid['schema']='invalid'
        elif name=='duplicate_id':invalid['rows'].insert(0,copy.deepcopy(invalid['rows'][0]))
        else:invalid['rows'][0],invalid['rows'][-1]=invalid['rows'][-1],invalid['rows'][0]
        bad=work/('invalid-'+name+'.json');bad.write_text(json.dumps(invalid))
        checks['invalid_'+name+'_refused']=cli(workbook.main,[bad,work/('invalid-'+name+'.docx')])==1
        checks['invalid_'+name+'_no_output']=not (work/('invalid-'+name+'.docx')).exists()
    # A test fixture simulates owner edits; never represented as real owner approval.
    for row in parsed.tables[0].rows[1:]:
        row.cells[2].text='SYNTHETIC owner notes'
        row.cells[4].text='Summary: SYNTHETIC final summary.' if export.row_id(row.cells[0])=='about' else 'SYNTHETIC final entry'
        if export.row_id(row.cells[0])!='about':
            row.cells[4].add_paragraph('LinkedIn: SYNTHETIC final LinkedIn')
            row.cells[4].add_paragraph('• SYNTHETIC final bullet')
    parsed.tables[2].rows[1].cells[3].text='SYNTHETIC keep new wording'
    parsed.tables[0].rows[-1].cells[4].add_paragraph('Headline: '+'X'*221)
    edited=work/'edited.docx';parsed.save(edited)
    carried=work/'carried.docx';workbook.build(doc,carried,carry_from=edited)
    checks['carry_forward']=workbook.read_carry(edited)==workbook.read_carry(carried)
    export.export(source,carried,work/'draft',draft=True,pages=False)
    export.export(source,carried,work/'final',pages=False)
    checks['decision_readback']='decision: SYNTHETIC keep new wording' in (work/'final/report.md').read_text()
    checks['linkedin_length_flag']='headline: 221 characters > 220' in (work/'final/report.md').read_text()
    final_before={f.name:f.read_bytes() for f in (work/'final').iterdir()}
    checks['export_overwrite_refused']=cli(export.main,[source,carried,work/'final'])==1 and {f.name:f.read_bytes() for f in (work/'final').iterdir()}==final_before
    checks['draft_marked']='DRAFT' in (work/'draft/linkedin.md').read_text()
    checks['final_column_only']='SYNTHETIC final' in (work/'final/linkedin.md').read_text() and 'draft entry' not in (work/'final/linkedin.md').read_text()
    export.export(source,wb,work/'partial',pages=False)
    report=(work/'partial/report.md').read_text()
    checks['partial_finals_not_filled']=all('- '+r['id'] in report for r in doc['rows']) and 'SYNTHETIC draft' not in (work/'partial/linkedin.md').read_text()
    parts={'greeting':'Hello,','opening':'SYNTHETIC Acme role needs platform operations.','middle':['SYNTHETIC fixed middle.'],'close':'SYNTHETIC close.','signoff':'Regards,','name':'Synthetic Person'}
    try:letter.write(dict(parts,opening='[placeholder]'),work/'refused.docx')
    except ValueError:checks['letter_placeholder_refused']=not (work/'refused.docx').exists()
    letter.write(parts,work/'letter.docx')
    parts_path=work/'parts.json';parts_path.write_text(json.dumps(parts))
    letter_before={name:(work/name).read_bytes() for name in ('letter.docx','letter.md')}
    checks['letter_overwrite_refused']=cli(letter.main,[parts_path,work/'letter.docx'])==1 and all((work/n).read_bytes()==raw for n,raw in letter_before.items())
    (work/'sidecar.md').write_text('SYNTHETIC existing sidecar')
    checks['letter_sidecar_refused']=cli(letter.main,[parts_path,work/'sidecar.docx'])==1 and not (work/'sidecar.docx').exists() and (work/'sidecar.md').read_text()=='SYNTHETIC existing sidecar'
    checks['page_count_not_checked']=all('not checked' in (work/k/'report.md').read_text() for k in ('draft','final','partial'))
    if not all(checks.values()):raise ValueError('synthetic_check_failed:'+','.join(k for k,v in checks.items() if not v)+':'+str(len(checks)))
    outputs={}
    names={'source-resume.docx':'source-resume.docx','imported.json':'imported.json','workbook.docx':'workbook.docx','carried.docx':'carried.docx','draft/resume.docx':'draft-resume.docx','draft/linkedin.md':'draft-linkedin.md','draft/report.md':'draft-report.md','final/resume.docx':'final-resume.docx','final/linkedin.md':'final-linkedin.md','final/report.md':'final-report.md','partial/report.md':'partial-report.md','letter.docx':'letter.docx','letter.md':'letter.md'}
    for path,name in names.items():
        raw=(work/path).read_bytes();outputs[name]=canonical_docx(raw) if name.endswith('.docx') else raw
    outputs['checks.json']=(json.dumps({'fixture':'synthetic-v1','kit_commit':manifest['kit_commit'],'recipe_sha256':manifest['recipe_sha256'],'kit_files':manifest['kit_files'],'dependencies':manifest['dependencies'],'dependency_sha256':manifest['dependency_sha256'],'checks':checks,'page_count':'not checked; no renderer invoked'},sort_keys=True,indent=2)+'\n').encode()
    return dict(sorted(outputs.items()))
