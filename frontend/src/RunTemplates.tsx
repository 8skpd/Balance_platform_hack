import { useEffect, useRef, useState } from 'react';
import { Save, RefreshCw, Trash2, Download } from 'lucide-react';

type Row=Record<string,any>;
type Request=<T=any>(url:string,options?:RequestInit)=>Promise<T>;

export function RunTemplates({request,disabled,snapshot,apply,onBusy}:{request:Request;disabled:boolean;snapshot:()=>Row;apply:(spec:Row)=>void;onBusy:(busy:boolean)=>void}) {
  const [items,setItems]=useState<Row[]>([]),[selected,setSelected]=useState(''),[loaded,setLoaded]=useState<Row|null>(null);
  const [name,setName]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState(''),[confirmDelete,setConfirmDelete]=useState(false),[busy,setBusy]=useState(false),[ready,setReady]=useState(false);
  const alive=useRef(true);
  useEffect(()=>{alive.current=true;let closed=false;request<Row[]>('/api/run-templates').then(r=>{if(!closed)setItems(r);}).catch(e=>{if(!closed)setError(e.message);}).finally(()=>{if(!closed)setReady(true);});return()=>{closed=true;alive.current=false;};},[request]);
  const start=()=>{setBusy(true);onBusy(true);setError('');setNotice('');setConfirmDelete(false);};
  const done=()=>{if(alive.current){setBusy(false);onBusy(false);}};
  const refresh=async()=>{start();try{const rows=await request<Row[]>('/api/run-templates');if(alive.current){setItems(rows);if(selected&&!rows.some(r=>r.id===selected)){setSelected('');setLoaded(null);}setNotice('Список обновлён. Настройки в форме не изменены.');}}catch(e){if(alive.current)setError((e as Error).message);}finally{done();}};
  const load=async()=>{start();try{const row=await request('/api/run-templates/'+selected);if(alive.current){apply(row.spec);setLoaded(row);setItems(prev=>[row,...prev.filter(r=>r.id!==row.id)]);setName(row.name);setNotice(`Применён шаблон «${row.name}».  Можно изменить настройки перед запуском.`);}}catch(e){if(alive.current)setError((e as Error).message);}finally{done();}};
  const save=async(update:boolean)=>{start();try{
    const data={name,spec:snapshot(),...(update?{revision:loaded!.revision}:{})};
    const row=await request('/api/run-templates'+(update?'/'+loaded!.id:''),{method:update?'PUT':'POST',body:JSON.stringify(data)});
    if(alive.current){setLoaded(row);setSelected(row.id);setName(row.name);setItems(prev=>[row,...prev.filter(r=>r.id!==row.id)]);setNotice(`Шаблон сохранён.`);}
  }catch(e){if(alive.current)setError((e as Error).message);}finally{done();}};
  const remove=async()=>{const revision=items.find(r=>r.id===selected)?.revision;start();try{
    await request(`/api/run-templates/${selected}?revision=${revision}`,{method:'DELETE'});
    if(alive.current){setItems(prev=>prev.filter(r=>r.id!==selected));setSelected('');setLoaded(null);setName('');setNotice('Шаблон удалён. Настройки в форме и готовые отчёты сохранены.');}
  }catch(e){if(alive.current)setError((e as Error).message);}finally{done();}};
  return <details className="run-templates"><summary>Шаблоны запусков {items.length>0&&`(${items.length})`}</summary>

    <fieldset disabled={disabled||busy||!ready} className="normalization-fieldset">
      <label className="field">Мои шаблоны<select aria-label="Мои шаблоны" value={selected} onChange={e=>{setSelected(e.target.value);setLoaded(null);setName(items.find(r=>r.id===e.target.value)?.name||'');setError('');setNotice('');setConfirmDelete(false);}}><option value="">Выберите шаблон</option>{items.map(r=><option value={r.id} key={r.id}>{r.name}</option>)}</select></label>
      <div className="template-actions"><button className="button secondary" type="button" disabled={!selected} onClick={load}><Download size={14}/>Применить шаблон</button><button className="text-button" type="button" onClick={refresh}><RefreshCw size={14}/>Обновить список шаблонов</button></div>
      <label className="field">Название шаблона<input maxLength={120} value={name} onChange={e=>setName(e.target.value)} placeholder="Например: Проверка новой стратегии"/></label>
      <div className="template-actions"><button className="button secondary" type="button" disabled={!name.trim()} onClick={()=>save(false)}><Save size={14}/>Сохранить как новый шаблон</button><button className="text-button" type="button" disabled={!loaded||loaded.id!==selected||!name.trim()} onClick={()=>save(true)}>Обновить шаблон</button><button className="text-button" type="button" disabled={!selected} onClick={()=>setConfirmDelete(true)}><Trash2 size={14}/>Удалить шаблон</button></div>
      {confirmDelete&&<div className="template-delete" role="group" aria-label="Подтверждение удаления шаблона"><p>Удалить «{items.find(r=>r.id===selected)?.name}» из ваших шаблонов?</p><button type="button" className="button secondary" onClick={remove}>Подтвердить удаление</button><button type="button" className="text-button" onClick={()=>setConfirmDelete(false)}>Оставить шаблон</button></div>}
    </fieldset>
    {error&&<div className="error-box" role="alert">{error}</div>}
    {notice&&<p className="template-notice" role="status">{notice}</p>}
    {loaded?.issues.map((issue:string,i:number)=><div className="error-box" key={i}>{issue}</div>)}
    {loaded&&<details className="template-composition"><summary>Состав сохранённого шаблона</summary><p>Штатных проверок: {loaded.spec.builtin_checks.length}; пользовательских: {loaded.spec.user_checks.length}.</p><ul>{loaded.checks.map((c:Row)=><li key={c.key}>{c.name} · v{c.version}</li>)}</ul></details>}
  </details>;
}
