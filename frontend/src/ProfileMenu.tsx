import {useEffect,useRef,useState} from 'react';
import {ChevronDown,LogOut,Settings2,X,Loader2} from 'lucide-react';
import type {User} from './Auth';
import {roleLabels} from './Access';
import './profile-menu.css';
type Request=<T=any>(url:string,options?:RequestInit)=>Promise<T>;
export function ProfileMenu({user,request,logout}:{user:User;request:Request;logout:()=>Promise<void>}) {
 const [open,setOpen]=useState(false),[settings,setSettings]=useState(false),[error,setError]=useState(''),[leaving,setLeaving]=useState(false);
 const root=useRef<HTMLDivElement>(null),trigger=useRef<HTMLButtonElement>(null),menu=useRef<HTMLDivElement>(null);
 const close=()=>{setOpen(false);trigger.current?.focus();};
 useEffect(()=>{if(!open)return;menu.current?.querySelector<HTMLButtonElement>('button')?.focus();const outside=(e:PointerEvent)=>{if(!root.current?.contains(e.target as Node))setOpen(false);};document.addEventListener('pointerdown',outside);return()=>document.removeEventListener('pointerdown',outside);},[open]);
 return <div className="account-menu" ref={root}>
  <button ref={trigger} type="button" className="account-trigger" aria-label="Меню профиля" aria-expanded={open} aria-haspopup="menu" onClick={()=>setOpen(!open)} onKeyDown={e=>{if(e.key==='ArrowDown'){e.preventDefault();setOpen(true);}}}><span className="account-avatar">{user.display_name.slice(0,2).toUpperCase()}</span><ChevronDown size={14}/></button>
  {open&&<div className="account-popover" role="menu" aria-label="Профиль" ref={menu} onKeyDown={e=>{if(e.key==='Escape'){e.preventDefault();close();}if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const buttons=Array.from(menu.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'));const i=buttons.indexOf(document.activeElement as HTMLButtonElement);buttons[e.key==='Home'?0:e.key==='End'?buttons.length-1:(i+(e.key==='ArrowDown'?1:-1)+buttons.length)%buttons.length]?.focus();}}}>
   <div className="account-identity"><strong>{user.display_name}</strong><span>@{user.username}</span><small>{roleLabels[user.role]}</small></div>
   <button role="menuitem" onClick={()=>{setOpen(false);setSettings(true);}}><Settings2 size={17}/>Настройки профиля</button>
   <button role="menuitem" disabled={leaving} onClick={async()=>{setLeaving(true);setError('');try{await logout();setOpen(false);}catch(e){setError((e as Error).message);}finally{setLeaving(false);}}}>{leaving?<Loader2 size={17} className="spin"/>:<LogOut size={17}/>}Выйти</button>{error&&<p className="profile-error" role="alert">{error}</p>}
  </div>}
  {settings&&<ProfileSettings user={user} request={request} close={()=>{setSettings(false);requestAnimationFrame(()=>trigger.current?.focus());}}/>}
 </div>;
}
function ProfileSettings({user,request,close}:{user:User;request:Request;close:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null),[name,setName]=useState(user.display_name),[savedName,setSavedName]=useState(user.display_name),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
 const [oldPassword,setOldPassword]=useState(''),[newPassword,setNewPassword]=useState(''),[repeat,setRepeat]=useState(''),[passwordError,setPasswordError]=useState(''),[passwordNotice,setPasswordNotice]=useState(''),[changing,setChanging]=useState(false);
 useEffect(()=>{const el=dialog.current!;el.showModal();return()=>el.close();},[]);
 const sync=()=>{window.dispatchEvent(new Event('balance-profile-changed'));try{localStorage.setItem('balance-auth-event',String(Date.now()));}catch{}};
 return <dialog ref={dialog} className="profile-dialog" aria-labelledby="profile-title" onCancel={e=>{e.preventDefault();close();}}>
  <header><h2 id="profile-title">Настройки профиля</h2><button className="icon-button" type="button" aria-label="Закрыть настройки профиля" onClick={close}><X size={20}/></button></header>
  <div className="profile-account"><span className="account-avatar">{user.display_name.slice(0,2).toUpperCase()}</span><div><strong>@{user.username}</strong><small>{roleLabels[user.role]}</small></div></div>
  <form onSubmit={async e=>{e.preventDefault();setBusy(true);setError('');setNotice('');try{const next=await request('/api/auth/profile',{method:'PATCH',body:JSON.stringify({display_name:name.trim(),expected_display_name:savedName})});setName(next.display_name);setSavedName(next.display_name);setNotice('Имя сохранено');sync();}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>
   <label className="field">Отображаемое имя<input autoComplete="name" value={name} maxLength={80} required disabled={busy} onChange={e=>{setName(e.target.value);setNotice('');}}/></label>
   {error&&<p className="profile-error" role="alert">{error}</p>}{notice&&<p className="profile-success" role="status">{notice}</p>}
   <button type="submit" className="button" disabled={busy||!name.trim()||name.trim()===savedName}>{busy?'Сохраняем…':'Сохранить имя'}</button>
  </form>
  <details className="profile-password"><summary>Изменить пароль</summary><form onSubmit={async e=>{e.preventDefault();setPasswordError('');setPasswordNotice('');if(newPassword!==repeat){setPasswordError('Новые пароли не совпадают');return;}setChanging(true);try{await request('/api/auth/password',{method:'POST',body:JSON.stringify({current_password:oldPassword,new_password:newPassword})});setOldPassword('');setNewPassword('');setRepeat('');setPasswordNotice('Пароль изменён. Другие сеансы завершены.');sync();}catch(e){setPasswordError((e as Error).message);}finally{setChanging(false);}}}>
   <label className="field">Текущий пароль<input type="password" autoComplete="current-password" required maxLength={128} value={oldPassword} onChange={e=>setOldPassword(e.target.value)}/></label>
   <label className="field">Новый пароль<input type="password" autoComplete="new-password" minLength={10} maxLength={128} required value={newPassword} onChange={e=>setNewPassword(e.target.value)}/></label>
   <label className="field">Повторите новый пароль<input type="password" autoComplete="new-password" minLength={10} maxLength={128} required value={repeat} onChange={e=>setRepeat(e.target.value)}/></label>
   <p className="profile-password-hint">От 10 символов. После смены пароля другие сеансы будут завершены.</p>
   {passwordError&&<p className="profile-error" role="alert">{passwordError}</p>}{passwordNotice&&<p className="profile-success" role="status">{passwordNotice}</p>}
   <button className="button secondary" disabled={changing}>{changing?'Меняем…':'Изменить пароль'}</button>
  </form></details>
 </dialog>;
}
