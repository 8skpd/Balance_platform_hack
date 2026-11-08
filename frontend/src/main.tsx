import {ProfileMenu} from './ProfileMenu';
import {AIChat} from './AIChat';
import {RunOverview} from './Overview';
import {ScriptLibrary} from './Procedures';
import {StageSetup,ChecksSetup} from './RunSetup';
import {datasetLabel} from './labels';
import {Disclosure} from './Disclosure';
import {Activity as ActivityLog,CompareRuns,Discussion,Lifecycle,ReviewPanel} from './Workflow';
import {StoragePanel} from './Operations';
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Circle,
  Clock3,
  Database,
  Download,
  FileJson,
  Filter,
  FolderOpen,
  Layers3,
  LayoutDashboard,
  ListChecks,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Sparkles,
  Terminal,
  X,
  XCircle,
} from "lucide-react";
import "./style.css";
import { AuthGate, type User } from "./Auth";
import { Administration, ReportAccess } from "./Access";
import { Jobs, PythonEditor } from "./Jobs";
import { Configuration, ConfigEditor, ConfigPreview } from "./Normalization";
import { RunTemplates } from "./RunTemplates";
import { Datasets } from "./Datasets";
import { PivotBuilder } from "./Pivot";
import { Library, CheckPicker, CheckPlan, CheckSource, type CheckSelection } from "./Library";

type Row = Record<string, any>;
type Route = {
  view: string;
  run: string;
  tab: string;
  app: string;
  path: string;
  check: string;
  changed: string;
  decision: string;
};
const initialRoute = (): Route => {
  const p = new URLSearchParams(location.search);
  return {
    view: p.get("view") || "overview",
    run: p.get("run") || "",
    tab: p.get("tab") || "overview",
    app: p.get("app") || "",
    path: p.get("path") || "",
    check: p.get("check") || "",
    changed: p.get("changed") || "",
    decision: p.get("decision") || "",
  };
};
const number = (value: number = 0) =>
  new Intl.NumberFormat("ru-RU").format(value);
const date = (value: string) =>
  new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
const typeLabels: Row = {
  SAME: "Без изменений",
  VALUE_CHANGED: "Значение изменилось",
  ATTRIBUTE_NEW: "Добавлен",
  ATTRIBUTE_LOST: "Удалён",
};
const stageLabels: Row = {
  queued: "Ожидание запуска",
  starting: "Подготовка",
  publishing: "Публикация отчёта",
  cancelling: "Отмена задачи",
  cancelled: "Задача отменена",
  timed_out: "Превышен лимит времени",
  failed: "Ошибка обработки",
  validation: "Проверка данных",
  normalization: "Нормализация",
  comparison: "Сравнение",
  aggregation: "Подготовка сводной",
  checks: "Автоматические проверки",
  user_checks: "Пользовательские проверки",
  complete: "Отчёт готов",
  error: "Ошибка обработки",
};

async function api<T = any>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Balance-Request": "1",
      ...options?.headers,
    },
  });
  if (response.status === 401)
    window.dispatchEvent(new Event("balance-session-expired"));
  if (!response.ok) {
    const body = await response
      .json()
      .catch(() => ({ detail: "Ошибка запроса" }));
    throw Object.assign(new Error(
      typeof body.detail === "string"
        ? body.detail
        : JSON.stringify(body.detail),
    ), {status:response.status});
  }
  return response.json();
}
function useData<T = any>(url: string, interval = 0, body?:string) {
  const [state, setState] = useState<{ url: string; data?: T; error?: string }>(
    { url: "" },
  );
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!url) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const load = async () => {
      try {
        const data = await api<T>(url, { signal: controller.signal,...(body?{method:"POST",body}:{}) });
        if (alive) setState({ url, data });
      } catch (e) {
        if (alive)
          setState((previous) => ({
            url,
            data: previous.url === url && ![401,403,404].includes((e as Error & {status:number}).status) ? previous.data : undefined,
            error: (e as Error).message,
          }));
      }
      if (alive && interval) timer = setTimeout(load, interval);
    };
    load();
    return () => {
      alive = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [url, interval, revision, body]);
  return {
    ...(state.url === url ? state : { data: undefined, error: undefined }),
    reload: () => setRevision((x) => x + 1),
  };
}
function Button({
  children,
  onClick,
  secondary = false,
  disabled = false,
  type = "button",
  className = "",
}: {
  children: React.ReactNode;
  onClick?: () => void;
  secondary?: boolean;
  disabled?: boolean;
  type?: "button" | "submit";
  className?: string;
}) {
  return (
    <button
      type={type}
      className={`button ${secondary ? "secondary" : ""} ${className}`}
      onClick={onClick}
      disabled={disabled}
    >
      {children}
    </button>
  );
}
function Status({ status }: { status: string }) {
  const labels: Row = { completed: "Завершён", failed: "Ошибка", timed_out: "Лимит времени", cancelled: "Отменён", cancelling: "Отмена…", queued: "В очереди", running: "Выполняется" };
  const active = ["queued", "running", "cancelling"].includes(status);
  return <span className={`status ${status === "completed" ? "success" : active ? "pending" : "danger"}`}>
    {active ? <Loader2 size={13} className="spin" /> : status === "completed" ? <CheckCircle2 size={13} /> : <XCircle size={13} />}
    {labels[status] || status}
  </span>;
}
function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <Layers3 size={32} />
      <h3>{title}</h3>
      {description && <p>{description}</p>}
      {children}
    </div>
  );
}
function Loading() {
  return (
    <div className="loading">
      <Loader2 className="spin" size={22} /> Загружаем данные…
    </div>
  );
}
function ErrorBox({ message }: { message?: string }) {
  return message ? (
    <div role="alert" className="error-box">
      <XCircle size={18} />
      <span>{message}</span>
    </div>
  ) : null;
}
function Pager({
  page,
  total,
  limit = 30,
  setPage,
}: {
  page: number;
  total: number;
  limit?: number;
  setPage: (p: number) => void;
}) {
  return (
    <div className="pager">
      <span>
        {total
          ? `${number((page - 1) * limit + 1)}–${number(Math.min(page * limit, total))} из ${number(total)}`
          : "Нет строк"}
      </span>
      <div>
        <button
          aria-label="Предыдущая страница"
          disabled={page <= 1}
          onClick={() => setPage(page - 1)}
        >
          <ChevronLeft size={16} />
        </button>
        <span>
          {page} / {Math.max(1, Math.ceil(total / limit))}
        </span>
        <button
          aria-label="Следующая страница"
          disabled={page * limit >= total}
          onClick={() => setPage(page + 1)}
        >
          <ChevronRight size={16} />
        </button>
      </div>
    </div>
  );
}

