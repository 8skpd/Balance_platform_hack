import './editor-layout.css';
import {useRef} from 'react';
import {Upload} from 'lucide-react';
export function PythonUpload({label,disabled=false,onFile}:{label:string;disabled?:boolean;onFile:(file:File)=>void}){
 const input=useRef<HTMLInputElement>(null);
 return <><button type="button" className="button secondary python-upload" disabled={disabled} onClick={()=>input.current?.click()}><Upload size={16}/>Загрузить .py</button><input hidden ref={input} type="file" aria-label={label} accept=".py" disabled={disabled} onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)onFile(file);}}/></>;
}
