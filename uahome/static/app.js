'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const state = { products: [], reviews: [], approvals: [], events: [], connectors: [] };
const tabs = {
  products: ['▦', '상품 후보', '좋은 상품을 발견하고, 숫자로 확인하세요.'],
  margin: ['◷', '마진 계산', '작은 비용까지 살펴보고 수익을 확인하세요.'],
  drafts: ['▤', '사입 · 상품등록', '1688 사입 정보를 정리하고 로켓그로스 등록을 준비하세요.'],
  reviews: ['♡', '리뷰 관리', '고객의 목소리를 모으고 답변을 준비하세요.'],
  approvals: ['✓', '승인함', '준비된 작업을 검토하고 다음 단계로 넘기세요.'],
  settings: ['⚙', '설정 · 백업', '데이터와 연결 상태를 확인하세요.']
};
const statuses = { candidate: '검토 후보', selected: '사입 검토', purchase_approved: '사입 승인', ordered: '발주 완료', received: '입고 완료', listing_ready: '등록 준비', listed: '쿠팡 등록 완료', inbound_planned: '센터 입고 준비', in_transit: '센터 이동 중', stocked: '센터 입고 확인', paused: '보류' };
const approvalStatus = { pending: '검토 대기', approved: '승인 완료', rejected: '반려', completed: '수동 완료 기록' };
const kinds = { purchase: '사입', listing: '상품 등록', review_reply: '리뷰 답변' };
const costFields = [
  ['sale_price', '판매가', '원', 0], ['purchase_price', '1688 매입 단가', 'CNY', 0],
  ['exchange_rate', '결제에 적용할 환율', 'KRW / CNY', ''], ['inbound_shipping', '입고 배송비 · 개당 배분', '원', 0],
  ['outbound_shipping', '별도 출고비 · 그로스 배송비 중복 제외', '원', 0], ['fulfillment_fee', '로켓그로스 입출고·배송비', '원 / 개', 0], ['storage_fee', '예상 보관비', '원 / 개', 0], ['customer_shipping', '고객 수취 배송비', '원', 0],
  ['packaging', '포장비', '원', 0], ['other_cost', '기타 개당 비용', '원', 0],
  ['fixed_fee', '고정 수수료 · 개당', '원', 0], ['platform_fee_rate', '판매 수수료율', '%', 0],
  ['payment_fee_rate', '결제 수수료율', '%', 0], ['ad_rate', '광고비율', '%', 0],
  ['return_rate', '예상 반품률', '%', 0], ['return_cost', '반품 1건당 손실비용', '원', 0],
  ['tax_reserve_rate', '세금 예비비율', '%', 0], ['target_margin_rate', '목표 마진율', '%', 20]
];
let active = 'products', search = '', statusFilter = '', sort = 'score', selectedDraft = '', selectedMargin = '', editingProduct = null, calculatorCosts = {}, assistReport = null, toastTimer;
const money = value => value === null || value === undefined ? '계산 불가' : new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 0 }).format(Number(value)) + '원';
const percent = value => value === null || value === undefined ? '—' : Number(value).toFixed(1) + '%';
const date = value => value ? new Date(value).toLocaleString('ko-KR', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
const productById = id => state.products.find(product => product.id === id);

function el(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on')) element.addEventListener(key.slice(2), value);
    else if (key === 'class') element.className = value;
    else if (key === 'value') element.value = value;
    else if (key === 'checked' || key === 'disabled') element[key] = Boolean(value);
    else element.setAttribute(key, String(value));
  }
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return element;
}
function button(text, handler, style = '') { return el('button', { type: 'button', class: 'button ' + style, onclick: async event => { const target = event.currentTarget; target.disabled = true; try { await handler(); } catch (error) { toast(error.message, true); } finally { target.disabled = false; } } }, text); }
function toast(message, error = false) { const node = $('#toast'); node.textContent = message; node.classList.toggle('toast-error', error); node.classList.remove('hidden'); clearTimeout(toastTimer); toastTimer = setTimeout(() => node.classList.add('hidden'), 6500); }
async function api(path, method = 'GET', data) {
  const response = await fetch(path, { method, headers: data === undefined ? {} : { 'Content-Type': 'application/json' }, body: data === undefined ? undefined : JSON.stringify(data) });
  let body; try { body = await response.json(); } catch { throw new Error('서버 응답을 읽지 못했습니다. 서버가 실행 중인지 확인하세요.'); }
  if (!response.ok) throw new Error(body.error || '요청을 처리하지 못했습니다.');
  return body.data;
}
async function load(render = true) {
  try {
    const data = await api('/api/state');
    for (const key of Object.keys(state)) state[key] = data[key] || [];
    $('#connection-alert').classList.add('hidden');
    renderMetrics(); if (render) renderView();
  } catch (error) { $('#connection-alert').textContent = '데이터를 불러오지 못했습니다. ' + error.message + ' · 오른쪽 위 새로고침으로 다시 시도하세요.'; $('#connection-alert').classList.remove('hidden'); }
}
function badge(text, style = '') { return el('span', { class: 'badge ' + style }, text); }
function empty(title, text, action) { return el('div', { class: 'empty' }, el('div', { class: 'empty-symbol', 'aria-hidden': true }, '◌'), el('h3', {}, title), el('p', {}, text), action); }
function panel(title, subtitle, body, extra) { return el('section', { class: 'panel' }, el('div', { class: 'panel-head' }, el('div', {}, el('h2', {}, title), subtitle ? el('p', {}, subtitle) : null), extra), body); }
function inputField(label, name, value = '', options = {}) {
  const field = el(options.multiline ? 'textarea' : options.select ? 'select' : 'input', { name, 'aria-label': label, value: options.select ? undefined : value, rows: options.multiline ? 3 : undefined, type: options.multiline || options.select ? undefined : options.type || 'text', required: options.required, min: options.min, max: options.max, step: options.step, maxlength: options.maxlength || (options.type === 'number' ? undefined : 10000) });
  if (options.select) { for (const [key, text] of options.select) field.append(el('option', { value: key }, text)); field.value = value; }
  const labelNode = el('label', { class: 'field' + (options.wide ? ' wide' : '') }, el('span', {}, label), field, options.hint ? el('small', {}, options.hint) : null);
  return labelNode;
}
function renderMetrics() {
  const awaiting = state.approvals.filter(a => a.status === 'pending').length;
  const profitable = state.products.filter(p => Number(p.margin?.profit) > 0).length;
  const items = [['검토 중인 상품', state.products.length, '등록된 전체 상품'], ['수익 예상 상품', profitable, '단위 추정이익이 양수인 상품'], ['내 결정이 필요한 작업', awaiting, '승인함에서 검토하세요'], ['답변 대기 리뷰', state.reviews.filter(r => r.status !== 'replied').length, '실제 고객 리뷰 기준']];
  $('#metrics').replaceChildren(...items.map(([label, value, hint]) => el('div', { class: 'metric' }, el('div', { class: 'metric-label' }, label, '↗'), el('div', { class: 'metric-value' }, value), el('small', {}, hint))));
}
function renderView() {
  active = Object.hasOwn(tabs, location.hash.slice(1)) ? location.hash.slice(1) : 'products';
  $('#page-title').textContent = tabs[active][1]; $('#page-subtitle').textContent = tabs[active][2];
  for (const node of document.querySelectorAll('.nav-item')) { const isActive = node.getAttribute('href') === '#' + active; node.classList.toggle('active', isActive); if (isActive) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current'); }
  const renderers = { products: renderProducts, margin: renderMargin, drafts: renderDrafts, reviews: renderReviews, approvals: renderApprovals, settings: renderSettings };
  $('#view').replaceChildren(renderers[active]());
}
function renderProducts() {
  const wrap = el('div');
  if (assistReport) wrap.append(el('div', { class: 'note' }, '규칙 기반 작업 준비: ' + (assistReport.prepared?.length || 0) + '건 준비 · ' + (assistReport.skipped?.length || 0) + '건 건너뜀', (assistReport.skipped || []).length ? el('ul', { class: 'checklist' }, ...assistReport.skipped.map(item => el('li', {}, (productById(item.id)?.name || '검토 항목') + ': ' + item.reason))) : null));
  const prep = button('검토할 작업 준비', async () => { const result = await api('/api/assist', 'POST', {}); assistReport = result; await load(); toast('규칙 기반 준비: ' + (result.prepared?.length || 0) + '건 · 건너뜀 ' + (result.skipped?.length || 0) + '건. 승인함과 리뷰를 확인하세요.'); }, 'small');
  const body = el('div');
  const searchInput = el('input', { type: 'search', placeholder: '상품명, 공급처 검색', 'aria-label': '상품 검색', value: search, oninput: event => { search = event.target.value; renderTable(); } });
  const filter = el('select', { 'aria-label': '상품 상태 필터', onchange: event => { statusFilter = event.target.value; renderTable(); } }, el('option', { value: '' }, '모든 상태'), ...Object.entries(statuses).map(([key, label]) => el('option', { value: key }, label))); filter.value = statusFilter;
  const sorting = el('select', { 'aria-label': '상품 정렬', onchange: event => { sort = event.target.value; renderTable(); } }, el('option', { value: 'score' }, '선정 점수순'), el('option', { value: 'profit' }, '추정이익순'), el('option', { value: 'new' }, '최근 추가순')); sorting.value = sort;
  const tableArea = el('div', { class: 'table-wrap' }); const count = el('span', { class: 'count' });
  body.append(el('div', { class: 'toolbar' }, searchInput, filter, sorting, el('span', { class: 'spacer' }), count), tableArea);
  function renderTable() {
    const items = state.products.filter(p => (!statusFilter || p.status === statusFilter) && [p.name, p.supplier, p.category].join(' ').toLowerCase().includes(search.toLowerCase()));
    items.sort((a, b) => sort === 'new' ? String(b.created_at).localeCompare(String(a.created_at)) : Number(b[sort === 'profit' ? 'margin' : 'ranking']?.[sort === 'profit' ? 'profit' : 'score'] || 0) - Number(a[sort === 'profit' ? 'margin' : 'ranking']?.[sort === 'profit' ? 'profit' : 'score'] || 0));
    count.textContent = items.length + '개 상품';
    if (!items.length) { tableArea.replaceChildren(empty(state.products.length ? '검색 결과가 없습니다' : '첫 상품 후보를 추가해보세요', '상품명과 비용을 입력하면 예상 마진과 선정 점수를 함께 비교할 수 있습니다. 예시 상품은 직접 불러올 때만 추가됩니다.', button(state.products.length ? '상품 추가' : '예시 데이터 불러오기', state.products.length ? () => openProduct() : async () => { await api('/api/demo', 'POST', {}); await load(); toast('예시 데이터가 추가되었습니다. 실제 상품 정보로 바꿔 사용하세요.'); }, 'primary'))); return; }
    const table = el('table', {}, el('thead', {}, el('tr', {}, ...['상품 / 공급처', '진행 상태', '판매가', '개당 추정이익', '선정 점수', '관리'].map(text => el('th', { scope: 'col' }, text)))));
    const rows = items.map(product => {
      const ranking = product.ranking || {}, margin = product.margin || {};
      return el('tr', {}, el('td', {}, el('div', { class: 'product-title' }, product.name), el('small', {}, (product.supplier || '공급처 미입력') + ' · ' + (product.category || '분류 미입력'))), el('td', {}, badge(statuses[product.status] || product.status, ['stocked', 'listed', 'listing_ready', 'purchase_approved'].includes(product.status) ? 'green' : product.status === 'paused' ? 'orange' : '')), el('td', { class: 'money' }, money(product.costs?.sale_price)), el('td', { class: 'money ' + (Number(margin.profit) < 0 ? 'negative' : 'positive') }, money(margin.profit), el('small', {}, percent(margin.margin_rate))), el('td', {}, el('div', { class: 'score' }, el('span', {}, ranking.score ?? '—'), el('progress', { class: 'score-track', max: 100, value: Math.min(100, Math.max(0, Number(ranking.score) || 0)), 'aria-label': '선정 점수' })), el('small', {}, ranking.recommendation || '수동 점수 기반')), el('td', {}, el('div', { class: 'actions' }, button('편집', () => openProduct(product), 'small'), button('초안', () => { selectedDraft = product.id; location.hash = 'drafts'; if (active === 'drafts') renderView(); }, 'small'))));
    });
    table.append(el('tbody', {}, rows)); tableArea.replaceChildren(table);
  }
  renderTable(); wrap.append(panel('상품 포트폴리오', '선정 점수는 입력한 수요·경쟁·공급처 점수에 기반한 참고 지표입니다.', body, prep));
  wrap.append(el('p', { class: 'hint' }, '반자동 준비는 미작성 리뷰 답변과 조건을 충족한 승인 요청을 만듭니다. 자동 승인·주문·게시를 실행하지 않습니다.'));
  return wrap;
}

function openProduct(product = null) {
  editingProduct = product; $('#product-dialog-title').textContent = product ? '상품 정보 편집' : '새 상품 추가'; $('#product-error').textContent = '';
  const productFields = el('div', { class: 'form-grid' });
  const transitions = { candidate: ['candidate', 'selected', 'paused'], selected: ['selected', 'candidate', 'paused'], purchase_approved: ['purchase_approved', 'paused'], ordered: ['ordered', 'received', 'paused'], received: ['received', 'listing_ready', 'paused'], listing_ready: ['listing_ready', 'received', 'paused'], listed: ['listed', 'inbound_planned', 'paused'], inbound_planned: ['inbound_planned', 'listed', 'in_transit', 'paused'], in_transit: ['in_transit', 'stocked', 'paused'], stocked: ['stocked', 'paused'], paused: ['paused', 'candidate', 'selected'] };
  const allowedStatuses = product ? transitions[product.status] || [product.status] : ['candidate', 'selected', 'paused'];
  const fields = [['name', '상품명', { required: true }], ['category', '상품 분류', {}], ['supplier', '공급처', {}], ['channel', '판매 채널', {}], ['source_url', '사입처 상품 주소', { type: 'url', wide: true }], ['seller_sku', '판매자 SKU', {}], ['coupang_product_id', '쿠팡 상품 ID', {}], ['fulfillment_center', '로켓그로스 입고 센터', {}], ['inbound_reference', '센터 입고계획 번호', {}], ['tracking_number', '센터 이동 운송장 번호', {}], ['quantity', '사입 수량', { type: 'number', min: 1, step: 1 }], ['moq', '최소 주문 수량 (MOQ)', { type: 'number', min: 1, step: 1 }], ['lead_days', '예상 입고 소요일', { type: 'number', min: 0, step: 1 }], ['status', '진행 상태', { select: allowedStatuses.map(key => [key, statuses[key]]) }], ['demand_score', '수요 점수 (0~5)', { type: 'number', min: 0, max: 5, step: .1 }], ['competition_score', '경쟁 강도 (낮을수록 유리)', { type: 'number', min: 0, max: 5, step: .1 }], ['supplier_score', '공급처 신뢰 점수 (0~5)', { type: 'number', min: 0, max: 5, step: .1 }], ['keywords', '검색 키워드', { hint: '쉼표로 구분', wide: true }], ['selling_points', '상품의 실제 장점', { multiline: true, wide: true }], ['specs', '규격 · 소재 · 구성 · 원산지 등', { multiline: true, wide: true }], ['notes', '선정 근거 · 확인할 내용', { multiline: true, wide: true, maxlength: 20000 }]];
  const defaults = { supplier: '1688', channel: '쿠팡 로켓그로스', quantity: 1, moq: 1, lead_days: 0, demand_score: 0, competition_score: 0, supplier_score: 0, status: 'candidate' };
  for (const [key, label, options] of fields) productFields.append(inputField(label, key, product?.[key] ?? defaults[key] ?? '', options));
  productFields.append(el('p', { class: 'note wide' }, '센터 입고 단계는 실제 쿠팡 센터 기록을 확인한 뒤 직접 변경하세요. 쿠팡 등록 완료는 상품 등록 기록이며 실제 판매·재고 가용 상태를 자동 확인한 결과가 아닙니다.'));
  productFields.append(el('div', { class: 'section-label' }, '개당 비용과 목표 수익'));
  for (const [key, label, unit, fallback] of costFields) productFields.append(inputField(label + ' (' + unit + ')', 'cost.' + key, product?.costs?.[key] ?? fallback, { type: 'number', min: key === 'exchange_rate' ? .000001 : 0, max: unit === '%' ? key === 'target_margin_rate' ? 99.99 : 100 : undefined, step: 'any', required: true }));
  productFields.append(el('p', { class: 'hint wide' }, '1688 매입 단가는 CNY, 환율은 실제 적용할 KRW/CNY 값을 직접 입력하세요. 나머지는 원화·개당 기준입니다. 그로스 입출고·배송비와 별도 출고비를 중복 입력하지 마세요. 수수료와 예비비는 판매가+고객 배송비를 기준으로 계산합니다. 세금 예비비는 실제 세액이 아닙니다.'));
  $('#product-fields').replaceChildren(productFields);
  const actions = $('.dialog-actions', $('#product-dialog')); const previousDelete = $('#delete-product'); if (previousDelete) previousDelete.remove();
  if (product) actions.prepend(button('상품 삭제', async () => { if (!confirm('이 상품을 삭제할까요? 연결된 리뷰나 승인 이력이 있으면 삭제할 수 없습니다.')) return; await api('/api/products/' + encodeURIComponent(product.id), 'DELETE', { revision: product.revision }); $('#product-dialog').close(); await load(); toast('상품을 삭제했습니다.'); }, 'danger small'));
  if (product) actions.firstChild.id = 'delete-product';
  $('#product-dialog').showModal();
}

function collectCosts(form) { const costs = {}; for (const [key] of costFields) { const input = form.elements.namedItem('cost.' + key); costs[key] = Number(input.value); if (!Number.isFinite(costs[key])) throw new Error('비용은 올바른 숫자로 입력하세요.'); } return costs; }
$('#product-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget, submit = form.querySelector('[type="submit"]'); submit.disabled = true;
  try {
    const data = {}; for (const [key, value] of new FormData(form)) if (!key.startsWith('cost.')) data[key] = ['quantity', 'moq', 'lead_days', 'demand_score', 'competition_score', 'supplier_score'].includes(key) ? Number(value) : value;
    data.costs = collectCosts(form); if (editingProduct) data.revision = editingProduct.revision;
    await api('/api/products' + (editingProduct ? '/' + encodeURIComponent(editingProduct.id) : ''), editingProduct ? 'PATCH' : 'POST', data);
    $('#product-dialog').close(); await load(); toast('상품 정보를 저장했습니다.');
  } catch (error) { $('#product-error').textContent = error.message; } finally { submit.disabled = false; }
});
function renderMargin() {
  const wrap = el('div', { class: 'split' }); const form = el('form'); const grid = el('div', { class: 'form-grid' });
  const select = el('select', { 'aria-label': '마진 계산할 상품', onchange: event => { selectedMargin = event.target.value; const p = productById(selectedMargin); if (p) calculatorCosts = { ...p.costs }; renderView(); } }, el('option', { value: '' }, '상품 불러오기 · 직접 계산도 가능'), ...state.products.map(p => el('option', { value: p.id }, p.name)));
  select.value = selectedMargin;
  for (const [key, label, unit, fallback] of costFields) grid.append(inputField(label + ' (' + unit + ')', 'cost.' + key, calculatorCosts[key] ?? fallback, { type: 'number', min: key === 'exchange_rate' ? .000001 : 0, max: unit === '%' ? key === 'target_margin_rate' ? 99.99 : 100 : undefined, step: 'any', required: true }));
  const result = el('div');
  async function calculate() { const costs = collectCosts(form); calculatorCosts = costs; const data = await api('/api/calculate', 'POST', { costs }); renderMarginResult(result, data); }
  form.addEventListener('submit', async event => { event.preventDefault(); try { await calculate(); } catch (error) { toast(error.message, true); } });
  form.append(select, el('p', { class: 'hint' }, '직접 계산한 값은 상품에 자동 저장되지 않습니다. 상품 정보 편집에서 저장하세요.'), grid, el('div', { class: 'dialog-actions' }, el('button', { type: 'submit', class: 'button primary' }, '마진 계산')));
  result.append(empty('모든 비용을 함께 계산하세요', '판매가·원가·수수료를 입력한 뒤 마진 계산을 누르면 손익분기 판매가와 목표 마진 판매가까지 확인할 수 있습니다.'));
  wrap.append(panel('개당 수익 시뮬레이션', '1688 매입 단가 × 환율 + 수입원가 + 로켓그로스 비용', el('div', { class: 'panel-body' }, form)), panel('계산 결과', '실제 정산액과 차이가 있을 수 있는 추정치입니다.', el('div', { class: 'panel-body' }, result)));
  wrap.append(renderImportCalculator(form));
  return wrap;
}
function renderImportCalculator(marginForm) {
  const form = el('form'); const grid = el('div', { class: 'form-grid' }); const output = el('div');
  const selectedProduct = productById(selectedMargin);
  const fields = [
    ['quantity', '이번 수입 수량 (개)', selectedProduct?.quantity ?? 1, 1, 1],
    ['cny_unit_price', '1688 매입 단가 (CNY)', selectedProduct?.costs?.purchase_price ?? '', 0, 'any'],
    ['exchange_rate', '실제 적용 환율 (KRW / CNY)', selectedProduct?.costs?.exchange_rate ?? '', .000001, 'any'],
    ['china_shipping_cny', '중국 내 배송비 전체 (CNY)', 0, 0, 'any'],
    ['international_shipping_krw', '국제 배송비 전체 (원)', 0, 0, 'any'],
    ['customs_duty_krw', '관세 전체 · 확인된 금액 (원)', 0, 0, 'any'],
    ['import_vat_krw', '수입 부가세 전체 · 확인된 금액 (원)', 0, 0, 'any'],
    ['brokerage_krw', '통관 대행비 전체 (원)', 0, 0, 'any'],
    ['inspection_krw', '검수비 전체 (원)', 0, 0, 'any'],
    ['labeling_krw', '라벨·원산지 작업비 전체 (원)', 0, 0, 'any'],
    ['domestic_inbound_krw', '국내 입고 운송비 전체 (원)', 0, 0, 'any']
  ];
  for (const [name, label, value, min, step] of fields) grid.append(inputField(label, name, value, { type: 'number', required: true, min, step }));
  const recoverable = el('input', { type: 'checkbox', name: 'vat_recoverable' });
  grid.append(el('label', { class: 'field wide checkbox-field' }, recoverable, el('span', {}, '수입 부가세를 공제 가능 금액으로 분리하기 · 적용 가능 여부 확인 후 선택')));
  form.append(el('p', { class: 'note warning' }, '총 운송·통관 비용을 수량으로 나눠 개당 원가에 반영합니다. 환율·관세·수입 부가세는 자동 조회하지 않습니다. 실제 견적과 세무 확인 내용을 입력하세요. 0원 항목은 미확정 비용이 빠져 있지 않은지 확인하세요.'), grid, el('div', { class: 'dialog-actions' }, el('button', { class: 'button primary', type: 'submit' }, '수입원가 배분 계산')), output);
  form.addEventListener('submit', async event => {
    event.preventDefault(); const submit = form.querySelector('[type="submit"]'); submit.disabled = true;
    try {
      const payload = {}; for (const [name] of fields) payload[name] = Number(form.elements.namedItem(name).value); payload.vat_recoverable = recoverable.checked;
      const data = await api('/api/import/calculate', 'POST', payload);
      const values = [['상품 매입 총액', data.purchase_total_krw], ['물류·통관 비용 총액', data.logistics_total_krw], ['현금 지출 총액 · 부가세 포함', data.cash_outlay_total], ['운영 원가 총액', data.operating_cost_total], ['개당 수입 원가', data.per_unit_landed_cost], ['개당 배분 부대비', data.per_unit_inbound_cost], ['수입 부가세 총액', data.import_vat_total]];
      const apply = button('마진 시뮬레이터에 적용', () => {
        const current = {}; for (const [key, , , fallback] of costFields) { const field = marginForm.elements.namedItem('cost.' + key); current[key] = field && field.value !== '' ? Number(field.value) : Number(fallback || 0); }
        calculatorCosts = { ...current, ...data.costs_patch }; renderView(); toast('매입 단가·환율·개당 배분 부대비를 시뮬레이터에 적용했습니다. 상품에는 아직 저장되지 않았습니다.');
      }, 'small primary');
      const actions = el('div', { class: 'actions' }, apply);
      if (selectedProduct) actions.append(button('선택 상품에 수입원가 저장', async () => {
        const latest = productById(selectedProduct.id); if (!latest) throw new Error('상품을 다시 선택하세요.');
        if (!confirm(latest.name + ' 상품의 수입 수량·매입 단가·환율·개당 입고비를 이번 계산값으로 덮어쓸까요? 수량 ' + payload.quantity + '개 · 개당 배분 부대비 ' + money(data.per_unit_inbound_cost) + '. 기존 승인 요청은 변경 전 자료가 될 수 있습니다.')) return;
        const evidence = { input: data.input || payload, cash_outlay_total: data.cash_outlay_total, import_vat_total: data.import_vat_total, vat_recoverable: data.vat_recoverable };
        const notes = [latest.notes || '', '[1688 수입원가 배분 근거 ' + new Date().toISOString() + ']\n' + JSON.stringify(evidence)].filter(Boolean).join('\n\n');
        if (notes.length > 20000) throw new Error('기존 메모와 원가 배분 근거가 20,000자를 초과합니다. 상품 메모를 백업하고 정리한 뒤 다시 저장하세요.');
        await api('/api/products/' + encodeURIComponent(latest.id), 'PATCH', { quantity: payload.quantity, notes, costs: { ...latest.costs, ...data.costs_patch }, revision: latest.revision }); await load(false); calculatorCosts = { ...productById(latest.id).costs }; renderView(); toast('선택 상품에 수입원가를 저장했습니다.');
      }, 'small'));
      output.replaceChildren(el('h3', { class: 'mt30' }, '수입원가 배분 결과'), el('div', { class: 'breakdown' }, values.map(([label, value]) => el('div', {}, el('span', {}, label), el('strong', {}, money(value))))), el('p', { class: 'hint' }, data.vat_recoverable ? '수입 부가세는 현금 지출에 포함하고 운영 원가에서는 분리했습니다.' : '수입 부가세를 운영 원가에 포함했습니다.'), actions, el('ul', { class: 'checklist' }, ...(data.warnings || []).map(warning => el('li', {}, warning))));
    } catch (error) { toast(error.message, true); } finally { submit.disabled = false; }
  });
  const section = panel('1688 수입원가 배분', 'CNY 사입부터 쿠팡 로켓그로스 입고 전까지의 비용을 정리하세요.', el('div', { class: 'panel-body' }, form));
  section.classList.add('wide'); return section;
}
function renderMarginResult(target, data) {
  const rows = [['총 매출 · 고객 배송비 포함', money(data.revenue)], ['입고 원가 · 환율 반영', money(data.landed_cost)], ['비용 합계', money(data.total_cost)], ['손익분기 판매가', money(data.break_even_price)], ['목표 마진 판매가', money(data.recommended_price)]];
  target.replaceChildren(el('div', { class: 'result-hero' }, el('p', {}, '개당 추정이익'), el('strong', { class: Number(data.profit) < 0 ? 'negative' : '' }, money(data.profit)), el('small', {}, '추정 마진율 ' + percent(data.margin_rate))), el('div', { class: 'breakdown' }, ...rows.map(([label, value]) => el('div', {}, el('span', {}, label), el('strong', {}, value)))), (data.warnings || []).length ? el('ul', { class: 'checklist negative' }, ...data.warnings.map(text => el('li', {}, text))) : null, el('ul', { class: 'checklist' }, ...(data.assumptions || []).map(text => el('li', {}, text))));
}

