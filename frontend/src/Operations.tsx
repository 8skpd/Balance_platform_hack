import {Database,Download,ShieldCheck,Plus,Loader2} from 'lucide-react';
import {Disclosure} from './Disclosure';
import {useEffect,useState} from 'react';
import {Activity,Issue,Pages,TransferForm,json,useAction,useResource} from './Workflow';
import type {Request,Row} from './Workflow';
export function fileSize(bytes:number) {
 if(!Number.isFinite(bytes)||bytes<0)return '—';
 const units=['Б','КБ','МБ','ГБ','ТБ'];
 const index=bytes>0?Math.min(units.length-1,Math.floor(Math.log(bytes)/Math.log(1024))):0;
 return (bytes/1024**index).toLocaleString('ru-RU',{maximumFractionDigits:index?1:0})+' '+units[index];
}
export function AccountOperations({request}:{request:Request}){
 const r=useResource('/api/admin/users',request),a=useAction();const [selected,setSelected]=useState<Row|null>(null),[reason,setReason]=useState('');
 return <section className="panel operations-panel account-operations"><h2>Доступ сотрудников</h2><p className="muted">Блокировка отзывает все сессии и отменяет незавершённые задачи. Разблокировка требует нового входа.</p><Issue text={r.error||a.error}/><label className="field">Обоснование действия<input value={reason} onChange={e=>setReason(e.target.value)} maxLength={1000} placeholder="Например: сотрудник завершил работу"/></label><div className="table-scroll"><table><thead><tr><th>Аккаунт</th><th>Доступ</th><th>Действия</th></tr></thead><tbody>{r.data?.map((u:Row)=><tr key={u.id}><td>{u.display_name}<small>@{u.username}</small></td><td>{u.blocked?'Заблокирован':'Активен'}</td><td><div className="job-actions"><button className="button secondary" disabled={a.busy||!reason.trim()} onClick={()=>a.act(async()=>{await request(`/api/admin/users/${u.id}/state`,json({blocked:!u.blocked,expected_blocked:u.blocked,reason},'PUT'));r.reload();window.dispatchEvent(new Event('balance-role-changed'));})}>{u.blocked?'Разблокировать':'Заблокировать'}</button><button className="text-button" disabled={a.busy||!reason.trim()} onClick={()=>a.act(async()=>{await request(`/api/admin/users/${u.id}/revoke-sessions`,json({reason}));window.dispatchEvent(new Event('balance-role-changed'));alert('Все сессии отозваны. Новый вход разрешён, если аккаунт активен.');})}>Отозвать сессии</button><button className="text-button" onClick={()=>setSelected(u)}>Передать анализы</button></div></td></tr>)}</tbody></table></div>{selected&&<EmployeeAnalyses key={selected.id} employee={selected} request={request} close={()=>setSelected(null)}/>}</section>;
}
function EmployeeAnalyses({employee,request,close}:{employee:Row;request:Request;close:()=>void}){
 const [page,setPage]=useState(1);const r=useResource(`/api/admin/users/${employee.id}/analyses?page=${page}`,request);
 return <div className="operations-panel"><h3>Анализы @{employee.username}</h3><p className="muted">Здесь показаны сведения для передачи владельцу. Содержимое отчёта доступно только участникам.</p><button className="text-button" onClick={close}>Закрыть</button><Issue text={r.error}/>{r.data?.items.map((run:Row)=><div className="comment" key={run.id}><h4>{run.name}</h4><small>{({queued:'В очереди',running:'Выполняется',completed:'Готов',failed:'Ошибка',cancelled:'Отменён',cancelling:'Отменяется'} as Row)[run.status]||'Обрабатывается'}</small><TransferForm run={run} request={request} done={r.reload}/></div>)}<Pages data={r.data} page={page} setPage={setPage}/></div>;
}
function Limits({request}:{request:Request}){
 const r=useResource('/api/admin/limits',request),a=useAction();const [values,setValues]=useState<Row|null>(null),[notice,setNotice]=useState('');
 useEffect(()=>{if(r.data)setValues(r.data);},[r.data]);
 const fields:Row={max_active_jobs:'Активных задач на пользователя',max_runs:'Анализов на пользователя (включая архив)',max_user_storage_mb:'Хранилище пользователя, МБ',max_job_storage_mb:'Рабочие файлы одной задачи, МБ',min_free_mb:'Минимум свободного места на сервере, МБ'};
 return <section className="panel operations-panel"><h2>Квоты</h2><Issue text={r.error||a.error}/>{notice&&<p role="status">{notice}</p>}{values&&<form onSubmit={e=>{e.preventDefault();a.act(async()=>{const next=await request('/api/admin/limits',json(values,'PUT'));setValues(next);setNotice('Квоты сохранены.');});}}><div className="two-columns">{Object.entries(fields).map(([k,label])=><label className="field" key={k}>{label as string}<input type="number" min={1} required value={values[k]} onChange={e=>setValues({...values,[k]:Number(e.target.value)})}/></label>)}</div><p className="muted">Новые задания проверяются перед постановкой в очередь. Исполнитель контролирует объём файлов во время работы. Архив сохраняет данные и продолжает занимать квоту.</p><button className="button" disabled={a.busy}>Сохранить квоты</button> <button className="text-button" type="button" onClick={r.reload}>Обновить значения</button></form>}</section>;
}
export function BackupPanel({request}:{request:Request}){
 const r=useResource('/api/admin/backups',request,4000),a=useAction();const busy=a.busy||r.data?.some((x:Row)=>x.status==='running');
 return <section className="panel backup-panel">
  <div className="backup-heading"><div><h2>Резервные копии</h2><p>База, датасеты и файлы отчётов.</p></div><button className="button" disabled={busy} onClick={()=>a.act(async()=>{await request('/api/admin/backups',{method:'POST'});r.reload();})}>{busy?<Loader2 size={16} className="spin"/>:<Plus size={16}/>}Создать резервную копию</button></div>
  <Issue text={r.error||a.error}/>
  <details className="backup-help"><summary>Как работает резервирование</summary><p>В копию также входят загрузки и файлы задач. Перед созданием завершите активные задачи; на время копирования изменения приостанавливаются.</p><p>Проверка восстановления выполняется отдельно от рабочей базы: сверяются файлы, база и открытие отчётов. Скачать копию может только администратор.</p></details>
  <div className="backup-history">{r.data?.map((op:Row)=><article className="backup-record" key={op.id}>
   <div className="backup-record-heading"><span className="backup-record-icon">{op.kind==='create'?<Database size={20}/>:<ShieldCheck size={20}/>}</span><div><h3>{op.kind==='create'?'Создание копии':'Проверка восстановления'}</h3><time dateTime={op.created_at}>{new Date(op.created_at).toLocaleString('ru-RU')}</time></div><span className={`status ${op.status==='completed'?'success':op.status==='failed'?'danger':'pending'}`}>{({running:'Выполняется',completed:'Успешно',failed:'Ошибка'} as Row)[op.status]}</span></div>
   <Issue text={op.error||''}/>
   {op.result&&<dl className="backup-facts">{(op.kind==='create'?[['Размер',fileSize(op.result.bytes)],['Файлов',op.result.files]]:[['Файлов проверено',op.result.files_verified],['Отчётов открыто',op.result.reports_opened],['База',op.result.integrity_check==='ok'?'Проверена':op.result.integrity_check],['API',op.result.api_probe?'проверен':'не проверен']]).map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>}
   {op.kind==='create'&&op.status==='completed'&&<div className="backup-actions"><a className="button secondary" href={`/api/admin/backups/${op.archive_id}/download`}><Download size={16}/>Скачать ZIP</a><button className="button secondary" disabled={busy} onClick={()=>a.act(async()=>{await request(`/api/admin/backups/${op.archive_id}/verify`,{method:'POST'});r.reload();})}><ShieldCheck size={16}/>Проверить восстановление</button></div>}
  </article>)}</div>{r.data&&!r.data.length&&<p className="backup-empty">Резервных копий пока нет.</p>}
 </section>;
}
export function AdminOperations({request}:{request:Request}){return <><Disclosure title="Доступ сотрудников и передача анализов"><AccountOperations request={request}/></Disclosure><Disclosure title="Квоты хранения"><Limits request={request}/></Disclosure><Disclosure title="Резервные копии"><BackupPanel request={request}/></Disclosure><Disclosure title="Журнал действий"><Activity url="/api/admin/audit" request={request}/></Disclosure></>;}
export function StoragePanel({request}:{request:Request}) {
 const q=useResource('/api/quotas',request),r=useResource('/api/storage/candidates',request),jobs=useResource('/api/jobs',request),a=useAction();
 const [selected,setSelected]=useState<Row|null>(null),[confirmation,setConfirmation]=useState('');
 const label=(item:Row)=>{
   if(item.kind!=='job')return item.name;
   const job=jobs.data?.find((j:Row)=>j.id===item.id);
   const kind=({run:'Расчёт',check:'Проверка',ai:'Мнение ИИ',import:'Импорт'} as Row)[job?.kind]||'Задача';
   return job?`${kind} от ${new Date(job.created_at).toLocaleString('ru-RU')}`:'Файлы завершённой задачи';
 };
 return <><h1>Хранилище и очистка</h1><section className="panel operations-panel">
   <Issue text={q.error||r.error||a.error}/>
   {q.data&&<p>Анализов: {q.data.usage.runs} / {q.data.limits.max_runs} · Активных задач: {q.data.usage.active_jobs} / {q.data.limits.max_active_jobs} · Файлы: {fileSize(q.data.usage.storage_bytes)} / {q.data.limits.max_user_storage_mb} МБ</p>}
   <button className="text-button" onClick={()=>{q.reload();r.reload();jobs.reload();}}>Обновить список</button>
   {selected&&<form className="cleanup-confirm" onSubmit={e=>{e.preventDefault();if(confirmation!==selected.displayName)return;a.act(async()=>{
     await request('/api/storage/cleanup',json({kind:selected.kind,id:selected.id,confirmation:selected.name}));setSelected(null);setConfirmation('');r.reload();q.reload();
   });}}><h3>Удаление: {selected.displayName}</h3><p>{selected.note} Удаление нельзя отменить.</p>
     <label className="field">Для подтверждения введите: {selected.displayName}<input value={confirmation} onChange={e=>setConfirmation(e.target.value)} required/></label>
     <button className="button" disabled={a.busy||confirmation!==selected.displayName}>Удалить окончательно</button> <button type="button" className="text-button" onClick={()=>setSelected(null)}>Отмена</button>
   </form>}
   <div className="table-scroll"><table><thead><tr><th>Тип</th><th>Название</th><th>Размер файлов</th><th/></tr></thead><tbody>
     {r.data?.map((x:Row)=><tr key={x.kind+x.id}><td>{({run:'Архивный анализ',job:'Файлы задачи',dataset:'Неиспользуемый датасет'} as Row)[x.kind]}</td><td>{label(x)}{x.kind==='job'&&<small><a className="text-button" href={`?view=jobs&job=${encodeURIComponent(x.id)}`}>Открыть задачу</a></small>}</td><td>{fileSize(x.bytes)}</td><td><button className="text-button" onClick={()=>{setSelected({...x,displayName:label(x)});setConfirmation('');}}>Очистить</button></td></tr>)}
   </tbody></table></div>{r.data&&!r.data.length&&<p>Нет данных, доступных для очистки.</p>}
 </section></>;
}
