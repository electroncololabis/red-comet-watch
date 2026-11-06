(() => {
  'use strict';
  const themes = {
    classic: {name:'赤色彗星'},
    zaku: {name:'扎古 · 铁幕军团', english:'ZEON ARMORED CORPS', note:'军绿 / 黄铜 / 暗红', colors:['#0b100b','#1d291b','#b2c78a','#d6bb79']},
    haman: {name:'哈曼 · 蔷薇王座', english:'AXIS ROYAL COMMAND', note:'珍珠白 / 蔷薇粉 / 玫瑰金', colors:['#f4edf0','#fffafd','#ad426e','#84602d']},
    federation: {name:'联邦 · 苍穹舰队', english:'EARTH FEDERATION FORCES', note:'海军蓝 / 冰白 / 青蓝', colors:['#071421','#102b43','#72c9ff','#c0ddef']},
    murasame: {name:'村雨 · 精神力禁域', english:'MURASAME RESEARCH LAB', note:'石墨黑 / 深紫 / 冷青', colors:['#0d0a17','#21182f','#c3a0ff','#74d9d5']},
  };
  const key = 'red-comet-appearance-v1';
  const root = document.documentElement;
  let selected = 'classic';
  try {
    const saved = JSON.parse(localStorage.getItem(key) || 'null');
    if (saved && Object.hasOwn(themes, saved.theme)) selected = saved.theme;
  } catch (_) {}

  document.addEventListener('DOMContentLoaded', () => {
    const picker = document.querySelector('#skinSelect');
    const gallery = document.querySelector('#themeGallery');
    const status = document.querySelector('#skinStatus');

    function apply(theme, persist = true) {
      selected = theme;
      if (theme === 'classic') delete root.dataset.skin;
      else root.dataset.skin = theme;
      const scene = theme === 'haman' ? 'haman-qubeley-gaza-c-20261106.png' : 'classic-sazabi-nightingale-20261106.png';
      root.style.setProperty('--skin-hangar', `url("/static/assets/themes/${scene}")`);
      root.style.setProperty('--skin-hangar-ratio', theme === 'haman' ? '1983 / 793' : '2 / 1');
      picker.value = theme;
      status.textContent = `当前：${themes[theme].name}`;
      gallery.querySelectorAll('button').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.theme === theme));
      });
      if (persist) try { localStorage.setItem(key, JSON.stringify({theme})); } catch (_) {}
    }

    for (const [id, theme] of Object.entries(themes)) {
      picker.add(new Option(theme.name, id));
      if (id === 'classic') continue;
      const card = document.createElement('article');
      card.className = 'skin-family';
      card.innerHTML = `<div class="skin-family-heading"><small>${theme.english}</small><h3>${theme.name}</h3><p>${theme.note}</p></div>
        <div class="skin-swatches">${theme.colors.map(color => `<i style="background:${color}" aria-hidden="true"></i>`).join('')}</div>
        <button type="button" data-theme="${id}">应用配色</button>`;
      card.querySelector('button').addEventListener('click', () => apply(id));
      gallery.append(card);
    }
    picker.addEventListener('change', () => apply(picker.value));
    document.querySelector('#resetSkin').addEventListener('click', () => apply('classic'));
    apply(selected, false);
  });
})();