function productSelector(label, onChange, current) { const select = el('select', { 'aria-label': label, onchange: event => onChange(event.target.value) }, el('option', { value: '' }, '상품을 선택하세요'), ...state.products.map(p => el('option', { value: p.id }, p.name))); select.value = current || ''; return select; }
function download(text, filename, type = 'text/plain;charset=utf-8') { const url = URL.createObjectURL(new Blob([text], { type })); const anchor = el('a', { href: url, download: filename }); anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
async function copy(text) { if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); toast('복사했습니다.'); } else { throw new Error('현재 브라우저에서는 자동 복사가 지원되지 않습니다. 문구를 선택해 직접 복사하세요.'); } }
function renderDrafts() {
  const wrap = el('div'); const selected = productById(selectedDraft); const draftArea = el('div');
  wrap.append(el('div', { class: 'note' }, '발주서와 상품 등록 초안을 준비합니다. 실제 사입·결제·게시 기능은 연결 전입니다. 승인 요청은 저장된 상품 정보의 스냅샷을 기준으로 생성됩니다.'), el('div', { class: 'select-row' }, productSelector('초안을 작성할 상품', id => { selectedDraft = id; renderView(); }, selectedDraft), selected ? button('상품 정보 편집', () => openProduct(selected)) : null), draftArea);
  if (!selected) { draftArea.append(panel('사입부터 등록까지', '', empty('초안을 만들 상품을 선택하세요', '공급처·상품 규격·장점·비용을 입력하면 사입 검토 문서와 등록 문구를 만들 수 있습니다.'))); return wrap; }
  const columns = el('div', { class: 'split' });
  for (const kind of ['purchase', 'listing']) {
    const result = el('div'); const create = button(kind === 'purchase' ? '발주서 초안 만들기' : '상품 등록 초안 만들기', async () => {
      const draft = await api('/api/products/' + encodeURIComponent(selected.id) + '/' + kind, 'POST', {});
      const text = kind === 'purchase' ? draft.text : [draft.title, '', draft.description, '', '키워드: ' + (draft.keywords || []).join(', ')].join('\n');
      const editor = el('textarea', { class: 'draft-text', 'aria-label': kinds[kind] + ' 초안 편집', value: text });
      result.replaceChildren(el('p', { class: 'hint' }, kind === 'purchase' ? '예상 사입비 ' + money(draft.estimated_total) + ' · 수량 ' + draft.quantity : '채널: ' + (draft.channel || '미정')), editor, el('p', { class: 'hint' }, '아래 문구 편집은 복사·다운로드용입니다. 승인 내용을 바꾸려면 상품 정보를 저장하고 초안을 다시 생성하세요.'), el('div', { class: 'actions' }, button('복사', () => copy(editor.value), 'small'), button('텍스트 내려받기', () => download(editor.value, 'uahome-' + kind + '.txt'), 'small')));
      const warnings = [...(draft.warnings || []), ...(draft.missing_fields || []).map(field => '확인 필요: ' + field)]; if (warnings.length) result.append(el('ul', { class: 'checklist' }, warnings.map(w => el('li', {}, w))));
    }, 'primary');
    const approvedFor = kind === 'purchase' ? selected.status === 'selected' : ['received', 'listing_ready'].includes(selected.status);
    const approval = button('검토 승인 요청', async () => { await api('/api/approvals', 'POST', { kind, product_id: selected.id }); await load(false); toast('승인함에 검토 요청을 추가했습니다.'); }, 'small');
    if (!approvedFor) { approval.disabled = true; approval.title = kind === 'purchase' ? '상품 상태를 사입 검토로 저장하세요.' : '상품 상태를 입고 완료로 저장하세요.'; }
    result.append(el('p', { class: 'hint' }, '상품 정보를 바탕으로 초안을 생성하세요.'));
    columns.append(panel(kind === 'purchase' ? '01 · 사입 준비' : '02 · 상품 등록 준비', kind === 'purchase' ? '사입 검토 → 승인 → 외부 발주 후 수동 완료' : '입고 완료 → 승인 → 외부 게시 후 수동 완료', el('div', { class: 'panel-body' }, el('div', { class: 'actions' }, create, approval), el('p', { class: 'hint' }, '현재 상태: ' + (statuses[selected.status] || selected.status) + (approvedFor ? '' : ' · ' + (kind === 'purchase' ? '사입 검토 상태에서 요청할 수 있습니다.' : '입고 완료 상태에서 요청할 수 있습니다.'))), result)));
  }
  wrap.append(columns); return wrap;
}

