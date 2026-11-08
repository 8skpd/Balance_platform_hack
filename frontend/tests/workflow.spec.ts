import {provisionAccount} from './test-accounts';
import {openTool,openMode,reveal} from './ui-navigation';
import { test, expect } from "@playwright/test";

test("real dataset: create run, compare, drill down, checks, export and mobile", async ({
  page,
}) => {
  const request = page.request;
  const account = await provisionAccount(request,{
    data: {
      username: `workflow-${Date.now()}`,
      display_name: "Проверка сценария",
      password: "Workflow-test-password-42",
    },
  });
  expect(account.status()).toBe(201);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 500)
      errors.push(`${response.status()} ${response.url()}`);
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Анализы" }),
  ).toBeVisible();
  await page.screenshot({ path: "../artifacts/overview.png", fullPage: true });
  await page.getByRole("button", { name: "Новый запуск", exact: true }).click();
  await page.getByLabel("Название запуска").fill("Проверка рабочего сценария");
  await page.getByRole("button", { name: "Запустить", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Проверка рабочего сценария" }),
  ).toBeVisible();
  const runId = new URL(page.url()).searchParams.get("run")!;
  await page.reload();
  await expect
    .poll(
      async () => {
        const response = await request.get(`/api/runs/${runId}`);
        const run = await response.json();
        if (run.status === "failed") throw new Error(run.error);
        return run.status;
      },
      { timeout: 150000, intervals: [2000] },
    )
    .toBe("completed");
  await expect(
    page.getByRole("button", { name: "Сводная таблица", exact: true }),
  ).toBeVisible({ timeout: 10000 });
  await page.screenshot({
    path: "../artifacts/run-overview.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Сводная таблица", exact: true })
    .click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.getByLabel("Группировать по").selectOption("CLASS");
  await page.getByRole("button",{name:"Построить сводную",exact:true}).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  await page.getByLabel("Группировать по").selectOption("PATH");
  await page.getByRole("button",{name:"Построить сводную",exact:true}).click();
  await page.locator("tbody .number-link").first().click();
  await expect(
    page.getByRole("heading", { name: "Заявки", exact: true }),
  ).toBeVisible();
  await page.locator("tbody .row-title").first().click();
  await expect(page.locator('.application-title')).toBeVisible();
  await expect(page.locator(".attribute-table tbody tr").first()).toBeVisible();
  await page
    .getByRole("button", { name: "Исходный JSON", exact: true })
    .click();
  await expect(page.locator(".raw-panel pre").first()).toContainText("appData");
  await page.screenshot({
    path: "../artifacts/application.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Проверки", exact: true }).click();
  await expect(page.locator("tbody tr").first()).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "HTML-отчёт" }).click();
  expect((await download).suggestedFilename()).toContain("report-");
  const exported = await request.get(`/api/runs/${runId}/report.html`);
  expect(await exported.text()).toContain(`?run=${runId}`);
  await openMode(page,'Редактор Python');
  await expect(page.getByLabel('Код Python')).toBeVisible();
  const submit = page.waitForResponse(r=>r.url().endsWith('/python-checks') && r.request().method()==='POST');
  await page.getByRole('button', {name:'Запустить проверку',exact:true}).click();
  const pythonJob = await (await submit).json();
  await expect.poll(async()=> (await (await request.get('/api/jobs/'+pythonJob.id)).json()).status,
    {timeout:30000,intervals:[1000]}).toBe('completed');
  await page.locator(`[data-job-id="${pythonJob.id}"]`).getByRole('button', {name:'Python-проверка',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Результат проверки',exact:true})).toBeVisible();
  await expect(page.locator('.job-detail')).toContainText('502 / 2002');
  await page.screenshot({path:'../artifacts/python-check.png',fullPage:true});
  await page.getByLabel('Название для библиотеки').fill('Проверка из редактора');
  const saveLibrary=page.waitForResponse(r=>r.url().endsWith('/api/check-library')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Сохранить в библиотеку',exact:true}).click();
  expect((await saveLibrary).status()).toBe(201);
  expect((await (await request.get('/api/check-library')).json())[0].name).toBe('Проверка из редактора');
  await page.getByLabel('Код Python').fill('while True: pass');
  await page.getByLabel('Лимит времени').fill('1');
  const timeoutResponse = page.waitForResponse(r=>r.url().endsWith('/python-checks') && r.request().method()==='POST');
  await page.getByRole('button',{name:'Запустить проверку',exact:true}).click();
  const timedJob = await (await timeoutResponse).json();
  await expect.poll(async()=> (await (await request.get('/api/jobs/'+timedJob.id)).json()).status,
    {timeout:15000,intervals:[1000]}).toBe('timed_out');
  await page.getByLabel('Лимит времени').fill('20');
  const runningResponse = page.waitForResponse(r=>r.url().endsWith('/python-checks') && r.request().method()==='POST');
  await page.getByRole('button',{name:'Запустить проверку',exact:true}).click();
  const runningJob = await (await runningResponse).json();
  await expect.poll(async()=> (await (await request.get('/api/jobs/'+runningJob.id)).json()).status,
    {timeout:15000,intervals:[500]}).toBe('running');
  await openTool(page,'Задачи');
  const row = page.locator('tr').filter({hasText:runningJob.id.slice(0,8)});
  await row.getByRole('button',{name:'Отменить',exact:true}).click();
  await expect(row).toContainText('Отменена',{timeout:10000});
  const retryResponse = page.waitForResponse(r=>r.url().endsWith('/retry'));
  await row.getByRole('button',{name:'Повторить',exact:true}).click();
  const retryJob = await (await retryResponse).json();
  expect(retryJob.id).not.toBe(runningJob.id);
  await request.post('/api/jobs/'+retryJob.id+'/cancel');
  await page.screenshot({path:'../artifacts/jobs.png',fullPage:true});
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "Анализы" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "../artifacts/mobile.png", fullPage: true });
  expect(errors).toEqual([]);
});
