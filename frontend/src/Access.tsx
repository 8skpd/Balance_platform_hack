import {Disclosure} from './Disclosure';
import {AISettings} from './AISettings';
import {CreateUserDialog} from './CreateUserDialog';
import {Plus} from 'lucide-react';
import {TransferForm} from './Workflow';
import { useEffect, useRef, useState } from 'react';
import {AdminOperations} from './Operations';
import type { User } from './Auth';
type Row = Record<string, any>;
type Request = <T = any>(url: string, options?: RequestInit) => Promise<T>;
export const roleLabels = {admin:'Администратор', user:'Пользователь', observer:'Наблюдатель'};

export function Sharing({runId, request, expanded=false}: {runId:string; request:Request; expanded?:boolean}) {
  const [open,setOpen]=useState(expanded), [members,setMembers]=useState<Row[]>([]);
  const [username,setUsername]=useState(''), [busy,setBusy]=useState(false), [error,setError]=useState(''), [notice,setNotice]=useState('');
  const url=`/api/runs/${runId}/shares`;
  useEffect(()=>{let active=true;if(open)request<Row[]>(url).then(v=>{if(active)setMembers(v);}).catch(e=>{if(active)setError(e.message);});return()=>{active=false;};},[open,url,request]);
  async function change(id?:string) {
    setBusy(true);setError('');setNotice('');
    try {
      await request(url+(id?'/'+id:''),{method:id?'DELETE':'POST',...(id?{}:{body:JSON.stringify({username})})});
      setMembers(await request(url));setUsername('');setNotice(id?'Доступ отозван.':'Доступ к просмотру предоставлен.');
    } catch(e){setError((e as Error).message);} finally{setBusy(false);}
  }
  return <section className="panel access-panel">{!expanded&&<button className="button secondary" aria-expanded={open} onClick={()=>setOpen(!open)}>Совместный доступ</button>}{open&&<>
    <h3>Просмотр анализа</h3><p className="muted">Доступ по логину: просмотр отчёта и исходных данных. Пользователи могут обсуждать результат; наблюдатели — только читать.</p>
    <form className="access-form" onSubmit={e=>{e.preventDefault();change();}}><label className="field">Логин получателя<input aria-label="Логин получателя" value={username} onChange={e=>setUsername(e.target.value)} minLength={3} maxLength={40} required placeholder="Логин существующего аккаунта"/></label><button className="button" disabled={busy||!username.trim()}>Предоставить просмотр</button></form>
    {error&&<p className="error-box" role="alert">{error}</p>}{notice&&<p className="info-strip" role="status">{notice}</p>}
    {!members.length?<p className="muted">Доступ пока никому не предоставлен.</p>:<ul className="access-members">{members.map(m=><li key={m.id}><span>{m.display_name} <small>@{m.username} · {m.role==='observer'?'Только просмотр':'Просмотр и обсуждение'}</small></span><button className="text-button" disabled={busy} aria-label={'Отозвать доступ '+m.username} onClick={()=>change(m.id)}>Отозвать доступ</button></li>)}</ul>}
    <label className="field">Ссылка на отчёт<input aria-label="Ссылка на отчёт" readOnly value={`${location.origin}/?run=${encodeURIComponent(runId)}`} onFocus={e=>e.currentTarget.select()}/></label><p className="muted">По ссылке нужно войти в аккаунт, которому предоставлен доступ.</p>
  </>}</section>;
}

