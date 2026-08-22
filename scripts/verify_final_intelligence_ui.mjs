// Final Intelligence phase: real browser verification (Phase R) — logs in,
// visits a real completed session with events+incident and another with
// events-only-zero-incidents, screenshots the new Analysis Report /
// Operator Copilot UI, and exercises one real Copilot question end-to-end.
import { chromium } from "playwright";

const BASE_URL = "http://localhost:3000";
const OUT_DIR = "C:/Dev/Crowdshield/scratchpad_ui_verification";
const SESSION_WITH_INCIDENT = "c0a0bb8e-3919-4122-a3d5-9953d763c489";
const SESSION_ZERO_INCIDENTS = "7ba81e1e-712b-4611-9477-62b1f8876b89";

async function main() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const logs = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") logs.push(`CONSOLE ERROR: ${msg.text()}`);
  });
  page.on("pageerror", (err) => logs.push(`PAGE ERROR: ${err.message}`));

  console.log("1. Navigating to /login...");
  await page.goto(`${BASE_URL}/login`);
  await page.fill('input[name="email"], input[type="email"]', "admin@crowdshield.dev");
  await page.fill('input[name="password"], input[type="password"]', "AdminPass123!");
  await page.click('button[type="submit"]');
  await page.waitForURL(/\/(dashboard)?$/, { timeout: 15000 }).catch(() => {});
  console.log("   Logged in, current URL:", page.url());

  console.log(`2. Visiting session with incident: ${SESSION_WITH_INCIDENT}`);
  await page.goto(`${BASE_URL}/dashboard?session=${SESSION_WITH_INCIDENT}`);
  await page.waitForSelector("text=Analysis Report", { timeout: 20000 });
  await page.waitForTimeout(2000); // let the report fetch settle
  await page.screenshot({ path: `${OUT_DIR}/1_report_with_incident_overview.png`, fullPage: true });
  console.log("   Screenshot 1 taken.");

  const overviewText = await page.locator("text=Overview").first().isVisible();
  const eventsHeading = await page.locator("text=/Detected Events/").first().textContent().catch(() => null);
  console.log("   Overview panel visible:", overviewText, "| Events heading:", eventsHeading);

  console.log("3. Scrolling to Operator Copilot and asking a real question...");
  await page.locator("text=CrowdShield Operator Copilot").scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${OUT_DIR}/2_copilot_panel_initial.png`, fullPage: false });

  const suggestionButtons = page.locator("button", { hasText: "most serious event" });
  const hasSuggestion = await suggestionButtons.count();
  console.log("   Suggested-question buttons found:", hasSuggestion);

  if (hasSuggestion > 0) {
    await suggestionButtons.first().click();
    console.log("   Clicked suggested question, waiting for real Ollama response (can take up to ~2 min)...");
    await page.waitForSelector("text=/Asking…/", { state: "hidden", timeout: 130000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `${OUT_DIR}/3_copilot_answer.png`, fullPage: false });
    console.log("   Screenshot 3 (copilot answer) taken.");
  } else {
    console.log("   WARNING: no suggested-question button found — skipping real Copilot call.");
  }

  console.log(`4. Visiting zero-incident session: ${SESSION_ZERO_INCIDENTS}`);
  await page.goto(`${BASE_URL}/dashboard?session=${SESSION_ZERO_INCIDENTS}`);
  await page.waitForSelector("text=Analysis Report", { timeout: 20000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT_DIR}/4_zero_incident_session.png`, fullPage: true });
  const incidentsSummary = await page.locator("text=/No event met the evidence threshold|No events were investigated/").first().textContent().catch(() => null);
  console.log("   Zero-incident summary text found:", incidentsSummary);

  console.log("5. Testing heatmap fullscreen click...");
  await page.goto(`${BASE_URL}/dashboard?session=${SESSION_WITH_INCIDENT}`);
  await page.waitForSelector("text=Heatmap", { timeout: 20000 });
  await page.waitForTimeout(1500);
  const heatmapImgButton = page.locator('button[aria-label="Open heatmap fullscreen"]');
  if (await heatmapImgButton.count() > 0) {
    await heatmapImgButton.first().click();
    await page.waitForTimeout(500);
    await page.screenshot({ path: `${OUT_DIR}/5_heatmap_fullscreen.png`, fullPage: false });
    console.log("   Screenshot 5 (heatmap fullscreen) taken.");
  } else {
    console.log("   No heatmap image available to click (no snapshot yet) — skipping.");
  }

  console.log("\n=== Console/page errors observed ===");
  console.log(logs.length ? logs.join("\n") : "(none)");

  await browser.close();
  console.log("\nDone.");
}

main().catch((err) => {
  console.error("FATAL:", err);
  process.exit(1);
});
