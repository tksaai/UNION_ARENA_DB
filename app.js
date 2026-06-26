(() => {
  'use strict';

  const DATA_URL = './cards.json';
  const IMAGE_CACHE = 'union-arena-card-images-v1';
  const MOBILE_WIDTH = 520;
  const MOBILE_INITIAL_RENDER = 72;
  const DESKTOP_INITIAL_RENDER = 144;
  const MOBILE_BATCH_SIZE = 48;
  const DESKTOP_BATCH_SIZE = 96;
  const LOAD_AHEAD_PX = 1100;
  const state = {
    cards: [],
    filtered: [],
    filters: {},
    renderedCount: 0,
    renderToken: 0,
    appendFrame: 0,
    activeCard: null,
    activeIndex: -1,
    activeVariantIndex: 0,
    touchStartX: 0,
    touchStartY: 0,
    touchEndX: 0,
    touchEndY: 0,
    pointerId: null,
  };

  const $ = (selector) => document.querySelector(selector);
  const elements = {
    search: $('#search-input'),
    count: $('#result-count'),
    grid: $('#card-grid'),
    loadStatus: $('#load-status'),
    loadSentinel: $('#load-sentinel'),
    loading: $('#loading'),
    empty: $('#empty-message'),
    columnToggle: $('#column-toggle'),
    columnCount: $('#column-count'),
    filterButton: $('#filter-button'),
    filterBadge: $('#filter-badge'),
    filterDialog: $('#filter-dialog'),
    filterFields: $('#filter-fields'),
    resetFilters: $('#reset-filters'),
    applyFilters: $('#apply-filters'),
    cardDialog: $('#card-dialog'),
    detailClose: $('#detail-close'),
    detailPrevious: $('#detail-previous'),
    detailNext: $('#detail-next'),
    detailSwipeArea: $('#detail-swipe-area'),
    detailImage: $('#detail-image'),
    detailPosition: $('#detail-position'),
    detailNumber: $('#detail-number'),
    detailName: $('#detail-name'),
    detailReading: $('#detail-reading'),
    detailTags: $('#detail-tags'),
    detailStats: $('#detail-stats'),
    detailEffect: $('#detail-effect'),
    detailTrigger: $('#detail-trigger'),
    detailProduct: $('#detail-product'),
    detailSource: $('#detail-source'),
    variantButtons: $('#variant-buttons'),
    settingsButton: $('#settings-button'),
    settingsDialog: $('#settings-dialog'),
    settingsClose: $('#settings-close'),
    cacheImages: $('#cache-images'),
    cacheStatus: $('#cache-status'),
    dataStatus: $('#data-status'),
    reloadData: $('#reload-data'),
  };

  const filterDefinitions = [
    ['title', 'タイトル'],
    ['productCode', '商品コード'],
    ['color', '色'],
    ['cardType', 'カード種類'],
    ['rarity', 'レアリティ'],
    ['needEnergy', '必要エナジー'],
    ['ap', '消費AP'],
    ['bp', 'BP'],
    ['triggerType', 'トリガー'],
  ];

  function normalize(value) {
    return String(value ?? '')
      .normalize('NFKC')
      .replace(/[\u3041-\u3096]/g, (char) => String.fromCharCode(char.charCodeAt(0) + 0x60))
      .toUpperCase();
  }

  function getVariants(card) {
    if (Array.isArray(card.variants) && card.variants.length) return card.variants;
    const image = card.imagePath || card.imageUrl;
    return image ? [{ id: card.uniqueId, label: card.rarity || '通常', imagePath: card.imagePath, imageUrl: card.imageUrl }] : [];
  }

  function getImageUrl(variant) {
    return variant?.imagePath || variant?.imageUrl || '';
  }

  function baseRarity(rarity) {
    return String(rarity || '').replace(/[★☆]+/g, '');
  }

  function getFilterValue(card, key) {
    if (key === 'color') return Array.isArray(card.color) ? card.color : [card.color].filter(Boolean);
    if (key === 'rarity') {
      const values = new Set([baseRarity(card.rarity)]);
      getVariants(card).forEach((variant) => values.add(baseRarity(variant.rarity)));
      return [...values].filter(Boolean);
    }
    return card[key];
  }

  function compareValues(a, b) {
    const numberA = Number(a);
    const numberB = Number(b);
    if (Number.isFinite(numberA) && Number.isFinite(numberB)) return numberA - numberB;
    return String(a).localeCompare(String(b), 'ja', { numeric: true });
  }

  function createOption(value) {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = String(value);
    return option;
  }

  function populateFilters() {
    elements.filterFields.replaceChildren();
    for (const [key, labelText] of filterDefinitions) {
      const values = new Set();
      state.cards.forEach((card) => {
        const value = getFilterValue(card, key);
        (Array.isArray(value) ? value : [value]).forEach((entry) => {
          if (entry !== undefined && entry !== null && entry !== '' && entry !== '-') values.add(String(entry));
        });
      });

      const label = document.createElement('label');
      label.className = 'filter-field';
      label.textContent = labelText;
      const select = document.createElement('select');
      select.dataset.filter = key;
      const all = document.createElement('option');
      all.value = '';
      all.textContent = 'すべて';
      select.append(all, ...[...values].sort(compareValues).map(createOption));
      select.value = state.filters[key] || '';
      label.append(select);
      elements.filterFields.append(label);
    }
  }

  function readFilters() {
    const filters = {};
    elements.filterFields.querySelectorAll('select[data-filter]').forEach((select) => {
      if (select.value) filters[select.dataset.filter] = select.value;
    });
    state.filters = filters;
    updateFilterBadge();
  }

  function updateFilterBadge() {
    const count = Object.keys(state.filters).length;
    elements.filterBadge.hidden = count === 0;
    elements.filterBadge.textContent = String(count);
  }

  function matchesFilter(card, key, expected) {
    const value = getFilterValue(card, key);
    if (Array.isArray(value)) return value.map(String).includes(expected);
    return String(value ?? '') === expected;
  }

  function getSearchText(card) {
    return normalize([
      card.cardNumber,
      card.cardName,
      card.furigana,
      card.title,
      card.product,
      card.productCode,
      ...(card.features || []),
      card.effectText,
      card.trigger,
    ].join(' '));
  }

  function prepareCard(card) {
    card.searchText = getSearchText(card);
    return card;
  }

  function isMobileWidth() {
    return innerWidth <= MOBILE_WIDTH;
  }

  function getInitialRenderSize() {
    return isMobileWidth() ? MOBILE_INITIAL_RENDER : DESKTOP_INITIAL_RENDER;
  }

  function getBatchSize() {
    return isMobileWidth() ? MOBILE_BATCH_SIZE : DESKTOP_BATCH_SIZE;
  }

  function cancelScheduledAppend() {
    if (!state.appendFrame) return;
    cancelAnimationFrame(state.appendFrame);
    state.appendFrame = 0;
  }

  function isLoadSentinelNearViewport() {
    if (!elements.loadSentinel || elements.loadSentinel.hidden) return false;
    return elements.loadSentinel.getBoundingClientRect().top <= innerHeight + LOAD_AHEAD_PX;
  }

  function updateLoadProgress(token = state.renderToken) {
    const hasMore = state.renderedCount < state.filtered.length;
    elements.loadStatus.hidden = !hasMore;
    elements.loadSentinel.hidden = !hasMore;
    if (!hasMore) return;

    elements.loadStatus.textContent =
      `${state.renderedCount.toLocaleString('ja-JP')} / ${state.filtered.length.toLocaleString('ja-JP')}枚表示中`;
    if (token === state.renderToken && isLoadSentinelNearViewport()) scheduleAppend(token);
  }

  function appendCards(token = state.renderToken, batchSize = getBatchSize()) {
    if (token !== state.renderToken || state.renderedCount >= state.filtered.length) return;

    const start = state.renderedCount;
    const end = Math.min(start + batchSize, state.filtered.length);
    const fragment = document.createDocumentFragment();

    for (let index = start; index < end; index += 1) {
      fragment.append(createCardElement(state.filtered[index], index, index < 18));
    }

    elements.grid.append(fragment);
    state.renderedCount = end;
    updateLoadProgress(token);
  }

  function scheduleAppend(token = state.renderToken) {
    if (state.appendFrame || token !== state.renderToken || state.renderedCount >= state.filtered.length) return;
    state.appendFrame = requestAnimationFrame(() => {
      state.appendFrame = 0;
      appendCards(token);
    });
  }

  function handleLoadMore() {
    if (state.renderedCount < state.filtered.length && isLoadSentinelNearViewport()) scheduleAppend();
  }

  function filterCards() {
    const words = normalize(elements.search.value).split(/\s+/).filter(Boolean);
    const filterEntries = Object.entries(state.filters);
    state.filtered = state.cards.filter((card) => {
      if (words.length && !words.every((word) => card.searchText.includes(word))) return false;
      return filterEntries.every(([key, expected]) => matchesFilter(card, key, expected));
    });
    renderCards({ resetScroll: true });
  }

  function createCardElement(card, index, priority = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'card-item';
    button.dataset.index = String(index);
    button.title = `${card.cardNumber} ${card.cardName}`;
    button.setAttribute('aria-label', `${card.cardNumber} ${card.cardName}`);
    const variants = getVariants(card);
    const imageUrl = getImageUrl(variants[0]);

    if (imageUrl) {
      const image = document.createElement('img');
      image.src = imageUrl;
      image.alt = `${card.cardNumber} ${card.cardName}`;
      image.width = 500;
      image.height = 700;
      image.loading = priority ? 'eager' : 'lazy';
      image.decoding = 'async';
      if ('fetchPriority' in image) image.fetchPriority = priority ? 'high' : 'low';
      image.addEventListener('error', () => {
        const fallback = document.createElement('span');
        fallback.className = 'image-fallback';
        fallback.textContent = `${card.cardNumber}\n${card.cardName}`;
        image.replaceWith(fallback);
      }, { once: true });
      button.append(image);
    } else {
      const fallback = document.createElement('span');
      fallback.className = 'image-fallback';
      fallback.textContent = `${card.cardNumber}\n${card.cardName}`;
      button.append(fallback);
    }

    const badges = document.createElement('span');
    badges.className = 'card-badges';
    const rarity = document.createElement('span');
    rarity.className = 'badge';
    rarity.textContent = card.rarity || card.cardType || '';
    badges.append(rarity);
    if (variants.length > 1) {
      const parallel = document.createElement('span');
      parallel.className = 'badge parallel';
      parallel.textContent = `+${variants.length - 1}`;
      badges.append(parallel);
    }
    button.append(badges);
    return button;
  }

  function renderCards({ resetScroll = false } = {}) {
    cancelScheduledAppend();
    state.renderToken += 1;
    state.renderedCount = 0;

    elements.count.textContent = state.filtered.length.toLocaleString('ja-JP');
    elements.empty.hidden = state.filtered.length !== 0;
    elements.grid.replaceChildren();

    if (resetScroll) window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    if (state.filtered.length === 0) {
      elements.loadStatus.hidden = true;
      elements.loadSentinel.hidden = true;
      return;
    }

    appendCards(state.renderToken, getInitialRenderSize());
  }

  function addStat(label, value) {
    if (value === undefined || value === null || value === '' || value === '-') return;
    if (Array.isArray(value) && value.length === 0) return;
    const wrapper = document.createElement('div');
    const term = document.createElement('dt');
    const description = document.createElement('dd');
    term.textContent = label;
    description.textContent = Array.isArray(value) ? value.join(' / ') : String(value);
    wrapper.append(term, description);
    elements.detailStats.append(wrapper);
  }

  function renderVariant(card, index) {
    const variants = getVariants(card);
    const variant = variants[index] || variants[0] || {};
    state.activeVariantIndex = Math.max(0, variants.indexOf(variant));
    elements.detailImage.src = getImageUrl(variant);
    elements.detailImage.alt = `${card.cardNumber} ${card.cardName}`;
    elements.detailTags.replaceChildren();
    [variant.rarity || card.rarity, card.cardType, ...(card.color || []), ...(card.features || [])]
      .filter((value) => value && value !== '-')
      .forEach((value) => {
        const tag = document.createElement('span');
        tag.textContent = value;
        elements.detailTags.append(tag);
      });
    [...elements.variantButtons.children].forEach((button, buttonIndex) => {
      button.classList.toggle('active', buttonIndex === index);
    });
  }

  function renderCardDetail(card) {
    state.activeCard = card;
    elements.detailNumber.textContent = card.cardNumber;
    elements.detailName.textContent = card.cardName || '';
    elements.detailReading.textContent = card.furigana || '';
    elements.detailEffect.textContent = card.effectText || '-';
    elements.detailTrigger.textContent = card.trigger || '-';
    elements.detailProduct.textContent = card.product || card.productCode || '-';
    elements.detailSource.href = card.sourceUrl || 'https://www.unionarena-tcg.com/jp/cardlist/';
    elements.detailStats.replaceChildren();
    addStat('必要エナジー', card.needEnergy);
    addStat('消費AP', card.ap);
    addStat('BP', card.bp);
    addStat('発生エナジー', (card.generatedEnergy || []).map((entry) => `${entry.color}${entry.count || ''}`));
    addStat('特徴', card.features);
    addStat('タイトル', card.title);

    const variants = getVariants(card);
    elements.variantButtons.replaceChildren();
    variants.forEach((variant, index) => {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = variant.label || variant.rarity || `画像 ${index + 1}`;
      button.addEventListener('click', () => renderVariant(card, index));
      elements.variantButtons.append(button);
    });
    renderVariant(card, 0);
    elements.detailPosition.textContent = `${state.activeIndex + 1} / ${state.filtered.length}`;
    elements.detailPrevious.disabled = state.activeIndex <= 0;
    elements.detailNext.disabled = state.activeIndex >= state.filtered.length - 1;
    elements.detailSwipeArea.scrollTop = 0;
    const detailContent = elements.cardDialog.querySelector('.detail-content');
    if (detailContent) detailContent.scrollTop = 0;
    preloadAdjacentCards();
  }

  function openCardAt(index) {
    if (index < 0 || index >= state.filtered.length) return;
    state.activeIndex = index;
    renderCardDetail(state.filtered[index]);
    if (!elements.cardDialog.open) elements.cardDialog.showModal();
  }

  function moveCard(offset) {
    if (!elements.cardDialog.open) return;
    const nextIndex = state.activeIndex + offset;
    if (nextIndex < 0 || nextIndex >= state.filtered.length) return;
    state.activeIndex = nextIndex;
    renderCardDetail(state.filtered[nextIndex]);
  }

  function preloadAdjacentCards() {
    [state.activeIndex - 1, state.activeIndex + 1].forEach((index) => {
      const card = state.filtered[index];
      if (!card) return;
      const imageUrl = getImageUrl(getVariants(card)[0]);
      if (imageUrl) {
        const image = new Image();
        image.src = imageUrl;
      }
    });
  }

  function resetTouch() {
    state.touchStartX = 0;
    state.touchStartY = 0;
    state.touchEndX = 0;
    state.touchEndY = 0;
    state.pointerId = null;
  }

  function handleDetailPointerDown(event) {
    if (!event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) {
      resetTouch();
      return;
    }
    state.pointerId = event.pointerId;
    state.touchStartX = event.clientX;
    state.touchStartY = event.clientY;
    state.touchEndX = state.touchStartX;
    state.touchEndY = state.touchStartY;
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function handleDetailPointerMove(event) {
    if (!state.touchStartX || event.pointerId !== state.pointerId) return;
    state.touchEndX = event.clientX;
    state.touchEndY = event.clientY;
  }

  function handleDetailPointerUp(event) {
    if (event.pointerId !== state.pointerId) return;
    if (!state.touchStartX) return;
    const distanceX = state.touchEndX - state.touchStartX;
    const distanceY = state.touchEndY - state.touchStartY;
    const isHorizontalSwipe = Math.abs(distanceX) >= 50 && Math.abs(distanceX) > Math.abs(distanceY) * 1.25;
    if (isHorizontalSwipe) moveCard(distanceX < 0 ? 1 : -1);
    resetTouch();
  }

  function setGridColumns(columns) {
    const safeColumns = Math.min(5, Math.max(1, Number(columns) || 3));
    document.documentElement.style.setProperty('--grid-columns', safeColumns);
    elements.columnCount.textContent = String(safeColumns);
    elements.columnToggle.setAttribute('aria-label', `表示列数: ${safeColumns}列。タップして切り替え`);
    localStorage.setItem('unionArenaColumns', String(safeColumns));
  }

  async function loadCards({ bypassCache = false } = {}) {
    elements.loading.hidden = false;
    elements.grid.replaceChildren();
    elements.empty.hidden = true;
    try {
      const response = await fetch(`${DATA_URL}${bypassCache ? `?t=${Date.now()}` : ''}`, { cache: bypassCache ? 'reload' : 'default' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (!Array.isArray(data)) throw new Error('cards.json must be an array');
      state.cards = data
        .filter((card) => card && card.cardNumber)
        .map(prepareCard)
        .sort((a, b) => String(a.cardNumber).localeCompare(String(b.cardNumber), 'ja', { numeric: true }));
      populateFilters();
      filterCards();
      elements.dataStatus.textContent = `${state.cards.length.toLocaleString('ja-JP')}枚 / ${new Date().toLocaleString('ja-JP')}`;
    } catch (error) {
      console.error(error);
      elements.empty.hidden = false;
      elements.empty.textContent = `カードデータを読み込めませんでした: ${error.message}`;
      elements.dataStatus.textContent = `読込エラー: ${error.message}`;
    } finally {
      elements.loading.hidden = true;
    }
  }

  async function cacheAllImages() {
    const urls = [...new Set(state.cards.flatMap((card) => getVariants(card).map(getImageUrl)).filter(Boolean))];
    const cache = await caches.open(IMAGE_CACHE);
    elements.cacheImages.disabled = true;
    let completed = 0;
    let failed = 0;
    for (const url of urls) {
      try {
        const request = new Request(url, { mode: url.startsWith(location.origin) || url.startsWith('./') ? 'same-origin' : 'no-cors' });
        const response = await fetch(request);
        await cache.put(request, response);
      } catch (error) {
        failed += 1;
      }
      completed += 1;
      elements.cacheStatus.textContent = `${completed}/${urls.length}（失敗 ${failed}）`;
    }
    elements.cacheImages.disabled = false;
    elements.cacheStatus.textContent = `完了: ${completed - failed}枚保存、${failed}枚失敗`;
  }

  function setupEvents() {
    let timer;
    elements.search.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(filterCards, 100);
    });
    setGridColumns(localStorage.getItem('unionArenaColumns') || (innerWidth < 500 ? 3 : 4));
    elements.columnToggle.addEventListener('click', () => {
      const current = Number(localStorage.getItem('unionArenaColumns')) || 3;
      setGridColumns(current >= 5 ? 1 : current + 1);
      handleLoadMore();
    });
    elements.grid.addEventListener('click', (event) => {
      const item = event.target.closest('.card-item');
      if (!item || !elements.grid.contains(item)) return;
      openCardAt(Number(item.dataset.index));
    });
    window.addEventListener('scroll', handleLoadMore, { passive: true });
    window.addEventListener('resize', handleLoadMore, { passive: true });
    elements.filterButton.addEventListener('click', () => elements.filterDialog.showModal());
    elements.applyFilters.addEventListener('click', () => {
      readFilters();
      filterCards();
    });
    elements.resetFilters.addEventListener('click', () => {
      state.filters = {};
      elements.filterFields.querySelectorAll('select').forEach((select) => { select.value = ''; });
      updateFilterBadge();
      filterCards();
    });
    elements.detailClose.addEventListener('click', () => elements.cardDialog.close());
    elements.detailPrevious.addEventListener('click', () => moveCard(-1));
    elements.detailNext.addEventListener('click', () => moveCard(1));
    elements.detailSwipeArea.addEventListener('pointerdown', handleDetailPointerDown);
    elements.detailSwipeArea.addEventListener('pointermove', handleDetailPointerMove);
    elements.detailSwipeArea.addEventListener('pointerup', handleDetailPointerUp);
    elements.detailSwipeArea.addEventListener('pointercancel', resetTouch);
    document.addEventListener('keydown', (event) => {
      if (!elements.cardDialog.open) return;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        moveCard(-1);
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        moveCard(1);
      }
    });
    elements.settingsButton.addEventListener('click', () => elements.settingsDialog.showModal());
    elements.settingsClose.addEventListener('click', () => elements.settingsDialog.close());
    elements.cacheImages.addEventListener('click', cacheAllImages);
    elements.reloadData.addEventListener('click', () => loadCards({ bypassCache: true }));
    [elements.filterDialog, elements.cardDialog, elements.settingsDialog].forEach((dialog) => {
      dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
      });
    });
    elements.cardDialog.addEventListener('close', () => {
      state.activeCard = null;
      state.activeIndex = -1;
      state.activeVariantIndex = 0;
      resetTouch();
    });
  }

  setupEvents();
  loadCards();
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('./service-worker.js').catch(console.error);
  }
})();
