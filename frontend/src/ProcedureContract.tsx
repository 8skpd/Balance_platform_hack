import {useEffect,useRef} from 'react';
import {FileCode2,TriangleAlert} from 'lucide-react';
import './contracts.css';
type Field=[string,string,string];
function Fields({rows,methods=false}:{rows:Field[];methods?:boolean}) {
 return <table className="contract-fields"><thead><tr><th>{methods?"Метод":"Поле"}</th><th>{methods?"Возвращает":"Тип"}</th><th>Описание</th></tr></thead><tbody>{rows.map(([name,type,description])=><tr key={name}><td><code>{name}</code></td><td><code>{type}</code></td><td>{description}</td></tr>)}</tbody></table>;
}
export function CheckContract({insert}:{insert?:()=>void}) {
 return <details className="procedure-contract"><summary>Контракт проверки</summary><div className="contract-document">
  <section><h3>Точка входа</h3><pre><code>{'run(mart, params) -> list[dict]\n\nCHECK = {"entrypoint": run}'}</code></pre></section>
  <section><h3>Входные данные</h3><Fields rows={[
   ['mart','Mart','Данные готового анализа: сравнение атрибутов и разрезы заявок.'],
   ['params','dict','Параметры проверки из конфигурации запуска.'],
  ]}/></section>
  <section><h3>Результат</h3><p>Функция возвращает список словарей. Один словарь — одна строка в отчёте проверок. Пустой список означает отсутствие результатов, а не PASS.</p><Fields rows={[
   ['LABEL','str','Название результата проверки, до 2 000 символов.'],['ARGUMENT','str','Группа или условие, которое проверяли, до 2 000 символов.'],
   ['SOURCE','str','Срез данных: BEFORE или AFTER.'],['CNT','int','Количество заявок, соответствующих условию.'],
   ['TOTAL','int','Всего заявок в проверяемой группе.'],['METRIC','int | float','Конечное число: например, доля изменений. NaN и бесконечность недопустимы.'],
   ['RESULT','str','PASS — условие выполнено; FAIL — обнаружена проблема.'],
   ['CONFIDENCE','str','HIGH — достаточно наблюдений; LOW — слабый сигнал.'],
   ['APP_IDS','list[str]','Идентификаторы заявок этого анализа для перехода из результата к деталям.'],
  ]}/><p className="contract-rule">0 ≤ CNT ≤ TOTAL ≤ 10¹². APP_IDS не содержит повторов; его длина не превышает CNT. Для полной детализации верните все идентификаторы группы. Верните не более 1 000 строк.</p></section>
  <section><h3>Доступ к данным</h3><Fields methods rows={[
   ['mart.rows(**filters)','list[dict]','Строки сравнения атрибутов, соответствующие фильтрам.'],
   ['mart.count(**filters)','int','Количество строк сравнения. Одна заявка может содержать несколько строк.'],
   ['mart.app_ids(**filters)','list[str]','Уникальные идентификаторы заявок по фильтрам.'],
   ['mart.segments()','list[dict]','Решения и разрезы всех заявок.'],
   ['mart.product_segments()','list[dict]','Контекст продуктов по заявкам. Доступен в отчётах, содержащих PRODUCT_CONTEXT.'],
   ['mart.total_apps()','int','Общее количество заявок в анализе.'],
  ]}/><p className="contract-rule">Фильтры для rows, count и app_ids: <code>path</code>, <code>path_prefix</code>, <code>key</code>, <code>array_key</code>, <code>diff_flag</code>, <code>change_type</code>, <code>value_before</code>, <code>value_after</code>, <code>app_ids</code>.</p></section>
  {insert&&<section className="contract-example"><h3>Стандартный пример</h3><p>Проверка доли заявок с изменениями. Порог задаётся параметром <code>max_share</code>.</p><button type="button" className="button secondary contract-insert" onClick={insert}><FileCode2 size={17}/>Вставить стандартный пример</button></section>}
 </div></details>;
}
const identity:Field[]=[['APP_ID','str','Идентификатор заявки.'],['PATH, ARRAY_KEY','str','Путь атрибута и ключ элемента массива. Вместе с APP_ID определяют сравниваемый атрибут.'],['PREFIX, CLASS, SUFFIX, KEY','str','Структурные части пути для группировки в отчёте.']];
const changes:Field[]=[['CHANGE_TYPE','str','SAME — без изменений; VALUE_CHANGED — изменилось значение; ATTRIBUTE_NEW — атрибут появился; ATTRIBUTE_LOST — исчез.'],['DIFF_FLAG','int','0 для SAME, 1 для остальных типов изменений.']];
export function ProcedureContract({stage,insert}:{stage:string;insert:()=>void}) {
 return <details className="procedure-contract"><summary>Контракт этапа</summary><div className="contract-document">
  <section><h3>Точка входа</h3><pre><code>{stage==='normalization'?'run(document, app_id, source, config, params)\nsegments(app_id, before, after, config, params)\n\nPROCEDURE = {"entrypoint": run, "segments": segments}':'run(data, params)\n\nPROCEDURE = {"entrypoint": run}'}</code></pre></section>
  <section><h3>Входные данные</h3>{stage==='normalization'?<Fields rows={[
   ['document','dict','Документ одного среза заявки. Функция run вызывается отдельно для BEFORE и AFTER.'],
   ['app_id','str','Идентификатор текущей заявки.'],['source','str','Срез: BEFORE или AFTER.'],
   ['config','dict','Правила нормализации: исключения, массивы, интервалы и структурные колонки.'],['params','dict','Параметры процедуры, выбранные для запуска.'],
  ]}/>:<Fields rows={[
   ['data','StageData',stage==='comparison'?'data.normalized() — строки нормализации; data.segments() — разрезы заявок.':'data.normalized() — строки нормализации; data.segments() — разрезы заявок; data.comparison() — результат сравнения.'],
   ['params','dict','Параметры процедуры, выбранные для запуска.'],
  ]}/>}</section>
  <section><h3>Результат</h3><p>Список или генератор словарей. Каждый словарь содержит поля:</p>
   {stage==='normalization'?<><Fields rows={[...identity,['SOURCE','str','Тот же срез, который передан в source.'],['VALUE','str','Исходное значение в строковом виде. Для null — строка NULL.'],['VALUE_TYPE','str','Тип исходного значения: string, number, boolean или null.'],['ADJUSTED_VALUE','str','Значение или интервал для группировки.']]}/><p className="contract-rule">APP_ID и SOURCE сохраняются. Пара PATH + ARRAY_KEY уникальна внутри заявки и среза.</p></>:stage==='comparison'?<><Fields rows={[...identity,['VALUE_BEFORE, VALUE_AFTER','str | None','Значения до и после. None, если атрибут отсутствует в срезе.'],['TYPE_BEFORE, TYPE_AFTER','str | None','Типы значений до и после.'],['ADJUSTED_BEFORE, ADJUSTED_AFTER','str | None','Значения или интервалы для группировки до и после.'],...changes]}/><p className="contract-rule">Верните все уникальные ключи APP_ID + PATH + ARRAY_KEY из объединения обоих срезов, без пропусков и добавления посторонних атрибутов.</p></>:<><Fields rows={[
    ['PATH, ARRAY_KEY','str','Атрибут и ключ элемента массива.'],['PREFIX, CLASS, SUFFIX, KEY','str','Структурные части пути для группировки.'],['SOURCE','str','Срез: BEFORE или AFTER.'],['ADJUSTED_VALUE','str','Значение или интервал группы.'],...changes,['CNT','int','Количество уникальных заявок в группе.'],['APP_IDS','list[str]','Полный список идентификаторов заявок группы для перехода к деталям.'],
   ]}/><p className="contract-rule">CNT = len(APP_IDS). Идентификаторы не повторяются и относятся к указанным атрибуту и срезу. Признаки изменений соответствуют сравнению.</p></>}
  </section>
  {stage==='normalization'&&<section><h3>Разрезы заявки: segments</h3><p>Вызывается один раз для пары документов. before и after — исходные документы двух срезов. Возвращает один словарь:</p><Fields rows={[
   ['APP_ID, DECISION_CHANGED','str, int','Идентификатор заявки и признак смены решения: 0 или 1.'],
   ['DECISION, PD_MODEL, TOP_OFFER, CUTOFF, REJECT_REASON, SCORE_OKB','str | None','Для каждого имени нужны два поля: с суффиксами _BEFORE и _AFTER. Неизвестные значения — None.'],
   ['PRODUCTS, PARTNER_TYPE, ROUTING_METHOD','str | None','Продукты, тип партнёра и способ маршрутизации. PRODUCTS — идентификаторы через точку с запятой.'],
   ['PRODUCT_CONTEXT','str','Необязательно: JSON-строка массива контекстов продуктов.'],
  ]}/></section>}
  <section><h3>Стандартная реализация</h3><p>{stage==='normalization'?<><code>defaults.normalize(document, app_id, source, config)</code> формирует строки нормализации; <code>defaults.segments(app_id, before, after)</code> — разрезы заявки.</>:stage==='comparison'?<><code>data.default_comparison()</code> возвращает поток строк стандартного сравнения.</>:<><code>data.default_facts()</code> возвращает поток готовых фактов с CNT и APP_IDS.</>}</p><button type="button" className="button secondary contract-insert" onClick={insert}><FileCode2 size={17}/>Вставить стандартный пример</button></section>
 </div></details>;
}
export function ReplaceCodeDialog({cancel,replace}:{cancel:()=>void;replace:()=>void}) {
 const dialog=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const el=dialog.current!;el.showModal();return()=>el.close();},[]);
 return <dialog ref={dialog} className="replace-code-dialog" aria-labelledby="replace-code-title" aria-describedby="replace-code-description" onCancel={e=>{e.preventDefault();cancel();}}>
  <div className="replace-code-heading"><TriangleAlert size={22}/><h2 id="replace-code-title">Заменить текущий код?</h2></div>
  <p id="replace-code-description">Текущий код будет полностью заменён стандартным примером. Несохранённые изменения будут потеряны.</p>
  <div className="replace-code-actions"><button type="button" className="button secondary" autoFocus onClick={cancel}>Отмена</button><button type="button" className="button" onClick={replace}>Заменить код</button></div>
 </dialog>;
}
