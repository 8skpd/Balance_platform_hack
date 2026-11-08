import {uploadArchive} from './uploadArchive';
import {datasetLabel} from './labels';
import { useEffect, useRef, useState } from 'react';
import { Database, Upload, Play, CheckCircle2, RefreshCw, X } from 'lucide-react';

type Row = Record<string, any>;
type Request = <T = any>(url:string, options?:RequestInit) => Promise<T>;
const labels:Row = {queued:'В очереди', running:'Проверка', cancelling:'Отмена…', cancelled:'Отменён', completed:'Готов', failed:'Ошибка', timed_out:'Лимит времени'};
const active = (status:string) => ['queued','running','cancelling'].includes(status);
const number = (value:number) => new Intl.NumberFormat('ru-RU').format(value);
const bytes = (value:number) => value < 1024 ? `${value} Б` : `${(value / (value < 1024 ** 2 ? 1024 : 1024 ** 2)).toLocaleString('ru-RU', {maximumFractionDigits:1})} ${value < 1024 ** 2 ? 'КБ' : 'МБ'}`;
const diagnostic = (error:string) => error.split('\n').filter(Boolean).at(-1)?.replace(/^(?:\w*Error|Exception):\s*/, '') || error;

export function Datasets({request, onRun}:{request:Request; onRun:(dataset:Row)=>void}) {
  const [datasets,setDatasets] = useState<Row[]>([]), [history,setHistory] = useState<Row[]>([]);
  const [mode,setMode]=useState<'archive'|'pair'|'single'>('archive'),[before,setBefore]=useState<File|null>(null),[after,setAfter]=useState<File|null>(null);
  const beforeInput=useRef<HTMLInputElement|null>(null),afterInput=useRef<HTMLInputElement|null>(null),generation=useRef(0);
  const [limits,setLimits] = useState<Row|null>(null), [file,setFile] = useState<File|null>(null), [name,setName] = useState('');
  const [error,setError] = useState(''), [pollError,setPollError] = useState(''), [notice,setNotice] = useState('');
  const [uploading,setUploading] = useState(false), [percent,setPercent] = useState(0), [busy,setBusy] = useState(''), [refresh,setRefresh] = useState(0);
  const xhr = useRef<XMLHttpRequest|null>(null), input = useRef<HTMLInputElement|null>(null);
  useEffect(() => {
    let closed=false; let timer:ReturnType<typeof setTimeout>; const controller=new AbortController();
    const load=async()=>{
      try {
        const [d,h,l]=await Promise.all([request('/api/datasets',{signal:controller.signal}),request('/api/dataset-imports',{signal:controller.signal}),request('/api/dataset-imports/limits',{signal:controller.signal})]);
        if(!closed){setDatasets(d);setHistory(h);setLimits(l);setPollError('');}
      } catch(e){if(!closed)setPollError((e as Error).message);}
      finally {if(!closed)timer=setTimeout(load,2500);}
    };
    void load();
    return ()=>{closed=true;clearTimeout(timer);controller.abort();};
  },[request,refresh]);
  useEffect(()=>()=>{generation.current++;if(xhr.current){xhr.current.onload=null;xhr.current.onerror=null;xhr.current.onabort=null;xhr.current.upload.onprogress=null;xhr.current.abort();}},[]);
  const cancelUpload=()=>{generation.current++;if(xhr.current)xhr.current.abort();else {setUploading(false);setNotice('Передача отменена.');}};
  const upload=async(e:React.FormEvent)=>{
    e.preventDefault();setError('');setNotice('');
    if(!limits)return;
    const files=mode==='pair'?[before,after]:[file];
    if(files.some(f=>!f))return;
    for(const f of files as File[]){
      if(!f.size || !(mode==='archive'?/\.zip$/i:/\.(csv|xml)$/i).test(f.name)){setError(mode==='archive'?'Выберите непустой ZIP-архив':'Выберите непустые файлы CSV или XML');return;}
      const max=mode==='archive'?limits.max_upload_bytes:/\.csv$/i.test(f.name)?limits.max_csv_bytes||limits.max_file_bytes:limits.max_xml_bytes||limits.max_file_bytes;
      if(f.size>max){setError(`Файл «${f.name}» превышает лимит ${bytes(max)}`);return;}
    }
    if(mode!=='archive'&&(files as File[]).reduce((n,f)=>n+f.size,0)>limits.max_upload_bytes-1024){setError(`Общий объём файлов превышает ${bytes(limits.max_upload_bytes)}`);return;}
    setUploading(true);setPercent(0);const attempt=++generation.current;
    try {
      let body:Blob=file!;let filename=file?.name||'';
      if(mode!=='archive'){
        const entries=mode==='pair'?[
          {path:'RESULTS/BEFORE_MASTER/data.'+before!.name.split('.').at(-1)!.toLowerCase(),file:before!},
          {path:'RESULTS/AFTER_BETA/data.'+after!.name.split('.').at(-1)!.toLowerCase(),file:after!},
        ]:[{path:'dataset.'+file!.name.split('.').at(-1)!.toLowerCase(),file:file!}];
        body=await uploadArchive(entries,{require_application_ids:true,source_files:(files as File[]).map(f=>f.name)},()=>generation.current===attempt);
        if(generation.current!==attempt)return;
        filename=name.trim()+'.zip';
      }
      if(body.size>limits.max_upload_bytes)throw new Error(`Загрузка превышает лимит ${bytes(limits.max_upload_bytes)}`);
      const req=new XMLHttpRequest();xhr.current=req;
      req.open('POST','/api/dataset-imports?'+new URLSearchParams({name:name.trim(),filename}));
      req.setRequestHeader('X-Balance-Request','1');req.setRequestHeader('Content-Type','application/zip');
      req.upload.onprogress=e=>{if(e.lengthComputable)setPercent(Math.round(e.loaded/e.total*100));};
      const finish=()=>{xhr.current=null;setUploading(false);setRefresh(n=>n+1);};
      req.onload=()=>{
        finish();
        if(req.status===401)window.dispatchEvent(new Event('balance-session-expired'));
        if(req.status===202){setNotice('Файлы переданы на проверку.');setFile(null);setBefore(null);setAfter(null);setName('');for(const ref of [input,beforeInput,afterInput])if(ref.current)ref.current.value='';}
        else {let message='Не удалось загрузить данные';try{const body=JSON.parse(req.responseText);message=typeof body.detail==='string'?body.detail:JSON.stringify(body.detail);}catch{}setError(message);}
      };
      req.onerror=()=>{finish();setError('Соединение прервано. Проверьте историю импорта перед повторной загрузкой.');};
      req.onabort=()=>{finish();setNotice('Передача прервана. Если файлы уже приняты, отмените проверку в истории импорта.');};
      req.send(body);
    } catch(e){if(generation.current===attempt){setUploading(false);setError((e as Error).message);}}
  };
  const action=async(id:string,action:string)=>{
    setBusy(id);setError('');setNotice('');
    try{await request(`/api/jobs/${id}/${action}`,{method:'POST'});setRefresh(n=>n+1);}
    catch(e){setError((e as Error).message);}finally{setBusy('');}
  };
  return <div className="datasets-view">
    <div className="page-heading"><div><h1>Датасеты</h1></div></div>
    {(error||pollError)&&<div className="error-box" role="alert">{error||pollError}</div>}
    {notice&&<div className="info-strip" role="status">{notice}</div>}
    <section className="panel upload-panel"><div className="panel-heading"><h2><Upload size={20}/> Загрузить датасет</h2><span>Доступен только вам</span></div>
      <form onSubmit={upload}>
        <label className="field">Способ загрузки<select aria-label="Способ загрузки" value={mode} disabled={uploading} onChange={e=>{setMode(e.target.value as 'archive'|'pair'|'single');setFile(null);setError('');}}><option value="archive">ZIP-архив</option><option value="pair">Два файла CSV / XML</option><option value="single">Один общий CSV / XML</option></select></label>
        <div className="upload-fields">
          {mode!=='pair'?<label className="field">{mode==='archive'?'ZIP-архив':'Общий CSV / XML'}<input key={mode} ref={input} type="file" accept={mode==='archive'?'.zip,application/zip':'.csv,.xml,text/csv,application/xml,text/xml'} required disabled={uploading} onChange={e=>{const next=e.target.files?.[0]||null;setFile(next);if(next&&!name)setName(next.name.replace(/\.(zip|csv|xml)$/i,'').slice(0,120));setError('');}}/></label>:<>
            <label className="field">До (MASTER)<input ref={beforeInput} type="file" accept=".csv,.xml,text/csv,application/xml,text/xml" required disabled={uploading} onChange={e=>{const next=e.target.files?.[0]||null;setBefore(next);if(next&&!name)setName(next.name.replace(/\.(csv|xml)$/i,'').slice(0,120));setError('');}}/></label>
            <label className="field">После (BETA)<input ref={afterInput} type="file" accept=".csv,.xml,text/csv,application/xml,text/xml" required disabled={uploading} onChange={e=>{setAfter(e.target.files?.[0]||null);setError('');}}/></label>
          </>}
          <label className="field">Название датасета<input required maxLength={120} value={name} disabled={uploading} onChange={e=>setName(e.target.value)}/></label>
        </div>
        <details className="import-format-help"><summary>Форматы и примеры</summary>
          <p><b>Один общий файл:</b> в CSV добавьте колонку <code>SOURCE</code> со значениями <code>BEFORE</code> / <code>AFTER</code>. В XML используйте <code>&lt;dataset&gt;&lt;BEFORE&gt;&lt;application&gt;…</code> и такой же раздел <code>AFTER</code>.</p><p><b>CSV:</b> одна строка — одна заявка. Обязательная колонка <code>application_id</code>; вложенные поля — через точку, например <code>decisionResult.decision</code>. UTF-8, разделитель — запятая, точка с запятой или табуляция.</p>
          <p><b>XML:</b> список <code>&lt;applications&gt;</code> с элементами <code>&lt;application&gt;</code>. ID — поле <code>appData.application_id</code> или атрибут <code>application_id</code>. Массив — элементы <code>&lt;item&gt;</code> внутри поля.</p>
          <p>Числа, <code>true</code>, <code>false</code> и <code>null</code> распознаются автоматически; пустое значение остаётся пустой строкой. CSV поддерживает JSON в ячейках. В XML можно явно задать <code>type="string"</code>, <code>type="array"</code>, <code>type="object"</code> или <code>type="null"</code>. ID сохраняются как текст.</p>
          <p><b>ZIP:</b> папки <code>RESULTS/BEFORE_MASTER/</code> и <code>RESULTS/AFTER_BETA/</code> с JSON, CSV или XML. Также подходит пара <code>BEFORE.csv</code> / <code>AFTER.csv</code> или XML. Заявки в обеих версиях должны иметь одинаковые ID.</p>
          <div className="job-actions"><a className="text-button" href="/examples/dataset.csv" download>Общий CSV</a><a className="text-button" href="/examples/dataset.xml" download>Общий XML</a><a className="text-button" href="/examples/before.csv" download>CSV «До»</a><a className="text-button" href="/examples/after.csv" download>CSV «После»</a><a className="text-button" href="/examples/before.xml" download>XML «До»</a><a className="text-button" href="/examples/after.xml" download>XML «После»</a></div>
        </details>
        <div className="upload-actions"><button className="button primary" disabled={uploading||(mode==='pair'?!before||!after:!file)||!name.trim()||!limits}><Upload size={16}/>Загрузить и проверить</button>
          {uploading&&<button className="button secondary" type="button" onClick={cancelUpload}><X size={16}/>Прервать передачу</button>}
          <span className="muted">{mode==='archive'?`ZIP до ${limits?bytes(limits.max_upload_bytes):'512 МБ'}`:`Файл до ${limits?bytes(limits.max_csv_bytes||limits.max_file_bytes):'512 МБ'}`} · до {number(limits?.max_pairs || 500000)} пар</span></div>
        {uploading&&<div className="upload-progress" role="status"><progress value={percent} max={100}/><span>{percent<100?`Передача: ${percent}%`:'Ожидание подтверждения сервера…'}</span></div>}
      </form>
    </section>
    <h2 className="dataset-section-title">Готовы к сравнению</h2>
    <div className="dataset-grid">{datasets.map(d=><section className="panel dataset-card" key={d.id}>
      <div className="dataset-card-title"><Database size={25} className="orange"/><span className="status neutral">{d.visibility==='private'?'Только мне':'Общий набор'}</span></div>
      <h2>{datasetLabel(d)}</h2>
      <div className="dataset-stats"><div><strong>{number(d.before)}</strong><span>BEFORE / MASTER</span></div><div><strong>{number(d.after)}</strong><span>AFTER / BETA</span></div></div>
      <div className="dataset-validation"><CheckCircle2 size={16}/>{d.unpaired?`${d.unpaired} без пары`:`Пар заявок: ${number(d.pairs)}`}</div>
      {!!d.missing_application_ids&&<p className="muted">В {number(d.missing_application_ids)} заявках ID взят из имени файла.</p>}
      {d.array_paths?.length>0&&<details><summary>Массивы для настройки ({d.array_paths.length}{d.array_paths.length===d.array_paths_limit?'+':''})</summary><ul className="dataset-array-paths">{d.array_paths.map((path:string)=><li key={path}><code>{path}</code></li>)}</ul></details>}
      <button className="button primary" onClick={()=>onRun(d)}><Play size={15}/>Запустить сравнение</button>
    </section>)}</div>
    {!datasets.length&&limits&&<p className="muted">Пока нет готовых датасетов.</p>}
    <h2 className="dataset-section-title">История импорта</h2>
    {!history.length&&<p className="muted">Загрузок пока нет.</p>}
    <div className="import-history">{history.map(h=><section className="panel import-card" key={h.id} data-import-id={h.id}>
      <div className="panel-heading"><div><h3>{h.name}</h3><span className="muted">{h.filename} · {bytes(h.size)} · {new Date(h.created_at).toLocaleString('ru-RU')}</span></div><span className={`status ${h.status==='completed'?'success':active(h.status)?'pending':'danger'}`}>{labels[h.status]}</span></div>
      {active(h.status)&&<div className="upload-progress"><progress value={h.progress||0} max={100}/><span>{h.status==='running'?`${h.stage||'Проверка'} · ${h.progress||0}%`:labels[h.status]}</span></div>}
      {h.stats&&<p>Пар: {number(h.stats.pairs)}{h.stats.source_formats?.length?` · ${h.stats.source_formats.join(" / ").toUpperCase()}`:""}{h.stats.ignored_files>0&&` · пропущено сопутствующих файлов: ${h.stats.ignored_files}`}</p>}
      {h.error&&<pre className="job-log error-box">{diagnostic(h.error)}</pre>}
      <div className="upload-actions">{['queued','running'].includes(h.status)&&<button className="button secondary" disabled={!!busy} onClick={()=>action(h.job_id,'cancel')}><X size={14}/>Отменить проверку</button>}
      {['failed','cancelled','timed_out'].includes(h.status)&&<button className="button secondary" disabled={!!busy} onClick={()=>action(h.job_id,'retry')}><RefreshCw size={14}/>Повторить проверку</button>}
      <a className="text-button" href={`?view=jobs&job=${h.job_id}`}>Подробности задачи</a></div>
    </section>)}</div>
  </div>;
}
