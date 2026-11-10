"""Small local desktop interface using the same workflow implementation as CLI."""
import json, tkinter as tk
from tkinter import ttk,filedialog,simpledialog,messagebox
from pathlib import Path
import tool

def run(db):
 root=tk.Tk();root.title('Контекстная память RimWorld');root.geometry('1080x740')
 job=tk.StringVar(value=str(tool.ROOT/'jobs/current'));bar=ttk.Frame(root);bar.pack(fill='x')
 ttk.Label(bar,text='Рабочая папка:').pack(side='left');ttk.Entry(bar,textvariable=job,width=85).pack(side='left')
 tree=ttk.Treeview(root,columns=('key','state'),show='headings');tree.heading('key',text='Ключ');tree.heading('state',text='Состояние');tree.column('key',width=650);tree.pack(fill='both',expand=True)
 detail=tk.Text(root,height=12,wrap='word');detail.pack(fill='both')
 editor=tk.Text(root,height=4,wrap='word');editor.pack(fill='x')
 status=tk.StringVar();ttk.Label(root,textvariable=status).pack(fill='x');buttons=ttk.Frame(root);buttons.pack(fill='x')
 def current():return Path(job.get())
 def refresh():
  j=tool.read(current()/'job.json');tree.delete(*tree.get_children())
  for r in j['rows']:tree.insert('','end',iid=r['id'],values=(r['key'],r['state']))
  status.set(json.dumps(tool.summary(j),ensure_ascii=False))
 def invoke(fn):
  try:fn();refresh()
  except Exception as ex:messagebox.showerror('Ошибка',str(ex))
 def select(event=None):
  ids=tree.selection()
  if not ids:return
  j=tool.read(current()/'job.json');r=next(r for r in j['rows'] if r['id']==ids[0]);detail.delete('1.0','end');detail.insert('1.0',json.dumps(r,ensure_ascii=False,indent=2));editor.delete('1.0','end');editor.insert('1.0',r.get('translation',''))
 tree.bind('<<TreeviewSelect>>',select)
 def analyze():
  source=filedialog.askopenfilename(title='Каталог JSON или ZIP',filetypes=[('Input','*.json *.zip')])
  if not source:source=filedialog.askdirectory(title='Или папка мода')
  if not source:return
  pkg=simpledialog.askstring('Источник','packageId оригинала (не перевода):');lang=simpledialog.askstring('Язык','en / zh-Hans / und:',initialvalue='en')
  if not pkg or not lang:return
  tool.analyze(db,source,current(),pkg,lang)
 def import_response():
  batch=filedialog.askopenfilename(title='Партия JSON');response=filedialog.askopenfilename(title='Ответ ID → перевод')
  if batch and response:tool.stage(current(),batch,response)
 def save():
  ids=tree.selection()
  if not ids:raise ValueError('Выберите строку')
  j=tool.read(current()/'job.json');r=next(r for r in j['rows'] if r['id']==ids[0]);v=editor.get('1.0','end-1c');errors=tool.validate([r],{r['id']:v})
  if errors:raise ValueError(str(errors))
  r.update(state='draft',translation=v);tool.write(current()/'job.json',j)
 def accept():
  ids=list(tree.selection())
  if not ids:raise ValueError('Выберите проверенные строки')
  reason=simpledialog.askstring('Принятие','Основание смысловой проверки:')
  if reason:tool.accept(db,current(),reason,ids)
 def build():
  pkg=simpledialog.askstring('Сборка','packageId нашего перевода:');name=simpledialog.askstring('Сборка','Название:')
  if pkg and name:messagebox.showinfo('Сборка',json.dumps(tool.build(current(),pkg,name,db),ensure_ascii=False))
 def resolve():
  j=tool.read(current()/'job.json');index=simpledialog.askinteger('Исключение','Индекс из списка (с нуля):')
  if index is None:return
  evidence=simpledialog.askstring('Проверка покрытия','Что проверено, путь к доказательству:')
  if evidence:
   issue=j['issues'].pop(index);j['review_log'].append({'coverage':issue,'evidence':evidence,'time':tool.now()});tool.write(current()/'job.json',j)
 def issues():
  j=tool.read(current()/'job.json');detail.delete('1.0','end');detail.insert('1.0',json.dumps(j['issues'],ensure_ascii=False,indent=2))
 for title,fn in [('Анализ',analyze),('Открыть',refresh),('Экспорт',lambda:tool.export(current())),('Импорт',import_response),('Сохранить правку',save),('Принять выбранное',accept),('Исключения',issues),('Разобрано',resolve),('Сборка',build)]:ttk.Button(buttons,text=title,command=lambda f=fn:invoke(f)).pack(side='left')
 root.mainloop()
