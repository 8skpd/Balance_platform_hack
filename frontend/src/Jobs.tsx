import {CheckContract,ReplaceCodeDialog} from './ProcedureContract';
import {PythonUpload} from './PythonUpload';
import {CodeEditor,type Validation} from './CodeEditor';
import { useEffect, useState } from 'react';
import { Play, RefreshCw, X } from 'lucide-react';

type Row = Record<string, any>;
type Request = <T = any>(url: string, options?: RequestInit) => Promise<T>;
const labels: Row = {queued:'В очереди', running:'Выполняется', cancelling:'Отмена…', cancelled:'Отменена', completed:'Завершена', failed:'Ошибка', timed_out:'Лимит времени'};
const kinds: Row = {run:'Расчёт отчёта', check:'Python-проверка', ai:'Мнение ИИ', import:'Импорт датасета'};
const active = (s: string) => ['queued','running','cancelling'].includes(s);
const date = (s: string) => new Date(s).toLocaleString('ru-RU');

function usePoll(url: string, request: Request, refresh = 0) {
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let closed = false;
    setData(null);
    const load = async () => {
      if (!url) return;
      try { const next = await request(url); if (!closed) {setData(next); setError('');} }
      catch (e) { if (!closed) setError((e as Error).message); }
    };
    void load(); const timer = setInterval(load, 2500);
    return () => {closed = true; clearInterval(timer);};
  }, [url, request, refresh]);
  return {data, error};
}

