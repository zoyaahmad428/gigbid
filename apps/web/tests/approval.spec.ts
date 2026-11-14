// Approval UI tests. The API is mocked with a real pending-approval payload captured from a run
// (tests/fixtures/run_pending.json), so these run without GigSim, the API or an LLM key.
import { expect, test, type Page, type Route } from "@playwright/test";
import fs from "fs";

const run = JSON.parse(fs.readFileSync(new URL("./fixtures/run_pending.json", import.meta.url), "utf8"));
const TID = run.run.thread_id as string;
const RUN_URL = `/runs/${encodeURIComponent(TID)}`;
const user = {
  user_id: "u_test", email: "t@example.com", onboarded: true,
  settings: { persona: "ai_ml", name: "Sana Qureshi", hourly_rate: 55, min_fixed_budget: 100, marketplace_token: "x" },
};

async function mockApi(page: Page, decisions: any[]) {
  await page.addInitScript(() => localStorage.setItem("gigbid.token", "test-token"));
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const p = url.pathname.replace(/^\/api/, "");
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (p === "/me") return json(user);
    if (p === "/approvals") return json({ runs: [run.run] });
    if (p === "/account") return json({ connects_balance: 80, weekly_allowance: 80 });
    if (p.endsWith("/stream"))
      return route.fulfill({ status: 200, contentType: "text/event-stream",
                             body: `event: end\ndata: {"status":"awaiting_approval"}\n\n` });
    if (p.endsWith("/decision")) {
      decisions.push(route.request().postDataJSON());
      return json({ thread_id: TID, status: "running" }, 202);
    }
    if (p === `/runs/${encodeURIComponent(TID)}` || p === `/runs/${TID}`) return json(run);
    return json({ detail: `unmocked ${p}` }, 404);
  });
}

test("pending approval is rebuilt from the server after a reload", async ({ page }) => {
  await mockApi(page, []);
  await page.goto("/approvals");
  await expect(page.getByText("Looking for Full Stack Developer")).toBeVisible();
  await page.getByRole("link", { name: "Looking for Full Stack Developer" }).click();
  await expect(page.getByText("Ready for your review.")).toBeVisible();
  await page.reload();
  await expect(page.getByText("Ready for your review.")).toBeVisible();
});

test("approve shows exactly what is sent and the Connects cost before sending", async ({ page }) => {
  const sent: any[] = [];
  await mockApi(page, sent);
  await page.goto(RUN_URL);
  await page.getByRole("button", { name: "Approve and send…" }).click();
  const dialog = page.getByRole("dialog", { name: "Confirm sending" });
  await expect(dialog).toContainText("Spends 12 Connects");
  await expect(dialog).toContainText("balance after: 68");
  expect(sent).toHaveLength(0);                       // nothing is sent until the second click
  await dialog.getByRole("button", { name: "Yes, send it" }).click();
  await expect.poll(() => sent).toEqual([{ action: "approve" }]);
});

test("reject needs a reason", async ({ page }) => {
  const sent: any[] = [];
  await mockApi(page, sent);
  await page.goto(RUN_URL);
  await page.getByRole("button", { name: "Reject" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Reject" });
  await expect(dialog.getByRole("button", { name: "Reject" })).toBeDisabled();
  await dialog.getByRole("textbox").fill("budget too low");
  await dialog.getByRole("button", { name: "Reject" }).click();
  await expect.poll(() => sent).toEqual([{ action: "reject", reason: "budget too low" }]);
});

test("edit sends the new letter and bid back for re-checking", async ({ page }) => {
  const sent: any[] = [];
  await mockApi(page, sent);
  await page.goto(RUN_URL);
  await page.getByRole("button", { name: "Edit" }).click();
  await page.locator("textarea.letter-edit").fill("Hello, a shorter letter [ev_b83609f70b].");
  await page.getByRole("spinbutton").fill("60");
  await page.getByRole("button", { name: "Save edit and re-check" }).click();
  await expect.poll(() => sent).toEqual([{ action: "edit", edited_text: "Hello, a shorter letter [ev_b83609f70b].", edited_bid: 60 }]);
});

test("claims are highlighted and citations open the evidence", async ({ page }) => {
  await mockApi(page, []);
  await page.goto(RUN_URL);
  await expect(page.locator(".letter .hl.supported").first()).toBeVisible();
  await page.locator(".letter .chip", { hasText: "ev_b83609f70b" }).first().click();
  await expect(page.getByText("Tool-using LLM agent for e-commerce operations staff").first()).toBeVisible();
});

test("phone width has no sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, []);
  await page.goto(RUN_URL);
  await expect(page.getByText("Ready for your review.")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});
