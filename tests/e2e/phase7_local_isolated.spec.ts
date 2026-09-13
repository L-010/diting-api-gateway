import { expect, test, type Page } from "@playwright/test";

const backendOrigin = process.env.E2E_BACKEND_ORIGIN || "http://127.0.0.1:8000";
const userPassword = process.env.E2E_USER_PASSWORD || "";
const adminPassword = process.env.E2E_ADMIN_PASSWORD || "";
const scopedKey = process.env.E2E_SCOPE_KEY || "";
const syntheticUser = "phase7-user";

async function login(page: Page, username: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录" }).click();
}

test.describe.configure({ mode: "serial" });

test("登录接口 4xx/5xx 会显示错误且网络中断不会卡住提交", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("synthetic-user");
  await page.getByLabel("密码").fill("synthetic-password");
  let calls = 0;
  await page.route("**/api/auth/login", async (route) => {
    calls += 1;
    if (calls === 1) {
      await route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ code: "ACCOUNT_LOCKED", message: "账号暂时锁定" }) });
      return;
    }
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.locator(".account-state-message strong")).toBeVisible();
  await page.getByRole("button", { name: "登录" }).click();
  await expect(page.getByRole("button", { name: "登录" })).toBeEnabled();
  expect(calls).toBe(2);
});

test("重复点击登录提交只发送一次请求并在 5xx 后恢复", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("用户名").fill("synthetic-user");
  await page.getByLabel("密码").fill("synthetic-password");
  let calls = 0;
  await page.route("**/api/auth/login", async (route) => {
    calls += 1;
    await new Promise((resolve) => setTimeout(resolve, 150));
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "服务暂不可用" }) });
  });
  const submit = page.getByRole("button", { name: "登录" });
  await Promise.all([submit.click(), submit.click()]);
  await expect(submit).toBeEnabled();
  expect(calls).toBe(1);
});

test("匿名用户可以在浏览器中提交合成注册申请", async ({ page }) => {
  test.skip(!userPassword, "隔离执行器未提供合成注册凭据");
  await page.goto("/register");
  await page.locator('input[name="username"]').fill("phase7-register");
  await page.locator('input[name="display_name"]').fill("阶段七合成用户");
  await page.locator('input[name="email"]').first().fill("phase7-register@example.test");
  await page.locator('input[name="password"]').fill(userPassword);
  await page.locator('textarea[name="registration_note"]').fill("仅用于本地隔离浏览器验证。");
  await page.getByRole("button", { name: "提交申请" }).click();
  await expect(page.getByText("验证邮件已发送至你填写的邮箱。").first()).toBeVisible();
});

test("管理员可以在浏览器中导入、决策并发布本地 OpenAPI", async ({ page }) => {
  test.skip(!adminPassword, "隔离执行器未提供合成管理员凭据");
  await login(page, "phase7-admin", adminPassword);
  await expect(page).toHaveURL(/\/admin$/);
  await page.goto("/admin/tools");
  await page.getByLabel("选择工具 本地演示工具").check();
  await page.getByRole("button", { name: "从上游同步差异" }).click();
  await expect(page.getByText("同步完成：发现").first()).toBeVisible();
  await page.getByRole("link", { name: "同步差异" }).click();
  await expect(page.getByRole("heading", { name: "OpenAPI 同步差异决策" })).toBeVisible();
  await page.getByRole("button", { name: "接受" }).first().click();
  await expect(page.getByText("差异状态已更新。").first()).toBeVisible();
  await page.getByRole("button", { name: "确认导入批次" }).click();
  await expect(page.getByText("导入批次已确认，可以一次性发布全部放行接口。").first()).toBeVisible();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "一键发布全部放行接口" }).click();
  await expect(page.getByText("已原子发布全部").first()).toBeVisible();
  await page.goto("/admin/monitor");
  await expect(page.getByRole("heading", { name: "调用监控与告警" })).toBeVisible();
  await page.goto("/admin/audit");
  await expect(page.getByRole("heading", { name: "审计日志" })).toBeVisible();
});

test("普通用户的登录、Key、Gateway、文件和会话流程在浏览器中受控", async ({ page }, testInfo) => {
  test.skip(!userPassword || !scopedKey, "隔离执行器未提供合成用户数据");
  await login(page, syntheticUser, userPassword);
  await expect(page).toHaveURL(/\/portal$/);
  await page.goto("/api-keys");
  await page.getByLabel("Key 标签").fill("浏览器本地验证 Key");
  await page.getByRole("button", { name: "生成并只显示一次" }).click();
  const key = (await page.locator(".key-secret").textContent())?.trim() || "";
  expect(key.startsWith("agw_")).toBeTruthy();
  await page.getByRole("button", { name: "我已保存，关闭" }).click();

  const idempotencyKey = `phase7-${Date.now()}`;
  const first = await page.request.post(`${backendOrigin}/gateway/demo/idempotent`, {
    headers: { "X-API-Key": key, "Idempotency-Key": idempotencyKey },
    data: { request: "same" },
  });
  const replay = await page.request.post(`${backendOrigin}/gateway/demo/idempotent`, {
    headers: { "X-API-Key": key, "Idempotency-Key": idempotencyKey },
    data: { request: "same" },
  });
  const failed = await page.request.post(`${backendOrigin}/gateway/demo/fail`, { headers: { "X-API-Key": key } });
  const denied = await page.request.post(`${backendOrigin}/gateway/demo/echo`, { headers: { "X-API-Key": scopedKey } });
  expect(first.status()).toBe(201);
  expect(replay.status()).toBe(201);
  expect(await replay.json()).toEqual(await first.json());
  expect(failed.status()).toBe(500);
  expect(denied.status()).toBe(403);

  await page.goto("/tasks");
  await expect(page.getByText("phase7-failed-task")).toBeVisible();
  await page.goto("/files");
  await expect(page.getByText("phase7-result.txt")).toBeVisible();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 phase7-result.txt" }).click();
  expect((await download).suggestedFilename()).toBe("phase7-result.txt");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除 phase7-result.txt" }).click();
  await expect(page.getByText("删除请求已提交").first()).toBeVisible();

  await page.goto("/api-keys");
  page.once("dialog", (dialog) => dialog.accept());
  await page.locator("tr", { hasText: "浏览器本地验证 Key" }).getByRole("button", { name: "禁用" }).click();
  await expect(page.getByText("已禁用", { exact: true }).first()).toBeVisible();
  const revoked = await page.request.post(`${backendOrigin}/gateway/demo/echo`, { headers: { "X-API-Key": key } });
  expect(revoked.status()).toBe(401);

  await page.goto("/admin");
  await expect(page).toHaveURL(/\/portal$/);
  await page.screenshot({ path: testInfo.outputPath("user-workflow.png"), fullPage: true });
  await page.getByRole("button", { name: "退出登录" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/portal");
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/admin");
  await expect(page).toHaveURL(/\/login/);
});

test("移动视口可以加载长列表页面且不产生横向页面溢出", async ({ browser }) => {
  test.skip(!userPassword, "隔离执行器未提供合成用户凭据");
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await context.newPage();
  await login(page, syntheticUser, userPassword);
  await page.goto("/calls?page=1&page_size=100");
  await expect(page.locator("body")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy();
  await page.goBack();
  await expect(page.locator("body")).toBeVisible();
  await context.close();
});
