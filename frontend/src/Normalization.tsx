import {datasetLabel} from './labels';
import { useEffect, useRef, useState } from 'react';
import { Plus, Trash2, Play, Save } from 'lucide-react';

type Row = Record<string, any>;
type Request = <T=any>(url:string, options?:RequestInit)=>Promise<T>;
type Draft = {layout:Row;exclude:string[]; arrays:{path:string;mode:string;keys:string}[]; buckets:{path:string;bounds:string}[]; drop:string; confidence:string; examples:string};
const empty:Draft={layout:{},exclude:[],arrays:[],buckets:[],drop:'0.1',confidence:'5',examples:'100'};
const pathOK=(p:string)=>!!p&&p.length<=2048&&p===p.trim()&&!/[\u0000-\u001f]/.test(p)&&p.split('.').every(x=>!!x&&x===x.trim());
const assert=(condition:unknown,message:string)=>{if(!condition)throw new Error(message);};
const unique=(values:string[])=>new Set(values).size===values.length;
const decimal=(s:string)=>{assert(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(s.trim()),'Введите число; десятичный разделитель — точка.');const n=Number(s);assert(Number.isFinite(n),'Число должно быть конечным.');return n;};
const intervals=(raw:string)=>{try{const b=raw.split(',').map(decimal);return [...b.map((n,i)=>`(${i?b[i-1]:'−∞'}; ${n}]`),`(${b.at(-1)}; +∞)`].join(' · ');}catch{return 'Проверьте границы интервалов.';}};

function configFrom(d:Draft) {
  assert(d.exclude.length<=100&&d.arrays.length<=200&&d.buckets.length<=100,'Лимит: 100 исключений, 200 массивов, 100 правил интервалов.');
  assert([...d.exclude,...d.arrays.map(r=>r.path),...d.buckets.map(r=>r.path)].every(pathOK),'Заполните пути: непустые части через точку, без пробелов по краям.');
  assert(unique(d.exclude)&&unique(d.arrays.map(r=>r.path))&&unique(d.buckets.map(r=>r.path)),'Пути внутри одного раздела не должны повторяться.');
  const arrays=Object.fromEntries(d.arrays.map(r=>{
    assert(['key','index','value'].includes(r.mode),'Неизвестный режим массива.');
    const keys=r.keys.split('\n').map(k=>k.trim()).filter(Boolean);
    if(r.mode==='key')assert(keys.length>0&&keys.length<=8&&unique(keys)&&keys.every(pathOK),`Массив ${r.path}: укажите от 1 до 8 разных ключей, каждый с новой строки.`);
    return [r.path,r.mode==='key'?{mode:r.mode,keys}:{mode:r.mode}];
  }));
  const buckets=Object.fromEntries(d.buckets.map(r=>{
    const bounds=r.bounds.trim()?r.bounds.split(',').map(decimal):[];
    assert(bounds.length<=100&&bounds.every((n,i)=>!i||n>bounds[i-1]),`Интервалы ${r.path}: до 100 границ, строго по возрастанию, без повторов.`);
    return [r.path,bounds];
  }));
  const drop=decimal(d.drop),confidence=decimal(d.confidence),examples=decimal(d.examples);
  assert(drop>=0&&drop<=1,'Порог падения одобрений должен быть от 0 до 1.');
  assert(Number.isInteger(confidence)&&confidence>=0&&confidence<=10000,'Порог уверенности — целое число от 0 до 10 000.');
  assert(Number.isInteger(examples)&&examples>=1&&examples<=20000,'Лимит примеров — целое число от 1 до 20 000.');
  return {layout:d.layout,exclude_paths:d.exclude,arrays,buckets,check_params:{max_drop_rel:drop,low_confidence_cnt:confidence,app_ids_limit:examples}};
}

