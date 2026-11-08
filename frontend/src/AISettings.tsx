import {useEffect,useId,useState} from 'react';
import {Loader2} from 'lucide-react';

type Settings={base_url:string;model:string;revision:number};
type Request=<T=any>(url:string,options?:RequestInit)=>Promise<T>;

export function AISettings({request}:{request:Request}){
 const [settings,setSettings]=useState<Settings|null>(null);
 const [url,setUrl]=useState(''),[model,setModel]=useState(''),[models,setModels]=useState<string[]>([]);
 const [busy,setBusy]=useState<'loading'|'saving'|'testing'|null>('loading');
 const [error,setError]=useState(''),[notice,setNotice]=useState(''),[warning,setWarning]=useState(false);
 const modelList=useId();
 const populate=(value:Settings)=>{setSettings(value);setUrl(value.base_url);setModel(value.model);setModels([]);};
 useEffect(()=>{let alive=true;setBusy('loading');request<Settings>('/api/admin/ai').then(value=>{if(alive)populate(value);}).catch(e=>{if(alive)setError(e.message);}).finally(()=>{if(alive)setBusy(null);});return()=>{alive=false;};},[request]);
 async function action(kind:'loading'|'saving'|'testing'){
  setBusy(kind);setError('');setNotice('');setWarning(false);
  try{
   if(kind==='loading'){populate(await request<Settings>('/api/admin/ai'));setNotice('Сохранённые настройки загружены.');}
   else if(kind==='saving'){
    const value=await request<Settings>('/api/admin/ai',{method:'PUT',body:JSON.stringify({base_url:url,model,revision:settings!.revision})});
    populate(value);setNotice('Настройки сохранены. Новые запросы используют модель '+value.model+'.');
   }else{
    setModels([]);
    const result=await request('/api/admin/ai/test',{method:'POST',body:JSON.stringify({base_url:url,model})});
    if(!result.connected)throw Error('Не удалось подключиться. '+(result.error||'Проверьте адрес Ollama.'));
    setModels(result.models||[]);setWarning(!result.available);
    setNotice(result.available?'Соединение установлено. Модель '+result.model+' найдена.':'Сервер доступен, но модель '+result.model+' не найдена. '+(result.models?.length?'Выберите установленную модель в поле «Модель».':'На сервере нет установленных моделей.'));
   }
  }catch(e){setError((e as Error).message);}finally{setBusy(null);}
 }
 const edited=()=>{setError('');setNotice('');};
 return <section className="panel admin-ai" aria-label="Подключение к ИИ" aria-busy={busy!==null}>
  <div className="panel-heading"><div><h2>Подключение к ИИ</h2><p>Сервер и модель для новых запросов всех пользователей.</p></div></div>
  {settings&&<>
   <label className="field">Адрес Ollama<input aria-label="Адрес Ollama" disabled={!!busy} type="url" maxLength={2048} value={url} onChange={e=>{setUrl(e.target.value);setModels([]);edited();}} placeholder="http://192.168.1.76:11434/api/chat"/></label>
   <label className="field">Модель<input aria-label="Модель Ollama" disabled={!!busy} list={modelList} maxLength={200} value={model} onChange={e=>{setModel(e.target.value);edited();}} placeholder="Имя модели в Ollama"/><datalist id={modelList}>{models.map(name=><option value={name} key={name}/>)}</datalist></label>
   <div className="job-actions"><button className="button" disabled={!!busy||!url.trim()||!model.trim()||(url===settings.base_url&&model===settings.model)} onClick={()=>action('saving')}>{busy==='saving'&&<Loader2 size={16} className="spin"/>}{busy==='saving'?'Сохранение…':'Сохранить настройки'}</button>
    <button className="button secondary" disabled={!!busy||!url.trim()||!model.trim()} onClick={()=>action('testing')}>{busy==='testing'&&<Loader2 size={16} className="spin"/>}{busy==='testing'?'Проверяем соединение…':'Проверить соединение'}</button>
    <button className="text-button" disabled={!!busy} onClick={()=>action('loading')}>Загрузить сохранённые настройки</button></div>
  </>}
  {busy==='loading'&&<p className="info-strip ai-connection-status" role="status">Загружаем настройки…</p>}
  {busy==='testing'&&<p className="info-strip ai-connection-status" role="status">Проверяем сервер и наличие модели…</p>}
  {error&&<p className="error-box" role="alert">{error}{!settings&&<button className="text-button" onClick={()=>action('loading')}>Повторить</button>}</p>}
  {notice&&<p className={`info-strip ai-connection-status ${warning?'is-warning':'is-success'}`} role="status">{notice}</p>}
 </section>;
}