function renderReviews() {
  const wrap = el('div'); const form = el('form'); const grid = el('div', { class: 'form-grid' });
  grid.append(inputField('상품', 'product_id', '', { select: [['', '상품을 선택하세요'], ...state.products.map(p => [p.id, p.name])], required: true }), inputField('별점', 'rating', '5', { select: [5, 4, 3, 2, 1].map(n => [String(n), n + '점']) }), inputField('고객 표시명', 'customer_name'), inputField('판매 채널', 'channel'), inputField('원본 리뷰 ID (선택)', 'external_id'), inputField('실제 리뷰 내용', 'content', '', { multiline: true, wide: true, required: true }));
  form.append(grid, el('div', { class: 'dialog-actions' }, el('button', { class: 'button primary', type: 'submit' }, '리뷰 저장')));
  form.addEventListener('submit', async event => { event.preventDefault(); try { const data = Object.fromEntries(new FormData(form)); data.rating = Number(data.rating); await api('/api/reviews', 'POST', data); await load(); toast('리뷰를 저장했습니다.'); } catch (error) { toast(error.message, true); } });
  wrap.append(panel('실제 리뷰 추가', '판매처에서 받은 실제 고객 리뷰를 입력하세요. API 연결 전에는 직접 추가합니다.', el('div', { class: 'panel-body' }, form)));
  const cards = el('div');
  if (!state.reviews.length) cards.append(empty('아직 등록된 리뷰가 없습니다', '실제 리뷰를 추가하면 배송·품질 등 내용을 확인하고 고객 답변 초안을 준비할 수 있습니다.'));
  for (const review of state.reviews) {
    const product = productById(review.product_id); const editor = el('textarea', { class: 'review-reply', 'aria-label': (product?.name || '상품') + ' 리뷰 답변 초안', value: review.reply_draft || '', placeholder: '초안을 생성하거나 직접 답변을 작성하세요.' });
    cards.append(el('article', { class: 'review-card' }, el('div', { class: 'card-head' }, el('div', {}, el('div', { class: 'card-title' }, product?.name || '상품'), el('span', { class: 'stars', 'aria-label': review.rating + '점' }, '★'.repeat(review.rating) + '☆'.repeat(5 - review.rating)), el('p', { class: 'review-meta' }, (review.customer_name || '고객') + ' · ' + (review.channel || '채널 미입력') + ' · ' + date(review.created_at))), badge({ pending: '답변 대기', drafted: '초안 준비', approved: '답변 승인', replied: '수동 답변 완료' }[review.status] || review.status, review.status === 'replied' ? 'green' : '')), el('p', { class: 'review-content' }, review.content), editor, el('div', { class: 'actions' }, button('답변 초안 만들기', async () => { const draft = await api('/api/reviews/' + encodeURIComponent(review.id) + '/draft', 'POST', {}); editor.value = draft.text || draft.reply_draft || draft.review?.reply_draft || ''; await load(false); if (draft.requires_attention) toast('고객 불편 사항을 확인한 뒤 답변을 검토하세요.'); }, 'small'), button('답변 저장', async () => { const latest = state.reviews.find(r => r.id === review.id) || review; await api('/api/reviews/' + encodeURIComponent(review.id), 'PATCH', { revision: latest.revision, reply_draft: editor.value }); await load(); toast('답변 초안을 저장했습니다.'); }, 'small'), button('복사', () => copy(editor.value), 'small'), button('승인 요청', async () => { const latest = state.reviews.find(r => r.id === review.id) || review; if (editor.value !== latest.reply_draft) throw new Error('편집한 답변을 먼저 저장하세요.'); await api('/api/approvals', 'POST', { kind: 'review_reply', product_id: review.product_id, review_id: review.id }); await load(); toast('답변 검토를 요청했습니다.'); }, 'small'))));
  }
  wrap.append(panel('리뷰와 답변', '초안은 입력된 리뷰를 바탕으로 작성됩니다. 외부 전송은 직접 완료하세요.', cards)); return wrap;
}