function draftFrom(text:string):Draft {
  const c=JSON.parse(text);const obj=(x:any)=>x&&typeof x==='object'&&!Array.isArray(x);
  assert(obj(c),'Конфигурация должна быть объектом JSON.');
  assert(Object.keys(c).every(k=>['exclude_paths','arrays','buckets','check_params','layout'].includes(k)),'Неизвестное поле конфигурации.');
  assert(Array.isArray(c.exclude_paths)&&c.exclude_paths.every((p:any)=>typeof p==='string')&&obj(c.arrays)&&obj(c.buckets===undefined?{}:c.buckets),'Проверьте exclude_paths, arrays и buckets.');
  const params={max_drop_rel:.1,low_confidence_cnt:5,app_ids_limit:100,...c.check_params};
  assert(c.check_params===undefined||obj(c.check_params),'check_params должен быть объектом.');
  assert(Object.keys(params).every(k=>['max_drop_rel','low_confidence_cnt','app_ids_limit'].includes(k))&&Object.values(params).every(v=>typeof v==='number'),'Неизвестный или нечисловой параметр проверок.');
  const draft:Draft={layout:c.layout||{},exclude:c.exclude_paths, arrays:Object.entries(c.arrays).map(([path,r]:[string,any])=>{
    assert(obj(r)&&Object.keys(r).every(k=>['mode','keys'].includes(k))&&typeof r.mode==='string','Некорректное правило массива.');
    assert(r.keys===undefined||(Array.isArray(r.keys)&&r.keys.every((k:any)=>typeof k==='string')),'Ключи массива должны быть списком строк.');
    return {path,mode:r.mode,keys:(r.keys||[]).join('\n')};
  }), buckets:Object.entries(c.buckets??{}).map(([path,b]:[string,any])=>{
    assert(Array.isArray(b)&&b.every((n:any)=>typeof n==='number'),'Границы интервалов должны быть списком чисел.');return {path,bounds:b.join(', ')};
  }),drop:String(params.max_drop_rel),confidence:String(params.low_confidence_cnt),examples:String(params.app_ids_limit)};
  configFrom(draft);return draft;
}

