"""Test the Streamlit-to-popup Razorpay checkout message flow in a JS harness."""

import shutil
import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPONENT_DIR = PROJECT_ROOT / "frontend" / "components" / "razorpay_checkout"
MAIN_HTML = COMPONENT_DIR / "index.html"
POPUP_HTML = COMPONENT_DIR / "checkout.html"

NODE_HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const mainHtml = fs.readFileSync(process.argv[1], 'utf8');
const popupHtml = fs.readFileSync(process.argv[2], 'utf8');
const mainScript = mainHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
const popupScript = popupHtml.match(/<script>([\s\S]*?)<\/script>/)[1];
const origin = 'http://localhost:8501';
const order = {
  key_id: 'rzp_test_public', order_id: 'order_test123', amount: 1500000,
  currency: 'INR', name: 'RentAI', description: 'October rent', rent_record_id: 'private-rent-id',
};

function bootMain(blockPopup = false) {
  const messages = [];
  const logs = [];
  const listeners = {};
  const timers = [];
  const intervals = [];
  const button = { disabled: false, listeners: {}, addEventListener(n, cb) { this.listeners[n] = cb; } };
  const status = { textContent: '' };
  const document = {
    body: { scrollHeight: 90 },
    getElementById(id) { return id === 'pay' ? button : status; },
  };
  const popup = {
    closed: false,
    messages: [],
    postMessage(message, targetOrigin) { this.messages.push({ message, targetOrigin }); },
    close() { this.closed = true; },
  };
  const window = {
    location: { href: `${origin}/component/rentai_razorpay_checkout/index.html`, origin },
    parent: { postMessage(message) { messages.push(message); } },
    crypto: { getRandomValues(bytes) { bytes.fill(7); return bytes; } },
    addEventListener(name, cb) { listeners[name] = cb; },
    setTimeout(cb) { timers.push(cb); return timers.length - 1; },
    clearTimeout() {},
    setInterval(cb) { intervals.push(cb); return intervals.length - 1; },
    clearInterval() {},
    open(url, target) { this.openCall = { url, target }; return blockPopup ? null : popup; },
  };
  vm.runInNewContext(mainScript, {
    window, document, URL, Uint8Array, Promise, Error,
    console: { info: (_label, entry) => logs.push(entry) },
  });
  listeners.message({ data: { type: 'streamlit:render', args: { checkout: order } } });
  return { button, status, messages, logs, listeners, timers, intervals, popup, window };
}

function validPopupResult(app, result, source = app.popup, eventOrigin = origin) {
  const channel = new URL(app.window.openCall.url).hash.slice(1);
  app.listeners.message({ source, origin: eventOrigin, data: {
    type: 'rentai_checkout_result', channel, result,
  } });
}

function bootPopup() {
  const messages = [];
  const logs = [];
  const listeners = {};
  const scripts = [];
  const elements = {
    status: { textContent: '' }, description: { textContent: '' }, amount: { textContent: '' },
  };
  const opener = {
    closed: false,
    postMessage(message, targetOrigin) { messages.push({ message, targetOrigin }); },
  };
  const window = {
    location: { hash: '#channel-token', origin, pathname: '/component/rentai_razorpay_checkout/checkout.html', search: '' },
    history: { replaceState() {} },
    opener,
    addEventListener(name, cb) { (listeners[name] ||= []).push(cb); },
    setTimeout(cb) { window.timeout = cb; return 1; },
    clearTimeout() {},
    close() { window.closed = true; },
  };
  const document = {
    getElementById(id) { return elements[id]; },
    createElement() { return { remove() { this.removed = true; } }; },
    head: { appendChild(script) { scripts.push(script); } },
  };
  vm.runInNewContext(popupScript, {
    window, document, Promise, Error, Intl,
    console: { info: (_label, entry) => logs.push(entry) },
  });
  const receive = (data, source = opener, eventOrigin = origin) => {
    for (const listener of listeners.message || []) listener({ source, origin: eventOrigin, data });
  };
  return { window, opener, messages, logs, listeners, scripts, elements, receive };
}

