import {PythonUpload} from './PythonUpload';
import {useEffect,useRef,useState} from 'react';
import {CodeEditor,type Request,type Stage,type Validation} from './CodeEditor';
import {ProcedureContract,CheckContract,ReplaceCodeDialog} from './ProcedureContract';
import {example} from './Jobs';
import {CheckPicker,type CheckSelection} from './Library';
import {BookOpen,Code2,SlidersHorizontal} from 'lucide-react';
import './run-setup.css';
type Row=Record<string,any>;
export const stageTitles={normalization:'Разбор и нормализация',comparison:'Сравнение',aggregation:'Подготовка отчёта',checks:'Автоматические проверки'};

function InlineCode({request,stage,initial,standard,apply,cancel,onBusy}:{request:Request;stage:Stage;initial:Row;standard:string;apply:(row:Row)=>void;cancel:()=>void;onBusy:(v:boolean)=>void}){
 const [name,setName]=useState(initial.name||stageTitles[stage]),[code,setCode]=useState(initial.code||standard),[params,setParams]=useState(JSON.stringify(initial.params||{},null,2));
 const [filename,setFilename]=useState(initial.filename||''),[valid,setValid]=useState<Validation|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[replace,setReplace]=useState(false);
 const save=async()=>{setBusy(true);onBusy(true);setError('');try{
  const parsed=JSON.parse(params);if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('Параметры должны быть JSON-объектом');
  const validation=await request<Validation>('/api/code/validate',{method:'POST',body:JSON.stringify({stage,code})});setValid(validation);if(!validation.valid)throw new Error('Исправьте ошибки в коде перед применением.');
  const body=stage==='checks'?{name,code,params:parsed,timeout:initial.timeout||20,memory_mb:initial.memory_mb||1024}:{name,stage,code,params:parsed,filename,timeout:initial.timeout||600,memory_mb:initial.memory_mb||2048};
  const row=await request(stage==='checks'?'/api/check-library':'/api/procedures',{method:'POST',body:JSON.stringify(body)});apply(row);
 }catch(e){setError((e as Error).message);}finally{setBusy(false);onBusy(false);}};
 const insert=()=>{if(code.trim()&&code.trim()!==standard.trim())setReplace(true);else{setCode(standard);setFilename('');}};
 return <div className="inline-procedure"><fieldset disabled={busy} className="normalization-fieldset">
  <div className="inline-source"><label className="field">Название<input aria-label="Название обработчика" maxLength={120} value={name} onChange={e=>setName(e.target.value)}/></label><div className="inline-python-upload"><PythonUpload label="Файл обработчика" disabled={busy} onFile={async file=>{try{if(!file.name.toLowerCase().endsWith('.py')||file.size>(stage==='checks'?50000:100000))throw new Error('Выберите файл .py до '+(stage==='checks'?'50':'100')+' КБ');const source=await file.text();if(code.trim()&&code!==standard&&!window.confirm('Заменить текущий код содержимым файла? Несохранённые изменения будут потеряны.'))return;setCode(source);setFilename(file.name);setName(file.name.replace(/\.py$/i,''));}catch(err){setError((err as Error).message);}}}/>{filename&&<span>{filename}</span>}</div></div>
  {stage==='checks'?<CheckContract insert={insert}/>:<ProcedureContract stage={stage} insert={insert}/>}
  <CodeEditor request={request} stage={stage} value={code} onChange={setCode} onValidation={setValid} disabled={busy}/>
  <details className="inline-params"><summary>Параметры процедуры</summary><label className="field">JSON<textarea aria-label="Параметры обработчика" rows={4} spellCheck={false} value={params} onChange={e=>setParams(e.target.value)}/></label></details>
  {error&&<p className="error-box" role="alert">{error}</p>}
  <div className="inline-actions"><button type="button" className="button" disabled={busy||!valid?.valid||!name.trim()} onClick={save}>{busy?'Применяем…':'Сохранить и применить'}</button><button type="button" className="button secondary" onClick={cancel}>Отмена</button></div>
 </fieldset>{replace&&<ReplaceCodeDialog cancel={()=>setReplace(false)} replace={()=>{setCode(standard);setFilename('');setReplace(false);}}/>}</div>;
}