export function ConfigEditor({value,onChange,section}:{value:string;onChange:(text:string)=>void;section?:"normalization"|"aggregation"|"checks"}) {
  const [mode,setMode]=useState('visual'),[draft,setDraft]=useState<Draft>(empty),[error,setError]=useState('');
  const emitted=useRef<string|null>(null),lastValid=useRef<string|null>(null);
  const [visualBlocked,setVisualBlocked]=useState(false);
  useEffect(()=>{if(value===emitted.current||!value)return;try{setDraft(draftFrom(value));lastValid.current=value;setError('');setVisualBlocked(false);}catch(e){setError((e as Error).message);setMode('json');}},[value]);
  const emit=(next:string)=>{emitted.current=next;onChange(next);};
  const change=(next:Draft)=>{setDraft(next);try{const c=configFrom(next);setError('');setVisualBlocked(false);const text=JSON.stringify(c,null,2);lastValid.current=text;emit(text);}catch(e){setError((e as Error).message);emit('');}};
  const switchMode=(next:string)=>{
    if(next==='visual'){try{setDraft(draftFrom(value));lastValid.current=value;setError('');setVisualBlocked(false);}catch(e){setError('Не удалось открыть визуальный редактор: '+(e as Error).message);setVisualBlocked(true);return;}}
    if(next==='json'&&!value)return;
    setMode(next);
  };
  const remove=(section:'exclude'|'arrays'|'buckets',index:number)=>change({...draft,[section]:draft[section].filter((_,i)=>i!==index)});
  return <div className="normalization-editor">
    <div className="normalization-tabs" role="group" aria-label="Режим редактора">
      <button type="button" className={`button ${mode==='visual'?'':'secondary'}`} aria-pressed={mode==='visual'} onClick={()=>switchMode('visual')}>Визуальный редактор</button>
      <button type="button" className={`button ${mode==='json'?'':'secondary'}`} aria-pressed={mode==='json'} disabled={mode==='visual'&&!value} onClick={()=>switchMode('json')}>JSON</button>
    </div>
    {error&&<div className="error-box" role="alert">{error}</div>}
    {visualBlocked&&lastValid.current&&<div className="normalization-recovery"><p>Исправьте JSON или вернитесь к последней корректной версии. Возврат заменит текущий JSON.</p><button type="button" className="button secondary" onClick={()=>{const text=lastValid.current!;setDraft(draftFrom(text));emit(text);setError('');setVisualBlocked(false);setMode('visual');}}>Восстановить и открыть редактор</button></div>}
    {mode==='json'?<textarea aria-label="JSON конфигурации" className="code-editor" spellCheck={false} value={value} onChange={e=>{const text=e.target.value;emit(text);try{draftFrom(text);lastValid.current=text;setError('');setVisualBlocked(false);}catch(err){setError((err as Error).message);}}}/>:<>
      <section hidden={!!section&&section!=="normalization"} className="normalization-section"><h3>Исключаемые поля</h3><p>Полный путь исключает поле и весь вложенный блок из сравнения.</p>
        {draft.exclude.map((path,i)=><div className="normalization-row" key={i}><label className="field">Путь исключения {i+1}<input value={path} onChange={e=>change({...draft,exclude:draft.exclude.map((p,n)=>n===i?e.target.value:p)})}/></label><button type="button" className="icon-button" aria-label={`Удалить исключение ${i+1}`} onClick={()=>remove('exclude',i)}><Trash2 size={17}/></button></div>)}
        <button type="button" className="text-button" onClick={()=>change({...draft,exclude:[...draft.exclude,'']})}><Plus size={15}/>Добавить исключение</button>
      </section>
      <section hidden={!!section&&section!=="normalization"} className="normalization-section"><h3>Сопоставление массивов</h3><p>По ключам — независимо от порядка; по позиции — по индексу; по значению — набор скаляров, повторы объединяются.</p>
        {draft.arrays.map((r,i)=><div className="normalization-rule" key={i}>
          <div className="normalization-row"><label className="field">Путь массива {i+1}<input value={r.path} onChange={e=>change({...draft,arrays:draft.arrays.map((a,n)=>n===i?{...a,path:e.target.value}:a)})}/></label><label className="field">Режим массива {i+1}<select aria-label={`Режим массива ${i+1}`} value={r.mode} onChange={e=>change({...draft,arrays:draft.arrays.map((a,n)=>n===i?{...a,mode:e.target.value}:a)})}><option value="key">По ключам</option><option value="index">По позиции</option><option value="value">По значению</option></select></label><button type="button" className="icon-button" aria-label={`Удалить массив ${i+1}`} onClick={()=>remove('arrays',i)}><Trash2 size={17}/></button></div>
          {r.mode==='key'&&<label className="field">Ключи массива {i+1}<textarea aria-label={`Ключи массива ${i+1}`} rows={2} placeholder={'product_id\ntype'} value={r.keys} onChange={e=>change({...draft,arrays:draft.arrays.map((a,n)=>n===i?{...a,keys:e.target.value}:a)})}/><small>Каждый ключ с новой строки. Путь внутри элемента, например body.action. Порядок ключей задаёт составной идентификатор.</small></label>}
          {r.path&&draft.exclude.some(p=>r.path===p||r.path.startsWith(p+'.'))&&<p className="normalization-note">Массив находится в исключённом блоке: правило не применяется.</p>}
        </div>)}
        <button type="button" className="text-button" onClick={()=>change({...draft,arrays:[...draft.arrays,{path:'',mode:'key',keys:''}]})}><Plus size={15}/>Добавить массив</button>
      </section>
      <section hidden={!!section&&section!=="aggregation"} className="normalization-section"><h3>Числовые интервалы</h3><p>Границы через запятую, дробная часть через точку: 0.3, 0.6, 1. Верхняя граница включена. Интервалы группируют значения; изменения считаются по исходным числам.</p>
        {draft.buckets.map((r,i)=><div className="normalization-rule" key={i}><div className="normalization-row"><label className="field">Путь интервала {i+1}<input value={r.path} onChange={e=>change({...draft,buckets:draft.buckets.map((b,n)=>n===i?{...b,path:e.target.value}:b)})}/></label><label className="field">Границы интервала {i+1}<input value={r.bounds} onChange={e=>change({...draft,buckets:draft.buckets.map((b,n)=>n===i?{...b,bounds:e.target.value}:b)})}/></label><button type="button" className="icon-button" aria-label={`Удалить интервал ${i+1}`} onClick={()=>remove('buckets',i)}><Trash2 size={17}/></button></div><small>{r.bounds.trim()?intervals(r.bounds):'Пустой список: группировка отключена.'}</small></div>)}
        <button type="button" className="text-button" onClick={()=>change({...draft,buckets:[...draft.buckets,{path:'',bounds:''}]})}><Plus size={15}/>Добавить интервалы</button>
      </section>
      <section hidden={!!section&&section!=="checks"} className="normalization-section"><h3>Параметры штатных проверок</h3><div className="normalization-row">{([['drop','Порог падения одобрений'],['confidence','Порог уверенности'],['examples','Лимит примеров заявок']] as const).map(([key,label])=><label className="field" key={key}>{label}<input inputMode="decimal" value={draft[key]} onChange={e=>change({...draft,[key]:e.target.value})}/></label>)}</div><p>Порог одобрений — доля от 0 до 1; остальные значения — целые числа. Настройки влияют на новые расчёты.</p></section>
    </>}
  </div>;
}

