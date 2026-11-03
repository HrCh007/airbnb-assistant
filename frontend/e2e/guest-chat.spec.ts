import { expect, test, type Page } from "@playwright/test";

async function ask(page: Page, question: string) {
  const before = await page.getByTestId("assistant-message").count();
  await page.locator("#chat-input").fill(question);
  await page.locator("#chat-input").press("Enter");
  await expect(page.getByTestId("assistant-message")).toHaveCount(before + 1);
  const last = page.getByTestId("assistant-message").last();
  await expect(last.getByTestId("assistant-text")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("status", { name: "Assistant is typing" })).toHaveCount(0);
  return last;
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.reload();
});

test("definition of done: one continuous conversation in Saket", async ({ page }) => {
  await expect(page.getByText("Which neighbourhood are you staying in?")).toBeVisible();
  for (const loc of ["dwarka", "janakpuri", "saket"]) await expect(page.getByTestId(`location-${loc}`)).toBeVisible();

  await page.getByTestId("location-saket").click();
  await expect(page.getByTestId("property-ready")).toContainText("Demo Property – Saket");
  await expect(page.getByTestId("header-context")).toContainText("Demo Property – Saket");

  let msg = await ask(page, "What's the Wi-Fi password?");
  await expect(msg).toContainText("saket-demo-2468");

  msg = await ask(page, "Suggest some nice places for dinner nearby.");
  await expect(msg.getByTestId("place-card").first()).toBeVisible();
  await expect(msg.getByTestId("place-card").first()).toContainText(/away/);

  msg = await ask(page, "Anything more budget-friendly?");
  await expect(msg.getByTestId("place-card").first()).toBeVisible();

  msg = await ask(page, "What about a nice place for drinks and dancing?");
  await expect(msg.getByTestId("place-card").first()).toBeVisible();

  msg = await ask(page, "What's the checkout time tomorrow?");
  await expect(msg).toContainText("11:00 AM");

  // Conversation survives a reload (session restored from the backend).
  await page.reload();
  await expect(page.getByTestId("assistant-message")).toHaveCount(5);
});

test("quick actions, suggestions and switching property", async ({ page }) => {
  await page.getByTestId("location-dwarka").click();
  await expect(page.getByTestId("property-ready")).toContainText("Dwarka");
  await page.getByRole("button", { name: "Property info" }).click();
  await expect(page.getByTestId("assistant-message").last().getByTestId("assistant-text")).toBeVisible();

  const msg = await ask(page, "Suggest some restaurants");
  await msg.getByRole("button", { name: "Anything more budget-friendly?" }).click();
  await expect(page.getByTestId("assistant-message")).toHaveCount(3);

  await page.getByTestId("change-location").click();
  await page.getByTestId("location-saket").click();
  await expect(page.getByTestId("header-context")).toContainText("Saket");
  const wifi = await ask(page, "What's the Wi-Fi password?");
  await expect(wifi).toContainText("saket-demo-2468");
  await expect(wifi).not.toContainText("dwarka-demo-1357");
});

test("guest PIN gates sensitive details", async ({ page }) => {
  await page.getByTestId("location-janakpuri").click();
  let msg = await ask(page, "What's the Wi-Fi password?");
  await expect(msg).not.toContainText("janak-demo-9753");
  await expect(msg).toContainText("guest PIN");
  await page.getByRole("button", { name: "Verify booking PIN" }).click();
  await page.getByLabel("Guest PIN").fill("2468");
  await page.getByRole("button", { name: "Unlock" }).click();
  await expect(page.getByRole("button", { name: "Booking verified" })).toBeVisible();
  msg = await ask(page, "What's the Wi-Fi password?");
  await expect(msg).toContainText("janak-demo-9753");
});

test("clear conversation", async ({ page }) => {
  await page.getByTestId("location-saket").click();
  await ask(page, "Hello");
  await page.getByRole("button", { name: "Clear conversation" }).click();
  await expect(page.getByTestId("assistant-message")).toHaveCount(0);
});