export function StageSetup({stage,request,value,onChange,onPending,children}:{stage:Exclude<Stage,'checks'>;request:Request;value:Row|undefined;onChange:(v:Row|undefined)=>void;onPending:(v:boolean)=>void;children?:React.ReactNode}){
 const [items,setItems]=useState<Row[]>([]),[standard,setStandard]=useState(''),[draft,setDraft]=useState<Row|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[loaded,setLoaded]=useState(false),[attempt,setAttempt]=useState(0);
 const [mode,setMode]=useState(value?'library':'standard');
 const pending=useRef(onPending);pending.current=onPending;
 useEffect(()=>{pending.current(!!draft||busy||(mode==='library'&&!value));return()=>pending.current(false);},[draft,busy,mode,value?.id]);
 useEffect(()=>{let closed=false;setLoaded(false);Promise.all([request<Row[]>('/api/procedures'),request<Row>('/api/procedures/catalog')]).then(([rows,catalog])=>{if(!closed){setItems(rows.filter(r=>r.stage===stage));setStandard(catalog.examples[stage]);setLoaded(true);setError('');}}).catch(e=>!closed&&setError(e.message));return()=>{closed=true;};},[request,stage,attempt]);
 const selected=items.find(r=>r.id===value?.id);
 useEffect(()=>{if(!draft)setMode(value?'library':'standard');},[value?.id,value?.revision]);
 const openCode=()=>{setDraft(selected?{...selected,params:{...selected.params,...value?.params}}:{code:standard});setMode('python');};
 const switchMode=(next:string)=>{
  if(next===mode)return;
  if(draft&&!window.confirm('Закрыть редактор? Неприменённые изменения кода будут потеряны.'))return;
  setDraft(null);setMode(next);
  if(next==='standard')onChange(undefined);
  if(next==='python')openCode();
 };
 return <div className="stage-setup">
  <div className="source-switch" role="group" aria-label="Способ обработки">{([{id:'standard',label:'Стандартная',icon:SlidersHorizontal},{id:'library',label:'Из библиотеки',icon:BookOpen},{id:'python',label:'Свой Python',icon:Code2}]).map(({id,label,icon:Icon})=><button key={id} type="button" aria-pressed={mode===id} disabled={!loaded||busy} onClick={()=>switchMode(id)}><Icon size={19}/><span>{label}</span></button>)}</div>
  {mode==='standard'&&<div className="standard-procedure"><div><strong>Стандартная процедура</strong><p>{stage==='normalization'?'Приводит ответы к единому формату.':stage==='comparison'?'Находит изменённые, новые и удалённые атрибуты.':'Группирует различия для сводной таблицы.'}</p></div><button type="button" className="text-button" disabled={!loaded} onClick={openCode}>Открыть код</button></div>}
  {mode==='library'&&<div className="stage-choice"><label className="field">Сохранённая процедура<select aria-label={'Процедура: '+stageTitles[stage]} disabled={!loaded} value={value?.id||''} onChange={e=>{const row=items.find(r=>r.id===e.target.value);onChange(row?{id:row.id,revision:row.revision,params:{}}:undefined);}}><option value="">{items.length?'Выберите процедуру':'Нет сохранённых процедур'}</option>{value&&!selected&&<option value={value.id}>Процедура недоступна</option>}{items.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select></label><button type="button" className="button secondary" disabled={!selected} onClick={openCode}>Открыть код</button></div>}
  {error&&<div className="error-box" role="alert">{error}<button type="button" className="text-button" onClick={()=>setAttempt(n=>n+1)}>Повторить</button></div>}
  {draft&&<InlineCode request={request} stage={stage} initial={draft} standard={standard} onBusy={setBusy} cancel={()=>{setDraft(null);setMode(value?'library':'standard');}} apply={row=>{setItems(prev=>[row,...prev]);onChange({id:row.id,revision:row.revision,params:{}});setDraft(null);setMode('library');}}/>}
  {children}
 </div>;
}

export function ChecksSetup({request,value,onChange,onPending,children}:{request:Request;value:CheckSelection|null;onChange:(v:CheckSelection)=>void;onPending:(v:boolean)=>void;children?:React.ReactNode}){
 const [draft,setDraft]=useState<Row|null>(null),[busy,setBusy]=useState(false),[revision,setRevision]=useState(0),[error,setError]=useState(''),[mode,setMode]=useState('standard');
 const pending=useRef(onPending);pending.current=onPending;
 useEffect(()=>{pending.current(!!draft||busy);return()=>pending.current(false);},[draft,busy]);
 const edit=async(spec:Row)=>{setBusy(true);setError('');try{const row=spec.kind==='builtin'?await request('/api/check-library/builtin/'+encodeURIComponent(spec.key)):await request(`/api/check-library/${spec.check_id}/versions/${spec.version}`);setDraft({...row,replaces:spec.kind==='builtin'?{builtin:spec.key}:{version:spec.id},params:{...row.params,...spec.params}});setMode('python');}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
 const switchMode=(next:string)=>{if(next===mode)return;if(draft&&!window.confirm('Закрыть редактор? Неприменённые изменения кода будут потеряны.'))return;setDraft(next==='python'?{code:example}:null);setMode(next);};
 return <div className="stage-setup">
  <div className="source-switch" role="group" aria-label="Источник проверок">{([{id:'standard',label:'Стандартные',icon:SlidersHorizontal},{id:'library',label:'Из библиотеки',icon:BookOpen},{id:'python',label:'Свой Python',icon:Code2}]).map(({id,label,icon:Icon})=><button key={id} type="button" aria-pressed={mode===id} disabled={busy||!value||id==='python'&&!draft&&(value?.user_checks.length||0)>=20} onClick={()=>switchMode(id)}><Icon size={19}/><span>{label}</span></button>)}</div>
  <div hidden={mode==='python'}><fieldset disabled={busy} className="normalization-fieldset"><CheckPicker key={revision} request={request} value={value} onChange={onChange} onEdit={edit} scope={mode==='standard'?'builtin':'custom'}/></fieldset></div>
  {error&&<p className="error-box" role="alert">{error}</p>}
  {draft&&<InlineCode request={request} stage="checks" initial={draft} standard={example} onBusy={setBusy} cancel={()=>{setMode(draft.replaces?.version?'library':'standard');setDraft(null);}} apply={row=>{onChange({builtin_checks:(value?.builtin_checks||[]).filter(k=>k!==draft.replaces?.builtin),user_checks:[...(value?.user_checks||[]).filter(v=>v.version_id!==draft.replaces?.version),{version_id:row.id,params:{}}]});setDraft(null);setMode('library');setRevision(n=>n+1);}}/>}
  <div hidden={mode!=='standard'}>{children}</div>
 </div>;
}
