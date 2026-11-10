"""Bounded glossary suggestions; never replace words or accept translations."""
import re

def compile_concepts(concepts,language):
 result=[]
 for c in concepts:
  patterns=[]
  for v in c.get('variants',{}).get(language,[]):
   if not v.strip():continue
   cjk=bool(re.search('[\u4e00-\u9fff]',v))
   pattern=re.escape(v)
   if cjk and len(v)==1:pattern=r'^\s*'+pattern+r'\s*$'
   elif not cjk:pattern=r'(?<!\w)'+pattern+r'(?!\w)'
   patterns.append(re.compile(pattern,re.IGNORECASE))
  if patterns:result.append((c,patterns))
 return result

def find_concepts(text,index,limit=8):
 # Placeholders and tags are identifiers, not evidence of a linguistic sense.
 text=re.sub(r'\{[^{}]*\}|\[[^\[\]]*\]|</?[A-Za-z][^>]*>',' ',text)
 matches=[]
 for c,patterns in index:
  found=[m for p in patterns for m in p.finditer(text)]
  if found:matches.append((max(len(m.group()) for m in found),c))
 # Keep competing senses rather than pretending that lexical match disambiguates.
 return [c for _,c in sorted(matches,key=lambda x:(-x[0],x[1]['id']))[:limit]]
