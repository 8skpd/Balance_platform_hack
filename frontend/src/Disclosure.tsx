import {useState,type ReactNode} from 'react';

/** Mount secondary tools on demand, then retain drafts when collapsed. */
export function Disclosure({title,children}:{title:string;children:ReactNode}) {
  const [mounted,setMounted]=useState(false);
  return <details className="disclosure" onToggle={e=>{if(e.currentTarget.open)setMounted(true);}}>
    <summary>{title}</summary>{mounted&&<div className="disclosure-body">{children}</div>}
  </details>;
}
