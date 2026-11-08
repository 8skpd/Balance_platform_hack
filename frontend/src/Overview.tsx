import {ArrowRight, ArrowUpRight, CheckCircle2, Layers3} from 'lucide-react';
import './vendor/bem-components.scoped.css';
import './overview.css';
type Row = Record<string, any>;
const count = (value:number) => new Intl.NumberFormat('ru-RU').format(value);
const decimal = (value:number) => new Intl.NumberFormat('ru-RU',{minimumFractionDigits:1,maximumFractionDigits:1}).format(value);
const signed = (value:number) => `${value>0?'+':value<0?'−':''}${decimal(Math.abs(value))}`;
const share = (part:number,total:number) => total>0?100*part/total:0;
const decision = (value:string|null) => ({APPROVE:'Одобрение',DECLINE:'Отказ',REJECT:'Отказ',REVIEW:'На проверку'}[value||'']||value||'Нет решения');

function DecisionBadge({value}:{value:string|null}) {
  const tone=value==='APPROVE'?'approved':['DECLINE','REJECT'].includes(value||'')?'declined':value==='REVIEW'?'review':'neutral';
  return <span className={`decision ${tone}`}>{decision(value)}</span>;
}

// Legacy reports already retain transition counts, so they need no recalculation.
export function approvalMetrics(summary:Row) {
  const transitions:Row[]=summary.transitions||[];
  const before=summary.approval?.before_count??transitions.reduce((n,r)=>n+(r.DECISION_BEFORE==='APPROVE'?r.CNT:0),0);
  const after=summary.approval?.after_count??transitions.reduce((n,r)=>n+(r.DECISION_AFTER==='APPROVE'?r.CNT:0),0);
  const total=summary.total_apps||0;
  return {before,after,beforeRate:share(before,total),afterRate:share(after,total),points:share(after-before,total),relative:before>0?100*(after-before)/before:null,available:total>0};
}
function Action({children,onClick}:{children:React.ReactNode;onClick:()=>void}) {
  return <button className="button button_theme_islands button_size_m infographic-action" onClick={onClick}><span className="button__text">{children}</span></button>;
}
export function RunOverview({run,open}:{run:Row;open:(tab:string,extra?:Record<string,string>)=>void}) {
  const s=run.summary,a=approvalMetrics(s),paths:Row[]=s.top_paths||[],transitions:Row[]=s.transitions||[];
  const metrics=[
    {label:'Всего заявок',value:s.total_apps,detail:'В двух версиях стратегии',action:()=>open('applications')},
    {label:'С различиями',value:s.changed_apps,detail:`${decimal(share(s.changed_apps,s.total_apps))}% от всех заявок`,action:()=>open('applications',{changed:'1'})},
    {label:'Сменили решение',value:s.decision_changed,detail:`${decimal(share(s.decision_changed,s.total_apps))}% от всех заявок`,action:()=>open('applications',{decision:'1'})},
    {label:'Изменённых атрибутов',value:s.changed_attributes,detail:'Перейти в сводную',action:()=>open('pivot')},
  ];
  return <div className="run-infographic">
    {!!s.empty_apps&&<p className="info-strip">Заявок без сравниваемых атрибутов: {count(s.empty_apps)}. Они учтены в общем количестве.</p>}
    <div className="infographic-grid">
      <section className="infographic-card infographic-totals" aria-label="Итоги анализа">
        {metrics.map(m=><button key={m.label} className="infographic-metric" onClick={m.action}><span>{m.label}<ArrowUpRight size={15}/></span><strong>{count(m.value??0)}</strong><small>{m.detail}</small></button>)}
      </section>
      <section className="infographic-card approval-card" aria-labelledby="approval-title">
        <div className="approval-heading"><h2 id="approval-title">Доля одобренных заявок</h2><span className="approval-delta" data-testid="approval-points">{a.available?`${signed(a.points)} п. п.`:'Нет данных'}</span></div>
        <div className="approval-values">
          <div><span>До изменения</span><strong data-testid="approval-before">{a.available?`${decimal(a.beforeRate)}%`:'—'}</strong><small>{count(a.before)} одобрено</small></div>
          <ArrowRight size={24} aria-hidden="true"/>
          <div><span>После изменения</span><strong data-testid="approval-after">{a.available?`${decimal(a.afterRate)}%`:'—'}</strong><small>{count(a.after)} одобрено</small></div>
        </div>
        <div className="approval-bars" aria-hidden="true"><div><i style={{width:`${a.beforeRate}%`}}/></div><div><i style={{width:`${a.afterRate}%`}}/></div></div>
        <p className="approval-relative" data-testid="approval-relative">{!a.available?'Нет заявок для сравнения':a.relative!==null?<><b>{signed(a.relative)}%</b> относительно прежней доли</>:a.after>0?'Ранее одобрений не было — относительное изменение не рассчитывается':'До и после одобрений нет'}</p>
      </section>
      <section className="infographic-card infographic-paths">
        <div className="infographic-heading"><h2>Чаще всего меняются</h2><span>Доля заявок</span></div>
        <div className="infographic-path-list">{paths.slice(0,5).map((r,i)=><button key={r.PATH} className="infographic-path" onClick={()=>open('applications',{path:r.PATH,changed:'1'})}>
          <span className="infographic-rank" aria-hidden="true">{String(i+1).padStart(2,'0')}</span><span className="infographic-path-name" title={r.PATH}>{r.PATH}</span>
          <span className="infographic-track" aria-hidden="true"><i style={{width:`${share(r.CNT,s.total_apps)}%`}}/></span>
          <span className="infographic-path-count"><b>{decimal(share(r.CNT,s.total_apps))}%</b><small>{count(r.CNT)} заявок</small></span>
        </button>)}{!paths.length&&<p className="infographic-empty"><CheckCircle2 size={22}/> Различий нет</p>}</div>
        <Action onClick={()=>open('pivot')}>Все атрибуты <ArrowUpRight size={15}/></Action>
      </section>
      <section className="infographic-card infographic-decisions">
        <div className="infographic-heading"><h2>Переходы решений</h2><span>До → после</span></div>
        <div className="infographic-transition-list">{transitions.map((t,i)=><div className="infographic-transition" key={i}>
          <div className="infographic-transition-label"><span><DecisionBadge value={t.DECISION_BEFORE}/> <ArrowRight size={13}/> <DecisionBadge value={t.DECISION_AFTER}/></span><b>{count(t.CNT)}</b></div>
          <div className="infographic-transition-bottom"><span className="infographic-track" aria-hidden="true"><i className={t.DECISION_BEFORE!==t.DECISION_AFTER?'is-changed':''} style={{width:`${share(t.CNT,s.total_apps)}%`}}/></span><small>{decimal(share(t.CNT,s.total_apps))}%</small></div>
        </div>)}</div>
        <p className="infographic-footnote"><Layers3 size={16}/><span><b>{count(s.changed_same_decision||0)}</b> с различиями без смены решения</span></p>
      </section>
    </div>
  </div>;
}
