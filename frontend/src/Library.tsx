import {CodeEditor,type Validation} from './CodeEditor';
import {CheckContract,ReplaceCodeDialog} from './ProcedureContract';
import './check-library.css';
import { useEffect, useRef, useState } from 'react';
import { Archive, Plus, Play, Save, Upload, FileCode2 } from 'lucide-react';
import { example } from './Jobs';

type Row = Record<string, any>;
type Request = <T = any>(url: string, options?: RequestInit) => Promise<T>;
export type CheckSelection = {builtin_checks:string[]; user_checks:{version_id:string; params?:Row}[]};
const blank = () => ({name:'',description:'',code:example,params:'{"max_share": 0.3}',timeout:20,memory_mb:1024});
const toDraft = (v:Row) => ({name:v.name,description:v.description,code:v.code,params:JSON.stringify(v.params,null,2),timeout:v.timeout,memory_mb:v.memory_mb});
const status:Row = {running:'Выполняется',completed:'Выполнена',failed:'Ошибка',timed_out:'Лимит времени',cancelled:'Отменена'};

export function Library({request,openJobs}: {request:Request;openJobs:()=>void}) {
  const [items,setItems] = useState<Row[]>([]), [definition,setDefinition] = useState<Row|null>(null);
  const [version,setVersion] = useState<Row|null>(null), [draft,setDraft] = useState(blank);
  const [archived,setArchived] = useState(false), [reports,setReports] = useState<Row[]>([]), [report,setReport] = useState('');
  const [error,setError] = useState(''), [message,setMessage] = useState(''), [busy,setBusy] = useState(false);
  const dirty = !version || JSON.stringify(draft)!==JSON.stringify(toDraft(version));
  const list = async () => setItems(await request('/api/check-library?include_archived='+archived));
  useEffect(()=>{ let alive=true; request<Row[]>('/api/check-library?include_archived='+archived).then(v=>{if(alive)setItems(v);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;}; },[archived,request]);
  useEffect(()=>{let alive=true; request<Row[]>('/api/runs').then(v=>{if(alive){const ready=v.filter(r=>r.status==='completed' && r.can_manage);setReports(ready);setReport(ready[0]?.id||'');}}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[request]);
  const load = async (id:string, number?:number) => {
    setBusy(true);setError('');setMessage('');
    try {const d=await request('/api/check-library/'+id);const v=await request(`/api/check-library/${id}/versions/${number||d.latest_version}`);setDefinition(d);setVersion(v);setDraft(toDraft(v));}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const [validation,setValidation]=useState<Validation|null>(null);
  const save = async () => {
    setBusy(true);setError('');setMessage('');
    try {
      const params=JSON.parse(draft.params);if(!params||typeof params!=='object'||Array.isArray(params))throw new Error('Параметры должны быть JSON-объектом');
      const payload={...draft,params,...(definition?{expected_version:definition.latest_version}:{})};
      const v=await request('/api/check-library'+(definition?'/'+definition.id+'/versions':''),{method:'POST',body:JSON.stringify(payload)});
      await load(v.check_id,v.version);await list();setMessage(`Сохранена версия ${v.version}.`);
    } catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const toggleArchive = async () => {
    if(!definition)return;setBusy(true);setError('');
    try {await request(`/api/check-library/${definition.id}/archive`,{method:'POST',body:JSON.stringify({archived:!definition.archived})});setDefinition({...definition,archived:!definition.archived});await list();}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const refreshHistory=async()=>{if(!definition)return;setBusy(true);try{setDefinition(await request('/api/check-library/'+definition.id));setError('');setMessage('История обновлена. Текст в редакторе сохранён.');}catch(e){setError((e as Error).message);}finally{setBusy(false);}};
  const test = async () => {
    if(!definition||!version)return;setBusy(true);setError('');
    try {await request(`/api/check-library/${definition.id}/versions/${version.version}/test`,{method:'POST',body:JSON.stringify({run_id:report})});setMessage(`Версия ${version.version} отправлена на проверку.`);}
    catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const fileInput=useRef<HTMLInputElement>(null);
  const [replaceExample,setReplaceExample]=useState(false);
  const edited=version?dirty:JSON.stringify(draft)!==JSON.stringify(blank());
  const allowDiscard=()=>!edited||window.confirm('Отбросить несохранённые изменения проверки?');
  const newCheck=()=>{if(!allowDiscard())return;setDefinition(null);setVersion(null);setDraft(blank());setMessage('');setError('');};
  const choose=(id:string,number?:number)=>{if(!allowDiscard())return;if(id)void load(id,number);else{setDefinition(null);setVersion(null);setDraft(blank());setMessage('');setError('');}};
  return <div className="check-library-page">
    <div className="page-heading"><h1>Библиотека проверок</h1><button type="button" className="button secondary" disabled={busy} onClick={newCheck}><Plus size={17}/>Новая проверка</button></div>
    {error&&<div className="error-box" role="alert">{error}</div>}{message&&<p className="check-library-notice" role="status">{message}</p>}
    <div className="panel check-library-workspace">
      <div className="check-library-picker"><label className="field">Сохранённая проверка<select aria-label="Сохранённая проверка" disabled={busy} value={definition?.id||''} onChange={e=>choose(e.target.value)}><option value="">{items.length?'Новая проверка':'Нет сохранённых проверок'}</option>{definition&&!items.some(i=>i.id===definition.id)&&<option value={definition.id}>{definition.name}{definition.archived?' · архив':''}</option>}{items.map(i=><option key={i.id} value={i.id}>{i.name}{i.archived?' · архив':''}</option>)}</select></label><label className="checkbox"><input type="checkbox" checked={archived} disabled={busy} onChange={e=>setArchived(e.target.checked)}/>Архив</label></div>
      {definition?.archived&&<div className="check-library-archived"><span>Проверка в архиве</span><button type="button" className="button secondary" disabled={busy} onClick={toggleArchive}>Восстановить</button></div>}
      <div className="check-library-body"><fieldset className="normalization-fieldset" disabled={busy||!!definition?.archived}>
        <label className="field check-library-name">Название проверки<input aria-label="Название проверки" value={draft.name} maxLength={120} placeholder="Например, доля изменившихся заявок" onChange={e=>setDraft({...draft,name:e.target.value})}/></label>
        <div className="check-library-codebar"><h2>Код проверки</h2><div><button type="button" className="button secondary" onClick={()=>fileInput.current?.click()}><Upload size={16}/>Загрузить .py</button><button type="button" className="text-button" onClick={()=>{if(draft.code.trim()&&draft.code.trim()!==example.trim())setReplaceExample(true);else setDraft(prev=>({...prev,code:example}));}}><FileCode2 size={16}/>Вставить пример</button></div><input hidden ref={fileInput} type="file" aria-label="Файл проверки библиотеки" accept=".py" onChange={async e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;try{if(!file.name.toLowerCase().endsWith('.py')||file.size>50000)throw new Error('Выберите файл .py до 50 КБ');const code=await file.text();if(draft.code.trim()&&draft.code.trim()!==example.trim()&&!window.confirm('Заменить текущий код содержимым файла?'))return;setDraft(prev=>({...prev,code,name:prev.name||file.name.replace(/\.py$/i,'')}));}catch(err){setError((err as Error).message);}}}/></div>
        <CodeEditor label="Код библиотеки" request={request} stage="checks" value={draft.code} onChange={code=>setDraft(prev=>({...prev,code}))} onValidation={setValidation} disabled={busy||!!definition?.archived}/>
        <CheckContract/>
        <details className="check-library-details"><summary>Описание и параметры</summary><div className="check-library-detail-body"><label className="field">Описание<textarea aria-label="Описание проверки" value={draft.description} maxLength={2000} rows={2} onChange={e=>setDraft({...draft,description:e.target.value})}/></label><label className="field">Параметры по умолчанию (JSON)<textarea aria-label="Параметры библиотеки" value={draft.params} rows={3} spellCheck={false} onChange={e=>setDraft({...draft,params:e.target.value})}/></label><div className="check-library-limits"><label className="field">Время, сек.<input aria-label="Время проверки" type="number" min={1} max={60} value={draft.timeout} onChange={e=>setDraft({...draft,timeout:Number(e.target.value)})}/></label><label className="field">Память, МБ<select aria-label="Память проверки" value={draft.memory_mb} onChange={e=>setDraft({...draft,memory_mb:Number(e.target.value)})}>{[512,1024,2048].map(n=><option key={n}>{n}</option>)}</select></label></div></div></details>
      </fieldset>
      {definition&&<details className="check-library-details"><summary>Версии и архив</summary><div className="check-library-detail-body"><label className="field">Сохранённая версия<select aria-label="Версия проверки" value={version?.version||''} disabled={busy} onChange={e=>choose(definition.id,Number(e.target.value))}>{definition.versions.map((v:Row)=><option key={v.id} value={v.version}>v{v.version} · {new Date(v.created_at).toLocaleString('ru-RU')}</option>)}</select></label><div className="check-library-actions"><button type="button" className="text-button" disabled={busy} onClick={refreshHistory}>Обновить историю</button><button type="button" className="text-button" disabled={busy} onClick={toggleArchive}><Archive size={15}/>{definition.archived?'Восстановить':'В архив'}</button></div></div></details>}
      {version&&<details className="check-library-details"><summary>Пробный запуск</summary><div className="check-library-detail-body"><label className="field">Отчёт для проверки<select aria-label="Отчёт для проверки" disabled={busy} value={report} onChange={e=>setReport(e.target.value)}>{!reports.length&&<option value="">Нет готовых отчётов</option>}{reports.map(r=><option value={r.id} key={r.id}>{r.name}</option>)}</select></label><div className="check-library-actions"><button type="button" className="button secondary" disabled={busy||!report||dirty} onClick={test}><Play size={15}/>Проверить v{version.version}</button><button type="button" className="text-button" onClick={openJobs}>Открыть задачи</button></div>{dirty&&<p>Перед пробным запуском сохраните изменения.</p>}</div></details>}
      </div>
      <div className="check-library-footer"><span>{version?`Версия ${version.version}`:''}</span><button type="button" className="button" disabled={busy||!validation?.valid||!draft.name.trim()||(!dirty&&version?.version===definition?.latest_version)||definition?.archived} onClick={save}><Save size={17}/>{busy?'Сохраняем…':definition?'Сохранить новую версию':'Сохранить проверку'}</button></div>
    </div>
    {replaceExample&&<ReplaceCodeDialog cancel={()=>setReplaceExample(false)} replace={()=>{setDraft(prev=>({...prev,code:example}));setReplaceExample(false);}}/>}
  </div>;
}

export function CheckPicker({request,value,onChange,onEdit,scope='all'}: {request:Request;value:CheckSelection|null;onChange:(v:CheckSelection)=>void;onEdit?:(spec:Row)=>void;scope?:'all'|'builtin'|'custom'}) {
  const [catalog,setCatalog]=useState<{builtin:Row[];custom:Row[]}|null>(null), [error,setError]=useState('');
  const currentValue=useRef(value);currentValue.current=value;
  useEffect(()=>{let alive=true; const load=async()=>{try{const [builtin,items]=await Promise.all([request<Row[]>('/api/check-library/builtin'),request<Row[]>('/api/check-library')]);const custom=await Promise.all(items.map(i=>request('/api/check-library/'+i.id)));if(alive){setCatalog({builtin,custom});if(currentValue.current===null)onChange({builtin_checks:builtin.map(i=>i.key),user_checks:[]});}}catch(e){if(alive)setError((e as Error).message);}};void load();return()=>{alive=false;};},[request,onChange]);
  if(error)return <div className="error-box">{error}</div>;
  if(!catalog||!value)return <p className="muted">Загрузка проверок…</p>;
  return <fieldset className="check-picker"><legend>Проверки расчёта</legend><p className="muted">Выбранные версии и параметры сохранятся в отчёте.</p>
    {scope!=='custom'&&catalog.builtin.map(c=><div className="setup-check-row" key={c.key}><label className="checkbox"><input type="checkbox" checked={value.builtin_checks.includes(c.key)} onChange={e=>onChange({...value,builtin_checks:e.target.checked?[...value.builtin_checks,c.key]:value.builtin_checks.filter(k=>k!==c.key)})}/>{c.name}</label>{onEdit&&<button type="button" className="text-button" disabled={value.user_checks.length>=20} onClick={()=>onEdit(c)}>Код</button>}</div>)}
    {scope==='all'&&!!catalog.custom.length&&<h4>Из библиотеки</h4>}{scope==='custom'&&!catalog.custom.length&&<div className="standard-procedure"><strong>Нет сохранённых проверок</strong></div>}
    {scope!=='builtin'&&catalog.custom.map(c=>{const chosen=value.user_checks.find(s=>c.versions.some((v:Row)=>v.id===s.version_id));return <div className="custom-choice" key={c.id}><label className="checkbox"><input type="checkbox" aria-label={'Включить '+c.name} checked={!!chosen} disabled={!chosen&&value.user_checks.length>=20} onChange={e=>onChange({...value,user_checks:e.target.checked?[...value.user_checks,{version_id:c.versions[0].id}]:value.user_checks.filter(s=>s!==chosen)})}/>{c.name}</label><select aria-label={'Версия '+c.name} disabled={!chosen} value={chosen?.version_id||c.versions[0].id} onChange={e=>onChange({...value,user_checks:value.user_checks.map(s=>s===chosen?{version_id:e.target.value}:s)})}>{c.versions.map((v:Row)=><option key={v.id} value={v.id}>v{v.version} · {v.timeout} сек. / {v.memory_mb} МБ</option>)}</select>{onEdit&&<button type="button" className="text-button" disabled={!chosen&&value.user_checks.length>=20} onClick={()=>onEdit({...c.versions.find((v:Row)=>v.id===(chosen?.version_id||c.versions[0].id)),check_id:c.id,params:chosen?.params})}>Код</button>}</div>;})}
    {value.user_checks.filter(s=>!catalog.custom.some(c=>c.versions.some((v:Row)=>v.id===s.version_id))).map(s=><div className="error-box unavailable-check" key={s.version_id}><span>Выбранная версия недоступна или проверка в архиве</span><button type="button" className="text-button" onClick={()=>onChange({...value,user_checks:value.user_checks.filter(v=>v.version_id!==s.version_id)})}>Убрать недоступную проверку</button></div>)}
    {value.builtin_checks.filter(key=>!catalog.builtin.some(c=>c.key===key)).map(key=><div className="error-box unavailable-check" key={key}><span>Штатная проверка недоступна: {key}</span><button type="button" className="text-button" onClick={()=>onChange({...value,builtin_checks:value.builtin_checks.filter(k=>k!==key)})}>Убрать недоступную штатную проверку</button></div>)}
    {value.builtin_checks.length+value.user_checks.length===0&&<p className="muted">Будет создан отчёт сравнения без автоматических проверок.</p>}
  </fieldset>;
}

export function CheckSource({runId,spec,request}:{runId:string;spec:Row;request:Request}) {
  const [expanded,setExpanded]=useState(false),[snapshot,setSnapshot]=useState<Row|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
  useEffect(()=>{if(!expanded)return;let alive=true;setError('');request(`/api/runs/${runId}/check-plan/${encodeURIComponent(spec.key)}`).then(row=>{if(alive)setSnapshot(row);}).catch(e=>{if(alive)setError(e.message);});return()=>{alive=false;};},[expanded,runId,spec.key,request,attempt]);
  return <details className="check-source" onToggle={e=>setExpanded(e.currentTarget.open)}><summary>Код и параметры</summary>{expanded&&<div className="check-source-body">
    {error?<div className="error-box" role="alert">{error}<button className="text-button" type="button" onClick={()=>setAttempt(n=>n+1)}>Повторить</button></div>:!snapshot?<p role="status">Загрузка кода…</p>:<><p className="check-source-meta">{snapshot.kind==='builtin'?'Штатная проверка':'Пользовательская проверка'} · версия {snapshot.version}</p><pre className="job-log">{snapshot.code}</pre><details><summary>Параметры</summary><pre className="job-log">{JSON.stringify(snapshot.params,null,2)}</pre></details>{snapshot.execution?.error&&<pre className="job-log error-box">{snapshot.execution.error}</pre>}{snapshot.logs&&<details><summary>Вывод проверки</summary><pre className="job-log">{snapshot.logs}</pre></details>}</>}
  </div>}</details>;
}

export function CheckPlan({run,request}: {run:Row;request:Request}) {
  const [snapshot,setSnapshot]=useState<Row|null>(null), [error,setError]=useState('');
  const open=async(key:string)=>{setSnapshot(null);setError('');try{setSnapshot(await request(`/api/runs/${run.id}/check-plan/${encodeURIComponent(key)}`));}catch(e){setError((e as Error).message);}};
  return <details id="check-plan" className="disclosure check-plan"><summary>Состав проверок{run.check_plan?.length ? ` · ${run.check_plan.length}` : ""}</summary>{run.check_plan===null&&<p>Код проверок этого отчёта не сохранён.</p>}
    {run.check_plan?.map((c:Row)=>{const execution=run.check_executions?.find((e:Row)=>e.key===c.key);return <div className="plan-row" key={c.key}><span>{c.name}<small>{c.kind==='builtin'?'Штатная':'Пользовательская'} · v{c.version}</small></span><span>{execution?`${status[execution.status]||execution.status}${execution.status==='completed'?' · строк: '+execution.rows:''}`:run.status==='queued'?'В очереди':['running','cancelling'].includes(run.status)?'В составе расчёта':'Нет сведений о выполнении'}</span>{run.can_manage && <button className="text-button" onClick={()=>open(c.key)}>Код и параметры</button>}</div>;})}
    {error&&<div className="error-box">{error}</div>}
    {snapshot&&<div className="snapshot"><div className="panel-heading"><h3>{snapshot.name} · v{snapshot.version}</h3><button className="text-button" onClick={()=>setSnapshot(null)}>Скрыть код</button></div><pre className="job-log">{snapshot.code}</pre><pre className="job-log">{JSON.stringify(snapshot.params,null,2)}</pre>{snapshot.execution?.error&&<pre className="job-log error-box">{snapshot.execution.error}</pre>}{snapshot.logs&&<><h4>Вывод проверки</h4><pre className="job-log">{snapshot.logs}</pre></>}</div>}
  </details>;
}