function renderApprovals() {
  const body = el('div');
  if (!state.approvals.length) body.append(empty('지금은 검토할 작업이 없습니다', '상품을 사입 검토·입고 완료 상태로 준비하거나 리뷰 답변을 저장한 뒤 승인 요청을 보내세요.'));
  for (const approval of [...state.approvals].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))) {
    const product = productById(approval.product_id); const payload = approval.payload || {}; const snapshot = payload.document || payload.draft || payload.purchase || payload.listing || payload.review || payload;
    const summary = typeof snapshot === 'string' ? snapshot : snapshot.text || snapshot.reply_draft || (snapshot.description ? [snapshot.title, '', snapshot.description, '', '키워드: ' + (snapshot.keywords || []).join(', ')].join('\n') : JSON.stringify(snapshot, null, 2));
    async function decide(decision) { if (decision === 'completed' && !confirm('외부 서비스에서 실제 작업을 완료하셨나요? 이 버튼은 완료 사실만 기록하며 주문·상품 게시·답변을 전송하지 않습니다.')) return; await api('/api/approvals/' + encodeURIComponent(approval.id) + '/decision', 'POST', { decision, revision: approval.revision }); await load(); toast(decision === 'completed' ? '외부 수동 완료 사실을 기록했습니다.' : '검토 결과를 저장했습니다.'); }
    const controls = approval.status === 'pending' ? [button('승인', () => decide('approved'), 'primary small'), button('반려', () => decide('rejected'), 'small')] : approval.status === 'approved' ? [button('외부 작업 후 완료 기록', () => decide('completed'), 'small primary'), button('반려 · 다시 준비', () => decide('rejected'), 'small')] : [];
    body.append(el('article', { class: 'approval-card' }, el('div', { class: 'card-head' }, el('div', {}, el('div', { class: 'card-title' }, (kinds[approval.kind] || approval.kind) + ' · ' + (product?.name || '상품')), el('span', { class: 'review-meta' }, date(approval.created_at) + ' · 검토 시점 자료')), badge(approvalStatus[approval.status] || approval.status, approval.status === 'pending' ? 'orange' : approval.status === 'rejected' ? 'red' : 'green')), el('pre', { class: 'approval-summary' }, summary), el('div', { class: 'actions' }, controls)));
  }
  return panel('의사결정과 실행 기록', '승인은 내부 검토입니다. 외부 주문·게시·답변을 실행하지 않습니다.', body);
}
function renderSettings() {
  const wrap = el('div', { class: 'split' });
  const exports = el('div', { class: 'panel-body' }, el('p', { class: 'note' }, '데이터는 이 앱을 실행한 컴퓨터의 SQLite 파일에 저장됩니다. 다른 컴퓨터로 옮길 때 JSON 백업을 사용하세요.'), el('div', { class: 'actions' }, el('a', { class: 'button', href: '/api/backup', download: 'uahome-backup.json' }, '전체 JSON 백업'), el('a', { class: 'button', href: '/api/export/products.csv', download: 'uahome-products.csv' }, '상품 CSV'), el('a', { class: 'button', href: '/api/export/listings.csv', download: 'uahome-listings.csv' }, '등록 초안 CSV')));
  const upload = el('input', { type: 'file', accept: '.json,application/json', 'aria-label': '복원할 JSON 백업 선택', onchange: async event => {
    const file = event.target.files[0]; if (!file) return;
    try { if (file.size > 10 * 1024 * 1024) throw new Error('10MB 이하의 백업 파일을 선택하세요.'); const backup = JSON.parse(await file.text()); if (!confirm('현재 상품·리뷰·승인 데이터를 백업 파일 내용으로 대체합니다. 먼저 현재 데이터를 백업했는지 확인하세요. 계속할까요?')) return; await api('/api/import', 'POST', { backup }); await load(); toast('백업을 복원했습니다.'); } catch (error) { toast(error.message, true); } finally { event.target.value = ''; }
  } });
  exports.append(el('h3', { class: 'mt30' }, '백업 복원'), el('p', { class: 'hint' }, '복원하면 현재 데이터가 대체됩니다. 이 앱에서 내려받은 JSON 백업만 사용하세요.'), upload, el('h3', { class: 'mt30' }, '예시로 살펴보기'), button('예시 데이터 추가', async () => { await api('/api/demo', 'POST', {}); await load(); toast('예시 데이터를 추가했습니다. 기존 상품은 유지됩니다.'); }, 'small'));
  const connectors = el('div', { class: 'panel-body' }, el('p', { class: 'note warning' }, '판매처와 사입처 API는 아직 연결되지 않았습니다. 판매 채널과 공급처가 정해지면 해당 API 지원 범위를 확인해 연동합니다.'));
  const items = Array.isArray(state.connectors) ? state.connectors : Object.entries(state.connectors).map(([name, value]) => ({ name, ...(typeof value === 'object' ? value : { status: value }) }));
  if (!items.length) connectors.append(el('div', { class: 'connector' }, '판매처 · 사입처', badge('연결 전', 'orange')));
  for (const item of items) connectors.append(el('div', { class: 'connector' }, item.name || item.label || item.id || '외부 서비스', badge('연결 전', 'orange')));
  const events = el('div', { class: 'panel-body' });
  if (!state.events.length) events.append(el('p', { class: 'hint' }, '상품과 승인 작업을 진행하면 이력이 기록됩니다.'));
  for (const event of [...state.events].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 20)) events.append(el('div', { class: 'event' }, event.message || event.summary || event.action || event.kind || '데이터 변경', el('small', {}, date(event.created_at))));
  wrap.append(panel('데이터 관리', '운영 기록을 안전하게 보관하세요.', exports), el('div', {}, panel('외부 서비스 연결', '현재 연결 상태', connectors), panel('최근 작업 이력', '실제로 저장된 변경 기록', events)));
  return wrap;
}

for (const [id, [icon, label]] of Object.entries(tabs)) $('#navigation').append(el('a', { href: '#' + id, class: 'nav-item' }, el('span', { class: 'nav-icon', 'aria-hidden': true }, icon), label));
$('#new-product').addEventListener('click', () => openProduct());
$('#refresh').addEventListener('click', () => load());
for (const id of ['close-product', 'cancel-product']) $('#' + id).addEventListener('click', () => $('#product-dialog').close());
window.addEventListener('hashchange', renderView);
renderMetrics(); renderView(); load();