export function Administration({user,request}:{user:User;request:Request}) {
  const [creating,setCreating]=useState(false);
  const [users,setUsers]=useState<User[]>([]), [roles,setRoles]=useState<Record<string,string>>({});
  const [error,setError]=useState(''), [notice,setNotice]=useState(''), [busy,setBusy]=useState(false);
  async function loadUsers(){const rows=await request<User[]>('/api/admin/users');setUsers(rows);setRoles(Object.fromEntries(rows.map(u=>[u.id,u.role])));}
  useEffect(()=>{loadUsers().catch(e=>setError(e.message));},[request]);
  async function action(fn:()=>Promise<void>) {setBusy(true);setError('');setNotice('');try{await fn();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  const saveRole=(target:User)=>action(async()=>{
    await request(`/api/admin/users/${target.id}/role`,{method:'PUT',body:JSON.stringify({role:roles[target.id],expected_role:target.role})});
    if(target.id===user.id)window.dispatchEvent(new Event('balance-role-changed'));
    else await loadUsers();
    setNotice('Роль изменена. Права действуют сразу, интерфейс пользователя обновится автоматически.');
  });
  return <><div className="page-heading"><div><h1>Администрирование</h1></div></div>
    {error&&<p className="error-box" role="alert">{error}</p>}{notice&&<p className="info-strip" role="status">{notice}</p>}
    <section className="panel admin-users"><div className="panel-heading"><div><h2>Пользователи и роли</h2></div><div className="job-actions"><button className="button secondary" disabled={busy} onClick={()=>{setNotice('');setError('');setCreating(true);}}><Plus size={16}/>Новый пользователь</button><button className="text-button" disabled={busy} onClick={()=>action(loadUsers)}>Обновить список</button></div></div>
    <p className="info-strip">Чужие отчёты доступны только после приглашения владельца, в том числе администраторам. Последнего администратора нельзя понизить.</p>
    <div className="table-scroll"><table><thead><tr><th>Пользователь</th><th>Роль</th><th/></tr></thead><tbody>{users.map(u=><tr key={u.id}><td>{u.display_name}<small>@{u.username}{u.id===user.id?' · Вы':''}</small></td><td><select aria-label={'Роль '+u.username} disabled={busy} value={roles[u.id]||u.role} onChange={e=>setRoles({...roles,[u.id]:e.target.value})}>{Object.entries(roleLabels).map(([v,label])=><option value={v} key={v}>{label}</option>)}</select></td><td><button className="button secondary" aria-label={'Сохранить роль '+u.username} disabled={busy||roles[u.id]===u.role} onClick={()=>saveRole(u)}>Сохранить роль</button></td></tr>)}</tbody></table></div></section>
    {creating&&<CreateUserDialog request={request} close={()=>setCreating(false)} created={account=>{setCreating(false);setNotice('Пользователь '+account.username+' создан.');loadUsers().catch(e=>setError(e.message));}}/>}
    <Disclosure title="Подключение к ИИ"><AISettings request={request}/></Disclosure><AdminOperations request={request}/></>;
}

export function ReportAccess({run,request,refresh}:{run:Row;request:Request;refresh:()=>void}) {
  const [open,setOpen]=useState(false);const trigger=useRef<HTMLButtonElement>(null);
  const close=()=>{setOpen(false);requestAnimationFrame(()=>trigger.current?.focus({preventScroll:true}));};
  return <><button ref={trigger} className="button secondary" onClick={()=>setOpen(true)}>Доступ</button>{open&&<AccessDialog run={run} request={request} close={close} done={()=>{close();refresh();}}/>}</>;
}
function AccessDialog({run,request,close,done}:{run:Row;request:Request;close:()=>void;done:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const el=dialog.current!;const overflow=document.body.style.overflow;document.body.style.overflow='hidden';el.showModal();return()=>{el.close();document.body.style.overflow=overflow;};},[]);
  return <dialog ref={dialog} className="access-dialog" aria-labelledby="report-access-title" onCancel={e=>{e.preventDefault();close();}} onClick={e=>{if(e.target===e.currentTarget)close();}}>
    <div className="modal-header"><h2 id="report-access-title">Доступ к анализу</h2><button className="text-button" onClick={close} aria-label="Закрыть доступ">Закрыть</button></div>
    {run.status==='completed'?<Sharing runId={run.id} request={request} expanded/>:<p className="muted">Совместный просмотр доступен после завершения расчёта.</p>}
    <div className="access-transfer"><TransferForm run={run} request={request} done={done}/></div>
  </dialog>;
}
