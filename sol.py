"""Agent-facing product: open -> answer -> review -> finish. No network by default."""
import argparse,collections,contextlib,hashlib,json,os,re,shutil,sqlite3,sys,tempfile,time,zipfile
from pathlib import Path
import xml.etree.ElementTree as E
import tool,scanner
VERSION='2.1.0'

def dbopen(path=None):
 d=tool.dbopen(path);d.execute('CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, decision TEXT NOT NULL)');d.execute('CREATE TABLE IF NOT EXISTS audit_history(seq INTEGER PRIMARY KEY,id TEXT,old TEXT,reason TEXT,time TEXT)');d.execute('CREATE TABLE IF NOT EXISTS audit_uses(id TEXT,job TEXT,PRIMARY KEY(id,job))');d.commit();return d
@contextlib.contextmanager
def locked(job):
 import msvcrt
 job=Path(job);job.mkdir(parents=True,exist_ok=True)
 with open(job/'.lock','a+b') as f:
  f.seek(0);f.write(b'0');f.flush();f.seek(0)
  try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
  except OSError:raise ValueError('Job is busy in another process')
  try:yield
  finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
def state(job):return tool.read(Path(job)/'job.json')
def save(job,j):tool.write(Path(job)/'job.json',j)
def ensure_identity(j):
 for r in j['rows']:
  if r['id']!=tool.identity(j['package'],r,j['language'],j['target'],j['glossary']):raise ValueError('Job identity was altered; reopen original source')
def backup(d):
 path=tool.ROOT/'state/backups'/('memory-'+time.strftime('%Y%m%d-%H%M%S')+'.sqlite3');path.parent.mkdir(parents=True,exist_ok=True)
 with sqlite3.connect(path) as dst:d.backup(dst)
 return str(path)
def load_audit(d,j):
 decisions={r[0]:json.loads(r[1]) for r in d.execute('SELECT id,decision FROM audit')}
 rows={r['id']:r for r in j['rows']}
 for task in j['issues']:
  q=decisions.get(task['id'])
  if not q:
   if task.get('decision',{}).get('status')=='accepted':task.pop('decision',None)
   continue
  task['decision']=q
  for original in q.get('generated_rows',[]):
   r=dict(original);r.pop('translation',None);r.pop('format_review',None)
   hit=d.execute("SELECT translation,row_json FROM memory WHERE id=? AND status='accepted'",(r['id'],)).fetchone()
   r['state']='automatic' if hit else 'pending'
   if hit:
    r['translation']=hit[0];approved=json.loads(hit[1]).get('format_review')
    if approved:r['format_review']=approved
   if r['id'] not in rows:j['rows'].append(r);rows[r['id']]=r
  if q.get('generated_artifact'):
   artifact=q['generated_artifact'];source=Path(artifact['path']);canonical=tool.ROOT/'state/artifacts'/(artifact['sha256']+'.dll')
   if source.exists() and hashlib.sha256(source.read_bytes()).hexdigest()==artifact['sha256']:
    canonical.parent.mkdir(parents=True,exist_ok=True)
    if source.resolve()!=canonical.resolve():shutil.copy2(source,canonical)
    artifact=dict(artifact,path=str(canonical.resolve()))
   current=j.setdefault('extra_files',[]);current[:]=[a for a in current if a['sha256']!=artifact['sha256']];current.append(artifact)
  for patch in q.get('generated_patches',[]):
   if patch not in j.setdefault('patches',[]):j['patches'].append(patch)
 return j

