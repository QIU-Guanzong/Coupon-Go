/* Stripe.js supplies the payment form. The application never receives card data. */
(() => {
  'use strict';
  const key = 'coupon_go_stripe_test';
  const form = document.getElementById('payment-form');
  const pay = document.getElementById('pay');
  const status = document.getElementById('status');
  const check = document.getElementById('check');
  const back = document.getElementById('back');
  const backTop = document.getElementById('back-top');
  let state;
  try { state = JSON.parse(sessionStorage.getItem(key) || 'null'); } catch (_) { state = null; }
  // Redirect payment methods may return Stripe parameters in the URL. Remove
  // them immediately; the server, not these parameters, decides fulfilment.
  const returned = new URL(location.href).searchParams.has('payment_intent');
  if (location.search) history.replaceState(null, '', location.pathname + location.hash);
  const message = text => { status.textContent = text; };
  const safeReturn = () => {
    try {
      const target = new URL(state?.return_url || './', location.href);
      return target.origin === location.origin ? target.href : new URL('./', location.href).href;
    } catch (_) { return new URL('./', location.href).href; }
  };
  const returnToApp = event => {
    event.preventDefault();
    sessionStorage.removeItem(key);
    location.assign(safeReturn());
  };
  back.addEventListener('click', returnToApp);
  backTop.addEventListener('click', returnToApp);
  if (!state || !String(state.publishable_key || '').startsWith('pk_test_') ||
      !state.client_secret || !Number.isInteger(state.payment_id) ||
      !Number.isInteger(state.order_id) || !state.access_token || !state.api_base ||
      state.currency !== 'HKD' || !Number.isFinite(Number(state.amount))) {
    message('付款資料已失效，請返回訂單重新開啟。');
    return;
  }
  document.getElementById('order-label').textContent = `訂單 #${state.order_id}`;
  document.getElementById('amount').textContent = `HK$${Number(state.amount).toFixed(2)}`;
  let checking = false;
  async function confirmServer() {
    if (checking) return;
    checking = true; check.disabled = true;
    message('正在向系統確認付款結果…');
    try {
      const base = String(state.api_base).replace(/\/$/, '');
      const result = await fetch(`${base}/api/v3/orders/${state.order_id}/stripe-test-confirm`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${state.access_token}` },
        body: JSON.stringify({ payment_id: state.payment_id }), cache: 'no-store',
      });
      const body = await result.json().catch(() => ({}));
      if (result.ok && body.status === 'paid') {
        message('測試付款已確認，優惠券已存入 Q Wallet。');
        form.hidden = true; check.hidden = true;
        sessionStorage.removeItem(key);
      } else if (result.ok && body.status === 'failed') {
        message('測試付款未成功，請返回訂單查看或重新付款。');
        form.hidden = true; check.hidden = true;
      } else {
        message(typeof body.detail === 'string' ? body.detail : '付款仍在確認中，請稍後再查詢。');
        form.hidden = true; check.hidden = false;
      }
    } catch (_) {
      message('暫時未能確認付款，請稍後再查詢訂單。');
      form.hidden = true; check.hidden = false;
    } finally { checking = false; check.disabled = false; }
  }
  check.addEventListener('click', confirmServer);
  if (returned) { confirmServer(); return; }
  if (typeof window.Stripe !== 'function') {
    message('未能載入 Stripe 付款表單，請檢查網絡後重新開啟。');
    return;
  }
  try {
    const stripe = window.Stripe(state.publishable_key);
    const elements = stripe.elements({ clientSecret: state.client_secret });
    elements.create('payment').mount('#payment-element');
    form.hidden = false;
    message('請輸入 Stripe 測試卡資料。');
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (pay.disabled) return;
      pay.disabled = true; message('正在提交付款…');
      try {
        const result = await stripe.confirmPayment({ elements,
          confirmParams: { return_url: location.origin + location.pathname }, redirect: 'if_required' });
        if (result.error) { message(result.error.message || '未能完成測試付款，請檢查資料後重試。'); }
        else { await confirmServer(); }
      } catch (_) { message('付款結果未明，請重新確認訂單狀態。'); form.hidden = true; check.hidden = false; }
      finally { pay.disabled = false; }
    });
  } catch (_) { message('未能載入付款表單，請返回訂單後重試。'); }
})();
