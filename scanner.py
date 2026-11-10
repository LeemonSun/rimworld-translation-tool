"""Conservative source scanner. Unsupported cases become stable, reviewable tasks."""
import copy,hashlib,json,re,subprocess,zipfile
from pathlib import Path
import xml.etree.ElementTree as E
import os
import tool
GAME=Path(os.environ.get('RIMWORLD_GAME_DIR', 'C:/Program Files (x86)/Steam/steamapps/common/RimWorld'))
RESOURCES={'.png','.jpg','.jpeg','.dds','.wav','.ogg','.mp3','.mat','.shader','.ttf','.otf','.pdb','.psd','.svg'}
DOCS={'.md','.txt','.pdf','.html','.url','.license'}
FIELDS={'label','description','labelShort','shortLabel','jobString','reportString','gerund','verb','noun','pawnLabel','deathMessage','letterLabel','letterText','message','inspectString','formatString','title','titleShort','titleFemale','titleShortFemale','baseDesc','skillLabel','adjective','nameMaker','fixedName','rulePack','labelMale','labelFemale','labelPlural','descriptionHyperlinks'}
TECHNICAL={'defName','texPath','texturePath','graphicClass','shaderType','sound','soundCast','thingClass','workerClass','driverClass','jobDef','labelColor','iconPath','Class','category','gender','defaultFactionType','race','kindDef','faction','drawSize','color','modContentPack','packageId'}

def xml(raw):
 if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML DTD/entities forbidden')
 return E.fromstring(raw)
def inspect(path,cache):
 sha=hashlib.sha256(path.read_bytes()).hexdigest();reader=tool.ROOT/'native/Metadata/bin/Release/net8.0/Metadata.dll';p=cache/(sha+'-'+hashlib.sha256(reader.read_bytes()).hexdigest()[:12]+'.json')
 if not p.exists():
  exe=tool.ROOT/'native/Metadata/bin/Release/net8.0/Metadata.exe'
  r=subprocess.run([str(exe),str(path)],capture_output=True,timeout=90)
  if r.returncode:raise ValueError('Metadata reader failed: '+r.stderr.decode('utf-8',errors='replace')[:300])
  tool.write(p,json.loads(r.stdout.decode('utf-8-sig')))
 return tool.read(p)
def conditions(attrs,active):
 for name,positive in [('IfModActive',True),('MayRequire',True),('IfModNotActive',False)]:
  if name in attrs:
   ids=[v.strip().lower() for v in re.split('[,;]',attrs[name]) if v.strip()];hit=all(v in active for v in ids)
   if positive!=hit:return False
 return True

