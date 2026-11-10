"""Isolated runtime value verification; never opens the user's profile."""
import hashlib,json,os,shutil,subprocess,time
from pathlib import Path
import xml.etree.ElementTree as E
import scanner,tool
AUDIT=r'''using System;using System.Linq;using System.IO;using System.Xml;using System.Text;using System.Collections;using Verse;using HarmonyLib;using UnityEngine;
[StaticConstructorOnStartup]public static class LeemonsunProductAudit {
 static int errors,keys,fields,lists;static StringBuilder log=new StringBuilder();static LeemonsunProductAudit(){LongEventHandler.ExecuteWhenFinished(Run);}
 static string Norm(string s){return (s??"").Replace("\r\n","\n").Replace("\\n","\n");}
 static void Check(bool ok,string message){if(!ok){errors++;log.AppendLine("FAIL "+message);}}
 static object Step(object obj,string key){int n;if(int.TryParse(key,out n))return ((IList)obj)[n];var f=AccessTools.Field(obj.GetType(),key);if(f!=null)return f.GetValue(obj);var p=AccessTools.Property(obj.GetType(),key);if(p!=null)return p.GetValue(obj,null);throw new Exception("Unknown field "+key);}
 static void Run(){try{var lang=LanguageDatabase.activeLanguage;Check(lang.folderName.StartsWith("Russian"),"Russian language inactive");
 foreach(var file in Directory.GetFiles(Path.Combine(GenFilePaths.SaveDataFolderPath,"expected"),"*.xml",SearchOption.AllDirectories)){
 var doc=new XmlDocument();doc.Load(file);string kind=Path.GetFileName(Path.GetDirectoryName(file));
 foreach(XmlNode child in doc.DocumentElement.ChildNodes){var node=child as XmlElement;if(node==null)continue;try{
 if(kind=="Keyed"){LoadedLanguage.KeyedReplacement value;Check(lang.keyedReplacements.TryGetValue(node.Name,out value)&&Norm(value.value)==Norm(node.InnerText),"KEY "+node.Name);keys++;continue;}
 var typ=GenTypes.GetTypeInAnyAssembly(kind);if(typ==null)throw new Exception("Unresolved full Def type "+kind);var path=node.Name.Split('.');object obj=GenDefDatabase.GetDefSilentFail(typ,path[0]);for(int i=1;i<path.Length;i++)obj=Step(obj,path[i]);
 if(node.HasChildNodes&&node.FirstChild is XmlElement){var values=(IList)obj;Check(values.Count==node.ChildNodes.Count,"List count "+node.Name);for(int i=0;i<Math.Min(values.Count,node.ChildNodes.Count);i++)Check(Norm((string)values[i])==Norm(node.ChildNodes[i].InnerText),"List value "+node.Name+"."+i);lists++;}else{Check(obj is string&&Norm((string)obj)==Norm(node.InnerText),"DEF "+node.Name);fields++;}
 }catch(Exception e){errors++;log.AppendLine("FAIL "+node.Name+": "+e);}}
 }
 var patchFile=Path.Combine(GenFilePaths.SaveDataFolderPath,"PatchExpected.xml");if(File.Exists(patchFile)){
 var patches=new XmlDocument();patches.Load(patchFile);var helper=AccessTools.TypeByName("LeemonsunContextPatch");Check(helper!=null,"Compiled language patch helper loaded");
 foreach(XmlElement node in patches.DocumentElement.ChildNodes){var asm=AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a=>a.GetName().Name==node.GetAttribute("assembly"));Check(asm!=null,"Original patch target loaded");if(asm==null||helper==null)continue;var method=asm.ManifestModule.ResolveMethod(int.Parse(node.GetAttribute("token")));var info=Harmony.GetPatchInfo(method);Check(info!=null&&info.Transpilers.Any(p=>p.owner.StartsWith("leemonsun.context.")),"Actual Harmony transpiler installed");var localize=AccessTools.Method(helper,"Localize");string key=node.GetAttribute("key"),source=node.GetAttribute("source");Check((string)localize.Invoke(null,new object[]{source,key})==key.Translate().ToString(),"Russian patch lookup");var current=LanguageDatabase.activeLanguage;try{LanguageDatabase.activeLanguage=new LoadedLanguage("English");Check((string)localize.Invoke(null,new object[]{source,key})==source,"English patch guard");}finally{LanguageDatabase.activeLanguage=current;}}
 }
 // Full translation report retained separately; third-party errors are not silently treated as product success.
 var sb=new StringBuilder();foreach(string n in new[]{"AppendGeneralLoadErrors","AppendDefInjectionsLoadErros","AppendMissingKeyedTranslations","AppendArgumentCountMismatches"}){var m=AccessTools.Method(typeof(LanguageReportGenerator),n);if(m!=null)m.Invoke(null,new object[]{sb});}File.WriteAllText(Path.Combine(GenFilePaths.SaveDataFolderPath,"TranslationReport.txt"),sb.ToString(),Encoding.UTF8);
 }catch(Exception e){errors++;log.AppendLine(e.ToString());}
 log.AppendLine("KEYS "+keys+" FIELDS "+fields+" LISTS "+lists);log.AppendLine("ERRORS "+errors);File.WriteAllText(Path.Combine(GenFilePaths.SaveDataFolderPath,"audit_result.txt"),log.ToString(),Encoding.UTF8);Application.Quit();}
}'''
def run(job,dependencies=(),timeout=150):
 job=Path(job).resolve();j=tool.read(job/'job.json');build=tool.read(job/'LATEST_BUILD.json');mod=Path(build['translation']);game=scanner.GAME
 if not mod.is_relative_to(job/'builds'):raise ValueError('Build must be inside this job')
 expected=build.get('files')
 if expected is None:raise ValueError('Rebuild using current product before runtime test')
 actual={p.relative_to(mod).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in mod.rglob('*') if p.is_file()}
 if actual!=expected:raise ValueError('Build files changed after assembly')
 language=mod/'Languages/Russian'
 if list((language/'Strings').rglob('*.txt')) if (language/'Strings').exists() else False:raise ValueError('Strings require a dedicated runtime adapter; refusing generic coverage claim')
 root=job/'runtime'/tool.digest([build['sha256'],time.time_ns()])[:16];test=root/'game';profile=root/'profile';test.mkdir(parents=True);(test/'Mods').mkdir();(profile/'Config').mkdir(parents=True)
 def junction(dest,target):
  # Both paths are explicit, absolute. No recursive cleanup/move ever occurs.
  code="New-Item -ItemType Junction -Path '"+str(dest).replace("'","''")+"' -Target '"+str(target).replace("'","''")+"' | Out-Null"
  r=subprocess.run(['powershell','-NoProfile','-Command',code],capture_output=True)
  if r.returncode:raise ValueError('Cannot create isolated game link')
 for name in ('Data','RimWorldWin64_Data','MonoBleedingEdge'):junction(test/name,game/name)
 for name in ('RimWorldWin64.exe','UnityPlayer.dll','UnityCrashHandler64.exe','steam_appid.txt','Version.txt'):shutil.copy2(game/name,test/name)
 harmony=Path('D:/SteamLibrary/steamapps/workshop/content/294100/2009463077');sources=[harmony,*map(Path,dependencies),Path(j['snapshot']),mod];packages=[]
 for i,source in enumerate(sources):
  metadata=scanner.xml((source/'About/About.xml').read_bytes());pkg=metadata.findtext('packageId')
  if pkg.lower() in [x.lower() for x in packages]:continue
  packages.append(pkg);junction(test/'Mods'/('mod'+str(i)),source)
 audit=test/'Mods/ProductAudit';(audit/'Assemblies').mkdir(parents=True);(audit/'About').mkdir();(audit/'About/About.xml').write_text('<ModMetaData><name>Context Tool Runtime Audit</name><author>Leemonsun</author><packageId>leemonsun.context.audit</packageId><supportedVersions><li>1.6</li></supportedVersions></ModMetaData>',encoding='utf-8')
 src=root/'Audit.cs';src.write_text(AUDIT,encoding='utf-8');managed=game/'RimWorldWin64_Data/Managed'
 cmd=['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe','/nologo','/noconfig','/nostdlib+','/target:library']+['/r:'+str(managed/r) for r in ('mscorlib.dll','System.dll','System.Core.dll','System.Xml.dll','netstandard.dll','Assembly-CSharp.dll','UnityEngine.CoreModule.dll')]+['/r:'+str(harmony/'1.6/Assemblies/0Harmony.dll'),'/out:'+str(audit/'Assemblies/ProductAudit.dll'),str(src)]
 r=subprocess.run(cmd,capture_output=True)
 if r.returncode:raise ValueError('Runtime audit compilation failed: '+r.stdout.decode('utf-8',errors='replace')[:2000])
 shutil.copytree(language,profile/'expected')
 if j.get('patches'):
  patches=E.Element('Patches')
  for patch in j['patches']:
   metadata=scanner.inspect(Path(j['snapshot'])/patch['file'],tool.ROOT/'state/metadata');E.SubElement(patches,'Patch',assembly=metadata['assembly'],token=str(patch['token']),key=patch['key'],source=patch['source'])
  E.ElementTree(patches).write(profile/'PatchExpected.xml',encoding='utf-8',xml_declaration=True)
 def writexml(path,root):E.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
 config=E.Element('ModsConfigData');E.SubElement(config,'version').text=(game/'Version.txt').read_text().strip();active=E.SubElement(config,'activeMods')
 order=[packages[0],'ludeon.rimworld',*packages[1:],'leemonsun.context.audit']
 for pkg in order:E.SubElement(active,'li').text=pkg.lower()
 writexml(profile/'Config/ModsConfig.xml',config)
 prefs=E.Element('PrefsData')
 for k,v in {'langFolderName':'Russian (Русский)','fullscreen':'False','screenWidth':'960','screenHeight':'540','volumeGame':'0','volumeMusic':'0','pauseOnError':'False','devMode':'True'}.items():E.SubElement(prefs,k).text=v
 writexml(profile/'Config/Prefs.xml',prefs)
 startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
 process=subprocess.Popen([str(test/'RimWorldWin64.exe'),'-savedatafolder='+str(profile),'-logFile',str(profile/'Player.log')],cwd=test,startupinfo=startup)
 started=time.monotonic();resultpath=profile/'audit_result.txt'
 try:
  while process.poll() is None and time.monotonic()-started<timeout:time.sleep(.5)
  if process.poll() is None:process.terminate();process.wait(timeout=10);raise ValueError('Isolated runtime test timed out; inspect '+str(profile/'Player.log'))
  if not resultpath.exists():raise ValueError('Game exited without audit result; inspect '+str(profile/'Player.log'))
  text=resultpath.read_text(encoding='utf-8-sig');passed=text.rstrip().endswith('ERRORS 0')
  result={'passed':passed,'profile':str(profile),'game_version':(game/'Version.txt').read_text().strip(),'seconds':round(time.monotonic()-started,2),'build_sha256':build['sha256'],'audit':text,'limits':'Loaded translation values checked; not full gameplay or visual review; translation report and Player.log require classification'}
  tool.write(job/'GAME_TEST.json',result);return result
 finally:
  if process.poll() is None:process.terminate()