def open_mod(d,source,version='1.6',mods=(),language='en',job=None):
 if language not in ('en','zh-Hans'):raise ValueError('Specify supported source language explicitly')
 begin=time.perf_counter();root=Path(source).resolve()
 if not root.exists():raise ValueError('Mod not found')
 paths=[root] if root.is_file() else sorted(p for p in root.rglob('*') if p.is_file())
 fingerprints=[]
 for p in paths:
  if p.is_symlink() or not p.resolve().is_relative_to(root if root.is_dir() else root.parent):raise ValueError('External input link')
  fingerprints.append([str(p.relative_to(root)) if root.is_dir() else p.name,hashlib.sha256(p.read_bytes()).hexdigest()])
 signature=tool.digest([fingerprints,version,sorted(mods),language,VERSION])
 out=Path(job).resolve() if job else tool.ROOT/'runs'/(re.sub(r'[^A-Za-z0-9_.-]','_',root.stem)[:40]+'-'+signature[:16])
 if (out/'job.json').exists():
  j=state(out)
  if j.get('input_signature')!=signature:raise ValueError('Existing job belongs to a different snapshot/profile')
  request(d,out);return report(d,out)
 if root.is_dir() and out.is_relative_to(root):raise ValueError('Output cannot be inside input')
 out.mkdir(parents=True,exist_ok=True)
 with locked(out):
  scan=scanner.scan(root,out,version,mods,language);catalog=out/'catalog.json';tool.write(catalog,scan['rows'])
  tool.analyze(d,catalog,out,scan['package'],language)
  j=state(out);j.update(product=VERSION,input_signature=signature,input_manifest=fingerprints,source=str(root),scan_profile=scan['profile'],mod_name=scan['name'],original_author=scan['author'],issues=scan['tasks'],files=scan['inventory'],snapshot=scan['snapshot'],timing={'open_seconds':round(time.perf_counter()-begin,3)},patches=[],extra_files=[])
  j=load_audit(d,j)
  with d:d.executemany('INSERT OR IGNORE INTO audit_uses VALUES(?,?)',[(t['id'],str(out)) for t in j['issues'] if t.get('decision',{}).get('status')=='accepted'])
  save(out,j);tool.write(out/'assemblies.json',scan['assemblies']);request(d,out)
 return report(d,out)

def request(d,job,limit=14000):
 job=Path(job);j=load_audit(d,state(job));ensure_identity(j);save(job,j)
 items=[]
 for r in j['rows']:
  if r['state']=='pending':
   q={k:r[k] for k in ('id','type','key','text','role','context') if k in r}
   if r.get('candidates'):q['suggestions']=[{k:c[k] for k in ('package','source','translation','status') if k in c} for c in r['candidates'][:2]]
   if r.get('concepts'):q['concepts']=r['concepts']
   items.append(('translations',q))
 for task in j['issues']:
  if not task.get('decision'):items.append(('source_review',task))
 batches=[];current={'translations':[],'source_review':[]};chars=0
 for kind,item in items:
  n=len(json.dumps(item,ensure_ascii=False))
  if chars and chars+n>limit:batches.append(current);current={'translations':[],'source_review':[]};chars=0
  current[kind].append(item);chars+=n
 if chars:batches.append(current)
 paths=[]
 for batch in batches:
  identifier=tool.digest(batch);path=job/'requests'/(identifier[:20]+'.json');tool.write(path,dict(batch,request_id=identifier,response_schema={'request_id':identifier,'translations':{'FULL_ID':'Russian text'},'source_review':[{'id':'TASK_ID','decision':'technical | covered | needs_patch | unresolved','reason':'concrete evidence/reason','rows':'optional contextual catalog records','patches':'optional approved method literal IDs'}]}));paths.append(str(path))
 tool.write(job/'requests.json',{'active':paths,'pending_translations':sum(r['state']=='pending' for r in j['rows']),'unreviewed_sources':sum(not i.get('decision') for i in j['issues'])})
 return {'requests':paths,'count':len(paths)}

