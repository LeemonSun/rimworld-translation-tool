"""Offline translation memory and compact API batches. No network or Steam side effects."""
from pathlib import Path
import argparse,collections,hashlib,json,re,sqlite3,sys

ROOT=Path(__file__).resolve().parent
SOURCES = {'example.mod': 'examples/reviewed.json'}

def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def write(path,data):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def identity(package,row):
 # Deliberately keep context: do not silently reuse a phrase from another mod/key.
 return hashlib.sha256(json.dumps([package,row['type'],row['key'],row['text'],'terms-v1'],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def database():
 p=ROOT/'state/translation_memory.sqlite3';p.parent.mkdir(exist_ok=True)
 db=sqlite3.connect(p);db.execute('CREATE TABLE IF NOT EXISTS reviewed(id TEXT PRIMARY KEY,package TEXT NOT NULL,kind TEXT NOT NULL,key TEXT NOT NULL,source TEXT NOT NULL,translation TEXT NOT NULL,origin TEXT NOT NULL)');return db
def remember(db):
 count=0
 for package,file in SOURCES.items():
  for row in read(ROOT/file):
   translation=row['translation'];assert isinstance(translation,str) and translation.strip(),(file,row['key'])
   db.execute('INSERT OR REPLACE INTO reviewed VALUES(?,?,?,?,?,?,?)',(identity(package,row),package,row['type'],row['key'],row['text'],translation,file));count+=1
 db.commit();return count
def normalize(s):return s.replace('\r\n','\n').replace('\\n','\n')
def check(batch,response):
 expected={r['id']:r for r in batch['strings']};errors=[]
 if not isinstance(response,dict):return ['response must be an object ID -> translation; positional arrays are rejected']
 for id in sorted(set(expected)-set(response)):errors.append({'id':id,'error':'missing'})
 for id in sorted(set(response)-set(expected)):errors.append({'id':id,'error':'unknown ID'})
 for id in expected.keys()&response.keys():
  source=expected[id]['text'];v=response[id]
  if not isinstance(v,str) or not v.strip():errors.append({'id':id,'error':'empty or non-string'});continue
  if collections.Counter(re.findall(r'\{[^{}]+\}',source))!=collections.Counter(re.findall(r'\{[^{}]+\}',v)):errors.append({'id':id,'error':'placeholders differ; grammar changes require explicit review'})
  if normalize(source).count('\n')!=normalize(v).count('\n'):errors.append({'id':id,'error':'line breaks differ'})
  if re.match(r'^\s*',source).group()!=re.match(r'^\s*',v).group() or re.search(r'\s*$',source).group()!=re.search(r'\s*$',v).group():errors.append({'id':id,'error':'boundary whitespace differs'})
 return errors
def main():
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='cmd',required=True)
 sub.add_parser('remember');sub.add_parser('selftest')
 p=sub.add_parser('prepare');p.add_argument('catalog');p.add_argument('--package',required=True);p.add_argument('--out',required=True);p.add_argument('--batch-size',type=int,default=60)
 p=sub.add_parser('check');p.add_argument('batch');p.add_argument('response')
 args=parser.parse_args()
 if args.cmd=='check':
  errors=check(read(args.batch),read(args.response));print(json.dumps({'errors':errors,'count':len(errors)},ensure_ascii=False));return bool(errors)
 db=database()
 try:
  if args.cmd=='remember':print(json.dumps({'imported_reviewed_records':remember(db),'stored':db.execute('SELECT count(*) FROM reviewed').fetchone()[0]}));return 0
  if args.cmd=='selftest':
   n=remember(db);hits=0
   for package,file in SOURCES.items():
    for row in read(ROOT/file):
     assert db.execute('SELECT translation FROM reviewed WHERE id=?',(identity(package,row),)).fetchone()[0]==row['translation'];hits+=1
   row=read(ROOT/next(iter(SOURCES.values())))[0];changed=dict(row,text=row['text']+' changed');assert identity('x',row)!=identity('y',row);assert identity('x',row)!=identity('x',changed)
   b={'strings':[{'id':'a','text':'Hello {0}'},{'id':'b','text':'World'}]};assert check(b,{'a':'Привет {0}','b':'Мир'})==[];assert check(b,{'a':'Привет','b':'Мир'});assert check(b,['Привет {0}','Мир']);assert check(b,{'a':'Привет {0}','b':'Мир','extra':'лишнее'})
   print(json.dumps({'reviewed_cache_roundtrip':hits,'context_and_source_isolation':True,'bad_response_detection':True,'network_requests':0}));return 0
  assert args.batch_size>0 and args.batch_size<=200,'batch size must be 1..200'
  rows=read(args.catalog);cached=[];pending=[];seen=set()
  for row in rows:
   id=identity(args.package,row)
   if id in seen:continue
   seen.add(id);found=db.execute('SELECT translation FROM reviewed WHERE id=?',(id,)).fetchone()
   item=dict(row,id=id)
   if found:cached.append(dict(item,translation=found[0]))
   else:pending.append(item)
  out=Path(args.out);write(out/'cached_reviewed.json',cached)
  # Per-run manifest prevents stale batch files from being mistaken for the current party.
  run=hashlib.sha256(json.dumps(pending,ensure_ascii=False,sort_keys=True).encode()).hexdigest()[:16];files=[]
  for i in range(0,len(pending),args.batch_size):
   file=out/('batch-'+run+'-'+str(i//args.batch_size+1).zfill(3)+'.json');write(file,{'package':args.package,'response_format':'JSON object: id -> Russian translation. Preserve every id; no positional arrays.','strings':pending[i:i+args.batch_size]});files.append(str(file))
  summary={'catalog_rows':len(rows),'unique_contexts':len(seen),'cached_reviewed':len(cached),'new_or_changed':len(pending),'batch_files':files,'network_requests':0};write(out/'manifest.json',summary);print(json.dumps(summary,ensure_ascii=False));return 0
 finally:db.close()
if __name__=='__main__':sys.exit(main())