export function ConfigPreview({request,value,dataset}:{request:Request;value:string;dataset:string}) {
  const [appId,setAppId]=useState(''),[result,setResult]=useState<Row|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const sequence=useRef(0);
  useEffect(()=>{sequence.current++;setResult(null);setError('');setBusy(false);},[value,dataset,appId]);
  useEffect(()=>()=>{sequence.current++;},[]);
  const run=async()=>{const ticket=++sequence.current;setBusy(true);setError('');setResult(null);try{
    const config=configFrom(draftFrom(value));const r=await request(`/api/datasets/${encodeURIComponent(dataset)}/normalization-preview`,{method:'POST',body:JSON.stringify({config,app_id:appId.trim()||null})});
    if(ticket===sequence.current)setResult(r);
  }catch(e){if(ticket===sequence.current)setError((e as Error).message);}finally{if(ticket===sequence.current)setBusy(false);}};
  const display=(v:Row|null)=>v===null?'Поле отсутствует':`${v.value===''?'""':v.value}${v.truncated?'…':''} · ${v.type}`;
  return <section className="normalization-preview">
    <h3>Предпросмотр на одной заявке</h3>
    <div className="normalization-row"><label className="field">ID заявки для предпросмотра<input value={appId} maxLength={100} placeholder="Пусто — первая пара по ID" onChange={e=>setAppId(e.target.value)}/></label><button className="button secondary" type="button" disabled={!value||!dataset||busy} onClick={run}><Play size={15}/>{busy?'Проверяем…':'Проверить правила'}</button></div>
    {error&&<div className="error-box" role="alert">{error}</div>}
    {result&&<div className="normalization-preview-result" role="status"><p><b>Заявка {result.app_id}</b> · всего пар: {result.pairs}</p>
      {result.errors.map((e:Row,i:number)=><div key={i} className="error-box">{e.source}: {e.message}</div>)}
      {result.array_paths.length>0&&<details><summary>Массивы в выбранной паре ({result.array_paths.length})</summary><ul>{result.array_paths.map((p:string)=><li key={p}><code>{p}</code></li>)}</ul></details>}
      {result.valid&&<><p>Правила применены. Атрибутов: {result.total} · изменилось: {result.changed}. BEFORE: {result.before_count} · AFTER: {result.after_count}.</p><div className="table-scroll"><table><thead><tr><th>Путь / ключ массива</th><th>BEFORE</th><th>AFTER</th><th>Изменение</th></tr></thead><tbody>{result.items.map((r:Row,i:number)=><tr key={i}><td>{r.path}{r.array_key&&<small>{r.array_key}</small>}</td><td>{display(r.before)}{r.before&&r.before.adjusted!==r.before.value&&<small>Интервал: {r.before.adjusted}</small>}</td><td>{display(r.after)}{r.after&&r.after.adjusted!==r.after.value&&<small>Интервал: {r.after.adjusted}</small>}</td><td>{{SAME:'Без изменений',VALUE_CHANGED:'Изменилось',ATTRIBUTE_NEW:'Добавлено',ATTRIBUTE_LOST:'Удалено'}[r.change as string]}</td></tr>)}</tbody></table></div><p className="muted">Показано {result.items.length} из {result.total}; сначала изменения. Длинные значения сокращены до 240 символов.</p></>}
    </div>}
  </section>;
}