function App({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => Promise<void>;
}) {
  const [route, setRoute] = useState<Route>(initialRoute);
  const [modal, setModal] = useState(false);
  const [preferredDataset, setPreferredDataset] = useState<Row|null>(null);
  const [error, setError] = useState("");
  const [historyPage,setHistoryPage]=useState(1),[historyArchive,setHistoryArchive]=useState(false),[historySearch,setHistorySearch]=useState('');
  const allRuns = useData<Row>(`/api/run-history?page=${route.view==='runs'?historyPage:1}&limit=20&archived=${route.view==='runs'&&historyArchive}&search=${encodeURIComponent(route.view==='runs'?historySearch:'')}`,4000);
  const canWrite = user.role !== "observer";
  const datasets = useData<Row[]>(canWrite ? "/api/datasets" : "");
  const runData = useData<Row>(route.run ? `/api/runs/${route.run}` : "", 3000);
  const navigate = (next: Partial<Route>) => {
    const r = { ...initialRoute(), ...next };
    const p = new URLSearchParams();
    Object.entries(r).forEach(([key, value]) => {
      if (
        value &&
        !(key === "view" && value === "overview") &&
        !(key === "tab" && value === "overview")
      )
        p.set(key, value);
    });
    history.pushState(null, "", p.size ? "?" + p.toString() : "/");
    setRoute(r);
    if (r.run !== route.run || r.view !== route.view) window.scrollTo(0, 0);
  };
  const openRun = (id: string, tab = "overview", extra: Partial<Route> = {}) =>
    navigate({
      view: "overview",
      run: id,
      tab,
      app: "",
      path: "",
      check: "",
      changed: "",
      decision: "",
      ...extra,
    });
  const nav = (view: string) =>
    navigate({
      view,
      run: "",
      tab: "overview",
      app: "",
      path: "",
      check: "",
      changed: "",
      decision: "",
    });
  useEffect(() => {
    const handler = () => setRoute(initialRoute());
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, []);
  useEffect(() => {
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        document.querySelectorAll('details.action-menu[open]').forEach(el=>el.removeAttribute('open'));
      }
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  const runs:Row[] = allRuns.data?.items || [];
  const run:Row|undefined = runData.data ? {...runData.data,viewer:user}:undefined;
  const pageName: Row = {
    overview: "Обзор системы",
    runs: "История запусков",
    datasets: "Датасеты",
    settings: "Настройки обработки",
    jobs: "Мои задачи",
    library: "Библиотека",
    admin: "Администрирование",
    compare: "Сравнение запусков",
    storage: "Хранилище",
  };
  const start = () => {if(canWrite){setPreferredDataset(null);setModal(true);}};
  return (
    <div className="shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="/"
          onClick={(e) => {
            e.preventDefault();
            nav("overview");
          }}
        >
          <span className="brand-symbol">
            <i />
            <i />
            <i />
          </span>
          <span>
            баланс<span className="brand-sub">платформа</span>
          </span>
        </a>
        <nav aria-label="Основная навигация">
          {[
            {id:'overview',icon:LayoutDashboard,label:'Обзор'},
            {id:'runs',icon:Layers3,label:'Запуски'},
            {id:'datasets',icon:Database,label:'Датасеты'},
          ].filter(n=>canWrite||['overview','runs'].includes(n.id)).map(n=><button key={n.id} title={n.label} aria-label={n.label} className={(!route.run&&route.view===n.id)||(route.run&&n.id==='runs')?'active':''} onClick={()=>nav(n.id)}><n.icon size={19}/><span>{n.label}</span></button>)}
          <>{canWrite&&<button title="Настройка анализа" aria-label="Настройка анализа" onClick={()=>setModal(true)}><Settings2 size={19}/><span>Настройка анализа</span></button>}</>
          <details className="nav-tools" open={['jobs','library','compare','storage','admin'].includes(route.view)||undefined}>
            <summary title="Инструменты"><Settings2 size={19}/><span>Инструменты</span><ChevronDown size={14}/></summary>
            {[
              {id:'jobs',icon:Clock3,label:'Задачи'},
              {id:'library',icon:ListChecks,label:'Библиотека'},
              {id:'compare',icon:Layers3,label:'Сравнение запусков'},
              {id:'storage',icon:Database,label:'Хранилище'},
              {id:'admin',icon:Settings2,label:'Администрирование'},
            ].filter(n=>n.id==='admin'?user.role==='admin':canWrite||n.id==='compare').map(n=><button key={n.id} title={n.label} aria-label={n.label} className={route.view===n.id&&!route.run?'active':''} onClick={()=>nav(n.id)}><n.icon size={18}/><span>{n.label}</span></button>)}
          </details>
        </nav>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div>
            <strong>
              {route.run ? "Результаты запуска" : pageName[route.view]}
            </strong>
          </div>
          <ProfileMenu user={user} request={api} logout={onLogout}/>
        </header>
        <main>
          {error && (
            <div className="dismiss-error">
              <ErrorBox message={error} />
              <button aria-label="Закрыть ошибку" onClick={() => setError("")}>
                <X size={16} />
              </button>
            </div>
          )}
          <ErrorBox message={allRuns.error || runData.error} />
          {!route.run && route.view==='runs'&&<section className="panel operations-panel"><div className="job-actions"><input aria-label="Поиск в истории" placeholder="Поиск анализа" value={historySearch} onChange={e=>{setHistorySearch(e.target.value);setHistoryPage(1);}}/><label><input type="checkbox" checked={historyArchive} onChange={e=>{setHistoryArchive(e.target.checked);setHistoryPage(1);}}/> Архив</label></div><Pager total={allRuns.data?.total||0} page={historyPage} limit={20} setPage={setHistoryPage}/></section>}
          {route.run ? (
            !run ? (
              runData.error ? (
                <Empty
                  title="Отчёт недоступен"
                  description="Запуск не найден или доступ к нему отозван."
                >
                  <Button onClick={() => nav("runs")}>Мои запуски</Button>
                </Empty>
              ) : (
                <Loading />
              )
            ) : (
              <>
                <button
                  className="text-button back"
                  onClick={() => nav("runs")}
                >
                  <ArrowLeft size={15} /> Все запуски
                </button>
                <div className="page-heading">
                  <div>

                    <h1>{run.name}</h1>
                    <p>{date(run.created_at)}</p>
                  </div>
                  <div className="heading-actions">
                    <Status status={run.status} />
                    {run.archived&&<span className="tag">В архиве</span>}
                    {!run.can_manage&&<span className="muted">{canWrite?'Совместный доступ':'Только просмотр'}</span>}
                    {run.can_manage&&<ReportAccess run={run} request={api} refresh={runData.reload}/>}

                    {run.status === "completed" && (
                      <a
                        className="button secondary"
                        href={`/api/runs/${run.id}/report.html`}
                      >
                        <Download size={16} /> HTML-отчёт
                      </a>
                    )}
                  </div>
                </div>
                {run.status !== "completed" ? (
                  <div className="panel progress-panel">
                    <div className="progress-summary">
                      <div className="progress-heading">
                        <div className="progress-title"><h2>{stageLabels[run.stage]||stageLabels[run.status]||"Обработка анализа"}</h2><span>{run.progress}%</span></div>
                        {run.can_manage&&<div className="progress-actions"><Button secondary onClick={()=>nav("jobs")}>Открыть задачи</Button>{["failed","cancelled","timed_out"].includes(run.status)&&<Button onClick={start}><RefreshCw size={16}/>Новый запуск</Button>}</div>}
                      </div>
                      <div className="progress-track" role="progressbar" aria-label="Выполнение анализа" aria-valuemin={0} aria-valuemax={100} aria-valuenow={run.progress}>
                        <div style={{width:Math.max(0,Math.min(100,run.progress))+"%"}}/>
                      </div>
                      <ErrorBox message={run.error}/>
                    </div>
                    <div className="progress-journal"><h3>Журнал этапов</h3><Journal events={run.events||[]} run={run}/></div>
                    <div className="progress-extras"><CheckPlan key={run.id} run={run} request={api}/></div>
                  </div>
                ) : (
                  <>
                    <div className="report-navigation">
                      <div className="tabs report-tabs" aria-label="Режимы отчёта">
                        {[
                          {id:'overview',label:'Обзор',icon:LayoutDashboard},
                          {id:'pivot',label:'Сводная таблица',icon:Layers3},
                          {id:'applications',label:'Заявки',icon:FileJson},
                          {id:'checks',label:'Проверки',icon:ListChecks},
                          {id:'ai',label:'Мнение ИИ',icon:Sparkles},
                        ].map(t=><button key={t.id} className={route.tab===t.id?'selected':''} aria-current={route.tab===t.id?'page':undefined} onClick={()=>openRun(run.id,t.id)}><t.icon size={16}/>{t.label}</button>)}
                      </div>
                      <details className="action-menu">
                        <summary aria-label="Дополнительные действия анализа">Ещё <ChevronDown size={14}/></summary>
                        <div className="action-menu-items">
                          {[
                            {id:'discussion',label:'Обсуждение и согласование'},
                            ...(run.can_manage?[
                              {id:'python',label:'Редактор Python'},
                              {id:'journal',label:'Журнал выполнения'},
                              {id:'manage',label:'Повторный расчёт и архив'},
                            ]:[]),
                          ].map(t=><button key={t.id} aria-current={route.tab===t.id?'page':undefined} onClick={e=>{e.currentTarget.closest('details')?.removeAttribute('open');openRun(run.id,t.id);}}>{t.label}</button>)}
                        </div>
                      </details>
                    </div>
                    <div className="report-content">
                    {route.tab === "overview" && (
                      <RunOverview
                        run={run}
                        open={(tab, extra) => openRun(run.id, tab, extra)}
                      />
                    )}
                    {route.tab === "pivot" && (
                      <PivotBuilder key={run.id} run={run} request={api} readOnly={!run.can_manage}
                        application={(appId, back, scope) => <ApplicationDetail pivotScope={scope} run={run} route={{...route, app:appId, path:"", changed:""}} back={back} />} />
                    )}
                    {route.tab === "applications" &&
                      (route.app ? (
                        <ApplicationDetail
                          key={route.app + route.path}
                          run={run}
                          route={route}
                          back={() =>
                            openRun(run.id, "applications", {
                              path: route.path,
                              check: route.check,
                              changed: route.changed,
                              decision: route.decision,
                            })
                          }
                        />
                      ) : (
                        <Applications
                          key={
                            route.path +
                            route.check +
                            route.changed +
                            route.decision
                          }
                          run={run}
                          route={route}
                          reset={() => openRun(run.id, "applications")}
                          open={(app) =>
                            openRun(run.id, "applications", { ...route, app })
                          }
                        />
                      ))}
                    {route.tab === "checks" && (
                      <Checks
                        run={run}
                        open={(index) =>
                          openRun(run.id, "applications", {
                            check: String(index),
                          })
                        }
                      />
                    )}
                    {route.tab === "python" && run.can_manage && <PythonEditor key={run.id} runId={run.id} request={api} />}
                    {route.tab === "journal" && run.can_manage && (
                      <div className="panel">
                        <div className="panel-heading">
                          <h2>Журнал выполнения</h2>
                          <span className="muted">
                            {run.events.length} событий
                          </span>
                        </div>
                        <Journal events={run.events} run={run} />
                      </div>
                    )}
                    {!run.can_manage && ["python","journal"].includes(route.tab) && <p className="info-strip">Этот раздел доступен только владельцу анализа с правом редактирования.</p>}
                    {route.tab === 'manage'&&run.can_manage&&<Lifecycle run={run} request={api} refresh={()=>{runData.reload();allRuns.reload();}} open={openRun}/>}
                    {route.tab === "discussion" && <><ReviewPanel key={run.id} run={run} user={user} request={api}/><Discussion key={run.id} runId={run.id} user={user} request={api}/><Disclosure title="История действий"><ActivityLog url={`/api/runs/${run.id}/activity`} request={api}/></Disclosure></>}
                    {route.tab === "ai" && <AIChat key={run.id} run={run} request={api} refresh={runData.reload}/>}
                    </div>
                  </>
                )}
              </>
            )
          ) : route.view === "compare" ? (<CompareRuns request={api}/>) : route.view === "storage" && canWrite ? (<StoragePanel request={api}/>) : route.view === "admin" ? (
            user.role === "admin" ? <Administration user={user} request={api}/> : <Empty title="Доступ ограничен" description="Нужна роль администратора."/>
          ) : !canWrite ? (
            !["overview","runs"].includes(route.view) ? <Empty title="Доступ ограничен" description="Наблюдатель может просматривать доступные отчёты."/> : <><div className="page-heading"><div><h1>Доступные анализы</h1></div></div><div className="panel">{!allRuns.data ? <Loading/> : !runs.length ? <Empty title="Пока нет доступных анализов" description={`Попросите владельца предоставить просмотр аккаунту @${user.username}.`}/> : <div className="table-scroll"><table><thead><tr><th>Анализ</th><th>Доступ</th><th>Состояние</th><th>Создан</th></tr></thead><tbody>{runs.map(r=><tr key={r.id}><td><button className="row-title" onClick={()=>openRun(r.id)}>{r.name}</button>{r.access === "shared" && <small>Совместный просмотр</small>}</td><td>{r.access === "shared" ? "Предоставлен владельцем" : "Свой отчёт"} · Просмотр</td><td><Status status={r.status}/></td><td>{date(r.created_at)}</td></tr>)}</tbody></table></div>}</div></>
          ) : route.view === "library" ? (
            <ScriptLibrary request={api} openJobs={() => nav("jobs")} />
          ) : route.view === "jobs" ? (
            <Jobs request={api} openRun={(id) => openRun(id)} />
          ) : route.view === "settings" ? (
            <Configuration request={api} />
          ) : route.view === "datasets" ? (
            <Datasets request={api} onRun={(dataset)=>{setPreferredDataset(dataset);datasets.reload();setModal(true);}} />
          ) : (
            <>
              <PageHeading
                eyebrow="АНАЛИЗ ИЗМЕНЕНИЙ"
                title={
                  route.view === "runs"
                    ? "История запусков"
                    : "Анализы"
                }
                description=""
                action={
                  <Button onClick={start}>
                    <Plus size={18} /> Новый запуск
                  </Button>
                }
              />
              <div className="panel">
                <div className="panel-heading">
                  <div>
                    <h2>
                      {route.view === "overview"
                        ? "Последние запуски"
                        : "Все запуски"}
                    </h2>

                  </div>
                  {route.view === "overview" && (
                    <button className="text-button" onClick={() => nav("runs")}>
                      Все запуски <ArrowRight size={15} />
                    </button>
                  )}
                </div>
                {!allRuns.data ? (
                  <Loading />
                ) : !runs.length ? (
                  <Empty
                    title="Первый отчёт начинается здесь"
                    description="Выданный датасет уже подключён. Создайте запуск, чтобы сравнить две версии стратегии."
                  >
                    <Button onClick={start}>
                      <Plus size={16} /> Создать запуск
                    </Button>
                  </Empty>
                ) : (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Анализ</th>
                          <th>Состояние</th>
                          <th>Заявок</th>
                          <th>С различиями</th>
                          <th>Создан</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {(route.view === "overview"
                          ? runs.slice(0, 5)
                          : runs
                        ).map((r) => (
                          <tr key={r.id}>
                            <td>
                              <button
                                className="row-title"
                                onClick={() => openRun(r.id)}
                              >
                                {r.name}
                              </button>
                              {r.access === "shared" && <small>Совместный просмотр</small>}
                            </td>
                            <td>
                              <Status status={r.status} />
                            </td>
                            <td>
                              {r.summary ? number(r.summary.total_apps) : "—"}
                            </td>
                            <td>
                              {r.summary ? (
                                <span className="orange-text">
                                  {number(r.summary.changed_apps)}
                                </span>
                              ) : (
                                "—"
                              )}
                            </td>
                            <td className="muted nowrap">
                              {date(r.created_at)}
                            </td>
                            <td>
                              <button
                                className="icon-button"
                                aria-label={`Открыть ${r.name}`}
                                onClick={() => openRun(r.id)}
                              >
                                <ArrowUpRight size={19} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </>
          )}

        </main>
      </div>
      {modal && canWrite && (
        <NewRunModal
          datasets={preferredDataset ? [preferredDataset, ...(datasets.data || []).filter(d=>d.id!==preferredDataset.id)] : datasets.data || []}
          close={() => setModal(false)}
          created={(id) => {
            setModal(false);
            allRuns.reload();
            openRun(id);
          }}
        />
      )}
    </div>
  );
}

function PageHeading({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>

        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}

function Applications({
  run,
  route,
  reset,
  open,
}: {
  run: Row;
  route: Route;
  reset: () => void;
  open: (app: string) => void;
}) {
  const [search, setSearch] = useState(""),
    [page, setPage] = useState(1),
    [changed, setChanged] = useState(route.changed === "1");
  const query = new URLSearchParams({
    search,
    page: String(page),
    changed: String(changed),
    decision_changed: String(route.decision === "1"),
    path: route.path,
  });
  if (route.check) query.set("check_index", route.check);
  const data = useData<Row>(`/api/runs/${run.id}/applications?${query}`);
  const selectedCheck = route.check ? run.checks[Number(route.check)] : null;
  return (
    <div className="panel">
      <div className="panel-heading">
        <div>
          <h2>Заявки</h2>

        </div>
        <FileJson size={20} className="muted" />
      </div>
      {(route.path || route.check || route.decision) && (
        <div className="filter-strip">
          <Filter size={15} />
          <span>
            {route.path ||
              (selectedCheck
                ? `${selectedCheck.LABEL}: заявок в детализации: ${selectedCheck.MEMBERSHIP?.count??selectedCheck.APP_IDS.length} из ${selectedCheck.CNT}`
                : "Только заявки со сменой решения")}
          </span>
          <button aria-label="Сбросить фильтры" onClick={reset}>
            <X size={15} />
          </button>
        </div>
      )}
      <div className="toolbar">
        <label className="search">
          <Search size={16} />
          <input
            aria-label="Поиск заявки"
            placeholder="Поиск по ID заявки…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
          />
        </label>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={changed}
            onChange={(e) => {
              setChanged(e.target.checked);
              setPage(1);
            }}
          />
          Только с различиями
        </label>
      </div>
      <ErrorBox message={data.error} />
      {!data.data ? (
        <Loading />
      ) : (
        <>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Заявка</th>
                  <th>MASTER</th>
                  <th>BETA</th>
                  <th>Изменений атрибутов</th>
                  <th>Продукты</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.data.items.map((r: Row) => (
                  <tr key={r.APP_ID}>
                    <td>
                      <button
                        className="row-title"
                        onClick={() => open(r.APP_ID)}
                      >
                        {r.APP_ID}
                      </button>
                    </td>
                    <td>
                      <span
                        className={`decision ${r.DECISION_BEFORE === "APPROVE" ? "approved" : ""}`}
                      >
                        {r.DECISION_BEFORE}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`decision ${r.DECISION_AFTER === "APPROVE" ? "approved" : ""}`}
                      >
                        {r.DECISION_AFTER}
                      </span>
                    </td>
                    <td>
                      <span
                        className={r.changed_attributes ? "orange-text" : ""}
                      >
                        {r.changed_attributes}
                      </span>
                    </td>
                    <td className="muted">
                      {r.PRODUCTS.replaceAll(";", ", ")}
                    </td>
                    <td>
                      <button
                        className="icon-button"
                        aria-label={`Открыть ${r.APP_ID}`}
                        onClick={() => open(r.APP_ID)}
                      >
                        <ArrowRight size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.data.items.length && (
              <Empty
                title="Заявки не найдены"
                description="Измените условия поиска."
              />
            )}
          </div>
          <Pager page={page} total={data.data.total} setPage={setPage} />
        </>
      )}
    </div>
  );
}

function displayValue(value: any, type: string) {
  if (value === null || value === undefined)
    return <span className="missing">отсутствует</span>;
  if (type === "null") return <span className="null-value">null</span>;
  if (value === "") return <span className="null-value">пустая строка</span>;
  return <span>{value}</span>;
}
function ApplicationDetail({
  run,
  route,
  back,
  pivotScope,
}: {
  pivotScope?:Row;
  run: Row;
  route: Route;
  back: () => void;
}) {
  const [scoped,setScoped]=useState(true);
  const [changed, setChanged] = useState(false),
    [search, setSearch] = useState(""),
    [page, setPage] = useState(1),
    [source, setSource] = useState(false);
  const q = new URLSearchParams({
    changed: String(changed),
    search,
    page: String(page),
    path: route.path,
  });
  const data = useData<Row>(
    pivotScope&&scoped?`/api/runs/${run.id}/pivot/attributes`:`/api/runs/${run.id}/applications/${encodeURIComponent(route.app)}?${q}`,
    0,pivotScope&&scoped?JSON.stringify({...pivotScope,app_id:route.app,page,limit:100,changed,search}):undefined,
  );
  const raw = useData<Row>(
    source
      ? `/api/runs/${run.id}/applications/${encodeURIComponent(route.app)}/source`
      : "",
  );
  return (
    <>
      <button className="text-button back" onClick={back}>
        <ArrowLeft size={15} /> К списку заявок
      </button>
      <div className="panel">
        <div className="panel-heading">
          <div>

            <h2 className="application-title">{route.app}</h2>
          </div>
          <Button secondary onClick={() => setSource(!source)}>
            <FileJson size={16} />
            {source ? "Скрыть JSON" : "Исходный JSON"}
          </Button>
        </div>
        {data.data && (
          <div className="application-summary">
            <span>Решение</span>
            <b>{data.data.segment.DECISION_BEFORE}</b>
            <ArrowRight size={17} />
            <b>{data.data.segment.DECISION_AFTER}</b>
            <span className="tag">
              {data.data.segment.changed_attributes} изменений
            </span>
          </div>
        )}
        {pivotScope&&<div className="filter-strip"><span>{scoped?"Атрибуты выбранной ячейки":"Все атрибуты заявки"}</span><button className="text-button" onClick={()=>{setScoped(!scoped);setPage(1);}}>{scoped?"Показать всю заявку":"Вернуться к выбранной ячейке"}</button></div>}
        {route.path && (
          <div className="filter-strip">
            <Filter size={14} />
            {route.path}
          </div>
        )}
        <div className="toolbar">
          <label className="search">
            <Search size={16} />
            <input
              aria-label="Поиск атрибута"
              placeholder="Путь или ключ массива…"
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={changed}
              onChange={(e) => {
                setChanged(e.target.checked);
                setPage(1);
              }}
            />
            Только изменения
          </label>
        </div>
        <ErrorBox message={data.error} />
        {!data.data ? (
          <Loading />
        ) : (
          <>
            <div className="table-scroll">
              <table className="attribute-table">
                <thead>
                  <tr>
                    <th>Атрибут / контекст</th>
                    <th>BEFORE / MASTER</th>
                    <th>AFTER / BETA</th>
                    <th>Изменение</th>
                  </tr>
                </thead>
                <tbody>
                  {data.data.items.map((r: Row) => (
                    <tr
                      key={r.PATH + r.ARRAY_KEY}
                      className={r.DIFF_FLAG ? "diff-row" : ""}
                    >
                      <td className="path-cell">
                        {r.PATH}
                        {r.ARRAY_KEY && <small>{r.ARRAY_KEY}</small>}
                      </td>
                      <td className="value-cell">
                        {displayValue(r.VALUE_BEFORE, r.TYPE_BEFORE)}
                      </td>
                      <td className="value-cell">
                        {displayValue(r.VALUE_AFTER, r.TYPE_AFTER)}
                      </td>
                      <td>
                        <span
                          className={`change-tag ${r.CHANGE_TYPE.toLowerCase()}`}
                        >
                          {typeLabels[r.CHANGE_TYPE]}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!data.data.total && <Empty title="Атрибуты не найдены" />}
            </div>
            <Pager
              total={data.data.total}
              page={page}
              limit={100}
              setPage={setPage}
            />
          </>
        )}
      </div>
      {source && (
        <div className="panel raw-panel">
          <div className="panel-heading">
            <h2>Исходные ответы СПР</h2>
          </div>
          <ErrorBox message={raw.error} />
          {!raw.data ? (
            <Loading />
          ) : (
            <div className="two-columns">
              {["BEFORE", "AFTER"].map((s) => (
                <div key={s}>
                  <h3>{s}</h3>
                  <pre>{JSON.stringify(raw.data?.[s], null, 2)}</pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <Disclosure key={run.id+route.app} title="Комментарии к заявке"><Discussion runId={run.id} user={run.viewer} request={api} kind="application" target={route.app}/></Disclosure>
    </>
  );
}

function CheckResults({name, rows, onlyFailed, open, discuss, run, spec}: {
  name: string; rows: Row[]; onlyFailed: boolean; open: (index:number)=>void; discuss: (index:number)=>void; run:Row; spec?:Row;
}) {
  const [expanded,setExpanded]=useState(false),[page,setPage]=useState(1);
  useEffect(()=>setPage(1),[onlyFailed]);
  const failed=rows.filter(c=>c.RESULT==='FAIL').length;
  const filtered=onlyFailed?rows.filter(c=>c.RESULT==='FAIL'):rows;
  if(!filtered.length&&(onlyFailed||rows.length>0))return null;
  const currentPage=Math.min(page,Math.max(1,Math.ceil(filtered.length/20)));
  return <details className="check-result-group" onToggle={e=>setExpanded(e.currentTarget.open)}>
    <summary><span className="check-group-name">{name}</span><span className="muted">Результаты: {rows.length}</span><span className={`status ${!rows.length?'':failed?'danger':'success'}`}>{!rows.length?'Нет результатов':failed?`FAIL · ${failed}`:'PASS'}</span></summary>
    {expanded&&<>{filtered.length>0?<><div className="table-scroll"><table><thead><tr>
      <th>Продукт / атрибут / правило</th><th>Срез</th><th>Количество / всего</th><th>Метрика</th><th>Результат</th><th>Заявки</th><th aria-label="Обсуждение"/>
    </tr></thead><tbody>{filtered.slice((currentPage-1)*20,currentPage*20).map(c=><tr key={c.index} data-check-index={c.index} className={c.CONFIDENCE==='LOW'?'low-confidence':''}>
      <td className="check-name">{c.EXPLANATION&&<small>{c.EXPLANATION}</small>}{c.LABEL!==name&&<small>{c.LABEL}</small>}{c.ARGUMENT||'Все заявки'}{c.CONFIDENCE==='LOW'&&<span className="low-label">Мало наблюдений</span>}</td>
      <td>{c.SOURCE==='BEFORE'?'До':c.SOURCE==='AFTER'?'После':c.SOURCE}</td>
      <td>{number(c.CNT)} <span className="muted">/ {number(c.TOTAL)}</span></td><td>{c.METRIC}</td>
      <td><span className={`status ${c.RESULT==='PASS'?'success':'danger'}`}>{c.RESULT}</span></td>
      <td>{(c.MEMBERSHIP?.count??c.APP_IDS.length)>0?<button className="number-link" onClick={()=>open(c.index)}>{number(c.MEMBERSHIP?.count??c.APP_IDS.length)} {c.MEMBERSHIP?.count===c.CNT?"заявок":"примеров"} <ArrowUpRight size={13}/></button>:'—'}</td>
      <td><button className="text-button" onClick={()=>discuss(c.index)}>Обсудить</button></td>
    </tr>)}</tbody></table></div><Pager total={filtered.length} page={currentPage} limit={20} setPage={setPage}/></>:<p className="checks-empty">Проверка не вернула результатов.</p>}{run.can_manage&&spec&&<CheckSource runId={run.id} spec={spec} request={api}/>}</>}
  </details>;
}
function Checks({ run, open }: { run: Row; open: (index: number) => void }) {
  const [onlyFailed, setOnlyFailed] = useState(false),[discussion,setDiscussion]=useState<number|null>(null);
  // Preserve original row indices: drill-down and comments address the saved result.
  const groups=new Map<string,{name:string;rows:Row[]}>();
  run.checks.forEach((c:Row,index:number)=>{
    const key=c.CHECK_ID||c.CHECK_NAME||c.LABEL;
    if(!groups.has(key))groups.set(key,{name:c.CHECK_NAME||c.LABEL,rows:[]});
    groups.get(key)!.rows.push({...c,index});
  });
  for(const spec of run.check_plan||[]){if(!groups.has(spec.key)&&![...groups.values()].some(g=>g.name===spec.name))groups.set(spec.key,{name:spec.name,rows:[]});}
  return <>
    <div className="panel checks-panel" id="check-plan">
      <div className="panel-heading"><h2>Результаты автоматических проверок</h2><label className="checkbox"><input type="checkbox" checked={onlyFailed} onChange={e=>setOnlyFailed(e.target.checked)}/>Только FAIL</label></div>
      {[...groups.entries()].map(([key,group])=><CheckResults key={run.id+key} {...group} run={run} spec={run.check_plan?.find((c:Row)=>c.key===key||c.name===group.name)} onlyFailed={onlyFailed} open={open} discuss={setDiscussion}/>)}
      {!groups.size&&<p className="checks-empty">Нет результатов проверок.</p>}
      {!!run.checks.length&&onlyFailed&&!run.checks.some((c:Row)=>c.RESULT==='FAIL')&&<p className="checks-empty">Срабатываний нет.</p>}
    </div>
    {discussion!==null&&<><button className="text-button" onClick={()=>setDiscussion(null)}>Закрыть обсуждение проверки</button><Discussion key={run.id+':'+discussion} runId={run.id} user={run.viewer} request={api} kind="check" target={String(discussion)}/></>}
  </>;
}
function Journal({ events,run }: { events: Row[];run:Row }) {
  const [snapshot,setSnapshot]=useState<Row|null>(null),[error,setError]=useState('');
  async function show(stage:string,check=false){try{setError('');setSnapshot(await api(`/api/runs/${run.id}/${check?"check-plan":"procedure-plan"}/${encodeURIComponent(stage)}`));}catch(e){setError((e as Error).message);}}
  return (<>
    {error&&<ErrorBox message={error}/>}
    {snapshot&&<div className="panel"><div className="panel-heading"><h3>{snapshot.name}</h3><button className="text-button" onClick={()=>setSnapshot(null)}>Закрыть код</button></div><pre className="job-log">{snapshot.code}</pre><details><summary>Базовые функции этой процедуры</summary>{Object.entries(snapshot.helpers||{}).map(([name,code])=><details key={name}><summary>{name}</summary><pre className="job-log">{String(code)}</pre></details>)}</details></div>}

    <div className="journal">
      {events.map((e, i) => (
        <div key={i}>
          <span
            className={`journal-marker ${e.stage === "error" ? "failed" : ""}`}
          />
          <time>{new Date(e.time).toLocaleTimeString("ru-RU")}</time>
          <span className="journal-stage">{stageLabels[e.stage]}</span>
          <span className="journal-message">{e.message}</span><div className="journal-actions">{run.can_manage&&run.procedure_plan?.[e.stage]&&<button className="text-button" onClick={()=>show(e.stage)}>Код этапа</button>}{run.can_manage&&e.check_key&&<button className="text-button" onClick={()=>show(e.check_key,true)}>Код проверки</button>}{run.can_manage&&!e.check_key&&["checks","user_checks"].includes(e.stage)&&<a className="text-button" href={`?run=${run.id}&tab=checks#check-plan`}>Код проверок</a>}</div>
        </div>
      ))}
    </div></>
  );
}

function NewRunModal({datasets,close,created}:{datasets:Row[];close:()=>void;created:(id:string)=>void}) {
  const config=useData<Row>('/api/config'),available=useData<Row[]>('/api/datasets');
  const datasetOptions=available.data||datasets;
  const [activeStep,setActiveStep]=useState('data');
  const [templateBusy,setTemplateBusy]=useState(false),[procedureSelection,setProcedureSelection]=useState<Row>({}),[autoAi,setAutoAi]=useState(false);
  const [selection,setSelection]=useState<CheckSelection|null>(null),[name,setName]=useState('Сравнение MASTER → BETA'),[dataset,setDataset]=useState(datasets[0]?.id||'');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[text,setText]=useState(''),[drafts,setDrafts]=useState<Record<string,boolean>>({});
  const [validated,setValidated]=useState(''),[preflightError,setPreflightError]=useState(''),[warnings,setWarnings]=useState<string[]>([]),[attempt,setAttempt]=useState(0);
  const pending=Object.values(drafts).some(Boolean);
  const markPending=React.useCallback((stage:string,v:boolean)=>setDrafts(prev=>prev[stage]===v?prev:{...prev,[stage]:v}),[]);
  useEffect(()=>{if(config.data)setText(JSON.stringify(config.data,null,2));},[config.data]);
  useEffect(()=>{if(!dataset&&datasetOptions.length)setDataset(datasetOptions[0].id);},[datasetOptions,dataset]);
  let payload='';try{if(dataset&&selection&&text&&name.trim())payload=JSON.stringify({name,dataset,config:JSON.parse(text),procedures:procedureSelection,auto_ai:autoAi,...selection});}catch{}
  useEffect(()=>{
    setValidated('');setPreflightError('');setWarnings([]);if(!payload||pending)return;
    let cancelled=false;const timer=setTimeout(()=>{api<Row>('/api/runs/validate',{method:'POST',body:payload}).then(r=>{if(cancelled)return;if(r.valid){setValidated(payload);setWarnings(r.diagnostics.map((d:Row)=>`${d.name}: ${d.message}`));}else setPreflightError(r.diagnostics.map((d:Row)=>d.message).join('; '));}).catch(e=>{if(!cancelled)setPreflightError(e.message);});},450);
    return()=>{cancelled=true;clearTimeout(timer);};
  },[payload,pending,attempt]);
  const ready=!!payload&&payload===validated&&!pending&&!templateBusy&&!busy;
  const dismiss=()=>{if(busy||templateBusy)return;if(pending&&!window.confirm('Закрыть настройку анализа? Неприменённый код будет потерян.'))return;close();};
  const dismissRef=React.useRef(dismiss);dismissRef.current=dismiss;
  useEffect(()=>{const old=document.body.style.overflow;document.body.style.overflow='hidden';const handle=(e:KeyboardEvent)=>{if(e.defaultPrevented)return;if(e.key==='Tab'&&!document.querySelector('dialog[open]')){const dialog=document.querySelector('.run-setup-modal');const nodes=Array.from(dialog?.querySelectorAll<HTMLElement>('button,input,select,textarea,summary,[tabindex="0"],[contenteditable="true"]')||[]).filter(el=>el.offsetParent!==null&&!el.matches(':disabled')&&el.tabIndex!==-1);const first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}if(e.key==='Escape'&&!document.querySelector('dialog[open]')&&!document.querySelector('.cm-tooltip-autocomplete')){e.preventDefault();dismissRef.current();}};document.addEventListener('keydown',handle);return()=>{document.body.style.overflow=old;document.removeEventListener('keydown',handle);};},[]);
  const submit=async(e:React.FormEvent)=>{e.preventDefault();if(!ready)return;setBusy(true);setError('');try{const run=await api<Row>('/api/runs',{method:'POST',body:payload});created(run.id);}catch(e){setError((e as Error).message);setValidated('');setAttempt(n=>n+1);}finally{setBusy(false);}};
  const stageOptions=(stage:'normalization'|'aggregation'|'checks',label:string)=><details className="stage-options"><summary>{label}</summary><ConfigEditor value={text} onChange={setText} section={stage}/>{stage==='normalization'&&<ConfigPreview request={api} value={text} dataset={dataset}/>}</details>;
  const steps=[{id:'data',title:'Данные',heading:'Данные для анализа',description:'Выберите набор ответов и задайте название запуска.'},{id:'normalization',title:'Нормализация',heading:'Разбор и нормализация',description:'Какие поля и значения будут участвовать в сравнении.'},{id:'comparison',title:'Сравнение',heading:'Сравнение ответов',description:'Как определять различия между версиями до и после.'},{id:'aggregation',title:'Отчёт',heading:'Подготовка отчёта',description:'Как группировать результаты в сводной таблице.'},{id:'checks',title:'Проверки',heading:'Автоматические проверки',description:'Какие правила проверить и включить в итоговый отчёт.'}];
  const currentIndex=steps.findIndex(s=>s.id===activeStep);
  const chosenDataset=datasetOptions.find(d=>d.id===dataset);
  const stepStatus=(id:string)=>drafts[id]?'Требует внимания':id==='data'?(dataset?'Датасет выбран':'Выберите датасет'):id==='checks'?(selection?`Выбрано: ${selection.builtin_checks.length+selection.user_checks.length}`:'Загрузка…'):procedureSelection[id]?'Своя процедура':'Стандартная';
  return <div className="modal-overlay"><section className="modal run-setup-modal" role="dialog" aria-modal="true" aria-labelledby="new-run-title">
    <div className="modal-header"><h2 id="new-run-title">Настройка анализа</h2><button type="button" className="icon-button" disabled={busy||templateBusy} onClick={dismiss} aria-label="Закрыть"><X size={21}/></button></div>
    <form onSubmit={submit}><fieldset disabled={busy||templateBusy} className="normalization-fieldset setup-form">
      <div className="setup-workbench">
        <div className="setup-step-nav" role="tablist" aria-label="Разделы настройки анализа" aria-orientation="horizontal">{steps.map((step,i)=><button type="button" role="tab" id={'setup-tab-'+step.id} aria-controls={'setup-pane-'+step.id} aria-selected={activeStep===step.id} tabIndex={activeStep===step.id?0:-1} key={step.id} onClick={()=>setActiveStep(step.id)} onKeyDown={e=>{let next=i;if(['ArrowDown','ArrowRight'].includes(e.key))next=(i+1)%steps.length;else if(['ArrowUp','ArrowLeft'].includes(e.key))next=(i+steps.length-1)%steps.length;else if(e.key==='Home')next=0;else if(e.key==='End')next=steps.length-1;else return;e.preventDefault();setActiveStep(steps[next].id);document.getElementById('setup-tab-'+steps[next].id)?.focus();}}><span className="setup-step-number">{i+1}</span><span className="setup-step-label"><strong><span className="setup-step-full">{step.title}</span><span className="setup-step-short">{step.id==='normalization'?'Разбор':step.title}</span></strong><small className={drafts[step.id]?'needs-attention':''}>{stepStatus(step.id)}</small></span>{drafts[step.id]&&<span className="setup-draft-dot" aria-label="Есть неприменённые изменения"/>}</button>)}</div>
        <div className="setup-panes">
          {steps.map(step=><section className="setup-pane" role="tabpanel" id={'setup-pane-'+step.id} aria-labelledby={'setup-tab-'+step.id} hidden={activeStep!==step.id} key={step.id}>
            <div className="setup-pane-heading"><h3>{step.heading}</h3><p>{step.description}</p></div>
            {step.id==='data'?<>
              <div className="setup-basics"><label className="field">Название анализа<input required maxLength={120} value={name} onChange={e=>setName(e.target.value)}/></label><label className="field">Датасет<select aria-label="Датасет" value={dataset} onChange={e=>setDataset(e.target.value)} required><option value="" disabled>Выберите датасет</option>{dataset&&!datasetOptions.some(d=>d.id===dataset)&&<option value={dataset}>Набор данных недоступен</option>}{datasetOptions.map(d=><option key={d.id} value={d.id}>{datasetLabel(d)}</option>)}</select></label></div>
              {chosenDataset&&<div className="setup-dataset"><span className="setup-dataset-icon"><Database size={23}/></span><div><strong>{datasetLabel(chosenDataset)}</strong><span>Ответы до и после изменения</span></div><div className="setup-dataset-count"><strong>{number(chosenDataset.pairs)}</strong><span>заявок</span></div></div>}
              <RunTemplates request={api} disabled={!payload||pending} onBusy={setTemplateBusy} snapshot={()=>{if(!payload||pending)throw new Error('Примените код и заполните настройки запуска.');return JSON.parse(payload);}} apply={spec=>{setProcedureSelection(spec.procedures||{});setAutoAi(!!spec.auto_ai);setName(spec.name);setDataset(spec.dataset);setText(JSON.stringify(spec.config,null,2));setSelection({builtin_checks:spec.builtin_checks,user_checks:spec.user_checks});setError('');}}/>
            </>:step.id==='checks'?<ChecksSetup request={api} value={selection} onChange={setSelection} onPending={v=>markPending('checks',v)}>{config.data&&stageOptions('checks','Параметры штатных проверок')}</ChecksSetup>:<StageSetup stage={step.id as 'normalization'|'comparison'|'aggregation'} request={api} value={procedureSelection[step.id]} onChange={value=>setProcedureSelection(prev=>{const next={...prev};if(value)next[step.id]=value;else delete next[step.id];return next;})} onPending={v=>markPending(step.id,v)}>{config.data&&step.id==='normalization'&&stageOptions('normalization','Поля и правила нормализации')}{config.data&&step.id==='aggregation'&&stageOptions('aggregation','Группировка числовых значений')}</StageSetup>}
          </section>)}
        </div>
      </div>
      {(pending||preflightError||error||config.error||available.error||warnings.length>0)&&<div className="setup-feedback">
        {pending&&<div className="setup-pending" role="status"><span>Завершите настройку выбранной процедуры.</span><button type="button" className="text-button" onClick={()=>setActiveStep(Object.keys(drafts).find(id=>drafts[id])||'data')}>Перейти к изменениям <ArrowRight size={16}/></button></div>}
        {preflightError&&<div className="error-box" role="alert">{preflightError}<button type="button" className="text-button" onClick={()=>setAttempt(n=>n+1)}>Проверить ещё раз</button></div>}
        {!!warnings.length&&<details><summary>Замечания к коду ({warnings.length})</summary><ul>{warnings.map((w,i)=><li key={i}>{w}</li>)}</ul></details>}
        <ErrorBox message={error||config.error||available.error}/>
      </div>}
      <div className="modal-footer"><div className="setup-step-actions">{currentIndex>0&&<button type="button" className="text-button" onClick={()=>setActiveStep(steps[currentIndex-1].id)}><ArrowLeft size={17}/>Назад</button>}{currentIndex<steps.length-1&&<button type="button" className="button secondary" onClick={()=>setActiveStep(steps[currentIndex+1].id)}>Далее <ArrowRight size={17}/></button>}</div><label className="setup-ai-toggle"><input type="checkbox" checked={autoAi} onChange={e=>setAutoAi(e.target.checked)}/><span>Мнение ИИ после расчёта</span></label><Button type="submit" disabled={!ready}>{busy||(!pending&&!!payload&&payload!==validated&&!preflightError)?<Loader2 size={16} className="spin"/>:<Play size={16}/>}Запустить</Button></div>
    </fieldset></form>
  </section></div>;
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate>
      {(user, logout) => <App key={`${user.id}:${user.role}`} user={user} onLogout={logout} />}
    </AuthGate>
  </React.StrictMode>,
);