def scan(source,work,version,active,language):
 source=Path(source).resolve();work=Path(work);work.mkdir(parents=True,exist_ok=True)
 if source.is_file():
  dest=work/'unpacked';tool.safezip(source,dest);source=dest
 about=list(source.glob('**/About/About.xml'))
 if len(about)!=1:raise ValueError('Input must contain exactly one mod About/About.xml')
 source=about[0].parent.parent
 if work.resolve().is_relative_to(source):raise ValueError('Job cannot be inside original mod')
 meta=xml((source/'About/About.xml').read_bytes());pkg=meta.findtext('packageId')
 if not pkg:raise ValueError('Original packageId missing')
 active={x.lower() for x in active}|{pkg.lower(),'ludeon.rimworld'}
 roots=[''];load=source/'LoadFolders.xml'
 if load.exists():
  lf=xml(load.read_bytes());branch=lf.find('v'+version)
  if branch is None:branch=lf.find('default')
  if branch is None:raise ValueError('LoadFolders has no selected version/default')
  roots=[(n.text or '').strip().strip('/') for n in branch if conditions(n.attrib,active)]
 else:
  versions=[p.name for p in source.iterdir() if p.is_dir() and re.fullmatch(r'\d+\.\d+',p.name) and tuple(map(int,p.name.split('.')))<=tuple(map(int,version.split('.'))) ]
  if versions:roots.append(max(versions,key=lambda x:tuple(map(int,x.split('.')))))
 roots=list(dict.fromkeys(roots));inventory=[];tasks=[];rows=[];assemblies=[];key_calls={};type_names={};custom_fields=set();files={}
 def task(kind,location,detail):
  data={'kind':kind,'location':location,'detail':detail};data['id']=tool.digest([pkg,data]);tasks.append(data)
 def add(kind,key,text,role,context,route=None,value_type=None):
  row={'type':kind,'key':key,'text':text,'role':role,'context':context}
  if route:row['route']=route
  if value_type:row['value_type']=value_type
  rows.append(row)
 # Select only known game load categories from the chosen roots; still inventory every file.
 categories={'About','Assemblies','Defs','Patches','Languages','Textures','Sounds','Source'}
 for p in sorted(source.rglob('*')):
  if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('Links in input must be materialized first')
  if not p.is_file():continue
  if not p.resolve().is_relative_to(source):raise ValueError('External input link')
  rel=p.relative_to(source).as_posix();raw=p.read_bytes();sha=hashlib.sha256(raw).hexdigest();selected=None
  for root in roots:
   prefix=root+'/' if root else ''
   if rel.startswith(prefix):
    rest=rel[len(prefix):]
    if rest.split('/')[0] in categories or '/' not in rest:selected=(root,rest)
  status='inactive' if selected is None else 'pending'
  # Store immutable hashes; relevant raw data is copied to job without linking originals.
  if selected:
   dest=work/'snapshot'/rel;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);files[rel]=(dest,selected,sha)
  inventory.append({'file':rel,'sha256':sha,'bytes':len(raw),'selection':selected,'status':status})
 # DLL metadata contains all methods and strings, without executing the original assembly.
 for rel,(p,(branch,rest),sha) in files.items():
  if rest.startswith('Assemblies/') and p.suffix.lower()=='.dll':
   try:
    data=inspect(p,tool.ROOT/'state/metadata');assemblies.append({'file':rel,'sha256':sha,'data':data})
    if data['errors']:task('assembly-errors',rel,data['errors'])
    for attribute in data.get('attributes',[]):task('custom-attribute',rel,attribute)
    for resource in data.get('resources',[]):task('embedded-resource',rel,resource)
    for t in data.get('types',[]):type_names[t['name'].rsplit('.',1)[-1]]=t['name'].lstrip('.')
    for f in data.get('fields',[]):
     if f['translate']:custom_fields.add(f['name'])
    for m in data['methods']:
     strings=[x['text'] for x in m['literals']]
     for lit in m['literals']:
      key_calls.setdefault(lit['text'],[]).append({'class':m['type'],'method':m['method'],'hash':m['hash']})
     # Entire method is a unit of meaning. Review cached per method, not assembly timestamp.
     task('method',rel,{'class':m['type'],'method':m['method'],'token':m['token'],'hash':m['hash'],'literals':m['literals'],'calls':list(dict.fromkeys(c['target'] for c in m['calls'] if any(x in c['target'] for x in ('Translate','Scribe','Widgets','Messages','Log::','String::','Named','Grammar','Letter'))))[:30],'full_method_inventory':'assemblies.json'})
   except (ValueError,OSError,subprocess.TimeoutExpired) as ex:task('assembly-errors',rel,str(ex))
 # Parse source files and keep all unknowns visible.
 definitions=[]
 for rel,(p,(branch,rest),sha) in files.items():
  ext=p.suffix.lower();status='processed';raw=p.read_bytes()
  if ext in RESOURCES:status='resource'
  elif rest.startswith('About/') or rest=='LoadFolders.xml':status='metadata'
  elif rest.startswith(('Textures/','Sounds/')):status='resource'
  elif rest.startswith('Source/'):
   status='reference-only'
   if not assemblies:task('source-without-assembly',rel,{'sha256':sha,'reason':'source is not proof of installed code'})
  elif ext=='.dll':status='metadata-inspected'
  elif ext=='.zip':
   try:
    with zipfile.ZipFile(p) as z:
     if any(Path(n.filename).suffix.lower() not in RESOURCES and not n.is_dir() for n in z.infolist()):task('nested-archive',rel,{'sha256':sha,'entries':[n.filename for n in z.infolist()]})
   except zipfile.BadZipFile:task('invalid-archive',rel,sha)
  elif rest.startswith('Languages/'):
   parts=rest.split('/');lang='English' if language=='en' else 'ChineseSimplified'
   if len(parts)<3 or parts[1]!=lang:status='other-language'
   elif parts[2]=='Strings' and ext=='.txt':
    text=raw.decode('utf-8-sig');route='/'.join(['Languages','Russian',*parts[2:]])
    add('Strings','file_'+tool.digest(route)[:16],text,'strings-file',{'file':rel},route=route)
   elif ext=='.xml' and parts[2] in ('Keyed','DefInjected'):
    try:
     tree=xml(raw)
     if tree.tag!='LanguageData':raise ValueError('Expected LanguageData')
     kind='Keyed' if parts[2]=='Keyed' else parts[3]
     for node in tree:
      if not conditions(node.attrib,active):continue
      if list(node):task('language-list',rel,{'key':node.tag,'xml':E.tostring(node,encoding='unicode')});continue
      related=[{'key':n.tag,'text':n.text or ''} for n in tree if n is not node and (n.tag in (node.tag+'Desc',node.tag+'Description',node.tag+'Tooltip',re.sub(r'(Desc|Description|Tooltip)$','',node.tag)))]
      refs=list(key_calls.get(node.tag,[]))
      # Dynamic key helpers: prefix literal must be part of this key; false positives only cause conservative invalidation.
      for prefix,methods in key_calls.items():
       if prefix and node.tag.startswith(prefix) and prefix.endswith('.') and prefix!=node.tag:refs+=methods
      add(kind,node.tag,node.text or '','keyed' if kind=='Keyed' else node.tag.rsplit('.',1)[-1],{'file':rel,'branch':branch,'related':related,'calls':refs,'conditions':node.attrib})
    except (E.ParseError,ValueError) as ex:task('invalid-language-xml',rel,str(ex))
   else:task('language-schema',rel,{'sha256':sha,'schema':parts[2]})
  elif ext=='.xml':
   try:
    tree=xml(raw)
    if tree.tag=='Defs':
     for de in tree:
      if conditions(de.attrib,active):definitions.append((rel,de))
    elif tree.tag=='Patch':task('patch',rel,{'sha256':sha,'xml':E.tostring(tree,encoding='unicode')})
    else:task('xml-schema',rel,{'sha256':sha,'root':tree.tag})
   except (E.ParseError,ValueError) as ex:task('invalid-xml',rel,str(ex))
  elif ext not in DOCS:task('unknown-file',rel,{'sha256':sha,'bytes':len(raw)})
  for record in inventory:
   if record['file']==rel:record['status']=status;break
 # Resolve templates locally, preserving nested data; ambiguous inheritance stays a review task.
 templates={de.attrib['Name']:de for _,de in definitions if de.attrib.get('Name')}
 def merged(de,trail=()):
  parent=de.attrib.get('ParentName')
  if not parent:return copy.deepcopy(de)
  if parent not in templates or parent in trail:raise ValueError('unresolved/cyclic ParentName '+parent)
  base=merged(templates[parent],trail+(parent,));out=copy.deepcopy(de)
  for child in base:
   if out.find(child.tag) is None:out.append(copy.deepcopy(child))
  # Nested inheritance is intentionally not guessed.
  for child in de:
   old=base.find(child.tag)
   if old is not None and list(child) and list(old):task('nested-inheritance',de.findtext('defName') or parent,{'field':child.tag})
  return out
 for rel,de in definitions:
  if de.attrib.get('Abstract','').lower()=='true':continue
  name=de.findtext('defName')
  if not name:task('unnamed-def',rel,E.tostring(de,encoding='unicode'));continue
  try:de=merged(de)
  except ValueError as ex:task('inheritance',rel,str(ex));continue
  kind=type_names.get(de.tag,de.tag);context={'file':rel,'defName':name,'type':kind,'definition':E.tostring(de,encoding='unicode')}
  def walk(node,path):
   if not conditions(node.attrib,active):return
   if not list(node):
    text=node.text or '';field=node.tag
    if not text.strip():return
    if field in FIELDS|custom_fields and field not in {'descriptionHyperlinks','rulePack','nameMaker'}:
     add(kind,name+'.'+path,text,field,context)
    elif field not in TECHNICAL and not re.fullmatch(r'[-+\d. ,()]+|true|false',text):task('custom-field',rel,{'type':kind,'key':name+'.'+path,'text':text,'context':context})
    return
   if node.tag=='rulesStrings' and all(n.tag=='li' and not list(n) for n in node):
    add(kind,name+'.'+path,'\n'.join(n.text or '' for n in node),'grammar-list',context,value_type='list');return
   for i,child in enumerate(node):walk(child,path+'.'+(str(i) if child.tag=='li' else child.tag))
  for node in de:
   if node.tag!='defName':walk(node,node.tag)
 # Active key collisions must never be silently overwritten.
 seen={}
 for row in rows:
  slot=(row['type'],row['key']);prior=seen.get(slot)
  if prior is not None:task('collision',str(slot),{'first':prior,'second':row})
  seen[slot]=row
 profile={'version':version,'active_mods':sorted(active),'language':language,'roots':roots}
 return {'package':pkg,'name':meta.findtext('name') or pkg,'author':meta.findtext('author') or meta.findtext('authors/li') or 'не указан','source':str(source),'profile':profile,'rows':rows,'tasks':tasks,'inventory':inventory,'assemblies':assemblies,'snapshot':str((work/'snapshot').resolve())}
