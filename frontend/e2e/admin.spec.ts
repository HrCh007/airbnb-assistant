import path from "node:path";
import { expect, test } from "@playwright/test";

const EMAIL = process.env.E2E_ADMIN_EMAIL || "admin@example.com";
const PASSWORD = process.env.E2E_ADMIN_PASSWORD || "ChangeMe-Dev-2026!";

test.describe("admin console", () => {
  test.skip(({ isMobile }) => isMobile, "admin flows are exercised on desktop");

  test("login, edit a property, upload and remove a document", async ({ page }) => {
    await page.goto("/admin");
    await expect(page).toHaveURL(/\/admin\/login/);
    await page.getByLabel("Email").fill(EMAIL);
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Invalid email or password.")).toBeVisible();
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Active properties")).toBeVisible();

    await page.getByRole("link", { name: "Properties", exact: true }).click();
    await page.getByTestId("admin-property").filter({ hasText: "Demo Property – Dwarka" }).click();
    await expect(page.locator("#name")).toHaveValue("Demo Property – Dwarka");

    // Edit a structured field and save.
    await page.locator("#facilities-housekeeping").fill("E2E: daily housekeeping 10 AM - 12 PM (fictional)");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByText("Saved. The property profile was re-indexed")).toBeVisible();

    // Upload a document.
    await page.getByRole("tab", { name: "Documents" }).click();
    const file = path.join(__dirname, "fixtures", "e2e-notes.md");
    await page.getByTestId("file-input").setInputFiles(file);
    const row = page.getByTestId("document-row").filter({ hasText: "e2e-notes.md" });
    await expect(row).toContainText("ready", { timeout: 20_000 });

    page.on("dialog", (d) => d.accept());
    await row.getByTitle("Remove").click();
    await expect(page.getByTestId("document-row").filter({ hasText: "e2e-notes.md" })).toHaveCount(0);
  });
});
