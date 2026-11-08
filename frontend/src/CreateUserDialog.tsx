import {useEffect,useRef,useState} from 'react';
import {Loader2,X} from 'lucide-react';
import type {User} from './Auth';

type Request=<T=any>(url:string,options?:RequestInit)=>Promise<T>;
export function CreateUserDialog({request,close,created}:{request:Request;close:()=>void;created:(user:User)=>void}){
 const dialog=useRef<HTMLDialogElement>(null);
 const [name,setName]=useState(''),[username,setUsername]=useState(''),[role,setRole]=useState<User['role']>('user');
 const [password,setPassword]=useState(''),[confirmation,setConfirmation]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
 useEffect(()=>{dialog.current!.showModal();return()=>dialog.current?.close();},[]);
 async function submit(e:React.FormEvent){
  e.preventDefault();setError('');
  if(password!==confirmation){setError('Пароли не совпадают');return;}
  setBusy(true);
  try{const user=await request<User>('/api/admin/users',{method:'POST',body:JSON.stringify({username,display_name:name,password,role})});setPassword('');setConfirmation('');created(user);}
  catch(e){setError((e as Error).message);}finally{setBusy(false);}
 }
 return <dialog ref={dialog} className="create-user-dialog" aria-labelledby="create-user-title" onCancel={e=>{e.preventDefault();if(!busy)close();}}>
  <form onSubmit={submit}>
   <header className="modal-header"><h2 id="create-user-title">Новый пользователь</h2><button type="button" className="icon-button" aria-label="Закрыть создание пользователя" disabled={busy} onClick={close}><X size={20}/></button></header>
   <div className="create-user-body"><div className="create-user-fields">
    <label className="field">Имя<input autoFocus required maxLength={80} autoComplete="off" value={name} disabled={busy} onChange={e=>setName(e.target.value)}/></label>
    <label className="field">Логин<input required minLength={3} maxLength={40} pattern="[a-zA-Z0-9_.\-]{3,40}" autoComplete="off" value={username} disabled={busy} onChange={e=>setUsername(e.target.value)}/></label>
    <label className="field create-user-role">Роль<select aria-label="Роль" value={role} disabled={busy} onChange={e=>setRole(e.target.value as User['role'])}><option value="user">Пользователь</option><option value="observer">Наблюдатель</option><option value="admin">Администратор</option></select></label>
    <label className="field">Пароль<input type="password" required minLength={10} maxLength={128} autoComplete="new-password" placeholder="Не менее 10 символов" value={password} disabled={busy} onChange={e=>setPassword(e.target.value)}/></label>
    <label className="field">Повторите пароль<input type="password" required minLength={10} maxLength={128} autoComplete="new-password" value={confirmation} disabled={busy} onChange={e=>setConfirmation(e.target.value)}/></label>
   </div>{error&&<div className="error-box" role="alert">{error}</div>}</div>
   <footer className="modal-footer"><button type="button" className="button secondary" disabled={busy} onClick={close}>Отмена</button><button type="submit" className="button" disabled={busy||!name.trim()||!username.trim()}>{busy&&<Loader2 size={16} className="spin"/>}{busy?'Создаём…':'Создать пользователя'}</button></footer>
  </form>
 </dialog>;
}
