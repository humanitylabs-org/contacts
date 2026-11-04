// Contacts screen QA: list, search, open, edit, add, more menu; phone + desktop, dark + light. node tests/view_browser.cjs <outdir>
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const assert = require('node:assert/strict'), path = require('node:path'), fs = require('node:fs');
const base = process.env.CONTACTS_URL || 'http://127.0.0.1:18822/', out = path.resolve(process.argv[2] || 'shots'); fs.mkdirSync(out, {recursive: true});
const STUB = 'window.WizardApp = {version: 1, ready() {}, shell: {toast(i, t) { window.__toast = t; return true; }}, nav: {openContextDraft(x) { window.__draft = x; return {kind: "unsent-draft", sent: false}; }}, update: {hold: () => ({release() {}})}, back: {register() {}, push: () => { window.__backs = (window.__backs || 0) + 1; return {release() { window.__backs--; }}; }}};';
(async () => {
  const launch = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {};
  const browser = await chromium.launch(launch), done = [];
  for (const [label, viewport, scheme] of [['390-dark', {width: 390, height: 844}, 'dark'], ['1100-light', {width: 1100, height: 800}, 'light']]) {
    const ctx = await browser.newContext({viewport, colorScheme: scheme, deviceScaleFactor: 2});
    const page = await ctx.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/wizard-os-home/core/app-client.js', r => r.fulfill({contentType: 'text/javascript', body: STUB}));
    await page.goto(base);
    await page.evaluate(() => { document.documentElement.dataset.presentation = 'window'; });
    await page.locator('.ct-row').first().waitFor();
    assert.equal(await page.locator('.ct-letter', {hasText: 'Favourites'}).count(), 1);
    assert.equal(await page.locator('.ct-head').evaluate(e => getComputedStyle(e).display), 'none', 'the shell owns the window title');
    for (const id of ['more', 'add']) {
      const control = page.locator('#' + id), box = await control.boundingBox();
      assert.ok(box && box.width >= 40 && box.height >= 40, `${id} stays visible when the app header is hidden`);
      assert.equal(await control.evaluate(e => e.contains(document.elementFromPoint(e.getBoundingClientRect().x + e.getBoundingClientRect().width / 2, e.getBoundingClientRect().y + e.getBoundingClientRect().height / 2))), true, `${id} owns its hit point`);
    }
    const wide = viewport.width >= 680, view = wide ? '#pane:not([hidden])' : '#view[open]';
    const bar = await page.locator('.ct-controls').boundingBox(), list = await page.locator('#main').boundingBox();
    if (wide) assert.ok(bar.y < list.y, 'wide windows keep search and actions on top');
    else assert.ok(bar.y >= list.y + list.height - 1, 'narrow windows put search, More and New in a bottom bar under the thumb');
    await page.screenshot({path: path.join(out, `list-${label}.png`)});
    await page.locator('#q').fill('acme');
    await page.waitForFunction(() => document.querySelectorAll('.ct-row').length === 1);
    await page.locator('.ct-row').first().click();
    await page.locator(view + ' .ct-top h2', {hasText: 'Ana Souza'}).waitFor();
    assert.match(await page.locator(view + ' .ct-card').textContent(), /WhatsApp/);
    if (wide) {
      assert.equal(await page.locator('.ct-row[aria-current="true"]').count(), 1, 'the open person is marked in the list beside it');
      assert.ok((await page.locator('#main').boundingBox()).width <= 380, 'the list stays beside the person');
      assert.equal(await page.evaluate(() => window.__backs), 1, 'the pane is on the Back stack');
    }
    await page.screenshot({path: path.join(out, `contact-${label}.png`)});
    await page.waitForTimeout(300);  // Reply context is prefetched with the person
    await page.locator(view).getByRole('button', {name: 'Reply', exact: true}).click();
    assert.equal(await page.evaluate(() => window.__draft?.id), await page.locator('.ct-row').first().getAttribute('data-id'), 'Reply drafts this person');
    assert.match(await page.evaluate(() => window.__draft.message), /Contacts person: Ana Souza/);
    assert.match(await page.locator('#status').textContent(), /Nothing sent/);
    await page.locator(view).getByRole('button', {name: 'Edit'}).click();
    await page.locator('#edit[open]').waitFor();
    await page.screenshot({path: path.join(out, `edit-${label}.png`)});
    await page.locator('#edit input[name=nickname]').fill('Aninha ' + label);
    await page.locator('#edit').getByRole('button', {name: 'Save'}).click();
    await page.locator('#edit[open]').waitFor({state: 'detached'}).catch(() => {});
    await page.waitForFunction(() => !document.querySelector('#edit[open]'));
    await page.locator(view + ' .ct-top h2', {hasText: 'Ana Souza'}).waitFor();  // saving an edit returns to the person
    assert.match(await page.locator(view + ' .ct-sub').textContent(), /Aninha/);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('#view[open]') && document.getElementById('pane').hidden);
    const back = await (await fetch(base + 'api/contacts?q=aninha')).json();
    assert.equal(back.contacts.length, 1, 'edit read back through the API');
    await page.locator('#q').fill('');
    await page.locator('#add').click();
    await page.locator('#edit[open] input[name=name]').fill('New Person ' + label);
    await page.locator('#edit').getByRole('button', {name: '＋ Add phone'}).click();
    await page.locator('#edit .ct-multi-row input[type=tel]').first().fill('+1 555 222 3333');
    await page.locator('#edit').getByRole('button', {name: 'Add contact'}).click();
    await page.locator('.ct-row.ct-new', {hasText: 'New Person'}).waitFor();  // adding returns to the list, the new row shown
    assert.equal(await page.evaluate(() => window.__toast), 'New Person ' + label + ' added');
    assert.equal(await page.locator('dialog[open]').count(), 0);
    await page.locator('#more').click();
    await page.locator('#tools[open]').getByRole('button', {name: 'My card & QR codes'}).waitFor();
    await page.screenshot({path: path.join(out, `menu-${label}.png`)});
    assert.deepEqual(errors, []);
    done.push(label); await ctx.close();
  }
  await browser.close();
  console.log('PASS contacts view: ' + done.join(', ') + ' · list, search, open, Reply draft, edit+readback, add, duplicates menu');
})().catch(e => { console.error('FAIL', e); process.exit(1); });