def receive(d,job,response):
 job=Path(job);answer=tool.read(response)
 # Save raw input before validation; never automatically re-execute an external API.
 tool.write(job/'receipts'/('sol-'+tool.digest(answer)+'.json'),{'answer':answer,'time':tool.now()})
 j=state(job);ensure_identity(j);request_id=answer.get('request_id');manifest=tool.read(job/'requests.json')
 path=next((Path(p) for p in manifest['active'] if tool.read(p)['request_id']==request_id),None)
 if not path:raise ValueError('Unknown/stale request_id. Export current requests first.')
 req=tool.read(path);expected={r['id'] for r in req['translations']};translations=answer.get('translations',{})
 if not isinstance(translations,dict) or set(translations)!=expected:raise ValueError('Translation ID set must exactly match this request')
 errors=tool.validate(req['translations'],translations)
 reviews=answer.get('format_reviews',{})
 for err in errors:
  if not isinstance(err,dict) or not isinstance(reviews,dict) or not str(reviews.get(err['id'],'')).strip():raise ValueError('Format review required: '+json.dumps(errors,ensure_ascii=False))
 if any(not isinstance(v,str) or not v.strip() for v in translations.values()):raise ValueError('Nonempty string translations required')
 tasks={t['id']:t for t in req['source_review']};decisions=answer.get('source_review',[])
 if not isinstance(decisions,list) or len({q.get('id') for q in decisions})!=len(decisions) or any(q.get('id') not in tasks for q in decisions):raise ValueError('Unknown/duplicate audit ID')
 byid={r['id']:r for r in j['rows']}
 for id,v in translations.items():
  r=byid[id];r.update(state='draft',translation=v)
  if id in reviews:r['format_review']={'signature':tool.digest([r['text'],v]),'reason':reviews[id]}
 for q in decisions:
  if q.get('decision') not in ('technical','covered','translate','artifact','needs_patch','unresolved') or not str(q.get('reason','')).strip():raise ValueError('Explicit audit decision and evidence required')
  task=tasks[q['id']];q=dict(q);q['generated_rows']=[];q['generated_patches']=[]
  if task['kind'] in ('invalid-xml','invalid-language-xml','assembly-errors','collision') and q['decision']!='unresolved':raise ValueError('Structural/assembly errors cannot be waived; fix the input or extraction profile')
  if q['decision']=='artifact':
   if not q.get('artifact'):raise ValueError('Reviewed language artifact required')
   artifact=Path(q['artifact']).resolve()
   if artifact.suffix.lower()!='.dll' or not artifact.is_file():raise ValueError('Only separately reviewed language DLL artifacts supported')
   source_files={f['sha256'] for f in j['files']}
   sha=hashlib.sha256(artifact.read_bytes()).hexdigest()
   if sha in source_files:raise ValueError('Cannot redistribute original mod DLL as a translation artifact')
   dest=tool.ROOT/'state/artifacts'/(sha+'.dll');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(artifact,dest)
   q['generated_artifact']={'path':str(dest.resolve()),'sha256':sha,'name':artifact.name}
   if q['generated_artifact'] not in j['extra_files']:j['extra_files'].append(q['generated_artifact'])
  if q['decision']=='translate':
   if task['kind']!='custom-field':raise ValueError('Translate decision requires an extracted custom field')
   detail=task['detail'];q.setdefault('rows',[]).append({'type':detail['type'],'key':detail['key'],'text':detail['text'],'role':detail['key'].split('.',1)[-1],'context':detail['context']})
  if q['decision']=='needs_patch':
   if task['kind']!='method' or not q.get('patches'):raise ValueError('Method patch selections required')
   for patch in q['patches']:
    literal=next((l for l in task['detail']['literals'] if l['ordinal']==patch.get('ordinal')),None)
    if literal is None or literal['text']!=patch.get('source'):raise ValueError('Patch must match an actual original IL literal')
    key='LST.'+tool.digest([j['package'],task['id'],patch['ordinal']])[:24]
    row={'type':'Keyed','key':key,'text':literal['text'],'role':'code-literal','context':{'file':task['location'],'class':task['detail']['class'],'method':task['detail']['method'],'method_hash':task['detail']['hash'],'ordinal':patch['ordinal']}}
    row['id']=tool.identity(j['package'],row,j['language'],j['target'],j['glossary']);row['state']='pending'
    q['generated_rows'].append(dict(row))
    if row['id'] not in byid:j['rows'].append(row);byid[row['id']]=row
    selected={'file':task['location'],'class':task['detail']['class'],'token':task['detail']['token'],'ordinal':patch['ordinal'],'source':literal['text'],'key':key}
    q['generated_patches'].append(selected)
    if selected not in j['patches']:j['patches'].append(selected)
  for row in q.get('rows',[]):
   if not all(k in row for k in ('type','key','text','context','role')):raise ValueError('Supplemental catalog requires explicit full context')
   row=dict(row);row['context']=dict(evidence=task['id'],provided=row['context']);row['id']=tool.identity(j['package'],row,j['language'],j['target'],j['glossary']);row['state']='pending'
   q['generated_rows'].append(dict(row))
   if row['id'] not in byid:j['rows'].append(row);byid[row['id']]=row
  next(t for t in j['issues'] if t['id']==q['id'])['decision']=dict(q,status='draft',time=tool.now())
 save(job,j);return report(d,job)

