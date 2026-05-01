const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const baseURL = process.env.BASE_URL || "http://127.0.0.1:8011";
const edgePath = process.env.EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const adminEmail = process.env.ADMIN_EMAIL || "";
const adminPassword = process.env.ADMIN_PASSWORD || "";
const outDir = path.resolve(__dirname, "..", "data", "playwright");
fs.mkdirSync(outDir, { recursive: true });

async function checkNoHorizontalOverflow(page, label) {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  assert.ok(
    metrics.scrollWidth <= metrics.clientWidth + 2,
    `${label} has horizontal overflow: ${metrics.scrollWidth} > ${metrics.clientWidth}`,
  );
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: edgePath,
  });
  const failures = [];

  async function run(name, fn) {
    try {
      await fn();
      console.log(`ok - ${name}`);
    } catch (error) {
      failures.push({ name, error });
      console.error(`not ok - ${name}`);
      console.error(error);
    }
  }

  await run("landing desktop SEO and content", async () => {
    const page = await browser.newPage({ viewport: { width: 1366, height: 900 } });
    await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });
    await assert.equal(await page.title(), "ApexGol AI | Plataforma");
    await assert.equal(await page.locator("meta[name='description']").count(), 1);
    await assert.equal(await page.locator("link[rel='canonical']").getAttribute("href"), "https://novo.tickpost.com.br/");
    await assert.ok((await page.locator("body").innerText()).includes("decisão"));
    await assert.equal(await page.locator("text=O que o cliente compra").count(), 1);
    await assert.equal(await page.locator("text=Agente com memória").count(), 1);
    await assert.equal(await page.locator("text=Build ").count(), 0);
    await checkNoHorizontalOverflow(page, "desktop");
    await page.screenshot({ path: path.join(outDir, "landing-desktop.png"), fullPage: true });
    await page.close();
  });

  await run("landing mobile layout has no clipped nav", async () => {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true });
    await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });
    await assert.equal(await page.locator("text=Dashboard Operacional").isVisible(), false);
    await checkNoHorizontalOverflow(page, "mobile");
    await page.screenshot({ path: path.join(outDir, "landing-mobile.png"), fullPage: true });
    await page.close();
  });

  await run("public support chat answers without login", async () => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const response = await page.request.post(`${baseURL}/api/support-chat`, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      data: { message: "como conecto meu Telegram?" },
    });
    assert.equal(response.status(), 200);
    const json = await response.json();
    assert.equal(json.ok, true);
    assert.match(json.answer, /Telegram|chatid|notifica/i);
    const planResponse = await page.request.post(`${baseURL}/api/support-chat`, {
      headers: { "X-Requested-With": "XMLHttpRequest" },
      data: { message: "no plano pro cada cliente tem seu proprio agente?" },
    });
    assert.equal(planResponse.status(), 200);
    const planJson = await planResponse.json();
    assert.equal(planJson.ok, true);
    assert.match(planJson.answer, /cliente|hist[oó]rico|mem[oó]ria|Telegram/i);
    await page.goto(`${baseURL}/`, { waitUntil: "networkidle" });
    await page.locator(".ai-fab").click();
    await page.locator("#ai-float-input").fill("como conecto meu Telegram?");
    await page.locator("#ai-float button").click();
    await page.waitForFunction(() => {
      const text = document.querySelector("#ai-float-note")?.innerText || "";
      return text && text !== "Processando...";
    }, { timeout: 25000 });
    await assert.match(await page.locator("#ai-float-note").innerText(), /Telegram|chatid|notifica/i);
    await page.close();
  });

  await run("signup and login pages render expected fields", async () => {
    const page = await browser.newPage({ viewport: { width: 1024, height: 768 } });
    await page.goto(`${baseURL}/signup?plan=pro`, { waitUntil: "networkidle" });
    await assert.equal(await page.locator("select[name='plan']").inputValue(), "pro");
    await assert.equal(await page.locator("input[name='password'][minlength='8']").count(), 1);
    await checkNoHorizontalOverflow(page, "signup");
    await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
    await assert.equal(await page.locator("input[name='email']").count(), 1);
    await assert.equal(await page.locator("text=Esqueci minha senha").count(), 1);
    await checkNoHorizontalOverflow(page, "login");
    await page.close();
  });

  await run("robots and sitemap are available", async () => {
    const page = await browser.newPage();
    const robots = await page.request.get(`${baseURL}/robots.txt`);
    assert.equal(robots.status(), 200);
    assert.match(await robots.text(), /Sitemap: https:\/\/novo\.tickpost\.com\.br\/sitemap\.xml/);
    const sitemap = await page.request.get(`${baseURL}/sitemap.xml`);
    assert.equal(sitemap.status(), 200);
    assert.match(await sitemap.text(), /<loc>https:\/\/novo\.tickpost\.com\.br\/<\/loc>/);
    await page.close();
  });

  await run("fantasy ai authenticated lineup builder", async () => {
    if (!adminEmail || !adminPassword) {
      console.log("skip - ADMIN_EMAIL/ADMIN_PASSWORD not set");
      return;
    }
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.goto(`${baseURL}/login`, { waitUntil: "networkidle" });
    await page.locator("input[name='email']").fill(adminEmail);
    await page.locator("input[name='password']").fill(adminPassword);
    await page.locator("button[type='submit']").click();
    await page.waitForURL(/\/app/, { timeout: 15000 });
    await assert.equal(await page.locator("text=Fantasy IA").first().isVisible(), true);
    await page.goto(`${baseURL}/dashboard`, { waitUntil: "networkidle" });
    const dashboardFantasyLink = page.locator("aside .nav-link", { hasText: "Fantasy IA" }).first();
    await assert.equal(await dashboardFantasyLink.getAttribute("href"), "/fantasy-ia");
    await assert.equal(await page.locator("button", { hasText: "Simular entrada e saida IA" }).first().isVisible(), true);
    await dashboardFantasyLink.click();
    await page.waitForURL(/\/fantasy-ia/, { timeout: 15000 });
    await page.goto(`${baseURL}/fantasy-ia`, { waitUntil: "networkidle" });
    await assert.equal(await page.locator("#fantasy-room-url").isVisible(), true);
    await assert.equal(await page.locator("button", { hasText: "Ler sala e montar time" }).isVisible(), true);
    await assert.equal(await page.locator("text=Time para finalizar no Rei do Pitaco").isVisible(), true);
    await page.locator("#fantasy-room-url").fill("https://fantasy.reidopitaco.com.br/fantasy?tab=dfs");
    await page.locator("button", { hasText: "Ler sala e montar time" }).click();
    await assert.match(await page.locator("#fantasy-note").innerText(), /vitrine|roomId/i);
    await page.locator("button", { hasText: "Montar melhor time" }).click();
    await page.waitForFunction(() => document.querySelectorAll(".fantasy-player").length >= 11, { timeout: 15000 });
    await page.waitForFunction(() => /pool|perfis|Usei/i.test(document.querySelector("#fantasy-note")?.innerText || ""), { timeout: 15000 });
    await assert.equal(await page.locator(".fantasy-player").count(), 11);
    await assert.match(await page.locator("#fantasy-note").innerText(), /pool|perfis|Usei/i);
    const costText = await page.locator("#fantasy-cost").innerText();
    const [used, budget] = costText.split("/").map((part) => Number(part.trim()));
    await assert.ok(used <= budget, `fantasy lineup exceeds budget: ${costText}`);
    await checkNoHorizontalOverflow(page, "fantasy");
    await page.screenshot({ path: path.join(outDir, "fantasy-ai.png"), fullPage: true });
    await page.close();
  });

  await browser.close();

  if (failures.length) {
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
