import {lazy,Suspense,type ComponentProps} from 'react';
import type {CodeEditor as EditorComponent} from './CodeEditorImpl';
export type {Request,Stage,Validation} from './CodeEditorImpl';
const Editor=lazy(()=>import('./CodeEditorImpl').then(module=>({default:module.CodeEditor})));
export function CodeEditor(props:ComponentProps<typeof EditorComponent>){
 return <Suspense fallback={<div role="status" style={{minHeight:240}}>Загрузка редактора…</div>}><Editor {...props}/></Suspense>;
}
