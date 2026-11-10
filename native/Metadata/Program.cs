using System.Reflection;
using System.Reflection.Emit;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Security.Cryptography;
using System.Text.Json;
var codes=typeof(OpCodes).GetFields(BindingFlags.Public|BindingFlags.Static).Where(f=>f.FieldType==typeof(OpCode)).Select(f=>(OpCode)f.GetValue(null)).ToDictionary(o=>o.Value);
using var stream=File.OpenRead(args[0]);using var pe=new PEReader(stream);var md=pe.GetMetadataReader();
string TypeName(EntityHandle h){
 if(h.IsNil)return "";
 if(h.Kind==HandleKind.TypeReference){var t=md.GetTypeReference((TypeReferenceHandle)h);return md.GetString(t.Namespace)+"."+md.GetString(t.Name);}
 if(h.Kind==HandleKind.TypeDefinition){var t=md.GetTypeDefinition((TypeDefinitionHandle)h);var parent=t.GetDeclaringType();return (parent.IsNil?md.GetString(t.Namespace):TypeName(parent))+"."+md.GetString(t.Name);}
 return "token:"+MetadataTokens.GetToken(h).ToString("X8");
}
string MemberName(EntityHandle h){
 if(h.Kind==HandleKind.MemberReference){var m=md.GetMemberReference((MemberReferenceHandle)h);return TypeName(m.Parent)+"::"+md.GetString(m.Name);}
 if(h.Kind==HandleKind.MethodDefinition){var m=md.GetMethodDefinition((MethodDefinitionHandle)h);return TypeName(m.GetDeclaringType())+"::"+md.GetString(m.Name);}
 if(h.Kind==HandleKind.MethodSpecification)return MemberName(md.GetMethodSpecification((MethodSpecificationHandle)h).Method);
 return "token:"+MetadataTokens.GetToken(h).ToString("X8");
}
var attributes=new List<object>();
var attributeProvider=new AttrTypes();
void Gather(object value,List<string> strings){if(value is string text)strings.Add(text);else if(value is System.Collections.Immutable.ImmutableArray<CustomAttributeTypedArgument<string>> items)foreach(var item in items)Gather(item.Value,strings);}
void ReadAttrs(CustomAttributeHandleCollection handles,string location){foreach(var ah in handles){var a=md.GetCustomAttribute(ah);try{var decoded=a.DecodeValue(attributeProvider);var strings=new List<string>();foreach(var v in decoded.FixedArguments)Gather(v.Value,strings);foreach(var v in decoded.NamedArguments)Gather(v.Value,strings);if(strings.Count>0)attributes.Add(new {location,constructor=MemberName(a.Constructor),strings,blob=Convert.ToHexString(md.GetBlobBytes(a.Value)).ToLowerInvariant()});}catch(Exception e){attributes.Add(new {location,constructor=MemberName(a.Constructor),decode_error=e.Message,blob=Convert.ToHexString(md.GetBlobBytes(a.Value)).ToLowerInvariant()});}}}
ReadAttrs(md.GetAssemblyDefinition().GetCustomAttributes(),"assembly");
var methods=new List<object>();var fields=new List<object>();var errors=new List<string>();var types=new List<object>();
foreach(var th in md.TypeDefinitions){var t=md.GetTypeDefinition(th);var name=TypeName(th);ReadAttrs(t.GetCustomAttributes(),name);types.Add(new {name,baseType=TypeName(t.BaseType)});
 foreach(var fh in t.GetFields()){var f=md.GetFieldDefinition(fh);ReadAttrs(f.GetCustomAttributes(),name+"::"+md.GetString(f.Name));fields.Add(new {type=name,name=md.GetString(f.Name),translate=f.GetCustomAttributes().Any(a=>MemberName(md.GetCustomAttribute(a).Constructor).Contains("MustTranslateAttribute"))});}
 foreach(var mh in t.GetMethods()){var m=md.GetMethodDefinition(mh);ReadAttrs(m.GetCustomAttributes(),name+"::"+md.GetString(m.Name));if(m.RelativeVirtualAddress==0)continue;
  try{var il=pe.GetMethodBody(m.RelativeVirtualAddress).GetILBytes();var literals=new List<object>();var calls=new List<object>();int pos=0,ordinal=0;
   while(pos<il.Length){int at=pos;short code=il[pos++];if(code==0xfe)code=(short)(0xfe00|il[pos++]);var op=codes[code];int n=op.OperandType switch {OperandType.InlineNone=>0,OperandType.ShortInlineI or OperandType.ShortInlineBrTarget or OperandType.ShortInlineVar=>1,OperandType.InlineVar=>2,OperandType.InlineI8 or OperandType.InlineR=>8,OperandType.InlineSwitch=>4+4*BitConverter.ToInt32(il,pos),_=>4};
    if(op==OpCodes.Ldstr)literals.Add(new {offset=at,ordinal=ordinal++,text=md.GetUserString(MetadataTokens.UserStringHandle(BitConverter.ToInt32(il,pos)&0xffffff))});
    if(op.OperandType==OperandType.InlineMethod)calls.Add(new {offset=at,target=MemberName(MetadataTokens.EntityHandle(BitConverter.ToInt32(il,pos)))});
    pos+=n;if(pos>il.Length)throw new Exception("Truncated IL");
   }
   if(literals.Count>0)methods.Add(new {type=name,method=md.GetString(m.Name),token=MetadataTokens.GetToken(mh),hash=Convert.ToHexString(SHA256.HashData(il)).ToLowerInvariant(),literals,calls});
  }catch(Exception e){errors.Add(name+"::"+md.GetString(m.Name)+": "+e.Message);}
 }
}
var resources=new List<object>();
foreach(var rh in md.ManifestResources){var r=md.GetManifestResource(rh);resources.Add(new {name=md.GetString(r.Name),embedded=r.Implementation.IsNil,offset=r.Offset});}
Console.OutputEncoding=new System.Text.UTF8Encoding(false);Console.Write(JsonSerializer.Serialize(new {assembly=md.GetString(md.GetAssemblyDefinition().Name),methods,fields,types,attributes,resources,errors}));

class AttrTypes:ICustomAttributeTypeProvider<string>{
 public string GetPrimitiveType(PrimitiveTypeCode c)=>c.ToString();
 public string GetSystemType()=>"System.Type";
 public bool IsSystemType(string t)=>t=="System.Type";
 public string GetSZArrayType(string e)=>e+"[]";
 public string GetTypeFromDefinition(MetadataReader r,TypeDefinitionHandle h,byte k){var t=r.GetTypeDefinition(h);return r.GetString(t.Namespace)+"."+r.GetString(t.Name);}
 public string GetTypeFromReference(MetadataReader r,TypeReferenceHandle h,byte k){var t=r.GetTypeReference(h);return r.GetString(t.Namespace)+"."+r.GetString(t.Name);}
 public string GetTypeFromSerializedName(string n)=>n;
 public PrimitiveTypeCode GetUnderlyingEnumType(string t)=>PrimitiveTypeCode.Int32;
}
