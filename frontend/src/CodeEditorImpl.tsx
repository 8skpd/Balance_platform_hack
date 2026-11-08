import {useEffect,useRef,useState} from 'react';
import {basicSetup} from 'codemirror';
import {EditorState,Compartment} from '@codemirror/state';
import {EditorView,keymap} from '@codemirror/view';
import {indentWithTab} from '@codemirror/commands';
import {python} from '@codemirror/lang-python';
import {autocompletion} from '@codemirror/autocomplete';
import {setDiagnostics,lintGutter} from '@codemirror/lint';
import './code-editor.css';

export type Stage='normalization'|'comparison'|'aggregation'|'checks';
type Fix={from:number;to:number;insert:string;expected:string;title:string;preview:string};
export type Validation={valid:boolean;scope:string;diagnostics:{line:number;column:number;message:string;severity:'error'|'warning';fix?:Fix}[]};
export type Request=<T=any>(url:string,options?:RequestInit)=>Promise<T>;

export function CodeEditor({value,onChange,stage,request,label='Код Python',disabled=false,onValidation}:{value:string;onChange:(code:string)=>void;stage:Stage;request:Request;label?:string;disabled?:boolean;onValidation?:(result:Validation|null)=>void}){
 const host=useRef<HTMLDivElement>(null),view=useRef<EditorView|null>(null),editable=useRef(new Compartment());
 const latest=useRef({onChange,onValidation});latest.current={onChange,onValidation};
 const checkedSource=useRef('');
 const [validation,setValidation]=useState<Validation|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{
  const editor=new EditorView({parent:host.current!,state:EditorState.create({doc:value,extensions:[
   basicSetup,python(),keymap.of([indentWithTab]),lintGutter(),
   autocompletion({override:[context=>{const word=context.matchBefore(/[\w.]+/);if(!word&&!context.explicit)return null;return {from:word?.from??context.pos,options:[
    ...['PROCEDURE','CHECK','APP_ID','SOURCE','PATH','ARRAY_KEY','CNT','APP_IDS','RESULT','PASS','FAIL'].map(label=>({label,type:'constant'})),
    ...['defaults.normalize','defaults.segments','data.normalized','data.segments','data.default_comparison','data.default_facts','data.comparison','mart.app_ids','mart.rows','mart.total_apps'].map(label=>({label,type:'function'}))]};}]}),
   EditorView.contentAttributes.of({'aria-label':label,'aria-multiline':'true'}),
   editable.current.of([EditorView.editable.of(!disabled),EditorState.readOnly.of(disabled)]),
   EditorView.updateListener.of(update=>{if(update.docChanged)latest.current.onChange(update.state.doc.toString());}),
   EditorView.theme({'&':{fontSize:'13px'},'.cm-content':{fontFamily:'ui-monospace, SFMono-Regular, Consolas, monospace'},'.cm-scroller':{overflow:'auto'},'&.cm-focused':{outline:'none'}}),
  ]})});view.current=editor;return()=>{view.current=null;editor.destroy();};
 },[]);
 useEffect(()=>{const editor=view.current;if(editor&&editor.state.doc.toString()!==value)editor.dispatch({changes:{from:0,to:editor.state.doc.length,insert:value}});},[value]);
 useEffect(()=>{view.current?.dispatch({effects:editable.current.reconfigure([EditorView.editable.of(!disabled),EditorState.readOnly.of(disabled)])});},[disabled]);
 useEffect(()=>{
  let cancelled=false;setValidation(null);setError('');latest.current.onValidation?.(null);
  if(view.current)view.current.dispatch(setDiagnostics(view.current.state,[]));
  const timer=setTimeout(()=>{request<Validation>('/api/code/validate',{method:'POST',body:JSON.stringify({stage,code:value})}).then(result=>{
   if(cancelled)return;checkedSource.current=value;setValidation(result);latest.current.onValidation?.(result);
   const editor=view.current;if(!editor)return;
   editor.dispatch(setDiagnostics(editor.state,result.diagnostics.map(d=>{const line=editor.state.doc.line(Math.min(Math.max(1,d.line),editor.state.doc.lines));const from=Math.min(line.to,line.from+Math.max(0,d.column-1));return {from,to:Math.min(line.to,from+1),severity:d.severity,message:d.message};})));
  }).catch(e=>{if(!cancelled)setError(e.message);});},450);
  return()=>{cancelled=true;clearTimeout(timer);};
 },[value,stage,request,attempt]);
 const jump=(line:number)=>{const editor=view.current;if(!editor)return;const pos=editor.state.doc.line(Math.min(Math.max(1,line),editor.state.doc.lines)).from;editor.dispatch({selection:{anchor:pos},effects:EditorView.scrollIntoView(pos,{y:'center'})});editor.focus();};
 const fix=(change:Fix)=>{const editor=view.current;if(!editor||disabled||editor.state.readOnly||editor.state.doc.toString()!==checkedSource.current)return;
  if(change.from<0||change.to<change.from||change.to>editor.state.doc.length||editor.state.doc.sliceString(change.from,change.to)!==change.expected)return;
  editor.dispatch({changes:{from:change.from,to:change.to,insert:change.insert},selection:{anchor:change.from+change.insert.length},userEvent:'input.contract-fix'});editor.focus();
 };
 const selectAll=()=>{const editor=view.current;if(!editor)return;editor.dispatch({selection:{anchor:0,head:editor.state.doc.length}});editor.focus();};
 return <div className="python-workspace">
  <div className="code-toolbar"><span>Python</span><div className="code-toolbar-actions"><span>Tab — отступ · Ctrl+F — поиск</span><button type="button" className="text-button" onClick={selectAll}>Выделить всё</button></div></div>
  <div ref={host} className="cm-host"/>
  <div className={`code-validation ${validation?.valid?'valid':''}`} role="status">{error?<>{error} <button type="button" className="text-button" onClick={()=>setAttempt(n=>n+1)}>Повторить проверку</button></>:!validation?'Проверяем контракт…':validation.valid?validation.diagnostics.length?'Структура проверена · есть замечания':'Структура контракта проверена':'Исправьте ошибки контракта'}</div>
  {!!validation?.diagnostics.length&&<ul className="code-diagnostics">{validation.diagnostics.map((d,i)=><li key={i} className={d.severity}><button type="button" onClick={()=>jump(d.line)}>Строка {d.line}: {d.message}</button>{d.fix&&!disabled&&checkedSource.current===value&&<div className="code-quick-fix"><code>{d.fix.preview}</code><button type="button" className="button secondary" onClick={()=>fix(d.fix!)}>{d.fix.title}</button></div>}</li>)}</ul>}
 </div>;
}