def review(d,job,reason):
 if not reason.strip():raise ValueError('Review reason required')
 job=Path(job);j=state(job);ensure_identity(j)
 selected=[r for r in j['rows'] if r['state']=='draft'];errors=tool.remaining_errors(selected,{r['id']:r['translation'] for r in selected})
 if errors:raise ValueError(str(errors))
 with d:
  for r in selected:
   tool.put(d,r,j['package'],r['translation'],'accepted',str(job),reason);d.execute('INSERT OR IGNORE INTO uses VALUES(?,?)',(r['id'],str(job)))
  for task in j['issues']:
   q=task.get('decision')
   if q and q.get('status')=='draft' and q['decision']!='unresolved':
    q.update(status='accepted',review_reason=reason)
    # Patch/supplemental rows are also restored from the audit entry on future scans.
    old=d.execute('SELECT decision FROM audit WHERE id=?',(task['id'],)).fetchone()
    d.execute('INSERT INTO audit_history(id,old,reason,time) VALUES(?,?,?,?)',(task['id'],old[0] if old else None,reason,tool.now()))
    d.execute('INSERT OR REPLACE INTO audit VALUES(?,?)',(task['id'],json.dumps(q,ensure_ascii=False)))
    d.execute('INSERT OR IGNORE INTO audit_uses VALUES(?,?)',(task['id'],str(job.resolve())))
 for r in selected:r['state']='accepted'
 for task in j['issues']:
  if task.get('decision',{}).get('decision')=='unresolved':
   j['review_log'].append({'unresolved':task['id'],'reason':task['decision']['reason'],'time':tool.now()});task.pop('decision',None)
 j['review_log'].append({'time':tool.now(),'reason':reason,'accepted_rows':[r['id'] for r in selected]});save(job,j);request(d,job);return report(d,job)

def report(d,job):
 job=Path(job);j=load_audit(d,state(job));unresolved=[t for t in j['issues'] if t.get('decision',{}).get('status')!='accepted' or t.get('decision',{}).get('decision')=='unresolved']
 result={'product':VERSION,'job':str(job.resolve()),'package':j['package'],**tool.summary(dict(j,issues=unresolved)),'draft_source_reviews':sum(t.get('decision',{}).get('status')=='draft' for t in j['issues']),'source_files':len(j['files']),'timing':j.get('timing',{}),'requests_manifest':str(job/'requests.json'),'next':'review' if any(r['state']=='draft' for r in j['rows']) or any(t.get('decision',{}).get('status')=='draft' for t in j['issues']) else ('answer_requests' if unresolved or any(r['state']=='pending' for r in j['rows']) else 'finish')}
 tool.write(job/'STATUS.json',result);return result