export function Configuration({request}:{request:Request}) {
  const [value,setValue]=useState(''),[loaded,setLoaded]=useState(false),[error,setError]=useState(''),[saved,setSaved]=useState(false),[busy,setBusy]=useState(false);
  const [revision,setRevision]=useState(0);
  const [datasets,setDatasets]=useState<Row[]>([]),[dataset,setDataset]=useState(''),[datasetError,setDatasetError]=useState('');
  const [loadAttempt,setLoadAttempt]=useState(0),[datasetAttempt,setDatasetAttempt]=useState(0),[loading,setLoading]=useState(true);
  useEffect(()=>{let closed=false;setLoading(true);setError('');request('/api/config/state').then(c=>{if(!closed){setValue(JSON.stringify(c.config,null,2));setRevision(c.revision);setLoaded(true);}}).catch(e=>{if(!closed)setError(e.message);}).finally(()=>{if(!closed)setLoading(false);});return()=>{closed=true;};},[request,loadAttempt]);
  useEffect(()=>{let closed=false;setDatasetError('');request('/api/datasets').then(d=>{if(!closed){setDatasets(d);setDataset(prev=>d.some((x:Row)=>x.id===prev)?prev:d[0]?.id||'');}}).catch(e=>{if(!closed)setDatasetError(e.message);});return()=>{closed=true;};},[request,datasetAttempt]);
  const change=(text:string)=>{setValue(text);setSaved(false);setError('');};
  const save=async()=>{setBusy(true);setError('');try{const config=configFrom(draftFrom(value));await request('/api/config',{method:'PUT',headers:{'If-Match':String(revision)},body:JSON.stringify(config)});setRevision(revision+1);setSaved(true);}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  return <div className="normalization-page"><div className="page-heading"><div><h1>Конфигурация сравнения</h1></div><button className="button" disabled={!loaded||!value||busy} onClick={save}><Save size={16}/>{saved?'Сохранено':busy?'Сохраняем…':'Сохранить'}</button></div>
    {error&&<div className="error-box" role="alert">{error}</div>}
    {loaded&&<button className="text-button" disabled={busy} onClick={async()=>{if(!window.confirm('Загрузить сохранённую конфигурацию? Текущий несохранённый ввод будет заменён.'))return;setBusy(true);try{const c=await request('/api/config/state');setValue(JSON.stringify(c.config,null,2));setRevision(c.revision);setError('');setSaved(false);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>Загрузить сохранённую конфигурацию</button>}
    {loaded?<><fieldset className="normalization-fieldset" disabled={busy}><div className="panel normalization-panel"><ConfigEditor value={value} onChange={change}/></div></fieldset>
      <div className="panel normalization-panel">{datasetError&&<div role="alert" className="error-box">Не удалось загрузить датасеты для предпросмотра: {datasetError}<button className="text-button" onClick={()=>setDatasetAttempt(n=>n+1)}>Повторить загрузку датасетов</button></div>}<label className="field">Датасет для предпросмотра<select aria-label="Датасет для предпросмотра" value={dataset} onChange={e=>setDataset(e.target.value)}><option value="">Выберите датасет</option>{datasets.map(d=><option key={d.id} value={d.id}>{datasetLabel(d)}</option>)}</select></label><ConfigPreview request={request} value={value} dataset={dataset}/></div></>:loading?<p role="status">Загрузка конфигурации…</p>:<button className="button secondary" onClick={()=>setLoadAttempt(n=>n+1)}>Повторить загрузку конфигурации</button>}
  </div>;
}
