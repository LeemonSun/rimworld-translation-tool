import unittest,tempfile,json,sqlite3,zipfile
from pathlib import Path
import tool
class Workflow(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=tool.ROOT);self.p=Path(self.tmp.name);self.d=tool.dbopen(self.p/'m.db')
 def tearDown(self):self.d.close();self.tmp.cleanup()
 def job(self,rows,name='j',pkg='original',lang='en'):
  src=self.p/(name+'.json');tool.write(src,rows);out=self.p/name;tool.analyze(self.d,src,out,pkg,lang);return out
 @unittest.skipUnless((tool.LEGACY / "work").is_dir(), "Local reviewed catalogs required")
 def test_real_catalogs(self):
  metrics=[]
  for title,pkg in [('LayeredApparel','costel.layeredapparel'),('SimpleLeadership','pb3n.SimpleLeadership'),('InMemoriam','astryl.InMemoriam')]:
   rows=tool.read(tool.LEGACY/'work'/title/'reviewed.json');job=self.job(rows,title,pkg);tool.export(job)
   j=tool.read(job/'job.json');answers={r['id']:old['translation'] for r,old in zip(j['rows'],rows)}
   errors=tool.validate(j['rows'],answers)
   tool.write(self.p/(title+'-validation.json'),errors)
   batch=self.p/'batch.json';response=self.p/'response.json';tool.write(batch,{'strings':j['rows']});tool.write(response,answers);tool.stage(job,batch,response,'Existing fixture has reviewed Russian grammatical macros' if errors else None)
   self.assertEqual(self.d.execute("select count(*) from memory where status='accepted'").fetchone()[0],sum(m['rows'] for m in metrics))
   tool.accept(self.d,job,'Existing own editorial work; explicit fixture acceptance')
   repeat=self.job(rows,title+'-repeat',pkg);r=tool.read(repeat/'job.json');self.assertTrue(all(x['state']=='automatic' for x in r['rows']));self.assertEqual(tool.export(repeat)['strings'],0)
   if title=='LayeredApparel':
    j=tool.read(job/'job.json');j['issues']=[];tool.write(job/'job.json',j);result=tool.build(job,'leemonsun.layeredapparel.russian','Test')
    import xml.etree.ElementTree as E
    expected={r['key']:r['translation'] for r in rows};actual={e.tag:e.text for e in E.parse(Path(result['staging'])/'Languages/Russian/Keyed/Translation.xml').getroot()};self.assertEqual(actual,expected)
   changed=[dict(r) for r in rows];changed[0]=dict(changed[0],text=changed[0]['text']+' changed');c=self.job(changed,title+'-change',pkg);self.assertEqual(tool.export(c)['strings'],1)
   with self.assertRaises(ValueError):tool.stage(c,batch,response)
   metrics.append({'name':title,'rows':len(rows),'baseline_characters':sum(len(r['text']) for r in rows),'repeat_sent_characters':0,'repeat_sent_rows':0,'network_requests':0})
  tool.write(tool.ROOT/'TEST_METRICS.json',metrics)
 def test_context_language_draft_and_rules(self):
  row={'type':'Keyed','key':'fire','text':'Fire','role':'button','context':'weapon command'}
  job=self.job([row]);j=tool.read(job/'job.json');r=j['rows'][0];tool.write(self.p/'b.json',{'strings':[r]});tool.write(self.p/'a.json',{r['id']:'Стрелять'});tool.stage(job,self.p/'b.json',self.p/'a.json')
  before=self.job([row],'before');self.assertEqual(tool.export(before)['strings'],1)
  tool.accept(self.d,job,'reviewed weapon command')
  for name,rows,lang in [('context',[dict(row,context='combustion')],'en'),('lang',[row],'zh-Hans')]:self.assertEqual(tool.export(self.job(rows,name,lang=lang))['strings'],1)
  rule={'source':'Fire','translation':'Стрелять','type':'Keyed','role':'button','context':'weapon command','language':'en','target':'ru','glossary':'terms-v1','packages':['second']}
  with self.d:self.d.execute('insert into rules values(?,?)',('fire',json.dumps(rule)))
  other=self.job([row],'rule',pkg='second');self.assertEqual(tool.export(other)['strings'],0)
  ambiguous=self.job([dict(row,context='combustion')],'ambiguous',pkg='second');self.assertEqual(tool.export(ambiguous)['strings'],1)
 def test_cli_and_revocation(self):
  import subprocess,sys
  def cli(*args,ok=True):
   r=subprocess.run([sys.executable,str(tool.ROOT/'tool.py'),'--db',str(self.p/'cli.db'),*map(str,args)],capture_output=True,text=True,encoding='utf-8')
   self.assertEqual(r.returncode,0 if ok else 1,r.stdout+r.stderr);return json.loads(r.stdout)
  src=self.p/'cli.json';tool.write(src,[{'type':'Keyed','key':'Action','text':'Fire','context':'weapon','role':'button'}]);job=self.p/'cli'
  cli('analyze',src,'--package','weapon.mod','--language','en','--job',job);batch=cli('export',job)['batches'][0];r=tool.read(job/'job.json')['rows'][0];ans=self.p/'answer.json';tool.write(ans,{r['id']:'Стрелять'})
  cli('import',job,batch,ans);cli('accept',job,'--reason','explicit contextual fixture approval');cli('resolve',job,'--index',0,'--evidence','synthetic single-record fixture')
  cli('build',job,'--package','test.ru','--name','test');impact=cli('revoke',r['id'],'--reason','test revocation');self.assertIn(str(job.resolve()),impact['affected_jobs']);cli('build',job,'--package','test.ru','--name','test',ok=False)
 def test_bad_inputs(self):
  self.assertTrue(tool.validate([{'id':'a','text':'{0} {0} <b>10</b>'}],{'a':'{0} <b>11</b>'}))
  job=self.job([{'type':'Keyed','key':'x','text':'Hello'}]);j=tool.read(job/'job.json');tool.write(self.p/'b.json',{'strings':j['rows']});tool.write(self.p/'r.json',{'bad':'wrong'})
  with self.assertRaises(ValueError):tool.stage(job,self.p/'b.json',self.p/'r.json')
  with self.assertRaises(ValueError):tool.build(job,'test.ru','test')
  z=self.p/'bad.zip'
  with zipfile.ZipFile(z,'w') as f:f.writestr('../escape.txt','bad')
  with self.assertRaises(ValueError):tool.safezip(z,self.p/'unpack')
  source=self.p/'source';source.mkdir();(source/'bad.xml').write_text('<bad>');(source/'foo.dll').write_bytes(b'fake')
  result=tool.analyze(self.d,source,self.p/'raw','original','en');self.assertEqual(result['coverage_issues'],2)
if __name__=='__main__':unittest.main(verbosity=2)


