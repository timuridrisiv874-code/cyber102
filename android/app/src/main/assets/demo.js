'use strict';
const byId = (id) => document.getElementById(id);
const sections = [...document.querySelectorAll('main > section')];
const nav = [...document.querySelectorAll('nav button')];
function showPage(id) {
  sections.forEach((section) => { section.hidden = section.id !== id; });
  nav.forEach((button) => {
    if (button.dataset.page === id) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  const heading = byId(id).querySelector('h1');
  heading.tabIndex = -1;
  heading.focus();
}
document.querySelectorAll('[data-page]').forEach((button) => {
  button.addEventListener('click', () => showPage(button.dataset.page));
});
byId('serial-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const found = byId('serial').value.trim().toUpperCase() === 'P-102';
  byId('pump').hidden = !found;
  byId('serial-status').textContent = found ? 'Паспорт открыт.' : 'В демо доступен только серийный номер P-102.';
});
byId('analyze').addEventListener('click', () => {
  const button = byId('analyze');
  button.disabled = true;
  byId('analysis-status').textContent = 'Открываем подготовленный результат…';
  window.setTimeout(() => {
    byId('frame').hidden = false;
    byId('result').hidden = false;
    byId('analysis-status').textContent = 'Демонстрационный результат готов.';
    button.disabled = false;
  }, 350);
});
byId('frame').addEventListener('click', () => {
  byId('result').focus();
  byId('result').scrollIntoView({block:'nearest'});
});
const scenarios = {
  seal_leak: ['Возможная утечка через уплотнение', 'Жидкость в зоне вала. Вибрация и температура подшипника растут.'],
  coupling_guard: ['Проверьте кожух муфты', 'В демонстрационном осмотре кожух отсутствует. Показания датчиков в норме.'],
  healthy: ['Предупреждений по сценарию нет', 'Демонстрационные показания находятся в заданных пределах.'],
};
function updateDiagnosis() {
  const [finding, evidence] = scenarios[byId('scenario').value];
  byId('finding').textContent = finding;
  byId('evidence').textContent = evidence;
}
byId('scenario').addEventListener('change', updateDiagnosis);
updateDiagnosis();
byId('save-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const entry = document.createElement('article');
  entry.className = 'panel';
  const title = document.createElement('h2');
  title.textContent = 'P-102 · ' + scenarios[byId('scenario').value][0];
  const note = document.createElement('p');
  note.textContent = byId('note').value.trim() || 'Без заметки';
  entry.append(title, note);
  byId('empty-history').hidden = true;
  byId('records').prepend(entry);
  byId('note').value = '';
  showPage('history');
});
