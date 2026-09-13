import { test, expect } from "@playwright/test";

const baseUrl = (process.env.E2E_BASE_URL || "").replace(/\/$/, "");
const mode = process.env.E2E_MODE || "unknown";

test.describe("发布核心只读 E2E", () => {
  test.beforeAll(() => {
    if (!baseUrl) {
      throw new Error("缺少 E2E_BASE_URL");
    }
  });

  test("健康接口和公开配置可用", async ({ request }) => {
    await expect((await request.get(`${baseUrl}/livez`)).status()).toBe(200);
    await expect((await request.get(`${baseUrl}/readyz`)).status()).toBe(200);
    await expect((await request.get(`${baseUrl}/api/public/config`)).status()).toBe(200);
    await expect((await request.get(`${baseUrl}/api/public/tools`)).status()).toBe(200);
  });

  test("未认证请求不会获得用户数据", async ({ request }) => {
    await expect((await request.get(`${baseUrl}/api/me`)).status()).toBe(401);
  });

  test("登录页可以加载", async ({ page }) => {
    const response = await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    expect(response?.status(), `E2E 模式 ${mode} 的登录页状态`).toBe(200);
    await expect(page.locator("body")).toBeVisible();
  });

  test("提供只读会话时验证用户接口", async ({ request }) => {
    const cookie = process.env.E2E_SESSION_COOKIE;
    test.skip(!cookie, "未提供 E2E_SESSION_COOKIE，仅执行未认证路径");
    const response = await request.get(`${baseUrl}/api/me`, { headers: { Cookie: cookie || "" } });
    await expect(response.status()).toBe(200);
  });
});