def check(d,job):
 job=Path(job);j=state(job);ensure_identity(j);problems=[]
 if not j['rows']:problems.append('No translatable rows extracted')
 for t in load_audit(d,j)['issues']:
  if t.get('decision',{}).get('status')!='accepted' or t.get('decision',{}).get('decision')=='unresolved':problems.append('Unreviewed source '+t['id'])
 for r in j['rows']:
  hit=d.execute('SELECT status,translation FROM memory WHERE id=?',(r['id'],)).fetchone()
  if r['state'] not in ('accepted','automatic') or not hit or hit['status']!='accepted' or hit['translation']!=r.get('translation'):problems.append('Unaccepted/revoked/changed '+r['id'])
 if not problems:problems.extend(tool.remaining_errors(j['rows'],{r['id']:r['translation'] for r in j['rows']}))
 for f in j['files']:
  if f.get('selection') is not None:
   p=Path(j['snapshot'])/f['file']
   if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:problems.append('Snapshot changed: '+f['file'])
 original=Path(j['source'])
 if original.exists():
  paths=[original] if original.is_file() else sorted(p for p in original.rglob('*') if p.is_file())
  current=[[str(p.relative_to(original)) if original.is_dir() else p.name,hashlib.sha256(p.read_bytes()).hexdigest()] for p in paths]
  if current!=j.get('input_manifest'):problems.append('Original changed since snapshot; reopen mod for incremental update')
 else:problems.append('Original unavailable; freshness cannot be confirmed')
 for artifact in j.get('extra_files',[]):
  p=Path(artifact['path'])
  if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=artifact['sha256']:problems.append('Reviewed language artifact changed')
 issues={'passed':not problems,'problems':problems,'game_test':'not performed','semantic_review':'accepted records only; no automatic proof of meaning'};tool.write(job/'CHECK.json',issues);return issues

