import {provisionAccount} from './test-accounts';
import { test, expect, type Page } from "@playwright/test";

test("logout and account switch clear private data in every tab", async ({
  browser,
  baseURL,
}) => {
  const context = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { "X-Balance-Request": "1" },
  });
  const other = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { "X-Balance-Request": "1" },
  });
  const suffix = Date.now(),
    password = "Cross-tab-test-password-42",
    username = `tab-b-${suffix}`;
  expect(
    (
      await provisionAccount(context.request,{
        data: {
          username: `tab-a-${suffix}`,
          display_name: "Первый пользователь",
          password,
        },
      })
    ).status(),
  ).toBe(201);
  expect(
    (
      await provisionAccount(other.request,{
        data: { username, display_name: "Второй пользователь", password },
      })
    ).status(),
  ).toBe(201);
  const config = await (await context.request.get("/api/config")).json();
  config.check_params.max_drop_rel = 0.66;
  expect(
    (await context.request.put("/api/config", { data: config, headers:{"If-Match":(await context.request.get("/api/config")).headers()["etag"]} })).status(),
  ).toBe(200);
  const a = await context.newPage(),
    second = await context.newPage();
  await a.goto("/");
  await second.goto("/?view=settings");
  await expect(second.getByLabel("Порог падения одобрений", {exact:true})).toHaveValue("0.66");
  await a.getByRole("button", { name: "Меню профиля", exact: true }).click();
  await a.getByRole("menuitem", { name: "Выйти", exact: true }).click();
  await expect(
    second.getByRole("heading", { name: "Вход в систему" }),
  ).toBeVisible();
  await expect(second.getByLabel("Порог падения одобрений", {exact:true})).toHaveCount(0);
  await a.getByLabel("Логин", { exact: true }).fill(username);
  await a.getByLabel("Пароль", { exact: true }).fill(password);
  await a.getByRole("button", { name: "Войти", exact: true }).click();
  await second.getByRole("button", {name:"Меню профиля",exact:true}).click();
  await expect(second.getByRole("menu", {name:"Профиль",exact:true})).toContainText("Второй пользователь");
  await second.keyboard.press("Escape");
  await expect(second.getByLabel("Порог падения одобрений", {exact:true})).toHaveValue("0.1");
  await context.close();
  await other.close();
});

test("two accounts: separate configurations, concurrent runs, denied links and logout", async ({
  browser,
  baseURL,
}) => {
  const contextA = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { "X-Balance-Request": "1" },
  });
  const contextB = await browser.newContext({
    baseURL,
    extraHTTPHeaders: { "X-Balance-Request": "1" },
  });
  const a = await contextA.newPage(),
    b = await contextB.newPage();
  const suffix = Date.now();
  const alice = `alice-${suffix}`,
    bob = `bob-${suffix}`;
  const password = "Browser-only-test-password-42";

  async function register(page: Page, username: string, name: string) {
    expect((await provisionAccount(page.request,{data:{username,display_name:name,password}})).status()).toBe(201);
    await page.goto('/');
    await expect(page.locator('main')).toBeVisible();
  }
  await register(a, alice, "Алиса");
  await register(b, bob, "Борис");

  for (const [page, threshold] of [
    [a, 0.12],
    [b, 0.34],
  ] as const) {
    await page
      .getByRole("button", { name: "Конфигурация", exact: true })
      .click();
    await page.getByRole("button", {name:"JSON", exact:true}).click();
    const editor = page.getByLabel("JSON конфигурации");
    await expect(editor).toBeVisible();
    const config = JSON.parse(await editor.inputValue());
    config.check_params.max_drop_rel = threshold;
    await editor.fill(JSON.stringify(config, null, 2));
    await page.getByRole("button", { name: "Сохранить", exact: true }).click();
    await expect(
      page.getByRole("button", { name: "Сохранено", exact: true }),
    ).toBeVisible();
  }
  expect(
    (await (await a.request.get("/api/config")).json()).check_params
      .max_drop_rel,
  ).toBe(0.12);
  expect(
    (await (await b.request.get("/api/config")).json()).check_params
      .max_drop_rel,
  ).toBe(0.34);

  async function start(page: Page, name: string) {
    await page.getByRole("button", { name: "Обзор", exact: true }).click();
    await page
      .getByRole("button", { name: "Новый запуск", exact: true })
      .click();
    await page.getByLabel("Название запуска").fill(name);
    await page.getByRole("button", { name: "Запустить", exact: true }).click();
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
    return new URL(page.url()).searchParams.get("run")!;
  }
  const runA = await start(a, "Запуск Алисы");
  const runB = await start(b, "Запуск Бориса");
  expect(
    (await (await a.request.get("/api/runs")).json()).map((r: any) => r.id),
  ).toEqual([runA]);
  expect(
    (await (await b.request.get("/api/runs")).json()).map((r: any) => r.id),
  ).toEqual([runB]);
  for (const suffix of [
    "",
    "/pivot",
    "/applications",
    "/applications/A/source",
    "/report.html",
  ]) {
    expect((await b.request.get(`/api/runs/${runA}${suffix}`)).status()).toBe(
      404,
    );
  }
  expect((await b.request.post(`/api/runs/${runA}/ai`)).status()).toBe(404);
  await b.goto(`/?run=${runA}`);
  await expect(
    b.getByRole("heading", { name: "Отчёт недоступен" }),
  ).toBeVisible();
  await expect(
    b.getByRole("heading", { name: "Запуск Алисы", exact: true }),
  ).toHaveCount(0);
  await a.reload();
  await expect(
    a.getByRole("heading", { name: "Запуск Алисы", exact: true }),
  ).toBeVisible();
  await expect
    .poll(
      async () => {
        const runs = await Promise.all(
          [
            [a, runA],
            [b, runB],
          ].map(
            async ([p, id]) =>
              await (await (p as Page).request.get(`/api/runs/${id}`)).json(),
          ),
        );
        for (const run of runs)
          if (run.status === "failed") throw new Error(run.error);
        return runs.every((r) => r.status === "completed");
      },
      { timeout: 150000, intervals: [2000] },
    )
    .toBe(true);
  const resultA = await (await a.request.get(`/api/runs/${runA}`)).json();
  const resultB = await (await b.request.get(`/api/runs/${runB}`)).json();
  expect(resultA.owner_id).not.toBe(resultB.owner_id);
  expect(resultA.summary.total_apps).toBe(2002);
  expect(resultB.summary.total_apps).toBe(2002);
  expect(resultA.config.check_params.max_drop_rel).toBe(0.12);
  expect(resultB.config.check_params.max_drop_rel).toBe(0.34);
  await a.getByRole("button", { name: "Меню профиля", exact: true }).click();
  await a.getByRole("menuitem", { name: "Выйти", exact: true }).click();
  await expect(
    a.getByRole("heading", { name: "Вход в систему" }),
  ).toBeVisible();
  expect((await a.request.get(`/api/runs/${runA}`)).status()).toBe(401);
  await a.getByLabel("Логин", { exact: true }).fill(alice);
  await a.getByLabel("Пароль", { exact: true }).fill(password);
  await a.getByRole("button", { name: "Войти", exact: true }).click();
  await expect(
    a.getByRole("heading", { name: "Запуск Алисы", exact: true }),
  ).toBeVisible();
  await a.screenshot({
    path: "../artifacts/multiuser-report.png",
    fullPage: true,
  });
  await contextA.close();
  await contextB.close();
});