async function run() {
  const scenario = process.argv[3];
  if (scenario.startsWith('main-')) {
    const app = bootMain(scenario === 'main-blocked');
    if (scenario === 'main-blocked') {
      app.button.listeners.click();
      assert.equal(app.window.openCall.target, '_blank');
      assert.match(app.status.textContent, /blocked the checkout tab/i);
      assert.equal(app.button.disabled, false);
      return;
    }
    app.button.listeners.click();
    assert.ok(app.window.openCall, 'window.open must run directly from the button click');
    assert.equal(app.window.openCall.target, '_blank');
    assert.match(app.window.openCall.url, /checkout\.html#.+/);
    assert.equal(app.button.disabled, true);
    const channel = new URL(app.window.openCall.url).hash.slice(1);
    app.listeners.message({ source: {}, origin, data: { type: 'rentai_checkout_ready', channel } });
    assert.equal(app.popup.messages.length, 0, 'messages from another window must be ignored');
    app.listeners.message({ source: app.popup, origin: 'https://attacker.test', data: { type: 'rentai_checkout_ready', channel } });
    assert.equal(app.popup.messages.length, 0, 'cross-origin messages must be ignored');
    app.listeners.message({ source: app.popup, origin, data: { type: 'rentai_checkout_ready', channel } });
    const init = app.popup.messages[0];
    assert.equal(init.targetOrigin, origin);
    assert.deepEqual(JSON.parse(JSON.stringify(init.message.checkout)), {
      key_id: order.key_id, order_id: order.order_id, amount: order.amount,
      currency: order.currency, name: order.name, description: order.description,
    });
    assert.ok(!JSON.stringify(init.message).includes('private-rent-id'));

    if (scenario === 'main-cancel') {
      validPopupResult(app, { status: 'checkout_cancelled' });
      const value = app.messages.find(message => message.type === 'streamlit:setComponentValue');
      assert.equal(value.value.status, 'checkout_cancelled');
      assert.equal(value.value.razorpay_signature, undefined);
      return;
    }
    if (scenario === 'main-wrong-order') {
      validPopupResult(app, {
        status: 'checkout_success', razorpay_payment_id: 'pay_test123',
        razorpay_order_id: 'order_different', razorpay_signature: 'signature',
      });
      const value = app.messages.find(message => message.type === 'streamlit:setComponentValue');
      assert.equal(value.value.status, 'checkout_failed');
      assert.equal(value.value.razorpay_signature, undefined);
      return;
    }
    const result = {
      status: 'checkout_success', razorpay_payment_id: 'pay_test123',
      razorpay_order_id: 'order_test123', razorpay_signature: 'untrusted-test-signature',
    };
    validPopupResult(app, result);
    validPopupResult(app, result);
    const values = app.messages.filter(message => message.type === 'streamlit:setComponentValue');
    assert.equal(values.length, 1, 'duplicate popup callbacks must only be forwarded once');
    assert.equal(values[0].value.razorpay_order_id, order.order_id);
    assert.equal(app.popup.messages.at(-1).message.type, 'rentai_checkout_ack');
    assert.ok(app.logs.some(entry => entry.stage === 'checkout_tab_open_requested'));
    return;
  }

  const popup = bootPopup();
  assert.equal(popup.messages[0].message.type, 'rentai_checkout_ready');
  assert.equal(popup.messages[0].targetOrigin, origin);
  popup.receive({ type: 'rentai_checkout_init', channel: 'channel-token', checkout: order }, {}, origin);
  assert.equal(popup.scripts.length, 0, 'untrusted opener source must be ignored');
  popup.receive({ type: 'rentai_checkout_init', channel: 'channel-token', checkout: order }, popup.opener, 'https://attacker.test');
  assert.equal(popup.scripts.length, 0, 'untrusted origin must be ignored');
  const incomingOrder = scenario === 'popup-invalid'
    ? { ...order, key_id: 'rzp_live_forbidden' }
    : order;
  popup.receive({ type: 'rentai_checkout_init', channel: 'channel-token', checkout: incomingOrder }, popup.opener, origin);

  if (scenario === 'popup-invalid') {
    assert.equal(popup.scripts.length, 0);
    assert.match(popup.elements.status.textContent, /invalid Test Mode order details/);
    return;
  }
  assert.equal(popup.scripts.length, 1);
  let options;
  let opened = false;
  popup.window.Razorpay = function(value) {
    options = value;
    this.on = () => {};
    this.open = () => { opened = true; };
  };
  if (scenario === 'popup-script-error') {
    popup.scripts[0].onerror();
    assert.ok(popup.messages.some(entry => entry.message.type === 'rentai_checkout_result' && entry.message.result.status === 'checkout_failed'));
    return;
  }
  popup.scripts[0].onload();
  assert.equal(opened, true);
  assert.equal(options.order_id, order.order_id);
  assert.equal(options.amount, order.amount);
  if (scenario === 'popup-cancel') {
    options.modal.ondismiss();
    assert.ok(popup.messages.some(entry => entry.message.type === 'rentai_checkout_result' && entry.message.result.status === 'checkout_cancelled'));
    return;
  }
  options.handler({
    razorpay_payment_id: 'pay_test123', razorpay_order_id: 'order_test123',
    razorpay_signature: 'untrusted-test-signature',
  });
  options.handler({
    razorpay_payment_id: 'pay_duplicate', razorpay_order_id: 'order_test123',
    razorpay_signature: 'duplicate',
  });
  const results = popup.messages.filter(entry => entry.message.type === 'rentai_checkout_result');
  assert.equal(results.length, 1, 'standalone page must suppress duplicate results');
  assert.equal(results[0].targetOrigin, origin);
  assert.equal(results[0].message.result.status, 'checkout_success');
  popup.receive({ type: 'rentai_checkout_ack', channel: 'channel-token' });
  assert.equal(popup.window.closed, true);
}

run().catch(error => { console.error(error); process.exitCode = 1; });
"""


class RazorpayCheckoutPopupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which("node")
        if not cls.node:
            raise unittest.SkipTest("Node.js is required to exercise the checkout JavaScript")

    def run_scenario(self, scenario: str):
        result = subprocess.run(
            [self.node, "-e", NODE_HARNESS, str(MAIN_HTML), str(POPUP_HTML), scenario],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_main_button_synchronously_opens_popup_and_validates_return_channel(self):
        self.run_scenario("main-success")

    def test_popup_block_is_reported_and_button_can_be_retried(self):
        self.run_scenario("main-blocked")

    def test_cancelled_result_returns_without_payment_details(self):
        self.run_scenario("main-cancel")

    def test_mismatched_order_result_is_rejected_before_backend_callback(self):
        self.run_scenario("main-wrong-order")

    def test_standalone_checkout_initializes_test_order_and_returns_once(self):
        self.run_scenario("popup-success")

    def test_standalone_checkout_script_error_is_returned(self):
        self.run_scenario("popup-script-error")

    def test_standalone_modal_cancellation_is_returned(self):
        self.run_scenario("popup-cancel")

    def test_standalone_page_rejects_non_test_mode_order_details(self):
        self.run_scenario("popup-invalid")

    def test_browser_pages_never_reference_the_key_secret(self):
        for path in (MAIN_HTML, POPUP_HTML):
            self.assertNotIn("RAZORPAY_KEY_SECRET", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
