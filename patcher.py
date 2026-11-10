"""Generate only explicitly approved, assembly-pinned method/literal patches."""
import hashlib,json,subprocess
from pathlib import Path
import os
import tool,scanner

def compile_patch(j,mod,out):
 selected=j['patches'];assemblies={r['file']:r for r in j['files']};rules=[]
 for p in selected:
  if p['file'] not in assemblies:raise ValueError('Patch assembly absent from snapshot')
  original=Path(j['snapshot'])/p['file'];metadata=scanner.inspect(original,tool.ROOT/'state/metadata');assembly=metadata['assembly']
  rules.append('{'+','.join([json.dumps(assembly),json.dumps(assemblies[p['file']]['sha256']),str(p['token']),str(p['ordinal']),json.dumps(p['source'],ensure_ascii=True),json.dumps(p['key'])])+'}')
 # Rules are object[] rows; matching is pinned by assembly SHA and method token plus literal ordinal.
 code=r'''using System;using System.IO;using System.Linq;using System.Reflection;using System.Reflection.Emit;using System.Collections.Generic;using System.Security.Cryptography;using Verse;using HarmonyLib;
 [StaticConstructorOnStartup]public static class LeemonsunContextPatch {
 static object[][] rules=new object[][]{RULES};static Dictionary<MethodBase,List<object[]>> map=new Dictionary<MethodBase,List<object[]>>();
 static LeemonsunContextPatch(){try{var harmony=new Harmony("PATCHID");foreach(var group in rules.GroupBy(r=>(string)r[0])){var asm=AppDomain.CurrentDomain.GetAssemblies().FirstOrDefault(a=>a.GetName().Name==group.Key);if(asm==null)throw new Exception("Original assembly not loaded: "+group.Key);var actual=BitConverter.ToString(SHA256.Create().ComputeHash(File.ReadAllBytes(asm.Location))).Replace("-","").ToLowerInvariant();foreach(var r in group){if(actual!=(string)r[1])throw new Exception("Original DLL changed; refusing outdated patch");var method=asm.ManifestModule.ResolveMethod((int)r[2]);if(!map.ContainsKey(method))map[method]=new List<object[]>();map[method].Add(r);}}foreach(var pair in map)harmony.Patch(pair.Key,transpiler:new HarmonyMethod(typeof(LeemonsunContextPatch),"Transpile"));}catch(Exception e){Log.Error("Leemonsun translation patch failed: "+e);}}
 static IEnumerable<CodeInstruction> Transpile(IEnumerable<CodeInstruction> instructions,MethodBase __originalMethod){int ordinal=0,hits=0;var wanted=map[__originalMethod];foreach(var i in instructions){if(i.opcode==OpCodes.Ldstr){var rule=wanted.FirstOrDefault(r=>(int)r[3]==ordinal);ordinal++;if(rule!=null){if((string)i.operand!=(string)rule[4])throw new Exception("Literal mismatch");hits++;yield return i;yield return new CodeInstruction(OpCodes.Ldstr,(string)rule[5]);yield return new CodeInstruction(OpCodes.Call,AccessTools.Method(typeof(LeemonsunContextPatch),"Localize"));continue;}}yield return i;}if(hits!=wanted.Count)throw new Exception("Patch did not cover every approved literal");}
 static string Localize(string original,string key){if(LanguageDatabase.activeLanguage!=null&&LanguageDatabase.activeLanguage.folderName.StartsWith("Russian",StringComparison.OrdinalIgnoreCase))return key.Translate().ToString();return original;}
 }
 '''.replace('RULES',','.join('new object[]'+r for r in rules)).replace('PATCHID','leemonsun.context.'+tool.digest(selected)[:16])
 src=out/'LanguagePatch.cs';src.write_text(code,encoding='utf-8');dest=mod/'Assemblies/Leemonsun.ContextTranslation.dll';dest.parent.mkdir(parents=True,exist_ok=True)
 managed=scanner.GAME/'RimWorldWin64_Data/Managed';refs=['mscorlib.dll','System.dll','System.Core.dll','netstandard.dll','Assembly-CSharp.dll','UnityEngine.CoreModule.dll'];harmony=Path(os.environ.get('RIMWORLD_HARMONY_DLL', str(scanner.GAME.parent.parent/'workshop/content/294100/2009463077/1.6/Assemblies/0Harmony.dll')))
 cmd=['C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe','/nologo','/noconfig','/nostdlib+','/target:library','/optimize+']+['/r:'+str(managed/r) for r in refs]+['/r:'+str(harmony),'/out:'+str(dest),str(src)]
 signature=tool.digest([code,[(str(p),hashlib.sha256(p.read_bytes()).hexdigest()) for p in [*[managed/r for r in refs],harmony]]]);cached=tool.ROOT/'state/patches'/(signature+'.dll');receipt=cached.with_suffix('.json')
 if cached.exists() and receipt.exists() and hashlib.sha256(cached.read_bytes()).hexdigest()==tool.read(receipt)['sha256']:
  import shutil
  shutil.copy2(cached,dest);return
 result=subprocess.run(cmd,capture_output=True,timeout=60)
 if result.returncode:raise ValueError('Approved language patch compilation failed: '+result.stdout.decode('utf-8',errors='replace')[:2000])
 import shutil
 cached.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dest,cached);tool.write(receipt,{'sha256':hashlib.sha256(cached.read_bytes()).hexdigest()})