export function Jobs({request, runId, openRun}: {request: Request; runId?: string; openRun?: (id:string)=>void}) {
  const [selected, setSelected] = useState(new URLSearchParams(location.search).get('job') || '');
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const list = usePoll('/api/jobs' + (runId ? '?run_id='+encodeURIComponent(runId) : ''), request, refresh);
  const detail = usePoll(selected ? '/api/jobs/'+selected : '', request, refresh);
  const runtime = usePoll('/api/runtime', request);
  const action = async (id:string, name:string) => {
    setBusy(true); setError('');
    try { const job = await request('/api/jobs/'+id+'/'+name, {method:'POST'}); setSelected(job.id); setRefresh(n=>n+1); }
    catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const job = detail.data;
  return <div className="jobs-view">
    <div className="page-heading"><div><h1>{runId ? 'Задачи отчёта' : 'Мои задачи'}</h1></div></div>
    {runtime.data && !(runtime.data.broker && runtime.data.workers && runtime.data.scheduler) && <div className="info-strip">Обработка временно недоступна. Задания сохранены в очереди.</div>}
    {(error || list.error || detail.error || runtime.error) && <div className="error-box" role="alert">{error || list.error || detail.error || runtime.error}</div>}
    <div className="panel table-panel"><div className="table-scroll"><table><thead><tr><th>Задача</th><th>Создана</th><th>Статус</th><th>Действия</th></tr></thead><tbody>
      {list.data?.map((j:Row)=><tr key={j.id} data-job-id={j.id} className={selected===j.id ? 'selected-job' : ''}><td><button className="text-button" onClick={()=>setSelected(j.id)}>{kinds[j.kind]}{j.check_ref && <small>{j.check_ref.name}</small>}</button></td><td>{date(j.created_at)}</td><td><span className={`status ${j.status==='completed'?'success':active(j.status)?'pending':'danger'}`}>{labels[j.status]}</span></td><td><div className="job-actions">{['queued','running'].includes(j.status) && <button className="button secondary" disabled={busy} onClick={()=>action(j.id,'cancel')}><X size={14}/>Отменить</button>}{['failed','cancelled','timed_out'].includes(j.status) && <button className="button secondary" disabled={busy} onClick={()=>action(j.id,'retry')}><RefreshCw size={14}/>Повторить</button>}{openRun && j.run_id && <button className="text-button" onClick={()=>openRun(j.run_id)}>К запуску</button>}</div></td></tr>)}
      {list.data?.length===0 && <tr><td colSpan={4}>Задач пока нет. Создайте расчёт или запустите Python-проверку.</td></tr>}
    </tbody></table></div></div>
    {job && <div className="panel job-detail"><div className="panel-heading"><h2>{kinds[job.kind]} · {labels[job.status]}</h2></div>
      {job.check_ref && <p>{job.check_ref.name} · v{job.check_ref.version}</p>}
      {job.error && <pre className="job-log error-box">{job.error}</pre>}
      {job.kind==='check' && job.result && <><h3>Результат проверки</h3><div className="table-scroll"><table><thead><tr>{['Проверка','Аргумент','Количество / всего','Метрика','Результат','Уверенность'].map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{job.result.rows.map((r:Row,i:number)=><tr key={i}><td>{r.LABEL}</td><td>{r.ARGUMENT}</td><td>{r.CNT} / {r.TOTAL}</td><td>{r.METRIC}</td><td>{r.RESULT}</td><td>{r.CONFIDENCE}</td></tr>)}</tbody></table></div><details><summary>Полный результат</summary><pre className="job-log">{JSON.stringify(job.result,null,2)}</pre></details></>}
      {job.kind==='import' && <p><a className="text-button" href="?view=datasets">К датасетам</a>{job.result && ` · ${job.result.pairs} пар · ${job.result.json_files} JSON`}</p>}
      {job.kind==='ai' && job.result && <div className="ai-content">{job.result.content}</div>}
      {job.code && <details><summary>Код и параметры этого запуска</summary><pre className="job-log">{job.code}</pre><pre>{JSON.stringify(job.params,null,2)}</pre></details>}
      <details><summary>Диагностика</summary><p>Попыток: {job.attempts} · Лимиты: {job.timeout} сек. / {job.memory_mb} МБ</p><pre className="job-log">{job.logs || 'Нет вывода'}</pre></details>
    </div>}
  </div>;
}

export const example = `def run(mart, params):
    apps = mart.app_ids(diff_flag=1)
    total = mart.total_apps()
    share = len(apps) / total if total else 0
    return [{
        "LABEL": "Доля изменившихся заявок",
        "SOURCE": "AFTER",
        "ARGUMENT": "Все заявки",
        "CNT": len(apps), "TOTAL": total,
        "METRIC": round(share, 4),
        "RESULT": "FAIL" if share > params.get("max_share", 0.3) else "PASS",
        "CONFIDENCE": "HIGH",
        "APP_IDS": apps,
    }]

CHECK = {"id": "changed_share", "label": "Доля изменений", "entrypoint": run}
`;

export function PythonEditor({request, runId}: {request:Request; runId:string}) {
  const [code,setCode] = useState(example);
  const [validation,setValidation]=useState<Validation|null>(null);
  const [libraryName,setLibraryName] = useState('');
  const [publish,setPublish]=useState(true);
  const [replace,setReplace]=useState(false);
  const [params,setParams] = useState('{"max_share": 0.3}');
  const [timeout,setTimeout] = useState(20);
  const [memory,setMemory] = useState(1024);
  const [error,setError] = useState('');
  const [message,setMessage] = useState('');
  const [busy,setBusy] = useState(false);
  const runtime = usePoll('/api/runtime', request);
  const submit = async () => {
    setError(''); setMessage(''); setBusy(true);
    try {
      const parsed = JSON.parse(params);
      if (!parsed || typeof parsed!=='object' || Array.isArray(parsed)) throw new Error('Параметры должны быть JSON-объектом');
      const job = await request('/api/runs/'+runId+'/python-checks', {method:'POST', body:JSON.stringify({code,params:parsed,timeout,memory_mb:memory,publish,name:libraryName.trim()||"Пользовательская проверка"})});
      setMessage('Проверка поставлена в очередь. Результат появится в списке ниже.');
    } catch(e) {setError((e as Error).message);} finally {setBusy(false);}
  };
  const saveToLibrary = async () => {
    setError('');setMessage('');setBusy(true);
    try {
      const parsed=JSON.parse(params);
      if(!parsed||typeof parsed!=='object'||Array.isArray(parsed))throw new Error('Параметры должны быть JSON-объектом');
      const v=await request('/api/check-library',{method:'POST',body:JSON.stringify({name:libraryName,code,params:parsed,timeout,memory_mb:memory})});
      setMessage(`«${v.name}» сохранена как v1. Она доступна в библиотеке и при создании нового расчёта.`);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  return <><div className="panel python-panel"><div className="panel-heading"><div><h2>Пользовательская проверка</h2><p>Проверка читает витрину этого отчёта. Результат можно добавить к проверкам этого отчёта без повторного расчёта данных.</p></div></div>
    <CheckContract insert={()=>{if(code.trim()&&code.trim()!==example.trim())setReplace(true);else setCode(example);}}/>
    <div className="python-editor-toolbar"><h3>Код проверки</h3><PythonUpload label="Файл проверки" disabled={busy} onFile={async file=>{try{if(!file.name.toLowerCase().endsWith('.py')||file.size>50000)throw new Error('Выберите файл .py до 50 КБ');const next=await file.text();if(code.trim()&&code.trim()!==example.trim()&&!window.confirm('Заменить текущий код содержимым файла? Несохранённые изменения будут потеряны.'))return;setCode(next);if(!libraryName)setLibraryName(file.name.replace(/\.py$/i,''));}catch(err){setError((err as Error).message);}}}/></div>
    <div className="code-label"><CodeEditor request={request} stage="checks" value={code} onChange={setCode} onValidation={setValidation} disabled={busy}/></div>
    <details className="editor-options"><summary>Параметры проверки</summary><div className="python-options"><label>Параметры JSON<textarea aria-label="Параметры JSON" value={params} onChange={e=>setParams(e.target.value)} maxLength={16000}/></label><label>Время, сек.<input aria-label="Лимит времени" type="number" min={1} max={60} value={timeout} onChange={e=>setTimeout(Number(e.target.value))}/></label><label>Память, МБ<select aria-label="Лимит памяти" value={memory} onChange={e=>setMemory(Number(e.target.value))}><option>512</option><option>1024</option><option>2048</option></select></label></div></details>
    {(error || runtime.error) && <div className="error-box" role="alert">{error || runtime.error}</div>}
    {runtime.data && !runtime.data.sandbox.available && <div className="error-box">{runtime.data.sandbox.error}</div>}
    {message && <div className="info-strip" role="status">{message}</div>}
    <label className="checkbox"><input type="checkbox" checked={publish} onChange={e=>setPublish(e.target.checked)}/>Добавить результат в общий отчёт</label>
    <button className="button primary" disabled={busy || !validation?.valid || !runtime.data?.sandbox.available} onClick={submit}><Play size={16}/>{busy ? 'Отправка…' : 'Запустить проверку'}</button>
    <div className="save-library"><label className="field">Название для библиотеки<input aria-label="Название для библиотеки" value={libraryName} maxLength={120} onChange={e=>setLibraryName(e.target.value)}/></label><button className="button secondary" disabled={busy||!validation?.valid||!libraryName.trim()} onClick={saveToLibrary}>Сохранить в библиотеку</button></div>
    </div>{replace&&<ReplaceCodeDialog cancel={()=>setReplace(false)} replace={()=>{setCode(example);setReplace(false);}}/>}<Jobs request={request} runId={runId}/></>;
}
