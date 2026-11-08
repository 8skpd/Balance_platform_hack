import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {ArrowDown,ArrowUp,Check,Copy,Loader2,RefreshCw,Sparkles,Square} from 'lucide-react';
import './ai-chat.css';
type Row=Record<string,any>;
type Request=<T=any>(path:string,options?:RequestInit)=>Promise<T>;
const activeStates=['queued','running','cancelling'];
const clock=(value:string)=>new Date(value).toLocaleString('ru-RU',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
function inline(text:string){return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part,i)=>part.startsWith('**')&&part.endsWith('**')?<strong key={i}>{part.slice(2,-2)}</strong>:part.startsWith('`')&&part.endsWith('`')?<code key={i}>{part.slice(1,-1)}</code>:part);}
function MessageBody({text}:{text:string}) {
 return <div className="chat-message-body">{text.split(/(```[\s\S]*?```)/g).map((part,i)=>{
  if(part.startsWith('```'))return <pre key={i}><code>{part.replace(/^```[^\n]*\n?/,'').replace(/```$/,'').trimEnd()}</code></pre>;
  return part.split(/\n\s*\n/).filter(Boolean).map((block,j)=>{
   const lines=block.split('\n');if(lines.every(l=>/^\s*[-*]\s/.test(l)))return <ul key={`${i}-${j}`}>{lines.map((l,k)=><li key={k}>{inline(l.replace(/^\s*[-*]\s/,''))}</li>)}</ul>;
   if(lines.every(l=>/^\s*\d+[.)]\s/.test(l)))return <ol key={`${i}-${j}`}>{lines.map((l,k)=><li key={k}>{inline(l.replace(/^\s*\d+[.)]\s/,''))}</li>)}</ol>;
   return <p key={`${i}-${j}`}>{lines.map((l,k)=><span key={k}>{k>0&&<br/>}{/^#{1,6}\s/.test(l)?<strong>{inline(l.replace(/^#{1,6}\s/,''))}</strong>:inline(l)}</span>)}</p>;
  });
 })}</div>;
}
function CopyAnswer({text}:{text:string}) {
 const [copied,setCopied]=useState(false),[error,setError]=useState('');
 useEffect(()=>{if(!copied)return;const timer=setTimeout(()=>setCopied(false),2000);return()=>clearTimeout(timer);},[copied]);
 return <><button className="chat-icon-button" aria-label={copied?'Ответ скопирован':'Скопировать ответ'} title={copied?'Скопировано':'Скопировать'} onClick={async()=>{try{await navigator.clipboard.writeText(text);setCopied(true);setError('');}catch{setError('Не удалось скопировать. Выделите текст ответа.');}}}>{copied?<Check size={14}/>:<Copy size={14}/>}</button>{error&&<span role="status">{error}</span>}</>;
}
function uid(){return typeof crypto.randomUUID==='function'?crypto.randomUUID():'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(Number(c)^crypto.getRandomValues(new Uint8Array(1))[0]&15>>Number(c)/4).toString(16));}
export function AIChat({run,request,refresh}:{run:Row;request:Request;refresh:()=>void}) {
 const draftKey=`balance-chat:${run.viewer.id}:${run.id}`;
 const [text,setText]=useState(()=>{try{return sessionStorage.getItem(draftKey)||'';}catch{return '';}}),[items,setItems]=useState<Row[]>([]),[history,setHistory]=useState<Row|null>(null),[model,setModel]=useState<Row|null>(null);
 const [loading,setLoading]=useState(true),[sending,setSending]=useState(false),[older,setOlder]=useState(false),[error,setError]=useState(''),[away,setAway]=useState(false);
 const mounted=useRef(true),sequence=useRef(0),nearBottom=useRef(true),pending=useRef<{message:string;id:string}|null>(null),sendingRef=useRef(false),scroll=useRef<HTMLDivElement>(null),input=useRef<HTMLTextAreaElement>(null),prepend=useRef<{height:number;top:number}|null>(null),cursor=useRef<string|null>(null),cursorInitialized=useRef(false);
 const merge=(rows:Row[])=>setItems(prev=>Array.from(new Map([...prev,...rows].map(r=>[r.id,r])).values()).sort((a,b)=>a.created_at.localeCompare(b.created_at)||a.id.localeCompare(b.id)));
 async function load(){const ticket=++sequence.current;try{const data=await request(`/api/runs/${run.id}/ai-chat`);if(!mounted.current||ticket!==sequence.current)return;merge(data.items);setHistory(data);if(!cursorInitialized.current){cursor.current=data.next_before;cursorInitialized.current=true;}setError('');}catch(e){if(mounted.current&&ticket===sequence.current){setError((e as Error).message);if([401,403,404].includes((e as any).status)){setItems([]);setHistory(null);}}}finally{if(mounted.current&&ticket===sequence.current)setLoading(false);}}
 useEffect(()=>{mounted.current=true;void load();const timer=setInterval(load,3000);if(run.can_manage)request('/api/ollama').then(r=>mounted.current&&setModel(r)).catch(()=>{});return()=>{mounted.current=false;++sequence.current;clearInterval(timer);};},[run.id,request]);
 useEffect(()=>{try{if(text)sessionStorage.setItem(draftKey,text);else sessionStorage.removeItem(draftKey);}catch{}},[text,draftKey]);
 const active=history?.active||(activeStates.includes(run.ai?.status)?{id:run.ai.job_id,status:run.ai.status}:null);
 const canSend=!!history?.can_send&&run.can_manage;
 useLayoutEffect(()=>{const field=input.current;if(!field)return;const resize=()=>{field.style.height='auto';field.style.height=Math.min(160,field.scrollHeight)+'px';};resize();let width=0;const observer=new ResizeObserver(entries=>{const next=entries[0].contentRect.width;if(next!==width){width=next;resize();}});observer.observe(field.parentElement!);return()=>observer.disconnect();},[text,canSend]);
 const bottom=()=>{const el=scroll.current;if(el)el.scrollTop=el.scrollHeight;nearBottom.current=true;setAway(false);};
 const signature=items.map(r=>`${r.id}:${r.status}:${r.content?.length||0}`).join('|')+(history?.legacy_answer?.content||'');
 useLayoutEffect(()=>{const el=scroll.current;if(!el)return;if(prepend.current){el.scrollTop=prepend.current.top+el.scrollHeight-prepend.current.height;prepend.current=null;}else if(nearBottom.current)bottom();},[signature]);
 async function loadOlder(){if(!cursor.current||older)return;setOlder(true);try{const data=await request(`/api/runs/${run.id}/ai-chat?before=${encodeURIComponent(cursor.current)}`);if(!mounted.current)return;const el=scroll.current;if(el)prepend.current={height:el.scrollHeight,top:el.scrollTop};merge(data.items);cursor.current=data.next_before;}catch(e){setError((e as Error).message);}finally{if(mounted.current)setOlder(false);}}
 async function send(message=text,retry=false){const value=message.trim();if(!value||value.length>4000||sendingRef.current||active||!canSend)return;sendingRef.current=true;setSending(true);setError('');if(retry||pending.current?.message!==value)pending.current={message:value,id:uid()};
  try{await request(`/api/runs/${run.id}/ai-chat`,{method:'POST',body:JSON.stringify({message:value,request_id:pending.current!.id})});if(!mounted.current)return;pending.current=null;if(message===text)setText('');nearBottom.current=true;await load();refresh();input.current?.focus();}catch(e){if(mounted.current)setError((e as Error).message);}finally{sendingRef.current=false;if(mounted.current)setSending(false);}}
 async function stop(){if(!active?.id)return;try{await request(`/api/jobs/${active.id}/cancel`,{method:'POST'});await load();refresh();}catch(e){setError((e as Error).message);}}
 const hasMessages=items.length>0||history?.legacy_answer;
 return <section className="ai-chat" aria-label="Чат с ИИ">
  <header className="chat-header"><span className="chat-avatar"><Sparkles size={21}/></span><div><h2>Помощник по анализу</h2><p>{active?active.status==='queued'?'Запрос в очереди':active.status==='cancelling'?'Останавливаем ответ…':'Готовит ответ…':model?.available===false?'Модель сейчас недоступна':'По данным текущего отчёта'}</p></div>{active&&<Loader2 size={18} className="spin"/>}</header>
  <div className="chat-scroll" ref={scroll} aria-label="Переписка" onScroll={()=>{const el=scroll.current!;nearBottom.current=el.scrollHeight-el.scrollTop-el.clientHeight<90;setAway(!nearBottom.current);}}>
   {cursor.current&&<button className="chat-history-button" disabled={older} onClick={loadOlder}>{older?'Загружаем…':'Показать предыдущие сообщения'}</button>}
   {loading&&!hasMessages?<div className="chat-empty"><Loader2 className="spin" size={24}/><p>Загружаем переписку…</p></div>:!hasMessages?<div className="chat-empty"><span className="chat-empty-icon"><Sparkles size={30}/></span><h3>Разберём результаты вместе</h3><p>Задайте вопрос об изменениях, решениях или результатах проверок.</p>{canSend&&<div className="chat-suggestions">{['Что изменилось в стратегии?','Как изменилась доля одобрений?','Какие проверки требуют внимания?'].map(q=><button key={q} disabled={sending||!!active} onClick={()=>send(q)}>{q}<ArrowUp size={14}/></button>)}</div>}</div>:null}
   {history?.legacy_answer&&!items.length&&<article className="chat-message assistant"><span className="chat-avatar"><Sparkles size={17}/></span><div className="chat-message-main"><div className="chat-bubble"><MessageBody text={history.legacy_answer.content}/></div><div className="chat-message-meta">Сохранённое мнение ИИ<CopyAnswer text={history.legacy_answer.content}/></div></div></article>}
   {items.map(item=><div key={item.id} className="chat-turn">
    {item.message&&<article className="chat-message user"><div className="chat-message-main"><div className="chat-bubble"><p>{item.message}</p></div><div className="chat-message-meta">{item.requested_by===run.viewer.id?'Вы':'Автор анализа'} · {clock(item.created_at)}</div></div></article>}
    <article className="chat-message assistant"><span className="chat-avatar"><Sparkles size={17}/></span><div className="chat-message-main">
     {item.status==='completed'&&item.content?<><div className="chat-bubble"><MessageBody text={item.content}/>{item.truncated&&<p className="chat-answer-note">Ответ достиг ограничения длины. Можно попросить продолжить.</p>}</div><div className="chat-message-meta"><time dateTime={item.finished_at||item.created_at}>{clock(item.finished_at||item.created_at)}</time><span title={item.model}>ИИ</span><CopyAnswer text={item.content}/></div>{item.stale&&<p className="chat-answer-note">Ответ по предыдущей версии отчёта</p>}</>:activeStates.includes(item.status)?<div className="chat-bubble chat-working" role="status"><Loader2 size={16} className="spin"/>{item.status==='queued'?'Ожидает в очереди…':item.status==='cancelling'?'Останавливаем ответ…':'Анализирую данные…'}</div>:<div className="chat-bubble chat-failed"><p>{item.status==='cancelled'?'Ответ остановлен':item.status==='timed_out'?'Модель не успела ответить':'Не удалось получить ответ'}</p>{canSend&&<button className="text-button" disabled={sending||!!active} onClick={()=>send(item.message||'Проанализируй результаты отчёта.',true)}><RefreshCw size={13}/>Повторить</button>}{item.error&&<details><summary>Подробности</summary><pre>{item.error}</pre></details>}</div>}
    </div></article>
   </div>)}
  </div>
  {away&&<button className="chat-to-bottom" aria-label="К последнему сообщению" onClick={bottom}><ArrowDown size={17}/></button>}
  <footer className="chat-footer">{error&&<div className="chat-error" role="alert">{error}<button className="text-button" onClick={load}>Обновить</button></div>}
   {canSend?<form className="chat-composer" onSubmit={e=>{e.preventDefault();void send();}}><textarea ref={input} aria-label="Сообщение ИИ" placeholder="Спросите о результатах анализа…" value={text} rows={1} maxLength={4000} onChange={e=>setText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/>{active?<button type="button" className="chat-send stop" aria-label="Остановить ответ" disabled={active.status==='cancelling'} onClick={stop}><Square size={16}/></button>:<button type="submit" className="chat-send" aria-label="Отправить сообщение" disabled={sending||!text.trim()}>{sending?<Loader2 size={18} className="spin"/>:<ArrowUp size={20}/>}</button>}</form>:!loading&&<p className="chat-readonly">{run.archived?'Анализ в архиве. Доступен просмотр переписки.':'Переписка доступна для просмотра. Вопросы задаёт владелец анализа.'}</p>}
   {canSend&&<div className="chat-composer-hint"><span>Enter — отправить · Shift+Enter — новая строка</span>{text.length>3500&&<span>{text.length}/4000</span>}</div>}
  </footer>
 </section>;
}
