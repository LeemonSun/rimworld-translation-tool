import contextlib,hashlib,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import sol,tool,scanner
class Product(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=tool.ROOT);self.p=Path(self.temp.name);self.db=sol.dbopen(self.p/'db.sqlite3');self.source=self.p/'mod';(self.source/'About').mkdir(parents=True);(self.source/'Languages/English/Keyed').mkdir(parents=True)
  (self.source/'About/About.xml').write_text('<ModMetaData><name>Fixture</name><author>Original author</author><packageId>fixture.original</packageId></ModMetaData>')
  self.keys=self.source/'Languages/English/Keyed/Keys.xml';self.keys.write_text('<LanguageData><Action>Fire</Action><ActionTooltip>Fire the weapon.</ActionTooltip><Other>Unchanged</Other></LanguageData>');self.job=self.p/'job'
 def tearDown(self):self.db.close();self.temp.cleanup()
 def open(self,job=None):return sol.open_mod(self.db,self.source,job=job or self.job)
 def answer_all(self,job=None):
  job=job or self.job
  for name in tool.read(job/'requests.json')['active']:
   req=tool.read(name);ans={'request_id':req['request_id'],'translations':{r['id']:{'Fire':'Стрелять','Fire the weapon.':'Выстрелить из оружия.','Unchanged':'Без изменений','Changed':'Изменено'}.get(r['text'],'Тест') for r in req['translations']},'source_review':[{'id':t['id'],'decision':'technical','reason':'Synthetic fixture non-displayed metadata'} for t in req['source_review']]};p=job/'answer.json';tool.write(p,ans);sol.receive(self.db,job,p)
  sol.review(self.db,job,'Explicit fixture context review')
 def test_complete_repeat_update_and_reproducibility(self):
  self.open();self.answer_all();self.assertTrue(sol.check(self.db,self.job)['passed']);a=sol.finish(self.db,self.job);b=sol.finish(self.db,self.job);self.assertEqual(a['sha256'],b['sha256']);self.assertNotEqual(a['translation'],b['translation'])
  repeat=self.p/'repeat';self.open(repeat);self.assertEqual(sol.report(self.db,repeat)['states'],{'automatic':3});self.assertEqual(sol.request(self.db,repeat)['count'],0)
  self.keys.write_text('<LanguageData><Action>Fire</Action><ActionTooltip>Changed</ActionTooltip><Other>Unchanged</Other></LanguageData>');changed=self.p/'change';self.open(changed);j=sol.state(changed);pending=[r['key'] for r in j['rows'] if r['state']=='pending'];self.assertEqual(set(pending),{'Action','ActionTooltip'})
  with self.assertRaises(ValueError):self.open(self.job)
 def test_bad_answer_receipt_and_identity(self):
  self.open();req=tool.read(tool.read(self.job/'requests.json')['active'][0]);bad=self.job/'bad.json';tool.write(bad,{'request_id':req['request_id'],'translations':{'wrong':'Лишнее'}})
  with self.assertRaises(ValueError):sol.receive(self.db,self.job,bad)
  self.assertEqual(len(list((self.job/'receipts').glob('*.json'))),1);self.assertFalse(sol.check(self.db,self.job)['passed'])
  p=self.p/'duplicate.json';p.write_text('{"same":1,"same":2}')
  with self.assertRaises(ValueError):tool.read(p)
  j=sol.state(self.job);j['rows'][0]['text']='tampered';sol.save(self.job,j)
  with self.assertRaises(ValueError):sol.check(self.db,self.job)
 def test_conditional_versions_and_inheritance(self):
  (self.source/'LoadFolders.xml').write_text('<loadFolders><v1.6><li>/</li><li IfModActive="optional.dep">Compat</li></v1.6></loadFolders>')
  (self.source/'Compat/Defs').mkdir(parents=True);(self.source/'Compat/Defs/D.xml').write_text('<Defs><ThingDef><defName>Optional</defName><label>Optional</label></ThingDef></Defs>')
  (self.source/'Defs').mkdir();(self.source/'Defs/D.xml').write_text('<Defs><ThingDef Name="Parent" Abstract="True"><description>Inherited text</description></ThingDef><ThingDef ParentName="Parent"><defName>Child</defName><label>Child</label></ThingDef></Defs>')
  self.open();j=sol.state(self.job);self.assertNotIn('Optional.label',{r['key'] for r in j['rows']});self.assertIn('Child.description',{r['key'] for r in j['rows']})
  other=self.p/'with-dep';sol.open_mod(self.db,self.source,mods=['optional.dep'],job=other);self.assertIn('Optional.label',{r['key'] for r in sol.state(other)['rows']})
 def test_xml_collision_unwaivable_and_snapshot_changes(self):
  (self.source/'Defs').mkdir();(self.source/'Defs/Broken.xml').write_text('<Defs>');self.open()
  with self.assertRaises(ValueError):self.answer_all()
  self.assertFalse(sol.check(self.db,self.job)['passed'])
 def test_crash_after_commit_recovers(self):
  self.open()
  for name in tool.read(self.job/'requests.json')['active']:
   req=tool.read(name);p=self.job/'response.json';tool.write(p,{'request_id':req['request_id'],'translations':{r['id']:'Тест' for r in req['translations']},'source_review':[]});sol.receive(self.db,self.job,p)
  with patch('sol.save',side_effect=OSError('simulated interrupted atomic write')):
   with self.assertRaises(OSError):sol.review(self.db,self.job,'crash fixture')
  sol.review(self.db,self.job,'recover fixture');self.assertTrue(sol.check(self.db,self.job)['passed'])
 def test_unsafe_zip_and_atomic_file(self):
  z=self.p/'evil.zip'
  with zipfile.ZipFile(z,'w') as f:f.writestr('Key.xml','1');f.writestr('key.xml','2')
  with self.assertRaises(ValueError):tool.safezip(z,self.p/'unpacked')
  p=self.p/'atomic.json';tool.write(p,{'before':True})
  with patch('tool.os.replace',side_effect=OSError('disk failure')):
   with self.assertRaises(OSError):tool.write(p,{'after':True})
  self.assertEqual(tool.read(p),{'before':True})
 def test_unresolved_reappears(self):
  (self.source/'Patches').mkdir();(self.source/'Patches/P.xml').write_text('<Patch />');self.open()
  for path in tool.read(self.job/'requests.json')['active']:
   req=tool.read(path);ans={'request_id':req['request_id'],'translations':{r['id']:'Тест' for r in req['translations']},'source_review':[{'id':t['id'],'decision':'unresolved','reason':'Needs actual patch analysis'} for t in req['source_review']]};p=self.job/'answer.json';tool.write(p,ans);sol.receive(self.db,self.job,p)
  sol.review(self.db,self.job,'Translations accepted, source question remains unresolved')
  active=tool.read(self.job/'requests.json')['active'];self.assertEqual(sum(len(tool.read(p)['source_review']) for p in active),1);self.assertFalse(sol.check(self.db,self.job)['passed'])
 def test_concurrent_job_lock(self):
  with sol.locked(self.job):
   with self.assertRaises(ValueError):
    with sol.locked(self.job):pass
 def test_audit_reuse_and_revoke(self):
  (self.source/'Patches').mkdir();(self.source/'Patches/P.xml').write_text('<Patch><Operation Class="PatchOperationReplace"><xpath>Defs/ThingDef/label</xpath><value><label>Changed</label></value></Operation></Patch>');self.open();self.answer_all();self.assertTrue(sol.check(self.db,self.job)['passed'])
  repeat=self.p/'repeat';self.open(repeat);self.assertEqual(sol.request(self.db,repeat)['count'],0)
  with self.db:self.db.execute('delete from audit')
  self.assertFalse(sol.check(self.db,self.job)['passed'])
if __name__=='__main__':unittest.main(verbosity=2)