def finish(d,job,package=None):
 job=Path(job);j=state(job);result=check(d,job)
 if not result['passed']:raise ValueError('Check failed; see CHECK.json ('+str(len(result['problems']))+' problems)')
 pkg=package or 'leemonsun.'+re.sub('[^a-z0-9.]','',j['package'].lower())+'.russian'
 if not re.fullmatch('[a-zA-Z0-9_.-]+',pkg) or pkg.lower()==j['package'].lower():raise ValueError('Separate translation packageId required')
 buildkey=tool.digest([[(r['id'],r['translation']) for r in j['rows']],pkg,j.get('patches',[]),j.get('extra_files',[])])[:20];(job/'builds').mkdir(exist_ok=True);out=Path(tempfile.mkdtemp(dir=job/'builds',prefix=buildkey+'-'));mod=out/'Translation'
 groups=collections.defaultdict(list)
 for r in j['rows']:
  if r.get('route'):
   route=Path(r['route']);p=mod/route
   if not p.resolve().is_relative_to((mod/'Languages/Russian').resolve()) or route.suffix.lower()!='.txt':raise ValueError('Unsafe Strings route')
   p.parent.mkdir(parents=True,exist_ok=True);p.write_text(r['translation'],encoding='utf-8');continue
  if not re.fullmatch(r'[A-Za-z_][\w.]*',r['type']) or not re.fullmatch(r'[A-Za-z_][\w.]*',r['key']):raise ValueError('Unsafe XML identity')
  groups[r['type']].append(r)
 def writexml(path,root):path.parent.mkdir(parents=True,exist_ok=True);E.indent(root);E.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True);E.parse(path)
 for kind,rs in groups.items():
  if len({r['key'] for r in rs})!=len(rs):raise ValueError('Active key collision')
  root=E.Element('LanguageData')
  for r in rs:
   node=E.SubElement(root,r['key'])
   if r.get('value_type')=='list':
    for text in r['translation'].split('\n'):E.SubElement(node,'li').text=text
   else:node.text=r['translation']
  writexml(mod/'Languages/Russian'/('Keyed' if kind=='Keyed' else 'DefInjected/'+kind)/'Translation.xml',root)
 root=E.Element('ModMetaData')
 for k,v in {'name':j['mod_name']+' — Русский перевод','author':'Leemonsun','packageId':pkg,'description':'Русский перевод '+j['mod_name']+'. Автор оригинала: '+j['original_author']+'. Требуется оригинал.'}.items():E.SubElement(root,k).text=v
 E.SubElement(E.SubElement(root,'supportedVersions'),'li').text=j['scan_profile']['version'];deps=E.SubElement(root,'modDependencies');after=E.SubElement(root,'loadAfter')
 for dep in [j['package']]+(['brrainz.harmony'] if j['patches'] or j.get('extra_files') else []):E.SubElement(E.SubElement(deps,'li'),'packageId').text=dep;E.SubElement(after,'li').text=dep
 writexml(mod/'About/About.xml',root)
 for artifact in j.get('extra_files',[]):
  name=Path(artifact['name']).name
  if name!=artifact['name']:raise ValueError('Unsafe artifact name')
  dest=mod/'Assemblies'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(artifact['path'],dest)
 if j['patches']:
  from patcher import compile_patch
  compile_patch(j,mod,out)
 archive=out/'Translation.zip'
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
  for p in sorted(mod.rglob('*')):
   if p.is_file():
    info=zipfile.ZipInfo(p.relative_to(mod).as_posix(),date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;z.writestr(info,p.read_bytes())
 with zipfile.ZipFile(archive) as z:
  if z.testzip():raise ValueError('Archive CRC failure')
 result={'translation':str(mod.resolve()),'zip':str(archive.resolve()),'rows':len(j['rows']),'game_test':'not performed','release_ready':False,'next':'game-test and existing publication workflow','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'files':{p.relative_to(mod).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in mod.rglob('*') if p.is_file()}};tool.write(out/'BUILD.json',result);tool.write(job/'LATEST_BUILD.json',result);return result

def verify(d,job,reason=None,dependencies=(),timeout=150):
 job=Path(job);static=check(d,job)
 if not static['passed']:raise ValueError('Static check failed; see CHECK.json')
 build=tool.read(job/'LATEST_BUILD.json');game=tool.read(job/'GAME_TEST.json') if (job/'GAME_TEST.json').exists() else {}
 actual={p.relative_to(Path(build['translation'])).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(build['translation']).rglob('*') if p.is_file()}
 if actual!=build['files']:raise ValueError('Build modified after assembly')
 if not game.get('passed') or game.get('build_sha256')!=build['sha256']:
  from runtime_check import run
  game=run(job,dependencies,timeout)
 if not game['passed']:raise ValueError('Runtime value check failed; inspect GAME_TEST.json')
 reportpath=Path(game['profile'])/'TranslationReport.txt';text=reportpath.read_text(encoding='utf-8-sig');reportsig=hashlib.sha256(reportpath.read_bytes()).hexdigest()
 counts={name:int(count) for name,count in re.findall(r'========== (.*?) \((\d+)\) ==========',text)}
 prior=tool.read(job/'REPORT_REVIEW.json') if (job/'REPORT_REVIEW.json').exists() else {}
 if reason:
  prior={'report_sha256':reportsig,'reason':reason,'time':tool.now()};tool.write(job/'REPORT_REVIEW.json',prior)
 baseline=tool.read(tool.ROOT/'state/runtime_baseline.json') if (tool.ROOT/'state/runtime_baseline.json').exists() else {}
 baseline_matches=bool(baseline.get('passed') and baseline.get('report_sha256')==reportsig and baseline.get('managed_sha256')==hashlib.sha256((scanner.GAME/'RimWorldWin64_Data/Managed/Assembly-CSharp.dll').read_bytes()).hexdigest())
 classified=baseline_matches or not any(counts.values()) or (prior.get('report_sha256')==reportsig and bool(prior.get('reason','').strip()))
 result={'technical_checks_passed':True,'runtime_values_passed':True,'runtime_report_counts':counts,'runtime_report_reviewed':classified,'matches_core_only_baseline':baseline_matches,'ready_for_existing_release_workflow':classified,'translation':build['translation'],'zip':build['zip'],'sha256':build['sha256'],'game_version':game['game_version'],'rows':len(state(job)['rows']),'semantic_review':'Explicit acceptance by Sol; no automatic proof of meaning','next':'Existing cover/publication workflow' if classified else 'Inspect TranslationReport.txt, then verify --report-reason with concrete classification'}
 tool.write(job/'DELIVERY.json',result);return result

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--db');sub=p.add_subparsers(dest='command',required=True)
 a=sub.add_parser('open');a.add_argument('source');a.add_argument('--version',default='1.6');a.add_argument('--mods',nargs='*',default=[]);a.add_argument('--language',default='en');a.add_argument('--job')
 for name in ('status','request','answer','review','check','finish','game-test','verify','api-prepare','api-import'):
  a=sub.add_parser(name);a.add_argument('job',type=Path)
  if name=='answer':a.add_argument('response')
  if name=='review':a.add_argument('--reason',required=True)
  if name=='finish':a.add_argument('--package')
  if name in ('game-test','verify'):a.add_argument('--dependencies',nargs='*',default=[]);a.add_argument('--timeout',type=int,default=150)
  if name=='verify':a.add_argument('--report-reason')
  if name=='api-import':a.add_argument('response')
 a=sub.add_parser('audit-revoke');a.add_argument('id');a.add_argument('--reason',required=True)
 sub.add_parser('doctor');sub.add_parser('backup');args=p.parse_args();d=dbopen(args.db)
 try:
  if args.command=='open':result=open_mod(d,args.source,args.version,args.mods,args.language,args.job)
  elif args.command=='backup':result={'backup':backup(d)}
  elif args.command=='doctor':
   paths={'python':Path(sys.executable),'metadata_reader':tool.ROOT/'native/Metadata/bin/Release/net8.0/Metadata.exe','legacy_conveyor':tool.LEGACY/'tools/conveyor.py','game':scanner.GAME/'RimWorldWin64.exe','compiler':Path('C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe')}
   result={'product':VERSION,'dependencies':{k:p.exists() for k,p in paths.items()},'database_integrity':d.execute('PRAGMA integrity_check').fetchone()[0],'network_required':False}
  elif args.command=='audit-revoke':
   old=d.execute('SELECT decision FROM audit WHERE id=?',(args.id,)).fetchone()
   with d:
    d.execute('INSERT INTO audit_history(id,old,reason,time) VALUES(?,?,?,?)',(args.id,old[0] if old else None,args.reason,tool.now()));d.execute('DELETE FROM audit WHERE id=?',(args.id,))
   result={'affected_jobs':[r[0] for r in d.execute('SELECT job FROM audit_uses WHERE id=?',(args.id,))]}
  else:
   with locked(args.job):
    if args.command=='status':result=report(d,args.job)
    elif args.command=='request':result=request(d,args.job)
    elif args.command=='answer':result=receive(d,args.job,args.response)
    elif args.command=='review':result=review(d,args.job,args.reason)
    elif args.command=='check':result=check(d,args.job)
    elif args.command=='finish':result=finish(d,args.job,args.package)
    elif args.command=='verify':result=verify(d,args.job,args.report_reason,args.dependencies,args.timeout)
    elif args.command=='api-prepare':result=tool.export(args.job)
    elif args.command=='api-import':
     answers=tool.read(args.response);j=state(args.job);pending={r['id'] for r in j['rows'] if r['state']=='pending'}
     if not isinstance(answers,dict) or set(answers)!=pending:raise ValueError('API IDs must exactly match all current pending translations')
     request(d,args.job)
     for path in tool.read(args.job/'requests.json')['active']:
      req=tool.read(path)
      if not req['translations']:continue
      receipt=args.job/'api_response_adapter.json';tool.write(receipt,{'request_id':req['request_id'],'translations':{r['id']:answers[r['id']] for r in req['translations']},'source_review':[]});receive(d,args.job,receipt)
     result=report(d,args.job)
    elif args.command=='game-test':
     from runtime_check import run
     result=run(args.job,args.dependencies,args.timeout)
  print(json.dumps({k:v for k,v in result.items() if k!='files'},ensure_ascii=False));return 0 if result.get('passed',True) else 1
 except (ValueError,OSError,KeyError,TypeError,sqlite3.Error,E.ParseError) as ex:print(json.dumps({'error':str(ex)},ensure_ascii=False));return 1
 finally:d.close()
if __name__=='__main__':sys.exit(main())
